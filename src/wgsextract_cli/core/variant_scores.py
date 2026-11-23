"""In-silico pathogenicity, splicing and conservation scores for `vcf table`.

Each score is taken from the first of these sources that has it:

1. The INFO field our own annotator writes: ``REVEL`` (`vcf revel`),
   ``am_pathogenicity``/``am_class`` (`vcf alphamissense`),
   ``CADD_RAW``/``CADD_PHRED`` (`vcf cadd`), ``SpliceAI`` (`vcf spliceai`,
   and fastVEP's, which is the same format), ``PHYLOP`` (`vcf phylop`) and
   ``GERP`` (`vcf gerp`).
2. fastVEP's own INFO fields (``FV_REVEL``, ``FV_ALPHAMISSENSE``,
   ``FV_PHYLOP``, ``FV_GERP``; ``FV_DBNSFP`` for SIFT and PolyPhen, after
   VEP's).
3. VEP's CSQ: the REVEL, AlphaMissense, CADD, SpliceAI and Conservation
   plugins, dbNSFP's columns, and VEP's own SIFT and PolyPhen.

A score no source gives stays empty (None), never 0: an unscored variant is
not a benign one.

CSQ values that describe a transcript (REVEL, AlphaMissense, SIFT, PolyPhen)
come from the transcript ``pick_csq`` chose, the one ``transcript_id`` names.
Values that describe the variant or its position (CADD, PhyloP, GERP) come
from that transcript, else any of the allele's entries. SpliceAI's are those
of the row's gene, else the highest of any gene's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import unquote

from wgsextract_cli.core.table_writer import Column

TEXT, REAL = "TEXT", "REAL"

SCORE_COLUMNS: tuple[Column, ...] = (
    Column(
        "revel",
        REAL,
        "REVEL missense pathogenicity score (0-1; INFO/REVEL from `vcf revel`, "
        "else fastVEP's FV_REVEL, VEP's REVEL or dbNSFP REVEL_score).",
    ),
    Column(
        "alphamissense_score",
        REAL,
        "AlphaMissense pathogenicity (0-1; INFO/am_pathogenicity from "
        "`vcf alphamissense`, else fastVEP's FV_ALPHAMISSENSE, VEP's "
        "am_pathogenicity or dbNSFP AlphaMissense_score).",
    ),
    Column(
        "alphamissense_class",
        TEXT,
        "AlphaMissense class: likely_benign, ambiguous or likely_pathogenic "
        "(INFO/am_class, else fastVEP's FV_ALPHAMISSENSE, VEP's am_class or "
        "dbNSFP AlphaMissense_pred).",
    ),
    Column(
        "cadd_raw",
        REAL,
        "CADD raw score (INFO/CADD_RAW from `vcf cadd`, else VEP's CADD_RAW "
        "or dbNSFP CADD_raw).",
    ),
    Column(
        "cadd_phred",
        REAL,
        "CADD PHRED-scaled score (INFO/CADD_PHRED, else VEP's CADD_PHRED or "
        "dbNSFP CADD_phred).",
    ),
    Column(
        "spliceai_max",
        REAL,
        "Highest SpliceAI delta score of this ALT (0-1; max of DS_AG, DS_AL, "
        "DS_DG and DS_DL, for the row's gene when scored; INFO/SpliceAI from "
        "`vcf spliceai` or fastVEP, else VEP's SpliceAI_pred_DS_*).",
    ),
    Column(
        "phylop",
        REAL,
        "PhyloP conservation score (INFO/PHYLOP from `vcf phylop`, else "
        "fastVEP's FV_PHYLOP or dbNSFP phyloP100way_vertebrate).",
    ),
    Column(
        "gerp",
        REAL,
        "GERP++ rejected-substitution score (INFO/GERP from `vcf gerp`, else "
        "fastVEP's FV_GERP, VEP's Conservation or dbNSFP GERP++_RS).",
    ),
    Column(
        "sift_score",
        REAL,
        "SIFT score (0-1, low is damaging) of the chosen transcript (VEP SIFT), "
        "else fastVEP's FV_DBNSFP.",
    ),
    Column(
        "sift_prediction",
        TEXT,
        "SIFT prediction, e.g. deleterious (VEP SIFT, else fastVEP's FV_DBNSFP).",
    ),
    Column(
        "polyphen_score",
        REAL,
        "PolyPhen-2 score (0-1, high is damaging) of the chosen transcript "
        "(VEP PolyPhen), else fastVEP's FV_DBNSFP.",
    ),
    Column(
        "polyphen_prediction",
        TEXT,
        "PolyPhen-2 prediction, e.g. probably_damaging (VEP PolyPhen, else "
        "fastVEP's FV_DBNSFP).",
    ),
)

# INFO fields of our annotators. A Number=1 value of an allele-specific score
# on a record with several ALTs can't be told apart, so it is left empty;
# conservation is a property of the position, so every ALT shares it.
REVEL, AM_SCORE, AM_CLASS = "REVEL", "am_pathogenicity", "am_class"
CADD_RAW, CADD_PHRED, SPLICEAI = "CADD_RAW", "CADD_PHRED", "SpliceAI"
PHYLOP, GERP = "PHYLOP", "GERP"
_POSITIONAL = frozenset({PHYLOP, GERP})


# Pipe-delimited INFO fields with an allele first, and their layout when the
# header doesn't spell it out ("Format: ALLELE|..."): SpliceAI's, and
# fastVEP's (docs/SUPPLEMENTARY_ANNOTATIONS.md of fastVEP).
FV_REVEL, FV_AM, FV_PHYLOP = "FV_REVEL", "FV_ALPHAMISSENSE", "FV_PHYLOP"
FV_GERP, FV_DBNSFP = "FV_GERP", "FV_DBNSFP"
PIPE_FORMATS: dict[str, tuple[str, ...]] = {
    SPLICEAI: tuple(
        "ALLELE SYMBOL DS_AG DS_AL DS_DG DS_DL DP_AG DP_AL DP_DG DP_DL".split()
    ),
    FV_REVEL: ("ALLELE", "SCORE"),
    FV_AM: ("ALLELE", "PATHOGENICITY", "CLASS"),
    FV_PHYLOP: ("ALLELE", "SCORE"),
    FV_GERP: ("ALLELE", "SCORE"),
    FV_DBNSFP: ("ALLELE", "SIFT", "POLYPHEN"),
}

# Annotation names for the table's provenance: INFO tags or CSQ fields.
ANNOTATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("revel", (REVEL, FV_REVEL, "REVEL_score")),
    ("alphamissense", (AM_SCORE, FV_AM, "AlphaMissense_score")),
    ("cadd", (CADD_PHRED, "CADD_phred")),
    ("spliceai", (SPLICEAI, "SpliceAI_pred_DS_AG")),
    ("phylop", (PHYLOP, FV_PHYLOP, "phyloP100way_vertebrate")),
    ("gerp", (GERP, FV_GERP, "Conservation", "GERP++_RS")),
    ("sift", ("SIFT", FV_DBNSFP)),
    ("polyphen", ("PolyPhen", FV_DBNSFP)),
)
NAMES = frozenset(name for name, _ in ANNOTATIONS)

# The numeric scores of our annotators' INFO fields, in column order (with
# alphamissense_class and spliceai_max between them): each one's fastVEP field
# and key, its CSQ fields (below), and whether any of an allele's CSQ entries
# may give it (it describes the variant, not the transcript).
_NUMERIC = (
    (REVEL, (FV_REVEL, "SCORE"), "revel", False),
    (AM_SCORE, (FV_AM, "PATHOGENICITY"), "am", False),
    (CADD_RAW, None, "cadd_raw", True),
    (CADD_PHRED, None, "cadd_phred", True),
    (PHYLOP, (FV_PHYLOP, "SCORE"), "phylop", True),
    (GERP, (FV_GERP, "SCORE"), "gerp", True),
)

# VEP's CSQ fields of each score, preferred first: its plugin's, then dbNSFP's.
_CSQ_SCORES = {
    "revel": ("REVEL", "REVEL_score"),
    "am": (AM_SCORE, "AlphaMissense_score"),
    "cadd_raw": ("CADD_RAW", "CADD_raw"),
    "cadd_phred": ("CADD_PHRED", "CADD_phred"),
    "phylop": ("phyloP100way_vertebrate",),
    "gerp": ("Conservation", "GERP++_RS"),
}

_DELTAS = ("DS_AG", "DS_AL", "DS_DG", "DS_DL")
_CSQ_DELTAS = tuple(f"SpliceAI_pred_{d}" for d in _DELTAS)
# dbNSFP's AlphaMissense_pred letters, most pathogenic first.
_AM_PRED = {"P": "likely_pathogenic", "A": "ambiguous", "B": "likely_benign"}
_AM_RANK = ("likely_pathogenic", "ambiguous", "likely_benign")

Entries = Sequence[Mapping[str, str]]


@dataclass(frozen=True)
class CsqScores:
    """The score fields a VCF's CSQ header declares, so that a row looks up
    only those."""

    names: dict[str, tuple[str, ...]]
    am_class: bool
    spliceai: bool
    sift: bool
    polyphen: bool

    @classmethod
    def of(cls, csq_fields: Sequence[str] | None) -> CsqScores:
        fields = set(csq_fields or ())
        return cls(
            names={
                score: tuple(n for n in names if n in fields)
                for score, names in _CSQ_SCORES.items()
            },
            am_class=bool({AM_CLASS, "AlphaMissense_pred"} & fields),
            spliceai=bool(set(_CSQ_DELTAS) & fields),
            sift="SIFT" in fields,
            polyphen="PolyPhen" in fields,
        )


def _number(raw: str | None) -> float | None:
    if raw is None or raw in ("", "."):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _text(raw: str | None) -> str | None:
    """A text value; fastVEP percent-encodes its separators."""
    if raw is None or raw in ("", "."):
        return None
    return unquote(raw) if "%" in raw else raw


def _highest(raw: str | None) -> float | None:
    """The highest number in a value VEP joined with '&' (one per co-located
    variant or dbNSFP transcript); higher means more damaging or conserved."""
    values = [v for part in (raw or "").split("&") if (v := _number(part)) is not None]
    return max(values) if values else None


def _info_value(
    info: Mapping[str, str],
    numbers: Mapping[str, str],
    tag: str,
    alt_index: int,
    variant_alts: int,
) -> str | None:
    """This ALT's value of an INFO score (``alt_index`` is 1-based)."""
    raw = info.get(tag)
    if raw is None or raw in ("", "."):
        return None
    number = numbers.get(tag)
    if number in ("A", "R"):
        values = raw.split(",")
        i = alt_index - 1 if number == "A" else alt_index
        return values[i] if i < len(values) and values[i] not in ("", ".") else None
    if variant_alts > 1 and tag not in _POSITIONAL:
        return None
    return raw


def _entry_score(entries: Entries, key: str) -> float | None:
    """The highest ``key`` of a pipe field's entries for one ALT."""
    values = [v for e in entries if (v := _highest(e.get(key))) is not None]
    return max(values) if values else None


def _csq_value(
    csq: Mapping[str, str] | None,
    entries: Entries,
    names: Sequence[str],
    *,
    any_entry: bool = False,
) -> float | None:
    """The first of ``names`` the chosen entry scores; with ``any_entry``,
    else the first any of the allele's entries scores."""
    for name in names:
        for entry in (csq, *(entries if any_entry else ())):
            if entry and (value := _highest(entry.get(name))) is not None:
                return value
    return None


def _prediction(raw: str | None) -> tuple[float | None, str | None]:
    """(score, prediction) of a SIFT or PolyPhen value: VEP's
    ``deleterious(0.01)``, or either part alone."""
    raw = _text(raw)
    if raw is None:
        return None, None
    if (number := _number(raw)) is not None:
        return number, None
    prediction, _, score = raw.partition("(")
    return _number(score.removesuffix(")")), prediction or None


def _csq_am_class(csq: Mapping[str, str] | None) -> str | None:
    if csq is None:
        return None
    if value := _text(csq.get(AM_CLASS)):
        return value
    found = {
        _AM_PRED[p]
        for p in (csq.get("AlphaMissense_pred") or "").split("&")
        if p in _AM_PRED
    }
    return next((c for c in _AM_RANK if c in found), None)


def _spliceai_max(entries: Entries, gene: str | None) -> float | None:
    """The highest delta score of SpliceAI entries already matched to the ALT,
    of the row's gene when SpliceAI scored it."""
    # Compared decoded: fastVEP percent-encodes symbols in SpliceAI and CSQ.
    wanted = _text(gene)
    of_gene = [e for e in entries if wanted and _text(e.get("SYMBOL")) == wanted]
    deltas = [
        v
        for e in (of_gene or entries)
        for key in _DELTAS
        if (v := _number(e.get(key))) is not None
    ]
    return max(deltas) if deltas else None


def _csq_deltas(entry: Mapping[str, str]) -> dict[str, str]:
    """VEP SpliceAI plugin's delta scores (its split output) as SpliceAI's."""
    return {d: entry.get(c, "") for d, c in zip(_DELTAS, _CSQ_DELTAS, strict=True)}


def _fv_text(
    info: Mapping[str, str],
    pipe_entries: Callable[[str], Entries],
    tag: str,
    key: str,
) -> str | None:
    """The first ``key`` text of a fastVEP field's entries for one ALT."""
    if tag not in info:
        return None
    return next((t for e in pipe_entries(tag) if (t := _text(e.get(key)))), None)


def allele_scores(
    fields: CsqScores,
    info: Mapping[str, str],
    numbers: Mapping[str, str],
    alt_index: int,
    variant_alts: int,
    csq: Mapping[str, str] | None,
    csq_entries: Entries,
    pipe_entries: Callable[[str], Entries],
    gene: str | None,
) -> tuple[float | str | None, ...]:
    """One ALT's values, in ``SCORE_COLUMNS`` order.

    ``fields`` are the header's CSQ score fields (``CsqScores.of``);
    ``variant_alts`` counts the record's real ALTs (not ``*`` or ``<NON_REF>``);
    ``csq`` is the chosen CSQ entry and ``csq_entries`` all of the ALT's;
    ``pipe_entries(tag)`` are a ``PIPE_FORMATS`` field's entries for the ALT.
    """

    names = fields.names
    numeric: list[float | None] = []
    for tag, fastvep, key, any_entry in _NUMERIC:
        value = (
            _number(_info_value(info, numbers, tag, alt_index, variant_alts))
            if tag in info
            else None
        )
        if value is None and fastvep and fastvep[0] in info:
            value = _entry_score(pipe_entries(fastvep[0]), fastvep[1])
        if value is None and names[key]:
            value = _csq_value(csq, csq_entries, names[key], any_entry=any_entry)
        numeric.append(value)
    revel, am_score, cadd_raw, cadd_phred, phylop, gerp = numeric
    am_class = _info_value(info, numbers, AM_CLASS, alt_index, variant_alts)
    if am_class is None:
        am_class = _fv_text(info, pipe_entries, FV_AM, "CLASS")
    if am_class is None and fields.am_class:
        am_class = _csq_am_class(csq)
    spliceai = _spliceai_max(pipe_entries(SPLICEAI), gene) if SPLICEAI in info else None
    if spliceai is None and fields.spliceai and csq is not None:
        spliceai = _spliceai_max([_csq_deltas(csq)], None)
        if spliceai is None:
            spliceai = _spliceai_max([_csq_deltas(e) for e in csq_entries], None)
    unscored: tuple[float | None, str | None] = (None, None)
    sift = polyphen = unscored
    if fields.sift and csq:
        sift = _prediction(csq.get("SIFT"))
    if fields.polyphen and csq:
        polyphen = _prediction(csq.get("PolyPhen"))
    if FV_DBNSFP in info:
        if sift == (None, None):
            sift = _prediction(_fv_text(info, pipe_entries, FV_DBNSFP, "SIFT"))
        if polyphen == (None, None):
            polyphen = _prediction(_fv_text(info, pipe_entries, FV_DBNSFP, "POLYPHEN"))
    return (
        revel,
        am_score,
        am_class,
        cadd_raw,
        cadd_phred,
        spliceai,
        phylop,
        gerp,
        *sift,
        *polyphen,
    )
