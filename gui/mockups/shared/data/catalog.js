/* Catalogs: microarray destinations, reference genomes and resources, and
 * the server operations the command palette and advanced workspace list.
 * Format keys are the real `microarray --formats` values; op names are the
 * real server operations (GET /v1/ops). Sizes are approximate. */
(function () {
  "use strict";
  var data = (window.WGSM.data = window.WGSM.data || {});

  data.destinations = [
    { id: "gedmatch", name: "GEDmatch", mark: "G", tone: 1, uploads: true, recommended: true,
      makes: "Combined kit (all chips)", markers: "≈ 2 M markers", formats: [["all", "Combined kit"]] },
    { id: "myheritage", name: "MyHeritage", mark: "MH", tone: 2, uploads: true, recommended: true,
      makes: "MyHeritage raw data (CSV)", markers: "≈ 610 k markers",
      formats: [["myheritage_v2", "v2 (GSA)"], ["myheritage_gsa_2025", "GSA 2025"], ["myheritage_gsa_2025_609424", "GSA 2025 (609,424)"], ["myheritage_v1", "v1"]] },
    { id: "ftdna", name: "FamilyTreeDNA", mark: "FT", tone: 4, uploads: true,
      makes: "Family Finder transfer file", markers: "≈ 630 k markers",
      formats: [["ftdna_v3", "v3"], ["ftdna_v2", "v2"], ["ftdna_v1", "v1 (Affymetrix, NCBI36)"]] },
    { id: "livingdna", name: "Living DNA", mark: "LD", tone: 5, uploads: true,
      makes: "Living DNA raw data", markers: "≈ 700 k markers", formats: [["ldna_v2", "v2"], ["ldna_v1", "v1"]] },
    { id: "23andme", name: "23andMe format", mark: "23", tone: 3, uploads: false,
      makes: "For tools that read 23andMe files", markers: "≈ 640 k markers",
      formats: [["23andme_v5", "v5"], ["23andme_v5_2025", "v5 (2025 export)"], ["23andme_v3+v5", "v3 + v5"], ["23andme_v4", "v4"], ["23andme_v3", "v3"], ["23andme_v2", "v1/v2"], ["23andme_api", "API"]] },
    { id: "ancestry", name: "AncestryDNA format", mark: "A", tone: 6, uploads: false,
      makes: "For tools that read AncestryDNA files", markers: "≈ 670 k markers",
      formats: [["ancestry_v2", "v2"], ["ancestry_v2_2024", "v2 (2024–25 export)"], ["ancestry_v2_2021", "v2 (2021 layout)"], ["ancestry_v1", "v1"]] },
    { id: "nucleus", name: "Nucleus", mark: "N", tone: 7, uploads: true,
      makes: "Nucleus array export", markers: "≈ 650 k markers", formats: [["nucleus", "2025"]] },
    { id: "aadr", name: "AADR (Reich Lab)", mark: "R", tone: 6, uploads: false, advanced: true,
      makes: "PLINK + EIGENSTRAT for research tools", markers: "1240K / HO / 2M panels",
      formats: [["aadr_1240k", "1240K"], ["aadr_ho", "Human Origins"], ["aadr_1240k+ho", "1240K + HO"], ["aadr_2m", "2M (needs ref aadr)"]] },
  ];

  data.references = [
    { code: "hs38", label: "hs38 (GRCh38, no alts)", build: "GRCh38", installed: true, recommended: true,
      download: "0.9 GB", disk: "3.1 GB", note: "Nebula, Dante 2019+, most US labs",
      indexes: { fai: true, dict: true, bwa: true, "bwa-mem2": false }, verified: true },
    { code: "hs37d5", label: "hs37d5 (GRCh37 + decoys)", build: "GRCh37", installed: true, recommended: true,
      download: "0.9 GB", disk: "3.0 GB", note: "Dante before 2019, many older BAMs",
      indexes: { fai: true, dict: true, bwa: false, "bwa-mem2": false }, verified: true },
    { code: "T2Tv20", label: "T2T-CHM13 v2.0", build: "T2T", installed: false, recommended: true,
      download: "0.9 GB", disk: "3.2 GB", note: "Complete genome incl. HG002 Y; long reads" },
    { code: "hs38d1", label: "hs38d1 (GRCh38 + decoys)", build: "GRCh38", installed: false,
      download: "0.9 GB", disk: "3.2 GB", note: "Newer Nebula files" },
    { code: "hs38DH", label: "hs38DH (alts + HLA)", build: "GRCh38", installed: false,
      download: "1.0 GB", disk: "3.3 GB", note: "1000 Genomes high coverage; aDNA" },
    { code: "hg38", label: "hg38 (UCSC)", build: "GRCh38", installed: false,
      download: "0.9 GB", disk: "3.2 GB", note: "YSEQ" },
    { code: "hg19", label: "hg19 (UCSC)", build: "GRCh37", installed: false,
      download: "0.9 GB", disk: "3.1 GB", note: "Early Dante (2018)" },
  ];

  data.resources = [
    { id: "microarray-targets", name: "Microarray targets", op: "ref/microarray-targets", size: "310 MB", state: "installed", use: "DNA-test files" },
    { id: "ancestry", name: "Ancestry reference (HGDP+1KG)", op: "qc/ancestry", size: "16 MB", state: "installed", use: "Ancestry proportions" },
    { id: "ybrowse", name: "YBrowse SNP positions", op: "ref/ybrowse", size: "480 MB", state: "update", use: "Y-SNP names" },
    { id: "clinvar", name: "ClinVar", op: "ref/clinvar", size: "160 MB", state: "installed", use: "Variant annotation" },
    { id: "gnomad", name: "gnomAD allele frequencies", op: "ref/gnomad", size: "≈ 26 GB", state: "available", use: "Variant annotation" },
    { id: "revel", name: "REVEL", op: "ref/revel", size: "≈ 0.6 GB", state: "available", use: "Missense scores" },
    { id: "cadd", name: "CADD", op: "ref/cadd", size: "≈ 85 GB", state: "available", use: "Deleteriousness" },
    { id: "vep", name: "VEP cache (GRCh38)", op: "vep/download", size: "≈ 26 GB", state: "available", use: "Full VEP annotation" },
    { id: "aadr", name: "AADR v66 2M panel", op: "ref/aadr", size: "≈ 3 GB", state: "available", use: "Research panel exports" },
  ];

  /* Command palette / advanced workspace entries: [label, op, icon, group, page] */
  data.ops = [
    ["Describe a file (build, coverage, integrity)", "info", "search", "Files", "directions/c/file.html"],
    ["Identify sequencer and read layout", "bam/identify", "search", "Files", "directions/c/file.html"],
    ["Make DNA-test files (microarray)", "microarray", "chip", "Genealogy", "directions/c/microarray.html"],
    ["Find Y haplogroup", "lineage/y-haplogroup", "y-lineage", "Genealogy", "directions/c/ancestry.html"],
    ["Find mtDNA haplogroup", "lineage/mt-haplogroup", "mt-ring", "Genealogy", "directions/c/ancestry.html"],
    ["Y-STR markers", "lineage/y-str", "repeats", "Genealogy", "directions/c/ancestry.html"],
    ["Ancestry proportions (K=7 / K=12)", "qc/ancestry", "admixture", "Genealogy", "directions/c/ancestry.html"],
    ["Convert BAM → CRAM", "bam/to-cram", "convert", "Convert", "directions/c/convert.html"],
    ["Convert CRAM → BAM", "bam/to-bam", "convert", "Convert", "directions/c/convert.html"],
    ["Convert to FASTQ (unalign)", "bam/unalign", "file-fastq", "Convert", "directions/c/convert.html"],
    ["Convert to unaligned BAM", "bam/to-ubam", "file-bam", "Convert", "directions/c/convert.html"],
    ["Sort / index / merge BAM", "bam/sort", "list", "Convert", "directions/c/advanced.html"],
    ["Extract mtDNA (FASTA, VCF, BAM)", "extract/mito-fasta", "mt-ring", "Extract", "directions/c/advanced.html"],
    ["Extract Y-DNA BAM/VCF", "extract/ydna-bam", "y-lineage", "Extract", "directions/c/advanced.html"],
    ["Extract a region or gene", "extract/region", "extract", "Extract", "directions/c/advanced.html"],
    ["Extract unmapped reads", "extract/unmapped", "extract", "Extract", "directions/c/advanced.html"],
    ["Align FASTQ to a reference", "align", "align", "Align & call", "directions/c/advanced.html"],
    ["Realign to another build", "realign", "realign", "Align & call", "directions/c/advanced.html"],
    ["Call SNPs", "vcf/snp", "variant", "Align & call", "directions/c/advanced.html"],
    ["Call indels", "vcf/indel", "variant", "Align & call", "directions/c/advanced.html"],
    ["DeepVariant", "vcf/deepvariant", "variant", "Align & call", "directions/c/advanced.html"],
    ["GATK HaplotypeCaller", "vcf/gatk", "variant", "Align & call", "directions/c/advanced.html"],
    ["Structural variants", "vcf/sv", "variant", "Align & call", "directions/c/advanced.html"],
    ["Phase variants", "vcf/phase", "variant", "Align & call", "directions/c/advanced.html"],
    ["Coverage per chromosome", "info/calculate-coverage", "coverage", "Quality", "directions/c/file.html"],
    ["FastQC / fastp read QC", "qc/fastqc", "qc", "Quality", "directions/c/advanced.html"],
    ["Contamination estimate", "qc/contamination", "qc", "Quality", "directions/c/advanced.html"],
    ["Relatedness between samples", "qc/relate", "relate", "Quality", "directions/c/advanced.html"],
    ["VCF QC", "qc/vcf", "qc", "Quality", "directions/c/advanced.html"],
    ["Annotate with VEP", "vep/run", "annotate", "Annotate", "directions/c/advanced.html"],
    ["Annotate with ClinVar", "vcf/clinvar", "annotate", "Annotate", "directions/c/advanced.html"],
    ["Variant table (searchable)", "vcf/table", "list", "Annotate", "directions/c/advanced.html"],
    ["Personal report", "report", "scroll-text", "Annotate", "directions/c/file.html"],
    ["Pharmacogenomics (PharmCAT)", "vcf/pharmcat", "annotate", "Annotate", "directions/c/advanced.html"],
    ["HLA / CYP2D6 / SMN typing", "typing/hla", "relate", "Annotate", "directions/c/advanced.html"],
    ["Install a reference genome", "ref/library", "reference", "References", "directions/c/references.html"],
    ["Index / verify a reference", "ref/index", "reference", "References", "directions/c/references.html"],
    ["Download example genomes", "example-genome/download", "download", "References", "directions/c/setup.html"],
    ["Repair FTDNA BAM", "repair/ftdna-bam", "pencil", "Utilities", "directions/c/advanced.html"],
    ["Check installed tools", "deps/check", "terminal", "Utilities", "directions/c/settings.html"],
  ];
})();
