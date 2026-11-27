"""Write BGZF VCFs with uppercase sequence alleles, without re-normalizing."""

import re
import sys

from wgsextract_cli.core.dependency_checks import get_tool_version


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
        [sys.executable, "-m", "wgsextract_cli.core.vcf_alleles"],
        ["bcftools", "view", "--no-version", "--threads", threads, "-Oz", "-o", output],
    ]
