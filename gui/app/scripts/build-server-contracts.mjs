import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const windows = process.platform === "win32";
const interpreters = [
  windows ? "../../.venv/Scripts/python.exe" : "../../.venv/bin/python",
  windows ? "../../../.pixi/envs/default/python.exe" : "../../../.pixi/envs/default/bin/python",
].map((relative) => fileURLToPath(new URL(relative, import.meta.url)));
const python = interpreters.find(existsSync) ?? (windows ? "python" : "python3");
const generator = fileURLToPath(new URL("../../../scripts/gen_server_openapi.py", import.meta.url));
const result = spawnSync(python, [generator, "--gui"], { stdio: "inherit" });
if (result.status !== 0) {
  if (result.error) console.error(result.error.message);
  console.error("Could not build the server transport types. Prepare the GUI Python dependencies with make setup (make.ps1 setup on Windows).");
  process.exitCode = result.status ?? 1;
}
