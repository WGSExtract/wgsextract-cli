"""Prepare single-sample working copies without a full-cohort intermediate."""

import gzip
import logging
import re
import sqlite3
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, closing
from itertools import chain
from os import fspath
from pathlib import Path

from .paths import PathInput
from .temp_directory import move_file, scratch_directory
from .utils import ToolExecutionError, WGSExtractError, popen, run_command, run_pipeline
from .variant_files import ensure_vcf_indexed, vcf_header_chromosomes


def has_sample_input_index(vcf: PathInput) -> bool:
    """Whether bcftools can use an existing index without writing to the input."""
    result = run_command(
        ["bcftools", "index", "--nrecords", vcf], capture_output=True, check=False
    )
    return result.returncode == 0 and "index file is older" not in (result.stderr or "")


def indexed_sample_vcf(vcf: str, sample: str, outdir: str, cleanup: ExitStack) -> str:
    """One selected sample, in an indexed scratch VCF that cleanup removes."""
    Path(outdir).mkdir(parents=True, exist_ok=True)
    workdir = Path(cleanup.enter_context(scratch_directory(outdir, "Sample_")))
    subset = workdir / "sample.vcf.gz"
    try:
        subset_vcf_sample(vcf, sample, subset)
        ensure_sample_vcf_indexed(subset)
    except (OSError, subprocess.SubprocessError, WGSExtractError) as exc:
        raise WGSExtractError(f"Could not select sample '{sample}': {exc}") from exc
    return str(subset)


def ensure_sample_vcf_indexed(vcf: Path) -> None:
    """Index selected records; sort only if their order prevents indexing."""
    try:
        ensure_vcf_indexed(vcf)
    except WGSExtractError as exc:
        cause = exc.__cause__
        if not isinstance(cause, subprocess.CalledProcessError) or not any(
            text in (cause.stderr or "")
            for text in ("Unsorted positions", "Chromosome blocks not continuous")
        ):
            raise
        with scratch_directory(vcf.parent, "SampleSort_") as folder:
            ordered = _sort_sample_vcf(vcf, Path(folder))
            move_file(ordered, vcf)
        ensure_vcf_indexed(vcf)


def _sort_sample_vcf(vcf: Path, folder: Path) -> Path:
    """Sort a single-sample VCF by coordinate, preserving equal-site order."""
    with (
        gzip.open(vcf, "rb") as source,
        closing(sqlite3.connect(folder / "records.sqlite")) as db,
    ):
        header = []
        line = source.readline()
        while line.startswith(b"#"):
            header.append(line)
            line = source.readline()
        contigs = {
            chrom.encode(): index
            for index, chrom in enumerate(
                vcf_header_chromosomes(b"".join(header).decode())
            )
        }

        # A disk-backed ordering key avoids holding a whole selected genome in memory.
        db.execute(
            "CREATE TABLE records (contig INTEGER, pos INTEGER, ordinal INTEGER, "
            "line BLOB, PRIMARY KEY (contig, pos, ordinal)) WITHOUT ROWID"
        )

        def records() -> Iterator[tuple[int, int, int, bytes]]:
            for ordinal, record in enumerate(chain((line,), source)):
                chrom, pos, _ = record.split(b"\t", 2)
                yield contigs.setdefault(chrom, len(contigs)), int(pos), ordinal, record

        db.executemany("INSERT INTO records VALUES (?,?,?,?)", records())
        ordered = folder / "sorted.vcf.gz"
        command = ["bgzip", "-c"]
        with (
            ordered.open("wb") as output,
            popen(command, stdin=subprocess.PIPE, stdout=output) as process,
        ):
            assert process.stdin is not None
            with process.stdin as stream:
                stream.writelines(header)
                stream.writelines(
                    row[0]
                    for row in db.execute(
                        "SELECT line FROM records ORDER BY contig, pos, ordinal"
                    )
                )
            if process.wait():
                raise subprocess.CalledProcessError(process.returncode, command)
    return ordered


def subset_vcf_sample(
    vcf: PathInput,
    sample: str,
    out: Path,
    *,
    selection: Callable[[list[str] | None], Sequence[str]] | None = None,
    output_type: str = "-Oz",
) -> None:
    """Select a sample, repairing the header only if bcftools reports a missing contig.

    A target selection can be rebuilt with the repaired contig names before retrying.
    """
    view = ["bcftools", "view", "--samples", sample, "--trim-alt-alleles"]
    output = [output_type, "-o", out]
    selection_args = list(selection(None)) if selection else []
    result = run_command(
        [*view, *selection_args, *output, vcf], capture_output=True, check=False
    )
    stderr = result.stderr or ""
    # HTSlib can report an undeclared record even when the target filter excludes it.
    if "-r" not in selection_args and re.search(
        r"Contig .+ is not defined in the header", stderr
    ):
        repaired = _header_with_record_contigs(vcf)
        if repaired is not None:
            header, used = repaired
            selection_args = list(selection(used)) if selection else []
            _stream_repaired_subset(
                vcf, header, [*view, *selection_args, *output, "-"], out
            )
            return
    if result.returncode:
        raise ToolExecutionError(result.returncode, result.args, result.stdout, stderr)
    if stderr.strip():
        logging.debug("%s", stderr.rstrip())


def _stream_repaired_subset(
    vcf: PathInput, header: str, view: Sequence[object], out: Path
) -> None:
    logging.info("Adding missing VCF contig declarations for sample selection.")
    with scratch_directory(out.parent, "SampleHeader_") as folder:
        new_header = Path(folder, "header.txt")
        new_header.write_text(header, encoding="utf-8", newline="\n")
        # reheader cannot read ordinary gzip. Decode compressed input as text
        # for both gzip and BGZF rather than recompressing the whole cohort.
        with Path(vcf).open("rb") as source:
            compressed = source.read(2) == b"\x1f\x8b"
        reheader = ["bcftools", "reheader", "-h", new_header]
        stages = (
            [["bgzip", "-dc", vcf], [*reheader, "-"]]
            if compressed
            else [[*reheader, vcf]]
        )
        run_pipeline(
            [*stages, view],
            description="Select VCF sample with repaired header",
            stderr_dir=out.parent,
        )


def _header_with_record_contigs(vcf: PathInput) -> tuple[str, list[str]] | None:
    """The replacement header and contigs with records, if repair is needed."""
    header = run_command(
        ["bcftools", "view", "--no-version", "-h", vcf], capture_output=True
    ).stdout
    declared = set(vcf_header_chromosomes(header))
    used: dict[str, None] = {}
    command = ["bcftools", "query", "-f", "%CHROM\\n", fspath(vcf)]
    with popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as query:
        assert query.stdout is not None
        for line in query.stdout:
            used.setdefault(line.rstrip(b"\r\n").decode(), None)
        if query.wait():
            raise subprocess.CalledProcessError(query.returncode, command)
    missing = [contig for contig in used if contig not in declared]
    if not missing:
        return None
    meta, columns = header.rstrip("\n").rsplit("\n", 1)
    return (
        "\n".join([meta, *(f"##contig=<ID={c}>" for c in missing), columns, ""]),
        list(used),
    )
