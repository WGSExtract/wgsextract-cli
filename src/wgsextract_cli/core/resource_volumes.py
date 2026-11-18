"""Disk layouts for published results and their caller-owned staging copies."""


def reference_evidence_demands(
    output_dir: str,
    staging_dir: str | None,
    bytes_per_copy: int | None,
    copies: int,
) -> list[tuple[str, str, int | None]]:
    """Keep the published gVCF beside its VCF, and raw/clipped/gathered
    copies in caller staging, not GATK's separate Java temporary volume."""
    return [
        (output_dir, "reference_evidence", bytes_per_copy),
        (
            staging_dir or output_dir,
            "reference_evidence",
            None if bytes_per_copy is None else bytes_per_copy * (copies - 1),
        ),
    ]
