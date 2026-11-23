"""The sample's sex for bcftools call's sex-aware ploidy.

`bcftools call --ploidy GRCh38` (or a ploidy file) applies its M lines only to
samples listed as male in --samples-file; every other sample is called as F,
so a man's chrY gets ploidy 0 (no calls at all) and his chrX is diploid.
"""

import contextlib
import re
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from wgsextract_cli.core.builds import ploidy_preset
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command


class _PloidySource(Protocol):
    @property
    def build(self) -> str | None: ...
    @property
    def confirmed_build(self) -> str | None: ...
    @property
    def ploidy_file(self) -> str | None: ...


def ploidy_args(
    ploidy: str | None,
    ploidy_file: str | None,
    lib: _PloidySource,
    *,
    options: str | None = "--ploidy or --ploidy-file",
    assembly_option: bool = False,
) -> list[str]:
    """bcftools call's ploidy options, the one policy every caller shares:
    the explicit choice, else the reference library's ploidy file, else
    bcftools' GRCh37/GRCh38 preset for a confirmed build. T2T (whose PARs
    aren't GRCh38's) and unconfirmed builds get no guess, naming ``options``
    (the command's own ploidy flags, or None when it has none) and the
    library file that would serve them."""
    if not ploidy and not ploidy_file:
        ploidy_file = lib.ploidy_file
    if ploidy_file:
        from wgsextract_cli.core.variant_files import verify_paths_exist

        verify_paths_exist({"--ploidy-file": ploidy_file})
        return ["--ploidy-file", ploidy_file]
    if ploidy:
        return ["--ploidy", ploidy]
    # bcftools' predefined GRCh37/GRCh38 ploidy covers both X and chrX naming.
    build = lib.confirmed_build
    if preset := ploidy_preset(build):
        return ["--ploidy", preset]
    library_file = f"ploidy_{build}.txt" if build else "ploidy.txt"
    fix = f"Pass {options}, or add" if options else "Add"
    reason = (
        "the build is not confirmed by the input header or reference; "
        "names and contig counts are not enough. "
        if not build
        else "bcftools' presets fit only GRCh37 and GRCh38, whose "
        "sex-chromosome PARs differ from other builds'. "
    )
    assembly = (
        " If you know the input is GRCh37 or GRCh38, pass --assembly."
        if not build and assembly_option
        else ""
    )
    raise WGSExtractError(
        "Ploidy could not be chosen for this reference (reference build: "
        f"{build or lib.build or 'unknown'}): {reason}"
        f"{fix} {library_file} to the reference library.{assembly}"
    )


def alignment_samples(alignment: str) -> list[str]:
    """The sample names bcftools mpileup gives an alignment: its read groups'
    SM, or the file name as given when it has none."""
    try:
        header = run_command(["samtools", "view", "-H", alignment], capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return [alignment]
    names = [
        field[3:]
        for line in header.stdout.splitlines()
        if line.startswith("@RG")
        for field in line.split("\t")
        if field.startswith("SM:")
    ]
    return list(dict.fromkeys(names)) or [alignment]


def _region_contigs(region: str | None) -> set[str] | None:
    """Contigs in a plain bcftools region list; unknown syntax stays sex-aware."""
    if not region:
        return None
    contigs = set()
    for part in region.split(","):
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?::[0-9]+(?:-[0-9]*)?)?", part.strip())
        if match is None:
            return None
        contigs.add(match[1])
    return contigs


def sex_dependent_ploidy(
    ploidy_args: list[str],
    region: str | None = None,
    *,
    contigs: set[str] | None = None,
) -> bool:
    """Whether the ploidy distinguishes the sexes: a predefined assembly or a
    file with M lines. bcftools rejects a samples file declaring a sex the
    ploidy doesn't (a numeric --ploidy 1 or 2)."""
    contigs = _region_contigs(region) if region else contigs
    if "--ploidy" in ploidy_args:
        preset = ploidy_args[ploidy_args.index("--ploidy") + 1]
        if preset.isdigit():
            return False
        if contigs is not None and preset in {"GRCh37", "GRCh38"}:
            autosomes = {str(i) for i in range(1, 23)} | {"m", "mt"}
            return any(c.lower().removeprefix("chr") not in autosomes for c in contigs)
        return True
    if "--ploidy-file" in ploidy_args:
        path = ploidy_args[ploidy_args.index("--ploidy-file") + 1]
        try:
            lines = Path(path).read_text().splitlines()
        except OSError:
            return False
        rows = [
            line.split()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not any(row[3:4] == ["M"] for row in rows):
            return False
        if contigs is None:
            contigs = {row[0] for row in rows if row}
        for contig in contigs:
            relevant = [row for row in rows if row[0] in {"*", contig}]
            # Be conservative about malformed/custom tables and overlapping
            # rules: only skip a scan when all applicable rules agree, and
            # every sex-specific interval has an identical opposite-sex rule.
            if any(len(row) != 5 for row in relevant):
                return True
            if len({row[4] for row in relevant}) > 1:
                return True
            rules = {tuple(row) for row in relevant}
            for chrom, start, end, sex, ploidy in rules:
                if sex == "*":
                    continue
                opposite = {"M": "F", "F": "M"}.get(sex)
                if (
                    opposite is None
                    or (chrom, start, end, opposite, ploidy) not in rules
                ):
                    return True
        return False
    return False


def _sex_aware(ploidy_args: list[str], region: str | None = None) -> bool:
    return sex_dependent_ploidy(ploidy_args, region)


@contextlib.contextmanager
def sex_samples_args(
    alignment: str,
    directory: str,
    ploidy_args: list[str],
    *,
    region: str | None = None,
    reference: str | None = None,
) -> Iterator[list[str]]:
    """`--samples-file` listing a male alignment's samples as M (none for a
    female). Unknown sex refuses sex-dependent calling. Regions whose
    ploidy is the same for both sexes need no whole-alignment sex scan."""
    from wgsextract_cli.commands.info.metrics import sample_sex_evidence
    from wgsextract_cli.core.ref_library.models import sequence_lengths
    from wgsextract_cli.core.results import current_result

    if not alignment.lower().endswith((".bam", ".cram")):
        yield []
        return
    contigs = None
    if not region:
        header = run_command(["samtools", "view", "-H", alignment], capture_output=True)
        contigs = set(sequence_lengths(header.stdout))
    if not sex_dependent_ploidy(ploidy_args, region, contigs=contigs):
        yield []
        return
    sex, observed, declared = sample_sex_evidence(
        alignment, directory, reference=reference
    )
    current_result().add_metrics(
        effective_sex=sex,
        declared_sex=declared,
        observed_sex=observed.sex,
        sex_method=observed.method,
        sex_confidence=observed.confidence,
    )
    if sex not in {"Male", "Female"}:
        raise UsageError(
            f"Unknown sex for {alignment}: refusing sex-dependent ploidy. "
            "Declare sex with `sample set --input FILE --sex Male|Female`, "
            "or explicitly choose numeric --ploidy or a sex-independent ploidy file."
        )
    if sex == "Female":
        yield []
        return
    with tempfile.NamedTemporaryFile(
        "w", suffix=".samples.txt", dir=directory, delete=False
    ) as f:
        f.writelines(f"{name}\tM\n" for name in alignment_samples(alignment))
    try:
        yield ["--samples-file", f.name]
    finally:
        Path(f.name).unlink(missing_ok=True)
