"""Chip-template (vendor) export and the hg38 to hg19 CombinedKit liftover.

Provenance: the vendor-format export, chromosome ordering and liftover are
modified ports of WGS Extract v4 ``program/aconv.py`` and
``program/hg38tohg19.py`` (release v44.2, 28 Feb 2023), Copyright (C)
2018-2020 City Farmer and Copyright (C) 2020-2022 Randy Harr, licensed under
the GNU GPL version 3 or later. WGS Extract CLI contributors modified them from
2026-03-02 (see the git history): zero-based liftover coordinates, strand
complementing, rejection of ambiguous placements, rCRS mtDNA kept unlifted,
template corrections and quality-gated no-calls. The modified work stays under
GPL-3.0-or-later; see NOTICE and docs/migrating-from-v4.md.
"""

import functools
import gzip
import itertools
import logging
import os
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pyliftover import LiftOver

from wgsextract_cli.core.microarray.coordinates import (
    LiftoverCounts,
    project_coordinates,
    sole_placement,
)
from wgsextract_cli.core.microarray.formats import TemplateFormat

# Re-exported: most callers find template formats here.
from wgsextract_cli.core.microarray.formats import (
    get_template_format as get_template_format,
)
from wgsextract_cli.core.microarray.identity import MicroarrayIdentity, resolve_rsid
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.utils import WGSExtractError

TemplateSearchPath = str | os.PathLike[str]
TemplateSearchInput = TemplateSearchPath | Sequence[TemplateSearchPath | None] | None
_TEMPLATE_SEARCH_PARENT_LIMIT = 3


_COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")


def sort_alleles(genotype: str) -> str:
    """A two-allele call in alphabetical order (AG, never GA), as 23andMe,
    MyHeritage and upstream WGS Extract write them."""
    return "".join(sorted(genotype)) if len(genotype) == 2 else genotype


def chr_to_int(chrom: str | int) -> int:
    """
    Converts chromosome name to an integer for sorting.
    M/MT -> 23, X -> 24, Y -> 25.
    Ported from WGS Extract v4 program/aconv.py chrconv().
    """
    c = str(chrom).upper().replace("CHR", "")
    if c == "M" or c == "MT":
        return 23
    if c == "X":
        return 24
    if c == "Y":
        return 25
    try:
        return int(c)
    except ValueError:
        return 99  # Unknown


# AncestryDNA numbers chrX 23, chrY 24, the X pseudoautosomal region 25 and
# chrMT 26; FTDNA names the pseudoautosomal region XY (at its chrX positions).
_VENDOR_CHROMS = {"23": "X", "24": "Y", "25": "X", "26": "MT", "XY": "X"}
# GRCh37's chrX pseudoautosomal regions, diploid in men too.
_X_PARS_GRCH37 = ((60001, 2699520), (154931044, 155260560))
_PACKAGED_TEMPLATES = (
    Path(__file__).resolve().parents[2] / "assets" / "microarray" / "raw_file_templates"
)


def vendor_chrom(chrom: str) -> str:
    """A template's chromosome as the kit names it (X, Y, MT, 1-22)."""
    return _VENDOR_CHROMS.get(chrom, chrom)


def _chrom_for_liftover(chrom: str) -> str:
    chrom = vendor_chrom(chrom)
    if chrom in {"M", "MT", "chrM", "chrMT"}:
        return "chrM"
    if chrom.startswith("chr"):
        return chrom
    return f"chr{chrom}"


def _chrom_from_liftover(chrom: str) -> str:
    if chrom in {"chrM", "chrMT", "M", "MT"}:
        return "MT"
    return chrom.removeprefix("chr")


def _iter_template_search_dirs(
    templates_dir: TemplateSearchInput,
) -> Iterator[str]:
    if not templates_dir:
        return

    search_dirs: Sequence[TemplateSearchPath | None]
    if isinstance(templates_dir, (str, os.PathLike)):
        search_dirs = (templates_dir,)
    else:
        search_dirs = templates_dir

    for search_dir in search_dirs:
        if search_dir:
            yield os.fspath(search_dir)


def _resolve_templates_roots(templates_dir: TemplateSearchInput) -> list[str]:
    """Resolve raw_file_templates roots from reference directories or nearby parents.

    Searches the provided directories plus a small number of parent directories
    for sibling ``microarray/raw_file_templates`` or ``raw_file_templates``
    directories. The bounded walk finds the reference-library layout without
    accidentally selecting unrelated templates from high-level filesystem roots.
    """
    roots: list[str] = []
    visited_dirs: set[str] = set()
    seen_roots: set[str] = set()

    for search_dir in _iter_template_search_dirs(templates_dir):
        current = str(absolute_path(search_dir))

        for _ in range(_TEMPLATE_SEARCH_PARENT_LIMIT + 1):
            if not current or current in visited_dirs:
                break
            visited_dirs.add(current)

            candidates = [
                str(Path(current, "microarray", "raw_file_templates")),
                str(Path(current, "raw_file_templates")),
            ]
            if Path(current.rstrip(os.sep)).name == "raw_file_templates":
                candidates.insert(0, current)

            for candidate in candidates:
                candidate = str(absolute_path(candidate))
                if Path(candidate).is_dir() and candidate not in seen_roots:
                    roots.append(candidate)
                    seen_roots.add(candidate)

            parent = str(Path(current).parent)
            if parent == current:
                break
            current = parent

    return roots


def _template_body_paths(
    templates_root: str, format_name: str, fmt_info: TemplateFormat
) -> list[str]:
    body_templates = []
    for i in range(1, fmt_info["parts"] + 1):
        part_suffix = f"_{i}" if fmt_info["parts"] > 1 else ""
        template_name = f"{format_name}{part_suffix}{fmt_info['suffix']}"
        path = Path(templates_root, "body", template_name)
        # Templates shipped with the CLI are gzipped, most as a delta against
        # another shipped template (see assets/microarray).
        candidates = (path.with_name(path.name + ext) for ext in DELTA_EXTENSIONS)
        body_templates.append(str(next((p for p in candidates if p.exists()), path)))
    return body_templates


# A delta body names a shipped base template, then rebuilds the template's rows
# from it in order: "=N" copies the base's next N rows, "-N" skips them, and
# "+ID<TAB>chromosome<TAB>position" inserts a row.
DELTA_SUFFIX = ".delta.gz"
DELTA_EXTENSIONS = (".gz", DELTA_SUFFIX)


def _open_template(path: str) -> TextIO:
    if path.endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path)


# A delta's base may itself be a delta, this many deep.
_MAX_DELTA_DEPTH = 4


def _body_rows(
    format_name: str, path: str, depth: int = 0
) -> Iterator[tuple[str, str, str]]:
    """A template body's rows as stored, before corrections."""
    if path.endswith(DELTA_SUFFIX):
        if depth >= _MAX_DELTA_DEPTH:
            raise WGSExtractError(f"Template delta {path} nests too deep.")
        yield from _delta_rows(path, depth)
        return
    api_order = format_name == "23andMe_SNPs_API"
    with _open_template(path) as f:
        for line in f:
            line = line.strip().replace('"', "")
            if not line or line[0] == "#":
                continue
            commas = line.count(",")
            parts = line.split("," if commas and commas > line.count("\t") else "\t", 3)
            if len(parts) < 3:
                continue
            if api_order:
                # This template is chrom (chr1), position, rsID.
                yield parts[2], _chrom_from_liftover(parts[0]), parts[1]
            else:
                yield parts[0], parts[1], parts[2]


def _delta_base_path(
    delta_path: str | Path, base_name: str, base_info: TemplateFormat
) -> Path:
    """Resolve a delta template's base beside the delta first, else in the CLI's
    packaged templates."""
    local_root = Path(delta_path).parent.parent
    (local,) = _template_body_paths(str(local_root), base_name, base_info)
    if Path(local).exists():
        return Path(local)
    (packaged,) = _template_body_paths(str(_PACKAGED_TEMPLATES), base_name, base_info)
    return Path(packaged)


def delta_bases(path: str | Path) -> list[Path]:
    """The base bodies a delta body is decoded from, nearest first, so a
    change to any of them makes what was built from it stale."""
    bases: list[Path] = []
    current = str(path)
    while current.endswith(DELTA_SUFFIX) and len(bases) < _MAX_DELTA_DEPTH:
        try:
            with gzip.open(current, "rt") as f:
                header = f.readline().rstrip("\n").split("\t")
        except OSError:
            break
        info = get_template_format(header[-1]) if header[0] == "#base" else None
        if len(header) != 2 or info is None or info["parts"] != 1:
            break
        base_path = _delta_base_path(current, header[1], info)
        current = str(base_path)
        bases.append(base_path)
    return bases


def _delta_rows(path: str, depth: int) -> Iterator[tuple[str, str, str]]:
    with gzip.open(path, "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
        base_info = get_template_format(header[-1]) if len(header) == 2 else None
        if header[0] != "#base" or base_info is None or base_info["parts"] != 1:
            raise WGSExtractError(f"Template delta {path} names no shipped base.")
        base_path = _delta_base_path(path, header[1], base_info)
        if not base_path.exists():
            raise WGSExtractError(f"Template delta {path}: base {header[1]} missing.")
        base = _body_rows(header[1], str(base_path), depth + 1)
        for line in f:
            op, arg = line[0], line[1:].rstrip("\n")
            if op == "+":
                snp_id, chrom, pos = arg.split("\t")
                yield snp_id, chrom, pos
                continue
            count = int(arg)
            taken = list(itertools.islice(base, count))
            if len(taken) != count:
                raise WGSExtractError(f"Template delta {path} overruns its base.")
            if op == "=":
                yield from taken
            elif op != "-":
                raise WGSExtractError(f"Template delta {path}: bad line {line!r}")


def _template_corrections() -> dict[tuple[str, str, str], str]:
    """{(template, ID, position): corrected position} from corrections.tsv."""
    corrections: dict[tuple[str, str, str], str] = {}
    with open(_PACKAGED_TEMPLATES / "corrections.tsv") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            template, snp_id, _chrom, pos, corrected = line.rstrip("\n").split("\t")
            corrections[(template, snp_id, pos)] = corrected
    return corrections


def template_rows(format_name: str, path: str) -> Iterator[tuple[str, str, str]]:
    """(ID, chromosome, position) of each marker in a template body, as the
    vendor names them, with corrections.tsv's fixes applied."""
    corrections = {
        (snp_id, pos): corrected
        for (template, snp_id, pos), corrected in _template_corrections().items()
        if template == format_name
    }
    rows = _body_rows(format_name, path)
    if not corrections:
        yield from rows
        return
    for snp_id, chrom, pos in rows:
        yield snp_id, chrom, corrections.get((snp_id, pos), pos)


def lift_to_grch37(
    lifter: LiftOver, chrom: str, pos: str
) -> tuple[str, str, bool] | None:
    """An NCBI36 or GRCh38 template position on GRCh37 (vendor chromosome
    naming), and whether it lies in a region inverted between the builds; None
    unless the chain places it exactly once on a primary chromosome."""
    if not pos.isdigit():
        return None
    hit = sole_placement(
        lifter.convert_coordinate(_chrom_for_liftover(chrom), int(pos) - 1)
    )
    if isinstance(hit, str) or "_" in hit.chrom:
        return None
    return _chrom_from_liftover(hit.chrom), str(hit.position + 1), hit.strand == "-"


def _resolve_templates_for_format(
    templates_dir: TemplateSearchInput, format_name: str, fmt_info: TemplateFormat
) -> tuple[str, list[str]]:
    roots = _resolve_templates_roots(templates_dir)
    if not roots:
        raise WGSExtractError(
            f"Microarray templates not found near {templates_dir!r}; "
            f"cannot generate {format_name} output."
        )

    first_missing: str | None = None
    for templates_root in roots:
        body_templates = _template_body_paths(templates_root, format_name, fmt_info)
        missing = [path for path in body_templates if not Path(path).exists()]
        if not missing:
            return templates_root, body_templates
        if first_missing is None:
            first_missing = missing[0]

    raise WGSExtractError(f"Template body not found: {first_missing}")


def template_resource_paths(
    templates_dir: TemplateSearchInput, format_name: str
) -> list[Path]:
    """The actual selected vendor header, bodies and packaged corrections."""
    fmt = get_template_format(format_name)
    if fmt is None:
        return []
    try:
        root, bodies = _resolve_templates_for_format(templates_dir, format_name, fmt)
    except WGSExtractError:
        # Freshness checks must not discard a CombinedKit when conversion fails.
        return []
    return [
        Path(root, "head", format_name + fmt["suffix"]),
        *(Path(body) for body in bodies),
        *(base for body in bodies for base in delta_bases(body)),
        _PACKAGED_TEMPLATES / "corrections.tsv",
    ]


def liftover_hg38_to_hg19(
    input_txt: str,
    output_txt: str,
    chain_file: str,
    templates_dir: TemplateSearchInput = None,
    *,
    reflib_dir: str | None = None,
) -> LiftoverCounts:
    """
    Performs liftover from hg38 to hg19 using pyliftover; returns the sites'
    outcomes. Unmapped, alternate-contig, multiply-placed and colliding sites are
    left out, so vendor files report them as no-calls.
    MT stays in rCRS coordinates, matching hg38 and GRCh37 chip templates.
    Ported from WGS Extract v4 program/hg38tohg19.py.
    """
    if not Path(chain_file).exists():
        raise FileNotFoundError(f"Liftover chain file not found: {chain_file}")

    headers: list[str] = []
    records: list[tuple[str, str]] = []
    coordinates: list[tuple[str, int]] = []

    with open(input_txt) as f_source:
        for line in f_source:
            if line.startswith("#"):
                headers.append(line)
                continue

            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue

            snp_id, chrom, pos, result = parts[0], parts[1], parts[2], parts[3]

            try:
                position = int(pos)
            except (ValueError, TypeError):
                continue
            records.append((snp_id, result))
            coordinates.append((_chrom_for_liftover(chrom), position))

    projections, counts = project_coordinates(
        coordinates, chain_file, reflib_dir, preserve_rcrs=True
    )
    if counts.lifted < len(coordinates):
        logging.warning(
            f"Liftover left out {len(coordinates) - counts.lifted:,} sites: "
            f"{counts.unmapped:,} not in hg19, {counts.alt_contig:,} on an alternate "
            f"contig, {counts.ambiguous:,} placed more than once, "
            f"{counts.collision:,} sharing an hg19 position with another site."
        )

    with open(output_txt, "w", newline="\n") as f:
        # 1. Use preserved headers from source if any
        if headers:
            f.writelines(headers)
        # 2. Fallback: If no headers and templates_dir provided, try finding 23andMe_V3 head
        elif templates_dir:
            for templates_root in _resolve_templates_roots(templates_dir):
                head_path = str(Path(templates_root, "head", "23andMe_V3.txt"))
                if Path(head_path).exists():
                    with open(head_path) as f_h:
                        f.writelines(f_h.readlines())
                    break

        for projection in projections:
            snp_id, result = records[projection.source_index]
            if projection.inverted:
                result = sort_alleles(result.translate(_COMPLEMENT))
            out_chrom = _chrom_from_liftover(projection.chrom)
            f.write(f"{snp_id}\t{out_chrom}\t{projection.position}\t{result}\n")
    return counts


@dataclass(frozen=True)
class VendorOutputProfile:
    layout: str = "tsv"
    haploid_style: str = "doubled"
    allele_order: str = "sorted"
    no_call: str = "--"
    indel_encoding: str = "unknown"
    normalize_mt: bool = False


@functools.cache
def vendor_output_profile(format_name: str) -> VendorOutputProfile:
    if "Ancestry" in format_name:
        return VendorOutputProfile(
            layout="split_alleles", no_call="00", indel_encoding="ID"
        )
    if "23andMe" in format_name:
        return VendorOutputProfile(
            haploid_style="single" if format_name.startswith("23andMe") else "doubled",
            indel_encoding="ID",
            normalize_mt=True,
        )
    if format_name.startswith("MyHeritage_"):
        return VendorOutputProfile(
            layout="quoted_csv",
            allele_order="legacy_myheritage_v1"
            if format_name == "MyHeritage_V1"
            else "sorted",
            indel_encoding="ID",
        )
    if format_name == "FTDNA_V2":
        return VendorOutputProfile(layout="quoted_csv")
    if format_name.startswith(("FTDNA_", "meuDNA_")):
        return VendorOutputProfile(layout="csv")
    if format_name == "Genera_V1":
        # Genera writes MT and Y calls as one letter, but X as two.
        return VendorOutputProfile(layout="csv", haploid_style="single_ymt")
    if format_name == "SelfDecode_V1":
        return VendorOutputProfile(haploid_style="single")
    return VendorOutputProfile()


def write_formatted_line(
    f: TextIO,
    format_name: str,
    snp_id: str,
    chrom: str,
    pos: str,
    result: str,
    eol: str = "\n",
) -> None:
    """Writes a line in the specific vendor format. Ported from WGS Extract v4
    program/aconv.py."""

    profile = vendor_output_profile(format_name)
    if profile.layout == "split_alleles":
        if result == "--":
            result = profile.no_call
        # Ancestry expects tab separated alleles
        if len(result) == 2:
            val = f"{result[0]}\t{result[1]}"
        else:
            val = "0\t0"  # Fallback
        f.write(f"{snp_id}\t{chrom}\t{pos}\t{val}{eol}")

    elif profile.normalize_mt:
        if chrom == "M":
            chrom = "MT"
        f.write(f"{snp_id}\t{chrom}\t{pos}\t{result}{eol}")

    elif profile.layout == "quoted_csv":
        if profile.allele_order == "legacy_myheritage_v1":
            if result == "CT":
                result = "TC"
            elif result == "GT":
                result = "TG"
        f.write(f'"{snp_id}","{chrom}","{pos}","{result}"{eol}')

    elif profile.layout == "csv":
        f.write(f"{snp_id},{chrom},{pos},{result}{eol}")

    else:
        # Generic fallback
        f.write(f"{snp_id}\t{chrom}\t{pos}\t{result}{eol}")


def _in_x_par(pos: str) -> bool:
    return pos.isdigit() and any(lo <= int(pos) <= hi for lo, hi in _X_PARS_GRCH37)


@dataclass
class GenotypeTally:
    """Called, heterozygous and no-call ("--") genotype counts."""

    sites: int = 0
    called: int = 0
    het: int = 0

    def add(self, genotype: str) -> None:
        self.sites += 1
        if genotype and genotype != "--":
            self.called += 1
            self.het += genotype[0] != genotype[-1]

    @property
    def no_call_rate(self) -> float:
        return 1 - self.called / self.sites if self.sites else 0.0

    @property
    def homozygosity(self) -> float:
        return 1 - self.het / self.called if self.called else 0.0


def tally_genotypes(genotypes: Iterable[str]) -> GenotypeTally:
    tally = GenotypeTally()
    for genotype in genotypes:
        tally.add(genotype)
    return tally


def is_autosome(chrom: str) -> bool:
    key = chrom.removeprefix("chr")
    return key.isdigit() and 1 <= int(key) <= 22


def _kit_is_male(calls: dict[tuple[str, str], str]) -> bool:
    """Whether the kit's chrX outside the PARs is (nearly) all homozygous, as a
    man's haploid X is; a woman's has thousands of heterozygous chip markers."""
    x = tally_genotypes(
        genotype
        for (chrom, pos), genotype in calls.items()
        if chrom == "X" and not _in_x_par(pos)
    )
    return x.called >= 100 and x.het < 0.05 * x.called


def _haploid_letter(genotype: str, chrom: str, pos: str, male: bool) -> str:
    """23andMe writes a haploid call as one letter: MT, Y, and a man's X
    outside the PARs (male is False for vendors writing X with two)."""
    haploid = chrom in ("MT", "M", "Y") or (
        chrom == "X" and male and not _in_x_par(pos)
    )
    if haploid and len(genotype) == 2 and genotype[0] == genotype[1] != "-":
        return genotype[0]
    return genotype


def convert_to_vendor_format(
    format_name: str,
    combined_kit_txt: str,
    output_path: str,
    templates_dir: TemplateSearchInput,
    lift_chain: str | None = None,
    *,
    identity: MicroarrayIdentity | None = None,
    indel_calls: Mapping[tuple[str, str, str], str] | None = None,
    quirks: str = "corrected",
) -> GenotypeTally:
    """
    Converts a CombinedKit.txt (GRCh37) to a vendor-specific format using
    templates. Ported from WGS Extract v4 program/aconv.py. Returns the tally of the
    autosomal genotypes written.

    A template on another build (FTDNA's NCBI36 Affymetrix export, its GRCh38
    Y files) finds its calls at its markers' GRCh37 positions, lifted with
    lift_chain (UCSC hg18ToHg19, hg38ToHg19); without the chain, by marker ID.

    Calls are written on the forward strand (quirks="corrected"); "vendor"
    writes the markers the vendor reported on the reverse strand that way.
    """
    from wgsextract_cli.core.microarray.quirks import vendor_strand_flips

    flips = vendor_strand_flips(format_name) if quirks == "vendor" else frozenset()
    flipped = 0
    fmt_info = get_template_format(format_name)
    if not fmt_info:
        raise WGSExtractError(f"Unknown microarray format: {format_name}")

    build = fmt_info.get("build")
    match_rsid = bool(build) and not lift_chain
    lifter = LiftOver(lift_chain) if build and lift_chain else None
    if match_rsid:
        logging.warning(
            f"No chain to place {format_name}'s {build} markers on GRCh37; "
            "matching its markers by ID instead."
        )
    called_variants: dict[tuple[str, str], str] = {}
    called_by_rsid: dict[str, str] = {}
    check_rsids = bool(identity and identity.resolve_rsids)
    identity_counts: Counter[str] = Counter()
    with open(combined_kit_txt) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 4:
                canonical_id = parts[0]
                if check_rsids:
                    source_identity = resolve_rsid(canonical_id)
                    if source_identity.current is None:
                        identity_counts[
                            "excluded_source_" + source_identity.status
                        ] += 1
                        continue
                    canonical_id = source_identity.current
                if match_rsid:
                    previous = called_by_rsid.get(canonical_id)
                    if check_rsids and previous is not None and previous != parts[3]:
                        called_by_rsid[canonical_id] = "--"
                        identity_counts["conflicting_source_rows"] += 1
                    else:
                        called_by_rsid[canonical_id] = parts[3]
                else:
                    source_key = (str(parts[1]), str(parts[2]))
                    previous = called_variants.get(source_key)
                    if check_rsids and previous is not None and previous != parts[3]:
                        called_variants[source_key] = "--"
                        identity_counts["conflicting_source_rows"] += 1
                    else:
                        called_variants[source_key] = parts[3]
    haploid_style = vendor_output_profile(format_name).haploid_style
    single_letter_haploid = haploid_style != "doubled"
    # A man's X counts as haploid only for the vendors writing it as one letter.
    male = haploid_style == "single" and _kit_is_male(called_variants)

    templates_root, body_templates = _resolve_templates_for_format(
        templates_dir, format_name, fmt_info
    )

    head_template = Path(templates_root, "head", f"{format_name}{fmt_info['suffix']}")
    head = head_template.read_bytes().decode() if head_template.exists() else ""
    # The body's line endings follow the head's (CRLF for 23andMe and AncestryDNA).
    eol = "\r\n" if head.endswith("\r\n") else "\n"
    # Write to a temp name so a failure never leaves a truncated vendor file.
    tmp_path = output_path + ".tmp"
    autosomal = GenotypeTally()
    try:
        with open(tmp_path, "w", newline="") as f_out:
            f_out.write(head)
            for body_template in body_templates:
                for t_id, t_chrom, t_pos in template_rows(format_name, body_template):
                    target_id = t_id
                    excluded = False
                    if check_rsids:
                        target_identity = resolve_rsid(t_id)
                        excluded = target_identity.current is None
                        target_id = target_identity.current or t_id
                        if target_identity.status != "unlisted":
                            identity_counts["template_" + target_identity.status] += 1
                    if excluded:
                        result = "--"
                    elif match_rsid:
                        result = called_by_rsid.get(target_id, "--")
                    else:
                        key: tuple[str, str] | None = (vendor_chrom(t_chrom), t_pos)
                        inverted = False
                        if lifter:
                            lifted = lift_to_grch37(lifter, t_chrom, t_pos)
                            key = lifted[:2] if lifted else None
                            inverted = bool(lifted and lifted[2])
                        result = called_variants.get(key, "--") if key else "--"
                        if inverted and result != "--":
                            # GRCh37's forward strand is the template build's reverse here.
                            result = sort_alleles(result.translate(_COMPLEMENT))
                        if result != "--" and key and (t_id, *key) in flips:
                            result = sort_alleles(result.translate(_COMPLEMENT))
                            flipped += 1
                    if not excluded and indel_calls is not None:
                        result = indel_calls.get((t_id, t_chrom, t_pos), result)
                    if is_autosome(vendor_chrom(t_chrom)):
                        autosomal.add(result)
                    if single_letter_haploid:
                        result = _haploid_letter(
                            result, vendor_chrom(t_chrom), t_pos, male
                        )
                    write_formatted_line(
                        f_out, format_name, t_id, t_chrom, t_pos, result, eol
                    )
        Path(tmp_path).replace(output_path)
        if quirks == "vendor":
            logging.info(
                f"{format_name}: {flipped} calls written on the reverse strand, "
                f"as the vendor reported {len(flips)} known markers."
            )
        if check_rsids:
            logging.warning(
                f"{format_name}: rsID identity checks {dict(identity_counts)}. "
                "Retired, ambiguous, unresolved and conflicting records remain no-calls; "
                "rsID resolution does not infer a probe strand."
            )
    finally:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
    return autosomal
