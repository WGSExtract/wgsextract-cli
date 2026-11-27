"""Focused server lookups regressions."""

import contextlib
import gzip
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.server_helpers import Client, Mcp, _serve
from tests.server_helpers import client as client
from tests.server_helpers import work as work
from wgsextract_cli.server.operations import (
    PathGuard,
)


def _pharmcat_run(sample: Path, folder: Path) -> None:
    """A `vcf pharmcat` manifest in ``sample`` whose phenotypes are in ``folder``."""
    phenotypes = {
        "geneReports": {
            "CYP2C19": {
                "recommendationDiplotypes": [
                    {
                        "allele1": {"name": "*1"},
                        "allele2": {"name": "*17"},
                        "phenotypes": ["Rapid Metabolizer"],
                    }
                ]
            }
        }
    }
    evidence = folder / "HG002.phenotype.json"
    evidence.write_text(json.dumps(phenotypes))
    manifest = sample / "HG002.phenotype.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "wgsextract.result/1",
                "command": "vcf pharmcat",
                "status": "ok",
                "exit_code": 0,
                "inputs": [{"path": str(sample / "HG002.vcf.gz"), "role": "input"}],
                "outputs": [{"path": str(evidence), "role": "pharmcat_phenotypes"}],
                "manifest": str(manifest),
                "provenance": {"finished": "2025-01-01T00:00:00+00:00"},
            }
        )
    )


_TABLE_HEADER = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=1000>
##contig=<ID=chr2,length=1000>
##INFO=<ID=CLNSIG,Number=.,Type=String,Description="ClinVar significance">
##INFO=<ID=WGSE_CONF,Number=A,Type=String,Description="Confidence">
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: Allele|Consequence|IMPACT|SYMBOL|Feature|Protein_position|DOMAINS">
##INFO=<ID=GNOMAD_POPMAX_AF,Number=A,Type=Float,Description="Group maximum AF">
##INFO=<ID=GNOMAD_NHOMALT,Number=A,Type=Integer,Description="Homozygote count">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=PS,Number=1,Type=Integer,Description="Phase set">
##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1
"""
# (chrom, pos, id, ref, alt, gene, ClinVar, confidence)
_TABLE_RECORDS = [
    ("chr1", 100, "rs1", "A", "G", "GENEA", "Pathogenic", "high"),
    ("chr1", 200, "rs2", "C", "T", "GENEA", "Likely_pathogenic", "moderate"),
    ("chr1", 300, "rs22", "G", "A", "GENEB", "Benign", "low"),
    ("chr1", 400, "rs4", "T", "C", "GENEB", "Pathogenic/Likely_pathogenic", "high"),
    ("chr2", 50, "rs5", "A", "T", "GENEC", None, "high"),
]


@pytest.fixture
def variant_tables(work: Path) -> dict[str, Path]:
    from tests.htslib_helpers import bgzip_index, run_wgsextract

    lines = [
        "\t".join(
            [
                chrom,
                str(pos),
                rsid,
                ref,
                alt,
                "50",
                "PASS",
                ";".join(
                    ([f"CLNSIG={clnsig}"] if clnsig else [])
                    + [
                        f"WGSE_CONF={conf}",
                        f"CSQ={alt}|missense_variant|MODERATE|{gene}|"
                        + ("ENST4|4-5/80|Pfam:PF1" if rsid == "rs4" else ""),
                    ]
                    + (
                        ["GNOMAD_POPMAX_AF=0.125", "GNOMAD_NHOMALT=0"]
                        if rsid == "rs4"
                        else []
                    )
                ),
                "GT:PS:AD" if rsid == "rs4" else "GT",
                "0|1:350:14,6" if rsid == "rs4" else "0/1",
            ]
        )  # fmt: skip
        for chrom, pos, rsid, ref, alt, gene, clnsig, conf in _TABLE_RECORDS
    ]
    plain = work / "sample.vcf"
    plain.write_text(_TABLE_HEADER + "\n".join(lines) + "\n")
    vcf = bgzip_index(plain)
    run_wgsextract("vcf", "table", "--vcf-input", vcf, "--outdir", str(work))
    tables = {
        "sqlite": work / "sample_variants.sqlite",
        "tsv": work / "sample_variants.tsv",
    }
    if importlib.util.find_spec("pyarrow"):
        run_wgsextract(
            "vcf", "table", "--vcf-input", vcf, "--outdir", str(work),
            "--no-sqlite", "--format", "parquet",
        )  # fmt: skip
        tables["parquet"] = work / "sample_variants.parquet"
    return tables


@pytest.mark.parametrize("through_link", [False, True])
def test_a_server_report_reads_no_evidence_outside_the_allowed_folders(
    tmp_path, work, through_link
):
    # #351 phase 2 security review: report reads the outputs earlier runs'
    # manifests list, wherever they are, so a manifest in an allowed folder
    # could lead a server job to any file. Review of #486: an allowed folder
    # named through a symlink must still count as inside.
    allowed = work
    if through_link:
        allowed = tmp_path / "link"
        try:
            allowed.symlink_to(work, target_is_directory=True)
        except OSError:
            pytest.skip("can't create symlinks here")
    sample = allowed / "HG002"
    sample.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    def genes(job: dict[str, Any]) -> set[str]:
        assert job["status"] == "succeeded", job
        (document,) = (
            o["path"] for o in job["result"]["outputs"] if o["path"].endswith(".json")
        )
        section = json.loads(Path(document).read_text())["sections"]["pharmacogenomics"]
        return {f["gene"] for f in section["findings"]}

    def over_http(client: Client) -> set[str]:
        body = {"workspace": str(sample)}
        return genes(client.request("POST", "/ops/report?wait=120", body)[1])

    with _serve(tmp_path, [allowed]) as client:
        _pharmcat_run(sample, sample)
        assert over_http(client) == {"CYP2C19"}
        _pharmcat_run(sample, outside)
        assert over_http(client) == set()
    mcp = Mcp("--allow", str(allowed), "--allow-jobs")

    def over_mcp() -> set[str]:
        started = mcp.call("report", workspace=str(sample))["structuredContent"]
        job = mcp.call("job_status", job_id=started["id"], wait_seconds=120)
        return genes(job["structuredContent"])

    assert over_mcp() == set()
    _pharmcat_run(sample, sample)
    assert over_mcp() == {"CYP2C19"}
    assert mcp.close() == 0


def test_a_server_report_never_touches_a_refused_recorded_path(
    tmp_path, work, monkeypatch
):
    # Review of #486: report checked that a recorded output existed before
    # asking the read scope, so a manifest naming a UNC path made a server
    # job connect to that host (sending this user's credentials on Windows).
    from tests.htslib_helpers import run_wgsextract
    from wgsextract_cli.core import read_scope

    sample = work / "HG002"
    sample.mkdir()
    share = "//host/share"
    for command, role, name in (
        ("vcf pharmcat", "pharmcat_phenotypes", "HG002.phenotype.json"),
        ("microarray", "combined_kit", "HG002_CombinedKit.txt"),
    ):
        manifest = sample / f"{name}.manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "wgsextract.result/1",
                    "command": command,
                    "status": "ok",
                    "exit_code": 0,
                    "outputs": [{"path": f"{share}/{name}", "role": role}],
                    "manifest": str(manifest),
                    "provenance": {"finished": "2025-01-01T00:00:00+00:00"},
                }
            )
        )
    probed: list[str] = []
    stat = os.stat

    def spy(path: Any, *args: Any, **kwargs: Any) -> Any:
        if os.fspath(path).replace("\\", "/").startswith(share):
            probed.append(os.fspath(path))
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", spy)
    with read_scope.limited_to(PathGuard([work]).allows):
        run_wgsextract("report", "--workspace", str(sample))
    assert probed == []
    report = json.loads((sample / "HG002_personal_report.json").read_text())
    assert report["sections"]["pharmacogenomics"]["findings"] == []
    assert [f["exists"] for f in report["sections"]["microarray"]["files"]] == [False]


@pytest.mark.parametrize("kind", ["sqlite", "tsv", "parquet"])
def test_variant_lookups_filter_and_page_every_table_format(
    tmp_path, work, variant_tables, kind
):
    if kind not in variant_tables:
        pytest.skip("pyarrow not installed")
    table = variant_tables[kind]

    def lookup(**query: Any) -> tuple[int, Any]:
        text = urllib.parse.urlencode({"path": str(table), **query})
        return client.request("GET", f"/variants?{text}")

    def positions(**query: Any) -> list[tuple[str, int]]:
        status, answer = lookup(**query)
        assert status == 200, answer
        return [(row["chrom"], row["pos"]) for row in answer["rows"]]

    with _serve(tmp_path, [work]) as client:
        assert positions(gene="GENEA") == [("chr1", 100), ("chr1", 200)]
        # rs2 is not rs22.
        assert positions(rsid="rs2") == [("chr1", 200)]
        assert positions(region="chr1:150-350") == [("chr1", 200), ("chr1", 300)]
        assert positions(region="chr1:300") == [("chr1", 300)]
        assert positions(region="chr2") == [("chr2", 50)]
        assert positions(clinvar="likely_pathogenic") == [("chr1", 200), ("chr1", 400)]
        assert positions(min_confidence="moderate", gene="GENEB") == [("chr1", 400)]
        status, answer = lookup(gene="GENEB", columns="pos,clinvar_significance")
        assert answer["rows"] == [
            {"pos": 300, "clinvar_significance": "Benign"},
            {"pos": 400, "clinvar_significance": "Pathogenic/Likely_pathogenic"},
        ]

        # Review of #351 PR B: OpenAPI clients repeat array parameters, and
        # a region past SQLite's integers was a 500.
        status, answer = client.request(
            "GET",
            f"/variants?path={urllib.parse.quote(str(table))}&rsid=rs4"
            "&columns=pos&columns=gene",
        )
        assert answer["rows"] == [{"pos": 400, "gene": "GENEB"}]
        assert positions(region=f"chr1:{10**20}-{10**21}") == []

        pages: list[list[int]] = []
        cursor = ""
        while cursor is not None:
            status, answer = lookup(limit=2, **({"cursor": cursor} if cursor else {}))
            pages.append([row["pos"] for row in answer["rows"]])
            cursor = answer["next_cursor"]
        assert pages == [[100, 200], [300, 400], [50]]
        status, answer = lookup(gene="GENEB", limit=1)
        assert [row["pos"] for row in answer["rows"]] == [300]
        status, answer = lookup(gene="GENEB", limit=1, cursor=answer["next_cursor"])
        assert ([row["pos"] for row in answer["rows"]], answer["next_cursor"]) == (
            [400],
            None,
        )

        # Review of #487: an empty start or end, a non-ASCII digit and a
        # cursor past SQLite's integers were 500s.
        for bad in (
            {"limit": 0}, {"rsid": "2"}, {"region": "chr1:9-1"}, {"x": 1},
            {"region": "chr1:,"}, {"region": "chr1:1-,"}, {"cursor": "\u00b2"},
            {"cursor": str(2**63)}, {"cursor": "-1"},
        ):  # fmt: skip
            status, answer = lookup(**bad)
            assert (status, answer["error"]["code"]) == (400, "usage"), bad
        outside = tmp_path / table.name
        shutil.copy(table, outside)
        status, answer = client.request(
            "GET", f"/variants?{urllib.parse.urlencode({'path': str(outside)})}"
        )
        assert (status, answer["error"]["code"]) == (403, "path_not_allowed")


def test_unreadable_variant_tables_are_refused_not_failed(
    tmp_path, work, variant_tables, monkeypatch
):
    # Review of #351 PR B: a truncated download and a missing pyarrow were
    # internal errors (500) instead of saying what's wrong.
    whole = gzip.compress(variant_tables["tsv"].read_bytes() * 200)
    truncated = work / "cut_variants.tsv.gz"
    truncated.write_bytes(whole[: len(whole) // 2])
    parquet = work / "sample_variants.parquet"
    parquet.touch()
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    with _serve(tmp_path, [work]) as client:
        for table in (truncated, parquet):
            text = urllib.parse.urlencode({"path": str(table), "limit": 1000})
            status, answer = client.request("GET", f"/variants?{text}")
            assert (status, answer["error"]["code"]) == (400, "usage"), table
        assert "pyarrow" in answer["error"]["message"]


def test_1209_corrupt_parquet_returns_a_client_error(client, work):
    pytest.importorskip("pyarrow.parquet")
    parquet = work / "broken_variants.parquet"
    parquet.write_bytes(b"not a parquet file")
    query = urllib.parse.urlencode({"path": str(parquet)})
    status, answer = client.request("GET", f"/variants?{query}")
    assert (status, answer["error"]["code"]) == (400, "usage")


def test_1209_parquet_reads_requested_and_filter_columns_only(client, work):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    parquet = work / "sample_variants.parquet"
    # This timestamp is valid in Arrow but outside Python datetime's range.
    pq.write_table(
        pa.table(
            {
                "chrom": ["chr1", "chr2"],
                "pos": [42, 84],
                "not_requested": pa.array([10**13, 10**13], type=pa.timestamp("s")),
            }
        ),
        parquet,
    )
    query = urllib.parse.urlencode(
        {"path": str(parquet), "columns": "pos,gene", "region": "chr1:42"}
    )
    status, answer = client.request("GET", f"/variants?{query}")
    assert status == 200
    assert answer["rows"] == [{"pos": 42}]


def test_report_lookup_reads_a_samples_newest_report(tmp_path, work):
    from tests.htslib_helpers import run_wgsextract

    sample = work / "HG002"
    sample.mkdir()
    _pharmcat_run(sample, sample)
    run_wgsextract("report", "--workspace", str(sample))
    outside = tmp_path / "outside"
    outside.mkdir()

    def lookup(path: Path, **query: Any) -> tuple[int, Any]:
        text = urllib.parse.urlencode({"path": str(path), **query})
        return client.request("GET", f"/report?{text}")

    with _serve(tmp_path, [work]) as client:
        status, answer = lookup(sample)
        assert status == 200, answer
        assert answer["sample"]["name"] == "HG002" and answer["disclaimer"]
        overview = answer["sections"]["pharmacogenomics"]
        assert overview["summary"] and "findings" not in overview
        status, answer = lookup(sample, section="pharmacogenomics")
        assert [f["gene"] for f in answer["findings"]] == ["CYP2C19"]
        assert lookup(sample, section="nope")[0] == 400
        assert lookup(work)[1]["error"]["code"] == "no_report"
        assert lookup(outside)[1]["error"]["code"] == "path_not_allowed"

        # Review of #487: a dangling report link was a 500; it is skipped.
        try:
            (sample / "HG004_personal_report.json").symlink_to(work / "gone.json")
        except OSError:
            pass
        else:
            status, answer = lookup(sample)
            assert (status, answer["sample"]["name"]) == (200, "HG002"), answer

        # Review of #487: a report whose sections aren't a mapping was a 500.
        report = sample / "HG002_personal_report.json"
        broken = work / "broken_personal_report.json"
        for sections in (["pharmacogenomics"], {"pgx": "text"}):
            document = json.loads(report.read_text())
            broken.write_text(json.dumps({**document, "sections": sections}))
            status, answer = lookup(broken)
            assert (status, answer["error"]["code"]) == (400, "usage"), sections

        # A newer report reached through a link must still be inside.
        report = sample / "HG002_personal_report.json"
        elsewhere = outside / "HG003_personal_report.json"
        shutil.copy(report, elsewhere)
        try:
            (sample / elsewhere.name).symlink_to(elsewhere)
        except OSError:
            return
        os.utime(elsewhere, (time.time() + 60, time.time() + 60))
        status, answer = lookup(sample)
        assert (status, answer["error"]["code"]) == (403, "path_not_allowed")


def test_a_lookup_refuses_a_boolean_it_cannot_read(tmp_path, work):
    # Review of #487: full=maybe quietly meant false.
    manifest = work / "snps.vcf.manifest.json"
    with _serve(tmp_path, [work]) as client:
        for value in ("maybe", "2", "Yes", "0"):
            text = urllib.parse.urlencode({"path": str(manifest), "full": value})
            status, answer = client.request("GET", f"/manifest?{text}")
            # A readable value gets as far as looking for the (absent) file.
            expected = (400, "usage") if value in ("maybe", "2") else (404,)
            assert (status, answer["error"]["code"])[: len(expected)] == expected


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that bind the user",
)
def test_an_unreadable_variant_database_is_a_usage_error(
    tmp_path, work, variant_tables
):
    # Review of #487: opening it raised outside the error handling (a 500).
    db = work / "locked_variants.sqlite"
    shutil.copy(variant_tables["sqlite"], db)
    db.chmod(0)
    try:
        with _serve(tmp_path, [work]) as client:
            text = urllib.parse.urlencode({"path": str(db)})
            status, answer = client.request("GET", f"/variants?{text}")
    finally:
        db.chmod(0o600)
    assert (status, answer["error"]["code"]) == (400, "usage"), answer


def test_a_blob_in_a_variant_database_is_returned_as_text(
    tmp_path, work, variant_tables
):
    # Review of #487: SQLite stores any type in any column; a BLOB made the
    # MCP query_variants call hang with no reply, and HTTP fail.
    db = work / "blob_variants.sqlite"
    shutil.copy(variant_tables["sqlite"], db)
    with contextlib.closing(sqlite3.connect(db)) as con, con:
        con.execute("UPDATE variants SET gene = ? WHERE rsid = 'rs4'", (b"GENEB",))
    mcp = Mcp("--allow", str(work))
    result = mcp.call("query_variants", path=str(db), rsid="rs4")
    assert result["isError"] is False
    assert [r["gene"] for r in result["structuredContent"]["rows"]] == ["GENEB"]
    assert mcp.close() == 0
    with _serve(tmp_path, [work]) as client:
        text = urllib.parse.urlencode({"path": str(db), "rsid": "rs4"})
        status, answer = client.request("GET", f"/variants?{text}")
    assert (status, [r["gene"] for r in answer["rows"]]) == (200, ["GENEB"])


def test_mcp_offers_lookups_without_allow_jobs(work, variant_tables):
    mcp = Mcp("--allow", str(work))
    tools = {t["name"]: t for t in mcp.request("tools/list", {})["result"]["tools"]}
    for name in ("read_manifest", "query_variants", "report_section"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
    result = mcp.call("query_variants", path=str(variant_tables["sqlite"]), rsid="rs4")
    assert result["isError"] is False
    assert [r["gene"] for r in result["structuredContent"]["rows"]] == ["GENEB"]
    result = mcp.call("query_variants", path=str(variant_tables["tsv"]), limit="2")
    assert result["structuredContent"]["error"]["code"] == "usage"
    result = mcp.call("report_section", path=str(work.parent))
    assert result["structuredContent"]["error"]["code"] == "path_not_allowed"
    assert mcp.close() == 0


def test_1289_variant_quality_and_gnomad_columns_are_queryable(
    tmp_path, work, variant_tables
):
    expected = {
        "phase_set": "350",
        "alt_fraction": 0.3,
        "transcript_id": "ENST4",
        "gnomad_popmax_af": 0.125,
        "gnomad_nhomalt": 0,
    }
    path = str(variant_tables["tsv"])
    with _serve(tmp_path, [work]) as http:
        query = urllib.parse.urlencode(
            {"path": path, "rsid": "rs4", "columns": ",".join(expected)}
        )
        status, answer = http.request("GET", f"/variants?{query}")
        assert status == 200, answer
        assert answer["rows"] == [expected]
    mcp = Mcp("--allow", str(work))
    try:
        result = mcp.call(
            "query_variants",
            path=str(variant_tables["sqlite"]),
            rsid="rs4",
            columns=list(expected),
        )
        assert result["structuredContent"]["rows"] == [expected]
    finally:
        assert mcp.close() == 0


def test_1622_protein_columns_are_typed_and_older_tables_are_readable(
    tmp_path, work, variant_tables
):
    expected = {"protein_position": 4, "protein_position_end": 5, "domains": "Pfam:PF1"}
    legacy = work / "legacy_variants.sqlite"
    with contextlib.closing(sqlite3.connect(legacy)) as db, db:
        db.execute("CREATE TABLE variants (pos INTEGER)")
        db.execute("INSERT INTO variants VALUES (400)")
    columns = ",".join(["pos", *expected])
    with _serve(tmp_path, [work]) as http:
        for path in [*variant_tables.values(), legacy]:
            query = urllib.parse.urlencode({"path": str(path), "columns": columns})
            status, answer = http.request("GET", f"/variants?{query}")
            assert status == 200, answer
            row = next(row for row in answer["rows"] if row["pos"] == 400)
            assert row == ({"pos": 400} if path == legacy else {"pos": 400, **expected})
            if path != legacy:
                assert next(row for row in answer["rows"] if row["pos"] == 100) == {
                    "pos": 100
                }
    mcp = Mcp("--allow", str(work))
    try:
        result = mcp.call(
            "query_variants",
            path=str(variant_tables["sqlite"]),
            rsid="rs4",
            columns=list(expected),
        )
        assert result["structuredContent"]["rows"] == [expected]
    finally:
        assert mcp.close() == 0
