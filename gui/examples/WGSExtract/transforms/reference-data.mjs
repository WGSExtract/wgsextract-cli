// Maps `wgsextract --json ref status` into rows for the Reference Data table.
// Each row carries its status, version, target build,
// purpose, and either its on-disk size (when installed) or download size.
import { readdirSync, statSync } from "node:fs";
import path from "node:path";

import { formatProvenanceTooltip } from "./tooltip-format.mjs";

const PROVENANCE = {
    bootstrap: {
        summary: "Core reference support files: liftover chains, microarray chip templates, and N-count tables.",
        source: "https://github.com/WGSExtract/wgsextract-cli",
        organization: "WGS Extract (compiling UCSC liftOver chains and vendor chip templates)",
        license: "Mixed component terms: UCSC liftOver chains are free for academic/non-profit/personal use (commercial use requires a UCSC license); vendor templates remain subject to their original sources.",
    },
    "gene-map": {
        summary: "RefSeq gene-to-coordinate maps (GRCh37 & GRCh38) for VCF/BAM gene filtering and Genome Viewer gene search.",
        source: "https://www.ncbi.nlm.nih.gov/refseq/ (via https://genome.ucsc.edu/)",
        organization: "NCBI / UCSC Genomics Institute",
        license: "Public domain / unrestricted data use (NCBI molecular data & UCSC database terms); credit NCBI RefSeq and UCSC.",
    },
    "dbsnp-hg38": {
        summary: "NCBI dbSNP b151 GRCh38 common human SNPs (~37M rsIDs) plus offline SQLite rsID lookup index.",
        source: "https://www.ncbi.nlm.nih.gov/snp/",
        organization: "National Center for Biotechnology Information (NCBI / NIH)",
        license: "Public domain (US government work); free for any use with attribution to NCBI dbSNP.",
    },
    "dbsnp-hg19": {
        summary: "NCBI dbSNP b151 GRCh37 common human SNPs (~37M rsIDs) plus offline SQLite rsID lookup index.",
        source: "https://www.ncbi.nlm.nih.gov/snp/",
        organization: "National Center for Biotechnology Information (NCBI / NIH)",
        license: "Public domain (US government work); free for any use with attribution to NCBI dbSNP.",
    },
    "cytobands-hg38": {
        summary: "UCSC GRCh38 chromosome banding table for the Genome Viewer ideogram.",
        source: "https://genome.ucsc.edu/",
        organization: "UCSC Genomics Institute",
        license: "Free for any purpose under UCSC download terms (data only, not Genome Browser software); credit UCSC.",
    },
    "cytobands-hg19": {
        summary: "UCSC GRCh37 chromosome banding table for the Genome Viewer ideogram.",
        source: "https://genome.ucsc.edu/",
        organization: "UCSC Genomics Institute",
        license: "Free for any purpose under UCSC download terms (data only, not Genome Browser software); credit UCSC.",
    },
    "annotation-vcf": {
        summary: "Genome-wide GRCh37 and GRCh38 SNP target files built locally from the bootstrap chip templates.",
        source: "https://github.com/WGSExtract/wgsextract-cli",
        organization: "WGS Extract",
        license: "Built locally from the Library Bootstrap chip templates; subject to the underlying template terms.",
    },
    "aadr-2m": {
        summary: "Allen Ancient DNA Resource (AADR) v66.p1 2M SNP marker metadata panel for ancient DNA export.",
        source: "https://dataverse.harvard.edu/dataverse/reich_lab",
        organization: "David Reich Lab, Harvard Medical School",
        license: "CC0 1.0 (Public Domain Dedication); free for any use, citation of AADR recommended.",
    },
    ybrowse: {
        summary: "Sites-only GRCh38 Y-chromosome SNP names and haplogroup marker annotations (updated daily upstream).",
        source: "https://ybrowse.org/",
        organization: "Thomas Krahn / YSEQ",
        license: "CC BY-SA (Thomas Krahn, YSEQ); attribution required, share-alike if adapted or redistributed.",
    },
    vep: {
        summary: "Local Ensembl VEP / vep-rs transcript cache for offline variant consequence annotation.",
        source: "https://www.ensembl.org/info/docs/tools/vep/",
        organization: "EMBL-EBI / Ensembl",
        license: "Open data without restriction under Ensembl's data disclaimer (VEP software is Apache-2.0); credit Ensembl.",
    },
    spliceai: {
        summary: "Precomputed SpliceAI splicing impact scores for GRCh38 MANE Select SNVs.",
        source: "https://github.com/Illumina/SpliceAI",
        organization: "Illumina, Inc.",
        license: "CC BY-NC (academic, personal, and non-profit use only); commercial use requires an Illumina license.",
    },
    alphamissense: {
        summary: "AlphaMissense missense variant pathogenicity scores for GRCh37 and GRCh38 (not validated for clinical use).",
        source: "https://github.com/google-deepmind/alphamissense",
        organization: "Google DeepMind",
        license: "CC BY 4.0 (relicensed from CC BY-NC-SA 4.0 in Mar 2024); free to use with attribution to DeepMind / Cheng et al.",
    },
    pharmgkb: {
        summary: "Clinical pharmacogenomic variant-drug annotations.",
        source: "https://www.clinpgx.org/ (formerly https://www.pharmgkb.org/)",
        organization: "ClinPGx / PharmGKB (Stanford University / NIH)",
        license: "CC BY-SA 4.0 with research/no-sale restrictions (no commercial resale without permission); attribution required.",
    },
    clinvar: {
        summary: "ClinVar clinical significance and disease assertions (GRCh37 & GRCh38).",
        source: "https://www.ncbi.nlm.nih.gov/clinvar/",
        organization: "National Center for Biotechnology Information (NCBI / NIH)",
        license: "Public domain / no restrictions on use or distribution; credit NCBI ClinVar.",
    },
    revel: {
        summary: "REVEL v1.3 ensemble missense pathogenicity scores (GRCh37 & GRCh38).",
        source: "https://sites.google.com/site/revelgenomics/",
        organization: "Stanford University (Ioannidis et al.)",
        license: "Free for non-commercial / academic use; commercial users must contact the authors for a license.",
    },
    gnomad: {
        summary: "gnomAD population allele frequencies (v2.1.1 / v4.1) and Genome Viewer track.",
        source: "https://gnomad.broadinstitute.org/",
        organization: "Broad Institute of MIT and Harvard (gnomAD Consortium)",
        license: "CC0 (primary allele frequency data); acknowledge gnomAD and do not attempt to reidentify participants.",
    },
    cadd: {
        summary: "CADD v1.7 whole-genome deleteriousness scores for SNVs and indels (GRCh37 & GRCh38).",
        source: "https://cadd.gs.washington.edu/",
        organization: "University of Washington / Berlin Institute of Health (BIH)",
        license: "Free for non-commercial / academic use; commercial use requires a license from the University of Washington.",
    },
    gerp: {
        summary: "GERP++ evolutionary conservation Rejected Substitution (RS) scores (bigWig).",
        source: "https://www.ensembl.org/ (GRCh38) & https://genome.ucsc.edu/ (GRCh37)",
        organization: "EMBL-EBI Ensembl Compara (GRCh38) / Stanford Sidow Lab via UCSC (GRCh37)",
        license: "GRCh38: open Ensembl data with attribution; GRCh37: academic/research track via UCSC (check original terms for commercial use).",
    },
    phylop: {
        summary: "PhyloP 100-way vertebrate evolutionary conservation scores (bigWig).",
        source: "https://genome.ucsc.edu/",
        organization: "UCSC Genomics Institute / Cornell Siepel Lab & Gladstone Pollard Lab",
        license: "Free for public use under UCSC download terms; credit UCSC, Pollard, and Siepel.",
    },
};

function referenceDataTooltip(id, fallbackSummary) {
    const meta = PROVENANCE[id];
    if (!meta) {
        return formatProvenanceTooltip({ summary: fallbackSummary });
    }
    return formatProvenanceTooltip(meta);
}

/** Build the Reference Data table rows from `wgsextract --json ref status`. */
export function items(document) {
    const details = document?.details;
    if (!details || typeof details !== "object" || typeof details.referenceLibrary?.path !== "string") {
        throw new Error("wgsextract ref status returned no reference library details.");
    }
    const refRoot = details.referenceLibrary.path;
    const annotations = details.annotations ?? {};
    const vep = details.vepCache ?? {};
    const dbsnp = details.dbsnp ?? {};
    const vepVersions = Array.isArray(vep.versions) ? vep.versions : [];
    const bootstrap = details.bootstrap ?? {};
    const installedVersion = bootstrap.installedVersion ? `v${bootstrap.installedVersion}` : "";
    const availableVersion = bootstrap.availableVersion ? `v${bootstrap.availableVersion}` : "";
    const bootstrapVersion = bootstrap.installed && availableVersion && installedVersion !== availableVersion
        ? `${installedVersion || "—"} / ${availableVersion}`
        : installedVersion || availableVersion || "—";

    const rows = [
        row({
            id: "bootstrap",
            name: "Library Bootstrap",
            installed: Boolean(bootstrap.installed),
            updateAvailable: bootstrap.updateAvailable === true,
            tags: [{ id: "required", title: "Required", style: "primary" }],
            version: bootstrapVersion,
            build: "All",
            usedFor: "Liftover chains, chip templates, N-counts",
            downloadSize: Number.isFinite(bootstrap.downloadBytes) && bootstrap.downloadBytes > 0
                ? `${(bootstrap.downloadBytes / (1024 ** 2)).toFixed(1)} MiB`
                : "—",
            tooltip: "Core reference support files: liftover chains, microarray chip templates, and N-count tables.",
        }),
        row({
            id: "gene-map",
            name: "Gene Map",
            installed: Boolean(details.geneMap?.installed),
            tags: [{ id: "viewer", title: "Viewer", style: "secondary" }],
            version: "RefSeq",
            build: "37 / 38",
            usedFor: "Gene filtering & IGV gene spans/search",
            downloadSize: "~2 MB",
            installedBytes: findNamedBytes(refRoot, ["genes_hg19.tsv", "genes_hg38.tsv"]),
            tooltip: "Lightweight RefSeq gene-to-coordinate maps (GRCh37 and GRCh38) used by VCF/BAM gene filters and the Genome Viewer.",
        }),
        row({
            id: "dbsnp-hg38",
            name: "dbSNP Common rsIDs (GRCh38)",
            installed: Boolean(dbsnp.hg38?.installed),
            tags: [{ id: "viewer", title: "Viewer", style: "secondary" }],
            version: "b151 (~37M SNPs)",
            build: "38",
            usedFor: "IGV dbSNP track & offline rsID search (GRCh38)",
            downloadSize: "~1.5 GB",
            installedBytes: sumPaths([
                dbsnp.hg38?.vcf, dbsnp.hg38?.vcf ? `${dbsnp.hg38.vcf}.tbi` : "", dbsnp.hg38?.rsidIndex,
            ]),
            tooltip: "NCBI dbSNP b151 GRCh38 common human SNPs (~37M rsIDs) plus offline SQLite lookup index for the Genome Viewer.",
        }),
        row({
            id: "dbsnp-hg19",
            name: "dbSNP Common rsIDs (GRCh37)",
            installed: Boolean(dbsnp.hg19?.installed),
            tags: [{ id: "viewer", title: "Viewer", style: "secondary" }],
            version: "b151 (~37M SNPs)",
            build: "37",
            usedFor: "IGV dbSNP track & offline rsID search (GRCh37)",
            downloadSize: "~1.5 GB",
            installedBytes: sumPaths([
                dbsnp.hg19?.vcf, dbsnp.hg19?.vcf ? `${dbsnp.hg19.vcf}.tbi` : "", dbsnp.hg19?.rsidIndex,
            ]),
            tooltip: "NCBI dbSNP b151 GRCh37 common human SNPs (~37M rsIDs) plus offline SQLite lookup index for the Genome Viewer.",
        }),
        row({
            id: "cytobands-hg38",
            name: "Cytobands (GRCh38)",
            installed: Boolean(cytobandFile(refRoot, "hg38")),
            tags: [{ id: "viewer", title: "Viewer", style: "secondary" }],
            version: "UCSC",
            build: "38",
            usedFor: "IGV chromosome ideogram (GRCh38)",
            downloadSize: "~40 KB",
            installedBytes: sumPaths([cytobandFile(refRoot, "hg38")]),
            tooltip: "UCSC GRCh38 chromosome banding table for the Genome Viewer ideogram.",
        }),
        row({
            id: "cytobands-hg19",
            name: "Cytobands (GRCh37)",
            installed: Boolean(cytobandFile(refRoot, "hg19")),
            tags: [{ id: "viewer", title: "Viewer", style: "secondary" }],
            version: "UCSC",
            build: "37",
            usedFor: "IGV chromosome ideogram (GRCh37)",
            downloadSize: "~40 KB",
            installedBytes: sumPaths([cytobandFile(refRoot, "hg19")]),
            tooltip: "UCSC GRCh37 chromosome banding table for the Genome Viewer ideogram.",
        }),
        row({
            id: "annotation-vcf",
            name: "Annotation VCFs (Microarray)",
            installed: Boolean(details.annotationVcf?.installed),
            version: "Chip targets",
            build: "37 / 38",
            usedFor: "Microarray export & general VCF annotation",
            downloadSize: "~25 MB (built)",
            installedBytes: findNamedBytes(refRoot, [
                "All_SNPs_hg19_ref.tab.gz", "All_SNPs_hg19_ref.tab.gz.tbi",
                "All_SNPs_hg38_ref.tab.gz", "All_SNPs_hg38_ref.tab.gz.tbi",
            ]),
            tooltip: "Genome-wide GRCh37 and GRCh38 SNP target files built from the bootstrap chip templates.",
        }),
        row({
            id: "aadr-2m",
            name: "AADR 2M SNP Panel",
            installed: Boolean(details.aadr?.installed),
            version: "v66.p1 (2M SNPs)",
            build: "37 / 38",
            usedFor: "Ancient DNA / AADR microarray export",
            downloadSize: "~105 MB",
            installedBytes: sumPaths([details.aadr?.file]),
            tooltip: "Hash-pinned CC0 AADR v66.p1 2M SNP metadata panel from Harvard Dataverse.",
        }),
        row({
            id: "ybrowse",
            name: "yBrowse Y-SNPs",
            installed: Boolean(details.ybrowse?.installed),
            version: "GRCh38 snapshot",
            build: "38",
            usedFor: "Y-chromosome SNP names & extraction",
            downloadSize: "~25 MB",
            installedBytes: directoryBytes(details.ybrowse?.file),
            tooltip: "Verified sites-only GRCh38 yBrowse Y-SNP reference snapshot.",
        }),
        row({
            id: "vep",
            name: "VEP Cache",
            installed: vepVersions.length > 0,
            version: vepVersions.length > 0 ? vepVersions.join(", ") : "v116",
            build: "37 / 38",
            usedFor: "Variant effect prediction (VEP)",
            downloadSize: "~12–20 GB",
            installedBytes: 0,
            tooltip: "Local Ensembl VEP / vep-rs transcript cache for offline variant consequence annotation.",
        }),
        row({
            id: "spliceai",
            name: "SpliceAI Scores",
            installed: Boolean(details.spliceai?.installed),
            version: "v1.4 MANE",
            build: "38",
            usedFor: "Splicing impact annotation",
            downloadSize: "~1.4 GB",
            installedBytes: sumWithIndex(details.spliceai?.file),
            tooltip: "Precomputed SpliceAI scores for GRCh38 MANE Select SNVs (academic/non-profit terms).",
        }),
        row({
            id: "alphamissense",
            name: "AlphaMissense Scores",
            installed: Boolean(details.alphamissense?.installed),
            version: "v1",
            build: "37 / 38",
            usedFor: "Missense pathogenicity scores",
            downloadSize: "~1.2 GB",
            installedBytes: sumWithIndex(details.alphamissense?.file),
            tooltip: "AlphaMissense missense variant pathogenicity scores for GRCh37 and GRCh38.",
        }),
        row({
            id: "pharmgkb",
            name: "PharmGKB Annotations",
            installed: Boolean(details.pharmgkb?.installed),
            version: "Latest",
            build: "38",
            usedFor: "Pharmacogenomic variant annotation",
            downloadSize: "~15 MB",
            installedBytes: sumWithIndex(details.pharmgkb?.file),
            tooltip: "PharmGKB clinical pharmacogenomic annotations.",
        }),
        // Advanced annotation resources
        annotationRow("clinvar", "ClinVar", annotations.clinvar, "Latest (NCBI)", "37 / 38", "Clinical significance & IGV track", "~200 MB"),
        annotationRow("revel", "REVEL", annotations.revel, "v1.3", "37 / 38", "Missense pathogenicity scores", "~2.5 GB"),
        annotationRow("gnomad", "gnomAD", annotations.gnomad, "v2.1.1 / v4.1", "37 / 38", "Population allele frequencies & IGV track", "~10 GB (v2.1.1)"),
        annotationRow("cadd", "CADD", annotations.cadd, "v1.7", "37 / 38", "Whole-genome deleteriousness scores", "~85 GB / build"),
        annotationRow("gerp", "GERP++", annotations.gerp, "RS (bigWig)", "37 / 38", "Evolutionary conservation & IGV track", "~10 GB / build"),
        annotationRow("phylop", "PhyloP", annotations.phylop, "100-way (bigWig)", "37 / 38", "Evolutionary conservation & IGV track", "~10 GB / build"),
    ];
    return { items: rows };
}

function annotationRow(id, name, buildsMap, version, build, usedFor, downloadSize) {
    const entries = Object.values(buildsMap ?? {});
    const installed = entries.some((entry) => entry?.installed);
    const bytes = entries.reduce((sum, entry) => sum + sumWithIndex(entry?.file), 0);
    return row({
        id,
        name,
        installed,
        version,
        build,
        usedFor,
        downloadSize,
        installedBytes: bytes,
        tooltip: referenceDataTooltip(id, `${name} (${version}) — ${usedFor}.`),
        advancedOnly: true,
    });
}

function row({ id, name, installed, updateAvailable = false, tags = [], version, build, usedFor, downloadSize, installedBytes = 0, tooltip = "", advancedOnly = false }) {
    const diskSize = installed ? formatDiskBytes(installedBytes) : "";
    return {
        id,
        title: name,
        status: installed ? (updateAvailable ? "update-available" : "installed") : "missing",
        tags,
        tooltip: referenceDataTooltip(id, tooltip),
        values: {
            name,
            version,
            build,
            usedFor,
            size: diskSize || downloadSize,
        },
        ...(advancedOnly ? { advancedOnly: true } : {}),
    };
}

function cytobandFile(refRoot, build) {
    for (const sub of ["downloads", "ref"]) {
        const candidate = path.join(refRoot, sub, `cytobands_${build}.tsv`);
        if (fileBytes(candidate) > 0) return candidate;
    }
    return "";
}

function fileBytes(filePath) {
    if (!filePath || typeof filePath !== "string") return 0;
    try {
        const info = statSync(filePath);
        return info.isFile() ? info.size : 0;
    } catch {
        return 0;
    }
}

function sumPaths(paths) {
    let total = 0;
    for (const item of paths) total += fileBytes(item);
    return total;
}

function sumWithIndex(filePath) {
    if (!filePath) return 0;
    return fileBytes(filePath) + fileBytes(`${filePath}.tbi`) + fileBytes(`${filePath}.csi`);
}

function findNamedBytes(refRoot, names) {
    let total = 0;
    for (const name of names) {
        for (const sub of ["downloads", "ref", "microarray", "."]) {
            const bytes = fileBytes(path.join(refRoot, sub, name));
            if (bytes > 0) {
                total += bytes;
                break;
            }
        }
    }
    return total;
}

function directoryBytes(dirPath) {
    if (!dirPath || typeof dirPath !== "string") return 0;
    try {
        const info = statSync(dirPath);
        if (info.isFile()) return info.size;
        if (!info.isDirectory()) return 0;
        let total = 0;
        for (const entry of readdirSync(dirPath, { withFileTypes: true })) {
            const child = path.join(dirPath, entry.name);
            if (entry.isFile()) total += fileBytes(child);
        }
        return total;
    } catch {
        return 0;
    }
}

function formatDiskBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    const units = [
        [1024 ** 3, "GB"],
        [1024 ** 2, "MB"],
        [1024, "KB"],
    ];
    for (const [scale, label] of units) {
        if (bytes >= scale) {
            return `${(bytes / scale).toFixed(1)} ${label}`;
        }
    }
    return `${bytes} B`;
}
