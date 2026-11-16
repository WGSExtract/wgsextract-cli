"""``--reference-evidence gvcf``: bcftools' gVCF for ``vcf snp``/``vcf indel``,
and publishing the gVCF other callers write beside their VCF (``publish_gvcf``).

The gVCF comes from its own pass over the reads, apart from the variant VCF,
which it never changes: one SNP+indel pileup (a SNP-only one, like ``vcf
snp``'s, reports indel sites as reference), with reads under MAPQ 20 left out
so they don't count as evidence for reference, and blocks banded by minimum
depth (``BCFTOOLS_BANDS``). Each contig with reads is called in its own
process, so a contig's records never depend on the thread count (bcftools
subsamples columns deeper than 255 bases with one random stream per process).
"""

from __future__ import annotations

import bisect
import logging
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import as_completed
from contextlib import ExitStack, suppress
from functools import partial
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.bgzf import is_bgzf
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.progress import ContigProgress
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.region_selection import selected_intervals
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.sample_ploidy import sex_dependent_ploidy
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command, run_pipeline
from wgsextract_cli.core.variant_confidence import SYMBOLIC_REFERENCE_ALTS
from wgsextract_cli.core.variant_files import ensure_vcf_indexed

from .basic import plain_reference
from .call_cleanup import indel_cleanup_pipeline
from .contig_calling import populated_contigs

if TYPE_CHECKING:
    from .caller_ploidy import PloidyInterval

GVCF_NAME = "bcftools.g.vcf.gz"


def _stages(
    alignment: str,
    ref: str,
    output: str,
    region_args: list[str],
    profile: list[str],
    call_args: list[str],
) -> list[list[str]]:
    bands = evidence.BCFTOOLS_BANDS
    # contract: no -I (indel sites must not read as reference), MAPQ >= 20,
    # and depth bands on both mpileup and call make the evidence's meaning.
    mpileup = [
        "bcftools", "mpileup", *profile, "-B",
        "-q", str(evidence.BCFTOOLS_MIN_MAPPING_QUALITY), "-g", bands,
        "-a", "FORMAT/AD,FORMAT/DP", "-C", "50", "-f", ref, "-Ou",
        *region_args, alignment,
    ]  # fmt: skip
    call = ["bcftools", "call", *call_args, "-m", "-g", bands, "-Ou"]
    return [mpileup, call, *indel_cleanup_pipeline(ref, output, "0", mixed=True)]


def _provenance(
    ref: str, region: str | None, ploidy_args: Sequence[str], sex_args: Sequence[str]
) -> evidence.Provenance:
    version = run_command(["bcftools", "--version"], capture_output=True)
    ploidy = " ".join(ploidy_args[1:2]) if ploidy_args else ""
    if "--ploidy-file" in ploidy_args:
        ploidy = Path(ploidy).name
    # bcftools calls a sample missing from --samples-file as female.
    sex = (
        "M"
        if sex_args
        else "F"
        if sex_dependent_ploidy(list(ploidy_args), region)
        else ""
    )
    lengths = read_fai_lengths(ref)
    rows = evidence.ploidy_definitions(ploidy_args)
    zero = evidence.ploidy_zero(rows, sex, lengths)
    if region:
        try:
            selected = selected_intervals(region, lengths, commas=True)
        except (OSError, ValueError):
            selected = None
        if selected is not None:
            region = evidence.canonical_region(
                (contig, start, end)
                for contig in lengths
                for start, end in selected.get(contig, ())
            )
            zero = tuple(item for item in zero if item.rpartition(":")[0] in selected)
    return evidence.Provenance(
        engine="bcftools",
        version=version.stdout.splitlines()[0].removeprefix("bcftools ").strip(),
        quality_model=evidence.DEPTH_ONLY,
        bands=evidence.BCFTOOLS_BANDS,
        min_mapping_quality=evidence.BCFTOOLS_MIN_MAPPING_QUALITY,
        region=region,
        ploidy=ploidy,
        sex=sex,
        ploidy_zero=zero,
    )


def write_gvcf(
    alignment: str,
    ref: str,
    outdir: str,
    *,
    threads: str,
    region: str | None,
    profile: list[str],
    ploidy_args: list[str],
    sex_args: list[str],
    prior_args: list[str],
    environment: Mapping[str, str] | None = None,
) -> str:
    """Write and publish ``bcftools.g.vcf.gz`` (+ .tbi) in ``outdir``, both as
    result outputs with the provenance as the gVCF's metrics."""
    output = str(Path(outdir, GVCF_NAME))
    provenance = _provenance(ref, region, ploidy_args, sex_args)
    call_args = [*ploidy_args, *sex_args, *prior_args]
    contigs = {} if region else populated_contigs(alignment, ref)
    env = {**os.environ, **environment} if environment else None
    cancelled = Event()
    logging.info(f"Writing reference evidence (gVCF) to {output}...")
    with (
        tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_gvcf_") as work,
        atomic_output(output) as staged,
    ):
        jobs: list[tuple[str, list[str]]] = []
        if contigs:
            for index, (name, length) in enumerate(contigs.items()):
                # A TSV keeps names such as HLA-A*01:01:01:01 literal.
                request = Path(work, f"{index:06d}.regions.tsv")
                request.write_text(f"{name}\t1\t{length}\n", encoding="utf-8", newline="\n")  # fmt: skip
                jobs.append((name, ["-R", str(request)]))
        else:
            jobs.append((region or "", ["-r", region] if region else []))
        parts = [str(Path(work, f"{index:06d}.vcf.gz")) for index in range(len(jobs))]
        progress = ContigProgress(contigs, contigs) if contigs else None

        def call(index: int) -> None:
            name, region_args = jobs[index]
            run_pipeline(
                _stages(alignment, ref, parts[index], region_args, profile, call_args),
                env=env,
                description="Reference evidence (gVCF)"
                + (f" ({name})" if name else ""),
                cancel_event=cancelled,
            )
            if progress:
                progress.done(name)

        # Longest contigs first, so a large one late in the order doesn't run alone.
        order = sorted(range(len(jobs)), key=lambda i: -contigs.get(jobs[i][0], 0))
        with ContextThreadPoolExecutor(
            max_workers=max(1, min(int(threads), len(jobs)))
        ) as pool:
            futures = [pool.submit(partial(call, index)) for index in order]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                cancelled.set()
                for future in futures:
                    future.cancel()
                raise
        header = Path(work, "provenance.txt")
        header.write_text(provenance.header() + "\n", encoding="utf-8", newline="\n")
        file_list = Path(work, "parts.txt")
        file_list.write_text("".join(f"{part}\n" for part in parts), encoding="utf-8", newline="\n")  # fmt: skip
        run_pipeline(
            [
                ["bcftools", "concat", "--no-version", "-f", str(file_list), "-Ou"],
                [
                    "bcftools",
                    "annotate",
                    "--no-version",
                    "-h",
                    str(header),
                    "-Oz",
                    "-o",
                    staged,
                ],
            ],  # fmt: skip
            description="Reference evidence (gVCF) merge",
            cancel_event=cancelled,
        )
        ensure_vcf_indexed(staged)
    current_result().add_output(output, evidence.ROLE, metrics=provenance.metrics())
    current_result().add_output(output + ".tbi", evidence.INDEX_ROLE)
    return output


def caller_provenance(
    engine: str,
    version: str,
    intervals: list[PloidyInterval],
    bounded: object,
    *,
    bands: str = "",
    min_mapping_quality: int | None = None,
) -> evidence.Provenance:
    """The provenance of a GQ caller's gVCF, called with the sex/PAR ploidy
    ``intervals`` (caller_ploidy.calling_ploidy's), which cover what it
    called. When ``bounded`` (a --region, or a caller that calls only some
    contigs by default) its Region is theirs, resolved; else every contig."""
    from .caller_ploidy import policy_sex

    def span(item: PloidyInterval) -> str:
        return f"{item.chrom}:{item.start + 1}-{item.end}"

    return evidence.Provenance(
        engine=engine,
        version=version,
        quality_model=evidence.GQ,
        bands=bands,
        min_mapping_quality=min_mapping_quality,
        region=(
            evidence.canonical_region((i.chrom, i.start, i.end) for i in intervals)
            if bounded
            else None
        ),
        ploidy=",".join(f"{span(i)}={i.ploidy}" for i in intervals if i.ploidy != 2),
        sex=policy_sex(intervals),
        ploidy_zero=tuple(span(i) for i in intervals if i.ploidy == 0),
    )


def clip_gvcf(
    source: str,
    intervals: Sequence[PloidyInterval],
    ref: str,
    output: str,
    called: Sequence[PloidyInterval] | None = None,
) -> None:
    """Write ``source``'s records inside ``intervals`` (one ploidy
    partition, zero-based half-open) to ``output`` (+ .tbi), so partitions
    gathered side by side never claim one base twice: a reference block is
    cut at the partition's edges (its REF becomes the base it now starts on),
    and any other record belongs to the partition its POS is in, as in the
    partitioned VCF. A record starting outside every partition ``called``
    (default ``intervals``) is kept where it covers the partition, so a
    deletion starting before the selection still covers its bases there.
    Every other field stays as the caller wrote it."""
    spans, owned = _spans(intervals), _spans(called or intervals)

    with (
        tempfile.TemporaryDirectory(
            dir=Path(output).parent, prefix=".wgse_gvcf_clip_"
        ) as work,
        ExitStack() as stack,
    ):
        fasta: list[str] = []

        def base(contig: str, position: int) -> str:
            if not fasta:
                # samtools faidx reads plain or bgzipped FASTA, not gzip.
                gzip_only = ref.lower().endswith(".gz") and not is_bgzf(ref)
                fasta.append(
                    stack.enter_context(plain_reference(ref, work))
                    if gzip_only
                    else ref
                )
            fetched = run_command(
                ["samtools", "faidx", fasta[0], f"{{{contig}}}:{position}-{position}"],
                capture_output=True,
            )
            return "".join(fetched.stdout.splitlines()[1:]).upper() or "N"

        errors = Path(work, "stderr.txt")
        with open(errors, "w+", encoding="utf-8", newline="\n") as stderr:
            reader = popen(
                ["bcftools", "view", "--no-version", source],
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
            writer = popen(
                ["bcftools", "view", "--no-version", "-Oz", "-o", output],
                stdin=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
            assert reader.stdout is not None and writer.stdin is not None
            broken = False
            try:
                for line in reader.stdout:
                    if not line.startswith("#"):
                        line = _cut_record(line, spans, owned, base)
                    writer.stdin.write(line)
            except BrokenPipeError:
                broken = True  # The writer failed; its stderr says why.
            finally:
                reader.stdout.close()
                with suppress(BrokenPipeError):
                    writer.stdin.close()
                codes = reader.wait(), writer.wait()
            if broken or any(codes):
                stderr.seek(0)
                raise WGSExtractError(
                    f"Cutting the gVCF {source} to its partition failed: "
                    f"{stderr.read().strip()[-500:]}"
                )
    ensure_vcf_indexed(output)


def _spans(intervals: Sequence[PloidyInterval]) -> dict[str, list[tuple[int, int]]]:
    """Each contig's sorted 1-based inclusive spans."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for item in intervals:
        spans.setdefault(item.chrom, []).append((item.start + 1, item.end))
    for ranges in spans.values():
        ranges.sort()
    return spans


_END = re.compile(r"(?<![^;])END=(\d+)")


def _first_ending_after(ranges: list[tuple[int, int]], position: int) -> int:
    """The index of the first of the sorted, disjoint ``ranges`` ending at
    or after ``position``."""
    return bisect.bisect_left(ranges, position, key=lambda span: span[1])


def _cut_record(
    line: str,
    spans: Mapping[str, list[tuple[int, int]]],
    owned: Mapping[str, list[tuple[int, int]]],
    base: Callable[[str, int], str],
) -> str:
    """``line`` (one gVCF record) cut to the sorted, disjoint 1-based
    ``spans``: "" when outside them, else one record per span it covers.
    A record other than a block is kept whole when its POS is in ``spans``,
    or outside ``owned`` while the bases it covers reach into ``spans``."""
    fields = line.split("\t", 8)
    contig, pos = fields[0], int(fields[1])
    ranges = spans.get(contig, [])
    index = _first_ending_after(ranges, pos)
    end_match = _END.search(fields[7])
    end = max(pos + len(fields[3]) - 1, int(end_match.group(1)) if end_match else 0)
    block = end_match is not None and all(
        alt in SYMBOLIC_REFERENCE_ALTS for alt in fields[4].split(",")
    )
    if not block:
        if index < len(ranges) and ranges[index][0] <= pos:
            return line
        claimed = owned.get(contig, [])
        at = _first_ending_after(claimed, pos)
        if at < len(claimed) and claimed[at][0] <= pos:
            return ""  # Its POS is another partition's.
        return line if index < len(ranges) and ranges[index][0] <= end else ""
    pieces = []
    while index < len(ranges) and ranges[index][0] <= end:
        first, last = ranges[index]
        index += 1
        start, stop = max(pos, first), min(end, last)
        if start == pos and stop == end:
            return line
        fields[1] = str(start)
        fields[3] = fields[3] if start == pos else base(contig, start)
        fields[7] = _END.sub(f"END={stop}", fields[7], count=1)
        pieces.append("\t".join(fields))
    return "".join(pieces)


def gather_gvcf(parts: Sequence[str], work: str, output: str) -> None:
    """Gather partition gVCFs cut by ``clip_gvcf`` into ``output`` in
    reference order. A record starting before the selection may reach into
    two partitions, so each copy after the first (same POS, REF and ALT) is
    dropped."""
    combined = str(Path(work, "combined.g.vcf.gz"))
    run_command(["bcftools", "concat", "--no-version", "-a", "-Oz", "-o", combined, *parts])  # fmt: skip
    run_pipeline(
        [
            ["bcftools", "sort", "-T", work, "-Ou", combined],
            ["bcftools", "norm", "--no-version", "-d", "exact", "-Oz", "-o", output],
        ],
        description="Reference evidence (gVCF) gather",
    )
    Path(combined).unlink()


def publish_gvcf(source: str, output: str, provenance: evidence.Provenance) -> None:
    """Publish a caller's gVCF ``source`` as ``output`` (+ .tbi) with the
    ``##wgseReferenceEvidence`` header. Only the header is rewritten (bcftools
    reheader copies the records' BGZF blocks), so every record, FILTER and
    FORMAT field stays as the caller wrote it."""
    header = run_command(["bcftools", "view", "-h", source], capture_output=True)
    prefix = f"##{evidence.HEADER_KEY}="
    lines = [line for line in header.stdout.splitlines() if not line.startswith(prefix)]  # fmt: skip
    lines.insert(len(lines) - 1, provenance.header())
    with (
        tempfile.TemporaryDirectory(dir=Path(output).parent, prefix=".wgse_gvcf_") as work,
        atomic_output(output) as staged,
    ):  # fmt: skip
        text = Path(work, "header.txt")
        text.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        run_command(["bcftools", "reheader", "-h", str(text), "-o", staged, source])
        ensure_vcf_indexed(staged)


def record_gvcf(output: str) -> None:
    """Record a published gVCF and its index as result outputs, with the
    provenance its header records as the gVCF's metrics."""
    header = run_command(["bcftools", "view", "-h", output], capture_output=True)
    provenance = next(
        (p for line in header.stdout.splitlines() if (p := evidence.parse_provenance(line))),
        None,
    )  # fmt: skip
    metrics = provenance.metrics() if provenance else None
    current_result().add_output(output, evidence.ROLE, metrics=metrics)
    current_result().add_output(output + ".tbi", evidence.INDEX_ROLE)
