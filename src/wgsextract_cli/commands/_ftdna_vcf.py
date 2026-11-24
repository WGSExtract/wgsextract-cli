"""FILTER repair policy for legacy FTDNA VCFs; docs/ftdna-vcf-repair.md
describes the mapping and how it differs from WGS Extract v4."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TextIO

from wgsextract_cli.api._runtime import checkpoint
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.core.variant_files import header_attributes

_CHECK_EVERY = 10_000
_SPOOL_IN_MEMORY = 64 << 20

_VALID_ID = re.compile(r"[A-Za-z0-9_.\-]+")
_LEGACY = re.compile(r"(DP|QUAL|GTL)\s*=\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)")
_STRUCTURED = re.compile(r"##(FILTER|INFO)=<(.*)>\s*$")

UNKNOWN = "FTDNA_UNKNOWN"
EVIDENCE = "FTDNA_FILTER"
# Each category's meaning; _category() picks one from a legacy expression.
CATEGORIES: dict[str, str] = {
    "DP1": "FTDNA filter DP=1: a single read covers the site",
    "DPM": "FTDNA filter DP=n with n > 750: more than 750 reads cover the site",
    "DPF": "FTDNA filter DP=n with 2 <= n <= 750: failed an FTDNA read-depth "
    "filter whose threshold FTDNA does not document",
    "Q40": "FTDNA filter QUAL=q with q < 40: site quality below 40",
    "QF": "FTDNA filter QUAL=q with q >= 40: failed an FTDNA quality filter "
    "whose threshold FTDNA does not document",
    "GTL": "FTDNA filter GTL=x: failed FTDNA's undocumented GTL filter",
    UNKNOWN: "FTDNA filter expression repair ftdna-vcf cannot interpret: "
    "the call failed a vendor filter of unknown meaning",
}
UNDECLARED = "Used by the input VCF without a header declaration; meaning unknown"
EVIDENCE_DESCRIPTION = (
    "Original FTDNA FILTER expressions replaced by repair ftdna-vcf, percent-encoded"
)
# Y-haplogroup INFO keys of FTDNA tooling and WGS Extract v4's yBrowse
# annotation; declared only when records use them undeclared.
HAPLOGROUP_INFO = {
    "HG": "Haplogroup annotation kept verbatim from the input, which did not "
    "declare it",
    "ISOGG": "ISOGG haplogroup annotation kept verbatim from the input, which "
    "did not declare it",
}


def _category(key: str, value: str) -> str | None:
    number = float(value)
    if key == "DP":
        if not number.is_integer() or number < 1:
            return None
        return "DP1" if number == 1 else "DPM" if number > 750 else "DPF"
    if key == "QUAL":
        return "Q40" if number < 40 else "QF"
    return "GTL"


def _encode(expression: str) -> str:
    """Percent-encode the characters a VCF INFO value can't hold."""
    return "".join(
        f"%{ord(char):02X}" if char in "%:;=, \t\r\n" else char for char in expression
    )


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else value


@dataclass
class _Header:
    lines: list[str] = field(default_factory=list)
    filters: dict[str, str | None] = field(default_factory=dict)
    infos: dict[str, str | None] = field(default_factory=dict)

    def add(self, line: str) -> None:
        self.lines.append(line)
        match = _STRUCTURED.match(line)
        if match is None:
            return
        attributes = header_attributes(match[2])
        if "ID" in attributes:
            table = self.filters if match[1] == "FILTER" else self.infos
            description = attributes.get("Description")
            table[attributes["ID"]] = (
                _unquote(description) if description is not None else None
            )


def _entries(value: str) -> str | list[str]:
    """A failed FILTER value's entries to rewrite, or the value to emit when
    nothing failed. PASS beside a failure and ``.``/empty entries are
    dropped; only PASS entries give ``PASS`` and only ``.`` entries ``.``.
    A value with no entries at all (empty, ``;``) is one unknown entry."""
    tokens = [token.strip() for token in value.split(";")]
    failures = [token for token in tokens if token not in ("", ".", "PASS")]
    if failures:
        return failures
    if "PASS" in tokens:
        return "PASS"
    return "." if "." in tokens else [value]


@dataclass
class _Plan:
    """Which IDs and INFO keys the records need, and the names they get.

    Built from one pass over the records without keeping per-record or
    per-value state, so memory stays flat on whole-genome inputs."""

    header: _Header
    info_used: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    undeclared: dict[str, None] = field(default_factory=dict)
    unknown_examples: dict[str, None] = field(default_factory=dict)
    unknown_records: int = 0
    names: dict[str, str] = field(default_factory=dict)
    new_filters: dict[str, str] = field(default_factory=dict)
    new_infos: dict[str, str] = field(default_factory=dict)

    def classify(self, token: str) -> str | None:
        """``token`` itself when it is an ID to keep, else its category;
        ``None`` for an undeclared, uninterpretable expression."""
        if token in self.header.filters or _VALID_ID.fullmatch(token):
            return token
        legacy = _LEGACY.fullmatch(token)
        category = _category(legacy[1], legacy[2]) if legacy else None
        return category

    def observe(self, value: str) -> None:
        if value in ("PASS", "."):
            return
        entries = _entries(value)
        if isinstance(entries, str):
            return
        unknown = False
        for token in entries:
            kind = self.classify(token)
            if kind == token:
                if token not in self.header.filters:
                    self.undeclared[token] = None
            elif kind is None:
                unknown = True
                self.categories.add(UNKNOWN)
                if len(self.unknown_examples) < 5:
                    self.unknown_examples[token] = None
            else:
                self.categories.add(kind)
        self.unknown_records += unknown

    def finish(self) -> None:
        # Undeclared IDs the records use are claimed first, so a mapped
        # category never takes a name the input gives another meaning.
        for token in self.undeclared:
            self.new_filters[token] = UNDECLARED
        for category, description in CATEGORIES.items():
            if category in self.categories:
                self.name(category, description)
        if self.categories:
            self.name(EVIDENCE, EVIDENCE_DESCRIPTION, info=True)
        for key, description in HAPLOGROUP_INFO.items():
            if key in self.info_used and key not in self.header.infos:
                self.new_infos[key] = description

    def name(self, base: str, description: str, *, info: bool = False) -> str:
        """The ID to emit for ``base``: renamed when the input already uses
        it for something else, and declared when the input doesn't."""
        declared = self.header.infos if info else self.header.filters
        taken = set(declared) | (self.info_used if info else set(self.undeclared))
        candidate, suffix = base, 0
        while candidate in taken and declared.get(candidate) != description:
            suffix += 1
            candidate = f"{base}_WGSE" + (str(suffix) if suffix > 1 else "")
        if candidate != base:
            warn(
                "ftdna_vcf_id_conflict",
                f"The input already uses {'INFO' if info else 'FILTER'} {base} "
                f"with another meaning; repaired records use {candidate}.",
                id=base,
                renamed=candidate,
            )
        if candidate not in declared:
            (self.new_infos if info else self.new_filters)[candidate] = description
        self.names[base] = candidate
        return candidate

    def rewrite(self, value: str) -> tuple[str, list[str]]:
        if value in ("PASS", "."):
            return value, []
        entries = _entries(value)
        if isinstance(entries, str):
            return entries, []
        ids: list[str] = []
        evidence: list[str] = []
        for token in entries:
            kind = self.classify(token)
            if kind == token:
                ids.append(token)
                continue
            ids.append(self.names[kind or UNKNOWN])
            if token:
                evidence.append(_encode(token))
        return ";".join(dict.fromkeys(ids)), evidence


def _fields(line: str) -> list[str]:
    return line.rstrip("\r\n").split("\t")


def _info_keys(info: str) -> Iterator[str]:
    if info != ".":
        for entry in info.split(";"):
            yield entry.split("=", 1)[0]


def _add_info(info: str, key: str, values: list[str]) -> str:
    entries = [] if info == "." else info.split(";")
    for index, entry in enumerate(entries):
        name, _, existing = entry.partition("=")
        if name == key:
            entries[index] = (
                f"{key}={','.join([existing, *values] if existing else values)}"
            )
            return ";".join(entries)
    return ";".join([*entries, f"{key}={','.join(values)}"])


def repair_vcf_stream(
    input_stream: TextIO, output_stream: TextIO, *, spool_dir: str | None = None
) -> tuple[int, int]:
    """Copy VCF text, turning FILTER values VCF can't hold into declared IDs;
    returns the record count and how many records were changed.

    Nothing is written until the whole input has been read and checked, so
    a malformed input leaves the output empty rather than half repaired.
    Records past the in-memory limit spool to ``spool_dir``, else the run's
    configured temporary folder."""
    header = _Header()
    plan = _Plan(header)
    records = 0
    with tempfile.SpooledTemporaryFile(
        max_size=_SPOOL_IN_MEMORY,
        mode="w+",
        encoding="utf-8",
        newline="\n",
        dir=spool_dir or configured_temp_directory(),
    ) as spool:
        columns = False
        for number, line in enumerate(input_stream, 1):
            if not line.strip():
                continue
            if not line.endswith("\n"):
                line += "\n"
            if not columns:
                if not line.startswith("#"):
                    raise WGSExtractError(
                        f"Line {number} is a record before the #CHROM header "
                        "line; the input is not a VCF."
                    )
                header.add(line)
                columns = line.startswith("#CHROM")
                continue
            fields = _fields(line)
            if len(fields) < 8:
                raise WGSExtractError(
                    f"Line {number} has {len(fields)} tab-separated columns; "
                    "a VCF record needs at least 8."
                )
            records += 1
            if not records % _CHECK_EVERY:
                checkpoint()
            plan.observe(fields[6])
            plan.info_used.update(_info_keys(fields[7]))
            spool.write(line)
        if not header.lines:
            return 0, 0  # Empty input: nothing to repair.
        if not columns:
            raise WGSExtractError("The input has no #CHROM header line.")

        plan.finish()
        _report(plan)

        output_stream.writelines(header.lines[:-1])
        for name, description in plan.new_filters.items():
            output_stream.write(f'##FILTER=<ID={name},Description="{description}">\n')
        for name, description in plan.new_infos.items():
            output_stream.write(
                f'##INFO=<ID={name},Number=.,Type=String,Description="{description}">\n'
            )
        output_stream.write(header.lines[-1])

        spool.seek(0)
        repaired = 0
        for count, line in enumerate(spool, 1):
            if not count % _CHECK_EVERY:
                checkpoint()
            fields = _fields(line)
            value, evidence = plan.rewrite(fields[6])
            if value == fields[6]:
                output_stream.write(line)
                continue
            fields[6] = value
            if evidence:
                fields[7] = _add_info(fields[7], plan.names[EVIDENCE], evidence)
            output_stream.write("\t".join(fields) + "\n")
            repaired += 1
    current_result().add_metrics(unknown_filter_records=plan.unknown_records)
    return records, repaired


def _report(plan: _Plan) -> None:
    if plan.unknown_records:
        shown = ", ".join(repr(token) for token in plan.unknown_examples)
        warn(
            "ftdna_vcf_unknown_filter",
            f"{plan.unknown_records} record(s) have FILTER expressions the FTDNA "
            f"repair policy can't interpret (such as {shown}); they became "
            f"{plan.names[UNKNOWN]}, and the originals are kept in "
            f"INFO/{plan.names[EVIDENCE]}.",
            records=plan.unknown_records,
        )
    if plan.undeclared:
        warn(
            "ftdna_vcf_undeclared_filter",
            f"FILTER ID(s) {', '.join(plan.undeclared)} lacked header "
            "declarations; they are declared with an unknown meaning.",
            ids=",".join(plan.undeclared),
        )
