// Maps `wgsextract --json ref library --list` (the catalog's genomes with their
// state in the library) to the reference-genome controls' payloads: `options`
// for the dropdowns and `items` for the Reference Genomes page's table. The CLI
// owns the catalog and the library's state; only how the app shows them is here.
import path from "node:path";

// Library rows after this catalog entry (THGySeqp from YSEQ) are for the app's Advanced mode only.
const advancedAfterCode = "THGySeqp";

const buildRules = [
    [["GRCh37", "hg19", "hs37", "hg37"], "GRCh37 / hg19"],
    [["GRCh38", "hg38", "hs38"], "GRCh38 / hg38"],
    [["T2T", "CHM13", "chm13", "HG002"], "T2T / CHM13"],
    [["Dog", "Canis", "GSD"], "Dog"],
    [["Cat", "Felis", "Fca"], "Cat"],
];

/** The installed genomes (indexed or not), one per file, the first selected. */
export function options(document) {
    const { genomes, genomesDir } = libraryList(document);
    const seen = new Set();
    const offered = [];
    for (const genome of genomes) {
        if (seen.has(genome.file) || !["installed", "unindexed"].includes(genome.status)) {
            continue;
        }
        seen.add(genome.file);
        offered.push({
            id: path.join(genomesDir, genome.file),
            title: genome.label,
            status: genome.status,
            ...(offered.length === 0 ? { selected: true } : {}),
        });
    }
    return { options: offered };
}

/** Every catalog row (one per source), with its status and size on disk, else its download size. */
export function items(document) {
    const { genomes, genomesDir } = libraryList(document);
    let advanced = false;
    const rows = genomes.map((genome) => {
        const name = cleanLabel(genome.label, genome.source);
        const description = genome.description ?? "";
        const row = {
            id: `${genome.code}-${genome.source}-${genome.file}`,
            title: name,
            status: genome.status,
            tags: genome.label.includes("(Rec)") ? [{ id: "recommended", title: "Recommended", style: "primary" }] : [],
            tooltip: description,
            values: {
                name,
                build: buildLabel(genome.code, genome.file, description),
                source: genome.source,
                code: genome.code,
                final: genome.file,
                ref: path.join(genomesDir, genome.file),
                size: humanBytes(genome.size_bytes) || humanBytes(genome.remote_bytes),
                description,
                sn: genome.sn_count == null ? "" : String(genome.sn_count),
            },
            ...(advanced ? { advancedOnly: true } : {}),
        };
        advanced ||= genome.code === advancedAfterCode;
        return row;
    });
    return { items: rows };
}

function libraryList(document) {
    const details = document?.details;
    if (!Array.isArray(details?.genomes) || typeof details.genomes_dir !== "string") {
        throw new Error("wgsextract ref library --list printed no genome list.");
    }
    return { genomes: details.genomes, genomesDir: details.genomes_dir };
}

function buildLabel(code, file, description) {
    const blob = `${code} ${file} ${description}`;
    const rule = buildRules.find(([tokens]) => tokens.some((token) => blob.includes(token)));
    return rule ? rule[1] : file;
}

function cleanLabel(label, source) {
    const primary = source.endsWith("-Alt") ? source.slice(0, -4) : source;
    let cleaned = label;
    for (const token of ["(Rec)", `(${source})`, `(${primary})`]) {
        if (token !== "()") {
            cleaned = cleaned.replaceAll(token, "");
        }
    }
    // The species' common name says enough; the subspecies only crowds the table.
    return cleaned.replaceAll("Canis lupus familiaris", "Canis lupus").replace(/ {2,}/g, " ").trim();
}

/** Bytes as "1.5 GB", "1.5 MB" or "1.5 KB" (binary units), "" for none. */
function humanBytes(total) {
    if (!Number.isInteger(total) || total <= 0) {
        return "";
    }
    const [unit, suffix] = total > 1_073_741_824 ? [1_073_741_824, "GB"] : total > 1_048_576 ? [1_048_576, "MB"] : [1024, "KB"];
    // Tenths, rounded half to even (as Python's `:.1f`, which printed these sizes before), in exact integers.
    let tenths = Math.floor((total * 10) / unit);
    const twiceRemainder = 2 * (total * 10 - tenths * unit);
    if (twiceRemainder > unit || (twiceRemainder === unit && tenths % 2 === 1)) {
        tenths += 1;
    }
    return `${Math.floor(tenths / 10)}.${tenths % 10} ${suffix}`;
}
