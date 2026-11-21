"""Y-STR calling (`lineage y-str`): read measurement, calling rules and the CLI."""

import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.ydna.ystr.analysis import (
    HOMOZYGOUS_READS,
    Call,
    call_multicopy,
    format_count,
    key,
    nist_value,
    write_ftdna_csv,
)
from wgsextract_cli.ydna.ystr.counter import TractReads, find_flank, measure
from wgsextract_cli.ydna.ystr.panel import markers, select, tracts

LEFT, RIGHT = "ACTTGTGTCAATAC", "ATGTATGTCTTTTC"


def _marker(name):
    return next(m for m in markers() if m.name == name)


def test_find_flank_tolerates_one_mismatch_but_not_ambiguity():
    assert find_flank("GG" + LEFT + "TT", LEFT) == 2
    assert find_flank("GG" + "T" + LEFT[1:] + "TT", LEFT) == 2
    assert find_flank("GG" + "TT" + LEFT[2:] + "TT", LEFT) is None
    assert find_flank(LEFT + "AGAT" + LEFT, LEFT) is None


def test_measure_reads_the_tract_or_a_lower_bound():
    tract = "AGAT" * 12
    assert measure("CC" + LEFT + tract + RIGHT + "GG", LEFT, RIGHT) == (48, None)
    # The read ends in the repeat: at least the bases after the left flank,
    # less a flank it may end inside.
    assert measure("CC" + LEFT + tract, LEFT, RIGHT) == (None, 48 - 14 + 1)
    assert measure(tract + RIGHT + "GG", LEFT, RIGHT) == (None, 48 - 14 + 1)
    # A right flank with two errors is there, just unusable: no bound.
    damaged = "GG" + RIGHT[2:]
    assert measure("CC" + LEFT + tract + damaged, LEFT, RIGHT) == (None, None)


def test_format_count_writes_partial_repeats_isfg_style():
    assert format_count(68, 4) == "17"
    assert format_count(70, 4) == "17.2"
    assert nist_value(Call(_marker("DYS441"), "called", "14")) == "15"


def _multicopy_reads(lengths: dict[int, int]) -> dict:
    """DYS385 reads at copy 1, given as {repeat count: reads}."""
    part = TractReads(lengths=Counter(), longest_read=150)
    copy1 = tracts("hg38")["DYS385"][0]
    assert copy1.copy_value is not None
    for count, n in lengths.items():
        part.lengths[copy1.hg38_length + (count - copy1.copy_value) * 4] = n
        part.overlapping += n
    empty = TractReads(longest_read=150)
    return {key(copy1): part, key(tracts("hg38")["DYS385"][1]): empty}


def _dys385(reads, min_reads=2):
    group = [_marker("DYS385a"), _marker("DYS385b")]
    calls = call_multicopy(group, tracts("hg38")["DYS385"], reads, min_reads)
    return [(c.status, c.value) for c in calls]


def test_multicopy_alleles_are_reported_in_ascending_order():
    assert _dys385(_multicopy_reads({14: 5, 11: 6})) == [
        ("called", "11"),
        ("called", "14"),
    ]


def test_multicopy_shared_allele_needs_enough_reads():
    assert _dys385(_multicopy_reads({14: HOMOZYGOUS_READS})) == [
        ("called", "14"),
        ("called", "14"),
    ]
    # Too few reads to rule out an unseen second copy, nor to tell a from b.
    assert _dys385(_multicopy_reads({14: HOMOZYGOUS_READS - 1})) == [
        ("low_support", ""),
        ("low_support", ""),
    ]
    # A read of another length (not stutter) may be the second copy.
    assert _dys385(_multicopy_reads({14: 8, 11: 1})) == [
        ("low_support", ""),
        ("low_support", ""),
    ]


def test_multicopy_alleles_need_the_requested_reads():
    assert _dys385(_multicopy_reads({11: 8, 14: 2}), min_reads=5) == [
        ("called", "11"),
        ("low_support", ""),
    ]


def test_multicopy_reads_too_scattered_for_any_allele_are_a_no_call():
    copy1 = tracts("hg38")["DYS385"][0]
    reads = _multicopy_reads({})
    # The two-read length is one repeat from a longer, stronger-weighted read.
    reads[key(copy1)].lengths.update({118: 2, 120: 1, 121: 1, 122: 1})
    assert _dys385(reads) == [("low_support", ""), ("low_support", "")]


def test_multicopy_reads_ending_in_a_longer_repeat_show_an_unspanned_copy():
    reads = _multicopy_reads({11: 8})
    copy1 = tracts("hg38")["DYS385"][0]
    # Two reads end inside a repeat longer than 11 + 1 units.
    reads[key(copy1)].bounds[copy1.hg38_length + 4 * 4] = 2
    assert _dys385(reads) == [("called", "11"), ("too_long", "")]


def test_ftdna_csv_follows_the_ftdna_column_order(tmp_path):
    calls = [Call(m, "called", "10") for m in reversed(select("Y-12"))]
    write_ftdna_csv(calls, tmp_path / "kit.csv")
    header, row = list(csv.reader((tmp_path / "kit.csv").open()))
    assert header[:5] == ["DYS393", "DYS390", "DYS19", "DYS391", "DYS385"]
    assert header[-1] == "DYS389II"
    assert row[4] == "10-10"


def _spanning_reads(marker: str, units: int, n: int, build="hg38") -> list[str]:
    """``n`` reads spanning ``marker`` with ``units`` repeats."""
    m = _marker(marker)
    (tract,) = tracts(build)[m.group]
    assert m.hg38_value is not None
    length = tract.hg38_length + (units - m.hg38_value) * m.period
    seq = "C" * 20 + tract.left + "A" * length + tract.right + "C" * 20
    pos = tract.start - 20 - len(tract.left)
    return [
        f"{marker}_{i}\t0\tchrY\t{pos}\t60\t{len(seq)}M\t*\t0\t0\t{seq}\t{'I' * len(seq)}"
        for i in range(n)
    ]


@needs_htslib
def test_y_str_cli_writes_calls_and_an_ftdna_row(tmp_path):
    reads = (
        _spanning_reads("DYS393", 13, 4)
        + _spanning_reads("DYS390", 24, 4)
        + _spanning_reads("DYS391", 11, 3)
        + _spanning_reads("DYS19", 15, 1)
    )
    bam = sam_to_bam(
        ["@SQ\tSN:chr1\tLN:248956422", "@SQ\tSN:chrY\tLN:57227415", *reads],
        tmp_path / "kit.bam",
    )
    run_wgsextract(
        "lineage", "y-str", "--input", bam, "--panel", "Y-12", "--build", "hg38",
    )  # fmt: skip
    header, row = list(csv.reader((tmp_path / "wgse" / "ystr/kit.ftdna.csv").open()))
    values = dict(zip(header, row, strict=True))
    assert (values["DYS393"], values["DYS390"], values["DYS391"]) == ("13", "24", "11")
    assert values["DYS19"] == ""
    table = list(
        csv.DictReader((tmp_path / "wgse" / "ystr/kit.ystr.tsv").open(), delimiter="\t")
    )
    dys19 = next(r for r in table if r["marker"] == "DYS19")
    assert (dys19["status"], dys19["read_alleles"]) == ("low_support", "15:1")
    dys392 = next(r for r in table if r["marker"] == "DYS392")
    assert dys392["status"] == "no_reads"


@needs_htslib
def test_y_str_cli_detects_the_build_from_the_header(tmp_path):
    reads = [
        read
        for marker, units in (("DYS393", 13), ("DYS390", 24), ("DYS391", 11))
        for read in _spanning_reads(marker, units, 3, build="t2t")
    ]
    bam = sam_to_bam(["@SQ\tSN:chrY\tLN:62460029", *reads], tmp_path / "kit.bam")
    run_wgsextract("lineage", "y-str", "--input", bam, "--panel", "Y-12")
    header, row = list(csv.reader((tmp_path / "wgse" / "ystr/kit.ftdna.csv").open()))
    values = dict(zip(header, row, strict=True))
    assert (values["DYS393"], values["DYS390"], values["DYS391"]) == ("13", "24", "11")


@needs_htslib
def test_y_str_cli_reports_a_sample_without_y_reads(tmp_path):
    bam = sam_to_bam(
        ["@SQ\tSN:chr1\tLN:248956422", "@SQ\tSN:chrY\tLN:57227415"],
        tmp_path / "kit.bam",
    )
    with pytest.raises(WGSExtractError, match="too few chrY reads"):
        run_wgsextract("lineage", "y-str", "--input", bam, "--build", "hg38")


def _issue_1155_reads(build, *, reference=False):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/ystr_issue_1155.json").read_text()
    )
    reads = []
    for row in fixture["records"]:
        if row["build"] != build:
            continue
        seq = row["reference_sequence"] if reference else row["sequence"]
        # Palindromic reads can have MAPQ 0 and either alignment orientation.
        mapq = 0 if row["group"] in ("DYS425", "DYS459") else 60
        cigar = f"{len(seq)}M"
        if row["group"] == "DYS425":
            insertion = len(seq) - len(row["reference_sequence"])
            if insertion:
                cigar = f"30M{insertion}I{len(seq) - 30 - insertion}M"
        for i in range(4):
            reads.append(
                f"{row['group']}_{row['part']}_{i}\t{16 if i % 2 else 0}\tchrY\t"
                f"{row['position']}\t{mapq}\t{cigar}\t*\t0\t0\t{seq}\t"
                f"{'I' * len(seq)}"
            )
    return reads


@needs_htslib
@pytest.mark.parametrize(
    "build,y_length", [("hg38", 57227415), ("hg19", 59373566), ("t2t", 62460029)]
)
def test_issue_1155_restored_strs_and_ambiguous_dys425_export(
    tmp_path, build, y_length
):
    reads = _issue_1155_reads(build)
    for marker, units in (
        ("DYS393", 13),
        ("DYS390", 24),
        ("DYS391", 11),
        ("DYS19", 15),
        ("DYS426", 12),
        ("DYS388", 12),
        ("DYS439", 12),
        ("DYS392", 13),
    ):
        reads += _spanning_reads(marker, units, 4, build)
    bam = sam_to_bam(
        [f"@SQ\tSN:chrY\tLN:{y_length}", *reads], tmp_path / "issue1155.bam"
    )
    run_wgsextract(
        "lineage", "y-str", "--input", bam, "--panel", "all", "--build", build
    )
    output = tmp_path / "wgse/ystr"
    table = {
        row["marker"]: row
        for row in csv.DictReader(
            (output / "issue1155.ystr.tsv").open(), delimiter="\t"
        )
    }
    expected = {
        "DYS448": "20",
        "DYS449": "30",
        "DYS459a": "11",
        "DYS459b": "13",
        "DYS589": "14",
    }
    assert {
        name: (table[name]["status"], table[name]["value"]) for name in expected
    } == {name: ("called", value) for name, value in expected.items()}
    # A non-reference DYS425 tract plus reference-matching paralogs does not
    # identify an assay allele when the flanks are shared.
    assert (table["DYS425"]["status"], table["DYS425"]["value"]) == ("not_callable", "")
    header, row = list(csv.reader((output / "issue1155.ftdna.csv").open()))
    exported = dict(zip(header, row, strict=True))
    assert exported["DYS459"] == "11-13"
    assert exported["DYS425"] == ""
    for name in ("DYS448", "DYS449", "DYS589"):
        assert exported[name] == expected[name]


@needs_htslib
def test_issue_1155_reference_paralogs_do_not_create_dys425_conflict(tmp_path):
    from wgsextract_cli.ydna.ystr.analysis import analyze

    bam = sam_to_bam(
        ["@SQ\tSN:chrY\tLN:62460029", *_issue_1155_reads("t2t", reference=True)],
        tmp_path / "reference.bam",
    )
    _, table = analyze(
        Path(bam),
        tmp_path / "result",
        build="t2t",
        selected=[_marker("DYS425"), _marker("DYS589")],
    )
    rows = {r["marker"]: r for r in csv.DictReader(table.open(), delimiter="\t")}
    assert (rows["DYS425"]["status"], rows["DYS425"]["value"]) == ("not_callable", "")
    # CHM13 has 12 repeat units plus a non-STR poly-T insertion: not 12.1.
    assert (rows["DYS589"]["status"], rows["DYS589"]["value"]) == ("called", "12")


@needs_htslib
@pytest.mark.parametrize("build", ["hg38", "hg19", "t2t"])
@pytest.mark.parametrize("repeat_insertion", [False, True])
def test_issue_1155_dys589_poly_t_boundary_is_not_a_fractional_allele(
    tmp_path, build, repeat_insertion
):
    from wgsextract_cli.ydna.ystr.analysis import analyze
    from wgsextract_cli.ydna.ystr.locations import revcomp

    fields = next(r for r in _issue_1155_reads(build) if r.startswith("DYS589_")).split(
        "\t"
    )
    seq = fields[9] if build != "t2t" else revcomp(fields[9])
    boundary = re.match(r"T+AG", seq[30:])
    assert boundary is not None
    end = 30 + boundary.end()
    seq = (
        seq[:30] + "T" * 8 + seq[30:end] + ("A" if repeat_insertion else "") + seq[end:]
    )
    if build == "t2t":
        seq = revcomp(seq)
    reads = []
    for i in range(4):
        fields[0] = f"boundary_{i}"
        fields[5], fields[9], fields[10] = f"{len(seq)}M", seq, "I" * len(seq)
        reads.append("\t".join(fields))
    bam = sam_to_bam(["@SQ\tSN:chrY\tLN:62460029", *reads], tmp_path / "boundary.bam")
    _, table = analyze(
        Path(bam), tmp_path / "result", build=build, selected=[_marker("DYS589")]
    )
    (row,) = list(csv.DictReader(table.open(), delimiter="\t"))
    assert (row["status"], row["value"]) == (
        "called",
        "14.1" if repeat_insertion else "14",
    )


def test_issue_1155_panel_builder_finds_inverted_and_duplicated_tracts():
    from scripts.build_ystr_panel import locate, locate_all, revcomp

    left = "CCGACGTAGCATGACCTAGGCTACGATCGATGCTAGCTACGAGTACGATCAGTACT"
    right = "ACGTCAGATCGATGCTACGACTAGCTACGATCGTAGCTACGACTAGCTAGCATGACT"
    original = left + "AGAGAT" * 8 + right
    start, end = len(left), len(original) - len(right)
    inverted = revcomp(left + "AGAGAT" * 10 + right)
    found = locate(original, start, end, inverted)
    assert found is not None
    lo, hi, _strand = found
    assert measure(
        inverted[lo - 14 : hi + 14],
        inverted[lo - 14 : lo],
        inverted[hi : hi + 14],
    ) == (60, None)
    doubled = inverted + "N" * 100 + inverted
    placed = locate_all(original, start, end, doubled)
    assert len(placed) == 2
    assert [doubled[lo:hi] for lo, hi, _strand in placed] == ["ATCTCT" * 10] * 2


def test_ystr_panel_builder_missing_sources_require_explicit_download(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_ystr_panel.py",
            "--hg38",
            "missing.fa",
            "--hg19",
            "missing.fa",
            "--t2t",
            "missing.fa",
            "--cache",
            str(tmp_path / "sources"),
            "--out",
            str(tmp_path / "panel"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Missing public Y-STR source" in result.stderr
    assert "--download" in result.stderr
    assert not list((tmp_path / "sources").iterdir())
