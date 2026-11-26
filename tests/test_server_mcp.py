"""Focused server mcp regressions."""

import io
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from tests.server_data import fake_data as fake_data
from tests.server_helpers import FAKE_DIR, Mcp, _serve
from tests.server_helpers import client as client
from tests.server_helpers import work as work

# The loadgroup scheduler recognizes this import for shared fake_30x fixtures.
from tests.smoke_utils import ensure_fake_data as ensure_fake_data
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.mcp_server import McpServer
from wgsextract_cli.server.operations import PathGuard


def _indexed_bam(work: Path, bam: Path) -> Path:
    copy = work / "s.bam"
    shutil.copyfile(bam, copy)
    subprocess.run(["samtools", "index", str(copy)], check=True)
    return copy


def test_1209_stdio_cancel_before_started_stops_worker_without_reply(tmp_path):
    submitted = threading.Event()
    release = threading.Event()

    class PausedSubmit(JobManager):
        def submit(self, op, params, **kwargs):
            job = super().submit(op, params, **kwargs)
            submitted.set()
            assert release.wait(10)
            return job

    manager = PausedSubmit(
        tmp_path / "jobs",
        config_path=None,
        worker_command=[
            sys.executable,
            "-c",
            "import sys; sys.stdin.readline(); sys.stdin.readline()",
        ],
    )
    stdout = io.StringIO()
    server = McpServer(manager, PathGuard([tmp_path]), allow_jobs=True, stdout=stdout)
    server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "example_genome_list",
                    "arguments": {"target_root": str(tmp_path)},
                },
            }
        )
    )
    try:
        assert submitted.wait(10)
        server.handle_line(
            '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}}'
        )
        release.set()
        job = manager.wait(manager.summaries()[0]["id"], 5)
        assert job is not None and job.status == "cancelled"
    finally:
        release.set()
        server.serve(io.StringIO())
    assert stdout.getvalue() == ""


def test_1209_mcp_job_status_rejects_boolean_wait(work):
    mcp = Mcp("--allow", str(work), "--allow-jobs")
    try:
        job = mcp.call("example_genome_list", target_root=str(work))[
            "structuredContent"
        ]
        for value in (True, False):
            result = mcp.call("job_status", job_id=job["id"], wait_seconds=value)
            assert result["isError"] is True
            assert result["structuredContent"]["error"]["code"] == "usage"
    finally:
        mcp.close()


def test_read_only_mcp_info_saves_nothing(tmp_path, fake_data):
    # Security review of PR #422: read-only `info` created the folders named
    # by outdir, and saved its metrics cache next to the input.
    data = tmp_path / "data"
    data.mkdir()
    for name in ("fake.bam", "fake.bam.bai"):
        shutil.copy2(FAKE_DIR / name, data / name)
    before = {p: p.read_bytes() if p.is_file() else None for p in data.rglob("*")}

    mcp = Mcp("--allow", str(data))
    schema = next(
        t["inputSchema"]
        for t in mcp.request("tools/list", {})["result"]["tools"]
        if t["name"] == "info"
    )
    assert not {"outdir", "detailed", "cache"} & set(schema["properties"])
    bam = str(data / "fake.bam")
    result = mcp.call("info", input=bam)
    assert result["structuredContent"]["result"]["metrics"]["sorted"] is True
    assert mcp.call("info", input=bam, cache=False)["isError"] is False
    for refused in (
        {"outdir": str(data / "a" / "b")},
        {"detailed": True},
        {"cache": True},
    ):
        result = mcp.call("info", input=bam, **refused)
        assert result["structuredContent"]["error"]["code"] == "read_only", refused
    assert mcp.close() == 0

    after = {p: p.read_bytes() if p.is_file() else None for p in data.rglob("*")}
    assert after == before


def test_mcp_offers_read_only_queries_by_default(work, fake_data):
    bam, _ = fake_data
    mcp = Mcp("--allow", str(work), "--allow", str(FAKE_DIR))
    tools = {t["name"]: t for t in mcp.request("tools/list", {})["result"]["tools"]}
    assert {"info", "read_manifest", "job_status"} <= set(tools)
    assert "vcf_snp" not in tools and "cancel_job" not in tools
    # ref acmg-data downloads and builds tens of GB (issue 629): a job.
    assert "ref_acmg_data" not in tools
    assert tools["info"]["annotations"]["readOnlyHint"] is True
    assert tools["info"]["inputSchema"]["required"] == ["input"]

    result = mcp.call("info", input=str(bam))
    assert result["isError"] is False
    job = result["structuredContent"]
    assert job["status"] == "succeeded" and job["result"]["metrics"]["sorted"]
    assert json.loads(result["content"][0]["text"]) == job

    result = mcp.call("info", input=str(work.parent / "a.bam"))
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "path_not_allowed"
    assert mcp.call("vcf_snp", input=str(bam))["error"]["code"] == -32602
    assert mcp.call("ref_acmg_data", ref=str(work))["error"]["code"] == -32602

    # PR #422 review: info's table_format saves files, yet read-only MCP
    # offered it; malformed params killed the call or the server.
    assert "table_format" not in tools["info"]["inputSchema"]["properties"]
    result = mcp.call("info", input=str(bam), table_format="tsv")
    assert result["structuredContent"]["error"]["code"] == "read_only"
    assert not list(work.glob("*.tsv"))
    assert mcp.request("tools/call", [])["error"]["code"] == -32602
    mcp.send({"method": "notifications/cancelled", "params": {"requestId": {}}})
    assert mcp.request("ping", {})["result"] == {}
    assert mcp.close() == 0


def test_mcp_runs_jobs_when_allowed(work, fake_data):
    bam, ref = fake_data
    mcp = Mcp("--allow", str(work), "--allow", str(FAKE_DIR), "--allow-jobs")
    started = mcp.call(
        "vcf_snp",
        input=str(bam),
        ref=str(ref),
        outdir=str(work),
        region="chrM",
        ploidy="1",
    )["structuredContent"]
    assert started["status"] in ("queued", "running")
    job = mcp.call("job_status", job_id=started["id"], wait_seconds=300)
    assert job["structuredContent"]["status"] == "succeeded"

    manifest = mcp.call(
        "read_manifest", path=str(work / "manifests" / "snps.vcf.manifest.json")
    )
    assert manifest["structuredContent"]["command"] == "vcf snp"
    assert "provenance" not in manifest["structuredContent"]
    assert mcp.call("cancel_job", job_id=started["id"])["isError"] is True
    assert mcp.close() == 0


def test_deleting_data_needs_allow_destructive(tmp_path, work, fake_data):
    # #351 asks for an explicit opt-in before a request deletes data the
    # server didn't make: an index, a sample's settings, the gene maps.
    bam = _indexed_bam(work, fake_data[0])
    index = Path(f"{bam}.bai")
    with _serve(tmp_path, [work]) as client:
        listed = {o["op"]: o for o in client.request("GET", "/ops")[1]["ops"]}
        assert listed["bam/unindex"]["destructive"] is True
        assert listed["ref/gene-map"]["destructive"] is True
        assert listed["ref/dense-sa"]["destructive"] is True
        assert listed["ref/library-delete"]["destructive"] is True
        assert listed["ref/test-genome"]["destructive"] is True
        assert "destructive" not in listed["bam/index"]
        status, response = client.request(
            "POST", "/ops/bam/unindex", {"input": str(bam)}
        )
        assert (status, response["error"]["code"]) == (403, "destructive")
        status, response = client.request(
            "POST", "/ops/ref/gene-map", {"ref": str(work), "delete": True}
        )
        assert (status, response["error"]["code"]) == (403, "destructive")
        status, response = client.request(
            "POST", "/ops/ref/dense-sa", {"ref": str(bam), "delete": True}
        )
        assert (status, response["error"]["code"]) == (403, "destructive")
        # #825: deleting a library genome, as the app's delete button does.
        status, response = client.request(
            "POST", "/ops/ref/library-delete", {"ref": str(work), "genome": "hs38"}
        )
        assert (status, response["error"]["code"]) == (403, "destructive")
        # #825: deleting the test genome, as the app's Datasets page does.
        status, response = client.request(
            "POST",
            "/ops/ref/test-genome",
            {"dataset_library": str(work), "delete": True},
        )
        assert (status, response["error"]["code"]) == (403, "destructive")
        assert client.request("GET", "/jobs")[1]["jobs"] == []
    assert index.exists()
    with _serve(tmp_path / "again", [work], allow_destructive=True) as client:
        status, job = client.request(
            "POST", "/ops/bam/unindex?wait=60", {"input": str(bam)}
        )
    assert (status, job["status"]) == (200, "succeeded")
    assert not index.exists() and bam.exists()


def test_mcp_offers_deleting_tools_only_with_allow_destructive(work):
    default = Mcp("--allow", str(work), "--allow-jobs")
    names = {t["name"]: t for t in default.request("tools/list", {})["result"]["tools"]}
    assert default.close() == 0
    assert "bam_unindex" not in names and "sample_unset" not in names
    assert "ref_library_delete" not in names
    assert names["ref_gene_map"]["annotations"]["destructiveHint"] is False
    allowed = Mcp("--allow", str(work), "--allow-jobs", "--allow-destructive")
    names = {t["name"]: t for t in allowed.request("tools/list", {})["result"]["tools"]}
    assert names["bam_unindex"]["annotations"]["destructiveHint"] is True
    assert names["ref_gene_map"]["annotations"]["destructiveHint"] is True
    assert names["ref_library_delete"]["annotations"]["destructiveHint"] is True
    assert names["bam_index"]["annotations"]["destructiveHint"] is False
    assert allowed.close() == 0
