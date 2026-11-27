"""Protein residues and domain overlaps already present in an annotated VCF."""

import sqlite3
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from tests.test_vcf_table import ANN, CSQ, HEADER, _record, _tsv
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command

_COLUMNS = ("protein_position", "protein_position_end", "domains")
_HEADER = HEADER.replace(
    f"Format: {CSQ}", "Format: Allele|Consequence|Feature|PICK|Protein_position|DOMAINS"
).replace(
    f"'{ANN}'",
    f"'{ANN} | cDNA.pos / cDNA.length | CDS.pos / CDS.length | AA.pos / AA.length'",
)


@needs_htslib
def test_1622_protein_fields_follow_the_picked_alt_and_transcript(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.vcf"
    source.write_text(
        _HEADER.replace("DOMAINS", "DOMAINS|ALLELE_NUM")
        + _record(
            "chr1", "100", ".", "AC", "AT,T", "40", "PASS",
            "CSQ=AT|missense_variant|ENST2|1|7/70|Pfam:PF1&Gene3D:3.1&"
            "Pfam:PF1&missing|1,AT|missense_variant|ENST1||91|Pfam:OTHER|1,"
            "T|frameshift_variant|ENST3|1|51||2",
            "GT", "1/2",
        )
        + _record(
            "chr1", "200", ".", "AC", "AT,T", "40", "PASS",
            "CSQ=T|missense_variant|ENST3|1|51|Pfam:OTHER"
            ";ANN=T|stop_gained|HIGH|GENE|G|transcript|NM_2|protein_coding|"
            "1/2|c.9G>T|p.Trp3Ter|9/250|9/210|3/70",
            "GT", "1/2",
        )
        + _record(
            "chr1", "250", ".", "AG", "AT,C", "40", "PASS",
            "ANN=T|missense_variant|MODERATE|GENE|G|transcript|NM_2|"
            "protein_coding|1/2|c.12G>T|p.Trp4Leu|12/250|12/210|4/70",
            "GT", "1/2",
        )
        + _record(
            "chr1", "270", ".", "AA", "AT", "40", "PASS",
            "CSQ=AT|missense_variant|ENST2|1|9|Pfam:PF1",
            "GT", "0/1",
        )
        + _record(
            "chr1", "300", ".", "C", "T", "40", "PASS",
            "ANN=T|stop_gained|HIGH|GENE|G|transcript|NM_2|protein_coding|"
            "1/2|c.9G>T|p.Trp3Ter|9/250|9/210|3/70",
            "GT", "0/1",
        ),
        encoding="utf-8",
    )  # fmt: skip
    split = tmp_path / "split.vcf"
    reference = write_fasta(
        tmp_path / "ref.fa",
        {"chr1": "A" * 100 + "C" + "A" * 99 + "C" + "A" * 49 + "G" + "A" * 48 + "C"},
    )
    # Left-normalizing AC>AT gives C>T, the literal ALT of its sibling.
    run_command([
        get_tool_path("bcftools"), "norm", "-f", str(reference), "-m", "-any", "--old-rec-tag",
        "OLD_MULTIALLELIC", "-o", str(split), str(source),
    ])  # fmt: skip
    out = tmp_path / "result"
    result = api.vcf.table(api.VcfTableParams(input=split, outdir=out))
    expected = [
        ("51", "51", ""),
        ("7", "7", "Pfam:PF1&Gene3D:3.1"),
        ("3", "3", ""),
        ("", "", ""),
        ("", "", ""),
        ("4", "4", ""),
        ("9", "9", "Pfam:PF1"),
        ("3", "3", ""),
    ]
    assert [
        tuple(row[name] for name in _COLUMNS)
        for row in _tsv(out / "split_variants.tsv").values()
    ] == expected
    with sqlite3.connect(out / "split_variants.sqlite") as db:
        rows = db.execute(
            "SELECT protein_position, protein_position_end, domains "
            "FROM variants ORDER BY rowid"
        ).fetchall()
    assert rows == [
        (51, 51, None),
        (7, 7, "Pfam:PF1&Gene3D:3.1"),
        (3, 3, None),
        (None, None, None),
        (None, None, None),
        (4, 4, None),
        (9, 9, "Pfam:PF1"),
        (3, 3, None),
    ]
    assert str(result.metrics["protein_annotation_fields"]).split(",") == [
        "CSQ/Protein_position",
        "ANN/AA.pos / AA.length",
        "CSQ/DOMAINS",
    ]
    pq = pytest.importorskip("pyarrow.parquet")
    api.vcf.table(
        api.VcfTableParams(
            input=split, outdir=out, table_format="parquet", no_sqlite=True
        )
    )
    parquet = pq.read_table(out / "split_variants.parquet", columns=list(_COLUMNS))
    assert [tuple(row.values()) for row in parquet.to_pylist()] == rows


@needs_htslib
def test_1622_coordinates_are_not_guessed_from_uncertain_or_other_annotations(
    tmp_path: Path,
) -> None:
    cases = [
        ("inframe_deletion", "8-10/70", ("8", "10")),
        ("inframe_insertion", "8-7", ("", "")),
        ("stop_lost", "71/70", ("71", "71")),
        ("missense_variant", "?-12/70", ("", "")),
        ("missense_variant", "4-8/nonsense", ("", "")),
        ("missense_variant", "9223372036854775808", ("", "")),
        ("synonymous_variant", "7", ("", "")),
        ("missense_variant", "", ("", "")),
    ]
    source = tmp_path / "source.vcf"
    source.write_text(
        _HEADER
        + "".join(
            _record(
                "chr1", str(i), ".", "A", "G", "40", "PASS",
                f"CSQ=G|{effect}|ENST1|1|{position}|"
                ",G|missense_variant|ENST2||200|Pfam:OTHER"
                ";ANN=G|missense_variant|MODERATE|GENE|G|transcript|NM_2|"
                "protein_coding|1/2|c.9A>G|p.Gln3Arg|9/250|9/210|3/70",
                "GT", "0/1",
            )
            for i, (effect, position, _) in enumerate(cases, start=1)
        )
        + _record(
            "chr1", "100", ".", "C", "G", "40", "PASS",
            "CSQ=T|missense_variant|ENST1|1|200|Pfam:OTHER",
            "GT", "0/1",
        )
        + _record(
            "chr1", "200", ".", "A", "AA,AAA", "40", "PASS",
            "CSQ=AA|frameshift_variant|ENST1|1|42|Pfam:OTHER",
            "GT", "1/2",
        )
        + _record(
            "chr1", "250", ".", "ANGT", "ANCT", "40", "PASS",
            "CSQ=C|missense_variant|ENST1|1|6|Pfam:PF1",
            "GT", "0/1",
        ),
        encoding="utf-8",
    )  # fmt: skip
    result = api.vcf.table(api.VcfTableParams(input=source, outdir=tmp_path))
    tsv = next(o.path for o in result.outputs if o.role == "variant_table")
    assert [
        tuple(row[name] for name in _COLUMNS) for row in _tsv(Path(tsv)).values()
    ] == [
        *[(*expected, "") for _, _, expected in cases],
        ("", "", ""),
        ("", "", ""),
        ("42", "42", "Pfam:OTHER"),
        ("6", "6", "Pfam:PF1"),
    ]


@needs_htslib
@pytest.mark.parametrize(
    ("ref", "alts", "tag", "spelling", "number", "owner"),
    [
        ("AAGT", "AACT,C", "CSQ", "C", "1", "AACT"),
        ("AAGT", "C,AACT", "CSQ", "C", "2", "AACT"),
        ("AAGT", "AACT,C", "CSQ", "C", "", None),
        ("AAGT", "AACT,AATT", "CSQ", "C", "", "AACT"),
        ("AAGT", "AACT,AATT", "CSQ", "AACT", "", "AACT"),
        ("AT", "A,ATTT", "CSQ", "-", "", "A"),
        ("A", "T", "ANN", "T-chr1:123456_A>G", "", "T"),
        ("A", "T,G", "ANN", "T", "", "T"),
    ],
    ids=[
        "csq-retained-first",
        "csq-retained-second",
        "csq-ambiguous",
        "csq-minimized",
        "csq-untrimmed",
        "csq-indel",
        "ann-compound",
        "ann-simple",
    ],
)
def test_1622_annotation_allele_identity_survives_splitting(
    tmp_path: Path,
    ref: str,
    alts: str,
    tag: str,
    spelling: str,
    number: str,
    owner: str | None,
) -> None:
    source = tmp_path / "source.vcf"
    header = _HEADER.replace("DOMAINS", "DOMAINS|ALLELE_NUM") if number else _HEADER
    consequence = "frameshift_variant" if spelling == "-" else "missense_variant"
    if tag == "CSQ":
        domain = "Pfam:PF1"
        annotation = f"CSQ={spelling}|{consequence}|ENST1|1|6|{domain}"
        if number:
            annotation += f"|{number}"
    else:
        domain = ""
        entry = (
            f"{spelling}|{consequence}|MODERATE|GENE|G|transcript|ENST1|"
            "protein_coding|1/2|c.16A>T|p.Thr6Ser|16/250|16/210|6/70"
        )
        if "," not in alts:
            entry = (
                "G-chr1:123456_A>T|stop_gained|HIGH|OTHER|G|transcript|ENST9," + entry
            )
        annotation = "ANN=" + entry
    source.write_text(
        header
        + _record(
            "chr1", "200", ".", ref, alts, "40", "PASS",
            annotation, "GT", "1/2" if "," in alts else "0/1",
        ),
        encoding="utf-8",
    )  # fmt: skip
    split = tmp_path / "split.vcf"
    run_command([
        get_tool_path("bcftools"), "norm", "-m", "-any", "--old-rec-tag",
        "OLD_MULTIALLELIC", "-o", str(split), str(source),
    ])  # fmt: skip
    for vcf in (source, split):
        api.vcf.table(api.VcfTableParams(input=vcf, outdir=tmp_path))
        rows = _tsv(tmp_path / f"{vcf.stem}_variants.tsv")
        columns = ("transcript_id", "consequence", *_COLUMNS)
        assert {
            alt: tuple(row[name] for name in columns) for (_, alt), row in rows.items()
        } == {
            alt: ("ENST1", consequence, "6", "6", domain) if alt == owner else ("",) * 5
            for alt in alts.split(",")
        }
