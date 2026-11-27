import type { NetworkMode } from "../../../shared/network-settings.js";
import type { RunProcess } from "../../../shared/types.js";
import { readEffectiveNetworkMode } from "./network-settings.js";

const cliName = /(?:^|[\\/])(?:wgsextract(?:\.exe|\.cmd)?|run-wgsextract\.(?:sh|ps1))$/i;
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

export function cliNetworkArguments(args: string[], mode: NetworkMode) {
    const separator = args.indexOf("--");
    const options = separator < 0 ? args : args.slice(0, separator);
    const tail = separator < 0 ? [] : args.slice(separator);
    // `deps ready -- …` and similar commands forward their remaining arguments.
    return [...options, "--network", mode, ...tail];
}

export function withCliNetworkPolicy(runProcess: RunProcess): RunProcess {
    return async (executable, args, options) => {
        const governed = isWgsExtractCli(executable, args) || isBootstrapLibrary(executable, args);
        return runProcess(executable, governed ? cliNetworkArguments(args, await readEffectiveNetworkMode()) : args, options);
    };
}
