"""Help entries for ``vcf``."""

from . import _ANNOTATED, _SCORED, _VCF_IN, CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "vcf snp": _h(
        "snps.vcf.gz (+ .tbi); with --reference-evidence gvcf also "
        "bcftools.g.vcf.gz (+ .tbi).",
        "wgsextract vcf snp --input sample.cram",
        "wgsextract vcf snp --input sample.cram --region chrM",
    ),
    "vcf indel": _h(
        "indels.vcf.gz (+ .tbi); with --reference-evidence gvcf also "
        "bcftools.g.vcf.gz (+ .tbi).",
        "wgsextract vcf indel --input sample.cram",
    ),
    "vcf freebayes": _h(
        "freebayes.vcf.gz (+ .tbi).",
        "wgsextract vcf freebayes --input sample.cram --region chr20",
    ),
    "vcf gatk": _h(
        "gatk.vcf.gz (+ .tbi).",
        "wgsextract vcf gatk --input sample.cram",
    ),
    "vcf deepvariant": _h(
        "deepvariant.vcf.gz (+ .tbi), deepvariant.vcf.gz.steps.json; optional "
        "deepvariant.g.vcf.gz (+ .tbi). Resume work: .deepvariant.work.",
        "wgsextract vcf deepvariant --input sample.cram",
        "wgsextract vcf deepvariant --input hifi.bam --pacbio",
        "wgsextract vcf deepvariant --input sample.cram --force-step call_variants",
    ),
    "vcf clair3": _h(
        "clair3.vcf.gz (+ .tbi).",
        "wgsextract vcf clair3 --input ont.bam",
    ),
    "vcf impute": _h(
        "imputed.vcf.gz (+ .tbi): every imputed record marked INFO/IMP, "
        "low-confidence ones FILTERed ImpLowConf.",
        "wgsextract vcf impute --input lowpass.bam --chromosomes chr20",
        "wgsextract vcf impute --vcf-input chip.vcf.gz",
    ),
    "vcf phase": _h(
        "phased.vcf.gz (+ .tbi) and phased.blocks.tsv; with --haplotag, "
        "haplotagged.bam or .cram (+ index).",
        "wgsextract vcf phase --input ont.bam --vcf-input clair3.vcf.gz",
        "wgsextract vcf phase --input hifi.bam --vcf-input calls.vcf.gz --haplotag",
    ),
    "vcf sv": _h(
        "sv.vcf.gz (Delly), pbsv.vcf.gz or sniffles.vcf.gz (+ .tbi).",
        "wgsextract vcf sv --input sample.cram",
        "wgsextract vcf sv --input hifi.bam --pacbio",
    ),
    "vcf cnv": _h(
        "cnv.vcf.gz (+ .tbi).",
        "wgsextract vcf cnv --input sample.cram",
    ),
    "vcf call": _h(
        "Each type's own VCF (snps.vcf.gz, indels.vcf.gz, the SV caller's VCF, "
        "cnv.vcf.gz), with --reference-evidence gvcf one bcftools.g.vcf.gz for "
        "SNPs and indels instead; --merge replaces them with variants.vcf.gz "
        "(variants.g.vcf.gz with the gVCF) (+ .tbi).",
        "wgsextract vcf call --input sample.cram --types snp,indel --merge",
        "wgsextract vcf call --input sample.cram --types snp,indel,sv,cnv",
    ),
    # vcf: annotation, filtering and analysis
    "vcf annotate": _h(
        "annotated.vcf.gz (+ .tbi).",
        f"wgsextract vcf annotate {_VCF_IN} --ann-vcf dbsnp.vcf.gz --cols ID",
    ),
    "vcf filter": _h(
        "filtered.vcf.gz (+ .tbi).",
        f"wgsextract vcf filter {_VCF_IN} --genes BRCA1,BRCA2 --bed targets.bed",
        f"wgsextract vcf filter {_VCF_IN} --expr 'QUAL>30'",
    ),
    "vcf table": _h(
        "<input stem>_variants.tsv (or .parquet with --format parquet) and "
        "<input stem>_variants.sqlite: one row per variant allele, with its "
        "annotations.",
        f"wgsextract vcf table {_VCF_IN} --bed targets.bed",
        f"wgsextract vcf table {_VCF_IN} --preset acmg-sf --region chr17",
        "wgsextract vcf table --vcf-input clinvar_annotated.vcf.gz --format parquet",
    ),
    "vcf compare": _h(
        "compare.summary.json and compare.summary.tsv (or .parquet); "
        "optional per-allele table, indexed anchor VCF, and indexed two-callset VCF partitions.",
        "wgsextract vcf compare --callset a=caller-a.vcf.gz --callset b=caller-b.vcf.gz --ref GRCh38.fa",
        "wgsextract vcf compare --callset a=caller-a.vcf.gz --callset b=caller-b.vcf.gz --ref GRCh38.fa --atomize --emit-partition-vcfs --outdir comparison",
        "wgsextract vcf compare --mode unique --callset child=child.vcf.gz --callset parent=parent.g.vcf.gz --include child --exclude parent --reference-evidence gvcf --emit-vcf",
    ),
    "vcf callability": _h(
        "exon_callability.json (per gene: callable, partial, not readable or not "
        "run, with the sources and thresholds) and exon_callability.tsv (one row "
        "per coding exon).",
        "wgsextract vcf callability --vcf-input snps.vcf.gz",
        "wgsextract vcf callability --vcf-input bcftools.g.vcf.gz --flank 10",
        "wgsextract vcf callability --vcf-input snps.vcf.gz --gene-list my_genes.tsv",
    ),
    "vcf clinvar": _h(
        "clinvar_annotated.vcf.gz and clinvar_pathogenic.vcf.gz (+ .tbi).",
        f"wgsextract vcf clinvar {_VCF_IN}",
    ),
    "vcf gnomad": _h(
        _ANNOTATED.format(name="gnomad")
        + "; with --max-af AF, also gnomad_af_lt_AF.vcf.gz and its "
        ".confidence.tsv table.",
        f"wgsextract vcf gnomad {_VCF_IN} --max-af 0.01",
    ),
    "vcf sv-freq": _h(
        "sv_freq_annotated.vcf.gz (+ .tbi) and sv_freq.tsv.",
        "wgsextract vcf sv-freq --vcf-input sniffles.vcf.gz",
    ),
    "vcf revel": _h(
        _SCORED.format(name="revel"),
        f"wgsextract vcf revel {_VCF_IN}",
    ),
    "vcf pharmgkb": _h(
        _ANNOTATED.format(name="pharmgkb"),
        f"wgsextract vcf pharmgkb {_VCF_IN}",
    ),
    "vcf regulatory": _h(
        _ANNOTATED.format(name="regulatory"),
        f"wgsextract vcf regulatory {_VCF_IN}",
    ),
    "vcf gtex": _h(
        "gtex_eqtl.tsv (one row per matched ALT, gene, tissue and credible set).",
        f"wgsextract vcf gtex {_VCF_IN}",
    ),
    "vcf phylop": _h(
        _SCORED.format(name="phylop"),
        f"wgsextract vcf phylop {_VCF_IN}",
    ),
    "vcf cadd": _h(
        _SCORED.format(name="cadd"),
        f"wgsextract vcf cadd {_VCF_IN} --min-score 20",
    ),
    "vcf gerp": _h(
        _SCORED.format(name="gerp"),
        f"wgsextract vcf gerp {_VCF_IN}",
    ),
    "vcf spliceai": _h(
        _ANNOTATED.format(name="spliceai"),
        f"wgsextract vcf spliceai {_VCF_IN}",
    ),
    "vcf alphamissense": _h(
        _SCORED.format(name="alphamissense"),
        f"wgsextract vcf alphamissense {_VCF_IN}",
    ),
    "vcf acmg": _h(
        "acmg_annotated.vcf.gz (+ .tbi) with INFO ACMG_CLASS, ACMG_CRITERIA and "
        "ACMG_TRANSCRIPT, and acmg_summary.json; research only, not a diagnosis.",
        "wgsextract vcf acmg --vcf-input sample_vep.vcf.gz --research-acmg "
        "--sa-dir acmg_sa --gff3 Homo_sapiens.GRCh38.116.gff3.gz",
    ),
    "vcf info": _h(
        "Prints a VCF summary (reference model/build, samples, format, record counts); "
        "writes no files unless --card is set.",
        f"wgsextract vcf info {_VCF_IN}",
    ),
    "vcf variantqc": _h(
        "<stem>.variantqc.html interactive VariantQC report from DISCVRSeq.",
        f"wgsextract vcf variantqc {_VCF_IN}",
    ),
    "vcf granite-qc": _h(
        "<stem>.granite_qc.json quality metrics report from granite qcVCF.",
        f"wgsextract vcf granite-qc {_VCF_IN}",
    ),
    "vcf chain-annotate": _h(
        "chain_annotated.vcf.gz (+ index), chain_annotate.steps.json; "
        "with gtex, chain_gtex_eqtl.tsv; "
        "with --to-table, chain_annotated_variants.tsv (or .parquet) and .sqlite.",
        f"wgsextract vcf chain-annotate {_VCF_IN} --annotations clinvar,gnomad "
        "--region chr22:19000000-20000000 --threads 4 --to-table",
    ),
    "vcf pharmcat": _h(
        "<stem>.report.html and <stem>.phenotype.json from PharmCAT. With supplemental "
        "reference evidence, also <stem>.reference-evidence.json.",
        f"wgsextract vcf pharmcat {_VCF_IN}",
    ),
    "vcf trio": _h(
        "trio_<mode>.vcf.gz (+ .tbi); VCF denovo/comphet also write "
        "trio_<mode>_inconclusive.vcf.gz for missing/weak parental evidence; "
        "from alignments, also trio_joint.vcf.gz "
        "(and trio_denovo_isolated.vcf.gz for --mode denovo). The manifest is "
        "trio.manifest.json.",
        "wgsextract vcf trio --proband child.vcf.gz --mother mother.vcf.gz "
        "--father father.vcf.gz --mode denovo",
    ),
    "vcf prepare": _h(
        "<input stem>_prepared.vcf.gz (+ .tbi, or .csi for contigs over 512 Mb).",
        f"wgsextract vcf prepare {_VCF_IN}",
        f"wgsextract vcf prepare {_VCF_IN} --ref hs38.fa --normalize",
    ),
    "vcf to-fasta": _h(
        "<input stem>_consensus.fa.gz (+ .fai); with --chain, a .chain file.",
        f"wgsextract vcf to-fasta {_VCF_IN} --ref hs38.fa",
    ),
}
