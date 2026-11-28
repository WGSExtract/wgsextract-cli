import { lstat, mkdtemp, mkdir, rm, symlink, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

/**
 * The WGSEXTRACT_APP_DIR an offline verifier gives the bundle's run-wgsextract-env wrapper so it
 * runs the CLI from `cli` (a checkout). The POSIX wrapper accepts the checkout itself. The Windows
 * wrapper only runs a setup-shaped runtime: <runtime>\app plus the <runtime>\bin\wgsextract.cmd shim
 * setup writes (see gui/scripts/dev-checkout-cli.ps1). There `app` is a junction to the checkout, in
 * a temporary folder outside the repository so repository-wide walkers never follow it back in.
 * Call dispose() when done: it removes the junction itself, never the checkout behind it.
 */
export async function offlineCliAppDir(cli, platform = process.platform) {
    if (platform !== "win32") return { appDir: cli, async dispose() {} };
    const runtime = await mkdtemp(path.join(os.tmpdir(), "wgse-cli-runtime-"));
    const appDir = path.join(runtime, "app");
    const bin = path.join(runtime, "bin");
    try {
        await mkdir(bin);
        await symlink(cli, appDir, "junction");
        await writeFile(path.join(bin, "wgsextract.cmd"),
            `@echo off\r\npixi run --manifest-path "%~dp0..\\app\\pixi.toml" wgsextract %*\r\n`);
    } catch (error) {
        await removeRuntime(runtime, appDir);
        throw error;
    }
    return { appDir, dispose: () => removeRuntime(runtime, appDir) };
}

async function removeRuntime(runtime, appDir) {
    const link = await lstat(appDir).catch(() => null);
    if (link?.isSymbolicLink()) await unlink(appDir);
    else if (link) throw new Error(`${appDir} is not the junction this verifier created; leaving ${runtime} in place.`);
    await rm(runtime, { recursive: true, force: true });
}
