"""One flat row per variant allele, from a VCF and whatever annotations it carries.

`vcf table` turns a VCF into a table an agent, a spreadsheet or SQL can query
without VCF knowledge: one row per ALT allele, fixed typed columns, no nested
values. The annotations come from the INFO fields our own commands write:

- VEP's ``CSQ`` (`vep run`) or SnpEff's ``ANN``: gene, consequence, impact
  and HGVS of one transcript per allele (see ``pick_csq``), and rsIDs,
  ClinVar and gnomAD values when the VCF lacks the dedicated fields below.
- ``CLNSIG``/``CLNDN`` (`vcf clinvar`), ``GNOMAD_AF`` (`vcf gnomad`),
  ``PHARMGKB`` (`vcf pharmgkb`) and ``WGSE_CONF``/``WGSE_CONF_REASONS`` (depth-aware
  confidence labels, `vcf clinvar`/`vcf gnomad`/`vcf pharmcat`).
- ``SV_AF``/``SV_FREQ`` (`vcf sv-freq`): structural variants' population
  frequency and class (common, low, rare or novel).
- ``ACMG_CLASS``/``ACMG_CRITERIA``/``ACMG_TRANSCRIPT`` (`vcf acmg`): fastVEP's
  automated ACMG/AMP class, research only (core/acmg.py).
- ``GNOMAD_POPMAX_AF``/``GNOMAD_NHOMALT`` (`vcf gnomad` with a gnomAD VCF):
  gnomAD's grpmax frequency and homozygote count.
- ``REVEL``, ``am_pathogenicity``/``am_class``, ``CADD_RAW``/``CADD_PHRED``,
  ``SpliceAI``, ``PHYLOP`` and ``GERP`` (`vcf revel`, `vcf alphamissense`,
  `vcf cadd`, `vcf spliceai`, `vcf phylop`, `vcf gerp`), else VEP's plugin,
  dbNSFP, SIFT and PolyPhen fields: in-silico scores (core/variant_scores.py).

The sample's FORMAT fields give its genotype, read depths and allele
fraction (AD, else the caller's VAF or AF; see ``alt_fraction``) and, for a
phased genotype, its phase block (PS).

An annotation the VCF lacks leaves its column empty. Records are parsed one at
a time, so a whole genome streams through in constant memory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from urllib.parse import unquote

from wgsextract_cli.core import acmg
from wgsextract_cli.core import clinical_evidence as ce
from wgsextract_cli.core import variant_scores as scores
from wgsextract_cli.core.csq_alleles import (
    allele_entries,
    csq_entries,
    csq_for_allele,
    for_allele,
    split_origin,
)
from wgsextract_cli.core.imputed_vcf import IMPUTED_TAG
from wgsextract_cli.core.table_writer import Column
from wgsextract_cli.core.variant_confidence import SPLIT_TAG, alt_fraction

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
    Column("gnomad_af", REAL, "gnomAD allele frequency of this ALT (0-1)."),
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
    Column(
        "transcript_id",
        TEXT,
        "ID of the chosen transcript that consequence, impact and HGVS describe "
        "(VEP Feature, else SnpEff Feature_ID).",
    ),
    Column(
        "phase_set",
        TEXT,
        "Phase block (FORMAT/PS) of a phased genotype (with '|'): calls with the "
        "same phase_set on one chromosome are phased to each other.",
    ),
    Column(
        "alt_fraction",
        REAL,
        "Fraction of the sample's reads carrying this ALT (0-1): FORMAT/AD over "
        "the larger of its sum and FORMAT/DP, else the caller's FORMAT/VAF or AF.",
    ),
    Column("ad_ref", INTEGER, "The sample's reads supporting REF (FORMAT/AD)."),
    Column("ad_alt", INTEGER, "The sample's reads supporting this ALT (FORMAT/AD)."),
    Column(
        "gnomad_popmax_af",
        REAL,
        "gnomAD's highest frequency of this ALT among non-bottlenecked genetic "
        "ancestry groups (grpmax/popmax; INFO/GNOMAD_POPMAX_AF, else VEP's "
        "AFR/AMR/EAS/NFE/SAS frequencies).",
    ),
    Column(
        "gnomad_nhomalt",
        INTEGER,
        "Homozygous-ALT individuals in gnomAD (INFO/GNOMAD_NHOMALT, `vcf gnomad`).",
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
_VEP_GNOMAD_AF = ("gnomADg_AF", "gnomADe_AF", "gnomAD_AF")
# gnomAD's grpmax (popmax before v4) is the highest frequency among the
# genetic ancestry groups that aren't founder or bottlenecked populations:
# African, admixed American, East Asian, non-Finnish European and South Asian.
# VEP reports those groups' frequencies; genomes first, then exomes, like
# gnomad_af.
_GNOMAD_GRPMAX_GROUPS = ("AFR", "AMR", "EAS", "NFE", "SAS")
_VEP_GNOMAD_GRPMAX = tuple(
    tuple(f"{source}_{group}_AF" for group in _GNOMAD_GRPMAX_GROUPS)
    for source in ("gnomADg", "gnomADe", "gnomAD")
)
GNOMAD_POPMAX_TAG = "GNOMAD_POPMAX_AF"
GNOMAD_NHOMALT_TAG = "GNOMAD_NHOMALT"
_PHARMGKB_LEVELS = ("1A", "1B", "2A", "2B", "3", "4")

_INFO_LINE = re.compile(
    r'^##INFO=<ID=([^,>]+),Number=([^,>]+),.*?Description="(.*)"', re.DOTALL
)
_FORMAT_LIST = re.compile(r"Format:\s*(.+)$")
_GT_SPLIT = re.compile(r"[/|]")


@dataclass
class VcfHeader:
    """What the table needs from a VCF header."""

    info_number: dict[str, str] = field(default_factory=dict)
    csq_fields: list[str] | None = None
    ann_fields: list[str] | None = None
    # The subfields of other pipe-delimited INFO fields ("Format: A|B|...").
    info_formats: dict[str, list[str]] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def annotations(self) -> list[str]:
        """The annotation sources present, for provenance and the manifest."""
        found = []
        if self.csq_fields:
            found.append("vep")
        if self.ann_fields:
            found.append("snpeff")
        for name, key in (
            ("clinvar", "CLNSIG"),
            ("gnomad", "GNOMAD_AF"),
            ("pharmgkb", "PHARMGKB"),
            ("confidence", "WGSE_CONF"),
            ("sv_freq", "SV_FREQ"),
            ("acmg", acmg.CLASS),
        ):
            if key in self.info_number:
                found.append(name)
        csq = set(self.csq_fields or ())
        for name, keys in scores.ANNOTATIONS:
            if any(k in self.info_number or k in csq for k in keys):
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
        match = _INFO_LINE.match(line)
        if not match:
            continue
        key, number, description = match.groups()
        header.info_number[key] = number
        if key == "CSQ":
            header.csq_fields = _subfield_names(description)
        elif key == "ANN":
            header.ann_fields = _subfield_names(description)
        elif _FORMAT_LIST.search(description):
            header.info_formats[key] = _subfield_names(description)
    return header


def _value(raw: str | None) -> str | None:
    return None if raw in (None, "", ".") else raw


def _to_int(raw: str | None) -> int | None:
    raw = _value(raw)
    if raw is None:
        return None
    try:
        return round(float(raw))
    except ValueError:
        return None


def _to_float(raw: str | None) -> float | None:
    raw = _value(raw)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _csq_grpmax(csq: Mapping[str, str] | None) -> float | None:
    """gnomAD's grpmax from VEP's per-group frequencies: the highest of the
    first source (genomes, exomes, combined) with any. VEP joins co-located
    known variants' values with '&'; the highest is taken."""
    for keys in _VEP_GNOMAD_GRPMAX if csq else ():
        values = [
            f
            for key in keys
            for v in (csq or {}).get(key, "").split("&")
            if (f := _to_float(v)) is not None
        ]
        if values:
            return max(values)
    return None


def _sample_reads(
    sample: Mapping[str, str], alt_index: int
) -> tuple[int | None, int | None]:
    """REF's and this ALT's read counts from FORMAT/AD (a record split from a
    multi-allelic one keeps REF and its own ALT)."""
    counts = [_to_int(v) for v in (sample.get("AD") or "").split(",")]
    if len(counts) <= alt_index:
        return None, None
    return counts[0], counts[alt_index]


def _transcript_id(
    csq: Mapping[str, str] | None, ann: Mapping[str, str] | None
) -> str | None:
    """The chosen annotation's transcript, never a regulatory feature's ID."""
    if csq is not None:
        if csq.get("Feature_type", "Transcript") != "Transcript":
            return None
        return _value(csq.get("Feature"))
    if ann is not None and ann.get("Feature_Type", "transcript") == "transcript":
        return _value(ann.get("Feature_ID"))
    return None


def _per_allele(raw: str | None, number: str | None, alt_index: int) -> str | None:
    """This ALT's value of an INFO field (``alt_index`` is 1-based)."""
    if raw is None:
        return None
    if number in ("A", "R"):
        values = raw.split(",")
        i = alt_index - 1 if number == "A" else alt_index
        return _value(values[i]) if i < len(values) else None
    return _value(raw)


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
    source_samples: int | None = None,
) -> Iterator[Row]:
    """One row (in ``COLUMNS`` order) per ALT allele of each VCF data line.

    ``sample_index`` picks the sample whose genotype fills the genotype
    columns (None for a sites-only VCF). With ``carried_only``, ALTs that
    sample doesn't carry (0/0 calls, the other ALT of a 0/1 multi-allelic
    site, no-calls) are left out. ``source_samples`` is the sample count of
    the VCF the records' INFO was computed over, when they were subset to
    one sample (``bcftools view -s -I``); it defaults to the header's.
    """
    numbers = header.info_number
    csq_names = header.csq_fields or []
    ann_names = header.ann_fields or []
    pharmcat = pharmcat or {}
    # A VCF none of whose fields gives a score skips the per-ALT lookups.
    scored = bool(scores.NAMES & set(header.annotations))
    csq_scores = scores.CsqScores.of(header.csq_fields)
    pipe_formats = {**scores.PIPE_FORMATS, **header.info_formats}
    unscored = (None,) * len(scores.SCORE_COLUMNS)
    single_sample = (
        source_samples if source_samples is not None else len(header.samples)
    ) == 1
    for line in records:
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) < 8:
            continue
        chrom, pos_text, vcf_id, ref, alt_text, qual_text, filt, info_text = fields[:8]
        alts = alt_text.split(",")
        if all(a in _NON_VARIANT_ALTS for a in alts):
            continue
        info = _info(info_text)
        gt = sample_depth = gq = gp = phase_set = None
        sample: dict[str, str] = {}
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
            sample_depth = _to_int(sample.get("DP"))
            if sample_depth is None and _value(sample.get("AD")):
                ad = [_to_int(v) for v in sample["AD"].split(",")]
                if all(v is not None for v in ad):
                    sample_depth = sum(v for v in ad if v is not None)
            gq = _to_int(sample.get("GQ"))
            gp = _max_probability(sample.get("GP"))
            # PS names a phase block only for a phased genotype.
            if gt is not None and "|" in gt:
                phase_set = _value(sample.get("PS"))
        depth = sample_depth if sample_depth is not None else _to_int(info.get("DP"))
        pos = int(pos_text)
        qual = _to_float(qual_text)
        filter_value = _value(filt)
        csq_all = csq_entries(info.get("CSQ"), csq_names)
        variant_alts = sum(a not in _NON_VARIANT_ALTS for a in alts)
        ann_all = csq_entries(info.get("ANN"), ann_names)
        origin = split_origin(info.get(SPLIT_TAG))
        split = SPLIT_TAG in info
        # INFO/DP4 counts every sample's reads: only one sample's are wanted.
        read_info = info if single_sample else {}

        for alt_index, alt in enumerate(alts, start=1):
            if alt in _NON_VARIANT_ALTS:
                continue
            zyg = None
            if has_sample:
                zyg = zygosity(gt, alt_index) or "no_call"
            if carried_only and zyg is not None and zyg not in CARRIED:
                continue
            ann_spellings: tuple[str, ...] = (alt,)
            index, n_alts = alt_index, len(alts)
            split_alt = origin is not None and n_alts == 1
            if origin is not None and split_alt:
                o_ref, o_alts, index = origin
                ann_spellings = (alt, o_alts[index - 1])
            csq_matches = csq_for_allele(csq_all, ref, alts, alt_index, origin)
            csq = pick_csq(csq_matches)
            ann_matches = for_allele(
                ann_all, "Allele", ann_spellings, index, n_alts, split=split_alt
            )
            # SnpEff lists an allele's effects most severe first.
            ann = ann_matches[0] if ann_matches else None
            pgx = _per_allele(info.get("PHARMGKB"), numbers.get("PHARMGKB"), alt_index)
            pgx_gene, pgx_level, pgx_drugs = _pharmgkb(pgx)
            gene = (
                _value((csq or {}).get("SYMBOL"))
                or _value((ann or {}).get("Gene_Name"))
                or pgx_gene
            )
            gnomad = _per_allele(
                info.get("GNOMAD_AF"), numbers.get("GNOMAD_AF"), alt_index
            )
            if gnomad is None and csq:
                gnomad = next(
                    (v for k in _VEP_GNOMAD_AF if (v := _value(csq.get(k)))), None
                )
            clnsig = _per_allele(info.get("CLNSIG"), numbers.get("CLNSIG"), alt_index)
            if clnsig is None and csq:
                clnsig = _value(csq.get("CLIN_SIG"))
            hgvs_p = _value((csq or {}).get("HGVSp")) or _value(
                (ann or {}).get("HGVS.p")
            )
            diplotype, phenotype = pharmcat.get(gene or "", (None, None))
            clinical = {
                key: _per_allele(info.get(key), numbers.get(key), alt_index)
                for key in (ce.IDENTITY, "CLNREVSTAT", "CLNSIGCONF")
            }
            clinical["CLNSIG"] = clnsig
            review = ce.review_evidence(clinical)
            ad_ref, ad_alt = _sample_reads(sample, alt_index)
            popmax = _to_float(
                _per_allele(
                    info.get(GNOMAD_POPMAX_TAG),
                    numbers.get(GNOMAD_POPMAX_TAG),
                    alt_index,
                )
            )
            if popmax is None:
                popmax = _csq_grpmax(csq)
            yield (
                chrom,
                pos,
                ref,
                alt,
                _rsids(vcf_id, csq, pgx),
                gene,
                _value((csq or {}).get("Consequence"))
                or _value((ann or {}).get("Annotation")),
                _value((csq or {}).get("IMPACT"))
                or _value((ann or {}).get("Annotation_Impact")),
                _value((csq or {}).get("HGVSc")) or _value((ann or {}).get("HGVS.c")),
                unquote(hgvs_p) if hgvs_p else None,  # VEP writes '=' as %3D
                clnsig,
                _per_allele(info.get("CLNDN"), numbers.get("CLNDN"), alt_index),
                _to_float(gnomad),
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
                _transcript_id(csq, ann),
                phase_set,
                alt_fraction(alt_index, n_alts, sample, read_info, split)
                if sample
                else None,
                ad_ref,
                ad_alt,
                popmax,
                _to_int(
                    _per_allele(
                        info.get(GNOMAD_NHOMALT_TAG),
                        numbers.get(GNOMAD_NHOMALT_TAG),
                        alt_index,
                    )
                ),
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
                            ann_spellings,
                        ),
                        gene,
                    )
                    if scored
                    else unscored
                ),
            )
