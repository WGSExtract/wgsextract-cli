"""scripts/build_reference_bootstrap.py drops YBrowse (#509) and adds CombinedKit v5 templates (#1107)."""

import hashlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_reference_bootstrap.py"
README = (
    "Liftover chains:\n-- hg19-chm13v2.chain\n\n"
    "Y Chromosome SNPs file (yBrowse.org):\n-- snps_hg38.vcf.gz (synthetic)\n\n"
    "Exome BED:\n-- exome.bed\n\n"
    "Microarray/:\n-- Microarray RAW file templates\n"
).replace("\n", "\r\n")
README_V020 = (
    "Liftover chains:\n-- hg19-chm13v2.chain\n\n"
    "Y Chromosome SNPs file (yBrowse.org):\n"
    "-- Not included since reference bootstrap v0.2.0: YBrowse data is\n"
    "   CC-BY-SA and is not redistributed with WGS Extract.\n\n"
    "Exome BED:\n-- exome.bed\n\n"
    "Microarray/:\n-- Microarray RAW file templates\n"
).replace("\n", "\r\n")
MEMBERS = {
    "reference/00README.txt": README.encode(),
    "reference/hg19-chm13v2.chain": b"chain",
    "reference/snps_hg38.vcf.gz": b"synthetic",
    "reference/snps_hg38.vcf.gz.tbi": b"synthetic",
    "reference/snps_grch37.vcf.gz.tbi.orig": b"synthetic",
    "reference/._hg19-chm13v2.chain": b"appledouble",
    "reference/.DS_Store": b"finder",
}


@pytest.mark.skipif(sys.platform == "win32", reason="hs38 CI runs on Ubuntu and macOS")
@pytest.mark.parametrize("corrupted", [None, "fasta", "indexes"])
def test_1509_hs38_install_verifies_both_release_assets(
    tmp_path: Path, corrupted: str | None
) -> None:
    fasta = tmp_path / "hs38.fa.gz"
    fasta.write_bytes(b"synthetic FASTA")
    fasta_sha = hashlib.sha256(fasta.read_bytes()).hexdigest()
    members = {
        "hs38.dict": b"dictionary",
        "hs38.fa.gz.fai": b"FASTA index",
        "hs38.fa.gz.gzi": b"BGZF index",
    }
    indexes = tmp_path / "indexes.tar.gz"
    indexes_sha = _source(indexes, members)
    if corrupted:
        (fasta if corrupted == "fasta" else indexes).write_bytes(b"corrupted")

    genomes = tmp_path / "reference" / "genomes"
    genomes.mkdir(parents=True)
    support_file = genomes / "hs38_nbin.csv"
    support_file.write_bytes(b"from bootstrap")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; install_reference "$@"',
            "bash",
            str(ROOT / "scripts" / "ci_hs38.sh"),
            str(tmp_path / "stage with spaces"),
            str(genomes),
            fasta.as_uri(),
            fasta_sha,
            indexes.as_uri(),
            indexes_sha,
        ],
        capture_output=True,
        text=True,
    )
    assert support_file.read_bytes() == b"from bootstrap"
    if corrupted:
        assert result.returncode != 0
        name = "hs38.fa.gz" if corrupted == "fasta" else "hs38-indexes.tar.gz"
        assert f"{name}: FAILED" in result.stdout
        assert sorted(genomes.iterdir()) == [support_file]
    else:
        assert result.returncode == 0, result.stderr
        assert (genomes / fasta.name).read_bytes() == fasta.read_bytes()
        for name, content in members.items():
            assert (genomes / name).read_bytes() == content


def _source(path: Path, members: dict[str, bytes] | None = None) -> str:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (members or MEMBERS).items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 1_600_000_000
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(
    source: Path,
    sha: str,
    output: Path,
    *,
    templates: Path | None = None,
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--source",
        str(source),
        "--source-sha256",
        sha,
        "--version",
        "9.9.9",
        "--output",
        str(output),
    ]
    if templates is None:
        cmd.append("--no-templates")
    else:
        cmd.extend(["--templates", str(templates)])
    return subprocess.run(cmd, capture_output=True, text=True)


def test_bootstrap_build_drops_ybrowse_files_and_is_reproducible(tmp_path):
    source = tmp_path / "old.tar.gz"
    sha = _source(source)
    assert _build(source, sha, tmp_path / "a").returncode == 0
    assert _build(source, sha, tmp_path / "b").returncode == 0
    name = "wgsextract-reference-bootstrap-9.9.9.tar.gz"
    built = (tmp_path / "a" / name).read_bytes()
    assert built == (tmp_path / "b" / name).read_bytes()
    digest = hashlib.sha256(built).hexdigest()
    assert (tmp_path / "a/SHA256SUMS").read_text() == f"{digest}  {name}\n"

    with tarfile.open(tmp_path / "a" / name) as tar:
        assert tar.getnames() == [
            "reference/00README.txt",
            "reference/hg19-chm13v2.chain",
        ]
        readme = tar.extractfile("reference/00README.txt").read().decode()  # type: ignore[union-attr]
        chain = tar.extractfile("reference/hg19-chm13v2.chain").read()  # type: ignore[union-attr]
    assert chain == b"chain"
    assert "snps_hg38.vcf.gz (synthetic)" not in readme
    assert "wgsextract ref ybrowse --install" in readme
    assert "Not included since reference bootstrap v9.9.9" in readme
    assert readme.startswith("Liftover chains:\r\n") and "\r\nExome BED:\r\n" in readme
    assert "\n" not in readme.replace("\r\n", "")


def test_bootstrap_build_adds_combinedkit_v5_templates_reproducibly(tmp_path):
    source = tmp_path / "v020.tar.gz"
    sha = _source(
        source,
        {
            "reference/00README.txt": README_V020.encode(),
            "reference/microarray/raw_file_templates/body/23andMe_V3.txt": b"v3-body\n",
            "reference/microarray/raw_file_templates/head/23andMe_V3.txt": b"v3-head\n",
        },
    )
    templates = tmp_path / "templates"
    (templates / "body").mkdir(parents=True)
    (templates / "head").mkdir(parents=True)
    (templates / "body" / "MyHeritage_V3.csv.delta.gz").write_bytes(b"mh-v3-delta")
    (templates / "head" / "MyHeritage_V3.csv").write_bytes(b"mh-v3-head\n")
    (templates / "corrections.tsv").write_bytes(b"# corrections\n")
    (templates / "README.md").write_bytes(b"# templates\n")
    (templates / ".DS_Store").write_bytes(b"ignored")

    assert _build(source, sha, tmp_path / "a", templates=templates).returncode == 0
    assert _build(source, sha, tmp_path / "b", templates=templates).returncode == 0
    name = "wgsextract-reference-bootstrap-9.9.9.tar.gz"
    built = (tmp_path / "a" / name).read_bytes()
    assert built == (tmp_path / "b" / name).read_bytes()

    with tarfile.open(tmp_path / "a" / name) as tar:
        assert tar.getnames() == [
            "reference/00README.txt",
            "reference/microarray/raw_file_templates/README.md",
            "reference/microarray/raw_file_templates/body/23andMe_V3.txt",
            "reference/microarray/raw_file_templates/body/MyHeritage_V3.csv.delta.gz",
            "reference/microarray/raw_file_templates/corrections.tsv",
            "reference/microarray/raw_file_templates/head/23andMe_V3.txt",
            "reference/microarray/raw_file_templates/head/MyHeritage_V3.csv",
        ]
        # Existing CombinedKit v3 template stays byte-identical.
        assert (
            tar.extractfile(
                "reference/microarray/raw_file_templates/body/23andMe_V3.txt"
            ).read()  # type: ignore[union-attr]
            == b"v3-body\n"
        )
        assert (
            tar.extractfile(
                "reference/microarray/raw_file_templates/body/MyHeritage_V3.csv.delta.gz"
            ).read()  # type: ignore[union-attr]
            == b"mh-v3-delta"
        )
        readme = tar.extractfile("reference/00README.txt").read().decode()  # type: ignore[union-attr]
    assert "Not included since reference bootstrap v0.2.0" in readme
    assert "Since reference bootstrap v9.9.9" in readme
    assert "CombinedKit v5" in readme
    assert "\n" not in readme.replace("\r\n", "")


def test_bootstrap_build_refuses_template_collision(tmp_path):
    source = tmp_path / "v020.tar.gz"
    sha = _source(
        source,
        {
            "reference/00README.txt": README_V020.encode(),
            "reference/microarray/raw_file_templates/head/23andMe_V3.txt": b"existing\n",
        },
    )
    templates = tmp_path / "templates"
    (templates / "head").mkdir(parents=True)
    (templates / "head" / "23andMe_V3.txt").write_bytes(b"duplicate\n")
    result = _build(source, sha, tmp_path / "out", templates=templates)
    assert result.returncode != 0
    assert "collides with existing member" in result.stderr


def test_bootstrap_build_refuses_an_unpinned_source(tmp_path):
    source = tmp_path / "old.tar.gz"
    _source(source)
    result = _build(source, "0" * 64, tmp_path / "out")
    assert result.returncode != 0
    assert "expected " + "0" * 64 in result.stderr
    assert not (tmp_path / "out").exists()


def test_a_failed_bootstrap_build_keeps_the_previous_archive(tmp_path):
    source = tmp_path / "old.tar.gz"
    sha = _source(source)
    out = tmp_path / "out"
    assert _build(source, sha, out).returncode == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with tarfile.open(source, "w:gz") as tar:  # README without a yBrowse section
        info = tarfile.TarInfo("reference/00README.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"x\n"))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    result = _build(source, sha, out)
    assert result.returncode != 0 and "no yBrowse section" in result.stderr
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
