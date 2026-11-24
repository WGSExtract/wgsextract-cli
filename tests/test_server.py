"""`wgsextract serve` (#351): shared helpers and basic transport contracts.

Focused jobs, paths, lifecycle, MCP and lookup cases live in sibling
test_server_* modules; the fixture/helper imports used by other suites stay here.
"""

import contextlib
import http.client
import importlib.util
import json
import ntpath
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.fake_tools import python_tool
from tests.smoke_utils import ensure_fake_data, get_cli_command
from wgsextract_cli.core import results
from wgsextract_cli.core.config import get_config_path
from wgsextract_cli.server.http_api import Server
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    operations,
    params_from_json,
)

CLI_ROOT = Path(__file__).resolve().parents[1]
FAKE_DIR = CLI_ROOT / "out" / "fake_30x"
VALIDATOR = Draft202012Validator(results.load_schema())
JOB_VALIDATOR = Draft202012Validator(
    {
        **json.loads(
            (
                CLI_ROOT
                / "src/wgsextract_cli/assets/schemas/wgsextract.server.responses.1.schema.json"
            ).read_text(encoding="utf-8")
        ),
        "$ref": "#/$defs/Job",
    }
)
TOKEN = "test-token"


@pytest.fixture
def fake_data() -> tuple[Path, Path]:
    if not shutil.which("samtools") or not shutil.which("bcftools"):
        pytest.skip("samtools/bcftools not found in PATH")
    ensure_fake_data(str(FAKE_DIR))
    return FAKE_DIR / "fake.bam", FAKE_DIR / "fake_ref.fa"


class Client:
    def __init__(self, url: str) -> None:
        self.url = url + "/v1"

    def request(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, _decode(response)
        except urllib.error.HTTPError as e:
            return e.code, _decode(e)

    def events(self, job_id: str) -> list[dict[str, Any]]:
        """A job's server-sent events, read until the stream ends."""
        request = urllib.request.Request(f"{self.url}/jobs/{job_id}/events")
        request.add_header("Authorization", f"Bearer {TOKEN}")
        with urllib.request.urlopen(request, timeout=300) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            text = response.read().decode()
        return [
            json.loads(line.removeprefix("data: "))
            for line in text.splitlines()
            if line.startswith("data: ")
        ]


def _decode(response: Any) -> Any:
    text = response.read().decode()
    if response.headers.get("Content-Type") == "application/json":
        return json.loads(text)
    return text


@pytest.fixture
def work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


@contextlib.contextmanager
def _serve(
    tmp_path: Path,
    roots: list[Path],
    max_jobs: int = 1,
    *,
    allow_destructive: bool = False,
    card: bool | None = None,
) -> Iterator[Client]:
    guard = PathGuard(roots, allow_destructive=allow_destructive)
    manager = JobManager(
        tmp_path / "state" / "jobs",
        config_path=get_config_path(),
        max_jobs=max_jobs,
        allowed=roots,
        card=card,
    )
    server = Server(0, TOKEN, manager, guard)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(server.url)
    finally:
        server.shutdown()
        server.server_close()
        manager.shutdown()


@pytest.fixture
def client(tmp_path: Path, work: Path) -> Iterator[Client]:
    with _serve(tmp_path, [work, FAKE_DIR]) as client:
        yield client


def test_requests_need_the_token_and_a_local_host(client):
    assert client.request("GET", "/health", token=None)[0] == 200
    for token in (None, "wrong"):
        status, body = client.request("GET", "/ops", token=token)
        assert (status, body["error"]["code"]) == (401, "unauthorized")
    status, body = client.request("GET", "/ops", headers={"Host": "evil.example"})
    assert (status, body["error"]["code"]) == (403, "bad_host")
    status, body = client.request(
        "GET", "/ops", headers={"Origin": "https://evil.example"}
    )
    assert (status, body["error"]["code"]) == (403, "bad_origin")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ([("Content-Length", "-1")], 400),
        ([], 411),
        ([("Transfer-Encoding", "chunked")], 400),
        ([("Content-Length", "0"), ("Content-Length", "0")], 400),
        ([("Content-Length", "\xa00")], 400),
    ],
)
def test_bad_lengths_are_refused_and_errors_close_the_connection(
    client, headers, status
):
    # #1209: invalid or missing framing must be rejected before reading a body.
    host, port = client.url.removeprefix("http://").removesuffix("/v1").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=30)
    connection.putrequest("POST", "/v1/ops/info")
    connection.putheader("Authorization", "Bearer " + TOKEN)
    connection.putheader("Content-Type", "application/json")
    for name, value in headers:
        connection.putheader(name, value)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == status
    assert json.loads(response.read())["error"]["code"] == "bad_length"
    assert response.headers["Connection"] == "close"
    connection.close()


@pytest.mark.parametrize(
    ("length", "expected"),
    [(1, (409, "job_id_in_use")), (2, (400, "bad_length")), (2, None)],
    ids=["complete-malformed-json", "truncated-body", "stalled-body"],
)
def test_1297_duplicate_job_reply_checks_body_framing(client, work, length, expected):
    job_id = "86f82f25-86e5-423f-acec-1c996d97e259"
    path = "/ops/example-genome/list"
    status, job = client.request(
        "POST",
        path + "?wait=30",
        {"target_root": str(work)},
        headers={"X-WGSE-Job-ID": job_id},
    )
    assert (status, job["id"], job["status"]) == (200, job_id, "succeeded")

    # Headers and body can arrive separately. Closing with unread bytes can
    # abort the connection before the client sees the 409 on Windows.
    host, port = client.url.removeprefix("http://").removesuffix("/v1").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=30)
    try:
        connection.putrequest("POST", "/v1" + path)
        connection.putheader("Authorization", "Bearer " + TOKEN)
        connection.putheader("X-WGSE-Job-ID", job_id)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(length))
        connection.endheaders()
        connection.send(b"{")
        if expected is None:
            # Leave the write side open so only the input deadline ends the read.
            with pytest.raises(
                (
                    http.client.RemoteDisconnected,
                    ConnectionAbortedError,
                    ConnectionResetError,
                )
            ):
                connection.getresponse()
            return
        if length == 2:
            assert connection.sock is not None
            connection.sock.shutdown(socket.SHUT_WR)
        with connection.getresponse() as response:
            assert (
                response.status,
                json.loads(response.read())["error"]["code"],
            ) == expected
    finally:
        connection.close()


def test_1209_job_log_tails_text_and_disallows_caching(client, tmp_path, work):
    status, job = client.request(
        "POST", "/ops/example-genome/list?wait=10", {"target_root": str(work)}
    )
    assert status == 200
    job_id = job["id"]
    content = b"old\r\n" * 20_000 + b"boundary\r\n" + b"x" * 65_530 + b"\r\nend"
    (tmp_path / "state" / "jobs" / f"{job_id}.log").write_bytes(content)
    path = f"/jobs/{job_id}/log"

    assert client.request("GET", f"{path}?tail=-1")[0] == 400
    request = urllib.request.Request(
        client.url + path + "?tail=2", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.read() == b"x" * 65_530 + b"\nend\n"
    assert client.request("GET", f"{path}?tail=0") == (
        200,
        content.decode().replace("\r\n", "\n") + "\n",
    )


def test_a_client_resetting_its_connection_prints_no_traceback(client, capfd):
    # Issue #554: on Windows, PowerShell's Invoke-RestMethod resets its idle
    # keep-alive connection, and `wgsextract serve` printed a ConnectionResetError
    # traceback on stderr for each one.
    host, port = client.url.removeprefix("http://").removesuffix("/v1").split(":")
    before = set(threading.enumerate())

    def handlers() -> set[threading.Thread]:
        return {t for t in set(threading.enumerate()) - before if t.is_alive()}

    with socket.create_connection((host, int(port)), timeout=30) as sock:
        # An unfinished request line: its handler waits to read the rest.
        sock.sendall(b"GET /v1/health HTTP/1.1")
        deadline = time.monotonic() + 30
        while not handlers() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert handlers(), "the server never picked up the connection"
        # Linger 0: closing sends a reset, as Windows does.
        linger = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
    deadline = time.monotonic() + 30
    while handlers() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not handlers(), "the reset connection's handler never finished"
    stderr = capfd.readouterr().err
    assert client.request("GET", "/health", token=None)[0] == 200
    assert "Traceback" not in stderr


class _RecordingFlavour:
    """Windows path rules on any OS, recording what would touch the disk."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(ntpath, name)

    def abspath(self, path: str) -> str:
        assert ntpath.isabs(path), path  # else it would read the working folder
        return ntpath.normpath(path)

    def realpath(self, path: str) -> str:
        self.opened.append(path)
        return ntpath.normpath(path)


_IN = r"C:\data\a.bam"


_ALIGN_REQUESTS = {
    "align": {"r1": r"C:\data\r1.fq", "ref": r"C:\data\ref.fa"},
    "realign": {"input": r"C:\data\a.bam", "ref": r"C:\data\ref.fa"},
}


_VCF_FILE_OPS = (
    "annotate", "filter", "table", "to-fasta", "trio", "clinvar", "revel",
    "phylop", "cadd", "gerp", "gnomad", "spliceai", "alphamissense", "pharmgkb",
    "pharmcat", "chain-annotate",
)  # fmt: skip
_VCF_PATHS = [
    (f"vcf/{name}", item)
    for name in _VCF_FILE_OPS
    for item in find(f"vcf/{name}").fields()
    if item.spec.path and not item.spec.local_only
]


_REF_PATHS = [
    (name, item)
    for name, op in operations().items()
    if name.startswith("ref/")
    for item in op.fields()
    if item.spec.path and not item.spec.local_only
]


_PET_ANALYZE_REQUESTS = {
    "pet-align": {"r1": r"C:\data\r1.fq", "species": "dog"},
    "analyze/comprehensive": {"input": _IN},
    "analyze/batch-gen": {
        "directory": r"C:\data",
        "output": r"C:\data\batch.csv",
    },
}
_PET_ANALYZE_PATHS = [
    (op, item)
    for op in _PET_ANALYZE_REQUESTS
    for item in find(op).fields()
    if item.spec.path and not item.spec.local_only
]


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


_CALLER_OPS = ("cnv", "sv", "freebayes", "gatk", "deepvariant", "clair3", "phase")
_CALLER_PATHS = [
    (f"vcf/{name}", item)
    for name in _CALLER_OPS
    for item in find(f"vcf/{name}").fields()
    if item.spec.path or item.spec.may_name_file
]


_REPAIR_PATHS = [
    (op, item)
    for op in ("repair/ftdna-bam", "repair/ftdna-vcf")
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]


_SIGN_PATHS = [
    (op, item)
    for op in ("sign/create", "sign/verify")
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]
_SIGN_REQUESTS = {
    "sign/create": {"paths": [r"C:\data\a.bam"], "outdir": r"C:\data"},
    "sign/verify": {"manifest": r"C:\data\SHA256SUMS"},
}


def test_deps_check_answers_with_the_tool_path(tmp_path, work):
    with _serve(tmp_path, [work]) as client:
        status, body = client.request(
            "POST", "/ops/deps/check?wait=60", {"tool": "samtools"}
        )
    assert (status, body["status"]) == (200, "succeeded"), body
    assert body["kind"] == "query"
    assert Path(body["result"]["details"]["path"]).name.startswith("samtools")


_EXAMPLE_PATHS = [
    (op, item)
    for op in ("example-genome/list", "example-genome/download")
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]


_TYPING_PATHS = [
    (op, item)
    for op in (
        "typing/cyp2d6",
        "typing/hla",
        "typing/smn",
        "typing/str",
        "typing/target-cnv",
    )
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]


_VEP_PATHS = [
    item
    for item in find("vep/run").fields()
    if item.spec.path or item.spec.may_name_file
]


_METHYLATION_PATHS = [
    item
    for item in find("methylation/pileup").fields()
    if item.spec.path or item.spec.may_name_file
]


# Each backend's region list: DeepVariant splits on whitespace, Parabricks
# (deepvariant/gatk --gpu) and GATK on commas.
_REGION_LISTS = [
    ("vcf/deepvariant", {}, " "),
    ("vcf/deepvariant", {"gpu": True}, ","),
    ("vcf/gatk", {}, ","),
]


SLOW_SAMTOOLS = """
import os, subprocess, sys, time
if sys.argv[1:2] == ["coverage"]:
    open(os.path.join({pids!r}, str(os.getpid())), "w").close()
    time.sleep(600)
sys.exit(subprocess.run([{real!r}, *sys.argv[1:]]).returncode)
"""


@pytest.fixture
def slow_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_data) -> Path:
    """`samtools coverage` hangs (a very slow tool); other subcommands are real.
    Returns the folder where each hanging call records its pid."""
    if sys.platform == "win32":
        pytest.skip(
            "Windows starts a bare 'samtools' with CreateProcess, which finds "
            "only .exe files, so a scripted fake can't replace it"
        )
    pids = tmp_path / "pids"
    pids.mkdir()
    real = shutil.which("samtools")
    source = SLOW_SAMTOOLS.format(pids=str(pids), real=real)
    bindir = python_tool(tmp_path / "bin", "samtools", source).parent
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return pids


def _job_logs(tmp_path: Path) -> str:
    """The workers' logs, to show why a job got stuck."""
    return "\n".join(
        f"== {log.name}\n{log.read_text(errors='replace')}"
        for log in sorted((tmp_path / "state" / "jobs").glob("*.log"))
    )


def _wait_for(predicate: Any, tmp_path: Path, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"timed out\n{_job_logs(tmp_path)}"
        time.sleep(0.2)


def interrupt_serve_announcement(
    work: Path, stop_signal: int, *args: str
) -> subprocess.CompletedProcess[str]:
    # The OS stream boundary: interrupt at the ready line, before HTTP startup,
    # rather than depending on subprocess scheduling.
    code = f"""
import os, signal, sys
from wgsextract_cli.main import main
class InterruptOnReady:
    def write(self, text):
        result = sys.__stdout__.write(text)
        if '"url"' in text:
            sys.__stdout__.flush()
            os.kill(os.getpid(), {stop_signal})
        return result
    def flush(self):
        sys.__stdout__.flush()
sys.stdout = InterruptOnReady()
main()
"""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "serve",
            "--ready-json",
            "--allow",
            str(work),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


class Mcp:
    """A `wgsextract serve --mcp` subprocess, spoken to line by line."""

    def __init__(self, *args: str) -> None:
        self.proc = subprocess.Popen(
            [*get_cli_command(), "serve", "--mcp", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.messages: Queue[dict[str, Any]] = Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        reply = self.request(
            "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}
        )
        assert reply["result"]["protocolVersion"] == "2025-06-18"
        self.send({"method": "notifications/initialized"})

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.messages.put(json.loads(line))

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: Any) -> dict[str, Any]:
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        while True:
            try:
                message = self.messages.get(timeout=300)
            except Empty:
                raise AssertionError(f"no reply to {method}") from None
            if message.get("id") == self.next_id:
                return message

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        return reply.get("result") or reply

    def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        return self.proc.wait(60)


def _indexed_bam(work: Path, bam: Path) -> Path:
    copy = work / "s.bam"
    shutil.copyfile(bam, copy)
    subprocess.run(["samtools", "index", str(copy)], check=True)
    return copy


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
##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: Allele|Consequence|IMPACT|SYMBOL|Feature">
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
                        + ("ENST4" if rsid == "rs4" else ""),
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
