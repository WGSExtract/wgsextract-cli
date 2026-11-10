"""`qc relate`: sex, relatedness and continental ancestry with somalier.

Each input is sketched at somalier's common SNP sites (`somalier extract`),
the sketches are compared pairwise (`somalier relate`) and, with the labelled
1000 Genomes sketches installed, given a superpopulation (`somalier
ancestry`). somalier is an optional tool from its own Pixi environment; it is
checked, never installed.
"""

from __future__ import annotations

import contextlib
import csv
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.api.qc import RelateParams
from wgsextract_cli.commands.qc import _relate_vcf
from wgsextract_cli.commands.vcf.basic import plain_reference
from wgsextract_cli.core import sample_workspace, somalier_resources
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.builds import build_from_chr1_length, canonical_build
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.paths import PathInput, absolute_path, expand_path
from wgsextract_cli.core.reference_assets import (
    input_stem,
    resolve_reference_library,
)
from wgsextract_cli.core.reference_resolver import (
    ReferenceLibrary,
    header_contig_lengths,
    library_roots,
)
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.sample_workspace import Confidence
from wgsextract_cli.core.table_writer import (
    Column,
    Value,
    check_format,
    provenance,
    table_path,
    write_table,
)
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    run_command,
)
from wgsextract_cli.core.variant_files import calculate_bam_md5, verify_paths_exist

SOMALIER = "somalier"
WINDOWS_UNSUPPORTED = (
    "somalier is not available on Windows (Bioconda builds it for Linux and "
    "macOS only). Run `qc relate` on Linux or macOS, for example under WSL2."
)
SKETCH_SUFFIX = ".somalier"
_ALIGNMENT = (".bam", ".cram")
_VCF = (".vcf", ".vcf.gz", ".vcf.bgz", ".bcf")
SEX_METHOD = "somalier: chrX heterozygosity and chrY depth at common SNPs"
ANCESTRY_METHOD = "somalier ancestry against the 1000 Genomes superpopulations"
# Relatedness (twice the kinship) halfway between 1, 1/2, 1/4 and 1/8.
SAME_PERSON = 0.75
FIRST_DEGREE = 0.375
SECOND_DEGREE = 0.1875
THIRD_DEGREE = 0.09
# Parents and children share an allele at (nearly) every site; siblings don't.
PARENT_CHILD_MAX_IBS0 = 0.01
# A pair further than this from its pedigree's relatedness is flagged.
PED_TOLERANCE = 0.1
# Below this many sites genotyped in both, a pair's relatedness is noise.
MIN_SHARED_SITES = 500
UNCERTAIN = "uncertain: a VCF's called sites only"
UNRELATED = ("unrelated", "too few shared sites", UNCERTAIN)
# chrX calls (of somalier's 365) needed to call the sex at all, and for high
# confidence; a 30x genome has ~350.
MIN_X_CALLS = 30
HIGH_X_CALLS = 100
# A sketch with almost no hom-ref calls holds only a VCF's variant sites.
CALLED_ONLY_MAX_HOM_REF = 0.05
_SAFE_NAME = re.compile(r"[^/\\\x00-\x1f]+")


def require_supported_platform() -> None:
    if sys.platform == "win32":
        raise WGSExtractError(WINDOWS_UNSUPPORTED)


@dataclass
class Input:
    path: str
    kind: str  # alignment, vcf or sketch
    header: str
    names: list[str]
    prefix: str = ""
    # An alignment without SM: somalier is told the name chosen for it.
    unnamed: bool = False
    sketches: list[Path] = field(default_factory=list)
    coverage: list[_relate_vcf.Coverage] = field(default_factory=list)


@dataclass
class Sample:
    name: str
    input: Input
    sketch: Path
    stats: dict[str, str]
    sex: str = "Unknown"
    sex_confidence: Confidence = "low"
    recorded_sex: str | None = None
    called_only: bool = False
    superpopulation: str | None = None
    probability: float | None = None


@dataclass
class Pair:
    a: str
    b: str
    relatedness: float
    ibs0: int
    ibs2: int
    shared: int
    expected: float | None
    relationship: str = ""
    flagged: bool = False


def _kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith(SKETCH_SUFFIX):
        return "sketch"
    if lower.endswith(_ALIGNMENT):
        return "alignment"
    if lower.endswith(_VCF):
        return "vcf"
    raise WGSExtractError(
        f"{path} isn't a BAM, CRAM, VCF or .somalier sketch; qc relate reads "
        "only those."
    )


# somalier's sketch format: a version byte, the sample name's length (one
# byte) and the name, then the site counts.
SKETCH_FORMAT = 2


def sketch_sample(path: PathInput) -> str:
    """The sample name stored in a .somalier sketch, whatever its file name."""
    try:
        with open(path, "rb") as f:
            version, length = f.read(2)
            name = f.read(length)
    except (OSError, ValueError) as e:
        raise WGSExtractError(f"{path} isn't a readable somalier sketch.") from e
    if version != SKETCH_FORMAT or len(name) != length:
        raise WGSExtractError(
            f"{path} isn't a somalier sketch of format {SKETCH_FORMAT} "
            f"(somalier 0.3); sketch the BAM, CRAM or VCF again."
        )
    return name.decode("utf-8", "replace")


def _sample_names(path: str, kind: str, header: str) -> list[str]:
    """The sample name(s) somalier stores: the SM of the reads, the header's
    samples for a VCF, the name inside a sketch. Empty for reads without SM."""
    if kind == "sketch":
        names = [sketch_sample(path)]
    elif kind == "vcf":
        line = next((x for x in header.splitlines() if x.startswith("#CHROM")), "")
        names = line.rstrip("\n").split("\t")[9:]
    else:
        names = sorted({rg["SM"] for rg in read_groups(header) if rg.get("SM")})
        if len(names) > 1:
            raise WGSExtractError(
                f"{path} has reads of several samples ({', '.join(names)}); "
                "qc relate compares one person per BAM/CRAM."
            )
    for name in names:
        # somalier names its sketch files after the sample.
        if not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
            raise WGSExtractError(
                f"{path} names a sample {name!r}; qc relate needs sample names "
                "without path separators or control characters."
            )
    return names


def _read_inputs(paths: Sequence[str]) -> list[Input]:
    items = []
    for path in paths:
        kind = _kind(path)
        header = "" if kind == "sketch" else get_bam_header(path)
        names = _sample_names(path, kind, header)
        unnamed = kind == "alignment" and not names
        if unnamed:
            names = [input_stem(path)]
        if not names:
            raise WGSExtractError(f"{path} has no samples.")
        items.append(Input(path, kind, header, names, unnamed=unnamed))
    # Names must be unique: a sketch keeps its name; another input whose name
    # collides is prefixed with its file name.
    counts = Counter(name for item in items for name in item.names)
    for item in items:
        if any(counts[n] > 1 for n in item.names) and item.kind != "sketch":
            item.prefix = f"{input_stem(item.path)}_"
    final = Counter(item.prefix + name for item in items for name in item.names)
    repeated = sorted(name for name, count in final.items() if count > 1)
    if repeated:
        raise WGSExtractError(
            f"Sample {repeated[0]} is in more than one input (two sketches, or "
            "two files with the same file and sample names); give it once."
        )
    return items


def _sites_for(item: Input, reflib: str) -> Path:
    lengths = header_contig_lengths(item.header)
    build = canonical_build(build_from_chr1_length(lengths))
    if build is None:
        raise WGSExtractError(
            f"Couldn't tell the reference build of {item.path} from its header "
            "(no chr1 of a known length); pass --sites with a matching sites VCF."
        )
    resource = somalier_resources.SITES.get((build, "chr1" in lengths))
    if resource is None:
        raise WGSExtractError(
            f"somalier has no {build} sites file for contigs named like those "
            f"of {item.path}; pass --sites with a matching sites VCF."
        )
    return somalier_resources.require(reflib, resource)


def _reference(
    item: Input,
    ref: Path | None,
    tmp: str,
    stack: contextlib.ExitStack,
    prepared: dict[str, str],
) -> str:
    """The FASTA the reads were aligned to, in a form somalier reads: plain
    or bgzipped with .fai and .gzi; another compressed one is decompressed
    for the run."""
    lib = ReferenceLibrary(
        expand_path(ref) if ref else None,
        calculate_bam_md5(item.path, None),
        input_path=item.path,
    )
    fasta = lib.fasta
    if not fasta or not Path(fasta).is_file():
        raise WGSExtractError(
            f"--ref is required for {item.path}: the reference FASTA it was aligned or called against."
        )
    if fasta in prepared:
        return prepared[fasta]
    if fasta.lower().endswith(".gz") and not Path(f"{fasta}.gzi").is_file():
        # Decompressed once per run, however many inputs share it.
        prepared[fasta] = stack.enter_context(plain_reference(fasta, tmp))
        return prepared[fasta]
    if not Path(f"{fasta}.fai").is_file():
        run_command(["samtools", "faidx", fasta], capture_output=True)
    return fasta


def _extract(item: Input, sites: str, ref: str | None, work: Path) -> list[Path]:
    folder = Path(tempfile.mkdtemp(dir=work, prefix="extract_"))
    source = item.path
    if item.kind == "vcf":
        source, item.coverage = _relate_vcf.prepare(
            item.path, item.header, sites, folder, item.names
        )
    command = [SOMALIER, "extract", "-d", str(folder), "-s", sites]
    if ref:
        command += ["-f", ref]
    if item.prefix:
        command += ["--sample-prefix", item.prefix]
    try:
        env = None
        if item.unnamed:
            env = {**os.environ, "SOMALIER_SAMPLE_NAME": item.names[0]}
        run_command([*command, source], capture_output=True, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"somalier extract failed on {item.path}: {e}") from e
    found = sorted(folder.glob(f"*{SKETCH_SUFFIX}"))
    if item.prefix:
        # somalier prefixes the name inside the sketch, not the file's.
        found = [p.rename(p.with_name(item.prefix + p.name)) for p in found]
    if not found:
        raise WGSExtractError(f"somalier extract wrote no sketch for {item.path}.")
    return found


def _sketch_inputs(
    items: list[Input], params: RelateParams, reflib: str, work: Path, threads: int
) -> None:
    prepared: dict[str, str] = {}
    with contextlib.ExitStack() as stack:
        jobs = []
        for item in items:
            if item.kind == "sketch":
                item.sketches = [Path(item.path)]
                continue
            sites = (
                expand_path(params.sites)
                if params.sites
                else str(_sites_for(item, reflib))
            )
            ref = _reference(item, params.ref, str(work), stack, prepared)
            jobs.append((item, sites, ref))
        if not jobs:
            return
        logging.info(f"Sketching {len(jobs)} input(s) at somalier's sites...")
        with ThreadPoolExecutor(max(1, min(threads, len(jobs)))) as pool:
            futures = [
                (item, pool.submit(_extract, item, sites, ref, work))
                for item, sites, ref in jobs
            ]
            for item, future in futures:
                item.sketches = future.result()
                checkpoint()


def _publish(items: list[Input], sketch_dir: Path) -> list[tuple[str, Input, Path]]:
    """Move new sketches into ``sketch_dir``, where a later run can reuse them.
    Every destination is checked first, so none overwrites another sketch of
    this run or an input not yet read."""
    planned = []
    for item in items:
        for sketch in item.sketches:
            name = sketch_sample(sketch)
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
            planned.append((name, item, sketch, sketch_dir / f"{safe}{SKETCH_SUFFIX}"))
    targets = Counter(absolute_path(target) for *_rest, target in planned)
    inputs = {absolute_path(item.path): item for item in items}
    for name, item, _sketch, target in planned:
        other = inputs.get(absolute_path(target))
        if targets[absolute_path(target)] > 1 or (other and other is not item):
            raise WGSExtractError(
                f"The sketch of {name} would be saved as {target}, over another "
                "sketch or input of this run; rename one of the samples or give "
                "another --outdir."
            )
    sketch_dir.mkdir(parents=True, exist_ok=True)
    for _name, item, sketch, target in planned:
        if item.kind != "sketch":
            os.replace(sketch, target)
        elif absolute_path(sketch) != absolute_path(target):
            shutil.copyfile(sketch, target)
    return [(name, item, target) for name, item, _sketch, target in planned]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        lines = (line.lstrip("#") if i == 0 else line for i, line in enumerate(f))
        return list(csv.DictReader(lines, delimiter="\t"))


def _float(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key) or "nan")
    except ValueError:
        return math.nan


def _x_called(stats: dict[str, str]) -> float:
    return sum(_float(stats, k) for k in ("X_hom_ref", "X_het", "X_hom_alt"))


def infer_sex(stats: dict[str, str]) -> tuple[str, Confidence]:
    """Male, Female or Unknown, and how sure, from somalier's per-sample
    chrX and chrY genotypes: a male has hardly any chrX heterozygotes and
    reads on chrY; a female the reverse. Both agreeing is high confidence."""
    x_called = _x_called(stats)
    if not x_called >= MIN_X_CALLS:
        return "Unknown", "low"
    x_het = _float(stats, "X_het") / x_called
    by_het = "Male" if x_het < 0.05 else "Female" if x_het > 0.15 else None
    depth, y_depth = _float(stats, "gt_depth_mean"), _float(stats, "Y_depth_mean")
    by_y = None
    if depth > 0 and not math.isnan(y_depth):
        ratio = y_depth / depth
        by_y = "Male" if ratio > 0.1 else "Female" if ratio < 0.03 else None
    if by_het and by_het == by_y:
        return by_het, "high" if x_called >= HIGH_X_CALLS else "medium"
    if by_het and by_y is None:
        return by_het, "medium"
    if by_y and by_het is None:
        return by_y, "medium"
    return "Unknown", "low"


def relationship(relatedness: float, ibs0: int, shared: int) -> str:
    """The closest degree of relationship to a relatedness (twice the
    kinship); IBS0 tells a parent and child from full siblings."""
    if shared < MIN_SHARED_SITES:
        return "too few shared sites"
    if relatedness >= SAME_PERSON:
        return "same person"
    if relatedness >= FIRST_DEGREE:
        if ibs0 / shared < PARENT_CHILD_MAX_IBS0:
            return "parent-child"
        return "full siblings"
    if relatedness >= SECOND_DEGREE:
        return "second-degree"
    if relatedness >= THIRD_DEGREE:
        return "third-degree"
    return "unrelated"


SAMPLES_TABLE = (
    Column("sample", "TEXT", "Sample name in the sketch."),
    Column("input", "TEXT", "The input file it came from."),
    Column("sex", "TEXT", "Male, Female or Unknown, from chrX and chrY."),
    Column("sex_confidence", "TEXT", "high, medium or low."),
    Column("recorded_sex", "TEXT", "The sex in effect before: override or reads."),
    Column("superpopulation", "TEXT", "Predicted 1000 Genomes superpopulation."),
    Column("superpopulation_probability", "REAL", "Its probability.", ".3f"),
    Column("depth_mean", "REAL", "Mean depth at the genotyped sites.", ".2f"),
    Column("x_depth_mean", "REAL", "Mean depth at chrX sites.", ".2f"),
    Column("y_depth_mean", "REAL", "Mean depth at chrY sites.", ".2f"),
    Column("x_het_fraction", "REAL", "Heterozygous share of chrX calls.", ".3f"),
    Column(
        "called_sites_only",
        "BOOLEAN",
        "A VCF's variant calls alone (not a whole genome's): relatedness uncertain.",
    ),
    Column("sites_genotyped", "INTEGER", "Autosomal sites genotyped."),
)
PAIRS_TABLE = (
    Column("sample_a", "TEXT", "First sample."),
    Column("sample_b", "TEXT", "Second sample."),
    Column(
        "relatedness", "REAL", "Twice the kinship: 1 same, 0.5 first-degree.", ".3f"
    ),
    Column("relationship", "TEXT", "What the relatedness and IBS0 suggest."),
    Column("ibs0", "INTEGER", "Sites where they share no allele."),
    Column("ibs2", "INTEGER", "Sites where they share both alleles."),
    Column("shared_sites", "INTEGER", "Sites genotyped in both."),
    Column("expected_relatedness", "REAL", "From --ped; empty if unknown.", ".3f"),
    Column("flagged", "BOOLEAN", "Disagrees with --ped, or the same person."),
)


def _number(value: float) -> float | None:
    return None if math.isnan(value) else value


def _sample_row(sample: Sample) -> list[Value]:
    stats, x_called = sample.stats, _x_called(sample.stats)
    genotyped = sum(_float(stats, k) for k in ("n_hom_ref", "n_het", "n_hom_alt"))
    return [
        sample.name,
        str(absolute_path(sample.input.path)),
        sample.sex,
        sample.sex_confidence,
        sample.recorded_sex,
        sample.superpopulation,
        sample.probability,
        _number(_float(stats, "gt_depth_mean")),
        _number(_float(stats, "X_depth_mean")),
        _number(_float(stats, "Y_depth_mean")),
        _float(stats, "X_het") / x_called if x_called > 0 else None,
        sample.called_only,
        int(genotyped) if not math.isnan(genotyped) else None,
    ]


def _pair_row(pair: Pair) -> list[Value]:
    return [
        pair.a,
        pair.b,
        pair.relatedness,
        pair.relationship,
        pair.ibs0,
        pair.ibs2,
        pair.shared,
        pair.expected,
        pair.flagged,
    ]


def _ped_rows(ped: str) -> list[list[str]]:
    """A PED/FAM file's rows, tab- or space-separated as PLINK writes them."""
    rows = []
    with open(ped) as f:
        for number, line in enumerate(f, start=1):
            fields = line.split()
            if not fields or fields[0].startswith("#"):
                continue
            if len(fields) < 5:
                raise WGSExtractError(
                    f"{ped} line {number}: a PED row needs family, sample, "
                    "father, mother and sex columns."
                )
            rows.append([*fields[:5], fields[5] if len(fields) > 5 else "-9"])
    return rows


def _relate(
    published: list[tuple[str, Input, Path]],
    sites: str,
    ped_rows: list[list[str]] | None,
    work: Path,
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    command = [SOMALIER, "relate", "-s", sites, "-o", str(work / "relate")]
    if ped_rows is not None:
        # somalier reads only tab-separated pedigrees.
        ped = work / "expected.ped"
        ped.write_text("".join("\t".join(row) + "\n" for row in ped_rows), newline="\n")
        command += ["--ped", str(ped)]
    try:
        run_command(
            [*command, *(str(sketch) for *_rest, sketch in published)],
            capture_output=True,
            # Past 447 samples somalier samples the unrelated pairs instead.
            env={**os.environ, "SOMALIER_REPORT_ALL_PAIRS": "1"},
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"somalier relate failed: {e}") from e
    samples = {row["sample_id"]: row for row in _read_tsv(work / "relate.samples.tsv")}
    return samples, _read_tsv(work / "relate.pairs.tsv")


def _ancestry(
    labels: Path, labelled: list[Path], sketches: list[Path], work: Path
) -> dict[str, dict[str, str]]:
    # 2,504 labelled sketches: a glob somalier expands keeps the command short.
    pattern = str(labelled[0].parent / f"*{SKETCH_SUFFIX}")
    command = [
        SOMALIER,
        "ancestry",
        "--labels",
        str(labels),
        "-o",
        str(work / "ancestry"),
        pattern,
        "++",
        *map(str, sketches),
    ]
    logging.info("Comparing with the 1000 Genomes superpopulations...")
    try:
        run_command(command, capture_output=True)
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"somalier ancestry failed: {e}") from e
    rows = _read_tsv(work / "ancestry.somalier-ancestry.tsv")
    return {row["sample_id"]: row for row in rows}


def _recorded_sex(item: Input) -> str | None:
    """The sex in effect for a BAM/CRAM: override or recorded, else (as other
    commands do) from a BAM's read counts. Counting a CRAM's reads takes
    minutes on a 30x genome, so for a CRAM only a known sex or `info`'s
    cached call is used."""
    if item.kind != "alignment":
        return None
    from wgsextract_cli.commands.info.metrics import (
        SEX_INFERENCE_VERSION,
        cached_info,
        sample_sex,
    )

    if item.path.lower().endswith(".cram"):
        fact = sample_workspace.known("sex", item.path)
        info = cached_info(item.path)
        if fact is not None:
            sex = str(fact.value)
        elif info and info.get("sex_inference_version") == SEX_INFERENCE_VERSION:
            sex = str(info["gender"])
        else:
            return None
    else:
        sex = sample_sex(item.path)
    return sex if sex in sample_workspace.SEXES else None


def _check_sample(sample: Sample, ped_sex: str | None) -> None:
    """Record the facts of a BAM/CRAM and warn where the sex disagrees."""
    if sample.sex in sample_workspace.SEXES and sample.input.kind == "alignment":
        sample_workspace.record(
            "somalier_sex",
            sample.input.path,
            sample.sex,
            method=SEX_METHOD,
            confidence=sample.sex_confidence,
        )
    if sample.superpopulation and sample.probability is not None:
        p = sample.probability
        confidence: Confidence = "high" if p >= 0.9 else "medium" if p >= 0.6 else "low"
        if sample.input.kind == "alignment":
            sample_workspace.record(
                "superpopulation",
                sample.input.path,
                sample.superpopulation,
                method=ANCESTRY_METHOD,
                confidence=confidence,
            )
    if sample.sex not in sample_workspace.SEXES:
        return
    if sample.recorded_sex and sample.recorded_sex != sample.sex:
        warn(
            "sex_mismatch",
            f"somalier finds {sample.name} {sample.sex}, but its recorded sex is "
            f"{sample.recorded_sex}: check that {Path(sample.input.path).name} is "
            "the right person's, or correct it with 'wgsextract sample set --sex'.",
            sample=sample.name,
            file=sample.input.path,
            inferred=sample.sex,
            recorded=sample.recorded_sex,
        )
    if ped_sex and ped_sex != sample.sex:
        warn(
            "ped_sex_mismatch",
            f"{sample.name} is {ped_sex} in the pedigree, but its reads look "
            f"{sample.sex}: the file may be someone else's.",
            sample=sample.name,
            expected=ped_sex,
            inferred=sample.sex,
        )


def called_sites_only(stats: dict[str, str]) -> bool:
    """A sketch of a VCF's variant calls alone: hardly any hom-ref sites, so
    its relatedness rests on the sites where it has a variant."""
    counts = [_float(stats, k) for k in ("n_hom_ref", "n_het", "n_hom_alt")]
    total = sum(counts)
    return total > 0 and counts[0] / total < CALLED_ONLY_MAX_HOM_REF


def _check_pair(row: dict[str, str], uncertain: set[str]) -> Pair:
    expected = _float(row, "expected_relatedness")
    pair = Pair(
        row["sample_a"],
        row["sample_b"],
        _float(row, "relatedness"),
        int(_float(row, "ibs0")),
        int(_float(row, "ibs2")),
        int(_float(row, "n")),
        expected if expected >= 0 else None,
    )
    pair.relationship = relationship(pair.relatedness, pair.ibs0, pair.shared)
    if pair.relationship == "too few shared sites":
        return pair
    if {pair.a, pair.b} & uncertain:
        pair.relationship = UNCERTAIN
        return pair
    if pair.expected is not None:
        if abs(pair.relatedness - pair.expected) > PED_TOLERANCE:
            pair.flagged = True
            warn(
                "relationship_mismatch",
                f"{pair.a} and {pair.b} should have relatedness {pair.expected:g} "
                f"by the pedigree, but have {pair.relatedness:.2f} "
                f"({pair.relationship}): a file may be swapped.",
                sample_a=pair.a,
                sample_b=pair.b,
                expected=pair.expected,
                relatedness=round(pair.relatedness, 3),
            )
    elif pair.relationship == "same person":
        pair.flagged = True
        warn(
            "same_person",
            f"{pair.a} and {pair.b} look like the same person (relatedness "
            f"{pair.relatedness:.2f}).",
            sample_a=pair.a,
            sample_b=pair.b,
            relatedness=round(pair.relatedness, 3),
        )
    return pair


def _warn_partial_vcfs(items: list[Input]) -> None:
    for item in items:
        for c in item.coverage:
            if c.filled:
                continue
            share = (
                f"{c.ratio:.0%} of" if c.ratio is not None else "an unknown share of"
            )
            warn(
                "vcf_called_sites_only",
                f"{Path(item.path).name} doesn't look like a whole-genome variant "
                f"VCF for {item.prefix}{c.sample} (it has {share} the variant sites "
                f"a genome would, on {c.autosomes:.0%} of the autosomes): its "
                "missing sites stay unknown, so its relatedness rests on its "
                "called sites alone and is reported as uncertain.",
                sample=item.prefix + c.sample,
                file=item.path,
                site_ratio=None if c.ratio is None else round(c.ratio, 3),
                autosomes=round(c.autosomes, 3),
            )


def run_relate(params: RelateParams) -> None:
    require_supported_platform()
    paths = [expand_path(p) for p in params.inputs]
    verify_paths_exist({f"input {i}": p for i, p in enumerate(paths, start=1)})
    sites = expand_path(params.sites) if params.sites else None
    ped = expand_path(params.ped) if params.ped else None
    verify_paths_exist({k: v for k, v in (("--sites", sites), ("--ped", ped)) if v})
    if params.ancestry and sites:
        raise UsageError(
            "--ancestry compares with 1000 Genomes sketches of somalier's own "
            "sites; it can't use --sites."
        )
    verify_dependencies([SOMALIER, "samtools", "bcftools"])
    check_format(params.table_format)
    log_dependency_info([SOMALIER])
    threads = int(get_resource_defaults(params.threads, None)[0])
    ped_rows = _ped_rows(ped) if ped else None
    outdir = (
        Path(expand_path(params.outdir)) if params.outdir else params.default_outdir()
    )
    outdir.mkdir(parents=True, exist_ok=True)
    # An external FASTA chooses only the sequence; the resources are in the
    # configured library.
    root = library_roots(expand_path(params.ref) if params.ref else None)
    reflib = resolve_reference_library(root.resource_root(somalier_resources.FOLDER))
    ancestry = params.ancestry
    if ancestry is None:
        ancestry = not sites and somalier_resources.installed(reflib)
        if not ancestry:
            logging.info(
                "Skipping ancestry: 'wgsextract ref somalier --install' installs "
                "the 1000 Genomes sketches it compares against."
            )
    labels, labelled = (
        somalier_resources.require_ancestry(reflib) if ancestry else (None, [])
    )

    result = current_result()
    stem = outdir / "relate"
    result.set_manifest_stem(stem)
    for path in paths:
        result.add_input(path)
    result.add_input(sites, "sites")
    result.add_input(ped, "pedigree")
    items = _read_inputs(paths)
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_somalier_") as tmp:
        work = Path(tmp)
        _sketch_inputs(items, params, reflib, work, threads)
        _warn_partial_vcfs(items)
        published = _publish(items, outdir / "somalier")
        # Every build's sites file lists the same SNPs and allele frequencies.
        relate_sites = sites or str(
            somalier_resources.require(reflib, somalier_resources.SITES["hg38", True])
        )
        checkpoint()
        stats, pair_rows = _relate(published, relate_sites, ped_rows, work)
        predictions = {}
        if labels is not None:
            checkpoint()
            sketches = [sketch for *_rest, sketch in published]
            predictions = _ancestry(labels, labelled, sketches, work)
        reports = [outdir / "relate.html"]
        os.replace(work / "relate.html", reports[0])
        if predictions:
            reports.append(outdir / "relate.ancestry.html")
            os.replace(work / "ancestry.somalier-ancestry.html", reports[1])

    ped_sexes = {
        row[1]: {"1": "Male", "2": "Female"}.get(row[4]) for row in ped_rows or []
    }
    samples = []
    for name, item, sketch in published:
        sample = Sample(name, item, sketch, stats.get(name, {}))
        sample.sex, sample.sex_confidence = infer_sex(sample.stats)
        sample.called_only = called_sites_only(sample.stats)
        sample.recorded_sex = _recorded_sex(item)
        prediction = predictions.get(name, {})
        sample.superpopulation = prediction.get("predicted_ancestry") or None
        if sample.superpopulation:
            p = _float(prediction, f"{sample.superpopulation}_prob")
            sample.probability = _number(p)
        _check_sample(sample, ped_sexes.get(name))
        samples.append(sample)
        result.add_output(
            sketch,
            "somalier_sketch",
            metrics={
                "sample": name,
                "sex": sample.sex,
                "superpopulation": sample.superpopulation,
                "superpopulation_probability": sample.probability,
            },
        )
    uncertain = {s.name for s in samples if s.called_only}
    pairs = [_check_pair(row, uncertain) for row in pair_rows]

    fmt = params.table_format
    metadata = provenance(paths, sorted_by="input order")
    samples_path = table_path(f"{stem}.samples", fmt)
    pairs_path = table_path(f"{stem}.pairs", fmt)
    write_table(
        samples_path, SAMPLES_TABLE, map(_sample_row, samples), fmt, metadata=metadata
    )
    write_table(pairs_path, PAIRS_TABLE, map(_pair_row, pairs), fmt, metadata=metadata)
    related = sum(1 for p in pairs if p.relationship not in UNRELATED)
    flagged = sum(1 for p in pairs if p.flagged)
    result.add_output(samples_path, "relate_samples", metrics={"rows": len(samples)})
    result.add_output(
        pairs_path,
        "relate_pairs",
        metrics={"rows": len(pairs), "related": related, "flagged": flagged},
    )
    for report in reports:
        result.add_output(report, "somalier_report", kind="html")
    result.add_metrics(
        samples=len(samples),
        pairs=len(pairs),
        related_pairs=related,
        flagged_pairs=flagged,
    )
    _print_summary(samples, pairs)


def _print_summary(samples: list[Sample], pairs: list[Pair]) -> None:
    human_output(f"{'Sample':<24}{'Sex':<10}Superpopulation")
    for s in samples:
        ancestry = (
            f"{s.superpopulation} ({s.probability or 0:.2f})"
            if s.superpopulation
            else "-"
        )
        human_output(f"{s.name:<24}{s.sex:<10}{ancestry}")
    shown = [p for p in pairs if p.relationship not in UNRELATED or p.flagged]
    if shown:
        human_output()
        human_output(f"{'Pair':<40}{'Relatedness':<13}Relationship")
        for p in shown:
            flag = "  FLAGGED" if p.flagged else ""
            human_output(
                f"{p.a + ' / ' + p.b:<40}{p.relatedness:<13.3f}{p.relationship}{flag}"
            )
    elif len(samples) > 1:
        human_output("\nNo related pairs.")
