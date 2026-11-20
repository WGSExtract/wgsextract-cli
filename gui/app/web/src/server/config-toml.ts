import { parse, stringify } from "smol-toml";

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
