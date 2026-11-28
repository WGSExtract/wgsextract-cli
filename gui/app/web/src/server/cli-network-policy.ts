import { existsSync } from "node:fs";
import path from "node:path";
import type { NetworkMode } from "../../../shared/network-settings.js";
import type { RunProcess } from "../../../shared/types.js";
import { HttpError } from "./errors.js";
import { readEffectiveNetworkMode } from "./network-settings.js";

const cliName = /(?:^|[\\/])(?:wgsextract(?:\.exe|\.cmd)?|run-wgsextract\.(?:sh|ps1))$/i;
const cliWrapperName = /(?:^|[\\/])run-wgsextract\.(?:sh|ps1)$/i;
const bootstrapName = /(?:^|[\\/])bootstrap-reference-library\.(?:sh|ps1)$/i;

function isWrappedCommand(executable: string, args: string[], name: RegExp) {
    if (name.test(executable)) return true;
    // Setup scripts are already translated to their platform executable.
    if (executable === "/bin/sh" || executable === "/usr/bin/env") return name.test(args[0] ?? "");
    const fileIndex = /(?:^|[\\/])powershell\.exe$/i.test(executable) ? args.indexOf("-File") : -1;
    return fileIndex >= 0 && name.test(args[fileIndex + 1] ?? "");
}

export function isWgsExtractCli(executable: string, args: string[] = []) {
    const pixi = executable === "/usr/bin/env" && args[0] === "pixi";
    if (pixi) return args[1] === "run" && cliName.test(args[2] ?? "");
    if (/(?:^|[\\/])pixi(?:\.exe)?$/i.test(executable)) return args[0] === "run" && cliName.test(args[1] ?? "");
    return isWrappedCommand(executable, args, cliName);
}

export function isBootstrapLibrary(executable: string, args: string[]) {
    return isWrappedCommand(executable, args, bootstrapName);
}

export function isLegacyBundledCli(executable: string, args: string[], env: NodeJS.ProcessEnv, cwd = process.cwd()) {
    if (!isWrappedCommand(executable, args, cliWrapperName) && !isBootstrapLibrary(executable, args)) return false;
    const appData = env.WGSEXTRACT_GUI_APP_DATA;
    const appDir = env.WGSEXTRACT_APP_DIR || (appData && path.join(appData, "runtime", "wgsextract-cli", "app"));
    if (!appDir) return false;
    const source = path.resolve(cwd, appDir, "src", "wgsextract_cli");
    // The pinned v0.8.10 and the development tree report the same version.
    // Check the installed source for the module that introduced network modes.
    return existsSync(path.join(source, "__init__.py")) && !existsSync(path.join(source, "core", "network_options.py"));
}

export function cliNetworkArguments(args: string[], mode: NetworkMode) {
    const separator = args.indexOf("--");
    const options = separator < 0 ? args : args.slice(0, separator);
    const tail = separator < 0 ? [] : args.slice(separator);
    // `deps ready -- …` and similar commands forward their remaining arguments.
    return [...options, "--network", mode, ...tail];
}

export function withCliNetworkPolicy(runProcess: RunProcess): RunProcess {
    return async (executable, args, options) => {
        const bootstrap = isBootstrapLibrary(executable, args);
        if (!bootstrap && !isWgsExtractCli(executable, args)) return runProcess(executable, args, options);
        const mode = await readEffectiveNetworkMode();
        if (!isLegacyBundledCli(executable, args, options.env ?? process.env, options.cwd)) {
            return runProcess(executable, cliNetworkArguments(args, mode), options);
        }
        // Setup already enforces consent and supplies this flag only after confirmation.
        const confirmedBootstrap = bootstrap && args.includes("--allow-large-download");
        if (mode !== "full" && !(mode === "low" && confirmedBootstrap)) {
            throw new HttpError(403, "The installed WGS Extract CLI cannot enforce this network mode. Update the CLI to run this operation.");
        }
        return runProcess(executable, confirmedBootstrap ? args.filter((arg) => arg !== "--allow-large-download") : args, options);
    };
}
