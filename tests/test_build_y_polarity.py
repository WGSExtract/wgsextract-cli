"""Issue #1168: independently verified polarity changes observable Y calls."""

import csv
import gzip
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.build_y_polarity import build
from scripts.build_yfull_tree import reference_polarity
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.ydna.yleaf_native.analysis import analyze
from wgsextract_cli.ydna.yleaf_native.data import extract_archive
from wgsextract_cli.ydna.yleaf_native.markers import Call, match_markers, read_markers
from wgsextract_cli.ydna.yleaf_native.polarity import correct_polarity
from wgsextract_cli.ydna.yleaf_native.predict import QcRules
from wgsextract_cli.ydna.yleaf_native.trees import YleafTree


def _module(
    tmp_path: Path,
    rows: str,
    independent: str,
    *,
    reverse_t2t: bool = False,
    legacy_overlay: bool = False,
    tree: dict[str, list[str]] | None = None,
    reference_bases: dict[int, str] | None = None,
) -> tuple[Path, Path]:
    data = tmp_path / "data"
    if tree is None:
        tree = {
            'ROOT (Y-Chromosome "Adam")': ["CT-M168", "C"],
            "CT-M168": ["R", "J-M267"],
            "R-M207": ["R-M343"],
            "R-M343": ["R1b"],
            "R": ["R-M207"],
            "R1b": [],
            "J-M267": ["J1"],
            "J1": [],
            "C": [],
        }
    tree_folder = data / "hg_prediction_tables"
    tree_folder.mkdir(parents=True)
    for name in ("ftdna", "isogg"):
        selected = dict(tree)
        if name == "ftdna" and legacy_overlay:
            selected["CF"] = ["R-M343"]
        (tree_folder / f"{name}_tree.json").write_text(json.dumps(selected))
    references = {}
    for genome in ("hg38", "hg19", "t2t"):
        folder = data / genome
        folder.mkdir(parents=True)
        target_rows = rows
        if genome == "t2t" and reverse_t2t:
            lifted = []
            for row in csv.reader(rows.splitlines(), delimiter="\t"):
                row[3] = str(60000001 - int(row[3]))
                row[5] = row[5].translate(str.maketrans("ACGT", "TGCA"))
                row[6] = row[6].translate(str.maketrans("ACGT", "TGCA"))
                row[4] = f"{row[5]}->{row[6]}"
                lifted.append("\t".join(row) + "\n")
            target_rows = "".join(lifted)
        for name in ("ftdna", "isogg"):
            (folder / f"{name}_positions_{genome}.txt").write_text(target_rows)
        target = list(csv.reader(target_rows.splitlines(), delimiter="\t"))
        bases = {int(row[3]): row[6] for row in target}
        for pos, base in (reference_bases or {}).items():
            if genome == "t2t" and reverse_t2t:
                pos = 60000001 - pos
                base = base.translate(str.maketrans("ACGT", "TGCA"))
            bases[pos] = base
        sequence = bytearray(b"N" * max(bases))
        for pos, base in bases.items():
            sequence[pos - 1] = ord(base)
        reference = folder / "chrY.fa"
        reference.write_bytes(b">chrY\n" + sequence + b"\n")
        subprocess.run(["samtools", "faidx", str(reference)], check=True)
        references[genome] = reference
        if genome != "hg38":
            chain = "hg38ToHg19" if genome == "hg19" else "hg38ToHs1"
            with gzip.open(folder / f"{chain}.over.chain.gz", "wt") as stream:
                strand = "-" if genome == "t2t" and reverse_t2t else "+"
                stream.write(
                    f"chain 1 chrY 60000000 + 0 60000000 chrY 60000000 {strand} 0 60000000 1\n60000000\n\n"
                )
    snapshot = tmp_path / "snps_hg38.csv"
    snapshot.write_text(
        "seqid,start,end,Name,allele_anc,allele_der,YCC_haplogroup,ISOGG_haplogroup\n"
        + independent
    )
    output = tmp_path / "module"
    spec = build(data, snapshot, output, references)
    installed = tmp_path / "installed"
    installed.mkdir()
    extract_archive(output / "y-polarity-2.tar.gz", installed, spec["files"])
    return data, installed / "polarity.json"


@pytest.mark.skipif(not shutil.which("bcftools"), reason="bcftools required")
@pytest.mark.parametrize("base,expected", [("A", "R-M343"), ("C", "NA")])
def test_issue1168_ct_reversal_ignores_legacy_overlay_and_changes_ftdna_haplogroup(
    tmp_path: Path, base: str, expected: str
) -> None:
    # CTS109 is C>A at GRCh38 chrY:2865577 in the pinned YBrowse snapshot;
    # the upstream FTDNA row reverses it. Other nodes contract the R1b path.
    rows = (
        "chrY\tCTS109\tCT-M168\t2865577\tA->C\tA\tC\n"
        "chrY\tM207\tR-M207\t15581983\tA->G\tA\tG\n"
        "chrY\tM343\tR-M343\t2887824\tC->A\tC\tA\n"
    )
    data, audit = _module(
        tmp_path,
        rows,
        "chrY,2865577,2865577,CTS109,C,A,CT-M168,CT-M168\n",
        legacy_overlay=True,
        reference_bases={2865577: "A"},
    )
    tree_file = tmp_path / "tree.json"
    tree_file.write_text(
        json.dumps(
            {
                'ROOT (Y-Chromosome "Adam")': ["CT-M168"],
                "CT-M168": ["R-M207"],
                "R-M207": ["R-M343"],
                "R-M343": [],
            }
        )
    )
    qc = tmp_path / "qc"
    qc.mkdir()
    (qc / "Intermediates.txt").write_text("CT-M168\nR-M207\nR-M343\n")
    for node in ("CT-M168", "R-M207", "R-M343"):
        (qc / f"{node}_int.txt").write_text("CT-M168\tD\n")
    table = data / "hg38/ftdna_positions_hg38.txt"
    selected = YleafTree(
        id="ftdna",
        name="FTDNA regression fixture",
        version="fixture",
        date="",
        license="fixture",
        source="synthetic R1b path",
        tree_file=tree_file,
        positions_files={"hg38": table},
        qc=QcRules(backbone=qc, tables=qc, ftdna_rules=True),
        origin="fixture",
    )
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chrY,length=60000000>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        f"chrY\t2865577\t.\tC\tA\t50\tPASS\t.\tGT:DP\t{'1' if base == 'A' else '0'}:30\n"
        "chrY\t2887824\t.\tC\tA\t50\tPASS\t.\tGT:DP\t1:30\n"
        "chrY\t15581983\t.\tA\tG\t50\tPASS\t.\tGT:DP\t1:30\n"
    )
    before = analyze(
        vcf,
        tmp_path / "before",
        tree=selected,
        build="hg38",
        no_ref_inference=True,
    )
    after = analyze(
        vcf,
        tmp_path / "after",
        tree=replace(selected, polarity_file=audit),
        build="hg38",
        no_ref_inference=True,
    )
    with before.open() as stream:
        old = next(csv.DictReader(stream, delimiter="\t"))
    with after.open() as stream:
        new = next(csv.DictReader(stream, delimiter="\t"))
    assert old["Hg"] != expected
    assert new["Hg"] == expected
    with (tmp_path / "after/sample/sample.out").open() as stream:
        evidence = {
            row["marker_name"]: row for row in csv.DictReader(stream, delimiter="\t")
        }
    assert evidence["CTS109"]["anc"] == "C"
    assert evidence["CTS109"]["der"] == "A"
    assert evidence["CTS109"]["state"] == ("D" if base == "A" else "A")
    provenance = json.loads((tmp_path / "after/run.json").read_text())
    assert provenance["marker_panel"]["polarity"]["corrected"] == 1


def test_issue1168_ancient_filter_follows_corrected_polarity_and_preserves_recurrence(
    tmp_path: Path,
) -> None:
    rows = (
        "chrY\ttransition\tR\t10\tT->C\tT\tC\n"
        "chrY\trecovered\tR\t20\tG->A\tG\tA\n"
        "chrY\trecurrent\tR\t30\tA->G\tA\tG\n"
        "chrY\trecurrent\tC\t30\tA->G\tA\tG\n"
        "chrY\tmixed-R\tR\t35\tA->G\tA\tG\n"
        "chrY\tmixed-C\tC\t35\tG->A\tG\tA\n"
        "chrY\tambiguous\tR\t40\tA->G\tA\tG\n"
    )
    data, audit = _module(
        tmp_path,
        rows,
        "chrY,10,10,transition,C,T,R,R\nchrY,20,20,recovered,A,G,R,R\n"
        "chrY,30,30,recurrent,G,A,R,R\n"
        "chrY,40,40,ambiguous,G,A,R,R\nchrY,41,42,ambiguous,G,GA,R,R\n",
        reference_bases={10: "T", 20: "G", 40: "G"},
    )
    table = data / "hg38/ftdna_positions_hg38.txt"
    markers, _ = correct_polarity(
        read_markers(table), table, audit, "ftdna", "hg38", ancient=True
    )
    passing, _ = match_markers(
        markers,
        {
            10: Call("T", 30, 100),
            20: Call("G", 30, 100),
            30: Call("G", 30, 100),
            35: Call("G", 30, 100),
            40: Call("G", 30, 100),
        },
    )
    assert [(row.marker.name, row.state) for row in passing] == [
        ("recovered", "D"),
        ("recurrent", "D"),
        ("recurrent", "D"),
        ("ambiguous", "D"),
    ]
    assert (
        json.loads(audit.read_text())["tables"]["ftdna/hg38"]["audit"][
            "ambiguous_independent_snp"
        ]
        == 1
    )
    rebuilt = tmp_path / "rebuilt"
    build(
        data,
        tmp_path / "snps_hg38.csv",
        rebuilt,
        {genome: data / genome / "chrY.fa" for genome in ("hg38", "hg19", "t2t")},
    )
    assert (rebuilt / "y-polarity-2.tar.gz").read_bytes() == (
        tmp_path / "module/y-polarity-2.tar.gz"
    ).read_bytes()
    table.write_text(rows + "chrY\tnew\tR\t50\tA->G\tA\tG\n")
    with pytest.raises(WGSExtractError, match="source table"):
        correct_polarity(
            read_markers(table), table, audit, "ftdna", "hg38", ancient=False
        )


def test_issue1168_reference_audit_does_not_flip_recurrences_or_unresolved_subclades() -> (
    None
):
    tree = {
        "root": ["CT", "A"],
        "CT": ["R1b", "J1"],
        "R1b": ["R-leaf"],
        "R-leaf": [],
        "J1": [],
        "A": [],
    }
    markers = [
        ("swapped-CT", "CT", 1, "A", "C"),
        ("swapped-A", "A", 2, "A", "C"),
        ("unresolved-R", "R-leaf", 3, "A", "C"),
        ("recurrent-CT", "CT", 4, "A", "C"),
        ("recurrent-A", "A", 4, "C", "A"),
    ]
    audit = reference_polarity(markers, "ACCC", tree, "R1b")
    assert audit["path_ref_ancestral"] == 1
    assert audit["off_path_ref_derived"] == 1
    assert audit["unresolved_reference_subclade"] == 1
    assert audit["recurrent_not_audited"] == 2


def test_issue1168_reverse_strand_correction_uses_target_build_alleles(
    tmp_path: Path,
) -> None:
    data, audit = _module(
        tmp_path,
        "chrY\tCTS109\tCT-M168\t2865577\tA->C\tA\tC\n",
        "chrY,2865577,2865577,CTS109,C,A,CT-M168,CT-M168\n",
        reverse_t2t=True,
        reference_bases={2865577: "A"},
    )
    table = data / "t2t/ftdna_positions_t2t.txt"
    markers, _ = correct_polarity(
        read_markers(table), table, audit, "ftdna", "t2t", ancient=False
    )
    passing, _ = match_markers(markers, {57134424: Call("T", 30, 100)})
    assert [
        (row.marker.ancestral, row.marker.derived, row.state) for row in passing
    ] == [("G", "T", "D")]


def test_issue1168_sister_branch_reversal_preserves_original_states(
    tmp_path: Path,
) -> None:
    data, audit = _module(
        tmp_path,
        "chrY\tFGC24780\tR\t10\tA->G\tA\tG\n"
        "chrY\tCTS109\tCT-M168\t20\tA->C\tA\tC\n"
        "chrY\tcontrol\tC\t30\tA->G\tA\tG\n",
        "chrY,10,10,FGC24780,G,A,C,C\nchrY,20,20,CTS109,C,A,CT-M168,CT-M168\n",
        reference_bases={10: "G", 20: "A"},
    )
    table = data / "hg38/ftdna_positions_hg38.txt"
    markers, _ = correct_polarity(
        read_markers(table), table, audit, "ftdna", "hg38", ancient=False
    )
    passing, _ = match_markers(
        markers, {10: Call("G", 30, 100), 20: Call("A", 30, 100)}
    )
    assert [(row.marker.name, row.state) for row in passing] == [
        ("FGC24780", "D"),
        ("CTS109", "D"),
    ]
    assert (
        json.loads(audit.read_text())["tables"]["ftdna/hg38"]["audit"][
            "branch_disagreement_not_corrected"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "branch,base,reason",
    [("R", "G", "path_ref_ancestral"), ("C", "A", "off_path_ref_derived")],
)
def test_issue1168_reference_lineage_gate_rejects_wrong_flips(
    tmp_path: Path, branch: str, base: str, reason: str
) -> None:
    data, audit = _module(
        tmp_path,
        f"chrY\twrong\t{branch}\t10\tA->G\tA\tG\n",
        f"chrY,10,10,wrong,G,A,{branch},{branch}\n",
        reference_bases={10: base},
    )
    table = data / "hg38/ftdna_positions_hg38.txt"
    markers, _ = correct_polarity(
        read_markers(table), table, audit, "ftdna", "hg38", ancient=False
    )
    passing, _ = match_markers(markers, {10: Call(base, 30, 100)})
    assert passing[0].state == ("D" if base == "G" else "A")
    assert (
        json.loads(audit.read_text())["tables"]["ftdna/hg38"]["audit"][
            f"{reason}_not_corrected"
        ]
        == 1
    )
