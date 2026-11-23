// Maps `wgsextract --json ref library --list` (the catalog's genomes with their
// state in the library) to the reference-genome controls' payloads: `options`
// for the dropdowns and `items` for the Reference Genomes page's table. The CLI
// owns the catalog and the library's state; only how the app shows them is here.
import path from "node:path";

import { formatProvenanceTooltip } from "./tooltip-format.mjs";

// Library rows after this catalog entry (THGySeqp from YSEQ) are for the app's Advanced mode only.
const advancedAfterCode = "THGySeqp";

const T2T_CHM13 = {
    source: "https://github.com/marbl/CHM13",
    organization: "Telomere-to-Telomere (T2T) Consortium / NHGRI",
    license: "CC0 / Public domain (open research assembly; cite Nurk et al., Science 2022).",
};
const T2T_HPP = {
    source: "https://humanpangenome.org/",
    organization: "Human Pangenome Reference Consortium (HPRC) & T2T Consortium",
    license: "CC0 / Open release under HPRC and T2T open data policies; cite HPRC and T2T.",
};
const T2T_HG002 = {
    source: "https://github.com/marbl/HG002",
    organization: "Telomere-to-Telomere (T2T) Consortium & Genome in a Bottle (NIST)",
    license: "CC0 / Public domain (openly consented GIAB HG002 sample; cite T2T and GIAB).",
};
const GRC_1000G = {
    source: "https://www.internationalgenome.org/ (GRC: https://www.ncbi.nlm.nih.gov/grc/human)",
    organization: "Genome Reference Consortium (GRC) / 1000 Genomes Project (IGSR / NCBI / EMBL-EBI)",
    license: "Public domain / unrestricted use; acknowledge GRC and the 1000 Genomes Project (IGSR).",
};
const UCSC_GRC = {
    source: "https://genome.ucsc.edu/",
    organization: "UCSC Genomics Institute / Genome Reference Consortium (GRC)",
    license: "Free for any purpose under UCSC download terms (GRC assembly data); credit UCSC and GRC.",
};
const ENSEMBL_GRC = {
    source: "https://www.ensembl.org/",
    organization: "EMBL-EBI / Ensembl & Genome Reference Consortium (GRC)",
    license: "Open data without restriction under Ensembl's data disclaimer; credit Ensembl and GRC.",
};
const GENCODE_GRC = {
    source: "https://www.gencodegenes.org/",
    organization: "GENCODE Consortium (EMBL-EBI / Wellcome Sanger Institute)",
    license: "Open data without restriction; credit GENCODE and GRC.",
};
const YSEQ_REF = {
    source: "https://www.yseq.net/",
    organization: "YSEQ GmbH (built from public GRC/UCSC/T2T sequences)",
    license: "Publicly hosted reference assembly derived from GRC/UCSC/T2T public data; check YSEQ for redistribution terms.",
};
const SEQUENCING_REF = {
    source: "https://sequencing.com/ (based on GRC/1000 Genomes hs38d1)",
    organization: "Sequencing.com / Genome Reference Consortium (GRC)",
    license: "Derived from public-domain GRC/NCBI GRCh38 sequences; provided for alignment compatibility.",
};
const DOG_GSD = {
    source: "https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_011100685.1/",
    organization: "Uppsala University / NCBI RefSeq",
    license: "Public domain / unrestricted use under NCBI molecular data policies; credit Uppsala University and NCBI.",
};
const CAT_FCA = {
    source: "https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_018350175.1/",
    organization: "University of Missouri / NCBI RefSeq",
    license: "Public domain / unrestricted use under NCBI molecular data policies; credit University of Missouri and NCBI.",
};

const GENOME_PROVENANCE = {
    T2Tv20: T2T_CHM13,
    T2Tv20a: T2T_CHM13,
    T2Tv11: T2T_CHM13,
    T2Tv10: T2T_CHM13,
    T2Tv09: T2T_CHM13,
    HPPv11: T2T_HPP,
    HPPv1: T2T_HPP,
    THGv27: T2T_HG002,
    THGv20: T2T_HG002,
    THG1243v3: {
        source: "https://ccb.jhu.edu/data/PR1/",
        organization: "Center for Computational Biology, Johns Hopkins University",
        license: "Open public/academic release (GenBank/JHU); cite Zimin et al., 2021.",
    },
    THGySeqp: YSEQ_REF,
    hg37: YSEQ_REF,
    hs38: GRC_1000G,
    hs38a: GRC_1000G,
    hs38d1: GRC_1000G,
    hs38d1a: GRC_1000G,
    hs37: GRC_1000G,
    hs37d5: GRC_1000G,
    "hs37-": GRC_1000G,
    hs38DH: {
        source: "https://www.internationalgenome.org/",
        organization: "Genome Reference Consortium (GRC) / 1000 Genomes Project / IPD-IMGT/HLA",
        license: "GRC/1000G assembly is public domain; included IPD-IMGT/HLA sequences are CC BY-ND (do not mirror or redistribute modified HLA sets without permission).",
    },
    hs38d1s: SEQUENCING_REF,
    hs38d1t: SEQUENCING_REF,
    hs38d1c: {
        source: "https://github.com/WGSExtract/wgsextract-cli (based on GRC/NCBI hs38d1 + viral decoys)",
        organization: "Genome Reference Consortium (GRC) / NCBI",
        license: "Public domain (GRC/NCBI human reference and GenBank viral decoy sequences).",
    },
    hs38d1v: {
        source: "https://verily.com/ (based on GRC/NCBI hs38d1)",
        organization: "Verily Life Sciences / Genome Reference Consortium (GRC)",
        license: "Derived from public-domain GRC/NCBI GRCh38 sequences; provided for alignment compatibility.",
    },
    hg38: UCSC_GRC,
    hg19: UCSC_GRC,
    hg37w: {
        source: "https://github.com/WGSExtract/wgsextract-cli (based on UCSC hg19 + NCBI rCRS)",
        organization: "WGS Extract / UCSC Genomics Institute / NCBI",
        license: "Public domain / UCSC & NCBI reference sequence terms; credit GRC, UCSC, and NCBI.",
    },
    GRCh38: ENSEMBL_GRC,
    GRCh37: ENSEMBL_GRC,
    "GRCh38-": GENCODE_GRC,
    "GRCh37-": GENCODE_GRC,
    Dog_UU_GSD: DOG_GSD,
    UU_Cfam_GSD_1_0: DOG_GSD,
    Cat_Fca126: CAT_FCA,
};

function genomeProvenance(genome, tag) {
    if (genome.code && GENOME_PROVENANCE[genome.code]) {
        return GENOME_PROVENANCE[genome.code];
    }
    const blob = `${genome.code ?? ""} ${genome.file ?? ""} ${genome.label ?? ""} ${tag ?? ""}`;
    if (/Dog|Canis|GSD/i.test(blob)) return DOG_GSD;
    if (/Cat|Felis|Fca/i.test(blob)) return CAT_FCA;
    if (/HG002/i.test(blob)) return T2T_HG002;
    if (/CHM13|T2T/i.test(blob)) return T2T_CHM13;
    if (/Gencode/i.test(blob)) return GENCODE_GRC;
    if (/Ensembl/i.test(blob)) return ENSEMBL_GRC;
    if (/UCSC|hg19|hg38/i.test(blob)) return UCSC_GRC;
    return GRC_1000G;
}

// Fallback tag lookup when an older CLI or fixture omits `genome.tag`.
const fallbackTagByCode = {
    hg19: "hg19h",
    hg37: "hg37h",
    hg37w: "EBI37h",
    "GRCh37-": "EBI37h",
    GRCh37: "EBI37g",
    "hs37-": "1K37g",
    hs37: "1K37g",
    hs37d5: "1K37g",
    hg38: "hg38h",
    "GRCh38-": "hg38h",
    GRCh38: "EBI38g",
    hs38: "1K38h",
    hs38a: "1K38h",
    hs38d1: "1K38h",
    hs38d1a: "1K38h",
    hs38DH: "1K38h",
    hs38d1c: "1K38ph",
    hs38d1v: "1K38ph",
    hs38d1s: "1K38pg",
    hs38d1t: "1K38pg",
    T2Tv20: "T2T CHM13",
    T2Tv20a: "T2T CHM13",
    T2Tv11: "T2T CHM13",
    T2Tv10: "T2T CHM13",
    T2Tv09: "T2T CHM13",
    HPPv11: "T2T CHM13",
    HPPv1: "T2T CHM13",
    THG1243v3: "T2T PR1",
    THGv27: "T2T HG002",
    THGv20: "T2T HG002",
    THGySeqp: "T2T HG002",
    Dog_UU_GSD: "Dog",
    UU_Cfam_GSD_1_0: "Dog",
    Cat_Fca126: "Cat",
};

const fallbackTagRules = [
    [["hg01243", "PR1", "PuertoRican"], "T2T PR1"],
    [["HG002", "hg002"], "T2T HG002"],
    [["T2T", "CHM13", "chm13"], "T2T CHM13"],
    [["Dog", "Canis", "GSD"], "Dog"],
    [["Cat", "Felis", "Fca"], "Cat"],
    [["GRCh37", "hg19", "hs37", "hg37"], "GRCh37"],
    [["GRCh38", "hg38", "hs38"], "GRCh38"],
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

/** Every catalog row (one per source), with its status and model tag identifier. */
export function items(document) {
    const { genomes, genomesDir } = libraryList(document);
    let advanced = false;
    const rows = genomes.map((genome) => {
        const name = cleanLabel(genome.label, genome.source);
        const description = genome.description ?? "";
        const tag = modelTag(genome.tag, genome.code, genome.file, description);
        const provenance = genomeProvenance(genome, tag);
        const row = {
            id: `${genome.code}-${genome.source}-${genome.file}`,
            title: name,
            status: genome.status,
            tags: genome.label.includes("(Rec)") ? [{ id: "recommended", title: "Recommended", style: "primary" }] : [],
            tooltip: formatProvenanceTooltip({
                summary: description || `${name} reference genome assembly.`,
                ...provenance,
            }),
            values: {
                name,
                tag,
                source: genome.source,
                code: genome.code,
                final: genome.file,
                ref: path.join(genomesDir, genome.file),
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

function modelTag(explicitTag, code, file, description) {
    if (typeof explicitTag === "string" && explicitTag.trim()) {
        return explicitTag.trim();
    }
    if (code && fallbackTagByCode[code]) {
        return fallbackTagByCode[code];
    }
    const blob = `${code ?? ""} ${file ?? ""} ${description ?? ""}`;
    const rule = fallbackTagRules.find(([tokens]) => tokens.some((token) => blob.includes(token)));
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
