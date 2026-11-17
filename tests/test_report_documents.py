"""Generated writer components keep report/1's validation behavior."""

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.gen_report_schema import report_schema
from wgsextract_cli.core import clinical_evidence, personal_report

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json"


def component_validator(name: str) -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        {
            "$ref": f"#/$defs/{name}",
            "$defs": schema["$defs"],
        }
    )


def test_929_complete_writer_report_schema_cannot_drift() -> None:
    published = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert published == report_schema()
    subprocess.run(
        [sys.executable, "scripts/gen_report_schema.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_929_source_records_preserve_nullable_and_legacy_latest_attempts() -> None:
    validate = component_validator("reportSource")
    attempt = {
        "command": "",
        "status": "legacy-custom",
        "finished": None,
        "manifest": None,
    }
    assert validate.is_valid(attempt)
    assert validate.is_valid({**attempt, "latest_attempt": None})
    assert validate.is_valid({**attempt, "latest_attempt": attempt})
    bad_values: tuple[object, ...] = (True, 1, 1.5, [], {})
    for field in attempt:
        assert not validate.is_valid(
            {key: value for key, value in attempt.items() if key != field}
        )
        for value in bad_values:
            assert not validate.is_valid({**attempt, field: value})
            assert not validate.is_valid(
                {
                    **attempt,
                    "latest_attempt": {**attempt, field: value},
                }
            )
    for field in ("finished", "manifest"):
        assert validate.is_valid({**attempt, field: "legacy text"})
    for field in ("command", "status"):
        assert not validate.is_valid({**attempt, field: None})
    assert not validate.is_valid({**attempt, "unknown": 1})
    assert not validate.is_valid(
        {**attempt, "latest_attempt": {**attempt, "unknown": 1}}
    )
    assert not validate.is_valid({**attempt, "latest_attempt": {}})
    for value in ("text", True, 1, []):
        assert not validate.is_valid({**attempt, "latest_attempt": value})


def test_929_warning_extensions_remain_open_without_loosening_known_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validate = Draft202012Validator(
        {
            "$ref": "#/$defs/reportWarning",
            "$defs": schema["$defs"],
        }
    )
    assert validate.is_valid({"code": "custom", "message": "", "details": [1, None]})
    for value in (
        {},
        {"code": "custom"},
        {"code": 1, "message": ""},
        {"code": "", "message": None},
    ):
        assert not validate.is_valid(value)


def test_929_missing_commands_and_links_remain_closed_string_records() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for name, valid in (
        ("reportMissing", {"what": "", "command": ""}),
        ("reportLink", {"label": "", "url": ""}),
    ):
        validate = Draft202012Validator(
            {
                "$ref": f"#/$defs/{name}",
                "$defs": schema["$defs"],
            }
        )
        assert validate.is_valid(valid)
        assert not validate.is_valid({})
        assert not validate.is_valid({**valid, "unknown": True})
        for field in valid:
            assert not validate.is_valid({**valid, field: None})


def test_929_generated_finding_graph_keeps_legacy_optional_and_nullable_fields() -> (
    None
):
    validate = component_validator("finding")
    variant = {
        "chrom": "chr1",
        "pos": None,
        "ref": None,
        "alt": None,
        "genotype": None,
        "zygosity": None,
        "depth": None,
        "genotype_quality": None,
        "alt_fraction": None,
    }
    evidence = {"file": None, "region": None, "manifest": None}
    finding: dict[str, object] = {
        "section": "clinvar",
        "label": "A finding",
        "value": "",
        "detail": None,
        "gene": None,
        "note": None,
        "confidence": "not_assessed",
        "confidence_reasons": [],
        "confidence_basis": "",
        "evidence": evidence,
        "variant": variant,
        "links": [],
        "disclaimer": "Technical result, not diagnosis.",
    }
    assert validate.is_valid(finding)
    assert validate.is_valid({**finding, "variant": None})
    for field in finding:
        assert not validate.is_valid(
            {key: value for key, value in finding.items() if key != field}
        )
    for field, value in (
        ("label", ""),
        ("confidence", "future-value"),
        ("section", "overview"),
        ("disclaimer", "arbitrary text"),
        ("evidence", {**evidence, "future": True}),
        ("variant", {**variant, "future": True}),
        ("future", True),
    ):
        assert not validate.is_valid({**finding, field: value})
    for clinical in ({}, {"stars": None}, {"stars": 0}, {"stars": 4}):
        assert validate.is_valid(
            {**finding, "variant": {**variant, "clinical_evidence": clinical}}
        )
    for stars in (-1, 5, True):
        assert not validate.is_valid(
            {**finding, "variant": {**variant, "clinical_evidence": {"stars": stars}}}
        )
    for copies in (1, 2, None):
        assert validate.is_valid(
            {**finding, "variant": {**variant, "mnv_residual_copies": copies}}
        )
    for copies in (0, 3):
        assert not validate.is_valid(
            {**finding, "variant": {**variant, "mnv_residual_copies": copies}}
        )
    pgx = {
        "status": "ambiguous",
        "alternatives": [{"diplotype": "*1/*2", "phenotype": "intermediate"}],
        "phenotype_consistent": False,
        "source": "PharmCAT",
    }
    assert validate.is_valid({**finding, "pgx": pgx})
    for pgx_field, pgx_value in (
        ("status", "future-value"),
        ("source", "other"),
        ("alternatives", [{"diplotype": "*1/*2"}]),
    ):
        assert not validate.is_valid({**finding, "pgx": {**pgx, pgx_field: pgx_value}})


def test_929_generated_facts_and_qc_keep_extensions_and_arbitrary_json_values() -> None:
    metric = {
        "name": "coverage",
        "label": "Coverage",
        "value": {"regions": [1, None, "unknown"]},
        "text": "recorded",
    }
    fact = {
        **metric,
        "source": None,
        "method": None,
        "confidence": None,
        "file": None,
        "at": None,
    }
    for name, record in (("metric", metric), ("fact", fact)):
        validate = component_validator(name)
        assert validate.is_valid({**record, "future": [True]})
        assert not validate.is_valid({**record, "text": None})
        for field in record:
            assert not validate.is_valid(
                {key: value for key, value in record.items() if key != field}
            )


def test_929_numeric_clinvar_identity_stays_a_schema_valid_identifier() -> None:
    record = clinical_evidence.review_evidence(
        {clinical_evidence.IDENTITY: 123, "CLNREVSTAT": "reviewed_by_expert_panel"}
    )
    assert record["id"] == "123"
    component_validator("reportClinicalEvidence").validate(record)


def test_929_missing_sv_chromosome_does_not_emit_schema_invalid_null(
    tmp_path: Path,
) -> None:
    table = tmp_path / "sv.tsv"
    table.write_text(
        "pos\tend\tsvtype\tsv_freq\tsv_af\tgenotype\tfilter\n"
        "5\t6\tDEL\trare\t0.0001\t0/1\tPASS\n",
        encoding="utf-8",
    )
    genes = tmp_path / "genes.tsv"
    genes.write_text(
        "gene\tcategory\tinheritance\thg38_chrom\thg38_start\thg38_end\n"
        "GENEA\ttest\tAD\tchr1\t1\t10\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "sv.manifest.json"
    document = {
        "command": "vcf sv-freq",
        "status": "ok",
        "inputs": [],
        "outputs": [{"path": str(table), "role": "sv_frequencies"}],
        "metrics": {},
        "provenance": {"finished": "2025-01-01T00:00:00+00:00"},
    }
    manifest.write_text(json.dumps(document), encoding="utf-8")
    report = personal_report.build_report(
        tmp_path,
        sources=[personal_report.Source(manifest, document)],
        gene_list=genes,
    )
    variant = report["sections"]["structural_variants"]["findings"][0]["variant"]
    assert variant is not None
    assert variant["chrom"] == ""
    component_validator("reportVariant").validate(variant)


def test_929_complete_report_keeps_closed_headers_and_open_section_extensions(
    tmp_path: Path,
) -> None:
    genes = tmp_path / "genes.tsv"
    genes.write_text("gene\tcategory\tinheritance\nGENEA\ttest\tAD\n", encoding="utf-8")
    document = json.loads(
        json.dumps(personal_report.build_report(tmp_path, sources=[], gene_list=genes))
    )
    validate = Draft202012Validator(report_schema())
    validate.validate(document)
    for field in document:
        assert not validate.is_valid(
            {key: value for key, value in document.items() if key != field}
        )
    assert not validate.is_valid({**document, "future": True})
    assert not validate.is_valid(
        {**document, "sample": {**document["sample"], "future": True}}
    )
    assert not validate.is_valid(
        {**document, "sections": {**document["sections"], "future": {}}}
    )
    for section in document["sections"].values():
        section["future"] = {"unrecognized": [None, True]}
    validate.validate(document)
    legacy = {**document["sections"]}
    legacy.pop("ancestry")
    legacy.pop("carrier_screening")
    validate.validate({**document, "sections": legacy})
    for field in (
        "overview",
        "lineage",
        "pharmacogenomics",
        "clinvar",
        "structural_variants",
        "microarray",
    ):
        assert not validate.is_valid(
            {
                **document,
                "sections": {
                    key: value for key, value in legacy.items() if key != field
                },
            }
        )
    for field in ("facts", "qc"):
        overview = {
            key: value for key, value in legacy["overview"].items() if key != field
        }
        assert not validate.is_valid(
            {**document, "sections": {**legacy, "overview": overview}}
        )


def test_929_generated_counts_keep_required_counters_and_bounded_extensions() -> None:
    validate = component_validator("reportClinvarCounts")
    counts = {
        "carried": 0,
        "in_gene_list": 0,
        "outside_gene_list": 0,
        "high": 0,
        "moderate": 0,
        "low": 0,
    }
    validate.validate({**counts, "future_counter": 2})
    for field in counts:
        assert not validate.is_valid(
            {key: value for key, value in counts.items() if key != field}
        )
    assert not validate.is_valid({**counts, "future_counter": -1})
    assert not validate.is_valid({**counts, "high": -1})
    assert not validate.is_valid({**counts, "future_counter": "2"})
