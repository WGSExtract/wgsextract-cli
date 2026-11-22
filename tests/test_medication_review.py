"""``report --pgx-context``: CPIC guidance for a person's listed medications,
gated on the evidence behind each pharmacogenomic call (#1176).

The CPIC database is the pinned release's rows for a few drugs
(tests/fixtures/cpic_mini), served by a counted download: the network is the
only boundary mocked. PharmCAT and Cyrius results are their manifests and
outputs, as the earlier PGx safety fixes (#842, #843, #844) left them; the
end-to-end case runs ``vcf pharmcat`` with real bcftools on a gVCF."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tests.htslib_helpers import needs_htslib
from tests.test_personal_report import _cli, _manifest, _touch
from tests.test_pharmcat import QUALITY_HEADER, _base, _release, _span_definition, _vcf
from tests.test_pharmcat import pharmcat_env as pharmcat_env
from wgsextract_cli import api
from wgsextract_cli.core import cpic_data
from wgsextract_cli.core import personal_report as pr
from wgsextract_cli.core.config import get_config_path, reload_settings
from wgsextract_cli.core.sample_workspace import SampleWorkspace

CPIC_MINI = Path(__file__).parent / "fixtures" / "cpic_mini" / "cpic_mini.sql.gz"
NOT_PRESCRIPTION = "Not a prescription; review with a clinician."


@dataclass
class ServedCpic:
    library: Path
    body: bytes
    downloads: list[str] = field(default_factory=list)


@pytest.fixture
def served_cpic(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> ServedCpic:
    """The pinned CPIC release is CPIC_MINI, served by a counted download into
    a fresh reference library; downloads still need --download."""
    served = ServedCpic(tmp_path_factory.mktemp("library"), CPIC_MINI.read_bytes())
    config = get_config_path()
    config.write_text(
        config.read_text() + f"reference_library = {json.dumps(str(served.library))}\n"
    )
    reload_settings()
    monkeypatch.setitem(
        cpic_data.PIN, "sha256", hashlib.sha256(served.body).hexdigest()
    )

    def download(url: str, dest: str, **_kwargs: Any) -> bool:
        served.downloads.append(url)
        Path(dest).write_bytes(served.body)
        return True

    monkeypatch.setattr(cpic_data, "download_file", download)
    return served


def _sample(tmp_path: Path) -> Path:
    folder = tmp_path / "HG002"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")
    workspace = SampleWorkspace(folder)
    workspace.record("build", _touch(folder / "HG002.vcf.gz"), "hg38",
                     method="VCF header", confidence="high")  # fmt: skip
    _touch(folder / "HG002.cram")
    return folder


Alternative = tuple[str, str, str]  # diplotype, phenotype, CPIC lookup key


def _pharmcat(
    folder: Path,
    genes: dict[str, list[Alternative]],
    labels: dict[str, tuple[str, str]],
    outside: tuple[str, ...] = (),
    truncated: bool = False,
) -> None:
    """A `vcf pharmcat` run: each gene's alternatives and the confidence label
    (and reasons) of its calls; ``truncated``: only a manifest listing part of
    them, without the confidence table."""
    reports = {
        gene: {
            "callSource": "OUTSIDE" if gene in outside else "MATCHER",
            "recommendationDiplotypes": [
                {
                    "allele1": {"name": diplotype.split("/")[0]},
                    "allele2": {"name": diplotype.split("/")[1]}
                    if "/" in diplotype
                    else None,
                    "phenotypes": [phenotype],
                    "lookupKey": [key] if key else [],
                }
                for diplotype, phenotype, key in alternatives
            ],
        }
        for gene, alternatives in genes.items()
    }
    phenotypes = folder / "HG002.phenotype.json"
    phenotypes.write_text(json.dumps({"geneReports": reports}))
    table = folder / "HG002.confidence.tsv"
    table.write_text(
        "# PharmCAT calls\nchrom\tpos\tref\talt\tgenotype\tzygosity\tconfidence\t"
        "reasons\tdepth\tgenotype_quality\talt_fraction\tgene\n"
        + "".join(
            f"chr1\t{i}\tC\tT\t0/1\thet\t{label}\t{reasons}\t30\t60\t0.5\t{gene}\n"
            for i, (gene, (label, reasons)) in enumerate(labels.items(), start=1)
        )
    )
    manifest = _manifest(
        folder,
        "HG002.phenotype.json",
        "vcf pharmcat",
        inputs=((folder / "HG002.vcf.gz", "input"),),
        outputs=((phenotypes, "pharmcat_phenotypes"),)
        + (() if truncated else ((table, "confidence_table"),)),
    )
    if truncated:
        document = json.loads(manifest.read_text())
        listed = [
            {"gene": gene, "confidence": label, "reasons": reasons.split("|") if reasons else []}
            for gene, (label, reasons) in labels.items()
        ]  # fmt: skip
        document["details"] = {"confidence": [
            {"role": "pgx_calls", "findings": listed, "findings_truncated": True}
        ]}  # fmt: skip
        manifest.write_text(json.dumps(document))


def _cyrius(folder: Path, **metrics: Any) -> None:
    _manifest(
        folder,
        "HG002_cyp2d6",
        "typing cyp2d6",
        inputs=((folder / "HG002.cram", "input"),),
        outputs=((_touch(folder / "HG002_cyp2d6.tsv"), "cyp2d6_calls"),),
        metrics={"median_depth": 31.0, **metrics},
    )


RESOLVED_CYRIUS = {
    "cyp2d6_genotype": "*1/*2",
    "cyp2d6_filter": "PASS",
    "cyp2d6_call_status": "resolved",
    "cyp2d6_call_info": "unique_match",
    "cyp2d6_raw_star_alleles": "*1_*2",
    "cyp2d6_cyp2d7_copy_number": 4,
}


def _context(folder: Path, *medications: str | dict[str, str]) -> Path:
    def item(m: str | dict[str, str]) -> str:
        if isinstance(m, str):
            return json.dumps(m)
        return "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in m.items()) + " }"

    path = folder.parent / "medications.toml"
    path.write_text(f"medications = [{', '.join(map(item, medications))}]\n")
    return path


def _report(folder: Path, *medications: str | dict[str, str]) -> Any:
    result = api.report.build(
        api.ReportParams(
            workspace=folder, pgx_context=_context(folder, *medications), download=True
        )
    )
    report = result.details
    assert report is not None
    jsonschema.validate(report, pr.load_schema())
    return report


def _review(folder: Path, *medications: str | dict[str, str]) -> dict[str, Any]:
    return _report(folder, *medications)["sections"]["medication_review"]


def _advisories(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each medication's (first) advisory finding."""
    out: dict[str, dict[str, Any]] = {}
    for finding in section["findings"]:
        if "advisory" in finding:
            out.setdefault(finding["advisory"]["medication"], finding)
    return out


# --- AC: completed safety states stay non-actionable through the report -------


@needs_htslib
@pytest.mark.parametrize(
    ("block", "status", "confidence"),
    [
        # A confident reference block over the whole multi-base definition.
        ("100\t110\t0/0:40:60", "advisory", "high"),
        # #842: a weak (GQ 2) block, and one leaving part of the span uncovered.
        ("100\t110\t0/0:30:2", "withheld", "low"),
        ("100\t105\t0/0:40:60|107\t110\t0/0:40:60", "withheld", "low"),
    ],
)
def test_1176_only_confident_reference_evidence_gives_a_cpic_advisory(
    tmp_path, pharmcat_env, served_cpic, block, status, confidence
):
    release = _release(tmp_path / "pharmcat")
    _span_definition(release)
    lines = []
    for part in block.split("|"):
        start, end, value = part.split("\t")
        lines.append(f"{{c}}\t{start}\t.\t{_base(int(start))}\t<NON_REF>\t.\tPASS\t"
                     f"END={end}\tGT:DP:GQ\t{value}\n")  # fmt: skip
    folder = _sample(tmp_path)
    vcf = _vcf(folder / "HG002.g.vcf.gz", "".join(lines), header=QUALITY_HEADER)
    api.vcf.pharmcat(
        api.VcfPharmcatParams(vcf_input=Path(vcf), pharmcat_dir=release, outdir=folder)
    )
    section = _review(folder, {"name": "clopidogrel", "population": "CVI ACS PCI"})
    finding = _advisories(section)["clopidogrel"]
    assert finding["advisory"]["status"] == status
    assert finding["confidence"] == confidence
    assert finding["disclaimer"] == NOT_PRESCRIPTION
    if status == "advisory":
        # The fake PharmCAT calls CYP2C19 *1/*17, a Rapid Metabolizer.
        assert finding["advisory"]["recommendation"]["phenotypes"] == {
            "CYP2C19": "Rapid Metabolizer"
        }
        assert "standard dose" in finding["value"]
    else:
        assert finding["advisory"]["recommendation"] is None
        assert "alternatives_differ" in finding["confidence_reasons"]


@pytest.mark.parametrize(
    ("cyrius", "pharmcat", "status", "reason"),
    [
        # A unique PASS call, given to PharmCAT as its outside call.
        (RESOLVED_CYRIUS, ("*1/*2", "Normal Metabolizer", "2.0"), "advisory", None),
        # #844: a PASS pick among candidates; a stale outside call stays unused.
        (
            {**RESOLVED_CYRIUS, "cyp2d6_genotype": "*1/*4",
             "cyp2d6_call_status": "unresolved", "cyp2d6_call_info": "pick_common_allele",
             "cyp2d6_raw_star_alleles": "*1_*4;*39_*4.009"},
            ("*1/*4", "Intermediate Metabolizer", "1.0"),
            "withheld",
            "outside_call_mismatch",
        ),
        # #844: CYP2D6+CYP2D7 copy number 8 (LowQ_high_CN): no outside call,
        # so PharmCAT has no CYP2D6 call.
        (
            {**RESOLVED_CYRIUS, "cyp2d6_genotype": "*1/*4x5",
             "cyp2d6_filter": "LowQ_high_CN", "cyp2d6_call_status": "unresolved",
             "cyp2d6_raw_star_alleles": "*1_*4", "cyp2d6_cyp2d7_copy_number": 8},
            None,
            "withheld",
            "no_labelled_calls",
        ),
    ],
)  # fmt: skip
def test_1176_cyrius_unique_pass_call_is_actionable_but_unresolved_calls_are_not(
    tmp_path, served_cpic, cyrius, pharmcat, status, reason
):
    folder = _sample(tmp_path)
    _cyrius(folder, **cyrius)
    if pharmcat:
        _pharmcat(folder, {"CYP2D6": [pharmcat]}, {}, outside=("CYP2D6",))
    else:
        _pharmcat(folder, {"CYP2D6": [("Unknown/Unknown", "No Result", "")]}, {})
    section = _review(folder, "codeine")
    finding = _advisories(section)["codeine"]
    assert finding["advisory"]["status"] == status
    (gene,) = [f for f in section["findings"] if "advisory" not in f]
    if status == "advisory":
        assert finding["confidence"] == gene["confidence"] == "high"
        assert finding["advisory"]["recommendation"]["classification"] == "Strong"
        return
    # Neither the picked genotype nor the D6+D7 copy number becomes a call.
    assert reason in finding["confidence_reasons"]
    assert gene["confidence"] in ("low", "not_assessed")
    assert section["genes"][0]["possible"] == []
    if pharmcat is None:
        assert "candidates *1_*4" in section["genes"][0]["baseline"]
        assert "copy number" not in json.dumps(section)


# --- AC: CPIC context adjustment, baseline and adjusted phenotype ------------


@pytest.mark.parametrize("paroxetine", [True, False])
def test_1176_paroxetine_phenoconversion_keeps_baseline_beside_adjusted_cyp2d6(
    tmp_path, served_cpic, paroxetine
):
    folder = _sample(tmp_path)
    _cyrius(folder, **RESOLVED_CYRIUS)
    _pharmcat(folder, {"CYP2D6": [("*1/*2", "Normal Metabolizer", "2.0")]}, {},
              outside=("CYP2D6",))  # fmt: skip
    report = _report(folder, "codeine", *(["paroxetine"] if paroxetine else []))
    section = report["sections"]["medication_review"]
    (gene,) = section["genes"]
    assert gene["baseline"] == "Normal Metabolizer"
    codeine = _advisories(section)["codeine"]["advisory"]
    if paroxetine:
        assert gene["possible"] == ["Intermediate Metabolizer", "Poor Metabolizer"]
        assert gene["rule"] == "cpic-paroxetine-cyp2d6-autoinhibition"
        assert gene["trigger"] == "paroxetine"
        assert "phenoconversion of normal metabolizers" in gene["quote"]
        assert gene["cpic_recommendation"]
        assert codeine["status"] == "context_review"
        # The guidance for the genetic phenotype, and what the effect could
        # lead to (codeine for a Poor Metabolizer: avoid).
        assert codeine["recommendation"]["phenotypes"] == {
            "CYP2D6": "Normal Metabolizer"
        }
        assert {"Poor Metabolizer"} <= {
            a["phenotypes"]["CYP2D6"] for a in codeine["alternatives"]
        }
        # PR review: both renderers show the status and what the change could
        # lead to, labelled as not guidance for this person.
        for text in (pr.render_markdown(report), pr.render_html(report)):
            assert "Review with your listed medications" in text
            assert "Not guidance for this person" in text
            assert "Avoid codeine use" in text
        # Paroxetine's own CPIC guidance already accounts for its effect.
        assert _advisories(section)["paroxetine"]["advisory"]["status"] == "advisory"
    else:
        assert gene["possible"] == [] and gene["rule"] is None
        assert gene["adjusted"].startswith("Normal Metabolizer (unchanged")
        assert codeine["status"] == "advisory" and codeine["alternatives"] == []


@pytest.mark.parametrize(
    ("alternatives", "status"),
    [
        (
            [("*1/*1", "Normal Metabolizer", "Normal Metabolizer"),
             ("*1/*38", "Normal Metabolizer", "Normal Metabolizer")],
            "advisory",
        ),
        (
            [("*1/*1", "Normal Metabolizer", "Normal Metabolizer"),
             ("*1/*2", "Intermediate Metabolizer", "Intermediate Metabolizer")],
            "withheld",
        ),
    ],
)  # fmt: skip
def test_1176_ambiguous_call_gives_guidance_only_when_its_alternatives_agree(
    tmp_path, served_cpic, alternatives, status
):
    folder = _sample(tmp_path)
    _pharmcat(folder, {"CYP2C19": alternatives}, {"CYP2C19": ("high", "")})
    section = _review(folder, {"name": "clopidogrel", "population": "NVI"})
    finding = _advisories(section)["clopidogrel"]
    assert finding["advisory"]["status"] == status
    if status == "withheld":
        assert finding["confidence"] == "low"
        assert {a["classification"] for a in finding["advisory"]["alternatives"]} == {
            "Strong",
            "Moderate",
        }
        assert "Withheld" in finding["value"]
    else:
        assert "standard dose" in finding["value"]


def test_1176_truncated_call_list_never_narrows_cpic_guidance(tmp_path, served_cpic):
    # PR review: without the confidence table, the manifest's partial call
    # list can't show a gene's weakest call, so a listed high call isn't enough.
    folder = _sample(tmp_path)
    _pharmcat(
        folder,
        {"CYP2C19": [("*1/*1", "Normal Metabolizer", "Normal Metabolizer")]},
        {"CYP2C19": ("high", "")},
        truncated=True,
    )
    report = _report(folder, {"name": "clopidogrel", "population": "NVI"})
    finding = _advisories(report["sections"]["medication_review"])["clopidogrel"]
    assert finding["advisory"]["status"] == "withheld"
    assert finding["advisory"]["recommendation"] is None
    pgx = report["sections"]["pharmacogenomics"]
    (gene,) = [f for f in pgx["findings"] if f["gene"] == "CYP2C19"]
    assert (gene["confidence"], gene["confidence_reasons"]) == (
        "not_assessed",
        ["calls_truncated"],
    )
    assert "calls_truncated" in {w["code"] for w in pgx["warnings"]}


def test_1176_guidance_needs_a_cpic_recommendation_for_every_possible_result(
    tmp_path, served_cpic
):
    # PR review: an alternative CPIC has no clopidogrel (NVI) recommendation
    # for must not drop out, leaving the other alternative's guidance unique.
    folder = _sample(tmp_path)
    _pharmcat(
        folder,
        {"CYP2C19": [("*1/*1", "Normal Metabolizer", "Normal Metabolizer"),
                     ("*1/*99", "Uncertain Function", "Uncertain Function")]},
        {"CYP2C19": ("high", "")},
    )  # fmt: skip
    finding = _advisories(
        _review(folder, {"name": "clopidogrel", "population": "NVI"})
    )["clopidogrel"]
    assert finding["advisory"]["status"] == "withheld"
    assert finding["advisory"]["recommendation"] is None
    assert any("CYP2C19 *1/*99" in n for n in finding["advisory"]["notes"])


@pytest.mark.parametrize(
    ("medication", "status", "fact"),
    [
        ("clopidogrel", "context_needed", "CVI ACS PCI, CVI non-ACS non-PCI, NVI"),
        ({"name": "clopidogrel", "population": "pediatrics"},
         "insufficient_evidence", "no clopidogrel recommendation for 'pediatrics'"),
        ({"name": "codeine", "population": "adults"},
         "advisory", "doesn't distinguish 'adults'"),
        ("not-a-cpic-drug", "insufficient_evidence", "isn't in CPIC v1.61.0"),
        ("warfarin", "insufficient_evidence", "dosing algorithm"),
        ("metformin", "insufficient_evidence", "no prescribing recommendation"),
    ],
)  # fmt: skip
def test_1176_missing_or_unsupported_context_says_so_instead_of_advising(
    tmp_path, served_cpic, medication, status, fact
):
    folder = _sample(tmp_path)
    _cyrius(folder, **RESOLVED_CYRIUS)
    _pharmcat(
        folder,
        {"CYP2D6": [("*1/*2", "Normal Metabolizer", "2.0")],
         "CYP2C19": [("*1/*1", "Normal Metabolizer", "Normal Metabolizer")]},
        {"CYP2C19": ("high", "")},
        outside=("CYP2D6",),
    )  # fmt: skip
    section = _review(folder, medication)
    name = medication if isinstance(medication, str) else medication["name"]
    finding = _advisories(section)[name]
    assert finding["advisory"]["status"] == status
    assert any(fact in note for note in finding["advisory"]["notes"])
    if status != "advisory":
        assert finding["advisory"]["recommendation"] is None


# --- AC: contraindication advisories ------------------------------------------


@pytest.mark.parametrize(
    ("label", "status"), [("high", "advisory"), ("low", "withheld")]
)
def test_1176_contraindication_needs_a_confident_disease_gene_call(
    tmp_path, served_cpic, label, status
):
    folder = _sample(tmp_path)
    _pharmcat(folder, {"G6PD": [("A-", "Deficient", "Deficient")]},
              {"G6PD": (label, "" if label == "high" else "low_gq")})  # fmt: skip
    result = api.report.build(
        api.ReportParams(
            workspace=folder,
            pgx_context=_context(folder, "rasburicase", "abacavir"),
            download=True,
        )
    )
    report = result.details
    assert report is not None
    section = report["sections"]["medication_review"]
    rasburicase = _advisories(section)["rasburicase"]
    assert rasburicase["advisory"]["status"] == status
    assert rasburicase["advisory"]["contraindication"] is (status == "advisory")
    if status == "advisory":
        assert rasburicase["value"] == "CPIC (Strong): Avoid use"
        assert rasburicase["advisory"]["recommendation"]["pmid"]
    # No HLA-B result: not assessed rather than guessed.
    assert _advisories(section)["abacavir"]["advisory"]["status"] == "not_assessed"
    markdown = pr.render_markdown(report)
    assert "not a prescription" in markdown.lower()
    assert "CPIC® database v1.61.0" in pr.render_html(report)


@pytest.mark.parametrize(
    ("outside", "keys", "status"),
    [
        ("*57:01/*08:01", "*57:01 positive", "advisory"),
        # An outside call that isn't the newest T1K call (an older run, or a
        # hand-written file) must not give HLA-B*57:01 carriers standard dosing.
        ("*44:02/*08:01", "*57:01 negative", "withheld"),
    ],
)
def test_1176_hla_outside_call_must_match_the_t1k_call_it_rests_on(
    tmp_path, served_cpic, outside, keys, status
):
    folder = _sample(tmp_path)
    _manifest(
        folder,
        "HG002_hla",
        "typing hla",
        inputs=((folder / "HG002.cram", "input"),),
        metrics={"HLA-A": "*02:01:01/*01:01:01", "HLA-B": "*57:01:01/*08:01:01"},
    )
    _pharmcat(folder, {"HLA-B": [(outside, keys, keys)]}, {}, outside=("HLA-B",))
    finding = _advisories(_review(folder, "abacavir"))["abacavir"]
    assert finding["advisory"]["status"] == status
    assert finding["advisory"]["contraindication"] is (status == "advisory")
    if status == "withheld":
        assert "outside_call_mismatch" in finding["confidence_reasons"]


# --- AC: local context, recorded release and context, no implicit downloads --


def test_1176_manifest_records_cpic_release_and_context_and_needs_download_consent(
    tmp_path, served_cpic, monkeypatch
):
    folder = _sample(tmp_path)
    _pharmcat(folder, {"G6PD": [("B", "Normal", "Normal")]}, {"G6PD": ("high", "")})
    context = _context(folder, "rasburicase")

    code, _ = _cli("report", "--workspace", str(folder), "--pgx-context", str(context))
    assert code == 2 and served_cpic.downloads == []

    def run() -> Any:
        return api.report.build(
            api.ReportParams(workspace=folder, pgx_context=context, download=True)
        )

    first = run()
    # Only the public release is fetched; no medication or sample data goes out.
    assert served_cpic.downloads == [cpic_data.PIN["url"]]
    roles = {record.role: record for record in first.inputs}
    assert roles["pgx_context"].path == str(context)
    assert Path(roles["cpic_data"].path).name == Path(cpic_data.PIN["url"]).name
    assert first.metrics["cpic_release"] == "v1.61.0"
    assert first.metrics["cpic_sha256"] == hashlib.sha256(served_cpic.body).hexdigest()
    assert first.details["sections"]["medication_review"]["context"]["medications"] == [
        {"name": "rasburicase", "population": None, "cpic_drug": "RxNorm:283821"}
    ]

    # A changed medication list changes the recorded context and the review.
    _context(folder, "rasburicase", "codeine")
    second = run()
    assert served_cpic.downloads == [cpic_data.PIN["url"]]  # cached, verified
    assert second.metrics["pgx_context_sha256"] != first.metrics["pgx_context_sha256"]
    assert "codeine" in _advisories(second.details["sections"]["medication_review"])

    # A new CPIC pin never reuses the cached release: it needs consent again.
    monkeypatch.setitem(cpic_data.PIN, "sha256", "0" * 64)
    code, _ = _cli("report", "--workspace", str(folder), "--pgx-context", str(context))
    assert code == 2


def test_1176_an_invalid_context_file_is_a_usage_error(tmp_path, served_cpic):
    folder = _sample(tmp_path)
    context = folder.parent / "medications.toml"
    context.write_text('medications = [{ name = "codeine", dose = "30 mg" }]\n')
    code, _ = _cli("report", "--workspace", str(folder), "--pgx-context", str(context))
    assert code == 2
