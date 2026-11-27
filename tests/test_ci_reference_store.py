"""Certification reference sharing across real linked Git checkouts (#1637)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.ci_certify_inputs import check_reference_bytes, validate_reference
from scripts.ci_certify_setup import prepare_reference
from scripts.ci_reference_store import ReferenceStore
from wgsextract_cli.core import constants
from wgsextract_cli.core.config import get_config_path, settings
from wgsextract_cli.core.microarray import targets
from wgsextract_cli.core.ref_library import catalog

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix certification library aliases"
)


@pytest.fixture
def shared_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReferenceStore:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main", "primary")
    git(
        "-C",
        "primary",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Prepare worktrees",
    )
    for name in ("first", "second"):
        git("-C", "primary", "worktree", "add", "-b", name, str(tmp_path / name))
    (tmp_path / "primary/.git/info/exclude").write_text("/reference\n")
    fasta = b">tiny\nACGTACGT\n"
    monkeypatch.setattr(
        catalog,
        "get_available_genomes",
        lambda: [
            {
                "code": "hs38",
                "final": "hs38.fa.gz",
                "sha256": hashlib.sha256(fasta).hexdigest(),
            }
        ],
    )
    data = b"fixture\n"
    monkeypatch.setattr(
        targets,
        "HG19_TO_HG38_CHAIN",
        replace(targets.HG19_TO_HG38_CHAIN, sha256=hashlib.sha256(data).hexdigest()),
    )
    archive = tmp_path / constants.BOOTSTRAP_FILENAME
    with tarfile.open(archive, "w:gz") as stream:
        item = tarfile.TarInfo("reference/support.txt")
        item.size = len(data)
        stream.addfile(item, io.BytesIO(data))
    monkeypatch.setattr(
        constants, "BOOTSTRAP_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )

    store = ReferenceStore.for_checkout(tmp_path / "first")
    (store.library / "genomes").mkdir(parents=True)
    archive.rename(store.version / constants.BOOTSTRAP_FILENAME)
    (store.library / "support.txt").write_bytes(data)
    (store.library / targets.HG19_TO_HG38_CHAIN.name).write_bytes(data)
    (store.library / "genomes/hs38.fa.gz").write_bytes(fasta)
    for suffix in ("fai", "gzi"):
        (store.library / f"genomes/hs38.fa.gz.{suffix}").write_bytes(data)
    store.record_verified(
        check_reference_bytes(store.library, store.version, shared=True)
    )
    monkeypatch.chdir(tmp_path / "first")
    settings["reference_library"] = str(Path.cwd() / "reference")
    prepare_reference(download=False)
    return store


def test_1637_second_worktree_reuses_verified_inputs_and_rejects_a_changed_file(
    shared_reference: ReferenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Path.cwd() / "reference"
    monkeypatch.chdir(first.parent.parent / "second")
    second = Path.cwd() / "reference"
    settings["reference_library"] = str(second)
    prepare_reference(download=False)
    assert second.is_symlink()
    assert (first / "genomes/hs38.fa.gz").samefile(second / "genomes/hs38.fa.gz")
    validate_reference()

    support = shared_reference.library / "support.txt"
    original = support.stat()
    support.write_bytes(b"corrupt\n")
    os.utime(support, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ValueError, match="unverified"):
        validate_reference()
    with pytest.raises(ValueError, match="Verified inputs changed"):
        prepare_reference(download=False)


def test_1637_setup_does_not_reapprove_a_modified_fasta_index(
    shared_reference: ReferenceStore,
) -> None:
    index = shared_reference.library / "genomes/hs38.fa.gz.gzi"
    index.write_bytes(b"")
    for download in (False, True):
        with pytest.raises(ValueError, match="Verified inputs changed"):
            prepare_reference(download=download)
    with pytest.raises(ValueError, match="unverified"):
        validate_reference()


def test_1637_setup_loads_reference_settings_in_a_fresh_process(
    shared_reference: ReferenceStore,
) -> None:
    tools = Path(__file__).resolve().parents[1] / "scripts"
    configured = Path.cwd().parent / "personal-reference"
    with get_config_path().open("a", encoding="utf-8") as config:
        config.write(f"\nreference_library = {json.dumps(str(configured))}\n")
    run = subprocess.run(
        [sys.executable, str(tools / "ci_certify_setup.py"), "--link-reference"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    assert "Refusing to change configured reference library" in run.stderr
    assert str(configured) in run.stderr
    assert (Path.cwd() / "reference").resolve() == shared_reference.library


def test_1637_migration_only_links_ignored_byte_identical_fastas(
    shared_reference: ReferenceStore,
) -> None:
    local = Path.cwd().parent / "second/reference/genomes/hs38.fa.gz"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local experiment\n")
    with pytest.raises(ValueError, match="byte-identical"):
        shared_reference.link_existing_hs38(local)
    assert local.read_bytes() == b"local experiment\n"

    source = shared_reference.library / "genomes/hs38.fa.gz"
    shutil.copyfile(source, local)
    sidecar = local.with_suffix(".gz.fai")
    sidecar.write_text("local index")
    assert shared_reference.link_existing_hs38(local)
    assert local.is_symlink() and local.samefile(source)
    assert sidecar.read_text() == "local index"


def test_1637_migration_leaves_existing_hardlink_and_certification_intact(
    shared_reference: ReferenceStore,
) -> None:
    store = shared_reference
    source = store.library / "genomes/hs38.fa.gz"
    local = Path.cwd().parent / "second/reference/genomes/hs38.fa.gz"
    local.parent.mkdir(parents=True)
    local.hardlink_to(source)
    store.record_verified(
        check_reference_bytes(store.library, store.version, shared=True)
    )

    assert not store.link_existing_hs38(local)
    assert not local.is_symlink() and local.samefile(source)
    prepare_reference(download=False)
    validate_reference()


def test_1637_legacy_certification_validates_the_configured_genome_directory(
    shared_reference: ReferenceStore,
) -> None:
    legacy = Path.cwd() / "legacy"
    shutil.copytree(shared_reference.library, legacy)
    retained = Path.cwd() / "out/certify/inputs"
    retained.mkdir(parents=True)
    shutil.copy(shared_reference.version / constants.BOOTSTRAP_FILENAME, retained)
    settings["reference_library"] = str(legacy)
    validate_reference()

    settings["reference_genome_library"] = str(Path.cwd() / "other-genomes")
    with pytest.raises(ValueError, match="missing or corrupt.*other-genomes"):
        validate_reference()
