"""``panel check`` and the ``wgsextract.panel/1`` research panel format (#1287)."""

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import tomli_w

from wgsextract_cli import api
from wgsextract_cli.core.errors import CodedError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

FIXTURES = Path(__file__).parent / "fixtures" / "panel"
SCHEMA = json.loads(
    (
        Path(__file__).parents[1]
        / "src/wgsextract_cli/assets/schemas/wgsextract.panel.1.schema.json"
    ).read_text(encoding="utf-8")
)
Panel = dict[str, Any]


def _panel() -> Panel:
    return json.loads((FIXTURES / "research_panel.json").read_text(encoding="utf-8"))


def _check(path: Path, **fields: Any) -> api.Result:
    return api.panel.check(api.PanelCheckParams(panel=path, **fields), check=False)


def _write(tmp_path: Path, document: Panel, suffix: str) -> Path:
    path = tmp_path / f"panel{suffix}"
    if suffix == ".toml":
        path.write_bytes(tomli_w.dumps(document).encode())
    else:
        path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _genes(result: api.Result) -> dict[str, dict[str, Any]]:
    (group,) = result.details["groups"]
    return {gene["given"]: gene for gene in group["genes"]}


@pytest.mark.parametrize("name", ["research_panel.toml", "research_panel.json"])
def test_valid_panel_resolves_its_genes_and_warns_about_the_rest(name):
    result = _check(FIXTURES / name)
    assert result.exit_code == 0
    details = result.details
    assert details["valid"] and details["problems"] == []
    assert details["research_only"] is True
    assert details["panel"]["id"] == "synthetic-test-panel"
    genes = _genes(result)
    # OTC is 354 amino acids: 1,065 coding bases with the stop codon.
    assert genes["OTC"]["cds_bp"] == 1065
    assert genes["OTC"]["cds_basis"] == "mane_select"
    assert genes["cps1"]["symbol"] == "CPS1"
    assert (genes["CYPIID6"]["status"], genes["CYPIID6"]["symbol"]) == (
        "alias",
        "CYP2D6",
    )
    assert genes["HGNC:613"]["symbol"] == "APOE"
    assert genes["NOT_A_GENE"]["status"] == "unknown"
    assert genes["UBC6"]["status"] == "ambiguous"
    assert set(genes["UBC6"]["candidates"]) == {"UBE2J1", "UBE2J2"}
    assert details["sites"][0]["genes"] == ["MTHFR"]
    codes = {w.code: w for w in result.warnings}
    assert codes["panel_gene_unresolved"].details["location"] == "groups[0].genes[5]"
    assert codes["panel_gene_ambiguous"].details["gene"] == "UBC6"
    assert codes["panel_gene_alias"].details["symbol"] == "CYP2D6"
    assert codes["panel_gene_noncoding"].details["gene"] == "MALAT1"
    assert {"panel_research_only", "panel_haplotype_pharmcat_gene"} <= set(codes)
    assert result.metrics["unresolved_genes"] == 2


def test_toml_and_json_panels_give_the_same_check():
    toml, json_ = (_check(FIXTURES / f"research_panel.{s}") for s in ("toml", "json"))
    for result in (toml, json_):
        for key in ("path", "format", "sha256"):
            del result.details["panel"][key]
    assert toml.details == json_.details
    assert [w.code for w in toml.warnings] == [w.code for w in json_.warnings]


def test_group_filters_override_the_defaults():
    (group,) = _check(FIXTURES / "research_panel.toml").details["groups"]
    filters = group["filters"]
    assert filters["max_af"] == 0.001
    assert filters["max_popmax_af"] == 0.01
    # An override's qualify replaces the defaults' clauses whole.
    assert filters["qualify"] == [{"min_spliceai": 0.2}]
    assert filters["missing_scores"] == "unscored"


def _set(path: str, value: Any) -> Callable[[Panel], None]:
    def edit(panel: Panel) -> None:
        *parents, last = path.split("/")
        node: Any = panel
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        if value is _DELETE:
            del node[int(last) if last.isdigit() else last]
        else:
            node[int(last) if last.isdigit() else last] = value

    return edit


_DELETE = object()
_HAPLOTYPE_SITE = "GRCh38:chr10:94781859:G"

# code, location, edit, and whether the JSON Schema alone refuses it too: one
# or more of each class of problem.
CASES: list[tuple[str, str, Callable[[Panel], None], bool]] = [
    ("unknown_key", "defaults.max_maf", _set("defaults/max_maf", 0.01), True),
    ("unknown_key", "groups[0].gene", _set("groups/0/gene", "OTC"), True),
    ("missing_key", "version", _set("version", _DELETE), True),
    ("missing_key", "groups[0].genes", _set("groups/0/genes", _DELETE), True),
    ("wrong_type", "defaults.max_af", _set("defaults/max_af", "0.01"), True),
    ("wrong_type", "defaults.pass_only", _set("defaults/pass_only", 1), True),
    ("wrong_type", "calibration.bins", _set("calibration/bins", 2.5), True),
    ("bad_value", "defaults.max_af", _set("defaults/max_af", 3), True),
    ("bad_value", "defaults.min_confidence", _set("defaults/min_confidence", "x"), True),
    ("bad_value", "defaults.qualify[0]", _set("defaults/qualify/0", {}), True),
    ("bad_value", "groups[0].genes", _set("groups/0/genes", []), True),
    ("bad_value", "sites[0].locus", _set("sites/0/locus", "chr1:11796321:G>A"), True),
    ("bad_value", "id", _set("id", "Has Spaces"), True),
    ("bad_value", "sites[0].locus", _set("sites/0/locus", "GRCh38:chr1:5:G>G"), False),
    ("bad_value", "haplotypes[0].alleles.a b", _set("haplotypes/0/alleles/a b", ["G", "C"]), False),
    ("duplicate", "defaults.qualify[0].impacts", _set("defaults/qualify/0/impacts", ["HIGH", "HIGH"]), True),
    ("duplicate", "sites[0].id", _set("sites/0/id", "group_a"), False),
    ("duplicate", "groups[0].genes[1]", _set("groups/0/genes/1", "otc"), False),
    ("duplicate", "haplotypes[0].alleles.c", _set("haplotypes/0/alleles/c", ["G", "A"]), False),
    ("unsupported_schema", "schema", _set("schema", "wgsextract.panel/2"), True),
    ("recessive_not_in_group", "groups[0].recessive[0]", _set("groups/0/recessive", ["ASL"]), False),
    ("no_qualify_rule", "groups[0]", lambda p: (p["defaults"].pop("qualify"), p["groups"][0]["overrides"].pop("qualify")), False),
    ("empty_panel", "", lambda p: [p.pop(k) for k in ("groups", "sites", "haplotypes")], False),
    ("build_mismatch", "build", _set("build", _DELETE), False),
    ("build_mismatch", "sites[0].locus", _set("sites/0/locus", "GRCh37:chr1:11856378:G>A"), False),
    ("locus_out_of_range", "sites[0].locus", _set("sites/0/locus", "GRCh38:chr21:46709984:G>A"), False),
    ("allele_length", "haplotypes[0].alleles.b", _set("haplotypes/0/alleles/b", ["A"]), False),
    ("haplotype_split", "haplotypes[0].sites", _set("haplotypes/0/sites/1", "GRCh38:chr11:100:A"), False),
]  # fmt: skip


@pytest.mark.parametrize("suffix", [".toml", ".json"])
@pytest.mark.parametrize(("code", "location", "edit", "in_schema"), CASES)
def test_each_problem_is_reported_with_its_location(
    tmp_path, suffix, code, location, edit, in_schema
):
    document = _panel()
    edit(document)
    result = _check(_write(tmp_path, document, suffix))
    assert result.exit_code == 1
    assert result.error is not None and result.error.code == "panel_invalid"
    assert result.details["valid"] is False
    found = {(p["code"], p["location"]) for p in result.details["problems"]}
    assert (code, location) in found
    if location:
        assert location in result.error.message


@pytest.mark.parametrize(("code", "location", "edit", "in_schema"), CASES)
def test_the_schema_refuses_what_it_can_express(code, location, edit, in_schema):
    """The generated JSON Schema refuses every structural problem; the
    cross-field ones pass it, and only `panel check` finds them."""
    document = _panel()
    edit(document)
    errors = list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(document))
    assert bool(errors) == in_schema


def test_the_schema_accepts_the_valid_panels():
    toml = tomllib.loads((FIXTURES / "research_panel.toml").read_text("utf-8"))
    jsonschema.validate(toml, SCHEMA)
    jsonschema.validate(_panel(), SCHEMA)


def test_misplaced_keys_name_the_keys_that_fit(tmp_path):
    document = _panel()
    document["groups"][0]["max_af"] = 0.01
    result = _check(_write(tmp_path, document, ".toml"))
    (problem,) = result.details["problems"]
    assert problem["location"] == "groups[0].max_af"
    assert "overrides" in problem["message"]


@pytest.mark.parametrize(
    ("suffix", "text", "location"),
    [
        (".toml", 'schema = "wgsextract.panel/1"\nid = \n', "line 2, column 6"),
        (".json", '{"schema": "wgsextract.panel/1",\n "id": }', "line 2, column 8"),
        (".json", '{"id": "a", "id": "b"}', ""),
    ],
)
def test_syntax_errors_give_the_line(tmp_path, suffix, text, location):
    path = tmp_path / f"panel{suffix}"
    path.write_text(text, encoding="utf-8")
    result = _check(path)
    (problem,) = result.details["problems"]
    assert (problem["code"], problem["location"]) == ("syntax", location)


def test_genes_resolve_in_the_requested_build():
    result = _check(FIXTURES / "research_panel.toml", build="hg19")
    assert result.details["build"] == "hg19"
    (otc,) = _genes(result)["OTC"]["loci"]
    assert otc == {"chrom": "chrX", "start": 38_186_936, "end": 38_282_181}
    # GRCh37's annotation tags no MANE transcripts.
    assert _genes(result)["OTC"]["cds_basis"] == "refseq_select"
    assert "panel_build_differs" in {w.code for w in result.warnings}


def test_haplotype_in_a_pharmcat_gene_is_allowed_with_a_pointer_to_pharmcat():
    result = _check(FIXTURES / "research_panel.toml")
    (haplotype,) = result.details["haplotypes"]
    assert haplotype["pharmcat_gene"] is True and result.exit_code == 0
    (warning,) = [
        w for w in result.warnings if w.code == "panel_haplotype_pharmcat_gene"
    ]
    assert "vcf pharmcat" in warning.message


def test_the_cli_fails_an_invalid_panel_with_its_problems(tmp_path):
    from tests.htslib_helpers import run_wgsextract

    document = copy.deepcopy(_panel())
    document["defaults"]["max_af"] = 2
    path = _write(tmp_path, document, ".toml")
    with pytest.raises(CodedError, match=r"defaults\.max_af: must be at most 1"):
        run_wgsextract("panel", "check", "--panel", str(path))
    run_wgsextract("panel", "check", "--panel", str(FIXTURES / "research_panel.toml"))
    assert not [p for p in tmp_path.iterdir() if p != path]


def test_panel_bed_accepts_hgnc_ids(tmp_path):
    result = api.panel.bed(
        api.PanelBedParams(genes=("HGNC:613",), pad_kb=0, outdir=tmp_path)
    )
    (output,) = result.outputs
    assert Path(output.path).read_text().split() == [
        "chr19",
        "44905795",
        "44909393",
        "APOE",
    ]
