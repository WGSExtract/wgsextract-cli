"""Shared cleanup of bcftools calls containing indels."""


def indel_cleanup_pipeline(
    ref: str, output: str, threads: str, *, mixed: bool = False
) -> list[list[str]]:
    """Remove stale indel bias tags and resolve IUPAC reference alleles."""
    # bcftools 1.24 copies these from the last SNP, even on another contig.
    # A mixed stream must retain both the SNP records and their valid tags.
    select = ["-i", 'TYPE="indel"', "--keep-sites"] if mixed else []
    return [
        [
            "bcftools", "annotate", "--no-version", *select,
            "-x", "INFO/BQBZ,INFO/MQSBZ", "-Ou",
        ],
        # VCF requires IUPAC reference codes to resolve to a concrete base (R -> A).
        [
            "bcftools", "norm", "--check-ref", "ws", "-f", ref,
            "--threads", threads, "-Oz", "-o", output,
        ],
    ]  # fmt: skip
