"""Issue #1230: the app and the CLI saving config.toml at once keep every change.

Both read the file, merge their change and write it back. Without a lock they
share, a save that read the file before the other's write silently undid it.
This runs the app's real saveConfig (from app/dist, which `npm test` builds
first) against the CLI's real save_config, started together, many times over.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from test_bundle_cli_args import CLI_ENV, CLI_ROOT, REPO_ROOT, find_cli_python

CONFIG_STORE = REPO_ROOT / "app/dist/web/src/server/config-store.js"
SAVES = 100
CLI_KEYS = ("reference_library", "output_directory", "temp_directory", "log_dir")

# Each saver prints "ready" once loaded and starts saving when told to, so the
# two overlap rather than one finishing while the other starts up.
APP_SAVER = """
import { createInterface } from "node:readline";
const [moduleURL, file, count] = process.argv.slice(1);
const { saveConfig } = await import(moduleURL);
console.log("ready");
await createInterface({ input: process.stdin })[Symbol.asyncIterator]().next();
for (let index = 0; index < Number(count); index++) {
  const key = `app_${index}`;
  const control = { id: "settings", kind: "configEditor", configFile: { path: file }, settings: [{ id: key, key, kind: "text" }] };
  await saveConfig(control, file, { [key]: "saved" }, ".");
}
"""
CLI_SAVER = """
import json, logging, sys
from wgsextract_cli.core import config
logging.disable(logging.WARNING)  # the app's keys are unknown settings to the CLI
file, count, keys = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3])
config.set_config_path(file)
print("ready", flush=True)
sys.stdin.readline()
for index in range(count):
    config.save_config({keys[index % len(keys)]: f"/cli/{index}"})
"""


class SettingsLockInteropTests(unittest.TestCase):
    def test_1230_app_and_cli_saves_at_once_keep_every_change(self) -> None:
        python = find_cli_python()
        if python is None:
            message = f"no Python can import the CLI from {CLI_ROOT / 'src'}"
            if os.environ.get("CI"):
                self.fail(message)
            self.skipTest(message)
        # GUI CI builds the app first; the CLI's own CI runs these tests unbuilt.
        if not CONFIG_STORE.is_file():
            self.skipTest(f"the app is not built ({CONFIG_STORE}; run `npm test`)")
        with tempfile.TemporaryDirectory(prefix="wgsextract-1230-") as folder:
            file = Path(folder) / "config.toml"
            savers = [
                subprocess.Popen(
                    [
                        "node",
                        "--input-type=module",
                        "-e",
                        APP_SAVER,
                        CONFIG_STORE.as_uri(),
                        str(file),
                        str(SAVES),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                ),
                subprocess.Popen(
                    [
                        str(python),
                        "-c",
                        CLI_SAVER,
                        str(file),
                        str(SAVES),
                        json.dumps(CLI_KEYS),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    env=CLI_ENV,
                    text=True,
                ),
            ]
            for saver in savers:
                self.addCleanup(saver.kill)
            for saver in savers:
                assert saver.stdout is not None
                self.assertEqual(saver.stdout.readline(), "ready\n")
            for saver in savers:
                assert saver.stdin is not None
                saver.stdin.write("go\n")
                saver.stdin.close()
            for saver in savers:
                self.assertEqual(saver.wait(timeout=120), 0)
                assert saver.stdout is not None
                saver.stdout.close()

            saved = tomllib.loads(file.read_text(encoding="utf-8"))
            lost = [f"app_{index}" for index in range(SAVES) if f"app_{index}" not in saved]
            self.assertEqual(lost, [], "the app's saves the CLI overwrote")
            last = {
                CLI_KEYS[index % len(CLI_KEYS)]: f"/cli/{index}"
                for index in range(SAVES - len(CLI_KEYS), SAVES)
            }
            self.assertEqual({key: saved.get(key) for key in CLI_KEYS}, last)
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["config.toml"])


if __name__ == "__main__":
    sys.exit(unittest.main())
