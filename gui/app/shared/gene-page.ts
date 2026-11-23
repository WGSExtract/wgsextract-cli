import type { LooseRecord } from "./types.js";

/** The `wgsextract info gene` document (`details` of its result), as the gene page reads it. */
export interface GeneTranscript {
    accession: string;
    strand: string;
    start: number;
    end: number;
    cdsStart: number | null;
    cdsEnd: number | null;
    exons: [number, number][];
}

export interface GeneCoverageStats {
    mean: number;
    min: number;
    callableFraction: number;
}

export interface GeneExonCoverage extends GeneCoverageStats {
    start: number;
    end: number;
    below: boolean;
}

export interface GeneBin extends GeneCoverageStats {
    start: number;
    end: number;
}

export interface GeneNotice {
    code: string;
    message: string;
    command: string;
    annotation: string;
}

export type GeneFilter = "rare_damaging" | "clinvar_plp" | "acmg_plp";
export const geneFilters: readonly GeneFilter[] = ["rare_damaging", "clinvar_plp", "acmg_plp"];
export type GeneExport = "all" | "rare-damaging" | "clinvar-plp" | "acmg-plp";

export interface GeneVariant {
    values: Record<string, string | number | null>;
    filters: GeneFilter[];
}

export interface GeneDocument {
    build: string;
    gene: { symbol: string; chrom: string; start: number; end: number; locus: string; source: string };
    transcripts: { source: string; items: GeneTranscript[] };
    variants: {
        status: "ok" | "missing";
        file: string;
        annotations: string[];
        provenance: { annotation: string; header: string[] }[];
        rareAF: number;
        acmgDisclaimer: string;
        rows: GeneVariant[];
        total: number;
        truncated: boolean;
    };
    coverage: {
        status: "ok" | "missing";
        threshold: number;
        gene: GeneCoverageStats | null;
        bins: GeneBin[];
        exons: GeneExonCoverage[];
    };
    notices: GeneNotice[];
}

export class GeneDocumentError extends Error {}

const record = (value: unknown, what: string): LooseRecord => {
    if (value && typeof value === "object" && !Array.isArray(value)) return value as LooseRecord;
    throw new GeneDocumentError(`${what} is not an object.`);
};
const list = (value: unknown, what: string): unknown[] => {
    if (Array.isArray(value)) return value;
    throw new GeneDocumentError(`${what} is not a list.`);
};
const text = (value: unknown, what: string): string => {
    if (typeof value === "string") return value;
    throw new GeneDocumentError(`${what} is not text.`);
};
const optionalText = (value: unknown): string => typeof value === "string" ? value : "";
const number = (value: unknown, what: string): number => {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    throw new GeneDocumentError(`${what} is not a number.`);
};
const optionalNumber = (value: unknown, what: string): number | null => value == null ? null : number(value, what);
const interval = (value: unknown, what: string): [number, number] => {
    const [start, end] = list(value, what);
    return [number(start, what), number(end, what)];
};
const stats = (value: unknown, what: string): GeneCoverageStats => {
    const item = record(value, what);
    return { mean: number(item.mean, what), min: number(item.min, what), callableFraction: number(item.callable_fraction, what) };
};
const cell = (value: unknown): string | number | null => typeof value === "string" || (typeof value === "number" && Number.isFinite(value)) ? value : null;

/** Reads the CLI's document; a missing or malformed field is an error, never a guess. */
export function geneDocument(value: unknown): GeneDocument {
    const doc = record(value, "Gene document");
    if (doc.schema !== "wgsextract.gene/1") throw new GeneDocumentError("Not a wgsextract.gene/1 document.");
    const gene = record(doc.gene, "gene");
    const transcripts = record(doc.transcripts, "transcripts");
    const variants = record(doc.variants, "variants");
    const coverage = record(doc.coverage, "coverage");
    const variantsOk = variants.status === "ok";
    const coverageOk = coverage.status === "ok";
    return {
        build: text(doc.build, "build"),
        gene: {
            symbol: text(gene.symbol, "gene symbol"), chrom: text(gene.chrom, "gene contig"),
            start: number(gene.start, "gene start"), end: number(gene.end, "gene end"),
            locus: text(gene.locus, "gene locus"), source: optionalText(gene.source),
        },
        transcripts: {
            source: optionalText(transcripts.source),
            items: list(transcripts.items, "transcripts").map((value) => {
                const item = record(value, "transcript");
                return {
                    accession: text(item.accession, "accession"), strand: text(item.strand, "strand"),
                    start: number(item.start, "transcript start"), end: number(item.end, "transcript end"),
                    cdsStart: optionalNumber(item.cds_start, "CDS start"), cdsEnd: optionalNumber(item.cds_end, "CDS end"),
                    exons: list(item.exons, "exons").map((exon) => interval(exon, "exon")),
                };
            }),
        },
        variants: {
            status: variantsOk ? "ok" : "missing",
            file: optionalText(variants.file),
            annotations: variantsOk ? list(variants.annotations, "annotations").map((name) => text(name, "annotation")) : [],
            provenance: variantsOk ? list(variants.provenance, "provenance").map((value) => {
                const item = record(value, "provenance");
                return { annotation: text(item.annotation, "annotation"), header: list(item.header, "header").map((line) => text(line, "header line")) };
            }) : [],
            rareAF: variantsOk ? number(variants.rare_af, "rare AF") : 0,
            acmgDisclaimer: optionalText(variants.acmg_disclaimer),
            rows: variantsOk ? list(variants.rows, "variant rows").map((value) => {
                const row = record(value, "variant row");
                const filters = list(row.filters, "filters").filter((name): name is GeneFilter => geneFilters.includes(name as GeneFilter));
                return { values: Object.fromEntries(Object.entries(row).filter(([key]) => key !== "filters").map(([key, item]) => [key, cell(item)])), filters };
            }) : [],
            total: variantsOk ? number(variants.total, "variant total") : 0,
            truncated: variants.truncated === true,
        },
        coverage: {
            status: coverageOk ? "ok" : "missing",
            threshold: coverageOk ? number(coverage.threshold, "threshold") : 0,
            gene: coverageOk ? stats(coverage.gene, "gene coverage") : null,
            bins: coverageOk ? list(coverage.bins, "bins").map((value) => {
                const item = record(value, "bin");
                return { start: number(item.start, "bin start"), end: number(item.end, "bin end"), ...stats(item, "bin") };
            }) : [],
            exons: coverageOk ? list(coverage.exons, "exon coverage").map((value) => {
                const item = record(value, "exon coverage");
                return { start: number(item.start, "exon start"), end: number(item.end, "exon end"), below: item.below === true, ...stats(item, "exon") };
            }) : [],
        },
        notices: list(doc.notices, "notices").map((value) => {
            const item = record(value, "notice");
            return { code: text(item.code, "notice code"), message: optionalText(item.message), command: optionalText(item.command), annotation: optionalText(item.annotation) };
        }),
    };
}

/** The rows a filter shows; the CLI tags each row, so the page never re-derives a filter. */
export function filteredVariants(rows: GeneVariant[], filter: GeneFilter | "all"): GeneVariant[] {
    return filter === "all" ? rows : rows.filter((row) => row.filters.includes(filter));
}

export function geneExportName(filter: GeneFilter | "all"): GeneExport {
    return filter === "all" ? "all" : filter.replaceAll("_", "-") as GeneExport;
}

/** Exon numbers follow transcription: the first exon is the 5'-most on the transcript's strand. */
export function exonNumbers(transcript: GeneTranscript): Map<string, number> {
    const ordered = transcript.strand === "-" ? [...transcript.exons].reverse() : transcript.exons;
    return new Map(ordered.map(([start, end], index) => [`${start}-${end}`, index + 1]));
}
