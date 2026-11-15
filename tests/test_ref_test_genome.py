"""#825: `ref test-genome` reports, installs and deletes the test genome, as
the app's Datasets page did with its own script."""

import hashlib
import io
import json
import os
import stat
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.http_server import RangeServer, serving
from wgsextract_cli.core import mini_genome
from wgsextract_cli.main import main

FILES = {
    "bam": "S.bam",
    "vcf": "S.vcf.gz",
    "fastq_r1": "S_R1.fastq.gz",
    "fastq_r2": "S_R2.fastq.gz",
    "ref": "mini.fa.gz",
}


def _dataset_zip(unsafe: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("hg19-mini/manifest.json", json.dumps({"files": FILES}))
        for name in FILES.values():
            zipped.writestr(f"hg19-mini/{name}", name)
        if unsafe == "outside":
            zipped.writestr("../evil", "x")
        elif unsafe == "link":
            link = zipfile.ZipInfo("hg19-mini/link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            zipped.writestr(link, "/etc/passwd")
    return buffer.getvalue()


@contextmanager
def _published(monkeypatch, data: bytes) -> Iterator[RangeServer]:
    """``data`` served locally as the test genome's release asset."""
    with serving(data=None, files={"mini.zip": data}) as http:
        monkeypatch.setattr(mini_genome, "URL", http.url_for("mini.zip"))
        monkeypatch.setattr(mini_genome, "SHA256", hashlib.sha256(data).hexdigest())
        yield http


@pytest.fixture
def published(monkeypatch):
    with _published(monkeypatch, _dataset_zip()) as http:
        yield http


def _run(capsys, datasets: Path, *flags: str) -> dict:
    try:
        main(["--json", "ref", "test-genome", "--dataset-library", str(datasets),
              *flags])  # fmt: skip
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_install_status_and_delete(tmp_path, published, capsys):
    datasets = tmp_path / "datasets"
    genome = datasets / mini_genome.DATASET_ID

    assert _run(capsys, datasets)["details"]["status"] == "missing"

    installed = _run(capsys, datasets, "--install")
    assert installed["details"] == {
        "library": str(datasets),
        "installed": True,
        "status": "installed",
        "path": str(genome),
        "changed": True,
    }
    config = (genome / "genome-config.toml").read_text()
    assert 'alignment = "S.bam"' in config and 'fastq_r2 = "S_R2.fastq.gz"' in config
    assert (genome / "S.vcf.gz").read_text() == "S.vcf.gz"
    assert (datasets / ".downloads" / f"{mini_genome.DATASET_ID}.zip").is_file()

    # Complete: nothing is fetched again. A file gone: unpacked again from
    # the kept zip, without a download.
    assert _run(capsys, datasets, "--install")["details"]["changed"] is False
    (genome / "S.bam").unlink()
    requests = len(published.requests)
    assert _run(capsys, datasets, "--install")["details"]["changed"] is True
    assert (genome / "S.bam").is_file() and len(published.requests) == requests

    size = sum(p.stat().st_size for p in datasets.rglob("*") if p.is_file())
    deleted = _run(capsys, datasets, "--delete")["details"]
    assert deleted["status"] == "missing" and deleted["bytes_freed"] == size
    assert _tree(datasets) == []
    assert _run(capsys, datasets, "--delete")["details"]["deleted"] == []


def test_a_download_that_fails_its_hash_installs_nothing(
    tmp_path, published, monkeypatch, capsys
):
    monkeypatch.setattr(mini_genome, "SHA256", "0" * 64)
    datasets = tmp_path / "datasets"

    result = _run(capsys, datasets, "--install")

    assert result["status"] == "failed" and "SHA-256" in result["error"]["message"]
    assert not (datasets / mini_genome.DATASET_ID).exists()
    assert not (datasets / ".downloads" / f"{mini_genome.DATASET_ID}.zip").exists()


@pytest.mark.parametrize(
    "plan_path",
    [
        "scripts/installlab/gui/wgsextract-plan.json",
        "gui/examples/WGSExtract/test-plans/fresh-install-default-hg19-test-genome.json",
    ],
    ids=["install-lab", "fresh-install"],
)
def test_issue_801_download_satisfies_app_required_output(
    tmp_path, published, capsys, plan_path
):
    datasets = tmp_path / "datasets"
    plan = json.loads((Path(__file__).resolve().parents[1] / plan_path).read_text())
    download = next(
        step
        for step in plan["steps"]
        if step.get("actionID") == "test-dataset-download"
    )

    main(["ref", "test-genome", "--install", "--dataset-library", str(datasets)])

    output = capsys.readouterr().out
    assert all(text in output for text in download["requiredOutput"])
    assert (datasets / mini_genome.DATASET_ID / "S.bam").is_file()


def test_a_partial_download_is_incomplete_and_deleted(tmp_path, capsys):
    datasets = tmp_path / "datasets"
    partial = datasets / ".downloads" / f"{mini_genome.DATASET_ID}.zip.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"half")

    assert _run(capsys, datasets)["details"]["status"] == "incomplete"
    assert _run(capsys, datasets, "--delete")["details"]["deleted"] == [str(partial)]
    assert not datasets.joinpath(".downloads").exists()


def test_delete_unlinks_a_linked_dataset_not_what_it_points_to(tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "genome-config.toml").write_text("")
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    (datasets / mini_genome.DATASET_ID).symlink_to(elsewhere)

    _run(capsys, datasets, "--delete")

    assert _tree(datasets) == [] and (elsewhere / "genome-config.toml").exists()


@pytest.mark.parametrize("unsafe", ["outside", "link"])
def test_issue_947_a_zip_with_an_unsafe_entry_installs_nothing(
    tmp_path, monkeypatch, capsys, unsafe
):
    datasets = tmp_path / "datasets"
    with _published(monkeypatch, _dataset_zip(unsafe)):
        result = _run(capsys, datasets, "--install")

    assert "Unsafe dataset zip entry" in result["error"]["message"]
    zip_name = f".downloads/{mini_genome.DATASET_ID}.zip"
    assert _tree(datasets) == [".downloads", zip_name]


def test_install_and_delete_together_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["ref", "test-genome", "--install", "--delete",
              "--dataset-library", str(tmp_path)])  # fmt: skip
    assert exit_info.value.code == 2
    assert os.listdir(tmp_path) == []
