"""CRAM references are looked up only on this computer (#636).

A tool decoding a CRAM it wasn't given the reference for searches REF_PATH,
whose http entries (and, before htslib 1.22, its default) are a reference
server. A local HTTP server stands in for EBI's.
"""

import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.fake_tools import FakeDocker
from tests.htslib_helpers import needs_htslib, write_fasta
from wgsextract_cli import api
from wgsextract_cli.core import containers
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.reference_lookup import local_ref_path
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.main import main

SEQUENCE = "ACGTTGCAAGGCTTAC" * 125


class _ReferenceServer(ThreadingHTTPServer):
    requests: list[str]


class _Handler(BaseHTTPRequestHandler):
    server: _ReferenceServer

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def reference_server() -> Iterator[_ReferenceServer]:
    server = _ReferenceServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=10)
    server.server_close()


def _server_url(server: _ReferenceServer) -> str:
    # htslib's REF_PATH keeps a URL's host:port together.
    return f"http://127.0.0.1:{server.server_address[1]}/%s"


@pytest.fixture
def orphan_cram(tmp_path: Path) -> Path:
    """An indexed CRAM whose @SQ UR: names a reference that has been deleted."""
    ref = write_fasta(tmp_path / "gone" / "ref.fa", {"chr1": SEQUENCE})
    sam = tmp_path / "reads.sam"
    reads = [
        f"r{i}\t0\tchr1\t{i * 50 + 1}\t60\t50M\t*\t0\t0\t"
        f"{SEQUENCE[i * 50 : i * 50 + 50]}\t{'I' * 50}"
        for i in range(30)
    ]
    sam.write_text(
        "\n".join(["@HD\tVN:1.6\tSO:coordinate", "@SQ\tSN:chr1\tLN:2000", *reads])
        + "\n"
    )
    cram = tmp_path / "reads.cram"
    samtools = get_tool_path("samtools")
    run_command([samtools, "view", "-C", "-T", str(ref), "-o", str(cram), str(sam)])
    run_command([samtools, "index", str(cram)])
    for path in ref.parent.iterdir():
        path.unlink()
    return cram


@needs_htslib
def test_cli_cram_without_its_reference_fails_without_asking_a_server(
    tmp_path, orphan_cram, reference_server, monkeypatch, capfd
):
    monkeypatch.setenv("REF_PATH", _server_url(reference_server))

    with pytest.raises(SystemExit) as exit_:
        main(["bam", "to-bam", "-i", str(orphan_cram), "-o", str(tmp_path / "out")])

    assert exit_.value.code == 1
    assert "Unable to fetch reference" in capfd.readouterr().err
    assert reference_server.requests == []


@needs_htslib
def test_api_cram_without_its_reference_fails_without_asking_a_server(
    tmp_path, orphan_cram, reference_server, monkeypatch
):
    monkeypatch.setenv("REF_PATH", _server_url(reference_server))

    result = api.bam.to_bam(
        api.BamToBamParams(input=orphan_cram, outdir=tmp_path / "out"), check=False
    )

    assert result.exit_code == 1
    assert reference_server.requests == []


@needs_htslib
def test_a_local_reference_cache_in_ref_path_still_decodes(
    tmp_path, orphan_cram, reference_server, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()
    md5 = hashlib.md5(SEQUENCE.encode()).hexdigest()
    (cache / md5).write_text(SEQUENCE)
    remote = _server_url(reference_server)
    monkeypatch.setenv("REF_PATH", f"{remote}:{cache}/%s")

    out = tmp_path / "out"
    main(["bam", "to-bam", "-i", str(orphan_cram), "-o", str(out)])

    view = run_command(
        [get_tool_path("samtools"), "view", "-c", str(out / "reads.bam")],
        capture_output=True,
    )
    assert view.stdout.strip() == "30"
    assert reference_server.requests == []


@pytest.mark.parametrize("host", ["http://refs.example:8080/%s:/srv/refs/%s", None])
def test_containers_get_a_local_ref_path_of_their_own(tmp_path, monkeypatch, host):
    # #636: the image's htslib may be old enough to ask EBI when REF_PATH is
    # unset; the host's REF_PATH entries don't belong in the container.
    (tmp_path / "bin").mkdir()
    docker = FakeDocker(tmp_path / "bin", tmp_path / "dv")
    docker.add_image("image")
    monkeypatch.setattr(
        containers, "get_tool_path", lambda _tool: str(tmp_path / "bin" / "docker")
    )
    if host is None:
        monkeypatch.delenv("REF_PATH", raising=False)
    else:
        monkeypatch.setenv("REF_PATH", host)

    containers.run_container("image", ["/bin/true"], [str(tmp_path)])

    ref_path = docker.runs()[-1]["env"]["REF_PATH"]
    assert ref_path and "://" not in ref_path
    assert not ref_path.startswith((str(tmp_path), "/srv"))


@pytest.mark.parametrize(
    ("ref_path", "sep", "local"),
    [
        ("https://www.ebi.ac.uk/ena/cram/md5/%s:/srv/a::b/%s", ":", "/srv/a::b/%s"),
        ("https://www.ebi.ac.uk/ena/cram/md5/%s;C:\\refs\\a::b\\%s", ";",
         "C:\\refs\\a::b\\%s"),
    ],
)  # fmt: skip
def test_ref_path_keeps_local_entries_as_written(ref_path, sep, local):
    # #636: '::' escapes a colon only where ':' separates entries.
    assert local_ref_path(ref_path, sep) == local
