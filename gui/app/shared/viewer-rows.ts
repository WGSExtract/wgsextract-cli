import type { ResultDocument } from "./result-document.js";
import type { LooseRecord } from "./types.js";
import { isViewerTrack } from "./viewer-result.js";

/**
 * Per-row genome viewer links for the tables a result document carries: a personal report's
 * findings and an mtDNA haplogroup's variants. A row names one explicit coordinate span, never a
 * gene or rsID, so opening it cannot trigger an online lookup. The viewer maps the contig name to
 * the matched reference (chr1 or 1, chrM or MT) when it opens the row.
 */
export type ViewerRowKind = "findings" | "mtVariants";

/** Why a row's link is disabled. `rowNoTrack` and `rowNoLocus` are known from the document alone. */
export type ViewerRowProblem = "rowNoLocus" | "rowNoTrack";

export interface ViewerRow {
    cells: string[];
    /** `chrom:start-end`, 1-based inclusive; empty when the row has no single position. */
    locus: string;
    /** The build the position is on, when its table says: the viewer refuses a reference of another build. */
    build?: ViewerBuild;
}

export type ViewerBuild = "hg19" | "hg38";

export function isViewerBuild(value: unknown): value is ViewerBuild {
    return value === "hg19" || value === "hg38";
}

export interface ViewerRowTable {
    kind: ViewerRowKind;
    /** The sample's BAM, CRAM or bgzip VCF the rows open; empty when the run read none. */
    track: string;
    rows: ViewerRow[];
}

/**
 * The pseudo-contig for Haplogrep's rCRS positions. The viewer maps it to the reference's
 * mitochondrion only when that is the 16,569 bp rCRS, never hg19's Yoruba chrM.
 */
export const rcrsContig = "rCRS";

export function viewerRowTables(document: ResultDocument): ViewerRowTable[] {
    const details = record(document.raw.details);
    if (!details) {
        return [];
    }
    const track = resultSampleTrack(document);
    const tables: ViewerRowTable[] = [];
    const findings = reportFindingRows(details);
    if (findings.length) {
        tables.push({ kind: "findings", track, rows: findings });
    }
    const variants = mtVariantRows(record(details.haplogrep));
    if (variants.length) {
        tables.push({ kind: "mtVariants", track, rows: variants });
    }
    return tables;
}

export function viewerRowProblem(table: ViewerRowTable, row: ViewerRow): ViewerRowProblem | undefined {
    return !row.locus ? "rowNoLocus" : !table.track ? "rowNoTrack" : undefined;
}

/** The run's own sample file: an existing BAM/CRAM input first, else a bgzip VCF input. */
export function resultSampleTrack(document: ResultDocument): string {
    const inputs = Array.isArray(document.raw.inputs) ? document.raw.inputs.map(record) : [];
    const tracks = inputs
        .filter((input): input is LooseRecord => input?.role === "input" && input.exists !== false && typeof input.path === "string" && isViewerTrack(input.path))
        .map((input) => String(input.path));
    return tracks.find((track) => /\.(bam|cram)$/i.test(track)) ?? tracks[0] ?? "";
}

/** A variant's span: its position through the last reference base it replaces. */
export function variantLocus(chrom: unknown, pos: unknown, ref?: unknown): string {
    if (typeof chrom !== "string" || !/^[A-Za-z0-9_.-]+$/.test(chrom) || !Number.isSafeInteger(pos) || Number(pos) < 1) {
        return "";
    }
    const length = typeof ref === "string" && /^[ACGTNacgtn]+$/.test(ref) ? ref.length : 1;
    return `${chrom}:${pos}-${Number(pos) + length - 1}`;
}

/**
 * A finding's span. A structural variant has no REF; its evidence region (`chrom:pos-end`) holds the
 * whole interval, so a deletion opens across all of it rather than at its first base.
 */
function findingLocus(variant: LooseRecord, region: unknown): string {
    const locus = variantLocus(variant.chrom, variant.pos, variant.ref);
    const span = typeof region === "string" ? /^([A-Za-z0-9_.-]+):(\d+)-(\d+)$/.exec(region) : null;
    if (!locus || variant.ref != null || !span || span[1] !== variant.chrom || Number(span[2]) !== variant.pos || Number(span[3]) < Number(span[2])) {
        return locus;
    }
    return `${span[1]}:${span[2]}-${span[3]}`;
}

/** Haplogrep's rCRS notation: `263G`, `315.1C` (insertion after 315), `8281-8289d` (deletion). */
export function mtVariantLocus(variant: string): string {
    const match = /^[ACGTN]?(\d+)(?:-(\d+))?/i.exec(variant);
    const start = Number(match?.[1]);
    const end = match?.[2] ? Number(match[2]) : start;
    return match && start >= 1 && end >= start ? `${rcrsContig}:${start}-${end}` : "";
}

function reportFindingRows(details: LooseRecord): ViewerRow[] {
    const sections = record(details.sections);
    if (details.schema !== "wgsextract.report/1" || !sections) {
        return [];
    }
    return Object.values(sections).flatMap((value) => {
        const section = record(value);
        const findings = Array.isArray(section?.findings) ? section.findings.map(record) : [];
        // ClinVar and ACMG sections name the build of their evidence VCF, which may not be the BAM's.
        const build = isViewerBuild(section?.build) ? section.build : undefined;
        return findings.filter((finding): finding is LooseRecord => finding !== undefined).map((finding) => {
            const variant = record(finding.variant);
            const locus = variant ? findingLocus(variant, record(finding.evidence)?.region) : "";
            return {
                cells: [text(section?.title), text(finding.label), text(finding.value), variant && locus ? variantText(variant) : ""],
                locus,
                ...(build && locus ? { build } : {}),
            };
        });
    });
}

function mtVariantRows(haplogrep: LooseRecord | undefined): ViewerRow[] {
    const found = haplogrep?.Found_Polys;
    if (typeof found !== "string") {
        return [];
    }
    return found.split(/\s+/).filter(Boolean).map((variant) => {
        const locus = mtVariantLocus(variant);
        const [start = "", end = ""] = locus.slice(rcrsContig.length + 1).split("-");
        return { cells: [variant, start === end ? start : `${start}–${end}`], locus };
    });
}

function variantText(variant: LooseRecord): string {
    const change = typeof variant.ref === "string" && typeof variant.alt === "string" ? ` ${variant.ref}>${variant.alt}` : "";
    return `${String(variant.chrom)}:${Number(variant.pos).toLocaleString("en-US")}${change}`;
}

function record(value: unknown): LooseRecord | undefined {
    return value != null && typeof value === "object" && !Array.isArray(value) ? value as LooseRecord : undefined;
}

function text(value: unknown): string {
    return typeof value === "string" ? value : value == null ? "" : String(value);
}
