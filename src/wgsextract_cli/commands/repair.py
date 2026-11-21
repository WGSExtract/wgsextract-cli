import argparse
import gzip
import io
import logging
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TextIO

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.api.repair import RepairFtdnaBamParams, RepairFtdnaVcfParams
from wgsextract_cli.cli.registration.repair import register as register
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.ref_library.assets import input_stem
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.results import current_result, json_mode
from wgsextract_cli.core.temp_directory import input_scratch_directory, scratch_parent
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    _is_sigpipe,
    popen,
    run_command,
)
from wgsextract_cli.core.variant_files import calculate_bam_md5

from ._ftdna_vcf import repair_vcf_stream

# Records between cancellation checks in the stream copies.
_CHECK_EVERY = 10_000


def repair_bam(args: argparse.Namespace) -> None:
    if _input_was_explicit(args) and args.input:
        _cli_repair_bam(args)
        return
    # Pipe mode: samtools view -h in.bam | wgsextract repair ftdna-bam | samtools view -b
    _refuse_json_in_pipe_mode()
    logging.info(LOG_MESSAGES["repair_bam"])
    repair_bam_stream(sys.stdin, sys.stdout)


def repair_vcf(args: argparse.Namespace) -> None:
    if _input_was_explicit(args) and args.input:
        _cli_repair_vcf(args)
        return
    # Pipe mode: bcftools view in.vcf | wgsextract repair ftdna-vcf > out.vcf
    _refuse_json_in_pipe_mode()
    logging.info(LOG_MESSAGES["repair_vcf"])
    repair_vcf_stream(sys.stdin, sys.stdout)


def repair_bam_file(params: RepairFtdnaBamParams) -> None:
    """Stream samtools view -h | repair | samtools view -b without temp SAM files."""
    input_path = expand_path(params.input)
    if not Path(input_path).is_file():
        raise WGSExtractError(f"Input BAM/CRAM does not exist: {input_path}")
    verify_dependencies(["samtools"])
    output_path = _repair_output_path(params, ".bam")

    cram_opt: list[str] = []
    if input_path.lower().endswith(".cram"):
        md5 = calculate_bam_md5(input_path, None)
        ref_arg = expand_path(params.ref) if params.ref else None
        ref = ReferenceLibrary(ref_arg, md5, input_path=input_path).fasta or ref_arg
        if not ref or not Path(ref).is_file():
            raise WGSExtractError(
                f"No reference FASTA for {input_path}"
                + (f" at {ref_arg}" if ref_arg else "; pass --ref.")
            )
        cram_opt = ["-T", ref]

    with (
        _stage_repair(output_path, ".bam") as staged,
        tempfile.TemporaryFile() as read_err,
        tempfile.TemporaryFile() as write_err,
    ):
        reader = popen(
            ["samtools", "view", "-h", *cram_opt, input_path],
            stdout=subprocess.PIPE,
            stderr=read_err,
        )
        writer = popen(
            ["samtools", "view", "-b", "-"],
            stdin=subprocess.PIPE,
            stdout=staged,
            stderr=write_err,
        )
        assert reader.stdout is not None and writer.stdin is not None
        counts = (0, 0)
        try:
            try:
                with (
                    io.TextIOWrapper(
                        reader.stdout, encoding="utf-8", errors="replace"
                    ) as sam_in,
                    io.TextIOWrapper(writer.stdin, encoding="utf-8") as sam_out,
                ):
                    counts = repair_bam_stream(sam_in, sam_out)
            except BrokenPipeError:
                pass  # The writer exited early; its exit status says why.
            read_code, write_code = reader.wait(), writer.wait()
        finally:
            for proc in (reader, writer):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        if write_code or read_code:
            # A failed reader leaves the writer an empty or truncated stream,
            # and a failed writer kills the reader with SIGPIPE; report the cause.
            reader_failed = read_code and not _is_sigpipe(read_code)
            label, code, err = (
                ("samtools view -h", read_code, read_err)
                if reader_failed or not write_code
                else ("samtools view -b", write_code, write_err)
            )
            raise WGSExtractError(
                f"FTDNA BAM repair failed in {label} (exit {code}): {_tail(err)}"
            )
    _record_repair(output_path, "repaired_alignment", counts)
    logging.info("Repaired FTDNA BAM written to %s", output_path)


def repair_vcf_file(params: RepairFtdnaVcfParams) -> None:
    input_path = expand_path(params.input)
    if not Path(input_path).is_file():
        raise WGSExtractError(f"Input VCF/BCF does not exist: {input_path}")
    output_path = _repair_output_path(params, ".vcf")
    output_dir = Path(output_path).parent
    with _stage_repair(output_path, ".vcf") as staged:
        with io.TextIOWrapper(
            staged, encoding="utf-8", newline="\n"
        ) as repaired_output:
            counts = _repair_vcf_to(input_path, repaired_output, output_dir)
    _record_repair(output_path, "repaired_vcf", counts)
    logging.info("Repaired FTDNA VCF written to %s", output_path)


def _repair_vcf_to(
    input_path: str, repaired_output: TextIO, output_dir: Path
) -> tuple[int, int]:
    spool_dir = scratch_parent(output_dir)
    if input_path.lower().endswith(".bcf"):
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".vcf",
            delete=False,
            encoding="utf-8",
            dir=input_scratch_directory(output_dir),
        ) as tmp_vcf:
            tmp_path = tmp_vcf.name
        try:
            with open(tmp_path, "wb") as vcf_output:
                run_command(["bcftools", "view", input_path], stdout=vcf_output)
            with open(tmp_path, encoding="utf-8", errors="replace") as vcf_input:
                return repair_vcf_stream(
                    vcf_input, repaired_output, spool_dir=spool_dir
                )
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
    with _open_text_variant_input(input_path) as vcf_input:
        return repair_vcf_stream(vcf_input, repaired_output, spool_dir=spool_dir)


def _refuse_json_in_pipe_mode() -> None:
    if json_mode():
        raise UsageError(
            "--json needs --input: in pipe mode the repaired records go to stdout."
        )


def _record_repair(path: str, role: str, counts: tuple[int, int]) -> None:
    records, repaired = counts
    current_result().add_output(path, role, metrics={"records": records})
    current_result().add_metrics(records=records, repaired_records=repaired)


def repair_bam_stream(input_stream: TextIO, output_stream: TextIO) -> tuple[int, int]:
    """Copy SAM text, replacing spaces in read names; returns the record count
    and how many records were changed."""
    records = repaired = 0
    for line in input_stream:
        if line.startswith("@"):
            output_stream.write(line)
            continue
        records += 1
        if not records % _CHECK_EVERY:
            checkpoint()
        fields = line.rstrip("\n").split("\t")
        if fields and " " in fields[0]:
            fields[0] = fields[0].replace(" ", ":")
            repaired += 1
        output_stream.write("\t".join(fields) + "\n")
    return records, repaired


def _repair_output_path(
    params: RepairFtdnaBamParams | RepairFtdnaVcfParams, extension: str
) -> str:
    input_path = expand_path(params.input)
    if params.output:
        output = str(absolute_path(expand_path(params.output)))
    else:
        outdir = expand_path(params.outdir or absolute_path(input_path).parent)
        name = f"{input_stem(input_path)}_repaired{extension}"
        output = str(absolute_path(Path(outdir, name)))
    destination = Path(output)
    try:
        aliases_input = destination.samefile(input_path)
    except FileNotFoundError:
        aliases_input = False
    if aliases_input:
        raise WGSExtractError(f"Output {output} would overwrite the input.")
    try:
        if not stat.S_ISREG(destination.lstat().st_mode):
            raise WGSExtractError(f"Output {output} is not a regular file.")
    except FileNotFoundError:
        pass
    return output


@contextmanager
def _stage_repair(output: str, extension: str) -> Iterator[IO[bytes]]:
    parent = Path(output).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=".wgse-repair-",
            suffix=extension,
            delete=False,
        )
    except OSError as exc:
        raise WGSExtractError(
            f"Cannot stage repaired output {output}: {exc.strerror or exc}"
        ) from exc
    staged = Path(temp.name)
    identity = None
    try:
        with temp:
            identity = os.fstat(temp.fileno())
            yield temp.file
        checkpoint()
        if not os.path.samestat(staged.lstat(), identity):
            raise WGSExtractError(f"Staging file changed for repaired output {output}.")
        staged.replace(output)
    finally:
        try:
            if identity is None or os.path.samestat(staged.lstat(), identity):
                staged.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def _input_was_explicit(args: argparse.Namespace) -> bool:
    explicit_dests = getattr(args, "_explicit_dests", None)
    if explicit_dests is None:
        return bool(getattr(args, "input", None))
    return "input" in explicit_dests


def _tail(stream: IO[bytes], max_chars: int = 2000) -> str:
    stream.seek(0)
    return stream.read().decode(errors="replace")[-max_chars:].strip()


def _open_text_variant_input(path: str) -> TextIO:
    if path.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


_cli_repair_bam = cli_handler(RepairFtdnaBamParams, repair_bam_file)
_cli_repair_vcf = cli_handler(RepairFtdnaVcfParams, repair_vcf_file)
