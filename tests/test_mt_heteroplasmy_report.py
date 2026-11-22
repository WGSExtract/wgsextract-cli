"""#1175: mtDNA heteroplasmy levels, confidence and reviewed variants in `report`.

Real mutserve (haplocheck's) calls reads on the real rCRS; hand-written
mutserve outputs cover what such reads can't easily show (references,
conflicts, missing files)."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tests.haplogrep_helpers import mini_tree as mini_tree
from tests.htslib_helpers import needs_htslib, needs_tools, sam_to_bam, write_fasta
from tests.test_1052_offline_defaults import blocked_network as blocked_network
from wgsextract_cli import api
from wgsextract_cli.core import personal_report as pr
from wgsextract_cli.core.ref_library.mitochondrial import pinned_sequences

RCRS = pinned_sequences()[0]
MT = "lineage mt-haplogroup"


def _reads(pos: int, n: int, alt: str | None = None, *, both: bool = True) -> list[str]:
    """``n`` 50 bp reads over rCRS ``pos`` (1-based), carrying ``alt`` there if
    given, on alternating strands (forward only unless ``both``)."""
    start = pos - 26
    seq = RCRS[start : start + 50]
    if alt:
        seq = seq[:25] + alt + seq[26:]
    tag = f"{pos}{alt or 'ref'}{'' if both else 'fwd'}"
    return [
        f"{tag}_{i}\t{16 if both and i % 2 else 0}\tchrM\t{start + 1}\t60\t50M\t*\t0"
        f"\t0\t{seq}\t{'I' * 50}"
        for i in range(n)
    ]


def _sites(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["hgvs"]: s for s in report["sections"]["mitochondrial"]["sites"]}


@needs_htslib
@needs_tools("haplogrep3", "java", "haplocheck")
def test_1175_report_keeps_mutserve_levels_at_reviewed_variants(
    tmp_path, mini_tree, blocked_network
):
    folder = tmp_path / "kit"
    folder.mkdir()
    ref = write_fasta(tmp_path / "ref.fa", {"chrM": RCRS})
    reads = [
        *_reads(1555, 140),
        *_reads(1555, 60, "G"),  # MT-RNR1 risk allele at 30%
        *_reads(3243, 120, "G"),  # homoplasmic
        *_reads(11778, 180),
        *_reads(11778, 20, "A", both=False),  # forward strand only
        *_reads(8344, 150),  # covered, no ALT
        *_reads(1494, 20),  # covered, too few reads to rule out
    ]
    bam = sam_to_bam(["@SQ\tSN:chrM\tLN:16569", *reads], folder / "kit.bam")
    result = api.lineage.mt_haplogroup(
        api.MtHaplogroupParams(
            input=Path(bam), ref=ref, outdir=folder, contamination=True
        )
    )
    kept = {o.role: Path(o.path).name for o in result.outputs}
    assert {"heteroplasmy_calls", "heteroplasmy_site_evidence"} <= kept.keys()
    # The haplogroup report still names the run's manifest.
    assert (folder / f"{kept['haplogroup_report']}.manifest.json").is_file()
    assert result.details["heteroplasmy"]["mt_reference"]["origin"] == "rCRS"
    assert result.metrics["mt_heteroplasmies"] >= 1

    report = pr.build_report(folder)
    section = report["sections"]["mitochondrial"]
    assert section["status"] == "ok"
    sites = _sites(report)
    risk, disease = sites["m.1555A>G"], sites["m.3243A>G"]
    # The supported 30% fraction survives; it isn't rounded to 0/1 or 1.
    assert (risk["state"], risk["confidence"]) == ("heteroplasmic", "high")
    assert risk["level"] == pytest.approx(0.3, abs=0.02)
    assert (disease["state"], disease["level"]) == ("homoplasmic", 1.0)
    weak = sites["m.11778G>A"]
    assert (weak["state"], weak["confidence"]) == ("weak_evidence", "low")
    assert "strand_bias" in weak["reasons"] and weak["level_rev"] == 0
    assert sites["m.8344A>G"]["state"] == "not_detected"
    low = sites["m.1494C>T"]
    assert (low["state"], low["reasons"]) == ("not_assessed", ["mt_low_depth"])
    assert sites["m.3460G>A"]["reasons"] == ["mt_no_coverage"]

    findings = {f["label"]: f for f in section["findings"]}
    mtrnr1 = findings["MT-RNR1 m.1555A>G"]
    assert "aminoglycoside" in mtrnr1["value"]
    variant = mtrnr1["variant"]
    assert (variant["genotype"], variant["zygosity"]) == (None, "heteroplasmic")
    assert variant["clinical_evidence"]["id"] == risk["clinvar_id"] == "9628"
    assert section["resource"]["clinvar_release"] == "2026-11-15"
    assert "PMID 34032273" in section["resource"]["cpic_citation"]
    assert set(findings) == {
        "MT-RNR1 m.1555A>G",
        "MT-TL1 m.3243A>G",
        "MT-ND4 m.11778G>A",
    }

    written = api.report.build(api.ReportParams(workspace=folder))
    files = {o.role: Path(o.path) for o in written.outputs}
    page = files["report_html"].read_text()
    assert "30.0%" in page and "not a prescribing instruction" in page
    assert "ClinVar release 2026-11-15" in files["report_markdown"].read_text()
    with files["findings_table"].open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    row = next(r for r in rows if r["label"] == "MT-RNR1 m.1555A>G")
    assert (row["section"], row["zygosity"], row["genotype"]) == (
        "mitochondrial",
        "heteroplasmic",
        "",
    )
    assert float(row["alt_fraction"]) == pytest.approx(0.3, abs=0.02)


# --- hand-written mutserve outputs --------------------------------------------

_RAW_HEADER = (
    "SAMPLE\tPOS\tREF\tTOP-FWD\tMINOR-FWD\tTOP-REV\tMINOR-REV\tCOV-FWD\tCOV-REV\t"
    "COV-TOTAL\tTYPE\tLEVEL\t%A\t%C\t%G\t%T\t%D\t%N\t%a\t%c\t%g\t%t\t%d\t%n\n"
)


def _raw_row(pos: int, ref: str, fwd: int, rev: int, alt: str, level: float) -> str:
    fractions = [
        str(level if b == alt else (1 - level) if b == ref else 0.0) for b in "ACGTDN"
    ]
    return (
        "\t".join(
            ["s", str(pos), ref, ref, alt, ref, alt, str(fwd), str(rev), str(fwd + rev)]
            + ["0", "0.0", *fractions, *fractions]
        )
        + "\n"
    )


def _mutserve_run(
    folder: Path,
    *,
    origin: str | None = "rCRS",
    calls: tuple[str, ...] = (),
    raw: tuple[str, ...] = (),
    contamination: str = "NO",
    keep_raw: bool = True,
) -> None:
    """A `lineage mt-haplogroup --contamination` run's manifest and files."""
    folder.mkdir(exist_ok=True)
    cram = folder / "kit.cram"
    cram.write_text("x")
    vcf = folder / "mutserve_heteroplasmies.vcf.gz"
    with gzip.open(vcf, "wt") as stream:
        stream.write(
            "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\t"
            "FORMAT\tsample.bam\n" + "".join(calls)
        )
    table = folder / "mutserve_heteroplasmies.raw.txt.gz"
    if keep_raw:
        with gzip.open(table, "wt") as stream:
            stream.write(_RAW_HEADER + "".join(raw))
    document = {
        "schema": "wgsextract.result/1",
        "command": MT,
        "status": "ok",
        "inputs": [{"path": str(cram), "role": "input"}],
        "outputs": [
            {"path": str(vcf), "role": "heteroplasmy_calls"},
            {"path": str(table), "role": "heteroplasmy_site_evidence"},
        ],
        "metrics": {"haplogroup": "H", "mt_contamination": contamination},
        "warnings": [],
        "details": {
            "heteroplasmy": {
                "caller": "mutserve",
                "version": "2.0.0-rc12",
                "mt_reference": {"origin": origin, "contig": "chrM"},
            }
        },
        "manifest": str(folder / "haplogrep_results.txt.manifest.json"),
        "provenance": {"finished": "2026-01-01T00:00:00+00:00"},
    }
    (folder / "haplogrep_results.txt.manifest.json").write_text(json.dumps(document))


def _call(pos: int, ref: str, alt: str, gt: str, level: float, depth: int) -> str:
    return f"chrM\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\t.\tGT:AF:DP\t{gt}:{level}:{depth}\n"


def test_1175_an_unconfirmed_mt_reference_assesses_nothing(tmp_path):
    folder = tmp_path / "kit"
    _mutserve_run(
        folder,
        origin=None,
        calls=(_call(1555, "A", "G", "1", 1.0, 900),),
        raw=(_raw_row(1555, "A", 450, 450, "G", 1.0),),
    )
    section = pr.build_report(folder)["sections"]["mitochondrial"]
    assert {s["state"] for s in section["sites"]} == {"not_assessed"}
    assert {w["code"] for w in section["warnings"]} == {"mt_reference_unconfirmed"}
    assert section["findings"] == []


@pytest.mark.parametrize(
    ("raw", "calls", "state", "reasons"),
    [
        # 40 reads support a 30% call, but can't make it high confidence.
        ((_raw_row(1555, "A", 20, 20, "G", 0.3),), (_call(1555, "A", "G", "0/1", 0.3, 40),),
         "heteroplasmic", ["mt_low_depth"]),
        # Below 10%, a call is weak evidence, not a negative.
        ((_raw_row(1555, "A", 500, 500, "G", 0.04),), (_call(1555, "A", "G", "0/1", 0.04, 1000),),
         "weak_evidence", ["mt_below_supported_level"]),
        # The read table's base disagrees with the reviewed variant's REF.
        ((_raw_row(1555, "C", 500, 500, "G", 0.3),), (), "not_assessed", ["mt_reference_conflict"]),
        # A call without reads behind it.
        ((), (_call(1555, "A", "G", "1", 1.0, 900),), "not_assessed", ["mt_evidence_conflict"]),
        # No ALT at 60 reads doesn't rule out a 10% heteroplasmy.
        ((_raw_row(1555, "A", 30, 30, "G", 0.0),), (), "not_assessed", ["mt_low_depth"]),
        ((_raw_row(1555, "A", 60, 60, "G", 0.0),), (), "not_detected", []),
    ],
)  # fmt: skip
def test_1175_low_depth_and_conflicting_evidence_are_not_negatives(
    tmp_path, raw, calls, state, reasons
):
    folder = tmp_path / "kit"
    _mutserve_run(folder, calls=calls, raw=raw)
    site = _sites(pr.build_report(folder))["m.1555A>G"]
    assert (site["state"], site["reasons"]) == (state, reasons)


def test_1175_two_alts_at_one_site_keep_their_own_levels(tmp_path):
    """PR review: mutserve gives one AF per allele, in GT order (2/1: ALT 2
    first); both m.8993 variants are heteroplasmic, not conflicts."""
    folder = tmp_path / "kit"
    fractions = {"C": "0.3", "G": "0.7"}
    raw = "\t".join(
        ["s", "8993", "T", "G", "C", "G", "C", "500", "500", "1000", "0", "0.0"]
        + [fractions.get(b, "0.0") for b in "ACGTDN"] * 2
    )
    call = "chrM\t8993\t.\tT\tC,G\t.\tPASS\t.\tGT:AF:DP\t2/1:0.7,0.3:1000\n"
    _mutserve_run(folder, calls=(call,), raw=(raw + "\n",))
    sites = _sites(pr.build_report(folder))
    levels = [
        (sites[v]["state"], sites[v]["level"]) for v in ("m.8993T>G", "m.8993T>C")
    ]
    assert levels == [("heteroplasmic", 0.7), ("heteroplasmic", 0.3)]


def test_1175_chm13_evidence_is_read_at_rcrs_positions(tmp_path):
    folder = tmp_path / "kit"
    # m.1555 is CHM13v2.0 chrM 979.
    _mutserve_run(
        folder,
        origin="CHM13v2.0's chrM",
        calls=(_call(979, "A", "G", "0/1", 0.25, 1000),),
        raw=(_raw_row(979, "A", 500, 500, "G", 0.25),),
    )
    report = pr.build_report(folder)
    site = _sites(report)["m.1555A>G"]
    assert (site["state"], site["level"]) == ("heteroplasmic", 0.25)
    (finding,) = report["sections"]["mitochondrial"]["findings"]
    assert finding["evidence"]["region"] == "chrM:979"
    assert (finding["variant"]["chrom"], finding["variant"]["pos"]) == ("rCRS", 1555)


def test_1175_heteroplasmy_in_a_contaminated_run_is_low_confidence(tmp_path):
    folder = tmp_path / "kit"
    _mutserve_run(
        folder,
        contamination="YES",
        calls=(_call(1555, "A", "G", "0/1", 0.3, 1000),),
        raw=(_raw_row(1555, "A", 500, 500, "G", 0.3),),
    )
    (finding,) = pr.build_report(folder)["sections"]["mitochondrial"]["findings"]
    assert (finding["confidence"], finding["confidence_reasons"]) == (
        "low",
        ["mt_contamination"],
    )


def test_1175_missing_evidence_is_reported_not_read_as_negative(tmp_path):
    folder = tmp_path / "kit"
    _mutserve_run(folder, keep_raw=False)
    section = pr.build_report(folder)["sections"]["mitochondrial"]
    assert "mt_evidence_missing" in {w["code"] for w in section["warnings"]}
    assert section["counts"]["not_detected"] == 0
    assert {s["reasons"][0] for s in section["sites"]} == {"mt_evidence_missing"}
