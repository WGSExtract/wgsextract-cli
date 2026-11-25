"""``panel check`` and the ``wgsextract.panel/1`` research panel format (#1287)."""

import copy
import gzip
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import tomli_w

from tests.htslib_helpers import bgzip_index, needs_htslib
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


def _audit_inputs(tmp_path: Path, *, phase_set: str) -> tuple[Path, Path, Path]:
    panel = {
        "schema": "wgsextract.panel/1",
        "id": "test-panel",
        "name": "Synthetic assay",
        "version": "1",
        "build": "GRCh38",
        "defaults": {
            "max_af": 0.01,
            "qualify": [
                {"impacts": ["HIGH"]},
                {"impacts": ["MODERATE"], "min_revel": 0.5, "min_cadd_phred": 20},
            ],
        },
        "groups": [{"id": "group", "genes": ["MTHFR"], "recessive": ["MTHFR"]}],
        "sites": [
            {"id": "reference", "locus": "GRCh38:chr1:11796325:G>A"},
            {"id": "gap", "locus": "GRCh38:chr1:11796340:G>A"},
            {"id": "low", "locus": "GRCh38:chr1:11796335:C>A"},
        ],
        "haplotypes": [
            {
                "id": "phase",
                "gene": "MTHFR",
                "sites": ["GRCh38:chr1:11796321:G", "GRCh38:chr1:11796322:C"],
                "alleles": {"left": ["A", "C"], "right": ["G", "T"]},
            }
        ],
    }
    header = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL">
##INFO=<ID=GNOMAD_AF,Number=A,Type=Float,Description="Frequency">
##INFO=<ID=REVEL,Number=A,Type=Float,Description="REVEL">
##INFO=<ID=CADD_PHRED,Number=A,Type=Float,Description="CADD">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">
##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">
##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase Set">
"""
    columns = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tTEST\n"
    records = [
        "11796321 . G A 50 PASS CSQ=A|stop_gained|HIGH|MTHFR;GNOMAD_AF=nan GT:DP:GQ:AD:PS 1|0:30:60:15,15:1",
        "11796322 . C T 50 PASS CSQ=T|missense_variant|MODERATE|MTHFR;REVEL=0.9;CADD_PHRED=25;GNOMAD_AF=0.001 GT:DP:GQ:AD:PS "
        + f"0|1:30:60:15,15:{phase_set}",
        "11796323 . A T 50 PASS CSQ=T|missense_variant|MODERATE|MTHFR;CADD_PHRED=25 GT:DP:GQ 1/1:30:60",
        "11796324 . C T 50 PASS CSQ=T|missense_variant|MODERATE|MTHFR;CADD_PHRED=1 GT:DP:GQ 1/1:30:60",
        "11796331 . G T 50 PASS CSQ=T|stop_gained|HIGH|MTHFR;GNOMAD_AF=0.1 GT:DP:GQ 1/1:30:60",
    ]
    calls = "".join("chr1\t" + record.replace(" ", "\t") + "\n" for record in records)
    vcf = tmp_path / "audit.vcf"
    vcf.write_text(header + columns + calls, encoding="utf-8")
    gvcf = tmp_path / "audit.g.vcf"
    gvcf.write_text(
        header
        + '##ALT=<ID=NON_REF,Description="Any ALT">\n'
        + '##INFO=<ID=END,Number=1,Type=Integer,Description="Block end">\n'
        + columns
        + "chr1\t11796325\t.\tG\t<NON_REF>\t.\tPASS\tEND=11796330\tGT:DP:GQ:MIN_DP\t0/0:30:60:30\n"
        + "chr1\t11796335\t.\tC\tA,<NON_REF>\t.\tPASS\t.\tGT:DP:GQ:AD\t0/1:2:5:1,1,0\n",
        encoding="utf-8",
    )
    return _write(tmp_path, panel, ".json"), Path(bgzip_index(vcf)), gvcf


@needs_htslib
@pytest.mark.parametrize("phase_set", ["1", "2"])
def test_1287_audit_distinguishes_mixed_scores_reference_and_phase(
    tmp_path: Path, phase_set: str
) -> None:
    panel, vcf, gvcf = _audit_inputs(tmp_path, phase_set=phase_set)
    table_format = "tsv" if phase_set == "1" else "parquet"
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
    assert gene["phase"] == ("trans" if phase_set == "1" else "unknown")
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
    assert watch["status"] == ("called" if phase_set == "1" else "ambiguous_phase")
    assert watch.get("diplotype", watch.get("candidates")) == (
        ["left", "right"] if phase_set == "1" else [["left", "right"]]
    )
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
def test_1287_audit_never_overwrites_its_panel(tmp_path: Path) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    original = panel.read_bytes()
    panel = panel.rename(tmp_path / "audit.panel_audit.json")
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=vcf,
            research_panel=True,
            outdir=tmp_path,
        ),
        check=False,
    )
    assert result.exit_code == 2
    assert panel.read_bytes() == original
    assert not panel.with_suffix(".tsv").exists()


@needs_htslib
def test_1287_one_complete_unphased_heterozygote_is_one_copy(tmp_path: Path) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    document = json.loads(panel.read_text(encoding="utf-8"))
    document["defaults"]["qualify"] = [{"impacts": ["HIGH"]}]
    del document["sites"], document["haplotypes"]
    _write(tmp_path, document, ".json")
    sample = tmp_path / "unphased.vcf"
    with gzip.open(vcf, "rt", encoding="utf-8") as stream:
        sample.write_text(stream.read().replace("1|0:30:", "1/0:30:"), encoding="utf-8")
    result = api.panel.audit(
        api.PanelAuditParams(panel=panel, vcf_input=sample, research_panel=True)
    )
    report = next(o for o in result.outputs if o.role == "panel_audit")
    details = json.loads(Path(report.path).read_text(encoding="utf-8"))
    (gene,) = details["groups"][0]["genes"]
    assert gene["qualifying_allele_copies"] == 1 and gene["phase"] is None
    assert gene["biallelic_state"] == "one_copy"


@needs_htslib
def test_1287_split_multiallelic_watch_preserves_both_alleles(tmp_path: Path) -> None:
    from wgsextract_cli.core.utils import run_command

    panel, _, _ = _audit_inputs(tmp_path, phase_set="1")
    document = json.loads(panel.read_text(encoding="utf-8"))
    document["sites"] = [{"id": "target", "locus": "GRCh38:chr1:11796321:G>A"}]
    del document["haplotypes"]
    panel = _write(tmp_path, document, ".json")
    raw = tmp_path / "multi.vcf"
    raw.write_text(
        """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=CSQ,Number=A,Type=String,Description="Format: Allele|Consequence|IMPACT|SYMBOL">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">
##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tTEST
chr1\t11796321\t.\tG\tA,T\t50\tPASS\tCSQ=A|stop_gained|HIGH|MTHFR,T|stop_gained|HIGH|MTHFR\tGT:DP:GQ:AD\t1/2:40:60:0,20,20
""",
        encoding="utf-8",
    )
    split = tmp_path / "split.vcf.gz"
    run_command(
        [
            "bcftools",
            "norm",
            "-m",
            "-any",
            "--keep-sum",
            "AD",
            "--old-rec-tag",
            "OLD_MULTIALLELIC",
            "-Oz",
            "-o",
            str(split),
            str(raw),
        ]
    )
    run_command(["bcftools", "index", str(split)])
    result = api.panel.audit(
        api.PanelAuditParams(panel=panel, vcf_input=split, research_panel=True)
    )
    report = next(o for o in result.outputs if o.role == "panel_audit")
    details = json.loads(Path(report.path).read_text(encoding="utf-8"))
    (watch,) = details["sites"]
    assert (watch["status"], watch["genotype"], watch["query_allele_copies"]) == (
        "confident_call",
        "1/2",
        1,
    )
    (gene,) = details["groups"][0]["genes"]
    assert gene["phase"] == "trans"


def test_1287_audit_requires_explicit_research_opt_in() -> None:
    from wgsextract_cli.core.utils import UsageError

    with pytest.raises(UsageError, match="--research-panel"):
        api.PanelAuditParams(panel=Path("panel.toml"), vcf_input=Path("sample.vcf.gz"))


@needs_htslib
def test_1287_null_counts_callable_cds_and_excludes_duplicated_controls(
    tmp_path: Path,
) -> None:
    panel, vcf, gvcf = _audit_inputs(tmp_path, phase_set="1")
    header = ""
    observed = []
    with gzip.open(vcf, "rt") as stream:
        for line in stream:
            if line.startswith("#"):
                header += line
            else:
                observed.append(line)
    control = [
        "chr1\t923930\t.\tC\tG\t50\tPASS\tCSQ=G|stop_gained|HIGH|SAMD11\tGT:DP:GQ\t1/1:30:60\n",
        "chr1\t944210\t.\tA\tT\t50\tPASS\tCSQ=T|stop_gained|HIGH|NOC2L\tGT:DP:GQ\t1/1:30:60\n",
    ]
    counts = tmp_path / "combined.vcf"
    counts.write_text(header + "".join(control + observed), encoding="utf-8")
    indexed = Path(bgzip_index(counts))
    gheader = gvcf.read_text(encoding="utf-8").split("#CHROM")[0]
    blocks = [
        (923931, 923939, 30, 60),
        (944211, 944219, 30, 60),
        (1013500, 1013509, 30, 60),
        (1020125, 1020134, 2, 3),
        (11796325, 11796330, 30, 60),
    ]
    grows = (
        control
        + observed[:4]
        + [
            f"chr1\t{start}\t.\tA\t<NON_REF>\t.\tPASS\tEND={end}\tGT:DP:GQ:MIN_DP\t0/0:{dp}:{gq}:{dp}\n"
            for start, end, dp, gq in blocks
        ]
    )
    gvcf.write_text(
        gheader
        + "#CHROM"
        + header.split("#CHROM")[1]
        + "".join(sorted(grows, key=lambda row: int(row.split("\t")[1]))),
        encoding="utf-8",
    )
    model = tmp_path / "exons.tsv"
    model.write_text(
        "#contig\tchr1\t248956422\n"
        "gene\ttranscript\ttag\tstrand\texon\texons\tchrom\tstart\tend\tcds_start\tcds_end\tlimitations\n"
        + "".join(
            f"{gene}\tNM_{gene}\tMANE Select\t+\t1\t1\tchr1\t{start}\t{start + 9}\t"
            f"{start}\t{start + 9}\t{mask}\n"
            for gene, start, mask in (
                ("MTHFR", 11796321, ""),
                ("NOC2L", 944210, ""),
                ("ISG15", 1013500, ""),
                ("AGRN", 1020125, ""),
                ("SAMD11", 923930, "segmental_duplication:923930-923934"),
            )
        ),
        encoding="utf-8",
    )

    def audit(
        directory: str, reference: Path | None
    ) -> tuple[dict[str, Any], set[str]]:
        result = api.panel.audit(
            api.PanelAuditParams(
                panel=panel,
                vcf_input=indexed,
                gvcf=reference,
                exons=model,
                research_panel=True,
                null_sets=100,
                seed=7,
                outdir=tmp_path / directory,
            )
        )
        report = next(o for o in result.outputs if o.role == "panel_audit")
        return json.loads(Path(report.path).read_text(encoding="utf-8")), {
            warning.code for warning in result.warnings
        }

    calibrated, warnings = audit("first", gvcf)
    repeated, _ = audit("repeat", gvcf)
    null = calibrated["groups"][0]["calibration"]
    assert repeated["groups"][0]["calibration"] == null
    assert null["basis"] == "callable_cds_bp"
    assert null["observed_allele_copies"] == 2
    assert null["observed_matched_bp"] == null["null_mean_matched_bp"] == 10
    assert null["control_genes"] == 2
    histogram = null["null_histogram"]
    assert set(histogram) == {"0", "2"} and sum(histogram.values()) == 100
    assert null["upper_tail_p"] == pytest.approx((histogram["2"] + 1) / 101)
    assert "panel_calibration_underpowered" in warnings
    assert calibrated["calibration_universe"]["segmental_duplication_exclusions"] == [
        "SAMD11"
    ]
    fallback, _ = audit("fallback", None)
    assert fallback["groups"][0]["calibration"]["basis"] == "cds_bp"
    assert fallback["groups"][0]["calibration"]["control_genes"] == 3
    original = model.read_text(encoding="utf-8")
    model.write_text(
        original.replace("1013509", "1013511").replace("11796330", "11796331"),
        encoding="utf-8",
    )
    in_support, _ = audit("between-control-lengths", None)
    assert in_support["groups"][0]["calibration"]["state"] == "calibrated"
    model.write_text(
        original.replace("11796330", "11796420"),
        encoding="utf-8",
    )
    unmatched, warnings = audit("outside-support", None)
    null = unmatched["groups"][0]["calibration"]
    assert null["reason"] == "outside_control_cds_range"
    assert null["unmatched_targets"] == ["MTHFR"]
    assert "upper_tail_p" not in null
    assert "panel_calibration_underpowered" in warnings


@needs_htslib
@pytest.mark.parametrize("end", [11796331, 248956422])
def test_1287_only_incomplete_called_regions_block_null_calibration(
    tmp_path: Path,
    end: int,
) -> None:
    from wgsextract_cli.core.reference_evidence import Provenance

    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    with gzip.open(vcf, "rt", encoding="utf-8") as stream:
        header, body = stream.read().split("#CHROM", 1)
    start = 1 if end == 248956422 else 11796321
    region = f"chr1:{start}-{end}"
    policy = Provenance("bcftools", "1.23", "gq", region=region)
    restricted = tmp_path / "restricted.vcf"
    restricted.write_text(
        header + policy.header() + "\n#CHROM" + body, encoding="utf-8"
    )
    model = tmp_path / "exons.tsv"
    model.write_text(
        "#contig\tchr1\t248956422\n"
        "gene\ttranscript\ttag\tstrand\texon\texons\tchrom\tstart\tend\tcds_start\tcds_end\tlimitations\n"
        "MTHFR\tNM_005957.5\tMANE Select\t+\t1\t1\tchr1\t11796321\t11796330\t11796321\t11796330\t\n",
        encoding="utf-8",
    )
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=restricted,
            research_panel=True,
            null_sets=100,
            exons=model,
            outdir=tmp_path / "regional",
        ),
        check=False,
    )
    if start == 1:
        assert result.exit_code == 0 and result.outputs
    else:
        assert result.exit_code == 2
        assert result.error and region in result.error.message
        assert not result.outputs


@needs_htslib
def test_1287_missing_mane_reference_requires_explicit_download(tmp_path: Path) -> None:
    panel, vcf, _ = _audit_inputs(tmp_path, phase_set="1")
    library = tmp_path / "library"
    library.mkdir()
    result = api.panel.audit(
        api.PanelAuditParams(
            panel=panel,
            vcf_input=vcf,
            ref=library,
            research_panel=True,
            null_sets=100,
            outdir=tmp_path / "local",
        ),
        check=False,
    )
    assert result.exit_code == 2
    assert result.error and "mane-grch38-v1.tsv.gz" in result.error.message
    assert "--download" in result.error.message
    assert not list(library.iterdir())


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
