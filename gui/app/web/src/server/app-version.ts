import { readFileSync } from "node:fs";
import path from "node:path";
import { appSupportContainerName, devAppDataName } from "./paths.js";

export type AppVersion = { version: string; dev: boolean };

/**
 * The installed app names its version. A development run (a server started from a checkout, which uses the
 * development data folder) shows the latest release instead: the repository's version, which the app shares with
 * the CLI and changes only at a release. It always counts as out of date.
 */
export function appVersion(env: NodeJS.ProcessEnv, repoRoot: string): AppVersion {
    if (appSupportContainerName() !== devAppDataName) {
        return { version: env.WGSEXTRACT_GUI_APPLICATION_VERSION?.trim() || "", dev: false };
    }
    return { version: releasedVersion(path.resolve(repoRoot, "..", "pyproject.toml")), dev: true };
}

function releasedVersion(pyproject: string): string {
    try {
        return /^version\s*=\s*"([^"]+)"/m.exec(readFileSync(pyproject, "utf8"))?.[1] ?? "";
    }
    catch {
        return "";
    }
}
