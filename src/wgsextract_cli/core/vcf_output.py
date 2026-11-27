"""Write BGZF VCFs with uppercase sequence alleles, without re-normalizing."""

import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from wgsextract_cli.core.atomic_output import output_publication_scope
from wgsextract_cli.core.dependency_checks import get_tool_version
from wgsextract_cli.core.utils import run_pipeline
from wgsextract_cli.core.variant_files import publish_indexed_vcf

_UPPERCASE = [sys.executable, str(Path(__file__).with_name("vcf_alleles.py"))]


def publish_called_vcf(
    output: str, *, source: str | None = None, stages: Sequence[list[str]] = ()
) -> None:
    """Publish a caller's text VCF (from a file or pipeline) with a fresh index."""
    uppercase = [*_UPPERCASE, *([source] if source is not None else [])]
    final = Path(output)
    with tempfile.TemporaryDirectory(dir=final.parent, prefix=".wgse_call_") as work:
        staged = Path(work, final.name)
        with staged.open("wb") as stream:
            run_pipeline(
                [*stages, uppercase, ["bgzip", "-c"]],
                stdin=subprocess.DEVNULL,
                stdout=stream,
                description="Normalize caller VCF allele case",
            )
        # A caller may publish inside its private directory before gathering.
        with output_publication_scope():
            publish_indexed_vcf(staged, final)


@contextmanager
def called_vcf_output(output: str) -> Iterator[str]:
    """Stage callers that only write to a file, then publish their VCF."""
    final = Path(output)
    with tempfile.TemporaryDirectory(dir=final.parent, prefix=".wgse_call_") as work:
        raw = str(Path(work, final.name))
        yield raw
        publish_called_vcf(output, source=raw)


def uppercase_vcf(
    command: list[str],
    output: str,
    *,
    source: str | None = None,
    threads: str = "0",
    check_ref: str | None = None,
) -> list[list[str]]:
    """Finish a bcftools pipeline, optionally folding case fixing into norm.

    Pass the command's options without its input and output. If ``check_ref``
    is given, the command must be a ref-backed ``norm`` without ``-c``.
    """
    version = get_tool_version("bcftools") or ""
    match = re.match(r"bcftools (\d+)\.(\d+)", version)
    inputs = [source] if source is not None else []
    if match and (int(match[1]), int(match[2])) >= (1, 25):
        options = ["--threads", threads, "-Oz", "-o", output]
        if check_ref is not None:
            return [[*command, "-c", check_ref + "C", *options, *inputs]]
        return [
            [*command, "-Ou", *inputs],
            ["bcftools", "norm", "--no-version", "-c", "C", *options],
        ]
    check = ["-c", check_ref] if check_ref is not None else []
    return [
        [*command, *check, "-Ov", *inputs],
        _UPPERCASE,
        ["bcftools", "view", "--no-version", "--threads", threads, "-Oz", "-o", output],
    ]
