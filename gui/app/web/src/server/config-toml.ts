import { readFile } from "node:fs/promises";
import { parse, stringify } from "smol-toml";
import { errnoCode } from "./errors.js";

/** Read old library keys without shadowing a deliberately blank canonical value. */
export function parseConfigToml(text: string): Record<string, unknown> {
    const values = parse(text);
    if (!Object.hasOwn(values, "mygenomes_library")) {
        const old = values.dataset_library ?? values.genome_library;
        if (typeof old === "string") {
            values.mygenomes_library = old;
        }
    }
    return values;
}

export { stringify as serializeConfigToml };

export async function readOptionalFlatToml(filePath: string): Promise<Record<string, unknown> | null> {
    try {
        return parseConfigToml(await readFile(filePath, "utf8"));
    }
    catch (error) {
        if (errnoCode(error) === "ENOENT") {
            return null;
        }
        throw error;
    }
}
