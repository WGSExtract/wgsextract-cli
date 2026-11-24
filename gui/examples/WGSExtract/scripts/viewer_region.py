"""Choose an initial 100 bp locus on the first contig for the genome viewer.

Avoids telomeres, centromeres, N-gaps and low-mapping-quality regions while
staying fast on multi-gigabyte indexed BAM/CRAM/VCF files.
"""

from __future__ import annotations

import gzip
import re
import shlex
import subprocess
from pathlib import Path

WINDOW_BP = 100
VCF_WINDOW_BP = 4_000
PROBE_BP = 2000
STEP_BP = 20
MIN_MAPQ = 20
SMALL_CONTIG_BP = 50_000
MAX_PROBE_READS = 500
MAX_STREAM_READS = 200

_CIGAR_RE = re.compile(r"(\d+)([MIDNSHP=X])")
_BUILD_CHR1_LENGTHS = {
    249250621: "hg19",
    248956422: "hg38",
    248387328: "t2t",
}
_CHR1_CENTROMERES = {
    "hg19": (120_000_000, 145_000_000),
    "hg38": (120_000_000, 146_000_000),
    "t2t": (120_000_000, 146_000_000),
}


def detect_build(lengths: dict[str, int]) -> str | None:
    """Identify hg19, hg38 or T2T-CHM13v2.0 from chromosome 1's length alone."""
    chr1 = [length for name, length in lengths.items() if name in ("chr1", "1")]
    return _BUILD_CHR1_LENGTHS.get(chr1[0]) if len(chr1) == 1 else None


def _contig_aliases(name: str) -> set[str]:
    primary = name.removeprefix("chr")
    if primary in ("M", "MT"):
        return {"chrM", "M", "MT", "chrMT"}
    if primary in {str(i) for i in range(1, 23)} | {"X", "Y"}:
        return {primary, f"chr{primary}"}
    return {name}


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if end < start:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_intervals(
    base: tuple[int, int], exclusions: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    cursor, limit = base
    for ex_start, ex_end in _merge_intervals(exclusions):
        if ex_end < cursor:
            continue
        if ex_start > limit:
            break
        if ex_start > cursor:
            result.append((cursor, min(limit, ex_start - 1)))
        cursor = max(cursor, ex_end + 1)
        if cursor > limit:
            break
    if cursor <= limit:
        result.append((cursor, limit))
    return result


def _cytoband_exclusions(
    reflib_root: str | None, build: str | None, contig: str, chrom_len: int
) -> list[tuple[int, int]]:
    if not reflib_root or build not in ("hg19", "hg38"):
        return []
    aliases = _contig_aliases(contig)
    pad = min(2_000_000, chrom_len // 20)
    for subdir in ("downloads", "ref"):
        path = Path(reflib_root, subdir, f"cytobands_{build}.tsv")
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            continue
        exclusions: list[tuple[int, int]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) != 5 or parts[0] not in aliases:
                    continue
                if parts[4] in ("acen", "gvar", "stalk"):
                    start = max(1, int(parts[1]) + 1 - pad)
                    end = min(chrom_len, int(parts[2]) + pad)
                    exclusions.append((start, end))
        except (OSError, ValueError):
            continue
        if exclusions:
            return _merge_intervals(exclusions)
    return []


def safe_intervals(
    contig: str, chrom_len: int, reflib_root: str | None, build: str | None
) -> list[tuple[int, int]]:
    """Non-telomeric, non-centromeric 1-based inclusive intervals on contig."""
    if chrom_len <= SMALL_CONTIG_BP:
        return [(1, chrom_len)]
    telomere = min(5_000_000, max(10_000, chrom_len // 10))
    base = (telomere + 1, chrom_len - telomere)
    exclusions = _cytoband_exclusions(reflib_root, build, contig, chrom_len)
    if not exclusions:
        if contig in ("chr1", "1") and build in _CHR1_CENTROMERES:
            exclusions = [_CHR1_CENTROMERES[build]]
        elif chrom_len >= 10_000_000:
            exclusions = [(int(chrom_len * 0.42), int(chrom_len * 0.58))]
    intervals = [
        (s, e)
        for s, e in _subtract_intervals(base, exclusions)
        if e - s + 1 >= WINDOW_BP
    ]
    return intervals or [(1, chrom_len)]


def _in_safe_intervals(pos: int, intervals: list[tuple[int, int]], margin: int) -> bool:
    return any(s + margin <= pos <= e - margin for s, e in intervals)


def _gene_probe_centers(
    reflib_root: str | None,
    build: str | None,
    contig: str,
    intervals: list[tuple[int, int]],
    chrom_len: int,
) -> list[int]:
    if not build or chrom_len <= SMALL_CONTIG_BP:
        return []
    aliases = _contig_aliases(contig)
    margin = PROBE_BP // 2
    candidates: list[int] = []

    if reflib_root and build in ("hg19", "hg38"):
        for subdir in ("downloads", "ref", "microarray"):
            path = Path(reflib_root, subdir, f"genes_{build}.tsv")
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    next(handle, None)
                    for line in handle:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) < 4 or parts[1] not in aliases:
                            continue
                        mid = (int(parts[2]) + int(parts[3])) // 2
                        if _in_safe_intervals(mid, intervals, margin):
                            candidates.append(mid)
            except (OSError, ValueError):
                continue
            if candidates:
                break

    if not candidates:
        try:
            import wgsextract_cli

            asset = (
                Path(wgsextract_cli.__file__).resolve().parent
                / "assets"
                / "panel"
                / f"genes_{build}.tsv.gz"
            )
            if asset.is_file():
                with gzip.open(asset, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if line.startswith(("#", "chrom\t")):
                            continue
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) < 6 or parts[0] not in aliases:
                            continue
                        if parts[5] != "protein_coding":
                            continue
                        mid = (int(parts[1]) + 1 + int(parts[2])) // 2
                        if _in_safe_intervals(mid, intervals, margin):
                            candidates.append(mid)
        except (ImportError, OSError, ValueError):
            pass

    if not candidates:
        return []
    targets = [int(chrom_len * f) for f in (0.20, 0.30, 0.65, 0.75)]
    picked: list[int] = []
    for target in targets:
        nearest = min(candidates, key=lambda pos: abs(pos - target))
        if all(abs(nearest - prev) > PROBE_BP * 2 for prev in picked):
            picked.append(nearest)
    return picked


def _clamp_window(center: int, size: int, start: int, end: int) -> tuple[int, int]:
    span = min(size, end - start + 1)
    w_start = max(start, min(end - span + 1, center - span // 2))
    return (w_start, w_start + span - 1)


def _containing_interval(
    pos: int, intervals: list[tuple[int, int]]
) -> tuple[int, int] | None:
    for s, e in intervals:
        if s <= pos <= e:
            return (s, e)
    return None


def _build_probes(
    intervals: list[tuple[int, int]], chrom_len: int, preferred: list[int]
) -> list[tuple[int, int]]:
    if chrom_len <= PROBE_BP:
        return [(1, chrom_len)]
    centers = list(preferred) + [
        int(chrom_len * f) for f in (0.25, 0.35, 0.65, 0.75, 0.15, 0.85)
    ]
    probes: list[tuple[int, int]] = []
    for center in centers:
        interval = _containing_interval(center, intervals)
        if not interval:
            continue
        window = _clamp_window(center, PROBE_BP, interval[0], interval[1])
        if all(
            window[1] < existing[0] or window[0] > existing[1] for existing in probes
        ):
            probes.append(window)
        if len(probes) >= 6:
            break
    if not probes:
        s, e = intervals[0]
        probes.append(_clamp_window((s + e) // 2, PROBE_BP, s, e))
    return probes


def _tool_argv(tool: str) -> list[str]:
    """Turn a tool executable path or wrapper command (such as `pixi run ... samtools`) into argv."""
    if Path(tool).is_file():
        return [tool]
    return shlex.split(tool) or [tool]


def _fetch_reference_slice(
    samtools: str, fasta: str, contig: str, start: int, end: int
) -> str:
    result = subprocess.run(
        [*_tool_argv(samtools), "faidx", fasta, f"{contig}:{start}-{end}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return "".join(
        line.strip().upper()
        for line in result.stdout.splitlines()
        if line and not line.startswith(">")
    )


def _accumulate_read(
    pos: int,
    mapq: int,
    cigar: str,
    seq: str,
    p_start: int,
    span: int,
    ref_seq: str,
    total_depth: list[int],
    hq_depth: list[int],
    alt_count: list[int],
) -> None:
    ref_pos = pos
    q_pos = 0
    is_hq = mapq >= MIN_MAPQ
    has_seq = seq != "*"
    ref_len = len(ref_seq)
    for raw_len, op in _CIGAR_RE.findall(cigar):
        length = int(raw_len)
        if op in ("M", "=", "X"):
            for i in range(length):
                idx = ref_pos + i - p_start
                if 0 <= idx < span:
                    total_depth[idx] += 1
                    if is_hq:
                        hq_depth[idx] += 1
                        if has_seq and idx < ref_len and q_pos + i < len(seq):
                            base = seq[q_pos + i].upper()
                            ref_base = ref_seq[idx]
                            if (
                                base in "ACGT"
                                and ref_base in "ACGT"
                                and base != ref_base
                            ):
                                alt_count[idx] += 1
            ref_pos += length
            q_pos += length
        elif op == "I":
            if is_hq:
                idx = ref_pos - p_start
                if 0 <= idx < span:
                    alt_count[idx] += 1
            q_pos += length
        elif op == "D":
            if is_hq:
                idx = ref_pos - p_start
                if 0 <= idx < span:
                    alt_count[idx] += 1
            ref_pos += length
        elif op == "N":
            ref_pos += length
        elif op == "S":
            q_pos += length


def _score_probe(
    input_path: str,
    fasta: str,
    contig: str,
    p_start: int,
    p_end: int,
    samtools: str,
) -> tuple[tuple[int | float, ...], int, int] | None:
    is_cram = input_path.lower().endswith(".cram")
    cmd = [*_tool_argv(samtools), "view", "-F", "0xF04"]
    if is_cram:
        cmd.extend(["-T", fasta])
    cmd.extend([input_path, f"{contig}:{p_start}-{p_end}"])

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if len(lines) >= MAX_PROBE_READS:
                break
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    if not lines:
        return None

    span = p_end - p_start + 1
    ref_seq = _fetch_reference_slice(samtools, fasta, contig, p_start, p_end)
    total_depth = [0] * span
    hq_depth = [0] * span
    alt_count = [0] * span

    for line in lines:
        fields = line.split("\t", 10)
        if len(fields) < 10:
            continue
        try:
            pos = int(fields[3])
            mapq = int(fields[4])
        except ValueError:
            continue
        _accumulate_read(
            pos,
            mapq,
            fields[5],
            fields[9],
            p_start,
            span,
            ref_seq,
            total_depth,
            hq_depth,
            alt_count,
        )

    if span <= WINDOW_BP:
        return ((1, 1, 0), p_start, p_end)

    offsets = list(range(0, span - WINDOW_BP + 1, STEP_BP))
    last_offset = span - WINDOW_BP
    if offsets[-1] != last_offset:
        offsets.append(last_offset)

    best: tuple[tuple[int | float, ...], int, int] | None = None
    for offset in offsets:
        tot_sum = sum(total_depth[offset : offset + WINDOW_BP])
        if tot_sum == 0:
            continue
        hq_slice = hq_depth[offset : offset + WINDOW_BP]
        hq_sum = sum(hq_slice)
        min_hq = min(hq_slice)
        mean_hq = hq_sum / WINDOW_BP
        low_mapq_frac = (tot_sum - hq_sum) / tot_sum
        ref_slice = ref_seq[offset : offset + WINDOW_BP] if len(ref_seq) >= offset + WINDOW_BP else ""
        no_n = "N" not in ref_slice
        variants = 0
        for idx in range(offset, offset + WINDOW_BP):
            depth = hq_depth[idx]
            alt = alt_count[idx]
            if (depth >= 3 and alt >= 2 and alt * 5 >= depth) or (
                depth in (1, 2) and alt >= 1 and min_hq < 3
            ):
                variants += 1
        well_mapped = no_n and (min_hq >= 4 or mean_hq >= 5.0) and low_mapq_frac <= 0.25
        score: tuple[int | float, ...] = (
            int(no_n),
            int(well_mapped),
            int(variants > 0),
            int(min_hq >= 4),
            min(variants, 3),
            -round(low_mapq_frac, 2),
            min(min_hq, 30),
            min(int(mean_hq), 40),
            min(tot_sum, 4000),
        )
        w_start = p_start + offset
        w_end = w_start + WINDOW_BP - 1
        if best is None or score > best[0]:
            best = (score, w_start, w_end)
    return best


def _first_mapped_position(
    input_path: str,
    fasta: str,
    contig: str,
    start: int,
    end: int,
    samtools: str,
) -> int | None:
    is_cram = input_path.lower().endswith(".cram")
    cmd = [*_tool_argv(samtools), "view", "-F", "0xF04"]
    if is_cram:
        cmd.extend(["-T", fasta])
    cmd.extend([input_path, f"{contig}:{start}-{end}"])
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    first_any: int | None = None
    first_hq: int | None = None
    try:
        assert proc.stdout is not None
        for idx, line in enumerate(proc.stdout):
            if idx >= MAX_STREAM_READS:
                break
            fields = line.split("\t", 6)
            if len(fields) < 5:
                continue
            try:
                pos = int(fields[3])
                mapq = int(fields[4])
            except ValueError:
                continue
            if first_any is None:
                first_any = pos
            if mapq >= MIN_MAPQ:
                first_hq = pos
                break
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return first_hq if first_hq is not None else first_any


def _first_variant_position(
    input_path: str, contig: str, start: int, end: int, bcftools: str
) -> int | None:
    proc = subprocess.Popen(
        [*_tool_argv(bcftools), "view", "-H", input_path, f"{contig}:{start}-{end}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            fields = line.split("\t", 3)
            if len(fields) >= 2:
                try:
                    return int(fields[1])
                except ValueError:
                    continue
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return None


def _first_variant_record(input_path: str, bcftools: str) -> tuple[str, int] | None:
    """Stream the very first variant record (chrom, pos) from a VCF."""
    proc = subprocess.Popen(
        [*_tool_argv(bcftools), "view", "-H", input_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            fields = line.split("\t", 3)
            if len(fields) >= 2:
                try:
                    return fields[0], int(fields[1])
                except ValueError:
                    continue
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return None


def _vcf_present_contigs(input_path: str, bcftools: str) -> list[str]:
    """Return contigs that actually have records in an indexed VCF (instant)."""
    indexed = any(Path(input_path + ext).is_file() for ext in (".tbi", ".csi"))
    if indexed:
        res = subprocess.run(
            [*_tool_argv(bcftools), "index", "-s", input_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            chroms = [
                parts[0]
                for line in res.stdout.splitlines()
                if (parts := line.split("\t")) and parts[0]
            ]
            if chroms:
                return chroms
        # Older .tbi indexes lack count metadata for bcftools index -s; use tabix -l.
        try:
            from wgsextract_cli.core.dependencies import get_tool_path

            tabix = get_tool_path("tabix")
        except ImportError:
            tabix = "tabix"
        if tabix:
            lres = subprocess.run(
                [*_tool_argv(tabix), "-l", input_path],
                capture_output=True,
                text=True,
                check=False,
            )
            if lres.returncode == 0:
                chroms = [
                    line.strip() for line in lres.stdout.splitlines() if line.strip()
                ]
                if chroms:
                    return chroms
    return []


def _is_primary_contig(name: str) -> bool:
    clean = name.removeprefix("chr").removeprefix("CHR").upper()
    return clean in {str(i) for i in range(1, 23)} | {"X", "Y", "M", "MT"}


def _choose_vcf_locus(
    input_path: str,
    lengths: dict[str, int],
    bcftools: str,
    reflib_root: str | None,
) -> str:
    """Select a readable 4 kb window centered on an actual VCF variant entry."""
    build = detect_build(lengths)
    present = [c for c in _vcf_present_contigs(input_path, bcftools) if c in lengths]
    ordered = (
        [c for c in present if _is_primary_contig(c)]
        + [c for c in present if not _is_primary_contig(c)]
    )
    if not ordered:
        first = _first_variant_record(input_path, bcftools)
        if first and first[0] in lengths:
            chrom, pos = first
            chrom_len = lengths[chrom]
            win = min(VCF_WINDOW_BP, chrom_len)
            w_start, w_end = _clamp_window(pos, win, 1, chrom_len)
            return f"{chrom}:{w_start}-{w_end}"
        ordered = list(lengths.keys())[:1]

    for contig in ordered[:3]:
        chrom_len = lengths[contig]
        win = min(VCF_WINDOW_BP, chrom_len)
        intervals = safe_intervals(contig, chrom_len, reflib_root, build)
        for search_intervals in (intervals, [(1, chrom_len)]):
            for s, e in search_intervals:
                pos = _first_variant_position(input_path, contig, s, e, bcftools)
                if pos is not None:
                    w_start, w_end = _clamp_window(pos, win, 1, chrom_len)
                    return f"{contig}:{w_start}-{w_end}"

    first = _first_variant_record(input_path, bcftools)
    if first and first[0] in lengths:
        chrom, pos = first
        chrom_len = lengths[chrom]
        win = min(VCF_WINDOW_BP, chrom_len)
        w_start, w_end = _clamp_window(pos, win, 1, chrom_len)
        return f"{chrom}:{w_start}-{w_end}"

    contig = ordered[0]
    chrom_len = lengths[contig]
    intervals = safe_intervals(contig, chrom_len, reflib_root, build)
    s, e = intervals[0]
    w_start, w_end = _clamp_window((s + e) // 2, min(VCF_WINDOW_BP, chrom_len), s, e)
    return f"{contig}:{w_start}-{w_end}"


def choose_initial_locus(
    input_path: str,
    fasta: str,
    lengths: dict[str, int],
    tool: str,
    is_alignment: bool,
    reflib_root: str | None = None,
) -> str:
    """Select an initial locus on a populated contig (100 bp for BAM/CRAM, 4 kb for VCF)."""
    if not is_alignment:
        return _choose_vcf_locus(input_path, lengths, tool, reflib_root)

    contig = next(iter(lengths))
    chrom_len = lengths[contig]
    if chrom_len <= WINDOW_BP:
        return f"{contig}:1-{chrom_len}"

    build = detect_build(lengths)
    intervals = safe_intervals(contig, chrom_len, reflib_root, build)

    preferred = _gene_probe_centers(reflib_root, build, contig, intervals, chrom_len)
    probes = _build_probes(intervals, chrom_len, preferred)
    best: tuple[tuple[int | float, ...], int, int] | None = None
    well_mapped_seen = 0
    for p_start, p_end in probes:
        candidate = _score_probe(input_path, fasta, contig, p_start, p_end, tool)
        if candidate is None:
            continue
        if best is None or candidate[0] > best[0]:
            best = candidate
        no_n, well_mapped, has_variant = best[0][0], best[0][1], best[0][2]
        if no_n and well_mapped and has_variant:
            return f"{contig}:{best[1]}-{best[2]}"
        if no_n and well_mapped:
            well_mapped_seen += 1
            if well_mapped_seen >= 3:
                return f"{contig}:{best[1]}-{best[2]}"
    if best is not None:
        return f"{contig}:{best[1]}-{best[2]}"

    # Sparse/subset/extracted BAM: stream the first mapped cluster in safe intervals, then anywhere on contig.
    for search_intervals in (intervals, [(1, chrom_len)]):
        for s, e in search_intervals:
            pos = _first_mapped_position(input_path, fasta, contig, s, e, tool)
            if pos is None:
                continue
            p_start, p_end = _clamp_window(pos + WINDOW_BP // 2, PROBE_BP, s, e)
            candidate = _score_probe(input_path, fasta, contig, p_start, p_end, tool)
            if candidate is not None:
                return f"{contig}:{candidate[1]}-{candidate[2]}"
            w_start, w_end = _clamp_window(pos, WINDOW_BP, s, e)
            return f"{contig}:{w_start}-{w_end}"

    s, e = intervals[0]
    w_start, w_end = _clamp_window((s + e) // 2, WINDOW_BP, s, e)
    return f"{contig}:{w_start}-{w_end}"
