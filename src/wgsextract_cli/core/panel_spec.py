"""``wgsextract.panel/1``: a declarative research panel, in TOML or JSON.

A panel names gene groups with the filters a variant must pass to count
("qualify"), single sites to watch and multi-site allele combinations
(haplotypes). The TypedDicts below are the format:
scripts/gen_output_schemas.py generates
assets/schemas/wgsextract.panel.1.schema.json from them, and :func:`validate`
walks the same types before its cross-field checks, so the schema and the
checks can't drift apart.

No key holds an expression. A variant qualifies through ``qualify`` clauses:
OR between clauses, AND inside one. A clause that needs a score the variant
lacks makes it ``unscored`` (listed, never counted) rather than passing or
failing it, so a missing score can't satisfy an OR silently.

A research panel is research only: it records its author's hypotheses, and a
variant that qualifies only passes the panel's filters.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
    TypedDict,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from wgsextract_cli.core.output_schema import Constraints, Shape, output_schema
from wgsextract_cli.core.utils import UsageError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

SCHEMA_ID = "wgsextract.panel/1"
SCHEMA_URN = "urn:wgsextract:schema:panel:1"
DISCLAIMER = (
    "Research use only; not a diagnosis. A research panel holds the genes, "
    "sites and allele combinations its author chose to examine; a variant "
    "that qualifies only passes the panel's filters, and nothing here says "
    "that it causes or predicts any condition. Don't act on panel results "
    "without a clinical genetics professional."
)

# The panel's coordinates, and the bundled annotation build each names.
PANEL_BUILDS = {"GRCh38": "hg38", "GRCh37": "hg19", "CHM13v2.0": "t2t"}
_BUILD = r"(GRCh38|GRCh37|CHM13v2\.0)"
_CHROM = r"(chr)?([1-9]|1[0-9]|2[0-2]|X|Y|M)"
_POS = r"[1-9][0-9]{0,9}"
SITE_LOCUS = re.compile(rf"^{_BUILD}:{_CHROM}:({_POS}):([ACGT]+)>([ACGT]+)$")
HAPLOTYPE_SITE = re.compile(rf"^{_BUILD}:{_CHROM}:({_POS}):([ACGT]+)$")
ALLELE_NAME = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9_.*+-]{0,63}$")

Fraction = Annotated[float, Constraints(minimum=0, maximum=1)]
Text = Annotated[str, Constraints(minLength=1)]
Ident = Annotated[
    str,
    Constraints(
        description="Letters, digits, '_', '.' or '-', starting with a letter or digit.",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
    ),
]
GeneName = Annotated[
    str,
    Constraints(
        description="An HGNC symbol, a unique synonym, or an HGNC ID (HGNC:613).",
        pattern=r"^[^\s,]+$",
    ),
]
Impact = Literal["HIGH", "MODERATE", "LOW", "MODIFIER"]
Consequence = Annotated[
    str,
    Constraints(
        description="A Sequence Ontology consequence term, e.g. missense_variant.",
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
Base = Annotated[str, Constraints(pattern=r"^[ACGT]+$")]


class Clause(TypedDict, total=False):
    impacts: Annotated[list[Impact], Constraints(minItems=1, uniqueItems=True)]
    consequences: Annotated[
        list[Consequence], Constraints(minItems=1, uniqueItems=True)
    ]
    min_revel: Fraction
    min_cadd_phred: Annotated[float, Constraints(minimum=0, maximum=100)]
    min_alphamissense: Fraction
    min_spliceai: Fraction


class Filters(TypedDict, total=False):
    max_af: Fraction
    max_popmax_af: Fraction
    pass_only: bool
    min_confidence: Literal["high", "moderate", "low"]
    missing_scores: Literal["unscored", "drop"]
    qualify: Annotated[
        list[Annotated[Clause, Constraints(minProperties=1)]],
        Constraints(minItems=1),
    ]


class _Group(TypedDict):
    id: Ident
    genes: Annotated[list[GeneName], Constraints(minItems=1)]


class Group(_Group, total=False):
    name: Text
    description: str
    recessive: Annotated[list[GeneName], Constraints(minItems=1)]
    overrides: Filters


class _Site(TypedDict):
    id: Ident
    locus: Annotated[
        str,
        Constraints(
            description="BUILD:CHROM:POS:REF>ALT, 1-based, e.g. GRCh38:chr1:11796321:G>A.",
            pattern=SITE_LOCUS.pattern,
        ),
    ]


class Site(_Site, total=False):
    name: Text
    rsid: Annotated[str, Constraints(pattern=r"^rs[1-9][0-9]*$")]


class _Haplotype(TypedDict):
    id: Ident
    gene: GeneName
    sites: Annotated[
        list[
            Annotated[
                str,
                Constraints(
                    description="BUILD:CHROM:POS:REF, 1-based.",
                    pattern=HAPLOTYPE_SITE.pattern,
                ),
            ]
        ],
        Constraints(minItems=1, uniqueItems=True),
    ]
    alleles: Annotated[
        dict[str, Annotated[list[Base], Constraints(minItems=1)]],
        Constraints(
            description="Allele name -> its bases, one per site, in the sites' order.",
            minProperties=1,
        ),
    ]


class Haplotype(_Haplotype, total=False):
    name: Text


class Calibration(TypedDict, total=False):
    null_sets: Annotated[int, Constraints(minimum=100, maximum=100000)]
    match_on: Annotated[
        list[Literal["callable_cds_bp"]], Constraints(minItems=1, uniqueItems=True)
    ]
    bins: Annotated[int, Constraints(minimum=2, maximum=100)]
    seed: Annotated[int, Constraints(minimum=0, maximum=2**53)]


class Source(TypedDict, total=False):
    citation: str
    url: str
    license: str
    note: str


class _Panel(TypedDict):
    schema: Literal["wgsextract.panel/1"]
    id: Annotated[str, Constraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
    name: Text
    version: Text


class Panel(_Panel, total=False):
    description: str
    build: Literal["GRCh38", "GRCh37", "CHM13v2.0"]
    source: Source
    defaults: Filters
    groups: Annotated[list[Group], Constraints(minItems=1)]
    sites: Annotated[list[Site], Constraints(minItems=1)]
    haplotypes: Annotated[list[Haplotype], Constraints(minItems=1)]
    calibration: Calibration


SHAPES: dict[object, Shape] = {
    Panel: Shape("panel", additional=False),
    Group: Shape("group", additional=False),
    Filters: Shape(
        "filters",
        "Variant filters: the panel's defaults, or a group's overrides of them.",
        additional=False,
    ),
    Clause: Shape(
        "clause",
        "A variant passes a clause when it passes every key in it.",
        additional=False,
    ),
    Site: Shape("site", additional=False),
    Haplotype: Shape("haplotype", additional=False),
    Calibration: Shape("calibration", additional=False),
    Source: Shape(
        "source", "Free text, copied into outputs, never read.", additional=False
    ),
}

# What an unset filter or calibration key means.
FILTER_DEFAULTS: dict[str, object] = {"pass_only": True, "missing_scores": "unscored"}
CALIBRATION_DEFAULTS: dict[str, object] = {
    "null_sets": 1000,
    "match_on": ["callable_cds_bp"],
    "bins": 20,
}


def panel_schema() -> dict[str, object]:
    return output_schema(
        Panel,
        shapes=SHAPES,
        schema_id=SCHEMA_URN,
        title=SCHEMA_ID,
        description="A declarative research panel (TOML or JSON). Research use "
        "only. See docs/panel.md.",
    )


@dataclass(frozen=True)
class Problem:
    """Why a panel is invalid, and where: ``location`` is a key path such as
    ``groups[1].genes[0]`` (0-based), or ``line 3, column 7`` for syntax."""

    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}" if self.location else self.message


@dataclass
class PanelFile:
    path: Path
    format: Literal["toml", "json"]
    sha256: str
    document: dict[str, Any] | None
    problems: list[Problem] = field(default_factory=list)


def read_panel(path: Path) -> PanelFile:
    """Parse a panel; ``document`` is None when the file isn't valid TOML or JSON.

    A ``.json`` file, or one of another suffix that starts with ``{``, is
    read as JSON; anything else as TOML.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"Cannot read --panel {path}: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    suffix = path.suffix.lower()
    if suffix == ".json" or (
        suffix != ".toml" and data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{")
    ):
        return _read_json(path, data, digest)
    try:
        document = tomllib.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        problem = Problem("syntax", "", f"not UTF-8 text: {exc}")
        return PanelFile(path, "toml", digest, None, [problem])
    except tomllib.TOMLDecodeError as exc:
        # "Expected '=' after a key in a key/value pair (at line 3, column 7)"
        message = str(exc)
        where = re.search(r"\(at (line \d+, column \d+)\)$", message)
        location = where.group(1) if where else ""
        message = message[: where.start()].rstrip() if where else message
        return PanelFile(
            path, "toml", digest, None, [Problem("syntax", location, message)]
        )
    return PanelFile(path, "toml", digest, document)


def _read_json(path: Path, data: bytes, digest: str) -> PanelFile:
    duplicates: list[str] = []

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in items:
            if key in seen:
                duplicates.append(key)
            seen[key] = value
        return seen

    try:
        document = json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs)
    except UnicodeDecodeError as exc:
        problem = Problem("syntax", "", f"not UTF-8 text: {exc}")
        return PanelFile(path, "json", digest, None, [problem])
    except json.JSONDecodeError as exc:
        problem = Problem("syntax", f"line {exc.lineno}, column {exc.colno}", exc.msg)
        return PanelFile(path, "json", digest, None, [problem])
    if duplicates:
        # TOML refuses a repeated key; JSON would quietly keep the last one.
        problems = [
            Problem("syntax", "", f"the key {key!r} is given twice")
            for key in dict.fromkeys(duplicates)
        ]
        return PanelFile(path, "json", digest, None, problems)
    if not isinstance(document, dict):
        problem = Problem("wrong_type", "", "a panel is a JSON object")
        return PanelFile(path, "json", digest, None, [problem])
    return PanelFile(path, "json", digest, document)


# ---------------------------------------------------------------- structure


def _join(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a table"
    return f"a {type(value).__name__}"  # TOML dates and times


def _is_type(value: object, tp: object) -> bool:
    if isinstance(value, bool) or tp is bool:
        return tp is bool and isinstance(value, bool)
    if tp is int:
        # JSON Schema counts 2.0 as an integer, so a JSON panel may write it.
        return isinstance(value, int) or (
            isinstance(value, float) and value.is_integer()
        )
    if tp is float:
        return isinstance(value, (int, float)) and math.isfinite(value)
    if tp is str:
        return isinstance(value, str)
    raise TypeError(f"Unsupported panel type: {tp!r}")


_TYPE_NAMES = {bool: "a boolean", int: "an integer", float: "a number", str: "a string"}


def _check(value: object, tp: object, path: str, out: list[Problem]) -> None:
    """Check ``value`` against a panel type; append what's wrong to ``out``."""
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is Annotated:
        before = len(out)
        _check(value, args[0], path, out)
        if len(out) == before:
            for constraint in args[1:]:
                if isinstance(constraint, Constraints):
                    _constrain(value, constraint, path, out)
        return
    if origin is Literal:
        if not any(type(value) is type(a) and value == a for a in args):
            options = ", ".join(json.dumps(a) for a in args)
            wanted = options if len(args) == 1 else f"one of {options}"
            out.append(
                Problem("bad_value", path, f"must be {wanted}, not {_shown(value)}")
            )
        return
    if origin is list:
        if not isinstance(value, list):
            out.append(
                Problem("wrong_type", path, f"must be a list, not {_kind(value)}")
            )
            return
        for index, item in enumerate(value):
            _check(item, args[0], _join(path, index), out)
        return
    if origin is dict or is_typeddict(tp):
        if not isinstance(value, dict):
            out.append(
                Problem("wrong_type", path, f"must be a table, not {_kind(value)}")
            )
        elif origin is dict:
            for key, item in value.items():
                _check(item, args[1], _join(path, key), out)
        else:
            _check_table(value, tp, path, out)
        return
    if not _is_type(value, tp):
        out.append(
            Problem(
                "wrong_type",
                path,
                f"must be {_TYPE_NAMES[tp]}, not {_kind(value)}",  # type: ignore[index]
            )
        )


def _check_table(
    value: Mapping[str, object], tp: object, path: str, out: list[Problem]
) -> None:
    hints = get_type_hints(tp, include_extras=True)
    required: frozenset[str] = getattr(tp, "__required_keys__", frozenset())
    for key in value:
        if key not in hints:
            out.append(
                Problem(
                    "unknown_key",
                    _join(path, key),
                    f"unknown key; {path or 'a panel'} takes {', '.join(hints)}",
                )
            )
    for key, hint in hints.items():
        if key in value:
            _check(value[key], hint, _join(path, key), out)
        elif key in required:
            out.append(Problem("missing_key", _join(path, key), "required"))


def _shown(value: object) -> str:
    try:
        return json.dumps(value)
    except TypeError:
        return _kind(value)


def _constrain(value: Any, rule: Constraints, path: str, out: list[Problem]) -> None:
    messages: list[str] = []
    if rule.minimum is not None and value < rule.minimum:
        messages.append(f"must be at least {rule.minimum:g}, not {value}")
    if rule.maximum is not None and value > rule.maximum:
        messages.append(f"must be at most {rule.maximum:g}, not {value}")
    if rule.pattern is not None and not re.search(rule.pattern, value):
        what = f" ({rule.description})" if rule.description else ""
        messages.append(f"{json.dumps(value)} is not in the expected form{what}")
    for least in (rule.minLength, rule.minItems, rule.minProperties):
        if least is not None and len(value) < least:
            messages.append("must not be empty")
    out.extend(Problem("bad_value", path, text) for text in messages)
    if rule.uniqueItems:
        keys = [json.dumps(item) for item in value]
        repeated = [item for i, item in enumerate(value) if keys.index(keys[i]) != i]
        if repeated:
            listed = ", ".join(json.dumps(item) for item in repeated)
            out.append(Problem("duplicate", path, f"lists {listed} more than once"))


# ---------------------------------------------------------------- meaning


@dataclass(frozen=True)
class Position:
    build: str  # GRCh38, GRCh37 or CHM13v2.0
    chrom: str  # chr1 ... chrM
    pos: int  # 1-based
    ref: str
    alt: str | None


def parse_position(text: str) -> Position:
    """A site locus (BUILD:CHROM:POS:REF>ALT) or haplotype site (BUILD:CHROM:POS:REF)."""
    match = SITE_LOCUS.match(text) or HAPLOTYPE_SITE.match(text)
    if match is None:
        raise ValueError(f"not a panel locus: {text}")
    build, _, chrom, pos, ref, *alt = match.groups()
    return Position(build, f"chr{chrom}", int(pos), ref, alt[0] if alt else None)


def validate(document: Mapping[str, Any]) -> list[Problem]:
    """Every problem with a parsed panel: its structure, then what the
    structure can't say (unique ids, one build, allele lengths...).

    Loci are checked against the contig lengths of the bundled annotations.
    """
    declared = document.get("schema")
    if isinstance(declared, str) and declared != SCHEMA_ID:
        return [
            Problem(
                "unsupported_schema",
                "schema",
                f"{declared!r} is not a format this version reads; it reads "
                f"{SCHEMA_ID!r}",
            )
        ]
    problems: list[Problem] = []
    _check(document, Panel, "", problems)
    if problems:
        return problems
    return _semantics(document)


def _semantics(panel: Mapping[str, Any]) -> list[Problem]:
    groups = panel.get("groups", [])
    sites = panel.get("sites", [])
    haplotypes = panel.get("haplotypes", [])
    problems: list[Problem] = []
    if not (groups or sites or haplotypes):
        problems.append(
            Problem(
                "empty_panel",
                "",
                "the panel has no groups, sites or haplotypes; give at least one",
            )
        )
    problems += _id_problems(panel)
    rules = panel.get("defaults", {}).get("qualify")
    for index, group in enumerate(groups):
        problems += _group_problems(_join("groups", index), group, rules)
    problems += _locus_problems(panel)
    for index, haplotype in enumerate(haplotypes):
        problems += _haplotype_problems(_join("haplotypes", index), haplotype)
    return problems


def _id_problems(panel: Mapping[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    seen: dict[str, str] = {}
    for kind in ("groups", "sites", "haplotypes"):
        for index, item in enumerate(panel.get(kind, [])):
            where = _join(_join(kind, index), "id")
            ident = item["id"]
            if ident in seen:
                problems.append(
                    Problem(
                        "duplicate",
                        where,
                        f"{ident!r} is already the id of {seen[ident]}",
                    )
                )
            else:
                seen[ident] = _join(kind, index)
    return problems


def _group_problems(
    where: str, group: Mapping[str, Any], default_rules: object
) -> list[Problem]:
    problems: list[Problem] = []
    names = [gene.upper() for gene in group["genes"]]
    for index, name in enumerate(names):
        if names.index(name) != index:
            problems.append(
                Problem(
                    "duplicate",
                    _join(_join(where, "genes"), index),
                    f"{group['genes'][index]} is listed twice in this group",
                )
            )
    for index, gene in enumerate(group.get("recessive", [])):
        if gene.upper() not in names:
            problems.append(
                Problem(
                    "recessive_not_in_group",
                    _join(_join(where, "recessive"), index),
                    f"{gene} is not one of this group's genes",
                )
            )
    if not (group.get("overrides", {}).get("qualify") or default_rules):
        problems.append(
            Problem(
                "no_qualify_rule",
                where,
                f"group {group['id']!r} has no qualify clauses; give "
                "defaults.qualify or the group's overrides.qualify",
            )
        )
    return problems


def _locus_problems(panel: Mapping[str, Any]) -> list[Problem]:
    from wgsextract_cli.core.panel import load_annotations

    loci = [
        (_join(_join("sites", i), "locus"), site["locus"])
        for i, site in enumerate(panel.get("sites", []))
    ] + [
        (_join(_join(_join("haplotypes", i), "sites"), j), text)
        for i, haplotype in enumerate(panel.get("haplotypes", []))
        for j, text in enumerate(haplotype["sites"])
    ]
    declared = panel.get("build")
    problems: list[Problem] = []
    if loci and declared is None:
        problems.append(
            Problem(
                "build_mismatch",
                "build",
                "required when the panel has sites or haplotypes: the build of "
                "their coordinates (GRCh38, GRCh37 or CHM13v2.0)",
            )
        )
    for where, text in loci:
        position = parse_position(text)
        if declared is not None and position.build != declared:
            problems.append(
                Problem(
                    "build_mismatch",
                    where,
                    f"{text} is on {position.build}, but the panel's build is "
                    f"{declared}; nothing is lifted over",
                )
            )
            continue
        contigs = load_annotations(PANEL_BUILDS[position.build]).contigs
        length = contigs.get(position.chrom)
        if length is not None and position.pos + len(position.ref) - 1 > length:
            problems.append(
                Problem(
                    "locus_out_of_range",
                    where,
                    f"{text} lies beyond the end of {position.chrom} "
                    f"({length:,} bp on {position.build})",
                )
            )
        if position.alt == position.ref:
            problems.append(Problem("bad_value", where, f"{text}: ALT is REF"))
    return problems


def _haplotype_problems(where: str, haplotype: Mapping[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    positions = [parse_position(text) for text in haplotype["sites"]]
    chroms = sorted({p.chrom for p in positions})
    if len(chroms) > 1:
        problems.append(
            Problem(
                "haplotype_split",
                _join(where, "sites"),
                f"its sites are on {', '.join(chroms)}; a haplotype's sites "
                "must share one chromosome",
            )
        )
    places = [(p.chrom, p.pos) for p in positions]
    for index, place in enumerate(places):
        if places.index(place) != index:
            problems.append(
                Problem(
                    "duplicate",
                    _join(_join(where, "sites"), index),
                    f"{place[0]}:{place[1]} is given twice",
                )
            )
    named: dict[tuple[str, ...], str] = {}
    for name, bases in haplotype["alleles"].items():
        allele = _join(_join(where, "alleles"), name)
        if not ALLELE_NAME.match(name):
            problems.append(
                Problem(
                    "bad_value",
                    allele,
                    f"{name!r} is not an allele name (letters, digits and "
                    "'*', '_', '.', '+' or '-')",
                )
            )
        if len(bases) != len(positions):
            problems.append(
                Problem(
                    "allele_length",
                    allele,
                    f"has {len(bases)} base{'s' * (len(bases) != 1)} for "
                    f"{len(positions)} site{'s' * (len(positions) != 1)}",
                )
            )
            continue
        combination = tuple(bases)
        if combination in named:
            problems.append(
                Problem(
                    "duplicate",
                    allele,
                    f"has the same bases as allele {named[combination]!r}",
                )
            )
        else:
            named[combination] = name
    return problems


# ---------------------------------------------------------------- effective values


def effective_filters(
    panel: Mapping[str, Any], group: Mapping[str, Any]
) -> dict[str, Any]:
    """A group's filters: its overrides, else the panel's defaults, else the
    format's defaults. ``qualify`` is replaced whole, not merged."""
    filters: dict[str, Any] = dict(FILTER_DEFAULTS)
    filters.update(panel.get("defaults", {}))
    filters.update(group.get("overrides", {}))
    return filters


def effective_calibration(panel: Mapping[str, Any]) -> dict[str, Any]:
    calibration: dict[str, Any] = dict(CALIBRATION_DEFAULTS)
    calibration.update(panel.get("calibration", {}))
    return calibration
