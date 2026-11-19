import hashlib
import io
import tarfile
from contextlib import closing
from pathlib import Path

import pytest

from tests.http_server import RangeServer
from wgsextract_cli.core import pharmcat_resources as resources
from wgsextract_cli.core.utils import WGSExtractError


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, truncated: bool
) -> None:
    complete = reference_archive(dict.fromkeys(resources.REFERENCE_FILES, b"reference"))
    bad = complete[: len(complete) // 2] if truncated else b"x" * len(complete)
    with closing(RangeServer(bad)) as server:
        monkeypatch.setattr(resources, "REFERENCE_URL", server.url)
        monkeypatch.setattr(
            resources, "REFERENCE_SHA256", hashlib.sha256(complete).hexdigest()
        )
        with pytest.raises(WGSExtractError, match="verify PharmCAT"):
            resources.ensure_reference(str(tmp_path))
        assert not list(tmp_path.iterdir())
        server.data = complete
        reference = resources.ensure_reference(str(tmp_path))
    assert Path(reference).read_bytes() == b"reference"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        resources.REFERENCE_FILES
    )


def test_pharmcat_partial_upstream_extraction_is_repaired_and_stays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / resources.REFERENCE_FASTA).write_bytes(b"truncated upstream extraction")
    files = {name: name.encode() for name in resources.REFERENCE_FILES}
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
    assert not list(tmp_path.iterdir())
