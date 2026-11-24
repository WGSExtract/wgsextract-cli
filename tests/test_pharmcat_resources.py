import hashlib
import io
import random
import tarfile
import threading
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from tests.http_server import RangeServer
from wgsextract_cli.api import CancelToken
from wgsextract_cli.api._runtime import Cancelled
from wgsextract_cli.core import execution_context
from wgsextract_cli.core import pharmcat_resources as resources
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.network_policy import DownloadRequired
from wgsextract_cli.core.ref_library import downloads
from wgsextract_cli.core.utils import WGSExtractError, run_command

pytestmark = needs_htslib


@pytest.fixture(scope="module")
def reference_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    root = tmp_path_factory.mktemp("pharmcat-reference")
    sequence = "".join(random.Random(1146).choices("ACGT", k=2_000_000))
    plain = write_fasta(root / "reference.fa", {"chr1": sequence})
    reference = root / resources.REFERENCE_FASTA
    with reference.open("wb") as stream:
        run_command(["bgzip", "-@", "1", "-c", str(plain)], stdout=stream)
    run_command(["samtools", "faidx", str(reference)])
    return {
        name: (root / name).read_bytes()
        for name in (
            resources.REFERENCE_FASTA,
            resources.REFERENCE_FASTA + ".fai",
            resources.REFERENCE_FASTA + ".gzi",
        )
    }


def reference_archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


@pytest.mark.parametrize("truncated", [True, False], ids=["truncated", "wrong-bytes"])
def test_pharmcat_bad_reference_download_is_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    truncated: bool,
    reference_files: dict[str, bytes],
) -> None:
    complete = reference_archive(reference_files)
    bad = complete[: len(complete) // 2] if truncated else b"x" * len(complete)
    with closing(RangeServer(bad)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        with pytest.raises(WGSExtractError, match="verify PharmCAT"):
            resources.ensure_reference(str(tmp_path))
        assert not any((tmp_path / name).exists() for name in reference_files)
        server.data = complete
        reference = resources.ensure_reference(str(tmp_path))
    assert Path(reference).read_bytes() == reference_files[resources.REFERENCE_FASTA]
    assert {
        name: (tmp_path / name).read_bytes() for name in reference_files
    } == reference_files


def test_pharmcat_partial_upstream_extraction_is_repaired_and_stays_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference_files: dict[str, bytes],
) -> None:
    (tmp_path / resources.REFERENCE_FASTA).write_bytes(b"truncated upstream extraction")
    files = reference_files
    complete = reference_archive(files)
    with closing(RangeServer(complete)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        resources.ensure_reference(str(tmp_path))
    monkeypatch.setattr(resources, "REFERENCE_URL", "https://invalid.invalid/offline")
    resources.ensure_reference(str(tmp_path))
    assert {name: (tmp_path / name).read_bytes() for name in files} == files


@pytest.mark.parametrize("index", [".gzi", ".fai"])
def test_1146_nonempty_damaged_reference_index_requires_consent_and_is_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference_files: dict[str, bytes],
    index: str,
) -> None:
    for name, data in reference_files.items():
        (tmp_path / name).write_bytes(data)
    broken = tmp_path / (resources.REFERENCE_FASTA + index)
    broken.write_bytes(
        reference_files[broken.name][:-1]
        if index == ".gzi"
        else b"chr1\t2000000\t6\t0\t1\n"
    )
    complete = reference_archive(reference_files)
    with closing(RangeServer(complete)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        with execution_context.bound(allow_downloads=False):
            with pytest.raises(DownloadRequired, match="--download"):
                resources.ensure_reference(str(tmp_path))
        assert not server.gets()
        with execution_context.bound(allow_downloads=True):
            resources.ensure_reference(str(tmp_path))
    assert broken.read_bytes() == reference_files[broken.name]


def test_1146_concurrent_first_use_reuses_the_first_completed_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference_files: dict[str, bytes],
) -> None:
    complete = reference_archive(reference_files)
    start = threading.Barrier(3)

    def install() -> str:
        start.wait(timeout=10)
        return resources.ensure_reference(str(tmp_path))

    with closing(RangeServer(complete)) as server:
        server.delays["hs38.fa.gz"] = 0.1
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        with ContextThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(install), pool.submit(install)
            start.wait(timeout=10)
            reference = first.result(timeout=15)
            assert second.result(timeout=15) == reference
        assert len(server.gets()) == 1
    assert {
        name: (tmp_path / name).read_bytes() for name in reference_files
    } == reference_files


def test_1146_reference_download_resumes_after_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference_files: dict[str, bytes],
) -> None:
    complete = reference_archive(reference_files)

    def interrupted_urlopen(request: Request, timeout: float) -> Any:
        response = urlopen(request, timeout=timeout)
        read: Callable[[int], bytes] = response.read
        received = False

        def interrupted_read(size: int = -1) -> bytes:
            nonlocal received
            if received:
                raise KeyboardInterrupt
            received = True
            return read(size)

        response.read = interrupted_read
        return response

    with closing(RangeServer(complete)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        with monkeypatch.context() as network:
            network.setattr(downloads, "urlopen", interrupted_urlopen)
            with pytest.raises(KeyboardInterrupt):
                resources.ensure_reference(str(tmp_path))
        partial = next(tmp_path.glob("*.partial"))
        received = partial.stat().st_size
        assert 0 < received < len(complete)
        assert not (tmp_path / resources.REFERENCE_FASTA).exists()
        reference = resources.ensure_reference(str(tmp_path))
        assert server.gets() == [None, f"bytes={received}-"]
    assert Path(reference).read_bytes() == reference_files[resources.REFERENCE_FASTA]
    assert not list(tmp_path.glob("*.partial*"))
    assert not list(tmp_path.glob("*.tar"))


def test_1146_cancelled_reference_transfer_neither_retries_nor_extracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference_files: dict[str, bytes],
) -> None:
    complete = reference_archive(reference_files)
    token = CancelToken()

    with closing(RangeServer(complete)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        started, release = threading.Event(), threading.Event()
        server.body_pauses["hs38.fa.gz"] = (256 * 1024, started, release)
        with ContextThreadPoolExecutor(max_workers=1) as pool:
            with execution_context.bound(cancel=token):
                transfer = pool.submit(resources.ensure_reference, str(tmp_path))
            try:
                assert started.wait(5)
                partial = tmp_path / ".pharmcat-reference.tar.partial"
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if partial.exists() and partial.stat().st_size > 0:
                        break
                    time.sleep(0.01)
                assert partial.exists() and partial.stat().st_size > 0
                token.cancel()
                release.set()
                with pytest.raises(Cancelled):
                    transfer.result(timeout=5)
            finally:
                release.set()
                server.body_pauses.clear()
        assert server.gets() == [None]
        assert not any((tmp_path / name).exists() for name in reference_files)
        assert 0 < partial.stat().st_size < len(complete)
        resources.ensure_reference(str(tmp_path))
        assert len(server.gets()) == 2
    assert {
        name: (tmp_path / name).read_bytes() for name in reference_files
    } == reference_files


def test_1146_cancelled_waiter_stops_while_first_installer_keeps_downloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference_files: dict[str, bytes],
) -> None:
    complete = reference_archive(reference_files)
    receiving, release, waiting = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    token = CancelToken()
    sleep = time.sleep

    def blocked_urlopen(request: Request, timeout: float) -> Any:
        response = urlopen(request, timeout=timeout)
        read: Callable[[int], bytes] = response.read

        def blocked_read(size: int = -1) -> bytes:
            receiving.set()
            if not release.wait(timeout=15):
                raise TimeoutError("test transfer was not released")
            return read(size)

        response.read = blocked_read
        return response

    def waiting_sleep(seconds: float) -> None:
        waiting.set()
        sleep(seconds)

    with closing(RangeServer(complete)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        monkeypatch.setattr(downloads, "urlopen", blocked_urlopen)
        with ContextThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(resources.ensure_reference, str(tmp_path))
            try:
                assert receiving.wait(timeout=5)
                monkeypatch.setattr(time, "sleep", waiting_sleep)
                with execution_context.bound(cancel=token):
                    second = pool.submit(resources.ensure_reference, str(tmp_path))
                assert waiting.wait(timeout=5)
                token.cancel()
                with pytest.raises(Cancelled):
                    second.result(timeout=2)
                assert not first.done()
            finally:
                release.set()
            assert Path(first.result(timeout=10)).is_file()
        assert server.gets() == [None]


def test_pharmcat_incomplete_archive_does_not_publish_a_fasta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = reference_archive({resources.REFERENCE_FASTA: b"reference"})
    with closing(RangeServer(complete)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        with pytest.raises(WGSExtractError, match="Invalid PharmCAT reference archive"):
            resources.ensure_reference(str(tmp_path))
    assert not any((tmp_path / name).exists() for name in resources.REFERENCE_FILES)
