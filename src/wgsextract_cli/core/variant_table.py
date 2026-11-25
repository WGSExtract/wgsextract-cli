"""One flat row per variant allele, from a VCF and whatever annotations it carries.

`vcf table` turns a VCF into a table an agent, a spreadsheet or SQL can query
without VCF knowledge: one row per ALT allele, fixed typed columns, no nested
values. The annotations come from the INFO fields our own commands write:

- VEP's ``CSQ`` (`vep run`) or SnpEff's ``ANN``: gene, consequence, impact
  and HGVS of one transcript per allele (see ``pick_csq``), rsIDs, ClinVar
  and gnomAD values when the VCF lacks the dedicated fields below, and VEP's
  1000 Genomes ``AF`` and ``MAX_AF``. Each frequency column names its source:
  a vep-rs cache built by `vep download` has 1000 Genomes frequencies but no
  gnomAD ones, and 1000 Genomes values never fill ``gnomad_af``.
- ``CLNSIG``/``CLNDN`` (`vcf clinvar`), ``GNOMAD_AF`` (`vcf gnomad`),
  ``PHARMGKB`` (`vcf pharmgkb`) and ``WGSE_CONF``/``WGSE_CONF_REASONS`` (depth-aware
  confidence labels, `vcf clinvar`/`vcf gnomad`/`vcf pharmcat`).
- ``SV_AF``/``SV_FREQ`` (`vcf sv-freq`): structural variants' population
  frequency and class (common, low, rare or novel).
- ``ACMG_CLASS``/``ACMG_CRITERIA``/``ACMG_TRANSCRIPT`` (`vcf acmg`): fastVEP's
  automated ACMG/AMP class, research only (core/acmg.py).

An annotation the VCF lacks leaves its column empty. Records are parsed one at
a time, so a whole genome streams through in constant memory.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TypeVar
from urllib.parse import unquote

from wgsextract_cli.core import acmg
from wgsextract_cli.core import clinical_evidence as ce
from wgsextract_cli.core import variant_scores as scores
from wgsextract_cli.core._variant_table_fields import csq_frequency as _csq_frequency
from wgsextract_cli.core._variant_table_fields import fraction as _fraction
from wgsextract_cli.core._variant_table_fields import per_allele as _per_allele
from wgsextract_cli.core._variant_table_fields import to_float as _to_float
from wgsextract_cli.core._variant_table_fields import to_int as _to_int
from wgsextract_cli.core._variant_table_fields import value as _value
from wgsextract_cli.core.csq_alleles import (
    allele_entries,
    csq_entries,
    csq_for_allele,
    for_allele,
    split_origin,
)
from wgsextract_cli.core.imputed_vcf import IMPUTED_TAG
from wgsextract_cli.core.table_writer import Column
from wgsextract_cli.core.variant_confidence import SPLIT_TAG
from wgsextract_cli.core.variant_depths import AD_TOTAL, allele_depths, count

TEXT, INTEGER, REAL = "TEXT", "INTEGER", "REAL"

# The table's schema, in column order. Add a column by appending it here and
# filling it in ``variant_rows``; the writers and docs read this list.
COLUMNS: tuple[Column, ...] = (
    Column("chrom", TEXT, "Chromosome, as named in the VCF (chr1 or 1)."),
    Column("pos", INTEGER, "1-based position of REF."),
    Column("ref", TEXT, "Reference allele."),
    Column("alt", TEXT, "This row's alternate allele (one row per ALT)."),
    Column(
        "rsid",
        TEXT,
        "dbSNP rsID(s), ';'-separated: the VCF ID, else VEP's or PharmGKB's.",
    ),
    Column("gene", TEXT, "Gene symbol (VEP SYMBOL, SnpEff Gene_Name or PharmGKB)."),
    Column(
        "consequence",
        TEXT,
        "Sequence Ontology consequence(s) on the chosen transcript, '&'-separated "
        "(e.g. missense_variant).",
    ),
    Column("impact", TEXT, "VEP/SnpEff impact: HIGH, MODERATE, LOW or MODIFIER."),
    Column("hgvs_c", TEXT, "HGVS coding change on the chosen transcript."),
    Column("hgvs_p", TEXT, "HGVS protein change on the chosen transcript."),
    Column(
        "clinvar_significance",
        TEXT,
        "ClinVar clinical significance (CLNSIG, e.g. Pathogenic, "
        "Likely_pathogenic, Benign).",
    ),
    Column("clinvar_condition", TEXT, "ClinVar condition name(s) (CLNDN)."),
    Column(
        "gnomad_af",
        REAL,
        "gnomAD allele frequency of this ALT (0-1). Empty when gnomAD lacks the "
        "ALT or when the VCF has no gnomAD annotation (`vcf gnomad`).",
    ),
    Column("genotype", TEXT, "The sample's GT, as in the VCF (e.g. 0/1, 1|1)."),
    Column(
        "zygosity",
        TEXT,
        "The sample's copies of this ALT: het, hom_alt, hemizygous, "
        "not_carried or no_call.",
    ),
    Column(
        "depth", INTEGER, "Read depth: FORMAT/DP, else the sum of AD, else INFO/DP."
    ),
    Column("gq", INTEGER, "Genotype quality (FORMAT/GQ)."),
    Column("qual", REAL, "Variant quality (QUAL)."),
    Column("filter", TEXT, "FILTER: PASS or the failed filters, ';'-separated."),
    Column(
        "confidence",
        TEXT,
        "Depth-aware confidence label: high, moderate or low (INFO/WGSE_CONF).",
    ),
    Column(
        "confidence_reasons",
        TEXT,
        "Reason codes behind the confidence label, '|'-separated "
        "(INFO/WGSE_CONF_REASONS).",
    ),
    Column(
        "pharmgkb_level",
        TEXT,
        "Best PharmGKB clinical-annotation level of evidence (1A strongest, "
        "then 1B, 2A, 2B, 3, 4).",
    ),
    Column("pharmgkb_drugs", TEXT, "Drugs in PharmGKB's annotations, '; '-separated."),
    Column(
        "pharmcat_diplotype",
        TEXT,
        "PharmCAT's diplotype for this row's gene (with --pharmcat-json).",
    ),
    Column(
        "pharmcat_phenotype",
        TEXT,
        "PharmCAT's phenotype for this row's gene (with --pharmcat-json).",
    ),
    Column(
        "sv_af",
        REAL,
        "Population allele frequency of the matching gnomAD SV (SV_AF, `vcf sv-freq`).",
    ),
    Column(
        "sv_freq",
        TEXT,
        "SV frequency class: common, low, rare or novel (SV_FREQ, `vcf sv-freq`).",
    ),
    Column(
        "imputed",
        INTEGER,
        "1 when the genotype was imputed from a reference panel (INFO/IMP, "
        "`vcf impute`), not read from the sample; else 0.",
    ),
    Column(
        "genotype_probability",
        REAL,
        "Probability of the sample's genotype (largest FORMAT/GP), for imputed calls.",
    ),
    Column("clinvar_id", TEXT, "ClinVar resource record ID (not the sample rsID)."),
    Column(
        "clinvar_review_status", TEXT, "ClinVar CLNREVSTAT assertion review status."
    ),
    Column(
        "clinvar_review_stars",
        INTEGER,
        "ClinVar review stars (0-4), not sequencing confidence.",
    ),
    Column(
        "clinvar_review_tier",
        TEXT,
        "ClinVar assertion support tier, not sequencing confidence.",
    ),
    Column(
        "clinvar_conflict_evidence",
        TEXT,
        "ClinVar conflicting submitter classifications (CLNSIGCONF).",
    ),
    Column(
        "original_record", TEXT, "Original allele and sample fields (URL-encoded JSON)."
    ),
    Column(
        "mnv_components", TEXT, "Phase-supported MNV components (URL-encoded JSON)."
    ),
    Column(
        "acmg_class",
        TEXT,
        "Research only, not a diagnosis: fastVEP's automated ACMG/AMP class "
        "(P, LP, VUS, LB or B; INFO/ACMG_CLASS, `vcf acmg`).",
    ),
    Column(
        "acmg_criteria",
        TEXT,
        "Research only: the ACMG/AMP criteria fastVEP found met, '&'-separated "
        "(INFO/ACMG_CRITERIA).",
    ),
    Column(
        "acmg_transcript",
        TEXT,
        "The transcript fastVEP classified the allele on (INFO/ACMG_TRANSCRIPT).",
    ),
    Column("phase_set", TEXT, "The sample's FORMAT/PS, only for phased genotypes."),
    Column(
        "alt_fraction",
        REAL,
        "This ALT's read fraction: AD_alt / sum(AD), else FORMAT/VAF or FORMAT/AF.",
    ),
    Column(
        "transcript_id",
        TEXT,
        "The chosen consequence's transcript (VEP Feature or SnpEff Feature_ID).",
    ),
    Column(
        "gnomad_popmax_af",
        REAL,
        "Maximum gnomAD genetic ancestry group allele frequency of this ALT.",
    ),
    Column("gnomad_nhomalt", INTEGER, "gnomAD count of homozygotes for this ALT."),
    Column(
        "kg_af",
        REAL,
        "1000 Genomes phase 3 allele frequency of this ALT, all populations "
        "(VEP's AF); not gnomAD.",
    ),
    Column(
        "max_af",
        REAL,
        "VEP's MAX_AF: this ALT's highest frequency in any population VEP's cache "
        "has (gnomAD and 1000 Genomes in Ensembl VEP's cache; 1000 Genomes only "
        "in a vep-rs cache from `vep download`).",
    ),
    Column(
        "ad_ref",
        INTEGER,
        "The sample's reads supporting REF (FORMAT/AD); empty when AD is "
        "missing or incomplete.",
    ),
    Column(
        "ad_alt",
        INTEGER,
        "The sample's reads supporting this ALT (FORMAT/AD); empty when AD is "
        "missing or incomplete.",
    ),
    *scores.SCORE_COLUMNS,
)
COLUMN_NAMES = tuple(column.name for column in COLUMNS)
Row = tuple[str | int | float | None, ...]

# Zygosities of rows the sample carries.
CARRIED = frozenset({"het", "hom_alt", "hemizygous"})

# ALTs that are not variants: a gVCF's reference-block allele and the
# spanning-deletion placeholder (the deletion has its own row).
_NON_VARIANT_ALTS = frozenset({".", "*", "<NON_REF>", "<*>", "<X>"})

# Ensembl's consequence order, most severe first
# (https://www.ensembl.org/info/genome/variation/prediction/predicted_data.html).
_SEVERITY = {
    term: rank
    for rank, term in enumerate(
        """transcript_ablation splice_acceptor_variant splice_donor_variant
        stop_gained frameshift_variant stop_lost start_lost
        transcript_amplification feature_elongation feature_truncation
        inframe_insertion inframe_deletion missense_variant
        protein_altering_variant splice_donor_5th_base_variant
        splice_region_variant splice_donor_region_variant
        splice_polypyrimidine_tract_variant incomplete_terminal_codon_variant
        start_retained_variant stop_retained_variant synonymous_variant
        coding_sequence_variant mature_miRNA_variant 5_prime_UTR_variant
        3_prime_UTR_variant non_coding_transcript_exon_variant intron_variant
        NMD_transcript_variant non_coding_transcript_variant
        coding_transcript_variant upstream_gene_variant downstream_gene_variant
        TFBS_ablation TFBS_amplification TF_binding_site_variant
        regulatory_region_ablation regulatory_region_amplification
        regulatory_region_variant intergenic_variant sequence_variant""".split()
    )
}
_UNRANKED = len(_SEVERITY)

# VEP gnomAD frequency fields, preferred first: genomes (whole-genome
# sequencing, like ours), then exomes, then the pre-v4 combined field.
VEP_GNOMAD_AF = ("gnomADg_AF", "gnomADe_AF", "gnomAD_AF")
_POPMAX_INFO = ("GNOMAD_POPMAX_AF", "AF_grpmax_joint", "AF_grpmax", "AF_popmax")
_NHOMALT_INFO = ("GNOMAD_NHOMALT", "nhomalt_joint", "nhomalt")
_POPMAX_CSQ = ("gnomAD_AF_grpmax_joint", "AF_grpmax_joint") + tuple(
    f"{source}_{field}"
    for source in ("gnomADg", "gnomADe", "gnomAD")
    for field in ("AF_grpmax", "AF_popmax")
)
_NHOMALT_CSQ = (
    "gnomAD_nhomalt_joint",
    "nhomalt_joint",
    "gnomADg_nhomalt",
    "gnomADe_nhomalt",
    "gnomAD_nhomalt",
)
_PHARMGKB_LEVELS = ("1A", "1B", "2A", "2B", "3", "4")

_FIELD_LINE = re.compile(r"^##(INFO|FORMAT)=<(.*)>$")
_ATTRIBUTE = re.compile(r'([^,=\s]+)=("(?:[^"\\]|\\.)*"|[^,]*)')
_FORMAT_LIST = re.compile(r"Format:\s*(.+)$")
_GT_SPLIT = re.compile(r"[/|]")


@dataclass
class VcfHeader:
    """What the table needs from a VCF header."""

    info_number: dict[str, str] = field(default_factory=dict)
    format_number: dict[str, str] = field(default_factory=dict)
    csq_fields: list[str] | None = None
    ann_fields: list[str] | None = None
    samples: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    info_formats: dict[str, list[str]] = field(default_factory=dict)

    @property
    def annotations(self) -> list[str]:
        """The annotation sources present, for provenance and the manifest."""
        found = []
        if self.csq_fields:
            found.append("vep")
        if self.ann_fields:
            found.append("snpeff")
        for name, keys in (
            ("clinvar", ("CLNSIG",)),
            ("gnomad", ("GNOMAD_AF", *_POPMAX_INFO, *_NHOMALT_INFO)),
            ("pharmgkb", ("PHARMGKB",)),
            ("confidence", ("WGSE_CONF",)),
            ("sv_freq", ("SV_FREQ",)),
            ("acmg", (acmg.CLASS,)),
        ):
            if any(key in self.info_number for key in keys):
                found.append(name)
        csq = set(self.csq_fields or ())
        for name, score_keys in scores.ANNOTATIONS:
            if any(key in self.info_number or key in csq for key in score_keys):
                found.append(name)
        return found


def _subfield_names(description: str) -> list[str]:
    """VEP's 'Format: Allele|Consequence|...' or SnpEff's
    "Functional annotations: 'Allele | Annotation | ...'" as a list of names."""
    match = _FORMAT_LIST.search(description)
    names = match.group(1) if match else description.partition(":")[2]
    return [name.strip(" '\"") for name in names.split("|")]


def parse_header(lines: Iterable[str]) -> VcfHeader:
    """Parse ``##`` header lines and the ``#CHROM`` line."""
    header = VcfHeader()
    for line in lines:
        line = line.rstrip("\r\n")
        header.lines.append(line)
        if line.startswith("#CHROM"):
            header.samples = line.split("\t")[9:]
            continue
        match = _FIELD_LINE.match(line)
        if not match:
            continue
        kind, body = match.groups()
        attributes = dict(_ATTRIBUTE.findall(body))
        key, number = attributes.get("ID"), attributes.get("Number")
        if key is None or number is None:
            continue
        if kind == "FORMAT":
            header.format_number[key] = number
            continue
        header.info_number[key] = number
        description = attributes.get("Description", "").strip('"')
        if key == "CSQ":
            header.csq_fields = _subfield_names(description)
        elif key == "ANN":
            header.ann_fields = _subfield_names(description)
        elif _FORMAT_LIST.search(description):
            header.info_formats[key] = _subfield_names(description)
    return header


def _format_fraction(
    sample: Mapping[str, str],
    numbers: Mapping[str, str],
    alt_index: int,
    n_alts: int,
    split_index: tuple[int, int] | None,
) -> float | None:
    for key in ("VAF", "AF"):
        raw = _value(sample.get(key))
        if raw is None:
            continue
        number = numbers.get(key)
        if number in ("A", "R"):
            value = _per_allele(raw, number, alt_index)
        elif number in (None, ".", "1"):
            index, alt_count = split_index or (alt_index, n_alts)
            values = raw.split(",")
            value = values[index - 1] if len(values) == alt_count else None
        else:
            continue
        if (fraction := _fraction(value)) is not None:
            return fraction
    return None


_Stat = TypeVar("_Stat", int, float)


def _gnomad_stat(
    info: Mapping[str, str],
    numbers: Mapping[str, str],
    alt_index: int,
    info_keys: Sequence[str],
    csq: Mapping[str, str] | None,
    csq_keys: Sequence[str],
    parse: Callable[[str | None], _Stat | None],
) -> _Stat | None:
    for key in info_keys:
        if (
            key in info
            and (value := parse(_per_allele(info[key], numbers.get(key), alt_index)))
            is not None
        ):
            return value
    if csq:
        for key in csq_keys:
            if key in csq and (value := parse(csq[key])) is not None:
                return value
    return None


def _transcript_id(
    csq: Mapping[str, str], ann: Mapping[str, str], csq_consequence: str | None
) -> str | None:
    if csq_consequence or not ann:
        if _value(csq.get("Feature_type")) in (None, "Transcript"):
            return _value(csq.get("Feature"))
    elif _value(ann.get("Feature_Type")) in (None, "transcript"):
        return _value(ann.get("Feature_ID"))
    return None


def _severity(consequence: str) -> int:
    return min(
        (_SEVERITY.get(term, _UNRANKED) for term in consequence.split("&")),
        default=_UNRANKED,
    )


def pick_csq(entries: Sequence[Mapping[str, str]]) -> Mapping[str, str] | None:
    """The CSQ entry that describes an allele, as VEP's ``--pick`` would choose it:
    one VEP flagged with PICK, else the MANE Select transcript, then the
    canonical one, then a protein-coding one, then the most severe consequence."""
    if not entries:
        return None
    flagged = [e for e in entries if e.get("PICK") == "1"]
    if flagged:
        return flagged[0]
    return min(
        entries,
        key=lambda e: (
            not e.get("MANE_SELECT"),
            e.get("CANONICAL") != "YES",
            e.get("BIOTYPE") != "protein_coding",
            _severity(e.get("Consequence", "")),
        ),
    )


def zygosity(gt: str | None, alt_index: int) -> str | None:
    """The sample's copies of ALT number ``alt_index`` (1-based) in ``gt``."""
    if gt is None:
        return None
    alleles = _GT_SPLIT.split(gt)
    called = [a for a in alleles if a != "."]
    if not called:
        return "no_call"
    copies = called.count(str(alt_index))
    if copies == 0:
        return "not_carried"
    if len(alleles) == 1:
        return "hemizygous"
    return "hom_alt" if copies == len(alleles) else "het"


def _rsids(
    vcf_id: str, csq: Mapping[str, str] | None, pharmgkb: str | None
) -> str | None:
    ids = [i for i in vcf_id.split(";") if i.startswith("rs")]
    if not ids and csq:
        ids = [
            i
            for i in csq.get("Existing_variation", "").split("&")
            if i.startswith("rs")
        ]
    if not ids and pharmgkb:
        ids = [e.split("|", 1)[0] for e in pharmgkb.split(",")]
        ids = [i for i in dict.fromkeys(ids) if i.startswith("rs")]
    return ";".join(ids) or None


def _pharmgkb(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """(gene, best level, drugs) from `vcf pharmgkb`'s rsID|gene|level|category|drugs."""
    if not raw:
        return None, None, None
    genes: list[str] = []
    levels: list[str] = []
    drugs: list[str] = []
    for entry in raw.split(","):
        parts = entry.split("|")
        if len(parts) < 5:
            continue
        genes.append(parts[1])
        levels.append(parts[2])
        drugs.extend(d.strip() for d in parts[4].split("/") if d.strip())
    ranked = [lvl for lvl in _PHARMGKB_LEVELS if lvl in levels]
    gene = next((g for g in genes if g), None)
    return gene, (ranked[0] if ranked else None), "; ".join(sorted(set(drugs))) or None


def _max_probability(raw: str | None) -> float | None:
    values = [
        v for part in (raw or "").split(",") if (v := _to_float(part)) is not None
    ]
    return max(values) if values and all(0 <= v <= 1 for v in values) else None


def _info(raw: str) -> dict[str, str]:
    if raw in ("", "."):
        return {}
    info = {}
    for item in raw.split(";"):
        key, _, value = item.partition("=")
        info[key] = value
    return info


def variant_rows(
    records: Iterable[str],
    header: VcfHeader,
    *,
    sample_index: int | None = 0,
    carried_only: bool = True,
    pharmcat: Mapping[str, tuple[str, str]] | None = None,
) -> Iterator[Row]:
    """One row (in ``COLUMNS`` order) per ALT allele of each VCF data line.

    ``sample_index`` picks the sample whose genotype fills the genotype
    columns (None for a sites-only VCF). With ``carried_only``, ALTs that
    sample doesn't carry (0/0 calls, the other ALT of a 0/1 multi-allelic
    site, no-calls) are left out.
    """
    for row, _, _, _ in indexed_variant_rows(
        records,
        header,
        sample_index=sample_index,
        carried_only=carried_only,
        pharmcat=pharmcat,
    ):
        yield row


def indexed_variant_rows(
    records: Iterable[str],
    header: VcfHeader,
    *,
    sample_index: int | None = 0,
    carried_only: bool = True,
    pharmcat: Mapping[str, tuple[str, str]] | None = None,
) -> Iterator[tuple[Row, int, int, str | None]]:
    """Rows with record ordinal, 1-based ALT index and split provenance."""
    numbers = header.info_number
    csq_names = header.csq_fields or []
    ann_names = header.ann_fields or []
    scored = bool(scores.NAMES & set(header.annotations))
    csq_scores = scores.CsqScores.of(header.csq_fields)
    pipe_formats = {**scores.PIPE_FORMATS, **header.info_formats}
    unscored = (None,) * len(scores.SCORE_COLUMNS)
    pharmcat = pharmcat or {}
    for record_index, line in enumerate(records):
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) < 8:
            continue
        chrom, pos_text, vcf_id, ref, alt_text, qual_text, filt, info_text = fields[:8]
        alts = alt_text.split(",")
        if all(a in _NON_VARIANT_ALTS for a in alts):
            continue
        variant_alts = sum(a not in _NON_VARIANT_ALTS for a in alts) if scored else 0
        info = _info(info_text)
        gt = sample_depth = gq = gp = phase_set = ad_total = None
        sample: dict[str, str] = {}
        ad: list[int] = []
        sample_column = None if sample_index is None else 9 + sample_index
        has_sample = sample_column is not None and len(fields) > sample_column
        if sample_column is not None and has_sample:
            sample = dict(
                zip(
                    fields[8].split(":"),
                    fields[sample_column].split(":"),
                    strict=False,
                )
            )
            gt = _value(sample.get("GT"))
            if gt and "|" in gt and "/" not in gt:
                phase_set = _value(sample.get("PS"))
            sample_depth = _to_int(sample.get("DP"))
            ad = allele_depths(sample.get("AD"))
            ad_total = sum(ad) if ad else None
            if sample_depth is None:
                sample_depth = ad_total
            gq = _to_int(sample.get("GQ"))
            gp = _max_probability(sample.get("GP"))
        depth = sample_depth if sample_depth is not None else _to_int(info.get("DP"))
        pos = int(pos_text)
        qual = _to_float(qual_text)
        filter_value = _value(filt)
        csq_all = csq_entries(info.get("CSQ"), csq_names)
        ann_all = csq_entries(info.get("ANN"), ann_names)
        origin = split_origin(info.get(SPLIT_TAG))
        split_index = (
            (origin[2], len(origin[1]))
            if origin and len(alts) == 1 and len(origin[1]) > 1
            else None
        )
        if split_index:
            ad_total = count(sample.get(AD_TOTAL))

        for alt_index, alt in enumerate(alts, start=1):
            if alt in _NON_VARIANT_ALTS:
                continue
            zyg = None
            if has_sample:
                zyg = zygosity(gt, alt_index) or "no_call"
            if carried_only and zyg is not None and zyg not in CARRIED:
                continue
            ann_spellings: tuple[str, ...] = (alt,)
            pipe_spellings = ann_spellings
            index, n_alts = alt_index, len(alts)
            alt_fraction: float | None
            if ad_total and len(ad) == n_alts + 1 and ad[alt_index] <= ad_total:
                alt_fraction = ad[alt_index] / ad_total
            else:
                alt_fraction = _format_fraction(
                    sample, header.format_number, alt_index, n_alts, split_index
                )
            split = origin is not None and n_alts == 1
            if origin is not None and split:
                _, o_alts, index = origin
                original_alt = o_alts[index - 1]
                ann_spellings = (alt, original_alt)
                if scored:
                    # The normalized spelling can name another ALT's retained entries.
                    pipe_spellings = (
                        (original_alt,) if alt in o_alts else (original_alt, alt)
                    )
            csq_matches = csq_for_allele(csq_all, ref, alts, alt_index, origin)
            csq = pick_csq(csq_matches)
            ann_matches = for_allele(
                ann_all, "Allele", ann_spellings, index, n_alts, split=split
            )
            # SnpEff lists an allele's effects most severe first.
            ann = ann_matches[0] if ann_matches else {}
            picked = csq or {}
            csq_consequence = _value(picked.get("Consequence"))
            pgx = _per_allele(info.get("PHARMGKB"), numbers.get("PHARMGKB"), alt_index)
            pgx_gene, pgx_level, pgx_drugs = _pharmgkb(pgx)
            gene = (
                _value(picked.get("SYMBOL")) or _value(ann.get("Gene_Name")) or pgx_gene
            )
            gnomad = _to_float(
                _per_allele(info.get("GNOMAD_AF"), numbers.get("GNOMAD_AF"), alt_index)
            )
            if gnomad is None:
                gnomad = _csq_frequency(csq, *VEP_GNOMAD_AF)
            clnsig = _per_allele(info.get("CLNSIG"), numbers.get("CLNSIG"), alt_index)
            if clnsig is None and csq:
                clnsig = _value(csq.get("CLIN_SIG"))
            hgvs_p = _value(picked.get("HGVSp")) or _value(ann.get("HGVS.p"))
            diplotype, phenotype = pharmcat.get(gene or "", (None, None))
            clinical = {
                key: _per_allele(info.get(key), numbers.get(key), alt_index)
                for key in (ce.IDENTITY, "CLNREVSTAT", "CLNSIGCONF")
            }
            clinical["CLNSIG"] = clnsig
            review = ce.review_evidence(clinical)
            row: Row = (
                chrom,
                pos,
                ref,
                alt,
                _rsids(vcf_id, csq, pgx),
                gene,
                csq_consequence or _value(ann.get("Annotation")),
                _value(picked.get("IMPACT")) or _value(ann.get("Annotation_Impact")),
                _value(picked.get("HGVSc")) or _value(ann.get("HGVS.c")),
                unquote(hgvs_p) if hgvs_p else None,  # VEP writes '=' as %3D
                clnsig,
                _per_allele(info.get("CLNDN"), numbers.get("CLNDN"), alt_index),
                gnomad,
                gt,
                zyg,
                depth,
                gq,
                qual,
                filter_value,
                _per_allele(info.get("WGSE_CONF"), numbers.get("WGSE_CONF"), alt_index),
                _per_allele(
                    info.get("WGSE_CONF_REASONS"),
                    numbers.get("WGSE_CONF_REASONS"),
                    alt_index,
                ),
                pgx_level,
                pgx_drugs,
                diplotype,
                phenotype,
                _to_float(info.get("SV_AF")),
                _value(info.get("SV_FREQ")),
                int(IMPUTED_TAG in info),
                gp,
                clinical[ce.IDENTITY],
                clinical["CLNREVSTAT"],
                _to_int(str(review["stars"])),
                str(review["review_tier"]) if "CLNSIG" in info else None,
                clinical["CLNSIGCONF"],
                _value(info.get(ce.ORIGIN)),
                _value(info.get(ce.COMPONENTS)),
                *(
                    _per_allele(info.get(tag), numbers.get(tag), alt_index)
                    for tag in acmg.TAGS
                ),
                phase_set,
                alt_fraction,
                _transcript_id(picked, ann, csq_consequence),
                _gnomad_stat(
                    info, numbers, alt_index, _POPMAX_INFO, csq, _POPMAX_CSQ, _fraction
                ),
                _gnomad_stat(
                    info, numbers, alt_index, _NHOMALT_INFO, csq, _NHOMALT_CSQ, count
                ),
                _csq_frequency(csq, "AF"),
                _csq_frequency(csq, "MAX_AF"),
                # A record split from a multi-ALT one keeps REF and its own ALT.
                ad[0] if len(ad) == n_alts + 1 else None,
                ad[alt_index] if len(ad) == n_alts + 1 else None,
                *(
                    scores.allele_scores(
                        csq_scores,
                        info,
                        numbers,
                        alt_index,
                        variant_alts,
                        csq,
                        csq_matches,
                        partial(
                            allele_entries,
                            info,
                            pipe_formats,
                            pipe_spellings,
                            refreshed=(scores.SPLICEAI, alt)
                            if split and scores.SPLICEAI_MATCH in info
                            else None,
                        ),
                        gene,
                    )
                    if scored
                    else unscored
                ),
            )
            yield row, record_index, alt_index, info.get(SPLIT_TAG) if split else None
