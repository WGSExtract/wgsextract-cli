"""Help entries for ``ref``."""

from . import _LIBRARY, CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "ref bootstrap": _h(
        f"{_LIBRARY}: the bootstrap liftover chains, chip templates and other "
        "small files.",
        "wgsextract ref bootstrap",
    ),
    "ref library": _h(
        f"{_LIBRARY}: the chosen reference genome and its indexes.",
        "wgsextract ref library --list",
        "wgsextract ref library --install hs38",
    ),
    "ref status": _h(
        "Prints the reference library's status; writes no files.",
        "wgsextract ref status",
    ),
    "ref recheck": _h(
        "Prints each recorded resource release, the newest and its status; "
        "writes no files except the --save-answers file. With --json, the "
        "result's details are the wgsextract.recheck/1 document.",
        "wgsextract ref recheck HG002/HG002_personal_report.manifest.json "
        "--answers answers.json",
        "wgsextract ref recheck HG002/HG002_personal_report.manifest.json "
        "--online --save-answers answers.json",
    ),
    "ref test-genome": _h(
        "In the dataset library: wgsextract-benchmark-hg19-mini/ with its "
        "genome-config.toml, and the verified zip in .downloads (--delete "
        "removes both). Without --install or --delete, prints its status.",
        "wgsextract ref test-genome --install",
        "wgsextract --json ref test-genome",
    ),
    "ref needs": _h(
        "Prints the missing reference genome, annotation data or optional tool and the command "
        "that installs each; writes no files. With --json, the result's "
        "details.missing lists them.",
        "wgsextract ref needs -- info --input sample.cram",
        "wgsextract --json ref needs -- vcf snp --input sample.bam",
    ),
    "ref download": _h(
        "The FASTA at --out.",
        "wgsextract ref download --url https://example.org/genome.fa.gz "
        "--out genome.fa.gz",
    ),
    "ref index": _h(
        "Beside the FASTA: .fai, .dict and the aligner's index files.",
        "wgsextract ref index --ref hs38.fa",
        "wgsextract ref index --ref hs38.fa --aligner bwa-mem2",
    ),
    "ref dense-sa": _h(
        "Beside the FASTA: <FASTA>.wgse-sa16, wgse-bwa's optional dense "
        "suffix-array file (0.8 GB for hs38); --list prints the FASTA's files, "
        "--delete removes them.",
        "wgsextract ref dense-sa --ref hs38.fa",
        "wgsextract ref dense-sa --ref hs38.fa --list",
    ),
    "ref verify": _h(
        "Prints what is missing or damaged; creates a missing .fai (and .gzi for "
        "a compressed FASTA) beside the FASTA.",
        "wgsextract ref verify --ref hs38.fa",
    ),
    "ref count-ns": _h(
        "<reference stem>_ncnt.csv and <reference stem>_nbin.csv.",
        "wgsextract ref count-ns --ref hs38.fa",
    ),
    "ref microarray-targets": _h(
        f"{_LIBRARY}: microarray/All_SNPs_hg19_ref.tab.gz and "
        "All_SNPs_hg38_ref.tab.gz (+ .tbi); --delete removes them.",
        "wgsextract ref microarray-targets",
        "wgsextract ref microarray-targets --delete",
    ),
    "ref cytobands": _h(
        f"{_LIBRARY}/downloads/cytobands_hg19.tsv or cytobands_hg38.tsv; "
        "validated zero-based UCSC primary-chromosome bands for offline viewing.",
        "wgsextract ref cytobands --build hg38 --download",
        "wgsextract ref cytobands --build hg19 --download",
        "wgsextract ref cytobands --build hg38",
    ),
    "ref gene-map": _h(
        f"{_LIBRARY}: the hg19 and hg38 gene maps and their indexed RefSeq "
        "transcript and exon models, transcripts_<build>.bed.gz, for the genome "
        "viewer and info gene (--delete removes them).",
        "wgsextract ref gene-map",
    ),
    "ref clinvar": _h(
        f"{_LIBRARY}: ClinVar VCFs for hg19 and hg38.",
        "wgsextract ref clinvar",
    ),
    "ref dbsnp": _h(
        f"{_LIBRARY}/downloads/dbsnp_common_<build>.vcf.gz (+ .tbi) and "
        "dbsnp_common_<build>.rsid.sqlite (~37M common human rsIDs; --delete removes them).",
        "wgsextract ref dbsnp",
        "wgsextract ref dbsnp --build hg38",
        "wgsextract ref dbsnp --delete",
    ),
    "ref gnomad": _h(
        f"{_LIBRARY}: gnomAD allele-frequency files.",
        "wgsextract ref gnomad --download",
        "wgsextract ref gnomad --release 4.1 --download",
    ),
    "ref revel": _h(f"{_LIBRARY}: REVEL score files.", "wgsextract ref revel"),
    "ref pharmgkb": _h(
        f"{_LIBRARY}: PharmGKB annotation files (--delete removes them).",
        "wgsextract ref pharmgkb",
    ),
    "ref phylop": _h(
        f"{_LIBRARY}: the PhyloP bigWig.", "wgsextract ref phylop --build hg38"
    ),
    "ref cadd": _h(
        f"{_LIBRARY}: CADD score files and tabix indexes.",
        "wgsextract ref cadd --build hg38",
    ),
    "ref gerp": _h(
        f"{_LIBRARY}: the GERP++ bigWig.", "wgsextract ref gerp --build hg38"
    ),
    "ref spliceai": _h(
        f"{_LIBRARY}: SpliceAI score files (--delete removes them).",
        "wgsextract ref spliceai",
        "wgsextract ref spliceai --delete",
    ),
    "ref alphamissense": _h(
        f"{_LIBRARY}: AlphaMissense score files (--delete removes them).",
        "wgsextract ref alphamissense",
    ),
    "ref acmg-data": _h(
        f"{_LIBRARY}: downloads/acmg/ holds the Ensembl GFF3, the downloads in "
        "sources/ and fastVEP's databases with acmg_data.json in sa/ (or "
        "--sa-dir); --dry-run prints the plan and sizes only.",
        "wgsextract ref acmg-data --dry-run",
        "wgsextract ref acmg-data --chromosomes chr22",
    ),
    "ref aadr": _h(
        f"{_LIBRARY}: the AADR 2M SNP panel (--delete removes it).",
        "wgsextract ref aadr --install",
        "wgsextract ref aadr --delete",
    ),
    "ref somalier": _h(
        f"{_LIBRARY}: somalier's sites files and the 1000 Genomes sketches "
        "(--delete removes them).",
        "wgsextract ref somalier --install",
        "wgsextract ref somalier --delete",
    ),
    "ref ancestry": _h(
        f"{_LIBRARY}: the HGDP+1KG ancestry reference (--delete removes it).",
        "wgsextract ref ancestry --install",
    ),
    "ref gnomad-sv": _h(
        f"{_LIBRARY}: gnomAD SV tables in gnomad-sv/ (--delete removes them).",
        "wgsextract ref gnomad-sv --install --source lr",
    ),
    "ref imputation": _h(
        f"{_LIBRARY}: the imputation panel's chromosomes, prepared for GLIMPSE2 "
        "(--delete removes them).",
        "wgsextract ref imputation --install --chromosomes chr20",
        "wgsextract ref imputation --delete --chromosomes all",
    ),
    "ref ybrowse": _h(
        f"{_LIBRARY}: the yBrowse GRCh38 SNP sites (--delete removes them).",
        "wgsextract ref ybrowse --install",
        "wgsextract ref ybrowse --delete",
    ),
}
