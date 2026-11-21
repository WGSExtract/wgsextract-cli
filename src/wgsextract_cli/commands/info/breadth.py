"""Breadth of coverage as a share of each contig's non-N bases."""

from collections.abc import Mapping

from .metrics import contig_key, is_primary_contig


def breadth_map(
    covered: Mapping[str, tuple[int, int]],
    with_total: bool,
    approx: bool = False,
    excluded: Mapping[str, int] | None = None,
) -> dict[str, str]:
    """Format per-contig (covered, total) base counts as breadth percentages.

    `excluded` gives the N bases (by `contig_key`) of contigs whose `total`
    spans the whole contig; they can't be sequenced, so they leave the
    denominator, as they do for mean depth."""
    prefix = "~" if approx else ""
    excluded = excluded or {}
    counts: dict[str, tuple[int, int]] = {}
    for name, (cov, total) in covered.items():
        callable_bp = total - excluded.get(contig_key(name), 0)
        if callable_bp <= 0:
            callable_bp = total
        # Reads placed in N runs could push coverage past the callable bases.
        counts[name] = (min(cov, callable_bp), callable_bp)
    result = {
        contig_key(name): f"{prefix}{cov / total * 100:.0f} %"
        for name, (cov, total) in counts.items()
        if total > 0
    }
    if with_total:
        primary = [v for k, v in counts.items() if is_primary_contig(k)]
        total_bp = sum(total for _cov, total in primary)
        if total_bp:
            covered_bp = sum(cov for cov, _total in primary)
            result["TOTAL_EST"] = f"{prefix}{covered_bp / total_bp * 100:.0f} %"
    return result


def whole_contig_ns(
    n_lengths: Mapping[str, int],
    contig_lengths: Mapping[str, int],
    spans: Mapping[str, int],
) -> dict[str, int]:
    """The N counts of the contigs whose measured span is the whole contig; a
    partial span's share of the Ns is unknown."""
    return {
        contig_key(name): n_lengths[contig_key(name)]
        for name, span in spans.items()
        if contig_key(name) in n_lengths and contig_lengths.get(name) == span
    }


def parse_samtools_coverage(path: str) -> dict[str, tuple[int, int]]:
    covered: dict[str, tuple[int, int]] = {}
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                continue
            start, end, covbases = int(p[1]), int(p[2]), int(p[4])
            covered[p[0]] = (covbases, end - start + 1)
    return covered


def parse_legacy_bincvg(path: str) -> dict[str, tuple[int, int]]:
    """Parse the older ``samtools depth | awk`` ``_bincvg.csv`` summary."""
    covered: dict[str, tuple[int, int]] = {}
    with open(path) as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) > 7:
                covered[p[0]] = (int(p[2]), int(p[7]))
    return covered


def exact_breadth(
    covered: Mapping[str, tuple[int, int]],
    with_total: bool,
    n_lengths: Mapping[str, int] | None,
    contig_lengths: Mapping[str, int] | None,
) -> dict[str, str]:
    spans = {name: total for name, (_cov, total) in covered.items()}
    ns = whole_contig_ns(n_lengths or {}, contig_lengths or {}, spans)
    return breadth_map(covered, with_total, excluded=ns)
