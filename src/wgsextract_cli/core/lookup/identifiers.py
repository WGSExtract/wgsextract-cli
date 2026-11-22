"""What a lookup asks about: build-qualified loci, rsIDs and genes, resolved
offline against installed resources.

rsIDs go through the packaged dbSNP build 157 identity history (merged IDs to
their current ID) and are placed by the reference library's genome-wide
microarray targets (``ref microarray-targets``); genes come from the packaged
NCBI RefSeq annotation ``panel bed`` uses. Nothing is fetched: a missing
resource is an error naming the command that installs it, and an identifier
with several placements is answered with the candidates, never the first one.
"""

from __future__ import annotations

import gzip
import io
import re
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.utils import UsageError, WGSExtractError

# Display names of the canonical build keys.
BUILD_NAMES = {"hg19": "GRCh37", "hg38": "GRCh38", "t2t": "CHM13v2.0"}
BUILD_CHOICES = tuple(BUILD_NAMES.values())
# A region lookup reads every record in it; genes span at most ~2.5 Mb.
MAX_REGION = 10_000_000

DBSNP_RELEASE = "NCBI dbSNP build 157 (January 2025)"
DBSNP_TERMS = "https://www.ncbi.nlm.nih.gov/home/about/policies/"

_LOCUS = re.compile(
    r"(?P<build>[^:\s]+):(?P<chrom>[^:\s]+):(?P<start>\d[\d,]*)"
    r"(?:-(?P<end>\d[\d,]*))?"
    r"(?::(?P<ref>[ACGTNacgtn]+)>(?P<alt>[ACGTNacgtn]+))?"
)
_RSID = re.compile(r"rs[1-9]\d*")


def build_name(build: str | None) -> str | None:
    """GRCh37, GRCh38 or CHM13v2.0 for any recognised alias; None otherwise."""
    key = canonical_build(build)
    return BUILD_NAMES.get(key) if key else None


def contig_key(name: str) -> str:
    """A contig's name without "chr", with M as MT: chr1 and 1 match."""
    key = name.removeprefix("chr")
    return "MT" if key == "M" else key


def local_contig(name: str, available: Iterable[str]) -> str | None:
    """The input's own name for contig ``name`` (chr1 or 1, chrM or MT)."""
    wanted = contig_key(name)
    return next((c for c in available if contig_key(c) == wanted), None)


@dataclass(frozen=True)
class Locus:
    """A build-qualified position or range (1-based, inclusive), with the
    alleles asked about when given."""

    build: str
    chrom: str
    start: int
    end: int
    ref: str | None = None
    alt: str | None = None

    @property
    def is_range(self) -> bool:
        return self.ref is None and self.end > self.start

    def text(self) -> str:
        point = self.end == self.start or self.ref is not None
        span = f"{self.start}" if point else f"{self.start}-{self.end}"
        alleles = f":{self.ref}>{self.alt}" if self.ref and self.alt else ""
        return f"{self.build}:{self.chrom}:{span}{alleles}"

    def overlaps(self, other: Locus) -> bool:
        return (
            contig_key(self.chrom) == contig_key(other.chrom)
            and self.start <= other.end
            and other.start <= self.end
        )

    def document(self) -> dict[str, object]:
        out: dict[str, object] = {
            "build": self.build,
            "chrom": self.chrom,
            "start": self.start,
            "end": self.end,
            "text": self.text(),
        }
        if self.ref and self.alt:
            out["ref"], out["alt"] = self.ref, self.alt
        return out


def parse_locus(text: str) -> Locus:
    """``BUILD:CHROM:POS``, ``BUILD:CHROM:START-END`` or
    ``BUILD:CHROM:POS:REF>ALT`` (GRCh38:chr19:44908684:T>C)."""
    match = _LOCUS.fullmatch(text.strip())
    if not match:
        raise UsageError(
            f"--locus {text!r} isn't BUILD:CHROM:POS[-END][:REF>ALT], "
            "e.g. GRCh38:chr19:44908684 or GRCh38:chr19:44908684:T>C."
        )
    build = build_name(match["build"])
    if build is None:
        raise UsageError(
            f"--locus {text!r}: unknown build {match['build']!r}; use "
            f"{', '.join(BUILD_CHOICES)}."
        )
    start = int(match["start"].replace(",", ""))
    end = int((match["end"] or match["start"]).replace(",", ""))
    ref, alt = match["ref"], match["alt"]
    if start < 1 or end < start:
        raise UsageError(f"--locus {text!r}: positions are 1-based, start <= end.")
    if ref is not None:
        if match["end"]:
            raise UsageError(
                f"--locus {text!r}: give alleles with a position, not a range."
            )
        ref, alt = ref.upper(), (alt or "").upper()
        end = start + len(ref) - 1
    if end - start + 1 > MAX_REGION:
        raise UsageError(
            f"--locus {text!r} spans more than {MAX_REGION:,} bases; ask about "
            "a smaller region."
        )
    return Locus(build, match["chrom"], start, end, ref, alt)


# rsIDs -----------------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """Where a placement resource puts an rsID (the IDs it listed there)."""

    chrom: str
    pos: int
    ids: tuple[str, ...]


@dataclass
class RsidResolution:
    rsid: str
    current: str | None
    history: str  # merged, withdrawn, ambiguous, unresolved or unlisted
    placements: list[Placement] = field(default_factory=list)

    @property
    def aliases(self) -> tuple[str, ...]:
        """Every ID that names this variant: the one given, its current ID
        and the IDs dbSNP merged into that."""
        names = {self.rsid}
        if self.current:
            names.add(self.current)
            names.update(_merged_into().get(self.current, ()))
        return tuple(sorted(names, key=lambda n: int(n[2:])))


@cache
def _merged_into() -> Mapping[str, tuple[str, ...]]:
    from wgsextract_cli.core.microarray.identity import load_rsid_map

    reverse: dict[str, list[str]] = {}
    for old, identity in load_rsid_map().items():
        if identity.current:
            reverse.setdefault(identity.current, []).append(old)
    return {k: tuple(v) for k, v in reverse.items()}


def placements_path(reflib: str, build: str) -> Path:
    from wgsextract_cli.core.microarray.targets import genome_wide_target_path

    return Path(genome_wide_target_path(reflib, canonical_build(build) or build))


def _missing_placements(path: Path, build: str, why: str) -> WGSExtractError:
    return WGSExtractError(
        f"rsID lookup on {build} needs the rsID placements {path} ({why}). "
        "Build them from the reference library's chip templates with "
        "'wgsextract ref microarray-targets' (after 'wgsextract ref bootstrap'), "
        "or ask with a build-qualified --locus instead."
    )


def resolve_rsid(rsid: str, build: str, reflib: str) -> RsidResolution:
    """The dbSNP history of ``rsid`` and its placements on ``build``."""
    from wgsextract_cli.core.microarray.identity import resolve_rsid as history

    text = rsid.strip().lower()
    if not _RSID.fullmatch(text):
        raise UsageError(f"--rsid {rsid!r} isn't a dbSNP rsID (rs429358).")
    identity = history(text)
    if identity.status == "withdrawn":
        raise UsageError(
            f"{text} was withdrawn from dbSNP ({DBSNP_RELEASE}); it has no "
            "current placement. Ask with a build-qualified --locus instead."
        )
    if canonical_build(build) not in ("hg19", "hg38"):
        raise UsageError(
            f"rsID placements exist only for GRCh37 and GRCh38, not {build}; "
            "ask with a build-qualified --locus instead."
        )
    resolution = RsidResolution(text, identity.current, identity.status)
    path = placements_path(reflib, build)
    if not path.is_file():
        raise _missing_placements(path, build, "not installed")
    try:
        resolution.placements = _scan_placements(path, set(resolution.aliases))
    except (OSError, EOFError, zlib.error, ValueError) as e:
        raise _missing_placements(path, build, f"unreadable: {e}") from None
    if not resolution.placements:
        raise WGSExtractError(
            f"{text} isn't among the {build} rsID placements in {path}, which "
            "list the sites of consumer genotyping chips, not all of dbSNP. Ask "
            "with a build-qualified --locus instead."
        )
    return resolution


def _scan_placements(path: Path, wanted: set[str]) -> list[Placement]:
    """Rows of a CHROM, POS, ID table whose ID is one of ``wanted``."""
    with gzip.open(path, "rb") as stream:
        data = stream.read()
    found: dict[tuple[str, int], set[str]] = {}
    for name in wanted:
        needle = b"\t" + name.encode() + b"\n"
        at = data.find(needle)
        while at >= 0:
            row = data[data.rfind(b"\n", 0, at) + 1 : at].split(b"\t")
            if len(row) != 2 or not row[1].isdigit():
                raise ValueError(f"malformed row for {name}")
            found.setdefault((row[0].decode(), int(row[1])), set()).add(name)
            at = data.find(needle, at + 1)
    return [
        Placement(chrom, pos, tuple(sorted(ids)))
        for (chrom, pos), ids in sorted(found.items())
    ]


# Genes -----------------------------------------------------------------------


@dataclass(frozen=True)
class GeneResource:
    release: str
    licence: str
    source: str
    resource: str


@dataclass
class GeneResolution:
    query: str
    symbols: tuple[str, ...]  # several: an alias of several genes
    loci: list[tuple[str, Locus]]  # (symbol, span)


@cache
def gene_resource(build: str) -> GeneResource:
    """The packaged annotation's release, licence and source, from its header."""
    key = canonical_build(build) or build
    name = f"assets/panel/genes_{key}.tsv.gz"
    data = resources.files("wgsextract_cli").joinpath(name).read_bytes()
    with gzip.open(io.BytesIO(data), "rt", encoding="utf-8") as stream:
        first, second = stream.readline(), stream.readline()
    release = "NCBI RefSeq " + first.split("annotation ", 1)[-1].split(";")[0].strip()
    licence = first.rsplit(";", 1)[-1].strip(" .\n") if ";" in first else ""
    source = second.removeprefix("# Source:").strip().split(" (sha256")[0]
    return GeneResource(release, licence, source, name)


def resolve_gene(name: str, build: str) -> GeneResolution:
    """A symbol (any case) or alias to its loci on ``build``. An unknown
    name is an error; an alias of several genes resolves to all of them."""
    from wgsextract_cli.core import panel

    key = canonical_build(build)
    if key not in panel.BUILDS:
        raise UsageError(f"Gene lookup isn't available for {build}.")
    notes = panel.load_annotations(key)
    upper = name.strip().upper()
    if upper in notes.loci:
        symbols: tuple[str, ...] = (notes.loci[upper][0].symbol,)
    else:
        symbols = notes.aliases.get(upper, ())
        if not symbols:
            panel.resolve_genes([name], notes)  # raises, with suggestions
    loci = [
        (item.symbol, Locus(BUILD_NAMES[key], item.chrom, item.start + 1, item.end))
        for symbol in symbols
        for item in notes.loci[symbol.upper()]
    ]
    return GeneResolution(name, symbols, loci)


def genes_at(locus: Locus) -> list[str]:
    """Symbols of the packaged annotation's genes overlapping ``locus``."""
    from wgsextract_cli.core import panel

    key = canonical_build(locus.build)
    if key not in panel.BUILDS:
        return []
    notes = panel.load_annotations(key)
    return sorted(
        {
            item.symbol
            for loci in notes.loci.values()
            for item in loci
            if contig_key(item.chrom) == contig_key(locus.chrom)
            and item.start < locus.end
            and locus.start <= item.end
        }
    )
