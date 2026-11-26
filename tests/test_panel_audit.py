"""Research panel genotype, reference evidence and phase."""

import json
from pathlib import Path
from typing import Literal

import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_panel_spec import _audit_inputs
from wgsextract_cli import api


@needs_htslib
@pytest.mark.parametrize(
    (
        "phase_set",
        "second_gt",
        "phase",
        "biallelic",
        "watch_status",
        "delayed_reference",
    ),
    [
        ("1", "0|1", "trans", "biallelic", "called", False),
        ("1", "1|0", "cis", "one_copy", "unmatched", True),
        ("2", "0|1", "unknown", "possible_biallelic", "ambiguous_phase", False),
    ],
)
def test_1287_audit_distinguishes_mixed_scores_reference_and_phase(
    tmp_path: Path,
    phase_set: str,
    second_gt: str,
    phase: str,
    biallelic: str,
    watch_status: str,
    delayed_reference: bool,
) -> None:
    panel, vcf, gvcf = _audit_inputs(
        tmp_path,
        phase_set=phase_set,
        second_gt=second_gt,
        undeclared_deepvariant=True,
        delayed_reference=delayed_reference,
    )
    table_format: Literal["tsv", "parquet"] = "tsv" if phase_set == "1" else "parquet"
    pq = pytest.importorskip("pyarrow.parquet") if table_format == "parquet" else None
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=vcf,
            gvcf=gvcf,
            research_panel=True,
            table_format=table_format,
        )
    )
    report = next(o for o in result.outputs if o.role == "panel_audit")
    table = next(o for o in result.outputs if o.role == "panel_audit_table")
    details = json.loads(Path(report.path).read_text(encoding="utf-8"))
    assert details["research_only"] and "not a diagnosis" in details["disclaimer"]
    (gene,) = details["groups"][0]["genes"]
    assert [a["pos"] for a in gene["qualifying"]] == [11796321, 11796322]
    assert gene["qualifying"][0]["missing"] == ["gnomad_af"]
    assert gene["qualifying"][0]["gnomad_af"] is None
    assert [a["pos"] for a in gene["unscored"]] == [11796323]
    assert gene["qualifying_allele_copies"] == 2
    assert gene["phase"] == phase
    assert gene["biallelic_state"] == biallelic
    reference, gap, low = details["sites"]
    assert (
        reference["status"],
        reference["genotype"],
        reference["query_allele_copies"],
    ) == (
        "confident_reference",
        "0/0",
        0,
    )
    assert gap["status"] == "not_assessed" and gap["genotype"] is None
    assert low["status"] == "weak_evidence"
    (watch,) = details["haplotypes"]
    assert watch["status"] == watch_status
    if watch_status == "called":
        assert watch["diplotype"] == ["left", "right"]
    elif watch_status == "ambiguous_phase":
        assert watch["candidates"] == [["left", "right"]]
    else:
        assert "diplotype" not in watch
    assert table.metrics["rows"] == 3
    if pq:
        audited = pq.read_table(table.path)
        assert audited.schema.metadata[b"research_only"] == b"true"
        assert audited["decision"].to_pylist() == [
            "qualifying",
            "qualifying",
            "unscored",
        ]


@needs_htslib
def test_1287_deepvariant_variants_only_header_does_not_imply_reference_evidence(
    tmp_path: Path,
) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1", undeclared_deepvariant=True)
    result = api.panel.audit(
        api.PanelAuditParams(panel=panel, vcf_input=vcf, research_panel=True)
    )
    report = next(o for o in result.outputs if o.role == "panel_audit")
    audit = json.loads(Path(report.path).read_text(encoding="utf-8"))
    assert audit["reference_evidence"] is None
    assert audit["sites"][0]["status"] == "not_assessed"
    mistaken = api.panel.audit(
        api.PanelAuditParams(panel=panel, vcf_input=vcf, gvcf=vcf, research_panel=True),
        check=False,
    )
    assert mistaken.exit_code == 2
    assert mistaken.error and "reference evidence" in mistaken.error.message


@needs_htslib
def test_1287_deepvariant_blocks_work_in_primary_gvcf(tmp_path: Path) -> None:
    panel, _, gvcf = _audit_inputs(tmp_path, phase_set="1", undeclared_deepvariant=True)
    document = json.loads(panel.read_text(encoding="utf-8"))
    del document["groups"], document["haplotypes"]
    panel.write_text(json.dumps(document), encoding="utf-8")
    result = api.panel.audit(
        api.PanelAuditParams(panel=panel, vcf_input=gvcf, research_panel=True)
    )
    report = next(o for o in result.outputs if o.role == "panel_audit")
    audit = json.loads(Path(report.path).read_text(encoding="utf-8"))
    assert audit["reference_evidence"] == "input"
    assert audit["sites"][0]["status"] == "confident_reference"
