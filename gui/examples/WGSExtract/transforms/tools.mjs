// Maps `wgsextract --json deps list` (every mandatory and optional tool, with
// its status and what `deps install` / `deps uninstall` can do about it) to the
// Tools page's table. The CLI decides what is installed, installable or
// unavailable on this computer; only how the app shows it is here.
//
// `values.action` picks the row's button in pages/tools.json: Install,
// Uninstall, or a disabled "Install it yourself" / "Not available" in their
// place, whose tooltip is the CLI's note. `deps list` probes every tool's
// version, so the page gives its data source 300 seconds (half a minute on
// Windows, more on a first start).

import { formatProvenanceTooltip } from "./tooltip-format.mjs";

const HTSLIB_META = {
    source: "https://www.htslib.org/",
    organization: "SAMtools / HTSlib team (Wellcome Sanger Institute & Genome Research Ltd)",
    license: "MIT / Expat (permissive open source; free for personal, academic, and commercial use).",
};
const OPENJDK_META = {
    source: "https://openjdk.org/",
    organization: "OpenJDK Community",
    license: "GPL-2.0 with Classpath Exception (free to run and distribute; linking does not force GPL on apps).",
};
const GLIMPSE2_META = {
    source: "https://odelaneau.github.io/GLIMPSE/",
    organization: "Olivier Delaneau Lab (University of Lausanne)",
    license: "MIT (permissive open source; free for personal, academic, and commercial use).",
};
const VEP_RS_META = {
    source: "https://github.com/natera-open-source/vep-rs",
    organization: "Natera Open Source",
    license: "Apache-2.0 (permissive open source; free for personal, academic, and commercial use).",
};
const PYTHON_META = {
    summary: "Python 3 interpreter powering WGS Extract scripts and workflows.",
    source: "https://www.python.org/",
    organization: "Python Software Foundation (PSF)",
    license: "PSF License v2 (permissive open source; free for personal, academic, and commercial use).",
};
const DEEPVARIANT_META = {
    source: "https://github.com/google/deepvariant",
    organization: "Google Health Genomics / WGS Extract native build",
    license: "BSD-3-Clause (permissive open source; free for personal, academic, and commercial use).",
};
const ILLUMINA_POLYFORM = {
    organization: "Illumina, Inc.",
    license: "PolyForm Strict 1.0.0 (source-available; non-commercial / restricted redistribution—check Illumina license for commercial use).",
};

const TOOL_PROVENANCE = {
    "Python Runtime": PYTHON_META,
    python3: PYTHON_META,
    samtools: {
        summary: "Reads, writes, indexes, sorts, and inspects SAM/BAM/CRAM alignment files.",
        ...HTSLIB_META,
    },
    bcftools: {
        summary: "Calls, filters, merges, queries, and manipulates VCF/BCF variant files.",
        source: "https://www.htslib.org/",
        organization: "SAMtools / HTSlib team (Wellcome Sanger Institute & Genome Research Ltd)",
        license: "MIT / GPL-3.0 (dual-licensed; free for personal, academic, and commercial use; GPL-3.0 applies when built with GSL).",
    },
    tabix: {
        summary: "Builds and queries position indexes (.tbi/.csi) on BGZF-compressed VCF/BED/TSV files.",
        ...HTSLIB_META,
    },
    bgzip: {
        summary: "Block-level gzip compressor (BGZF) required for indexed BAM/VCF/BED files.",
        ...HTSLIB_META,
    },
    htsfile: {
        summary: "Identifies SAM/BAM/CRAM/VCF/BCF and compression formats.",
        ...HTSLIB_META,
    },
    bwa: {
        summary: "Burrows-Wheeler Aligner (BWA-MEM) for short-read DNA alignment.",
        source: "https://github.com/lh3/bwa",
        organization: "Heng Li (Dana-Farber Cancer Institute / Harvard Medical School)",
        license: "GPL-3.0 (open source; free to use; redistributed modifications must stay GPL-3.0).",
    },
    "bwa-mem2": {
        summary: "SIMD-accelerated successor to BWA-MEM producing identical alignments faster.",
        source: "https://github.com/bwa-mem2/bwa-mem2",
        organization: "Intel Labs & Heng Li",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    "bwa-mem3": {
        summary: "High-performance BWA-MEM3 short-read aligner compatible with bwa-mem2 indexes.",
        source: "https://github.com/fg-labs/bwa-mem3",
        organization: "Fulcrum Genomics (fg-labs)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    tar: {
        summary: "Archive utility used to unpack downloaded reference and data bundles.",
        source: "https://www.gnu.org/software/tar/",
        organization: "GNU Project / OS vendor (BSD tar on macOS)",
        license: "GPL-3.0 (GNU tar) or BSD-2-Clause (libarchive/macOS bsdtar); free to use.",
    },
    minimap2: {
        summary: "Versatile aligner for PacBio HiFi, Oxford Nanopore long reads, and assemblies.",
        source: "https://github.com/lh3/minimap2",
        organization: "Heng Li (Dana-Farber Cancer Institute / Harvard Medical School)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    pbmm2: {
        summary: "PacBio minimap2 front-end tuned for HiFi/CCS and CLR long reads.",
        source: "https://github.com/PacificBiosciences/pbmm2",
        organization: "Pacific Biosciences (PacBio)",
        license: "BSD-3-Clause-Clear (permissive open source with no implicit patent grant).",
    },
    pbsv: {
        summary: "PacBio structural variant caller for HiFi/CLR alignments.",
        source: "https://github.com/PacificBiosciences/pbsv",
        organization: "Pacific Biosciences (PacBio)",
        license: "BSD-3-Clause-Clear (permissive open source with no implicit patent grant).",
    },
    sniffles: {
        summary: "Fast structural variant caller (Sniffles2) for long-read alignments.",
        source: "https://github.com/fritzsedlazeck/Sniffles",
        organization: "Fritz Sedlazeck Lab (Baylor College of Medicine)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    sambamba: {
        summary: "Multi-threaded tool for sorting, indexing, and marking duplicates in BAM files.",
        source: "https://lomereiter.github.io/sambamba/",
        organization: "Artem Tarasov & Pjotr Prins (BioD / Sambamba contributors)",
        license: "GPL-2.0+ (open source; free to use; redistributed modifications must stay GPL-2.0+).",
    },
    samblaster: {
        summary: "Streams paired-end SAM alignments to mark duplicates and extract split/discordant reads.",
        source: "https://github.com/GregoryFaust/samblaster",
        organization: "Gregory Faust & Ira Hall Lab (University of Virginia / WashU)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    fastp: {
        summary: "All-in-one FASTQ preprocessor for adapter trimming, quality filtering, and QC reports.",
        source: "https://github.com/OpenGene/fastp",
        organization: "OpenGene (Shifu Chen et al.)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    fastqc: {
        summary: "Per-base quality control report generator for raw FASTQ sequencing reads.",
        source: "https://www.bioinformatics.babraham.ac.uk/projects/fastqc/",
        organization: "Babraham Bioinformatics (Babraham Institute)",
        license: "GPL-3.0 (open source; free to use; redistributed modifications must stay GPL-3.0).",
    },
    multiqc: {
        summary: "Aggregates QC metrics and plots from multiple bioinformatics tools into one HTML report.",
        source: "https://multiqc.info/",
        organization: "Seqera Labs / Phil Ewels & MultiQC contributors",
        license: "GPL-3.0 (open source; free to use; redistributed modifications must stay GPL-3.0).",
    },
    delly: {
        summary: "Integrated structural variant caller for paired-end short-read and long-read WGS.",
        source: "https://github.com/dellytools/delly",
        organization: "Tobias Rausch (EMBL)",
        license: "BSD-3-Clause (permissive open source; free for personal, academic, and commercial use).",
    },
    freebayes: {
        summary: "Bayesian haplotype-based small-variant (SNP/indel) caller.",
        source: "https://github.com/freebayes/freebayes",
        organization: "Erik Garrison & Gabor Marth Lab",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    vep: {
        summary: "Ensembl Variant Effect Predictor (Perl) for transcript consequence annotation.",
        source: "https://www.ensembl.org/info/docs/tools/vep/",
        organization: "EMBL-EBI / Ensembl",
        license: "Apache-2.0 (permissive open source; free for personal, academic, and commercial use).",
    },
    "vep-rs": {
        summary: "Fast Rust reimplementation of Ensembl VEP supporting GRCh37, GRCh38, and T2T-CHM13.",
        ...VEP_RS_META,
    },
    "vep-cache-builder": {
        summary: "Builds local offline transcript caches for vep-rs from Ensembl release files.",
        ...VEP_RS_META,
    },
    fastvep: {
        summary: "High-speed Rust variant annotation and research ACMG/AMP classification engine.",
        source: "https://github.com/Huang-lab/fastVEP",
        organization: "Huang Lab",
        license: "Apache-2.0 (permissive open source; free for personal, academic, and commercial use).",
    },
    gatk: {
        summary: "Genome Analysis Toolkit (GATK4) for HaplotypeCaller variant calling and BAM/VCF utilities.",
        source: "https://gatk.broadinstitute.org/",
        organization: "Broad Institute of MIT and Harvard",
        license: "BSD-3-Clause (permissive open source; free for personal, academic, and commercial use).",
    },
    run_deepvariant: {
        summary: "Deep-learning small-variant caller (DeepVariant) for short and long reads.",
        ...DEEPVARIANT_META,
    },
    "wgse-deepvariant": {
        summary: "Native (Docker-free) build of Google DeepVariant for Apple Silicon and Linux.",
        ...DEEPVARIANT_META,
    },
    "run_clair3.sh": {
        summary: "Fast deep-learning germline small-variant caller for Nanopore, PacBio HiFi, and Illumina reads.",
        source: "https://github.com/HKU-BAL/Clair3",
        organization: "Luo Lab, The University of Hong Kong (HKU-BAL)",
        license: "BSD-3-Clause (permissive open source; free for personal, academic, and commercial use).",
    },
    modkit: {
        summary: "Oxford Nanopore toolkit for summarizing and converting base-modification (5mC/5hmC) calls.",
        source: "https://github.com/nanoporetech/modkit",
        organization: "Oxford Nanopore Technologies (ONT)",
        license: "Oxford Nanopore Technologies PLC Public License v1.0 (source-available; check ONT terms for commercial/redistribution limits).",
    },
    verifybamid2: {
        summary: "Estimates DNA sample contamination and ancestry PCs directly from BAM/CRAM alignments.",
        source: "https://github.com/Griffan/VerifyBamID",
        organization: "Hyun Min Kang Lab (University of Michigan)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    somalier: {
        summary: "Fast sample-swap, relatedness, sex, and ancestry check across BAM/CRAM/VCF files.",
        source: "https://github.com/brentp/somalier",
        organization: "Brent Pedersen / Aaron Quinlan Lab (University of Utah)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    plink2: {
        summary: "PLINK 2.0 genome-wide association and polygenic score (PGS) calculation toolkit.",
        source: "https://www.cog-genomics.org/plink/2.0/",
        organization: "Christopher Chang & Shaun Purcell (COG Genomics)",
        license: "GPL-3.0 (open source; free to use; redistributed modifications must stay GPL-3.0).",
    },
    whatshap: {
        summary: "Read-backed haplotype phasing and haplotagging for long and short reads.",
        source: "https://whatshap.readthedocs.io/",
        organization: "Tobias Marschall & Alexander Schönhuth groups",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    longphase: {
        summary: "Ultra-fast long-read phasing for SNPs, indels, structural variants, and methylation.",
        source: "https://github.com/twolinin/longphase",
        organization: "Yao-Ting Huang Lab (National Chung Cheng University)",
        license: "GPL-3.0 (open source; free to use; redistributed modifications must stay GPL-3.0).",
    },
    GLIMPSE2_phase: {
        summary: "GLIMPSE2 low-coverage whole-genome imputation and phasing engine.",
        ...GLIMPSE2_META,
    },
    GLIMPSE2_ligate: {
        summary: "GLIMPSE2 utility that ligates imputed chromosome chunks into whole-chromosome VCFs.",
        ...GLIMPSE2_META,
    },
    GLIMPSE2_chunk: {
        summary: "GLIMPSE2 utility that partitions chromosomes into imputation windows.",
        ...GLIMPSE2_META,
    },
    GLIMPSE2_split_reference: {
        summary: "GLIMPSE2 utility that converts reference panels into compact binary chunks.",
        ...GLIMPSE2_META,
    },
    curl: {
        summary: "Command-line HTTP/HTTPS/FTP transfer tool used for resumable downloads.",
        source: "https://curl.se/",
        organization: "Daniel Stenberg & the curl Project",
        license: "curl License (MIT-style permissive open source; free for any use).",
    },
    java: {
        summary: "Default OpenJDK Java runtime used by Haplogrep3, Haplocheck, GATK, and FastQC.",
        ...OPENJDK_META,
    },
    java17: {
        summary: "OpenJDK 17 runtime required by DISCVRSeq VariantQC (GATK4-based JAR).",
        ...OPENJDK_META,
    },
    haplogrep3: {
        summary: "Mitochondrial DNA (mtDNA) haplogroup classification engine.",
        source: "https://haplogrep.i-med.ac.at/ (https://github.com/genepi/haplogrep3)",
        organization: "Institute of Genetic Epidemiology, Medical University of Innsbruck",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    haplocheck: {
        summary: "Detects mitochondrial DNA contamination and heteroplasmy from BAM/VCF files.",
        source: "https://github.com/genepi/haplocheck",
        organization: "Institute of Genetic Epidemiology, Medical University of Innsbruck",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    pharmcat_python: {
        summary: "PharmCAT pharmacogenomics allele caller and guideline report generator.",
        source: "https://pharmcat.org/ (https://github.com/PharmGKB/PharmCAT)",
        organization: "PharmGKB / ClinPGx & University of Pennsylvania",
        license: "MPL-2.0 (weak copyleft open source; free to use; modified MPL source files must stay MPL-2.0).",
    },
    cyrius: {
        summary: "CYP2D6 star-allele and copy-number caller for short-read WGS.",
        source: "https://github.com/Illumina/Cyrius",
        ...ILLUMINA_POLYFORM,
    },
    "smn_caller.py": {
        summary: "SMN1/SMN2 copy-number and spinal muscular atrophy carrier caller.",
        source: "https://github.com/Illumina/SMNCopyNumberCaller",
        ...ILLUMINA_POLYFORM,
    },
    "run-t1k": {
        summary: "Fast HLA and KIR genotyping from short-read or long-read WGS/RNA-seq.",
        source: "https://github.com/mourisl/T1K",
        organization: "Li Song (Dartmouth College / Dana-Farber Cancer Institute)",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
    ExpansionHunter: {
        summary: "Short tandem repeat (STR) expansion caller for short-read WGS.",
        source: "https://github.com/Illumina/ExpansionHunter",
        ...ILLUMINA_POLYFORM,
    },
    LongTR: {
        summary: "Tandem repeat (TR) genotyper for PacBio HiFi and Oxford Nanopore long reads.",
        source: "https://github.com/gymrek-lab/LongTR",
        organization: "Gymrek Lab (UC San Diego)",
        license: "GPL-2.0 (open source; free to use; redistributed modifications must stay GPL-2.0).",
    },
    strkit: {
        summary: "Short tandem repeat genotyper with read-level support for long reads.",
        source: "https://github.com/davidlougheed/strkit",
        organization: "David Lougheed (McGill University / C3G)",
        license: "GPL-3.0 (open source; free to use; redistributed modifications must stay GPL-3.0).",
    },
    dorado: {
        summary: "Oxford Nanopore high-performance POD5 basecaller and modified-base caller.",
        source: "https://github.com/nanoporetech/dorado",
        organization: "Oxford Nanopore Technologies (ONT)",
        license: "Oxford Nanopore Technologies PLC Public License v1.0 (source-available; check ONT terms for commercial/redistribution limits).",
    },
    discvrseq: {
        summary: "DISCVRSeq GATK4 toolkit providing VariantQC HTML reports for single or multi-VCF QC.",
        source: "https://github.com/BimberLab/DISCVRSeq",
        organization: "Bimber Lab (Oregon Health & Science University)",
        license: "Apache-2.0 (permissive open source; free for personal, academic, and commercial use).",
    },
    granite: {
        summary: "Granite VCF quality-control (qcVCF) and manipulation suite for single-sample and family VCFs.",
        source: "https://github.com/dbmi-bgm/granite",
        organization: "Department of Biomedical Informatics (DBMI), Harvard Medical School",
        license: "MIT (permissive open source; free for personal, academic, and commercial use).",
    },
};

function toolTooltip(tool) {
    const meta = TOOL_PROVENANCE[tool.name] ?? {
        summary: `External bioinformatics tool (${tool.group ?? "optional"}).`,
        source: "https://github.com/WGSExtract/wgsextract-cli",
        organization: "External / Bioconda package",
        license: "See the upstream package documentation for license terms.",
    };
    const extras = [];
    if (tool.path) extras.push(["Installed path", tool.path]);
    if (tool.note) extras.push(["Note", tool.note]);
    return formatProvenanceTooltip({ ...meta, extras });
}

/**
 * One row per tool: installed tools first (mandatory, then pre-installed, then
 * the rest), then missing ones, then those this computer can't have; each
 * group alphabetical.
 */
export function items(document) {
    const tools = document?.details?.tools;
    if (!Array.isArray(tools)) {
        throw new Error("wgsextract deps list printed no tool list.");
    }
    return { items: [...tools].sort(compareTools).map(row) };
}

const STATUS_ORDER = { installed: 0, missing: 1, unavailable: 2 };

function installedRank(tool) {
    if (tool.status !== "installed") return 0;
    return tool.group === "mandatory" ? 0 : tool.preinstalled ? 1 : 2;
}

function compareTools(a, b) {
    return (STATUS_ORDER[a.status] ?? 1) - (STATUS_ORDER[b.status] ?? 1)
        || installedRank(a) - installedRank(b)
        || String(a.name).localeCompare(String(b.name), "en", { sensitivity: "base" });
}

/** Mandatory, optional, or pre-installed: optional but shipped with the CLI, so it can't be removed on its own. */
function groupTag(tool) {
    if (tool.group === "mandatory") return { id: "mandatory", title: "mandatory", style: "primary" };
    if (tool.preinstalled) return { id: "pre-installed", title: "pre-installed", style: "secondary" };
    return { id: tool.group, title: tool.group, style: "secondary" };
}

function row(tool) {
    const installed = tool.status === "installed";
    return {
        id: tool.name,
        title: tool.name,
        status: tool.status,
        tags: [groupTag(tool)],
        tooltip: toolTooltip(tool),
        values: {
            name: tool.name,
            details: installed ? tool.version ?? "" : tool.note ?? "",
            environment: source(tool),
            environmentTools: (tool.environment_tools ?? []).join(", "),
            action: action(tool),
            note: tool.note ?? "",
        },
    };
}

/** Which button the row offers: install, uninstall, manual (install it yourself), unavailable, or none. */
function action(tool) {
    if (tool.can_uninstall) {
        return "uninstall";
    }
    if (tool.can_install) {
        return "install";
    }
    if (tool.status === "unavailable") {
        return "unavailable";
    }
    if (tool.status === "missing" && tool.group === "optional" && tool.note) {
        return "manual";
    }
    return "";
}

/** The Pixi environment a tool comes from (its own, or the CLI's "default"), else where it was found. */
function source(tool) {
    if (tool.environment) {
        return tool.environment;
    }
    if (tool.status !== "installed") {
        return "";
    }
    return tool.runtime === "pixi" ? "default" : tool.runtime ?? "";
}
