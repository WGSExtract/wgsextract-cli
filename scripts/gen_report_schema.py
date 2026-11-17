"""Generate the complete report/1 contract from actual writer document types."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wgsextract_cli.core import report_documents as report  # noqa: E402
from wgsextract_cli.core.clinical_evidence import ClinicalEvidenceDocument  # noqa: E402
from wgsextract_cli.core.output_schema import Shape, output_schema  # noqa: E402

OUTPUT = ROOT / "src/wgsextract_cli/assets/schemas/wgsextract.report.1.schema.json"


def report_schema() -> dict[str, object]:
    shapes: dict[object, Shape] = {
        report.ReportDocument: Shape("report", additional=False),
        report.ReportSampleDocument: Shape("reportSample", additional=False),
        report.ReportSectionsDocument: Shape("reportSections", additional=False),
        report.ReportSectionDocument: Shape("section"),
        report.ReportOverviewDocument: Shape("reportOverview"),
        report.ReportAncestryDocument: Shape("reportAncestry"),
        report.ReportAncestryComponentDocument: Shape("reportAncestryComponent"),
        report.ReportClinvarDocument: Shape("reportClinvar"),
        report.ReportClinvarCountsDocument: Shape(
            "reportClinvarCounts", additional=report.NonnegativeCount
        ),
        report.ReportGeneDocument: Shape("reportGene", additional=False),
        report.ReportGeneListDocument: Shape("gene_list"),
        report.ReportAcmgDocument: Shape("reportAcmg"),
        report.ReportAcmgCountsDocument: Shape(
            "reportAcmgCounts", additional=report.NonnegativeCount
        ),
        report.ReportSvDocument: Shape("reportSv"),
        report.ReportSvThresholdsDocument: Shape("reportSvThresholds"),
        report.ReportMicroarrayDocument: Shape("reportMicroarray"),
        report.ReportMicroarrayFileDocument: Shape("reportMicroarrayFile"),
        report.ReportAttemptDocument: Shape("reportAttempt", additional=False),
        report.ReportSourceDocument: Shape("reportSource", additional=False),
        report.ReportWarningDocument: Shape("reportWarning"),
        report.ReportMissingDocument: Shape("reportMissing", additional=False),
        report.ReportLinkDocument: Shape("reportLink", additional=False),
        report.ReportFindingDocument: Shape("finding", additional=False),
        report.ReportEvidenceDocument: Shape("reportEvidence", additional=False),
        report.ReportVariantDocument: Shape("reportVariant", additional=False),
        ClinicalEvidenceDocument: Shape("reportClinicalEvidence", additional=False),
        report.ReportPgxDocument: Shape("reportPgx", additional=False),
        report.ReportPgxAlternativeDocument: Shape(
            "reportPgxAlternative", additional=False
        ),
        report.ReportFactDocument: Shape("fact"),
        report.ReportMetricDocument: Shape("metric"),
    }
    return output_schema(
        report.ReportDocument,
        shapes=shapes,
        schema_id="urn:wgsextract:schema:report:1",
        title="wgsextract.report/1",
        description="A sample's personal report: what earlier wgsextract runs found, by section, each finding with a confidence label, its evidence and the 'technical result, not diagnosis' disclaimer. Written by `wgsextract report` as <sample>_personal_report.json. See docs/personal-report.md.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(report_schema(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if actual != text:
            sys.stderr.writelines(
                difflib.unified_diff(
                    actual.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=str(OUTPUT.relative_to(ROOT)),
                    tofile="generated report schema",
                )
            )
            parser.exit(1, "Report schema is stale; run scripts/regen.py.\n")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
