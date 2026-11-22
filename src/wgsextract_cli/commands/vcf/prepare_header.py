"""What ``vcf prepare`` learns from one read of a VCF, and the header it
writes from that: every contig the records use declared (named and ordered as
``--ref`` when given, with its lengths), and every INFO, FORMAT and FILTER ID
the records use declared."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.core.utils import WGSExtractError, popen
from wgsextract_cli.core.variant_files import (
    chromosome_rename_mapping,
    header_attributes,
)

DECLARED = "Used by the input VCF without a header line; declared by vcf prepare"
_CHECK_EVERY = 100_000
_EXAMPLES = 5
# htslib reports each undeclared ID once, then treats it as a String.
_UNDECLARED = re.compile(
    r"\b(INFO|FORMAT|FILTER) '([^']*)'(?: at \S+)? is not defined in the header"
)
# A FILTER value htslib can't even declare, such as FTDNA's "DP<5".
_UNDECLARABLE_FILTER = re.compile(r"Could not add dummy header for FILTER '([^']*)'")
_TAG_ID = re.compile(r"[A-Za-z_][0-9A-Za-z_.]*")
_FILTER_ID = re.compile(r"[^\s;,=<>\"]+")
_CONTIG = "##contig=<"


@dataclass
class Scan:
    """The records of a VCF, read once in file order."""

    records: int = 0
    # Each contig's highest position, in the order the records first use it.
    max_pos: dict[str, int] = field(default_factory=dict)
    # Records that come before the one above them in coordinate order.
    out_of_order: int = 0
    multiallelic: int = 0
    # Records that splitting the multiallelic ones adds.
    extra_alleles: int = 0
    undeclared: dict[str, dict[str, None]] = field(
        default_factory=lambda: {"FILTER": {}, "INFO": {}, "FORMAT": {}}
    )


def scan_records(vcf: str, work: Path) -> Scan:
    """Read every record with bcftools, which also names the INFO, FORMAT and
    FILTER IDs the header doesn't declare."""
    scan = Scan()
    err_path = work / "scan.err"
    with err_path.open("wb") as err:
        proc = popen(
            ["bcftools", "view", "--no-version", "-H", vcf],
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
        )
        try:
            assert proc.stdout is not None
            current, last = "", 0
            with proc.stdout as stream:
                for line in stream:
                    chrom, pos_text, _, _, alt, _ = line.split("\t", 5)
                    pos = int(pos_text)
                    scan.records += 1
                    if not scan.records % _CHECK_EVERY:
                        checkpoint()
                    if chrom != current:
                        if chrom in scan.max_pos:
                            scan.out_of_order += 1
                        current, last = chrom, 0
                    elif pos < last:
                        scan.out_of_order += 1
                    last = pos
                    if pos > scan.max_pos.get(chrom, 0):
                        scan.max_pos[chrom] = pos
                    alleles = alt.count(",") + 1
                    if alleles > 1:
                        scan.multiallelic += 1
                        scan.extra_alleles += alleles - 1
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        code = proc.wait()
    stderr = err_path.read_text(encoding="utf-8", errors="replace")
    for kind, name in _UNDECLARED.findall(stderr):
        scan.undeclared[kind][name] = None
    for name in _UNDECLARABLE_FILTER.findall(stderr):
        scan.undeclared["FILTER"][name] = None
    _refuse_invalid_ids(scan, vcf)
    if code:
        tail = "\n".join(stderr.strip().splitlines()[-5:])
        raise WGSExtractError(f"bcftools could not read {vcf} (exit {code}): {tail}")
    return scan


def _refuse_invalid_ids(scan: Scan, vcf: str) -> None:
    filters = [
        name for name in scan.undeclared["FILTER"] if not _FILTER_ID.fullmatch(name)
    ]
    if filters:
        shown = ", ".join(repr(name) for name in filters[:_EXAMPLES])
        raise WGSExtractError(
            f"{len(filters)} FILTER value(s) in {vcf}, such as {shown}, are not "
            "FILTER IDs, as legacy Family Tree DNA VCFs write them. Run "
            "`wgsextract repair ftdna-vcf` on it first, then `wgsextract vcf "
            "prepare` on the repaired VCF."
        )
    tags = [
        f"{kind}/{name}"
        for kind in ("INFO", "FORMAT")
        for name in scan.undeclared[kind]
        if not _TAG_ID.fullmatch(name)
    ]
    if tags:
        raise WGSExtractError(
            f"{vcf} uses INFO/FORMAT keys that are not valid IDs and can't be "
            f"declared: {', '.join(tags[:_EXAMPLES])}."
        )


@dataclass
class _Contig:
    name: str
    final: str
    line: str | None
    length: int | None


@dataclass
class HeaderPlan:
    """The header the prepared VCF gets, and what differs from the input's.
    Contig names are the prepared VCF's (after renaming)."""

    text: str
    changed: bool
    renames: list[tuple[str, str]]
    order: list[str]
    declared_contigs: list[str]
    lengths_added: list[str]
    reordered: bool
    unknown_length: list[str]
    off_reference: list[str]
    declared_tags: list[str]

    def final_name(self, name: str) -> str:
        return dict(self.renames).get(name, name)


def plan_header(
    header: str, scan: Scan, ref_lengths: Mapping[str, int] | None
) -> HeaderPlan:
    """The prepared VCF's header. The contig lines keep the input's names so
    that ``bcftools annotate --rename-chrs`` renames records and header alike."""
    contigs: dict[str, _Contig] = {}
    kept: list[str] = []
    contig_at: int | None = None
    for line in header.rstrip("\n").split("\n"):
        if line.startswith(_CONTIG) and line.endswith(">"):
            attributes = header_attributes(line[len(_CONTIG) : -1])
            name = attributes.get("ID")
            if name:
                contig_at = len(kept) if contig_at is None else contig_at
                size = attributes.get("length", "")
                contigs.setdefault(
                    name,
                    _Contig(name, name, line, int(size) if size.isdigit() else None),
                )
                continue
        kept.append(line)
    declared = list(contigs)
    for name in scan.max_pos:
        contigs.setdefault(name, _Contig(name, name, None, None))

    renames: list[tuple[str, str]] = []
    order = list(contigs.values())
    if ref_lengths is not None:
        renames = chromosome_rename_mapping(list(contigs), list(ref_lengths))
        for source, target in renames:
            contigs[source].final = target
        _refuse_name_collisions(order)
        rank = {name: index for index, name in enumerate(ref_lengths)}
        order.sort(key=lambda c: (0, rank[c.final]) if c.final in rank else (1, 0))

    lines: list[str] = []
    lengths_added: list[str] = []
    unknown: list[str] = []
    off_reference: list[str] = []
    for contig in order:
        reference_length = ref_lengths.get(contig.final) if ref_lengths else None
        if ref_lengths is not None and reference_length is None:
            off_reference.append(contig.final)
        length = contig.length or reference_length
        if length is None:
            unknown.append(contig.final)
        if contig.line is not None and (contig.length or not reference_length):
            lines.append(contig.line)
            continue
        if contig.line is not None:
            lengths_added.append(contig.final)
            given = f"ID={contig.name}"
            lines.append(
                contig.line.replace(given, f"{given},length={reference_length}", 1)
            )
        else:
            lines.append(
                f"{_CONTIG}ID={contig.name}"
                + (f",length={length}" if length else "")
                + ">"
            )

    chrom = next(i for i, line in enumerate(kept) if line.startswith("#CHROM"))
    at = chrom if contig_at is None else contig_at
    tags = _tag_lines(scan)
    text_lines = [*kept[:at], *lines, *kept[at:chrom], *tags, *kept[chrom:]]
    text = "\n".join(text_lines) + "\n"
    return HeaderPlan(
        text=text,
        changed=text != header.rstrip("\n") + "\n",
        renames=renames,
        order=[contig.final for contig in order],
        declared_contigs=[c.final for c in order if c.line is None],
        lengths_added=lengths_added,
        reordered=[c.name for c in order if c.line is not None] != declared,
        unknown_length=unknown,
        off_reference=off_reference,
        declared_tags=[
            f"{kind}/{name}"
            for kind, names in scan.undeclared.items()
            for name in names
        ],
    )


def _tag_lines(scan: Scan) -> list[str]:
    lines = [
        f'##FILTER=<ID={name},Description="{DECLARED}">'
        for name in scan.undeclared["FILTER"]
    ]
    for kind in ("INFO", "FORMAT"):
        for name in scan.undeclared[kind]:
            # Number=. and Type=String keep the values exactly as written;
            # GT is the genotype whichever way it is declared.
            number = "1" if kind == "FORMAT" and name == "GT" else "."
            lines.append(
                f"##{kind}=<ID={name},Number={number},Type=String,"
                f'Description="{DECLARED}">'
            )
    return lines


def _refuse_name_collisions(contigs: list[_Contig]) -> None:
    by_final: dict[str, list[str]] = {}
    for contig in contigs:
        by_final.setdefault(contig.final, []).append(contig.name)
    clashes = [names for names in by_final.values() if len(names) > 1]
    if clashes:
        shown = "; ".join(" and ".join(names) for names in clashes[:_EXAMPLES])
        raise WGSExtractError(
            f"The VCF names one --ref contig in more than one way ({shown}); "
            "merge or remove the duplicates first."
        )


def refuse_records_beyond_reference(
    scan: Scan, plan: HeaderPlan, ref_lengths: Mapping[str, int]
) -> None:
    """A record past the end of its --ref contig means the VCF is for another
    build (or the wrong --ref was given): refuse before writing anything."""
    beyond = [
        f"{plan.final_name(name)}:{pos:,} > {length:,}"
        for name, pos in scan.max_pos.items()
        if (length := ref_lengths.get(plan.final_name(name))) and pos > length
    ]
    if beyond:
        raise WGSExtractError(
            "Records lie past the end of their --ref contig ("
            + ", ".join(beyond[:_EXAMPLES])
            + "): the VCF was called against another build than --ref."
        )


def sort_reason(scan: Scan, plan: HeaderPlan) -> str | None:
    """Why the records must be sorted: ``positions`` when they are out of
    coordinate order, ``contig_order`` when their contigs aren't in the
    header's order; None when they are sorted."""
    if scan.out_of_order:
        return "positions"
    rank = {name: index for index, name in enumerate(plan.order)}
    seen = [rank[plan.final_name(name)] for name in scan.max_pos]
    return None if seen == sorted(seen) else "contig_order"
