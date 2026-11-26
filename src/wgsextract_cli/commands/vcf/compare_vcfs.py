"""Stream comparison VCFs directly into compressed, staged output files."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path

from wgsextract_cli.core.utils import WGSExtractError, popen

from .compare_inputs import Dictionary
from .compare_records import Allele, Call

STATE_TAG = "WGSE_COMPARE_STATE"
DECISION_TAG = "WGSE_COMPARE_DECISION"
PARTITIONS = ("shared", "only_a", "only_b")


def comparison_info(labels: tuple[str, ...]) -> str:
    order = ", ".join(labels)
    return (
        f'##INFO=<ID={STATE_TAG},Number=.,Type=String,Description="Per-callset state in order: {order}">\n'
        f'##INFO=<ID={DECISION_TAG},Number=1,Type=String,Description="Comparison decision">\n'
    )


@contextmanager
def compressed_vcf(path: Path, work: Path) -> Iterator[Callable[[str], None]]:
    command = ["bcftools", "view", "--no-version", "-Oz", "-o", str(path), "-"]
    with (
        (work / f"{path.name}.stderr").open(
            "w+", encoding="utf-8", newline="\n"
        ) as errors,
        popen(command, stdin=subprocess.PIPE, stderr=errors) as process,
    ):
        stream = process.stdin
        assert stream is not None

        def check_exit() -> None:
            if process.wait():
                errors.seek(0)
                raise WGSExtractError(
                    f"Could not write {path.name}: {errors.read(4096).strip()}"
                )

        def write(line: str) -> None:
            try:
                stream.write(line.encode("utf-8"))
            except BrokenPipeError:
                check_exit()
                raise

        try:
            yield write
            try:
                stream.close()
            except BrokenPipeError:
                check_exit()
                raise
            check_exit()
        finally:
            if process.poll() is None:
                process.kill()
            with suppress(BrokenPipeError):
                stream.close()


class PartitionVCFs:
    def __init__(
        self,
        stack: ExitStack,
        paths: dict[str, Path],
        work: Path,
        dictionary: Dictionary,
        labels: tuple[str, ...],
    ) -> None:
        header = "##fileformat=VCFv4.2\n" + comparison_info(labels)
        for chrom in dictionary.names.values():
            length = dictionary.lengths.get(chrom)
            suffix = f",length={length}" if length else ""
            header += f"##contig=<ID={chrom}{suffix}>\n"
        header += "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        self.streams = {}
        for name in PARTITIONS:
            write = stack.enter_context(compressed_vcf(paths[f"compare_{name}"], work))
            write(header)
            self.streams[name] = write

    def write(self, allele: Allele, calls: list[Call]) -> None:
        a, b = calls
        if a.carried:
            decision = "shared" if b.carried else "only_a"
        elif b.carried:
            decision = "only_b"
        else:
            return
        chrom, pos, ref, alt = allele
        self.streams[decision](
            f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t.\t.\t"
            f"{STATE_TAG}={a.state},{b.state};{DECISION_TAG}={decision}\n"
        )
