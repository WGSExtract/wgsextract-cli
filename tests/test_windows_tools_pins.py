"""Keep GKL pins in GATK, including a GKL-only tool release."""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("complete", [True, False])
def test_gkl_release_pin_is_scoped_to_gatk(tmp_path, monkeypatch, complete):
    script = Path(__file__).parents[1] / "packaging/windows/pin.py"
    spec = importlib.util.spec_from_file_location("windows_pin", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = tmp_path / "pixi.toml"
    manifest.write_text(
        "[target.win-64.dependencies]\n# windows-tools-begin\n"
        'bwa = {url = "old", sha256 = "old"}\n# windows-tools-end\n'
        "[feature.gatk.target.win-64.dependencies]\n"
        "# gatk-windows-tools-begin\n# gatk-windows-tools-end\n"
        "[feature.pharmcat.target.win-64.dependencies]\n"
        "# pharmcat-windows-tools-begin\n# pharmcat-windows-tools-end\n"
    )
    assets = [
        {
            "name": "gkl-0.8.11-wgse_0.conda",
            "browser_download_url": "https://example.invalid/gkl",
            "digest": "sha256:" + "1" * 64,
        }
    ]
    if complete:
        assets.append(
            {
                "name": "bwa-0.7.19-wgse_0.conda",
                "browser_download_url": "https://example.invalid/bwa",
                "digest": "sha256:" + "2" * 64,
            }
        )
    monkeypatch.setattr(module, "MANIFEST", manifest)
    monkeypatch.setattr(module, "release_assets", lambda _: assets)
    monkeypatch.setattr(module.sys, "argv", ["pin.py", "windows-tools-test"])
    module.main()
    default, rest = manifest.read_text().split(
        "[feature.gatk.target.win-64.dependencies]"
    )
    gatk, pharmcat = rest.split("[feature.pharmcat.target.win-64.dependencies]")
    assert "gkl =" not in default
    assert "gkl =" in gatk and "bwa =" not in gatk
    assert ('url = "old"' in default) == (not complete)
    assert "gkl =" not in pharmcat
    assert ("bwa =" in default) and "bwa =" not in pharmcat


def test_pharmcat_windows_pins_follow_bcftools_and_htslib(tmp_path, monkeypatch):
    script = Path(__file__).parents[1] / "packaging/windows/pin.py"
    spec = importlib.util.spec_from_file_location("windows_pin", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = tmp_path / "pixi.toml"
    manifest.write_text(
        "# windows-tools-begin\n# windows-tools-end\n"
        "# gatk-windows-tools-begin\n# gatk-windows-tools-end\n"
        "[feature.pharmcat.target.win-64.dependencies]\n"
        'bcftools = {url = "old", sha256 = "old"}\n'
        "# pharmcat-windows-tools-begin\n# pharmcat-windows-tools-end\n"
    )
    assets = [
        {
            "name": f"{name}-1.0-wgse_0.conda",
            "browser_download_url": f"https://example.invalid/{name}",
            "digest": "sha256:" + "3" * 64,
        }
        for name in ("bcftools", "htslib", "samtools")
    ]
    monkeypatch.setattr(module, "MANIFEST", manifest)
    monkeypatch.setattr(module, "release_assets", lambda _: assets)
    monkeypatch.setattr(module.sys, "argv", ["pin.py", "windows-tools-test"])
    module.main()
    pharmcat = manifest.read_text().split("[feature.pharmcat.target.win-64")[1]
    assert "example.invalid/bcftools" in pharmcat
    assert "example.invalid/htslib" in pharmcat
    assert "samtools" not in pharmcat
