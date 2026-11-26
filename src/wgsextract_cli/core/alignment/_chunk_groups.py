"""Split a coordinate-sorted BAM on stdin into consecutive contig groups."""

import json
import logging
import re
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path


def binary_group_splitter_available() -> bool:
    from importlib.metadata import PackageNotFoundError, version

    try:
        release = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?", version("pysam"))
    except PackageNotFoundError:
        return False
    return release is not None and tuple(map(int, release.groups())) >= (0, 24, 1)


@dataclass
class ChunkGroupOutput:
    options: list[str]
    splitter: list[str]

    def sort(self, target: Path, prefix: Path, threads: str, memory: str) -> list[str]:
        argv = ["samtools", "sort", "--no-PG", "-@", threads, "-m", memory,
                "-T", str(prefix)]  # fmt: skip
        if self.splitter:
            return [*argv, "-u", "-O", "BAM", "-"]
        # The fallback needs CSI on references with contigs >512 Mb.
        return [*argv, "-l", "1", "-O", "BAM", "--write-index", "-o",
                f"{target}##idx##{target}.csi", "-"]  # fmt: skip

    def stages(self, number: int) -> list[list[str]]:
        return [[*self.splitter, str(number)]] if self.splitter else []


def chunk_group_output(
    workdir: Path,
    names: list[list[str]],
    reference: str,
    fmt: str,
    profile: str,
    threads: str,
    checkpoint: Path | None,
) -> ChunkGroupOutput:
    from wgsextract_cli.core.alignment.cram_staging import cram_version
    from wgsextract_cli.core.alignment.profiles import (
        output_profile_options,
        probe_cram_output_version,
    )
    from wgsextract_cli.core.atomic_output import atomic_write
    from wgsextract_cli.core.utils import WGSExtractError

    streaming = binary_group_splitter_available()
    codec = output_profile_options(profile) if fmt == "CRAM" else []
    if fmt == "CRAM" and not codec and (checkpoint is not None or streaming):
        # samtools cat requires the same CRAM minor version for old and new groups.
        version = (
            cram_version(str(checkpoint))
            if checkpoint is not None
            else probe_cram_output_version([])
        )
        if version is None:
            raise WGSExtractError(
                "Could not determine the CRAM version for chunk groups."
            )
        codec = ["--output-fmt-option", f"version={version[0]}.{version[1]}"]
    options = [
        "-O",
        fmt,
        *(["--reference", reference, *codec] if fmt == "CRAM" else []),
    ]
    if not streaming or (
        fmt == "CRAM" and not {"version=3.0", "version=3.1"}.intersection(codec)
    ):
        return ChunkGroupOutput(options, [])

    manifest = workdir / "split-groups.json"
    atomic_write(manifest, json.dumps(names).encode("utf-8"))
    logging.info("Streaming each sorted chunk into its reference groups.")
    return ChunkGroupOutput(
        options,
        [
            sys.executable,
            "-m",
            "wgsextract_cli.core.alignment._chunk_groups",
            str(manifest),
            reference,
            fmt,
            json.dumps(codec[1::2]),
            threads,
        ],  # fmt: skip
    )


def split_groups(
    manifest: Path,
    number: int,
    reference: str,
    fmt: str,
    format_options: list[str],
    threads: int,
) -> None:
    pysam = import_module("pysam")
    groups: list[list[str]] = json.loads(manifest.read_bytes())
    group_by_name = {
        name: index for index, names in enumerate(groups) for name in names
    }
    cram = fmt == "CRAM"
    options = (
        {
            "reference_filename": reference,
            "format_options": [option.encode("ascii") for option in format_options],
        }
        if cram
        else {}
    )
    with pysam.AlignmentFile(sys.stdin.buffer, "rb") as source:
        by_id = [group_by_name[name] for name in source.references]
        unplaced = group_by_name["*"]

        def group(record_id: int) -> int:
            return by_id[record_id] if record_id >= 0 else unplaced

        records = iter(source)
        record = next(records, None)
        for index in range(len(groups)):
            path = (
                manifest.parent
                / f"group-{index:03d}"
                / f"chunk-{number:05d}.{fmt.lower()}"
            )
            with pysam.AlignmentFile(
                str(path),
                "wc" if cram else "wb",
                template=source,
                threads=threads,
                **options,
            ) as output:
                while record is not None and group(record.reference_id) == index:
                    output.write(record)
                    record = next(records, None)
        if record is not None:
            raise ValueError("Sorted chunk has records outside reference group order")


if __name__ == "__main__":
    manifest, reference, fmt, options, threads, number = sys.argv[1:]
    split_groups(
        Path(manifest), int(number), reference, fmt, json.loads(options), int(threads)
    )
