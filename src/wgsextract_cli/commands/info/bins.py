"""Exact depth-bucket breadth, without materializing per-base depth on disk."""

import csv
import io
import logging
import re
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import IO

from wgsextract_cli.core.results import human_output
from wgsextract_cli.core.table_writer import (
    Column,
    Value,
    table_path,
    tsv_lines,
    write_table,
)
from wgsextract_cli.core.utils import WGSExtractError, popen

from .metrics import IdxStat, contig_key, is_primary_contig
from .render import _cram_reference_opts, _resolve_sample_targets

_DEPTH_CHUNK_BYTES = 8 << 20
_DEPTH_DIGITS = b"0123456789"
_DEPTH_BUCKETS = bytes.maketrans(b"123456789", b"111444488")


def _count_depth_block(data: bytes, counts: Mapping[bytes, list[int]]) -> None:
    if b"\r" in data:
        data = data.replace(b"\r\n", b"\n")
    start = 0
    mixed = False
    rows = data.count(b"\n")
    while start < len(data):
        first_end = data.index(b"\n", start)
        separator = data.find(b"\t", start, first_end)
        name = data[start:separator] if separator >= 0 else b""
        if separator < 0 or name not in counts:
            raise WGSExtractError(
                f"Unexpected samtools depth row: {data[start : start + 120]!r}"
            )
        prefix = name + b"\t"
        end = len(data)
        if start == 0:
            mixed = data.count(b"\n" + prefix) != rows - 1
        if mixed:
            # Most chunks contain one contig. Only boundary chunks need a
            # line-start search, performed in C rather than iterating rows.
            transition = re.compile(rb"(?m)^(?!" + re.escape(prefix) + rb")")
            match = transition.search(data, start)
            assert match is not None
            end = match.start()
        block = data[start:end]
        if mixed:
            rows = block.count(b"\n")
        zero = block.count(b"\t0\n")
        # Removing digits leaves the name's non-digits and two tabs per row.
        # This validates fields without allocating a name-stripped copy.
        shape = name.translate(None, _DEPTH_DIGITS) + b"\t\t\n"
        if (
            block.translate(None, _DEPTH_DIGITS) != shape * rows
            or b"\t\t" in block
            or b"\t\n" in block
            or block.count(b"\t0") != zero
        ):
            if b"\t-" in block:
                raise WGSExtractError("samtools depth returned a negative depth.")
            raise WGSExtractError(
                f"Unexpected samtools depth row: {data[start : start + 120]!r}"
            )
        bucketed = block.translate(_DEPTH_BUCKETS)
        low = bucketed.count(b"\t1\n")
        medium = bucketed.count(b"\t4\n")
        bins = counts[name]
        bins[1] += low
        bins[2] += medium
        bins[3] += rows - zero - low - medium
        start = end


def count_depth_bins(
    stream: IO[bytes], lengths: Mapping[str, int]
) -> dict[str, list[int]]:
    """Count binary samtools output in chunks; infer unreported zero depths."""
    counts = {name: [0, 0, 0, 0] for name in lengths}
    encoded = {name.encode(): bins for name, bins in counts.items()}
    pending = b""
    while chunk := stream.read(_DEPTH_CHUNK_BYTES):
        data = pending + chunk
        end = data.rfind(b"\n") + 1
        if end:
            _count_depth_block(data[:end], encoded)
        pending = data[end:]
        if len(pending) > _DEPTH_CHUNK_BYTES:
            raise WGSExtractError("Unexpected samtools depth row: line too long.")
    if pending:
        _count_depth_block(pending + b"\n", encoded)
    for name, bins in counts.items():
        bins[0] = lengths[name] - sum(bins[1:])
        if bins[0] < 0:
            raise WGSExtractError(f"Depth sites exceed the length of {name}.")
    return counts


_BANDS = ("0", "1-3", "4-7", "8+")
# The column names are the TSV's since --bins was added; Model N Len is newer.
BINS_TABLE = (
    Column("Seq Name", "TEXT", "Contig."),
    Column("Model Len", "INTEGER", "Contig (or --region) length in bases."),
    Column(
        "Model N Len",
        "INTEGER",
        "Reference N bases, left out of the fractions; whole contigs only, as a "
        "partial span's share is unknown.",
    ),
    Column("Depth 0 bases", "INTEGER", "Bases at depth 0, N bases included."),
    *(
        Column(f"Depth {band} bases", "INTEGER", f"Bases at depth {band}.")
        for band in _BANDS[1:]
    ),
    *(
        Column(
            f"Depth {band} fraction",
            "REAL",
            f"Fraction of the non-N length at depth {band}.",
            ".8f",
        )
        for band in _BANDS
    ),
)
_N_COLUMN = BINS_TABLE[2].name
_Raw = tuple[str, int, int | None, list[int]]  # name, length, Ns, depth bands


def _non_n_bands(ns: int, bins: list[int]) -> list[int]:
    """Depth 0 / 1-3 / 4-7 / 8+ counts of the non-N bases.

    Ns are depth 0 unless reads overlap them (an isolated N, a gap's edge).
    Depth gives counts, not positions, so such covered Ns leave the deepest
    bands, where reads spanning them sit, keeping each share within 100 %."""
    counts = [max(bins[0] - ns, 0), *bins[1:]]
    excess = max(ns - bins[0], 0)
    for band in (3, 2, 1):
        taken = min(excess, counts[band])
        counts[band] -= taken
        excess -= taken
    return counts


def _bins_row(name: str, length: int, ns: int, bins: list[int]) -> tuple[Value, ...]:
    callable_bp = max(length - ns, 0)
    return (
        name,
        length,
        ns,
        *bins,
        *(c / callable_bp if callable_bp else 0.0 for c in _non_n_bands(ns, bins)),
    )


def _read_bins(path: Path) -> list[_Raw]:
    """The depth counts of a TSV bins report; reports from before the N column
    have the same raw counts."""
    lines = [
        line.split("\t")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    with_ns = bool(lines) and _N_COLUMN in lines[0]
    raw: list[_Raw] = []
    for row in lines[1:]:
        values = [int(v) for v in row[1 : 7 if with_ns else 6]]
        ns = values.pop(1) if with_ns else None
        raw.append((row[0], values[0], ns, values[1:5]))
    return raw


def bins_report_current(path: Path, input_path: str) -> bool:
    """A readable TSV bins report newer than the input."""
    from .render import is_fresh_output

    try:
        return is_fresh_output(str(path), input_path) and bool(_read_bins(path))
    except (OSError, ValueError, IndexError):
        return False


def _fmt_len(bases: int) -> str:
    if bases >= 1_000_000:
        return f"{bases / 1_000_000:,.0f} M"
    return f"{bases / 1_000:,.0f} K" if bases >= 1_000 else str(bases)


def render_bins(rows: list[tuple[Value, ...]], region: str | None = None) -> str:
    """The v4 "Bin Coverage by Sequence Name" table: the chromosomes and mtDNA,
    their Total, and the other contigs pooled, as percentages of non-N bases."""
    shown: list[tuple[str, list[int]]] = []
    pools = {"Total": [0] * 6, "Other": [0] * 6}  # length, Ns, four bands
    for row in rows:
        name = str(row[0])
        length, ns, *bins = (int(str(v)) for v in row[1:7])
        values = [length, ns, *_non_n_bands(ns, bins)]
        primary = (
            region is not None or is_primary_contig(name) or contig_key(name) == "M"
        )
        if primary:
            shown.append((name, values))
        pool = pools["Total" if primary else "Other"]
        pool[:] = [a + b for a, b in zip(pool, values, strict=True)]
    if not region:
        shown += [(name, pool) for name, pool in pools.items() if pool[0]]

    def pct(count: int, of: int) -> str:
        return f"{count / of * 100:.2f} %" if of else "-"

    lines = [
        "Bin Coverage by Sequence Name (% of non-N bases)",
        f"{'Seq Name':<10}{'Model Len':>10}{'N Len':>9}{'1 to 3':>10}"
        f"{'4 to 7':>10}{'8 or More':>11}{'1 or More':>11}",
    ]
    for name, (length, ns, _zero, low, mid, high) in shown:
        of = max(length - ns, 0)
        lines.append(
            f"{name:<10}{_fmt_len(length):>10}{_fmt_len(ns):>9}{pct(low, of):>10}"
            f"{pct(mid, of):>10}{pct(high, of):>11}{pct(low + mid + high, of):>11}"
        )
    return "\n".join(lines) + "\n"


def _show_bins(rows: list[tuple[Value, ...]], region: str | None, display: str) -> None:
    if display == "table":
        human_output(render_bins(rows, region), end="")
        return
    text = tsv_lines(BINS_TABLE, rows)
    if display == "csv":
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows(
            line.split("\t") for line in text.splitlines()
        )
        text = stream.getvalue()
    human_output(text, end="")


def run_depth_bins(
    input_path: str,
    reference: str | None,
    lengths: Mapping[str, int],
    outdir: str,
    region: str | None = None,
    table_format: str = "tsv",
    metadata: Mapping[str, str] | None = None,
    reuse: bool = False,
    n_lengths: Mapping[str, int] | None = None,
    display: str = "tsv",
) -> Path:
    """One samtools depth pass, default filters, both overlapping mates counted.
    The `n_lengths` N bases (by `contig_key`) of whole contigs are left out.

    With ``reuse``, a whole-file TSV report written after the input last changed
    is shown again instead, with the current N counts (``--with-bins``: the
    coverage table it follows is reused the same way). ``--bins`` alone always
    rescans. ``display`` prints the rows as "tsv", "csv" or the v4 "table".
    """
    from .render import region_file_tag

    ns = n_lengths or {}

    def ns_of(name: str) -> int:
        return ns.get(contig_key(name), 0)

    Path(outdir).mkdir(parents=True, exist_ok=True)
    stem = Path(input_path).name + region_file_tag(region) + "_coverage_bins"
    output = Path(table_path(Path(outdir, stem), table_format))
    if (
        reuse
        and not region
        and table_format == "tsv"
        and bins_report_current(output, input_path)
    ):
        logging.info("Using existing depth-bin report %s", output)
        saved = _read_bins(output)
        # Depth 0 is what the bands leave of a whole contig: reports from
        # #1203 (the first N-aware bins) saved it less the Ns.
        raw = [(n, length, [length - sum(b[1:]), *b[1:]]) for n, length, _, b in saved]
        rows = [_bins_row(n, length, ns_of(n), bins) for n, length, bins in raw]
        if any(
            (ns, bins[0]) != (ns_of(n), raw_bins[0])
            for (n, _, ns, bins), (_, _, raw_bins) in zip(saved, raw, strict=True)
        ):
            write_table(output, BINS_TABLE, rows, table_format, metadata=metadata)
        _show_bins(rows, region, display)
        return output
    selected = dict(lengths)
    command = ["samtools", "depth", *_cram_reference_opts(input_path, reference)]
    if region:
        stats: list[IdxStat] = [
            {"name": name, "length": length, "mapped": 0, "unmapped": 0}
            for name, length in lengths.items()
        ]
        name, start, end = _resolve_sample_targets(stats, region)[0]
        selected = {name: end - start + 1}
        command += ["-r", f"{name}:{start}-{end}"]
    command.append(input_path)
    logging.info("Calculating exact depth bins (one streaming pass)...")
    with tempfile.TemporaryFile(dir=outdir) as stderr:
        process = popen(command, stdout=subprocess.PIPE, stderr=stderr)
        assert process.stdout is not None
        try:
            counts = count_depth_bins(process.stdout, selected)
            if process.wait():
                stderr.seek(0)
                detail = stderr.read().decode(errors="replace")[-4000:]
                raise WGSExtractError(f"samtools depth failed: {detail}")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    rows = [
        _bins_row(
            name,
            selected[name],
            ns_of(name) if selected[name] == lengths[name] else 0,
            bins,
        )
        for name, bins in counts.items()
    ]
    write_table(output, BINS_TABLE, rows, table_format, metadata=metadata)
    _show_bins(rows, region, display)
    logging.info("Depth-bin report saved to %s", output)
    return output
