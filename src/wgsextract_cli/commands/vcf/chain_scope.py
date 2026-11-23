"""Select the samples' records a targeted annotation chain needs."""

import re
import subprocess
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.paths import absolute_path, append_suffix
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.region_selection import (
    Intervals,
    bed_intervals_and_sha256,
    merge_intervals,
)
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import UsageError, popen, run_command
from wgsextract_cli.core.variant_files import (
    match_chromosome_name,
    vcf_index_chromosomes,
)

_CONTIG_END = 2**31 - 1
_REGION_SPAN = re.compile(r"(\d+)(?:-(\d*))?|-(\d+)")
_CONTIG_ATTRIBUTE = re.compile(r'(?:^|,)([^=,]+)=("(?:\\.|[^"\\])*"|[^,]*)')


@dataclass(frozen=True)
class ChainScope:
    intervals: Intervals
    whole_contigs: Intervals
    bed_sha256: str | None
    reference_index: Path | None


def vcf_indexes(path: str) -> list[Path]:
    """Existing index sidecars, including HTSlib's alternate compressed names."""
    source = Path(path)
    candidates = [Path(path + ext) for ext in (".tbi", ".csi")]
    if source.suffix.lower() in {".gz", ".bgz"}:
        candidates.extend(source.with_suffix(ext) for ext in (".tbi", ".csi"))
    return [index for index in candidates if index.is_file()]


def _indexed_contigs(source: str) -> list[str]:
    indexes = vcf_indexes(source)
    if indexes and all(
        p.stat().st_mtime_ns >= Path(source).stat().st_mtime_ns for p in indexes
    ):
        return vcf_index_chromosomes(source)
    return []


def _record_contigs(source: str, needed: set[str]) -> list[str]:
    # Plain VCFs may omit the contig dictionary and have no index.
    command = ["bcftools", "query", "-f", "%CHROM\\n", source]
    with popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as query:
        assert query.stdout is not None
        contigs: dict[str, None] = {}
        for line in query.stdout:
            name = line.rstrip(b"\r\n").decode()
            contigs[name] = None
            needed.discard(name)
            if not needed:
                # An exact match wins over an alias; no later row can change it.
                query.terminate()
                return list(contigs)
        if query.wait():
            raise UsageError(f"Cannot read contig names from VCF: {source}")
    return list(contigs)


def _vcf_header(source: str) -> str:
    return run_command(
        ["bcftools", "view", "--no-version", "-h", source], capture_output=True
    ).stdout


def _declared_contigs(header: str) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for line in header.splitlines():
        if line.startswith("##contig=<") and line.endswith(">"):
            fields = dict(_CONTIG_ATTRIBUTE.findall(line[len("##contig=<") : -1]))
            if name := fields.get("ID"):
                length = fields.get("length", "")
                result[name] = (int(length) or None) if length.isdigit() else None
    return result


def _open_end(length: int | None) -> int:
    # VCF permits a telomeric record at length + 1. This is not a scoped base.
    return length + 1 if length is not None else _CONTIG_END


def _match_contig(name: str, contigs: Collection[str]) -> str | None:
    return name if name in contigs else match_chromosome_name(name, contigs)


def _region_rows(
    region: str, lengths: dict[str, int | None]
) -> tuple[list[tuple[str, int, int]], bool]:
    rows = []
    unknown_end = False
    for part in region.split(","):
        item = part.strip()
        if not item or any(ch.isspace() for ch in item):
            raise UsageError(f"Invalid --region: {item!r}")
        if name := _match_contig(item, lengths):
            unknown_end |= lengths[name] is None
            rows.append((name, 0, _open_end(lengths[name])))
            continue
        chrom, _, span = item.rpartition(":")
        if not chrom:
            raise UsageError(f"--region {item!r}: unknown VCF contig {item!r}.")
        match = _REGION_SPAN.fullmatch(span)
        if not match:
            raise UsageError(f"Invalid --region: {item!r}")
        name = _match_contig(chrom, lengths)
        if name is None:
            raise UsageError(f"--region {item!r}: unknown VCF contig {chrom!r}.")
        length = lengths[name]
        first, last, leading_dash = match.groups()
        start = int(first or "1")
        if leading_dash:
            end = int(leading_dash)
        elif last is None:
            end = start
        elif last:
            end = int(last)
        else:
            unknown_end |= length is None
            end = _open_end(length)
        if length is not None and (start > length or (last != "" and end > length)):
            raise UsageError(
                f"--region {item!r} is out of bounds for {name} ({length:,} bp)."
            )
        if not 0 < start <= end:
            raise UsageError(f"Invalid --region: {item!r}")
        rows.append((name, start - 1, end))
    return rows, unknown_end


def _clip_bed(
    bed: Path, rows: list[tuple[str, int, int]], lengths: dict[str, int | None]
) -> list[tuple[str, int, int]]:
    names = {chrom: _match_contig(chrom, lengths) for chrom in {row[0] for row in rows}}
    if absent := ", ".join(sorted(chrom for chrom in names if names[chrom] is None)):
        warn(
            "bed_contigs_not_in_vcf",
            f"BED contigs absent from the VCF were skipped: {absent}.",
            contigs=absent,
            path=str(bed),
        )
    clipped = []
    for chrom, start, end in rows:
        if (name := names[chrom]) is not None:
            if (length := lengths[name]) is not None:
                end = min(end, length)
            if start < end:
                clipped.append((name, start, end))
    return clipped


def _missing_region_contigs(region: str | None, declared: Collection[str]) -> set[str]:
    needed: set[str] = set()
    for part in (region or "").split(","):
        item = part.strip()
        if not item or item in declared:
            continue
        chrom, _, span = item.rpartition(":")
        if chrom and _REGION_SPAN.fullmatch(span):
            if chrom in declared:
                continue
            # A colon can also be part of a contig name.
            needed.add(chrom)
        needed.add(item)
    return needed


def resolve_scope(
    source: str, region: str | None, bed: Path | None, fasta: str | None
) -> ChainScope | None:
    if region is None and bed is None:
        return None
    bed_rows: list[tuple[str, int, int]] = []
    bed_sha256 = None
    if bed is not None:
        bed = absolute_path(bed)
        try:
            bed_rows, bed_sha256 = bed_intervals_and_sha256(bed)
        except (OSError, ValueError, EOFError) as exc:
            raise UsageError(f"Invalid --bed {bed}: {exc}") from exc
    declared_lengths = _declared_contigs(_vcf_header(source))
    indexed = _indexed_contigs(source)
    if not indexed:
        needed = _missing_region_contigs(region, declared_lengths)
        needed.update(
            chrom for chrom, _, _ in bed_rows if chrom not in declared_lengths
        )
        if needed:
            indexed = _record_contigs(source, needed)
    contigs = dict.fromkeys([*indexed, *declared_lengths])
    reference_index = None
    reference_lengths = {}
    if (
        fasta
        and any(not declared_lengths.get(name) for name in contigs)
        and read_scope.may_follow(fai := append_suffix(fasta, ".fai"))
    ):
        reference_lengths = read_fai_lengths(fasta)
        if reference_lengths:
            reference_index = fai
    lengths = {
        name: declared_lengths.get(name)
        or reference_lengths.get(_match_contig(name, reference_lengths) or "")
        for name in contigs
    }
    result = current_result()
    scope_params: dict[str, object] = {"region": region, "bed": None}
    unknown_end = False
    if bed is not None:
        rows = _clip_bed(bed, bed_rows, lengths)
        result.add_input(bed, "regions_bed")
        scope_params.update(bed=str(bed), bed_sha256=bed_sha256)
    else:
        assert region is not None
        rows, unknown_end = _region_rows(region, lengths)

    selected: Intervals = {}
    for chrom, start, end in rows:
        selected.setdefault(chrom, []).append((start, end))
    merged = merge_intervals(selected)
    intervals = {name: merged[name] for name in contigs if name in merged}
    counts = {
        "region_intervals": sum(map(len, intervals.values())),
        "region_bases": (
            None
            if unknown_end
            else sum(
                min(end, lengths[name] or end) - start
                for name, spans in intervals.items()
                for start, end in spans
            )
        ),
    }
    result.add_metrics(**counts)
    result.provenance.params.update(scope_params, **counts)
    result.set_details({"scope": scope_params})
    whole_contigs = {name: [(0, _open_end(lengths[name]))] for name in intervals}
    return ChainScope(intervals, whole_contigs, bed_sha256, reference_index)


def _write_regions(path: Path, intervals: Intervals) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for chrom, ranges in intervals.items():
            stream.writelines(f"{chrom}\t{start + 1}\t{end}\n" for start, end in ranges)


def select_records(
    source: str,
    output: str,
    intervals: Intervals,
    threads: str,
    *,
    remap: bool = False,
) -> None:
    from_index = _indexed_contigs(source)
    if remap and not intervals.keys() <= set(from_index):
        # A step can change chromosome naming. Prefer an exact name even if
        # it's declared only in the header and carries no records.
        contigs = dict.fromkeys([*from_index, *_declared_contigs(_vcf_header(source))])
        if contigs:
            mapped: Intervals = {}
            for chrom, spans in intervals.items():
                if name := _match_contig(chrom, contigs):
                    mapped.setdefault(name, []).extend(spans)
            merged = merge_intervals(mapped)
            intervals = {name: merged[name] for name in contigs if name in merged}
    command = ["bcftools", "view", "--no-version", "--threads", str(int(threads) - 1)]
    if not intervals:
        run_command([*command, "-h", "-Oz", "-o", output, source])
        return

    with tempfile.TemporaryDirectory(dir=Path(output).parent) as work:
        path = Path(work, "targets.tsv")
        _write_regions(path, intervals)
        if from_index:
            # One seek per contig prevents a long record spanning separate BED
            # intervals from being returned twice. -T applies the exact union.
            bounds = Path(work, "bounds.tsv")
            _write_regions(
                bounds,
                {
                    chrom: [(rows[0][0], rows[-1][1])]
                    for chrom, rows in intervals.items()
                },
            )
            command += ["-R", str(bounds)]
        command += ["-T", str(path), "--targets-overlap", "1"]
        run_command([*command, "-Oz", "-o", output, source])
