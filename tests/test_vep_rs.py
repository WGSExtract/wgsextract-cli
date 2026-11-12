"""`vep run|download|verify --engine vep-rs` with the real vep-rs and
vep-cache-builder on a one-gene genome (tests/fixtures/vep_rs), skipped when
they aren't installed (`pixi install -e vep-rs`)."""

import argparse
import gzip
import shutil
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, needs_tools, run_wgsextract
from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.api import VepRunParams
from wgsextract_cli.api.vep import VepDownloadParams, VepVerifyParams
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import WGSExtractError

FIXTURES = Path(__file__).parent / "fixtures" / "vep_rs"
needs_vep_rs = needs_tools("vep-rs", "vep-cache-builder")
pytestmark = needs_htslib

# TINY1 (ENST00000000001) is a two-exon gene on contig 1 at 1001-1300; codon 2
# (ACC, Thr) starts at 1014.
_VCF = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=1,length=3000>\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    "1\t1014\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    "1\t1150\t.\tT\tA\t50\tPASS\t.\tGT\t1/1\n"
)


def _build_cache(cache_root: Path) -> Path:
    """A vep-rs cache of the fixture gene, as vep-cache-builder makes it."""
    from wgsextract_cli.core.utils import run_command

    version_dir = cache_root / "vep-rs" / "homo_sapiens" / "116_GRCh38"
    cache_root.mkdir(parents=True)
    genome = cache_root.parent / "genome.fa"
    shutil.copy(FIXTURES / "genome.fa", genome)
    run_command(["samtools", "faidx", str(genome)])
    run_command(
        [
            get_tool_path("vep-cache-builder") or "vep-cache-builder",
            "--release", "116", "--assembly", "GRCh38",
            "--gff3", str(FIXTURES / "genes.gff3"),
            "--fasta", str(FIXTURES / "protein.fa"),
            "--genome-fasta", str(genome),
            "--output-dir", str(version_dir),
        ],
        capture_output=True,
    )  # fmt: skip
    return version_dir


@pytest.fixture
def vep_rs_cache(tmp_path) -> Path:
    return _build_cache(tmp_path / "cache")


def _input(tmp_path: Path) -> str:
    plain = tmp_path / "in.vcf"
    plain.write_text(_VCF)
    return bgzip_index(plain)


def _csq(vcf_gz: str) -> dict[int, dict[str, str]]:
    """Each record's CSQ for TINY1's transcript, by position, read by field
    name from the header as the variant table reads it."""
    fields: list[str] = []
    out = {}
    with gzip.open(vcf_gz, "rt") as f:
        for line in f:
            if line.startswith("##INFO=<ID=CSQ"):
                fields = line.split("Format: ")[1].split('"')[0].split("|")
            elif not line.startswith("#"):
                cols = line.split("\t")
                csq = next(kv[4:] for kv in cols[7].split(";") if kv.startswith("CSQ="))
                out[int(cols[1])] = dict(
                    zip(fields, csq.split(",")[0].split("|"), strict=True)
                )
    return out


@needs_vep_rs
def test_vep_rs_annotates_and_feeds_the_variant_table(tmp_path, vep_rs_cache):
    vcf = _input(tmp_path)
    out = tmp_path / "out"

    result = api.vep.run(
        VepRunParams(
            input=Path(vcf),
            engine="vep-rs",
            vep_cache=vep_rs_cache.parents[2],
            ref=tmp_path / "genome.fa",
            outdir=out,
        )
    )

    annotated = str(out / "in_vep.vcf.gz")
    assert result.metrics["engine"] == "vep-rs"
    assert Path(annotated + ".tbi").is_file()
    csq = _csq(annotated)
    assert csq[1014]["Consequence"] == "missense_variant"
    assert csq[1014]["SYMBOL"] == "TINY1"
    assert csq[1014]["HGVSp"] == "ENSP00000000001:p.Thr2Ala"
    assert csq[1150]["Consequence"] == "intron_variant"
    # A builder cache without variations or prediction matrices says so.
    assert "vep_cache_gaps" in {w.code for w in result.warnings}

    table = api.vcf.table(api.VcfTableParams(input=Path(annotated), outdir=out))
    rows = next(o.path for o in table.outputs if o.path.endswith(".tsv"))
    text = Path(rows).read_text()
    assert "TINY1" in text and "missense_variant" in text


@needs_vep_rs
def test_vep_rs_is_the_default_engine(tmp_path, vep_rs_cache, monkeypatch):
    """#602: with no --engine or vep_engine, vep run annotates with vep-rs."""
    from wgsextract_cli.core.config import settings

    monkeypatch.delitem(settings, "vep_engine", raising=False)
    result = api.vep.run(
        VepRunParams(
            input=Path(_input(tmp_path)),
            vep_cache=vep_rs_cache.parents[2],
            ref=tmp_path / "genome.fa",
            outdir=tmp_path / "o",
        )
    )

    assert result.metrics["engine"] == "vep-rs"
    assert _csq(str(tmp_path / "o" / "in_vep.vcf.gz"))[1014]["SYMBOL"] == "TINY1"


@needs_vep_rs
def test_vep_rs_engine_comes_from_config(tmp_path, vep_rs_cache, monkeypatch):
    from wgsextract_cli.core.config import settings

    monkeypatch.setitem(settings, "vep_engine", "vep-rs")
    vcf = _input(tmp_path)

    run_wgsextract(
        "vep", "run", "-i", vcf, "--vep-cache", str(vep_rs_cache.parents[2]),
        "--ref", str(tmp_path / "genome.fa"), "-o", str(tmp_path / "o"),
    )  # fmt: skip

    assert _csq(str(tmp_path / "o" / "in_vep.vcf.gz"))[1014]["SYMBOL"] == "TINY1"


@needs_vep_rs
def test_vep_rs_without_its_cache_names_the_download(tmp_path):
    vcf = _input(tmp_path)
    (tmp_path / "empty").mkdir()

    result = api.vep.run(
        VepRunParams(
            input=Path(vcf),
            engine="vep-rs",
            vep_cache=tmp_path / "empty",
            vep_assembly="GRCh38",
        ),
        check=False,
    )

    assert result.status != "ok"
    assert any("vep download --engine vep-rs" in w.message for w in result.warnings)
    assert not (tmp_path / "in_vep.vcf.gz").exists()


@pytest.mark.parametrize(
    "arg",
    ["--custom file.vcf.gz,X,vcf", "--database", "-CUST file.vcf.gz,X,vcf"],
)
def test_vep_rs_refuses_options_it_would_ignore(tmp_path, arg):
    vcf = _input(tmp_path)

    with pytest.raises(api.CommandFailed, match="--engine ensembl"):
        api.vep.run(VepRunParams(input=Path(vcf), engine="vep-rs", vep_args=arg))
    assert not (tmp_path / "in_vep.vcf.gz").exists()


def test_ensembl_vep_is_refused_on_windows_with_the_alternative(tmp_path, monkeypatch):
    from wgsextract_cli.cli import platform_support

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform_support, "assert_native_windows_python", lambda: None)

    def args(engine: str) -> argparse.Namespace:
        return argparse.Namespace(command="vep", vep_cmd="run", engine=engine)

    with pytest.raises(WGSExtractError, match="--engine vep-rs"):
        platform_support.verify_platform_support(args("ensembl"))
    platform_support.verify_platform_support(args("vep-rs"))


@needs_vep_rs
def test_verify_accepts_a_built_cache_and_catches_a_changed_file(
    tmp_path, vep_rs_cache
):
    params = VepVerifyParams(engine="vep-rs", vep_cache=vep_rs_cache.parents[2])

    result = api.vep.verify(params, check=False)
    assert result.status == "ok"
    assert result.metrics["damaged_files"] == 0
    # Only contig 1 is in the fixture: the others are reported missing.
    assert result.metrics["missing_chromosomes"] == 24

    shard = next((vep_rs_cache / "transcripts" / "1").iterdir())
    shard.write_text(shard.read_text().replace("TINY1", "TINY2"))
    result = api.vep.verify(params, check=False)
    assert result.status != "ok"
    assert result.metrics["damaged_files"] == 1


@needs_vep_rs
@pytest.mark.parametrize("manifest", ["", "0" * 64 + "  ../../outside.txt\n"])
def test_verify_fails_on_an_empty_or_escaping_manifest(
    tmp_path, vep_rs_cache, manifest
):
    """Copilot review on #626: a truncated or hostile CHECKSUMS.sha256 must not
    pass as a verified cache."""
    (vep_rs_cache / "CHECKSUMS.sha256").write_text(manifest)

    result = api.vep.verify(
        VepVerifyParams(engine="vep-rs", vep_cache=vep_rs_cache.parents[2]),
        check=False,
    )

    assert result.status != "ok"


# --- vep download --engine vep-rs ----------------------------------------------
# The Ensembl files download for real (download_file) from a local HTTP
# server; only the mirror table is pointed at it.


def _gz(data: bytes) -> bytes:
    return gzip.compress(data)


def _ensembl_files() -> dict[str, bytes]:
    """The files `vep download --engine vep-rs --no-variations` fetches, as
    Ensembl lays them out: the fixture gene and genome, and empty tables."""
    from wgsextract_cli.commands import _vep_rs_cache as rc

    files = {
        "release-116/gff3/homo_sapiens/Homo_sapiens.GRCh38.116.gff3.gz": _gz(
            (FIXTURES / "genes.gff3").read_bytes()
        ),
        "release-116/fasta/homo_sapiens/pep/Homo_sapiens.GRCh38.pep.all.fa.gz": _gz(
            (FIXTURES / "protein.fa").read_bytes()
        ),
        "release-116/fasta/homo_sapiens/dna/"
        "Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz": _gz(
            (FIXTURES / "genome.fa").read_bytes()
        ),
    }
    for table in rc._CORE_TABLES:
        files[f"release-116/mysql/homo_sapiens_core_116_38/{table}.txt.gz"] = _gz(b"")
    for table in ("phenotype", "phenotype_feature"):
        files[f"release-116/mysql/homo_sapiens_variation_116_38/{table}.txt.gz"] = _gz(
            b""
        )
    return _with_checksums(files)


def _with_checksums(files: dict[str, bytes]) -> dict[str, bytes]:
    """Add each folder's CHECKSUMS, with the BSD sums Ensembl lists."""
    import collections
    import tempfile

    from wgsextract_cli.commands import _vep_resources as vr

    listings: dict[str, list[str]] = collections.defaultdict(list)
    with tempfile.TemporaryDirectory() as tmp:
        for path, data in files.items():
            folder, name = path.rsplit("/", 1)
            Path(tmp, "f").write_bytes(data)
            total, blocks = vr.calculate_bsd_sum(str(Path(tmp, "f")))
            listings[folder].append(f"{total:05d} {blocks} {name}")
    for folder, lines in listings.items():
        files[f"{folder}/CHECKSUMS"] = ("\n".join(lines) + "\n").encode()
    return files


@needs_vep_rs
def test_download_builds_the_vep_rs_cache_from_ensembl_files(tmp_path, monkeypatch):
    from tests.disk_volumes import Volumes
    from wgsextract_cli.commands import _vep_resources as vr

    # Room for a real build, whatever the test machine has.
    Volumes(monkeypatch).free[tmp_path] = 100
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    with serving(None, _ensembl_files()) as srv:
        for mirror in vr.VEP_MIRRORS:
            monkeypatch.setitem(vr.VEP_MIRRORS, mirror, srv.base)
        result = api.vep.download(
            VepDownloadParams(
                engine="vep-rs", vep_cache=tmp_path / "c", variations=False
            )
        )

    version_dir = tmp_path / "c" / "vep-rs" / "homo_sapiens" / "116_GRCh38"
    assert [o.path for o in result.outputs] == [str(version_dir)]
    assert (version_dir / "transcripts" / "1").is_dir()
    # The downloads are gone once the cache is built.
    assert sorted(p.name for p in (tmp_path / "c" / "vep-rs").iterdir()) == [
        "homo_sapiens"
    ]
    verified = api.vep.verify(
        VepVerifyParams(engine="vep-rs", vep_cache=tmp_path / "c"), check=False
    )
    assert verified.metrics["damaged_files"] == 0


@needs_vep_rs
def test_download_fails_when_ensembl_checksums_are_unreadable(tmp_path, monkeypatch):
    """Copilot review on #626: without the listing a missing file can't be
    told from a lost one, so nothing is built."""
    from wgsextract_cli.commands import _vep_resources as vr

    files = {k: v for k, v in _ensembl_files().items() if not k.endswith("CHECKSUMS")}
    with serving(None, files) as srv:
        for mirror in vr.VEP_MIRRORS:
            monkeypatch.setitem(vr.VEP_MIRRORS, mirror, srv.base)
        result = api.vep.download(
            VepDownloadParams(
                engine="vep-rs", vep_cache=tmp_path / "c", variations=False
            ),
            check=False,
        )

    assert result.status != "ok"
    assert not (tmp_path / "c" / "vep-rs" / "homo_sapiens").exists()


def test_delete_removes_an_unfinished_vep_rs_download(tmp_path):
    work = tmp_path / "vep-rs" / ".download-homo_sapiens-116_GRCh38"
    (work / "core_dumps").mkdir(parents=True)
    (work / "core_dumps" / "gene.txt.gz.partial").write_bytes(b"x")

    api.vep.delete(api.VepDeleteParams(engine="vep-rs", vep_cache=tmp_path))

    assert not work.exists()


@needs_vep_rs
def test_chain_annotate_runs_vep_with_the_configured_vep_rs(
    tmp_path, vep_rs_cache, monkeypatch
):
    """Copilot review on #626: with vep_engine = "vep-rs" and only vep-rs's
    cache, chain-annotate's VEP step isn't skipped as missing Ensembl VEP."""
    from types import SimpleNamespace

    from wgsextract_cli.commands.vcf import chain
    from wgsextract_cli.core.config import settings

    monkeypatch.setitem(settings, "vep_engine", "vep-rs")
    lib = SimpleNamespace(vep_cache=str(vep_rs_cache.parents[2]))

    assert chain.skip_reason("vep", lib) is None  # type: ignore[arg-type]
    monkeypatch.setitem(settings, "vep_engine", "ensembl")
    if get_tool_path("vep") is None:
        assert "vep is not installed" in chain.skip_reason("vep", lib)  # type: ignore[arg-type]


@needs_vep_rs
def test_download_refuses_a_cache_folder_without_room_for_the_build(
    tmp_path, monkeypatch
):
    """#626 review: the vep-rs build downloads ~17 GB and keeps it until the
    cache is complete, so a folder without room is refused before any
    download."""
    from tests.disk_volumes import Volumes
    from wgsextract_cli.commands import _vep_resources as vr

    volumes = Volumes(monkeypatch)
    volumes.free[tmp_path] = 5
    with serving(None, _ensembl_files()) as srv:
        for mirror in vr.VEP_MIRRORS:
            monkeypatch.setitem(vr.VEP_MIRRORS, mirror, srv.base)
        result = api.vep.download(
            VepDownloadParams(engine="vep-rs", vep_cache=tmp_path / "c"),
            check=False,
        )

    assert result.status != "ok"
    assert "GiB free" in (str(result.error) or "")
    assert srv.paths == []


@needs_vep_rs
def test_a_failed_download_is_reported_not_the_ones_it_stopped(tmp_path, monkeypatch):
    """#626 review: when a later file fails its checksum, the downloads it
    stops must not be reported in its place."""
    from tests.disk_volumes import Volumes
    from wgsextract_cli.commands import _vep_resources as vr

    Volumes(monkeypatch).free[tmp_path] = 100
    files = _ensembl_files()
    bad = "release-116/mysql/homo_sapiens_variation_116_38/phenotype_feature.txt.gz"
    files[bad] = _gz(b"not what CHECKSUMS lists")
    with serving(None, files) as srv:
        # Still downloading the gene set when the bad file is checked.
        srv.delays["release-116/gff3/homo_sapiens/Homo_sapiens.GRCh38.116.gff3.gz"] = 2
        for mirror in vr.VEP_MIRRORS:
            monkeypatch.setitem(vr.VEP_MIRRORS, mirror, srv.base)
        result = api.vep.download(
            VepDownloadParams(
                engine="vep-rs", vep_cache=tmp_path / "c", variations=False
            ),
            check=False,
        )

    assert result.status != "ok"
    assert "phenotype_feature.txt.gz failed its checksum" in str(result.error)
    assert "gff3" not in str(result.error)
