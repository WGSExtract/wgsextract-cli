"""Reference-library and dependency queries exposed by the server."""

from pathlib import Path

import pytest

from tests.server_helpers import _serve
from tests.server_helpers import client as client
from tests.server_helpers import work as work
from wgsextract_cli.server.operations import RequestError, find, params_from_json


def test_ref_library_list_is_a_query_with_the_listing_as_details(client, work):
    (work / "genomes").mkdir()
    (work / "genomes" / "hs38.fa.gz").write_bytes(b"x")
    _, ops = client.request("GET", "/ops")
    assert {op["op"]: op["kind"] for op in ops["ops"]}["ref/library-list"] == "query"

    status, job = client.request("POST", "/ops/ref/library-list", {"ref": str(work)})
    assert status == 202, job
    client.events(job["id"])
    _, job = client.request("GET", f"/jobs/{job['id']}")
    assert job["status"] == "succeeded", job
    details = job["result"]["details"]
    assert details["library"] == str(work)
    assert {g["code"]: g["status"] for g in details["genomes"]}["hs38"] == ("unindexed")
    assert details["genomes_dir"] == str(work / "genomes")
    assert {g["code"]: g["size_bytes"] for g in details["genomes"]}["hs38"] == 1


def test_read_only_library_list_cannot_look_up_download_sizes():
    # #825: remote_sizes writes the library's size cache, so read-only MCP
    # refuses it.
    op = find("ref/library-list")
    assert "remote_sizes" not in op.schema(read_only=True)["properties"]
    with pytest.raises(RequestError) as refused:
        params_from_json(op, {"remote_sizes": True}, read_only=True)
    assert refused.value.code == "read_only"


def test_ref_library_requests_must_name_a_genome():
    # PR #458 review: a request without `install` was accepted as a job.
    with pytest.raises(RequestError, match="ref library --list") as refused:
        params_from_json(find("ref/library"), {})
    assert refused.value.code == "usage"


def test_deps_check_answers_with_the_tool_path(tmp_path, work):
    with _serve(tmp_path, [work]) as client:
        status, body = client.request(
            "POST", "/ops/deps/check?wait=60", {"tool": "samtools"}
        )
    assert (status, body["status"]) == (200, "succeeded"), body
    assert body["kind"] == "query"
    assert Path(body["result"]["details"]["path"]).name.startswith("samtools")
