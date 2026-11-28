import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { appVersion } from "../dist/web/src/server/app-version.js";

const repoRoot = path.resolve(import.meta.dirname, "..", "..");

test("a development run shows the latest release's version as Dev; the installed app names its own", (t) => {
  // Owner request: a checkout shows the latest released version with "Dev", always out of date.
  const previous = process.env.WGSEXTRACT_GUI_APP_DATA_NAME;
  t.after(() => {
    if (previous === undefined) delete process.env.WGSEXTRACT_GUI_APP_DATA_NAME;
    else process.env.WGSEXTRACT_GUI_APP_DATA_NAME = previous;
  });
  const released = /^version\s*=\s*"([^"]+)"/m.exec(readFileSync(path.join(repoRoot, "..", "pyproject.toml"), "utf8"))?.[1];
  assert.ok(released);
  delete process.env.WGSEXTRACT_GUI_APP_DATA_NAME;
  assert.deepEqual(appVersion({ WGSEXTRACT_GUI_APPLICATION_VERSION: "9.9.9" }, repoRoot), { version: released, dev: true });
  assert.deepEqual(appVersion({}, path.join(tmpdir(), "no-such-checkout", "gui")), { version: "", dev: true });
  process.env.WGSEXTRACT_GUI_APP_DATA_NAME = "WGSExtract-gui";
  assert.deepEqual(appVersion({ WGSEXTRACT_GUI_APPLICATION_VERSION: " 0.9.1 " }, repoRoot), { version: "0.9.1", dev: false });
});
