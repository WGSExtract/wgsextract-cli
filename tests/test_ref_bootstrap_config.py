import json
import runpy
import sys
import threading
from pathlib import Path

from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.core import config, constants
from wgsextract_cli.core.ref_library import processing as reference_processing


def _bootstrap(path: str, *, cancel_event=None) -> bool:
    """The archive's download is tested elsewhere; it leaves the library."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return True


def test_1213_cancelled_bootstrap_does_not_retry(tmp_path, monkeypatch):
    path = "bootstrap.tar.gz"
    started, release = threading.Event(), threading.Event()
    cancel = api.CancelToken()
    reflib = tmp_path / "library"
    result = []

    def download():
        result.append(
            api.ref.bootstrap(
                api.RefBootstrapParams(ref=reflib), cancel=cancel, check=False
            )
        )

    with serving(files={path: b"a" * (1 << 20)}) as server:
        monkeypatch.setattr(constants, "BOOTSTRAP_URL", server.url_for(path))
        server.body_pauses[path] = (1 << 18, started, release)
        worker = threading.Thread(target=download, daemon=True)
        worker.start()
        try:
            assert started.wait(5), "the bootstrap request never reached the server"
            cancel.cancel()
            worker.join(2)
            assert not worker.is_alive(), "cancelled bootstrap did not return promptly"
            assert result[0].exit_code == 130
            assert server.get_paths == [path]
            assert not (reflib / constants.BOOTSTRAP_FILENAME).exists()
        finally:
            release.set()
            worker.join(5)


def test_bootstrap_saves_reference_library_when_unconfigured(
    tmp_path, monkeypatch, fake_aadr_download
):
    config_path = tmp_path / "config.toml"
    reflib = tmp_path / "reference"

    monkeypatch.setattr(config, "get_config_path", lambda: config_path)
    config.settings.clear()
    monkeypatch.setattr(
        "wgsextract_cli.core.reference_processing.download_bootstrap", _bootstrap
    )

    api.ref.bootstrap(api.RefBootstrapParams(ref=reflib))

    config.reload_settings()
    assert config.settings["reference_library"] == str(reflib)


def test_bootstrap_keeps_existing_reference_library(
    tmp_path, monkeypatch, fake_aadr_download
):
    config_path = tmp_path / "config.toml"
    existing_reflib = tmp_path / "existing-reference"
    requested_reflib = tmp_path / "requested-reference"

    monkeypatch.setattr(config, "get_config_path", lambda: config_path)
    config.settings.clear()
    config.save_config({"reference_library": str(existing_reflib)})
    monkeypatch.setattr(
        "wgsextract_cli.core.reference_processing.download_bootstrap", _bootstrap
    )

    api.ref.bootstrap(api.RefBootstrapParams(ref=requested_reflib))

    config.reload_settings()
    assert config.settings["reference_library"] == str(existing_reflib)


def test_1087_gui_bootstrap_uses_library_not_last_selected_fasta(
    tmp_path, monkeypatch, fake_aadr_download
):
    workspace = tmp_path / "users"
    workspace.mkdir()
    library = tmp_path / "selected-library"
    fasta = tmp_path / "external-fasta" / "genome.fa"
    fasta.parent.mkdir()
    fasta.write_text(">chr1\nACGT\n")
    (workspace / "config.toml").write_text(
        f"reference_library = {json.dumps(str(library))}\n"
        f"reference_fasta = {json.dumps(str(fasta))}\n"
    )
    monkeypatch.setenv("WGSEXTRACT_GUI_USERS_WORKSPACE", str(workspace))
    monkeypatch.delenv("WGSEXTRACT_GUI_CONFIG_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["bootstrap_reference_library.py"])

    def download(folder, *, cancel_event=None):
        destination = Path(folder)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "bootstrap.txt").write_text("downloaded")
        return True

    monkeypatch.setattr(
        "wgsextract_cli.core.reference_processing.download_bootstrap", download
    )
    helper = (
        Path(__file__).resolve().parents[1]
        / "gui/examples/WGSExtract/scripts/bootstrap_reference_library.py"
    )
    runpy.run_path(str(helper), run_name="__main__")

    assert (library / "bootstrap.txt").read_text() == "downloaded"
    assert list(fasta.parent.iterdir()) == [fasta]


def test_download_bootstrap_normalizes_existing_assets_and_installs_ploidy(
    tmp_path, monkeypatch
):
    reflib = tmp_path / "reference"
    nested_ref = reflib / "reference" / "ref"
    nested_ref.mkdir(parents=True)
    (nested_ref / "All_SNPs_hg19_ref.tab.gz").write_text("snps\n", encoding="utf-8")
    (nested_ref / ".DS_Store").write_text("metadata\n", encoding="utf-8")
    (reflib / f".bootstrap-{constants.BOOTSTRAP_VERSION}").write_text(
        constants.BOOTSTRAP_SHA256 + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        reference_processing,
        "run_command",
        lambda *_args, **_kwargs: type("Result", (), {"stdout": "* * * M 2\n"})(),
    )

    assert reference_processing.download_bootstrap(str(reflib))
    assert (reflib / "ref" / "All_SNPs_hg19_ref.tab.gz").is_file()
    assert not (reflib / "ref" / ".DS_Store").exists()
    assert (reflib / "ploidy_hg19.txt").is_file()
    assert (reflib / "ploidy_hg38.txt").is_file()
