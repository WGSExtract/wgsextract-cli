"""Build-specific baseline windows and GIAB v3.5 short-read masks."""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from gzip import open as gzip_open
from importlib.resources import as_file, files
from pathlib import Path

from wgsextract_cli.commands.info.metrics import contig_key
from wgsextract_cli.commands.info.targets import BedTarget, read_bed
from wgsextract_cli.core.execution_context import current
from wgsextract_cli.core.file_lock import exclusive_lock
from wgsextract_cli.core.read_scope import may_follow
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import (
    download_file,
    verify_download_sha256,
)
from wgsextract_cli.core.ref_library.layout import downloads_dir
from wgsextract_cli.core.utils import WGSExtractError

AUTOSOMES = tuple(map(str, range(1, 23)))
PRIMARY = frozenset((*AUTOSOMES, "X", "Y"))
GIAB_ROOT = (
    "https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/release/"
    "genome-stratifications/v3.5"
)
ASSEMBLIES = {"hg19": "GRCh37", "hg38": "GRCh38", "t2t": "CHM13"}
TRACKS = (
    ("SegmentalDuplications", "segdups"),
    ("Mappability", "lowmappabilityall"),
)
# SHA-256 of the upstream gzip files, not of the derived mask.
DIGESTS = {
    "hg19": (
        "59a73c1381e066c51b742cd8213f018888318371a5d9389651bd671912cd79be",
        "0f8e4d735bdc64300131481f92b575d4535215b47ff44d74280be0275150bec6",
    ),
    "hg38": (
        "09ac029a7d0d681a9f0ed6581a6bc10fc708a834bc3f91c46a9bef504f6055c5",
        "593b1e126f9cc3d2bdeedc9ab2e61ea97fe2b21102db46d4607aa86ca0c897b0",
    ),
    "t2t": (
        "2e88c01ea7a1c6f85cdd72a3a0400df81c00f538e7d14f139568af1b4ea4cc2e",
        "5a95c5a51d64db674fb272fe70c98c27ccacdfcf7f63ae5b47eff6b21f8bae50",
    ),
}

Interval = tuple[int, int]
BedRow = tuple[str, int, int]


@dataclass(frozen=True)
class IntervalMask:
    """Disjoint half-open intervals, with logarithmic overlap queries."""

    intervals: dict[str, list[Interval]]

    @classmethod
    def from_rows(cls, rows: Iterable[BedRow]) -> "IntervalMask":
        grouped: dict[str, list[Interval]] = defaultdict(list)
        for chrom, start, end in rows:
            grouped[chrom].append((start, end))
        merged: dict[str, list[Interval]] = {}
        for chrom, intervals in grouped.items():
            spans: list[Interval] = []
            for start, end in sorted(intervals):
                if spans and start <= spans[-1][1]:
                    left, right = spans[-1]
                    spans[-1] = (left, max(end, right))
                else:
                    spans.append((start, end))
            merged[chrom] = spans
        return cls(merged)

    def overlaps(self, chrom: str, start: int, end: int) -> bool:
        spans = self.intervals.get(chrom)
        if not spans:
            return False
        previous = bisect_left(spans, (end, 0)) - 1
        return previous >= 0 and spans[previous][1] > start


def mask_rows(path: Path) -> Iterator[BedRow]:
    with gzip_open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            chrom, left, right, *_ = line.split()
            chrom = contig_key(chrom)
            if chrom in PRIMARY:
                start, end = int(left), int(right)
                if not 0 <= start < end:
                    raise ValueError(f"invalid interval {chrom}:{start}-{end}")
                yield chrom, start, end


def giab_tracks(build: str) -> Iterator[tuple[str, str, str]]:
    assembly = ASSEMBLIES[build]
    for (folder, suffix), digest in zip(TRACKS, DIGESTS[build], strict=True):
        filename = f"{assembly}_{suffix}.bed.gz"
        yield filename, f"{GIAB_ROOT}/{assembly}@all/{folder}/{filename}", digest


def mask_cache() -> Path:
    return downloads_dir(resolve_reference_library()) / "target-cnv-giab-v3.5"


def mask_cache_paths() -> Iterator[Path]:
    cache = mask_cache()
    yield cache
    yield cache.parent
    for build in ASSEMBLIES:
        for filename, _, _ in giab_tracks(build):
            yield from _track_paths(cache / filename)


def _track_paths(path: Path) -> Iterator[Path]:
    yield path
    for suffix in (".partial", ".partial.source", ".partial.segments", ".lock.guard"):
        yield path.with_name(path.name + suffix)


def _detailed_mask(build: str) -> IntervalMask:
    cache = mask_cache()
    paths = []
    for filename, url, digest in giab_tracks(build):
        path = cache / filename
        if not all(may_follow(p) for p in _track_paths(path)):
            raise WGSExtractError("GIAB mask cache is outside the allowed folders.")
        cache.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(path.with_name(f"{path.name}.lock"), timeout=300):
            if not (path.is_file() and verify_download_sha256(str(path), digest)):
                if not download_file(
                    url,
                    str(path),
                    expected_sha256=digest,
                    use_mirrors=False,
                    use_cache=False,
                    cancel_event=current().cancel,
                ):
                    raise WGSExtractError(
                        f"Could not download GIAB v3.5 mask {filename}."
                    )
        paths.append(path)
    return IntervalMask.from_rows(row for path in paths for row in mask_rows(path))


def baseline_and_mask(
    build: str, lengths: Mapping[str, int], *, detailed: bool
) -> tuple[list[BedTarget], IntervalMask]:
    if build not in ASSEMBLIES:
        raise WGSExtractError(
            f"No target-CNV baseline for build {build or 'unknown'}; "
            "supported builds are GRCh37, GRCh38 and T2T-CHM13v2.0."
        )
    assets = files("wgsextract_cli").joinpath("assets/target_cnv")
    windows = assets.joinpath(f"{build}.baseline.bed")
    compact = assets.joinpath(f"{build}.mask.bed.gz")
    if not windows.is_file() or not compact.is_file():
        raise WGSExtractError(
            f"Missing bundled target-CNV baseline or mask for {ASSEMBLIES[build]}; "
            "reinstall WGS Extract."
        )
    with as_file(windows) as bed:
        baseline = read_bed(bed, lengths, option="Bundled target-CNV baseline")
    if detailed:
        return baseline, _detailed_mask(build)
    with as_file(compact) as mask:
        return baseline, IntervalMask.from_rows(mask_rows(mask))
