"""The Library page's Install buttons turn into Delete once an item is
installed: each installer has a delete, and `ref status` reports the item
gone afterwards."""

import json
from pathlib import Path

import pytest

from tests.htslib_helpers import run_wgsextract
from wgsextract_cli import api
from wgsextract_cli.commands.ref.status import ref_status_values
from wgsextract_cli.core.utils import UsageError

AADR = Path("aadr", "v66.p1_2M.aadr.patch.PUB.snp")


def _values(reflib: Path) -> dict[str, str]:
    status = api.ref.status(api.RefStatusParams(ref=reflib)).details
    return ref_status_values(status)


def _touch(*paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("command", "files", "value"),
    [
        (
            "spliceai",
            ["ref/spliceai_hg38.vcf.gz", "ref/spliceai_hg38.vcf.gz.tbi"],
            "library.spliceaiInstalled",
        ),
        (
            "alphamissense",
            ["ref/alphamissense_hg19.tsv.gz", "ref/alphamissense_hg19.tsv.gz.tbi"],
            "library.alphamissenseInstalled",
        ),
        (
            "pharmgkb",
            ["ref/pharmgkb_hg38.vcf.gz", "ref/pharmgkb_hg38.vcf.gz.csi"],
            "library.pharmgkbInstalled",
        ),
    ],
)
def test_annotation_resource_delete_removes_files_and_indexes(
    tmp_path, command, files, value
):
    reflib = tmp_path / "reference"
    _touch(*(reflib / f for f in files), reflib / "ref" / "clinvar_hg38.vcf.gz")
    assert _values(reflib)[value] == "true"

    run_wgsextract("ref", command, "--delete", "--ref", str(reflib))

    assert not any((reflib / f).exists() for f in files)
    assert (reflib / "ref" / "clinvar_hg38.vcf.gz").is_file()  # not theirs
    assert _values(reflib)[value] == "false"


def test_aadr_delete_and_bootstrap_reinstalls_it(
    tmp_path, monkeypatch, fake_aadr_download
):
    reflib = tmp_path / "reference"

    def bootstrap(path: str) -> bool:  # the archive's download is tested elsewhere
        _touch(Path(path, ".bootstrap-0.2.0"))
        return True

    monkeypatch.setattr(
        "wgsextract_cli.core.reference_processing.download_bootstrap", bootstrap
    )
    api.ref.bootstrap(api.RefBootstrapParams(ref=reflib))
    assert (reflib / AADR).read_bytes() == fake_aadr_download
    # GRCh38 exports place the panel with the forward chain.
    assert (reflib / "hg19ToHg38.over.chain.gz").is_file()
    assert _values(reflib)["library.aadrInstalled"] == "true"
    assert _values(reflib)["library.isBootstrapped"] == "true"

    run_wgsextract("ref", "aadr", "--delete", "--ref", str(reflib))
    assert not (reflib / "aadr").exists()
    values = _values(reflib)
    assert values["library.aadrInstalled"] == "false"
    # A library without the panel isn't fully bootstrapped: offer it again.
    assert values["library.isBootstrapped"] == "false"

    api.ref.bootstrap(api.RefBootstrapParams(ref=reflib))
    assert (reflib / AADR).is_file()


@pytest.mark.parametrize("command", ["aadr", "ybrowse"])
@pytest.mark.parametrize("flags", [[], ["--install", "--delete"]])
def test_explicit_downloads_need_install_or_delete(tmp_path, command, flags):
    with pytest.raises(UsageError, match="--install or --delete"):
        run_wgsextract("ref", command, *flags, "--ref", str(tmp_path))


def test_ybrowse_delete_removes_every_snapshot(tmp_path):
    reflib = tmp_path / "reference"
    root = reflib / "ybrowse" / "hg38"
    version = "a" * 64
    _touch(root / version / "manifest.json", root / ("b" * 64) / "manifest.json")
    (root / "current.json").write_text(json.dumps({"version": version}))
    assert _values(reflib)["library.ybrowseInstalled"] == "true"

    run_wgsextract("ref", "ybrowse", "--delete", "--ref", str(reflib))

    assert not (reflib / "ybrowse").exists()
    assert _values(reflib)["library.ybrowseInstalled"] == "false"


def test_microarray_targets_delete_removes_them_and_their_leftovers(tmp_path):
    reflib = tmp_path / "reference"
    target = reflib / "microarray" / "All_SNPs_hg38_ref.tab.gz"
    legacy = reflib / "ref" / "All_SNPs_hg19_ref.tab.gz"
    kept = reflib / "microarray" / "raw_file_templates" / "head" / "23andMe_V5.csv"
    _touch(
        target,
        Path(f"{target}.tbi"),
        Path(f"{target}.lock.guard"),
        reflib / "microarray" / "All_SNPs_hg38_ref.tab.manifest.json",
        legacy,
        kept,
    )
    assert _values(reflib)["library.annotationVcfInstalled"] == "true"

    run_wgsextract("ref", "microarray-targets", "--delete", "--ref", str(reflib))

    assert sorted(p.name for p in (reflib / "microarray").iterdir()) == [
        "raw_file_templates"
    ]
    assert not legacy.exists() and kept.is_file()
    assert _values(reflib)["library.annotationVcfInstalled"] == "false"


def test_vep_delete_removes_one_release_only(tmp_path):
    reflib = tmp_path / "reference"
    species = reflib / "vep" / "homo_sapiens"
    _touch(
        species / "116_GRCh38" / "info.txt",
        species / "115_GRCh37" / "info.txt",
        reflib / "vep" / "homo_sapiens_vep_116_GRCh38.tar.gz.partial",
    )
    params = api.VepDeleteParams(ref=reflib, engine="ensembl")

    api.vep.delete(params)

    assert sorted(p.name for p in species.iterdir()) == ["115_GRCh37"]
    assert not (reflib / "vep" / "homo_sapiens_vep_116_GRCh38.tar.gz.partial").exists()
    assert _values(reflib)["library.vepCacheInstalled"] == "true"  # 115 is left

    api.vep.delete(
        api.VepDeleteParams(
            ref=reflib, vep_version="115", assembly="GRCh37", engine="ensembl"
        )
    )
    assert not species.exists()
    assert (reflib / "vep").is_dir()  # the cache folder itself stays
    assert _values(reflib)["library.vepCacheInstalled"] == "false"


def test_deleting_what_is_not_installed_succeeds(tmp_path):
    reflib = tmp_path / "reference"
    reflib.mkdir()
    for command in ("spliceai", "pharmgkb", "microarray-targets"):
        run_wgsextract("ref", command, "--delete", "--ref", str(reflib))
    run_wgsextract("ref", "aadr", "--delete", "--ref", str(reflib))
    assert api.vep.delete(api.VepDeleteParams(ref=reflib)).status == "ok"
    assert sorted(reflib.iterdir()) == []
