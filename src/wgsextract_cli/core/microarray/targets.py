"""Build a genome-wide microarray target from the chip templates.

A microarray kit is genotyped at its chip's positions. The templates the
reference library ships (``ref bootstrap``), plus the few the CLI ships, list
every chip's markers in GRCh37 (FTDNA's Affymetrix list is NCBI36 and its Y
lists GRCh38, lifted with UCSC's hg18ToHg19 and hg38ToHg19 chains), so their union is the genome-wide target; for GRCh38
its positions are lifted with UCSC's hg19ToHg38 chain, except chrM: GRCh37's MT
and hg38's chrM are both the rCRS. The result is saved in the library as
``microarray/All_SNPs_<build>_ref.tab.gz`` and rebuilt when it was built by an
older version of this builder, or its template bodies, corrections or chains
change. The adjacent inputs sidecar records content identities for those resources.
"""

import gzip
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pyliftover import LiftOver

from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.file_lock import build_lock as _build_lock
from wgsextract_cli.core.microarray.coordinates import sole_placement
from wgsextract_cli.core.microarray.formats import resolve_format_key
from wgsextract_cli.core.microarray.resource_identity import resource_identity
from wgsextract_cli.core.microarray.templates import (
    _PACKAGED_TEMPLATES,
    _chrom_for_liftover,
    _resolve_templates_roots,
    _template_body_paths,
    chr_to_int,
    get_template_format,
    lift_to_grch37,
    template_rows,
)
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.utils import WGSExtractError, run_command


@dataclass(frozen=True)
class ChainSource:
    """A UCSC liftOver chain: its library file name, its URLs (UCSC first, then
    mirrors, tried in order) and the SHA-256 every copy must have."""

    name: str
    urls: tuple[str, ...]
    sha256: str


# UCSC chain files are free only for non-commercial use by independent researchers
# and nonprofit organizations (UCSC's EULA); a mirror must ship UCSC's liftOver
# README (its licence terms) beside the chain.
_UCSC_LIFTOVER = "https://hgdownload.soe.ucsc.edu/goldenPath/{}/liftOver/{}"
_MIRROR = (
    "https://github.com/WGSExtract/wgsextract-cli/releases/download/"
    "ucsc-liftover-chains-1/{}"
)
HG19_TO_HG38_CHAIN = ChainSource(
    "hg19ToHg38.over.chain.gz",
    (
        _UCSC_LIFTOVER.format("hg19", "hg19ToHg38.over.chain.gz"),
        _MIRROR.format("hg19ToHg38.over.chain.gz"),
    ),
    "5c0598e500ceb5a78c73086929e8ef993aec309bcafb595139b53d440b125a1d",
)
HG18_TO_HG19_CHAIN = ChainSource(
    "hg18ToHg19.over.chain.gz",
    (
        _UCSC_LIFTOVER.format("hg18", "hg18ToHg19.over.chain.gz"),
        _MIRROR.format("hg18ToHg19.over.chain.gz"),
    ),
    "f1e57673d8f306ea4ed1553ea5fc2001b3cc3d149eef501fc97305b3b3e2a9d6",
)
HG38_TO_HG19_CHAIN = ChainSource(
    "hg38ToHg19.over.chain.gz",
    (_UCSC_LIFTOVER.format("hg38", "hg38ToHg19.over.chain.gz"),),
    "a4af0aaac27b26c2b3b52c031ebbdbea1a2f7ffc645a6cab70683f74064920a2",
)
# The chain lifting a template's build to GRCh37.
_CHAINS_TO_GRCH37 = {"NCBI36": HG18_TO_HG19_CHAIN, "GRCh38": HG38_TO_HG19_CHAIN}
# Builds whose markers join the target only when their chain is installed: the
# FTDNA Y files ship with the CLI, so fetching their chain is left to a run that
# writes one of them (see chain_to_grch37), not to every target build.
_OPTIONAL_CHAINS = {"GRCh38"}
# Bump when the builder's output changes, so libraries rebuild their target.
# 5 is CombinedKit v5: the letter-named chip variants and third-tier vendors.
TARGET_VERSION = 5
_VERSION_LINE = f"##wgsextract_microarray_target_version={TARGET_VERSION}\n"
# In order of precedence for a position's ID.
_FORMATS = [
    "23andMe_V2",
    "23andMe_V3",
    "23andMe_V35",
    "23andMe_V4",
    "23andMe_V5",
    "23andMe_V5_2025",
    "23andMe_SNPs_API",
    "Ancestry_V1",
    "Ancestry_V2",
    "Ancestry_V2_2024",
    "FTDNA_V2",
    "FTDNA_V3",
    "LDNA_V1",
    "LDNA_V2",
    "MyHeritage_V1",
    "MyHeritage_V2",
    "Nucleus_V1",
    "FTDNA_V1_Affy",
    "Ancestry_V2_2021",
    "MyHeritage_GSA_2025",
    "MyHeritage_GSA_2025_609424",
]
# The pinned CHM13 v1 site map covers exactly these templates' markers.
CHM13_V1_FORMATS = tuple(_FORMATS)
_FORMATS += [
    "Ancestry_V2ab",
    "Ancestry_V2cd",
    "Ancestry_V2e",
    "FTDNA_V4",
    "FTDNA_V2Y",
    "FTDNA_V3Y",
    "FTDNA_V4Y",
    "MyHeritage_V3",
    "TellMeGen_V1",
    "Genera_V1",
    "MTHFR_V1",
    "meuDNA_V1",
    "meuDNA_V2",
    "SelfDecode_V1",
]


def genome_wide_target_path(reflib_dir: str, build: str) -> str:
    return str(Path(reflib_dir, "microarray", f"All_SNPs_{build}_ref.tab.gz"))


def is_genome_wide_target(path: str, reflib_dir: str, build: str) -> bool:
    """Whether path is the library's own target for build (which
    build_genome_wide_target keeps current), not one supplied separately."""
    target_build = canonical_build(build)
    if target_build not in ("hg19", "hg38"):
        return False
    own = genome_wide_target_path(reflib_dir, target_build)
    try:
        return Path(path).samefile(own)
    except OSError:
        return False


def _is_current(target: str, inputs: dict[str, object]) -> bool:
    if not (Path(target).is_file() and Path(target + ".tbi").is_file()):
        return False
    try:
        with gzip.open(target, "rt") as f:
            if f.readline() != _VERSION_LINE:
                return False
        try:
            recorded = Path(target + ".inputs.json").read_text(encoding="utf-8")
        except FileNotFoundError:
            # Current-version targets predate resource sidecars.
            return True
        return bool(json.loads(recorded) == inputs)
    except (OSError, EOFError, ValueError):
        return False


def _write_inputs(target: str, inputs: dict[str, object]) -> None:
    metadata = Path(target + ".inputs.json")
    with tempfile.TemporaryDirectory(
        dir=metadata.parent, prefix=".target-inputs-"
    ) as work:
        staged = Path(work, "inputs.json")
        staged.write_text(
            json.dumps(inputs, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        staged.replace(metadata)


def _template_paths(
    roots: list[str], formats: Sequence[str] = ()
) -> Iterator[tuple[str, str | None, list[str]]]:
    """(format, the build of its positions when not GRCh37, body paths) of each
    template found (of formats, by default every chip's), from the first root
    holding all of its parts."""
    for name in formats or _FORMATS:
        fmt = get_template_format(name)
        if not fmt:
            continue
        for root in roots:
            paths = _template_body_paths(root, name, fmt)
            if all(Path(p).exists() for p in paths):
                yield name, fmt.get("build"), paths
                break


def collect_template_markers(
    template_roots: Sequence[str | None],
    chains: Mapping[str, str] | None = None,
    formats: Sequence[str] = (),
) -> dict[tuple[str, int], str]:
    """The union of the chip templates' markers on GRCh37: {(chrNN, pos): ID}.

    A template on another build (NCBI36, GRCh38) is included only when chains
    gives that build's chain to GRCh37."""
    roots = _resolve_templates_roots(list(template_roots))
    markers: dict[tuple[str, int], str] = {}
    lifters: dict[str, LiftOver] = {}
    for name, build, paths in _template_paths(roots, formats):
        lifter = None
        if build:
            if not chains or build not in chains:
                continue
            lifter = lifters.setdefault(build, LiftOver(chains[build]))
        for path in paths:
            for rsid, chrom, pos in template_rows(name, path):
                if lifter:
                    lifted = lift_to_grch37(lifter, chrom, pos)
                    if not lifted:
                        continue
                    chrom, pos, _inverted = lifted
                if pos.isdigit() and chrom:
                    position = int(pos)
                    if position > 0:
                        markers.setdefault((_chrom_for_liftover(chrom), position), rsid)
    return markers


def lifted_builds(template_roots: Sequence[str | None]) -> set[str]:
    """The non-GRCh37 builds of the installed templates."""
    roots = _resolve_templates_roots(list(template_roots))
    return {build for _name, build, _paths in _template_paths(roots) if build}


def _lift_to_hg38(
    markers: dict[tuple[str, int], str], chain: str
) -> dict[tuple[str, int], str]:
    lifter = LiftOver(chain)
    lifted: dict[tuple[str, int], str] = {}
    lost: Counter[str] = Counter()
    for (chrom, pos), rsid in markers.items():
        if chrom == "chrM":
            # UCSC's hg19 chrM is the Yoruba sequence; the templates' MT, like
            # hg38's chrM, is the rCRS.
            lifted.setdefault((chrom, pos), rsid)
            continue
        hit = sole_placement(lifter.convert_coordinate(chrom, pos - 1))
        # One-to-one onto a primary chromosome only.
        if isinstance(hit, str) or "_" in hit.chrom:
            lost[hit if isinstance(hit, str) else "alt_contig"] += 1
            continue
        lifted.setdefault((hit.chrom, hit.position + 1), rsid)
    logging.info(
        f"Lifted {len(lifted):,} chip markers to hg38 "
        f"({sum(lost.values()):,} didn't lift: {dict(lost)})."
    )
    return lifted


def build_genome_wide_target(
    reflib_dir: str,
    build: str,
    template_roots: Sequence[str | None],
    chain_roots: Sequence[str] = (),
) -> str:
    """Write and index microarray/All_SNPs_<build>_ref.tab.gz unless it's
    current; return its path. Liftover chains are looked for in chain_roots,
    then in reflib_dir, where missing chains are downloaded only for rebuilds.

    Runs that start together on one library take turns (a lock file), and a run
    that finds the target already built by another uses it. Current-version
    targets predating resource sidecars remain usable in read-only libraries;
    their sidecars are added opportunistically when writable."""
    target_build = canonical_build(build)
    if target_build not in ("hg19", "hg38"):
        raise WGSExtractError(
            f"Microarray targets can only be built for hg19 or hg38, not {build}."
        )
    out = genome_wide_target_path(reflib_dir, target_build)
    roots = _resolve_templates_roots(list(template_roots))
    template_files = [
        path for _name, _build, paths in _template_paths(roots) for path in paths
    ]
    templates = [
        {
            "format": name,
            "parts": [resource_identity(path, include_path=False) for path in paths],
        }
        for name, _build, paths in _template_paths(roots)
    ]
    builds = sorted(lifted_builds(template_roots))
    chain38_needed = target_build == "hg38" and bool(template_files)
    lifts: dict[str, str | None] = {
        build: _installed_chain(reflib_dir, _CHAINS_TO_GRCH37[build], chain_roots)
        for build in builds
    }
    chain38 = (
        _installed_chain(reflib_dir, HG19_TO_HG38_CHAIN, chain_roots)
        if chain38_needed
        else None
    )
    recorded_inputs: dict[str, object] = {}
    try:
        recorded = json.loads(Path(out + ".inputs.json").read_text(encoding="utf-8"))
        if isinstance(recorded, dict):
            recorded_inputs = recorded
    except (OSError, ValueError):
        pass
    inputs: dict[str, object] = {
        "version": TARGET_VERSION,
        "templates": templates,
        "corrections": resource_identity(
            _PACKAGED_TEMPLATES / "corrections.tsv", include_path=False
        ),
        # Missing source chains don't invalidate a completed offline target.
        **{
            _chain_key(build): resource_identity(chain, include_path=False)
            if chain
            else recorded_inputs.get(_chain_key(build))
            for build, chain in lifts.items()
        },
        "chain38": resource_identity(chain38, include_path=False)
        if chain38
        else recorded_inputs.get("chain38")
        if chain38_needed
        else None,
    }
    # Before taking the lock, so a current target in a read-only library is used.
    if _is_current(out, inputs):
        if not Path(out + ".inputs.json").exists():
            try:
                _write_inputs(out, inputs)
            except OSError as error:
                logging.debug(
                    "Using current target without a resource sidecar: %s", error
                )
        return out
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with _build_lock(out + ".lock"):
        if _is_current(out, inputs):
            return out
        chains = {
            build: _chain(reflib_dir, _CHAINS_TO_GRCH37[build], chain_roots)
            if build not in _OPTIONAL_CHAINS
            else lifts[build]
            for build in builds
        }
        chain38 = (
            hg19_to_hg38_chain(reflib_dir, chain_roots) if chain38_needed else None
        )
        for build, chain in chains.items():
            inputs[_chain_key(build)] = resource_identity(chain, include_path=False)
        installed = {build: chain for build, chain in chains.items() if chain}
        inputs["chain38"] = resource_identity(chain38, include_path=False)
        if _is_current(out, inputs):
            return out
        markers = collect_template_markers(template_roots, installed)
        if not markers:
            if Path(out).is_file() and Path(out + ".tbi").is_file():
                logging.warning(
                    f"No microarray templates found to rebuild {out} from; using it "
                    "as it is."
                )
                return out
            raise WGSExtractError(
                "No microarray templates found to build the genome-wide target from; "
                "run 'wgsextract ref bootstrap' to install them."
            )
        logging.info(
            f"Building the {target_build} microarray target from {len(markers):,} "
            "chip template markers (once; it's saved in the reference library)."
        )
        if target_build == "hg38":
            assert chain38 is not None
            markers = _lift_to_hg38(markers, chain38)
        _write_target(markers, out)
        _write_inputs(out, inputs)
    logging.info(f"Saved the {target_build} microarray target: {out}")
    return out


def ncbi36_chain(reflib_dir: str, search: Sequence[str] = ()) -> str:
    """The hg18ToHg19 chain from search or the library, downloaded into the
    library when it's missing."""
    return _chain(reflib_dir, HG18_TO_HG19_CHAIN, search)


def chain_to_grch37(reflib_dir: str, build: str, search: Sequence[str] = ()) -> str:
    """The chain from a template's build (NCBI36 or GRCh38) to GRCh37, from
    search or the library, downloaded into the library when it's missing."""
    if build not in _CHAINS_TO_GRCH37:
        raise WGSExtractError(f"No chain from {build} to GRCh37 for the chip markers.")
    return _chain(reflib_dir, _CHAINS_TO_GRCH37[build], search)


def lifted_chain(
    reflib_dir: str | None, build: str, search: Sequence[str] = ()
) -> str | None:
    """The chain from build to GRCh37, or None (warned) to match its markers
    by ID instead."""
    if not reflib_dir:
        return None
    try:
        return chain_to_grch37(reflib_dir, build, search)
    except WGSExtractError as e:
        logging.warning(str(e))
        return None


def fetch_requested_chains(
    reflib_dir: str, format_keys: Iterable[str], search: Sequence[str] = ()
) -> None:
    """Install the chains of requested templates whose chain a target build
    doesn't fetch itself (FTDNA's GRCh38 Y files), so their markers join it."""
    builds = {
        fmt["build"]
        for key in format_keys
        if (name := resolve_format_key(key))
        and (fmt := get_template_format(name))
        and fmt.get("build") in _OPTIONAL_CHAINS
    }
    for build in sorted(builds):
        chain_to_grch37(reflib_dir, build, search)


def output_chain_key(build: str) -> str:
    """A vendor output's recorded-input key for its template's chain;
    "ncbi36_chain" keeps earlier NCBI36 outputs comparable."""
    return "ncbi36_chain" if build == "NCBI36" else f"{build}_chain"


def _chain_key(build: str) -> str:
    # "chain36" keeps NCBI36-only targets' recorded inputs comparable.
    return "chain36" if build == "NCBI36" else f"chain_{build}"


def hg19_to_hg38_chain(reflib_dir: str, search: Sequence[str] = ()) -> str:
    """The hg19ToHg38 chain from search or the library, downloaded into the
    library when it's missing."""
    return _chain(reflib_dir, HG19_TO_HG38_CHAIN, search)


def download_chain(source: ChainSource, dest: str) -> bool:
    """Download source to dest from the first of its URLs that serves a copy
    with its SHA-256. Each URL stages into its own file, so while dest is
    missing, trying one source never discards another's resumable partial
    download; once dest is in place, every source's staging files are removed."""
    staged = {
        url: f"{dest}.{hashlib.sha256(url.encode()).hexdigest()[:12]}"
        for url in source.urls
    }
    for url, stage in staged.items():
        if download_file(url, stage, expected_sha256=source.sha256):
            Path(stage).replace(dest)
            for leftover in staged.values():
                for suffix in ("", ".partial", ".partial.source", ".partial.segments"):
                    Path(leftover + suffix).unlink(missing_ok=True)
            return True
        logging.warning(f"Could not get {source.name} from {url}.")
    return False


def _installed_chain(
    reflib_dir: str, source: ChainSource, search: Sequence[str] = ()
) -> str | None:
    return next(
        (
            str(path)
            for directory in (*search, reflib_dir)
            if (path := Path(directory, source.name)).is_file()
        ),
        None,
    )


def _chain(reflib_dir: str, source: ChainSource, search: Sequence[str] = ()) -> str:
    found = _installed_chain(reflib_dir, source, search)
    if found:
        return found
    chain = str(Path(reflib_dir, source.name))
    if not Path(chain).is_file():
        Path(chain).parent.mkdir(parents=True, exist_ok=True)
        with _build_lock(chain + ".lock"):
            if not Path(chain).is_file() and not download_chain(source, chain):
                raise WGSExtractError(
                    f"Could not download {source.name} (tried {', '.join(source.urls)}), "
                    "needed to place the chip markers on another genome build."
                )
    return chain


def _write_target(markers: dict[tuple[str, int], str], out: str) -> None:
    """Write and index out through private temporary files, then rename both into
    place; only this run's temporary files are ever removed."""
    fd, plain = tempfile.mkstemp(
        prefix=".targets_", suffix=".tab", dir=Path(out).parent
    )
    staged = plain + ".gz"
    try:
        with os.fdopen(fd, "w", newline="\n") as f:
            f.write(_VERSION_LINE + "#CHROM\tPOS\tID\n")
            for (chrom, pos), rsid in sorted(
                markers.items(), key=lambda m: (chr_to_int(m[0][0]), m[0][1])
            ):
                f.write(f"{chrom}\t{pos}\t{rsid}\n")
        with open(staged, "wb") as gz:
            run_command(["bgzip", "-c", plain], stdout=gz)
        run_command(["tabix", "-f", "-s", "1", "-b", "2", "-e", "2", staged])
        Path(staged).replace(out)
        Path(staged + ".tbi").replace(out + ".tbi")
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(
            f"Could not write the microarray target {out}: {e}"
        ) from e
    finally:
        for path in (plain, staged, staged + ".tbi"):
            Path(path).unlink(missing_ok=True)
