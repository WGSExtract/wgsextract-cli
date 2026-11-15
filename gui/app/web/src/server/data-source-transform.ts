import { stat } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import type { DataSourcePayload, LooseRecord } from "../../../shared/types.js";
import { errorMessage } from "./errors.js";
import { resolveBundlePath } from "./paths.js";

/** A data source's `transform`: the bundle module export that maps what its command printed to a payload. */
export interface DataSourceTransform {
    module: string;
    export: string;
}

/** Validates a manifest data source's `transform` (bundle JSON, so untyped until here). */
export function dataSourceTransform(value: unknown, sourcePath: string): DataSourceTransform | undefined {
    if (value == null) {
        return undefined;
    }
    const spec = value as LooseRecord;
    if (typeof spec !== "object" || typeof spec.module !== "string" || !spec.module || typeof spec.export !== "string" || !spec.export) {
        throw new Error(`Data source ${sourcePath} transform needs a module and an export.`);
    }
    return { module: spec.module, export: spec.export };
}

/** Parses the JSON a data source's command printed and maps it with the transform's export. */
export async function transformedPayload(sourcePath: string, stdout: string, transform: DataSourceTransform, bundleRoot: string): Promise<DataSourcePayload> {
    let output: unknown;
    try {
        output = JSON.parse(stdout);
    }
    catch (error) {
        throw new Error(`Data source ${sourcePath} did not print valid JSON: ${errorMessage(error)}`);
    }
    const map = (await transformModule(transform.module, bundleRoot))[transform.export];
    if (typeof map !== "function") {
        throw new Error(`Data source transform ${transform.module} has no function ${transform.export}.`);
    }
    const payload: unknown = await map(output);
    if (payload == null || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error(`Data source transform ${transform.module} ${transform.export} did not return an object.`);
    }
    return payload as DataSourcePayload;
}

async function transformModule(modulePath: string, bundleRoot: string): Promise<Record<string, unknown>> {
    const file = resolveBundlePath(modulePath, bundleRoot);
    // Keyed by modification time, so an edited bundle module is loaded again.
    const { mtimeMs } = await stat(file);
    return await import(`${pathToFileURL(file).href}?mtime=${mtimeMs}`) as Record<string, unknown>;
}
