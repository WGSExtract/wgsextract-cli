import { DatabaseSync } from "node:sqlite";
import path from "node:path";
import type { ViewerRsidStatus } from "../../../shared/genome-viewer.js";
import { guardedViewerPath, isMissingViewerPath } from "./viewer-files.js";
import { viewerContigAliases, viewerReferenceMetadata } from "./viewer-reference-metadata.js";

const rsidPattern = /^rs(\d{1,12})$/i;
const rsidWindowBp = 100;
const rsidHalfWindow = 49;

export interface ViewerRsidIndex {
    path: string;
    build: string;
    reference: Map<string, number>;
}

export type ViewerRsidLookupResult =
    | { locus: string; rsid: string }
    | { problem: "invalidLocus" | "rsidNotFound" | "rsidIndexInvalid" };

function openReadOnlyDatabase(file: string): DatabaseSync {
    return new DatabaseSync(file, { readOnly: true });
}

function isValidRsidDatabase(file: string, expectedBuild: string): boolean {
    let db: DatabaseSync | undefined;
    try {
        db = openReadOnlyDatabase(file);
        const rows = db.prepare("SELECT key, value FROM meta").all() as Array<{ key?: unknown; value?: unknown }>;
        const meta = new Map<string, string>();
        for (const row of rows) {
            if (typeof row.key === "string" && typeof row.value === "string") {
                meta.set(row.key, row.value);
            }
        }
        if (meta.get("schema_version") !== "1" || meta.get("build") !== expectedBuild) return false;
        const count = Number(meta.get("count") ?? "0");
        if (!Number.isSafeInteger(count) || count < 1) return false;
        const sample = db.prepare("SELECT c.name, r.pos FROM rsids r JOIN chroms c ON c.cid = r.cid LIMIT 1").get();
        return sample !== undefined;
    } catch {
        return false;
    } finally {
        db?.close();
    }
}

/** Auto-discovers the installed offline dbSNP common rsID SQLite lookup DB for the reference build. */
export async function installedViewerRsidIndex(
    fasta: string,
    referenceRoot: string,
): Promise<{ status: ViewerRsidStatus; index?: ViewerRsidIndex }> {
    const metadata = await viewerReferenceMetadata(fasta);
    if (!metadata || !metadata.build) {
        return { status: { available: false, fileName: "dbsnp_common.rsid.sqlite", notices: [] } };
    }
    const { reference, build } = metadata;
    const fileName = `dbsnp_common_${build}.rsid.sqlite`;
    let sawInvalid = false;
    for (const directory of ["downloads", "ref", "."]) {
        const candidate = path.join(referenceRoot, directory, fileName);
        try {
            const canonical = await guardedViewerPath(candidate, [referenceRoot]);
            if (!isValidRsidDatabase(canonical, build)) {
                sawInvalid = true;
                continue;
            }
            return {
                status: { available: true, fileName, build, notices: [] },
                index: { path: canonical, build, reference },
            };
        } catch (error) {
            if (isMissingViewerPath(error)) continue;
            sawInvalid = true;
        }
    }
    const displayBuild = build === "hg38" ? "GRCh38" : build === "hg19" ? "GRCh37" : build;
    return {
        status: {
            available: false,
            fileName,
            build,
            notices: [{ code: sawInvalid ? "rsidIndexInvalid" : "rsidMissing", fileName, build: displayBuild }],
        },
    };
}

/** Looks up one rsID in the session's verified local SQLite index and returns a 100 bp window. */
export async function lookupViewerRsid(
    index: ViewerRsidIndex,
    query: string,
    referenceRoots: string[],
): Promise<ViewerRsidLookupResult> {
    const match = rsidPattern.exec(query.trim());
    if (!match?.[1]) return { problem: "invalidLocus" };
    const numericId = Number(match[1]);
    if (!Number.isSafeInteger(numericId) || numericId < 1) return { problem: "invalidLocus" };
    let canonical: string;
    try {
        canonical = await guardedViewerPath(index.path, referenceRoots);
    } catch {
        return { problem: "rsidIndexInvalid" };
    }
    let db: DatabaseSync | undefined;
    try {
        db = openReadOnlyDatabase(canonical);
        const row = db.prepare(
            "SELECT c.name AS chrom, r.pos AS pos FROM rsids r JOIN chroms c ON c.cid = r.cid WHERE r.rsid = ?",
        ).get(numericId) as { chrom?: unknown; pos?: unknown } | undefined;
        if (!row || typeof row.chrom !== "string" || typeof row.pos !== "number") {
            return { problem: "rsidNotFound" };
        }
        const pos = row.pos;
        const matches = viewerContigAliases(row.chrom).filter((candidate) => index.reference.has(candidate));
        const chr = matches.length === 1 ? matches[0] : undefined;
        const length = chr ? index.reference.get(chr) : undefined;
        if (!chr || !length || !Number.isSafeInteger(pos) || pos < 1 || pos > length) {
            return { problem: "rsidNotFound" };
        }
        let start = Math.max(1, pos - rsidHalfWindow);
        const end = Math.min(length, start + rsidWindowBp - 1);
        start = Math.max(1, end - rsidWindowBp + 1);
        return { locus: `${chr}:${start}-${end}`, rsid: `rs${match[1]}` };
    } catch {
        return { problem: "rsidIndexInvalid" };
    } finally {
        db?.close();
    }
}
