"""Resource REF checks against the reference FASTA: stale records are
dropped with a count, and a resource for another build is refused."""

import gzip
import zlib
from pathlib import Path

from wgsextract_cli.core.results import warn
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import match_chromosome_name

# Fewer checked resource records can't tell a stale row or two from a wrong build.
_WRONG_BUILD_MIN_RECORDS = 20
# An allele table's build check reads this many rows from blocks spread
# through the file: its first rows can't tell, as hg19 and hg38 share some
# leading coordinates (chr1's first gene, OR4F5, has the same in both).
_BUILD_SAMPLE_ROWS = 1000
_BUILD_SAMPLE_BLOCKS = 64
# Every block htslib and bgzip write starts with this gzip header.
_BGZF_HEADER = b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00BC\x02\x00"
_MITO = {"M", "MT", "chrM", "chrMT"}


def record_count(vcf: str | Path) -> int:
    with gzip.open(vcf, "rb") as records:
        return sum(1 for line in records if not line.startswith(b"#"))


def _spread_lines(path: str) -> list[str]:
    """Complete lines from BGZF blocks spread evenly through ``path``, without
    reading the rest of it."""
    size = Path(path).stat().st_size
    blocks: dict[int, list[str]] = {}
    with open(path, "rb") as stream:
        for i in range(_BUILD_SAMPLE_BLOCKS):
            offset = size * i // _BUILD_SAMPLE_BLOCKS
            stream.seek(offset)
            data = stream.read(1 << 17)  # a block (<= 64 KiB) starts in the first half
            start = data.find(_BGZF_HEADER)
            while start != -1 and offset + start not in blocks:
                try:
                    text = zlib.decompressobj(31).decompress(data[start:])
                except zlib.error:  # header-like bytes inside compressed data
                    start = data.find(_BGZF_HEADER, start + 1)
                    continue
                lines = text.decode(errors="replace").split("\n")
                # A block starts and ends mid-line, except the file's first.
                blocks[offset + start] = lines[0 if offset + start == 0 else 1 : -1]
    per_block = _BUILD_SAMPLE_ROWS // max(len(blocks), 1)
    return [line for lines in blocks.values() for line in lines[:per_block]]


def _refuse_wrong_build(differ: int, checked: int, resource: str) -> None:
    if checked >= _WRONG_BUILD_MIN_RECORDS and differ * 2 > checked:
        raise WGSExtractError(
            f"Reference/build mismatch: {differ} of {checked} records in {resource} "
            "have a REF base that differs from the reference FASTA; the resource "
            "is likely for another genome build."
        )


def check_table_build(
    source: str, fields: list[str], lengths: dict[str, int], reference: str, work: str
) -> None:
    """Refuse an allele table for another build. Only its rows at the query's
    SNVs are normalized, too few for a small query to show the build, and a
    table has no contig lengths to compare; so check rows from across it."""
    chrom, pos, ref, alt = (fields.index(c) for c in ("CHROM", "POS", "REF", "ALT"))
    names: dict[str, str | None] = {}
    sites, beyond = [], 0
    for line in _spread_lines(source):
        row = line.rstrip("\r").split("\t")
        if line.startswith("#") or len(row) < len(fields):
            continue
        if row[chrom] not in names:
            names[row[chrom]] = match_chromosome_name(row[chrom], lengths)
        name = names[row[chrom]]
        if (
            not name
            or name in _MITO  # hg19's chrM is another sequence; see matching
            or not row[pos].isdigit()
            or len(row[ref]) != 1
            or len(row[alt]) != 1
            or not {row[ref], row[alt]} <= set("ACGT")
        ):
            continue
        if int(row[pos]) > lengths[name]:
            beyond += 1
        else:
            sites.append(f"{name}\t{row[pos]}\t.\t{row[ref]}\t{row[alt]}\t.\t.\t.\n")
    if len(sites) + beyond < _WRONG_BUILD_MIN_RECORDS:
        return
    sample, kept = Path(work, "build-sample.vcf"), Path(work, "build-sample.vcf.gz")
    with sample.open("w", newline="\n") as handle:
        handle.write("##fileformat=VCFv4.2\n")
        handle.writelines(f"##contig=<ID={c},length={n}>\n" for c, n in lengths.items())
        handle.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        handle.writelines(sites)
    run_command(
        ["bcftools", "norm", "-f", reference, "-c", "x", "-Oz", "-o", str(kept),
         str(sample)],
        capture_output=True,
    )  # fmt: skip
    checked = len(sites) + beyond
    _refuse_wrong_build(checked - record_count(kept), checked, Path(source).name)


def report_ref_mismatches(removed: int, total: int, resource: str) -> None:
    """Warn about the resource records ``bcftools norm -c x`` dropped for a REF
    that differs from the FASTA; refuse a resource that mostly differs, as it
    is for another build and its chance matches would annotate wrong variants."""
    _refuse_wrong_build(removed, total, resource)
    if removed:
        warn(
            "annotation_ref_mismatch_excluded",
            f"Excluded {removed} of {total} records in {resource} whose REF base "
            "differs from the reference FASTA (e.g. hg19 bases left in a GRCh38 "
            "table); they annotate no variant. No sample records were excluded.",
            count=removed,
            total=total,
            resource=resource,
        )
