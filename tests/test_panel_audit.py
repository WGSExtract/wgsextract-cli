"""Research panel genotype, reference evidence and phase."""

import csv
import gzip
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib
from tests.test_panel_spec import _audit_inputs, _check, _panel, _write
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.panel_calibration import CnvMatch, Opportunity, matched_null


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
        # #1622 only extends vcf table, not the panel audit export.
        assert not {"protein_position", "protein_position_end", "domains"}.intersection(
            audited.column_names
        )
        assert audited.schema.metadata[b"research_only"] == b"true"
        assert audited["decision"].to_pylist() == [
            "qualifying",
            "qualifying",
            "unscored",
        ]


@needs_htslib
def test_1630_lowercase_vcf_alleles_keep_watched_diplotype(tmp_path: Path) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    with gzip.open(vcf, "rt", encoding="utf-8") as stream:
        text = stream.read()
    lower = tmp_path / "lowercase.vcf"
    lower.write_text(
        text.replace("\tG\tA\t", "\tg\ta\t").replace("\tC\tT\t", "\tc\tt\t"),
        encoding="utf-8",
    )

    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel, vcf_input=Path(bgzip_index(lower)), research_panel=True
        )
    )

    report = next(o for o in result.outputs if o.role == "panel_audit")
    (watch,) = json.loads(Path(report.path).read_text(encoding="utf-8"))["haplotypes"]
    assert watch["status"] == "called"
    assert watch["diplotype"] == ["left", "right"]


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
@pytest.mark.parametrize("as_primary", [True, False])
def test_1287_corrupt_deepvariant_probe_reports_read_error(
    tmp_path: Path, as_primary: bool
) -> None:
    panel, vcf, gvcf = _audit_inputs(
        tmp_path, phase_set="1", undeclared_deepvariant=True
    )
    header = b"".join(
        line
        for line in gvcf.read_bytes().splitlines(keepends=True)
        if line.startswith(b"#")
    )
    bgzip = get_tool_path("bgzip")
    assert bgzip is not None
    header_blocks = subprocess.check_output([bgzip, "-c"], input=header)
    body_blocks = bytearray(
        subprocess.check_output(
            [bgzip, "-c"], input=b"chr1\t11796321\t.\tG\tA\t30\tPASS\t.\tGT\t0/1\n"
        )
    )
    # Keep the header and EOF readable; corrupt the CRC of the body block.
    body_blocks[-28 - 8] ^= 1
    corrupt = tmp_path / "corrupt.g.vcf.gz"
    corrupt.write_bytes(header_blocks[:-28] + body_blocks)
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=corrupt if as_primary else vcf,
            gvcf=None if as_primary else corrupt,
            research_panel=True,
        ),
        check=False,
    )
    assert result.exit_code != 0 and result.error
    assert str(corrupt) in result.error.message
    assert "checking DeepVariant reference blocks" in result.error.message
    assert "CRC32 checksum mismatch" in result.error.message
    assert not result.outputs


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


def _weighted_audit(
    tmp_path: Path,
    calls: list[tuple[str, str, str, str]],
    filters: dict[str, Any],
    dosage: dict[str, float] | None = None,
    *,
    split: bool = False,
) -> tuple[dict[str, Any], api.Result]:
    path, source, gvcf = _audit_inputs(tmp_path, phase_set="1")
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec.pop("sites")
    spec.pop("haplotypes")
    spec["defaults"] = filters
    if dosage is not None:
        spec["groups"][0]["dosage"] = dosage
    path.write_text(json.dumps(spec), encoding="utf-8")
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        header, _, columns = stream.read().partition("#CHROM")
    weighted = tmp_path / "scoring.vcf"
    weighted.write_text(
        header.replace("IMPACT|SYMBOL", "IMPACT|SYMBOL|CLIN_SIG")
        + '##FILTER=<ID=LowQual,Description="Low quality">\n'
        + '##ALT=<ID=DEL,Description="Symbolic deletion">\n'
        + "".join(
            f'##INFO=<ID={name},Number={number},Type=String,Description="{name}">\n'
            for name, number in [
                ("WGSE_CLINVAR_ID", "1"),
                ("WGSE_CLINVAR_GERMLINE", "A"),
                ("SVTYPE", "1"),
                ("CLNSIG", "A"),
                ("CLNREVSTAT", "A"),
                ("CLNSIGCONF", "A"),
                ("WGSE_CONF", "A"),
            ]
        )
        + "#CHROM"
        + columns.splitlines()[0]
        + "\n"
        + "".join(
            f"chr1\t{11796321 + i}\t.\tG\t{alt}\t50\t{flag}\t{info}\tGT:PS\t{sample}\n"
            for i, (alt, flag, info, sample) in enumerate(calls)
        ),
        encoding="utf-8",
    )
    if split:
        split_vcf = tmp_path / "scoring-split.vcf"
        subprocess.run(
            [
                get_tool_path("bcftools") or "bcftools",
                "norm",
                "-m",
                "-any",
                "--old-rec-tag",
                "OLD_MULTIALLELIC",
                "-o",
                str(split_vcf),
                str(weighted),
            ],
            capture_output=True,
            check=True,
        )
        weighted = split_vcf
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=path,
            vcf_input=Path(bgzip_index(weighted)),
            gvcf=gvcf,
            research_panel=True,
        )
    )
    report = next(o for o in result.outputs if o.role == "panel_audit")
    details = json.loads(Path(report.path).read_text(encoding="utf-8"))
    assert details["research_only"]
    return details["groups"][0], result


@pytest.mark.parametrize(
    ("score_order", "clause", "dosage", "problem"),
    [
        (["not_a_score"], {"min_revel": 0.5}, {"hom": 0.5}, "defaults.score_order[0]"),
        (["revel"], {"min_cadd_phred": 20}, {"cis": 1}, "defaults.qualify[0]"),
        (
            ["revel", "cadd_phred"],
            {"min_revel": 0.5, "min_cadd_phred": 20},
            {"trans": 0.5},
            "defaults.qualify[0]",
        ),
        (
            ["revel"],
            {"min_revel": 0.5, "weight": -0.1},
            {"het": 0},
            "defaults.qualify[0].weight",
        ),
        (
            ["revel"],
            {"min_revel": 0.5, "weight": 0},
            {"phase_unknown": 1.1},
            "groups[0].dosage.phase_unknown",
        ),
    ],
)
def test_1617_panel_check_rejects_invalid_fallback_rules_and_weights(
    tmp_path: Path,
    score_order: list[str],
    clause: dict[str, float],
    dosage: dict[str, float],
    problem: str,
) -> None:
    panel = _panel()
    panel["groups"][0].pop("overrides")
    panel["groups"][0]["dosage"] = dosage
    panel["defaults"].update(score_order=score_order, qualify=[clause])
    result = _check(_write(tmp_path, panel, ".toml"))
    assert result.exit_code == 1
    details = cast(dict[str, Any], result.details)
    assert problem in {finding["location"] for finding in details["problems"]}


@needs_htslib
def test_1617_weight_tiers_use_first_valid_score_and_preserve_missingness(
    tmp_path: Path,
) -> None:
    missense = "CSQ=A|missense_variant|MODERATE|MTHFR"
    high = "CSQ=A|stop_gained|HIGH|MTHFR"
    group, result = _weighted_audit(
        tmp_path,
        [
            ("A", ".", f"{missense};REVEL=0.9;CADD_PHRED=30", "0/1:."),
            ("A", "PASS", f"{missense};REVEL=0.6", "0/1:."),
            ("A", "PASS", f"{missense};REVEL=0.4;CADD_PHRED=30", "0/1:."),
            ("A", "PASS", f"{missense};CADD_PHRED=30", "0/1:."),
            ("A", "PASS", f"{missense};REVEL=1.2;CADD_PHRED=30", "0/1:."),
            ("A", "PASS", high, "0/1:."),
            ("A", "PASS", missense, "0/1:."),
            ("A", "LowQual", f"{missense};REVEL=0.9", "0/1:."),
            ("A", "PASS", f"{missense};REVEL=0.9;WGSE_CONF=low", "0/1:."),
            ("<DEL>", "PASS", "CSQ=<DEL>|stop_gained|HIGH|MTHFR;SVTYPE=DEL", "0/1:."),
            ("A", "PASS", f"{high};SVTYPE=DEL", "0/1:."),
        ],
        {
            "min_confidence": "moderate",
            "missing_confidence": "keep",
            "missing_filter": "keep",
            "score_order": ["revel", "cadd_phred"],
            "qualify": [
                {"min_revel": 0.5, "weight": 0.5},
                {"min_revel": 0.8, "weight": 0.75},
                {"min_cadd_phred": 25, "weight": 0.25},
                {"impacts": ["HIGH"], "weight": 0},
            ],
        },
    )
    (gene,) = group["genes"]
    assert [(a["pos"], a["clause_weight"]) for a in gene["qualifying"]] == [
        (11796321, 0.75),
        (11796322, 0.5),
        (11796324, 0.25),
        (11796325, 0.25),
        (11796326, 0),
    ]
    assert [(a["pos"], a["missing"]) for a in gene["unscored"]] == [
        (11796327, ["cadd_phred", "revel"]),
    ]
    assert group["burden"]["weighted_allele_copies"] == 1.75
    assert group["burden"]["qualifying_allele_copies"] == 5
    assert {"panel_missing_filter", "panel_missing_confidence"} <= {
        w.code for w in result.warnings
    }
    table = next(o for o in result.outputs if o.role == "panel_audit_table")
    with Path(table.path).open(encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        # #1660 adds protein columns only to vcf table, even when audit is weighted.
        assert not {"protein_position", "protein_position_end", "domains"}.intersection(
            reader.fieldnames or ()
        )
        rows = list(reader)
    assert all(None not in row for row in rows)
    assert [
        float(row["weighted_allele_copies"])
        for row in rows
        if row["decision"] == "qualifying"
    ] == [
        0.75,
        0.5,
        0.25,
        0.25,
        0,
    ]


@needs_htslib
@pytest.mark.parametrize(
    ("sites", "expected", "weighted_total"),
    [
        (["1|0:1", "1|0:1"], ["cis", "cis"], 0.5),
        (["1|0:1", "0|1:1", "1|0:1"], ["trans", "trans", "trans"], 2.25),
        (["1|0:1", "0|1:2", "1/.:."], ["phase_unknown"] * 3, 0.375),
        (["1/2:."], ["trans", "trans"], 1.5),
        (["1:.", "1/1:.", "0/1:."], ["haploid", "hom", "het"], 2.5),
    ],
)
def test_1617_dosage_classifies_each_alt_once(
    tmp_path: Path, sites: list[str], expected: list[str], weighted_total: float
) -> None:
    group, _ = _weighted_audit(
        tmp_path,
        [
            (
                "A,C" if gt.startswith("1/2") else "A",
                "PASS",
                "CSQ=A|stop_gained|HIGH|MTHFR,C|stop_gained|HIGH|MTHFR",
                gt,
            )
            for gt in sites
        ],
        {"qualify": [{"impacts": ["HIGH"]}]},
        {"het": 0.5, "hom": 0.5, "cis": 0.25, "trans": 0.75, "phase_unknown": 0.125},
    )
    (gene,) = group["genes"]
    assert [a["dosage_category"] for a in gene["qualifying"]] == expected
    assert gene["weighted_allele_copies"] == weighted_total


@needs_htslib
@pytest.mark.parametrize("direct", [True, False])
def test_1617_clinvar_veto_requires_allele_bound_nonconflicting_germline_evidence(
    tmp_path: Path, direct: bool
) -> None:
    csq = "CSQ=A|stop_gained|HIGH|MTHFR|benign"
    review = ";CLNREVSTAT=criteria_provided"
    bound = ";WGSE_CLINVAR_ID=123"

    def clinical(sig: str) -> str:
        mark = f";WGSE_CLINVAR_GERMLINE={sig}" if direct else ""
        return f"{csq};CLNSIG={sig}{review}{bound}{mark}"

    group, result = _weighted_audit(
        tmp_path,
        [
            ("A", "PASS", clinical("Benign"), "0/1:."),
            ("A", "PASS", clinical("Benign/Likely_benign"), "1/1:."),
            (
                "A",
                "PASS",
                f"{clinical('Benign')};CLNSIGCONF=Pathogenic(1)",
                "0/1:.",
            ),
            ("A", "PASS", clinical("Pathogenic"), "0/1:."),
            ("A", "PASS", f"{csq}{review}{bound}", "0/1:."),
            (
                "A,C",
                "PASS",
                f"{csq},C|stop_gained|HIGH|MTHFR|benign;CLNSIG=Likely_benign,Pathogenic{review},criteria_provided{bound}",
                "0/1:.",
            ),
        ],
        {"qualify": [{"impacts": ["HIGH"]}], "exclude_clinvar_benign": True},
    )
    (gene,) = group["genes"]
    assert [a["pos"] for a in gene["clinvar_benign_exclusions"]] == (
        [11796321, 11796322] if direct else []
    )
    assert gene["qualifying_allele_copies"] == (4 if direct else 7)
    assert (
        "panel_clinvar_annotation_missing" in {w.code for w in result.warnings}
    ) is not direct


@needs_htslib
def test_1617_split_record_does_not_make_copied_clinvar_id_allele_bound(
    tmp_path: Path,
) -> None:
    group, _ = _weighted_audit(
        tmp_path,
        [
            (
                "A,C",
                "PASS",
                "CSQ=A|stop_gained|HIGH|MTHFR,C|stop_gained|HIGH|MTHFR;"
                "WGSE_CLINVAR_ID=123;CLNSIG=Benign,Benign;"
                "CLNREVSTAT=criteria_provided,criteria_provided;WGSE_CLINVAR_GERMLINE=Benign,.",
                "1/2:.",
            )
        ],
        {"qualify": [{"impacts": ["HIGH"]}], "exclude_clinvar_benign": True},
        split=True,
    )
    (gene,) = group["genes"]
    assert [a["alt"] for a in gene["clinvar_benign_exclusions"]] == ["A"]
    assert [a["alt"] for a in gene["qualifying"]] == ["C"]


def test_1617_weighted_z_score_does_not_round_away_small_differences() -> None:
    regions = {
        gene: Opportunity("1", "autosome", f"NM_{gene}", 3, ((1, 3),), 3, 0)
        for gene in ("TARGET", "LOWER", "TIE")
    }
    stats = matched_null(
        ["TARGET"],
        {"TARGET"},
        regions,
        {"TARGET": 1, "LOWER": 3, "TIE": 1},
        bins=2,
        seed=7,
        null_sets=100,
        basis="cds_bp",
        weighted_scores={
            "TARGET": Fraction(1),
            "LOWER": 3 * Fraction("0.3333333333333333"),
            "TIE": Fraction(1),
        },
    )
    # Draws differ by 10^-16. The displayed mean rounds to 1, but the
    # target is above the true mean by about one null standard deviation.
    assert stats["null_mean"] == 1
    assert stats["z_score"] > 0.75


@pytest.mark.parametrize("rare_control", [False, True])
@pytest.mark.parametrize("weight", ["1", "1e-200", "5e-324"])
def test_1664_cnv_p_and_z_do_not_depend_on_drawing_rare_controls(
    rare_control: bool,
    weight: str,
) -> None:
    genes = ["TARGET", *(f"CONTROL{i}" for i in range(200))]
    regions = {
        gene: Opportunity("1", "autosome", f"NM_{gene}", 3, ((1, 3),), 3, 0)
        for gene in genes
    }
    stats = matched_null(
        ["TARGET"],
        {"TARGET"},
        regions,
        {},
        bins=2,
        seed=0,
        null_sets=100,
        basis="callable_cds_bp",
        weighted_scores={},
        cnv=CnvMatch(
            strata=dict.fromkeys(genes, (2, 5, "unmasked")),
            components={
                gene: (burden * Fraction(weight), Fraction())
                for gene, burden in zip(
                    genes, [2, int(rare_control), *([0] * 199)], strict=True
                )
            },
        ),
    )
    if rare_control:
        assert stats["upper_tail_p"] == pytest.approx(1 / 201)
        assert stats["null_mean"] == pytest.approx(float(Fraction(weight) / 200), abs=0)
        assert stats["z_score"] == pytest.approx(399 / 199**0.5)
    else:
        assert stats["upper_tail_p"] is None
        assert stats["z_score"] is None


@pytest.mark.parametrize(("events", "underpowered"), [(10, False), (1, True)])
def test_1664_cnv_power_depends_on_contributing_genes_not_weight(
    events: int,
    underpowered: bool,
) -> None:
    targets = [f"TARGET{i}" for i in range(10)]
    genes = [*targets, *(f"CONTROL{i}" for i in range(20))]
    opportunities = {
        gene: Opportunity("1", "autosome", f"NM_{gene}", 3, ((1, 3),), 3, 0)
        for gene in genes
    }

    def run(weight: Fraction) -> dict[str, Any]:
        return matched_null(
            targets,
            set(targets),
            opportunities,
            {},
            bins=2,
            seed=7,
            null_sets=100,
            basis="callable_cds_bp",
            weighted_scores={},
            cnv=CnvMatch(
                strata=dict.fromkeys(genes, (2, 5, "unmasked")),
                components={
                    gene: (
                        weight if index < len(targets) + events else Fraction(),
                        Fraction(),
                    )
                    for index, gene in enumerate(genes)
                },
            ),
        )

    full, scaled = run(Fraction(1)), run(Fraction(1, 10))
    assert full["null_mean_contributing_genes"] == events / 2
    assert scaled["null_mean_contributing_genes"] == events / 2
    assert full["underpowered"] is scaled["underpowered"] is underpowered
    assert full["z_score"] == scaled["z_score"]
    assert full["upper_tail_p"] == scaled["upper_tail_p"]
