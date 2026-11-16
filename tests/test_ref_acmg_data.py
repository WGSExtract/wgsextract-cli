"""`ref acmg-data` (issue 629): the eight sources of `vcf acmg` and the
Ensembl GFF3, fetched from a local server standing in for their publishers
(the network is the boundary), checked, and built into fastVEP databases by
the real fastVEP and bcftools. The sources are the one-gene fixtures of
tests/test_vcf_acmg.py on chromosome 1; gnomAD's is in the joint release's
shape. Skipped without fastVEP (`pixi install -e fastvep`)."""

import base64
import gzip
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.htslib_helpers import needs_htslib, needs_tools
from tests.http_server import RangeServer, serving
from wgsextract_cli import api
from wgsextract_cli.api.ref import RefAcmgDataParams, RefStatusParams
from wgsextract_cli.api.vcf import VcfAcmgParams
from wgsextract_cli.core import acmg_data
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import UsageError, run_command

FIXTURES = Path(__file__).parent / "fixtures" / "acmg"
GENE = Path(__file__).parent / "fixtures" / "vep_rs"
needs_fastvep = needs_tools("fastvep")
pytestmark = needs_htslib

GNOMAD = """##fileformat=VCFv4.2
##contig=<ID=chr1,length=248956422>
##INFO=<ID=AC_joint,Number=A,Type=Integer,Description="ac">
##INFO=<ID=AN_joint,Number=1,Type=Integer,Description="an">
##INFO=<ID=AF_joint,Number=A,Type=Float,Description="af">
##INFO=<ID=nhomalt_joint,Number=A,Type=Integer,Description="hom">
##INFO=<ID=AC_genomes,Number=A,Type=Integer,Description="not kept">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr1\t1150\t.\tT\tA\t.\tPASS\tAF_joint=0.3;AC_joint=300000;AN_joint=1000000;nhomalt_joint=50000;AC_genomes=1
"""
CLINGEN = (
    '"CLINGEN GENE DISEASE VALIDITY CURATIONS","","","","","",""\n'
    '"FILE CREATED: 2026-11-16","","","","","",""\n'
    '"GENE SYMBOL","GENE ID (HGNC)","DISEASE LABEL","DISEASE ID (MONDO)","MOI",'
    '"SOP","CLASSIFICATION"\n'
    '"+++","+++","+++","+++","+++","+++","+++"\n'
    '"TINY1","HGNC:1","Tiny syndrome","MONDO:0000001","AD","SOP10","Definitive"\n'
)
GFF3 = "Homo_sapiens.GRCh38.116.gff3.gz"


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def _bgzf(plain: Path) -> Path:
    out = plain.with_name(plain.name + ".gz")
    with out.open("wb") as f:
        run_command([get_tool_path("bgzip"), "-c", str(plain)], stdout=f)
    return out


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _bsd_sum(data: bytes) -> str:
    """What Ensembl's CHECKSUMS lists: `sum`'s checksum and 1 KB blocks."""
    checksum = 0
    for byte in data:
        checksum = (((checksum >> 1) | ((checksum & 1) << 15)) + byte) & 0xFFFF
    return f"{checksum:05d} {-(-len(data) // 1024)}"


def _goog_hash(data: bytes) -> dict[str, str]:
    md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
    return {"x-goog-hash": f"crc32c=AAAAAA==, md5={md5}"}


def _revel_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "revel_with_transcript_ids", (FIXTURES / "revel.csv").read_text()
        )
    return out.getvalue()


@pytest.fixture
def publishers(tmp_path, monkeypatch):
    """Every source as its publisher serves it, with the checksum each
    publishes; the module's URLs point there."""
    gff3 = gzip.compress((GENE / "genes.gff3").read_bytes(), mtime=0)
    clinvar = gzip.compress((FIXTURES / "clinvar.vcf").read_bytes(), mtime=0)
    summary = gzip.compress((FIXTURES / "variant_summary.txt").read_bytes(), mtime=0)
    constraint = (FIXTURES / "constraint.tsv").read_bytes()
    phylop = gzip.compress((FIXTURES / "phylop.wigFix").read_bytes(), mtime=0)
    gnomad = _bgzf(_write(tmp_path / "gnomad.vcf", GNOMAD)).read_bytes()
    files = {
        f"ensembl/{GFF3}": gff3,
        "ensembl/CHECKSUMS": f"{_bsd_sum(gff3)} {GFF3}\n".encode(),
        "clinvar.vcf.gz": clinvar,
        "clinvar.vcf.gz.md5": f"{_md5(clinvar)}  /pub/clinvar_20261108.vcf.gz\n".encode(),
        "variant_summary.txt.gz": summary,
        "variant_summary.txt.gz.md5": f"{_md5(summary)}  variant_summary.txt.gz\n".encode(),
        "constraint.tsv": constraint,
        "clingen.csv": CLINGEN.encode(),
        "revel.zip": _revel_zip(),
        "phylop/chr1.phyloP100way.wigFix.gz": phylop,
        "phylop/md5sum.txt": f"{_md5(phylop)}  chr1.phyloP100way.wigFix.gz\n".encode(),
        "gnomad/chr1.vcf.bgz": gnomad,
    }
    with serving(None, files) as server:
        server.headers = {
            "constraint.tsv": _goog_hash(constraint),
            "gnomad/chr1.vcf.bgz": _goog_hash(gnomad),
        }
        for name, path in {
            "ENSEMBL": "ensembl/",
            "CLINVAR_URL": "clinvar.vcf.gz",
            "VARIANT_SUMMARY_URL": "variant_summary.txt.gz",
            "CONSTRAINT_URL": "constraint.tsv",
            "CLINGEN_URL": "clingen.csv",
            "REVEL_URL": "revel.zip",
            "PHYLOP_DIR": "phylop/",
            "GNOMAD_URL": "gnomad/{chrom}.vcf.bgz",
        }.items():
            monkeypatch.setattr(acmg_data, name, server.url_for(path))
        yield server


@pytest.fixture
def library(tmp_path):
    """A library holding the one-gene genome, and a GRCh38 SpliceAI VCF."""
    lib = tmp_path / "lib"
    (lib / "genomes").mkdir(parents=True)
    shutil.copy(GENE / "genome.fa", lib / "genomes" / "genome.fa")
    text = (FIXTURES / "spliceai.vcf").read_text()
    text = text.replace("##INFO", "##contig=<ID=1,length=248956422>\n##INFO", 1)
    spliceai = _bgzf(_write(tmp_path / "spliceai.vcf", text))
    run_command([get_tool_path("tabix"), "-p", "vcf", str(spliceai)])
    return lib, spliceai


def _params(library, **extra) -> RefAcmgDataParams:
    lib, spliceai = library
    return RefAcmgDataParams(
        ref=lib, chromosomes="chr1", spliceai_file=spliceai, threads=2, **extra
    )


def _sa(library) -> Path:
    return library[0] / "ref" / "acmg" / "sa"


def _manifest(library) -> dict:
    return json.loads((_sa(library) / "acmg_data.json").read_text())


def _gets(server: RangeServer) -> int:
    return sum(method == "GET" for method, _ in server.requests)


def _classes(vcf: Path) -> dict[int, str]:
    out = {}
    with gzip.open(vcf, "rt") as f:
        for line in f:
            if not line.startswith("#"):
                info = dict(
                    kv.partition("=")[::2] for kv in line.split("\t")[7].split(";")
                )
                out[int(line.split("\t")[1])] = info.get("ACMG_CLASS", "")
    return out


@needs_fastvep
def test_builds_every_database_vcf_acmg_then_finds_in_the_library(
    publishers, library, tmp_path
):
    lib, _ = library
    # A connection cut half-way through gnomAD's stream resumes where it stopped.
    publishers.drop_paths = {"gnomad/chr1.vcf.bgz"}

    result = api.ref.acmg_data(_params(library))

    manifest = _manifest(library)
    built = {e["source"] for e in manifest["databases"].values() if e.get("records")}
    assert built == {s.key for s in acmg_data.SOURCES}
    clinvar = manifest["sources"]["clinvar"]
    assert clinvar["release"] == "20261108"
    assert clinvar["files"]["clinvar_20261108.vcf.gz"]["verified"].startswith("MD5 ")
    assert manifest["gff3"]["verified"].startswith("Ensembl CHECKSUMS")
    gnomad = manifest["sources"]["gnomad"]["files"]["chr1.vcf.bgz"]
    assert gnomad["verified"] == f"MD5 {gnomad['md5']}"
    assert "acmg_data_terms" in {w.code for w in result.warnings}
    assert {"acmg_data.json", GFF3} <= {Path(o.path).name for o in result.outputs}
    assert not (_sa(library) / ".building").exists()
    status = api.ref.status(RefStatusParams(ref=lib)).details["acmgData"]
    assert status["installed"] and status["chromosomes"] == ["chr1"]

    # vcf acmg reads the library's databases and GFF3 without being told.
    vcf = _bgzf(_write(tmp_path / "in_vep.vcf", (FIXTURES / "in_vep.vcf").read_text()))
    run_command([get_tool_path("tabix"), "-p", "vcf", str(vcf)])
    api.vcf.acmg(
        VcfAcmgParams(
            research_acmg=True,
            input=vcf,
            ref=lib / "genomes" / "genome.fa",
            outdir=tmp_path / "out",
        )
    )
    classes = _classes(tmp_path / "out" / "acmg_annotated.vcf.gz")
    assert classes[1017] == "LP"  # a null variant in a constrained, curated gene
    assert classes[1150] == "B"  # gnomAD joint AF 0.3, kept by the reduction: BA1
    summary = json.loads((tmp_path / "out" / "acmg_summary.json").read_text())
    assert summary["databases"]["sources"]["clinvar"]["release"] == "20261108"


@needs_fastvep
def test_a_failed_source_is_reported_and_a_rerun_builds_only_it(publishers, library):
    publishers.files["phylop/md5sum.txt"] = (
        b"0" * 32 + b"  chr1.phyloP100way.wigFix.gz\n"
    )

    with pytest.raises(api.CommandFailed, match="phylop_chr1"):
        api.ref.acmg_data(_params(library))
    built = _manifest(library)["databases"]
    assert "phylop_chr1" not in built and len(built) == 7

    phylop = publishers.files["phylop/chr1.phyloP100way.wigFix.gz"]
    publishers.files["phylop/md5sum.txt"] = (
        f"{_md5(phylop)}  chr1.phyloP100way.wigFix.gz\n".encode()
    )
    publishers.requests.clear()
    api.ref.acmg_data(_params(library))
    assert "phylop_chr1" in _manifest(library)["databases"]
    # Only phyloP's checksum list and file were fetched again.
    assert _gets(publishers) == 2

    publishers.requests.clear()
    result = api.ref.acmg_data(_params(library))
    assert _gets(publishers) == 0
    assert "nothing to do" in result.text


@needs_fastvep
def test_a_download_that_fails_its_published_md5_is_removed(publishers, library):
    publishers.files["clinvar.vcf.gz.md5"] = b"0" * 32 + b"  clinvar_20261108.vcf.gz\n"

    with pytest.raises(api.CommandFailed, match="clinvar:.*MD5"):
        api.ref.acmg_data(_params(library))

    assert not list((library[0] / "ref" / "acmg" / "sources").glob("clinvar*"))
    assert "clinvar" not in _manifest(library)["databases"]


def test_dry_run_shows_terms_and_sizes_and_writes_nothing(publishers, library):
    result = api.ref.acmg_data(_params(library, dry_run=True))

    assert not (library[0] / "ref" / "acmg").exists()
    assert _gets(publishers) == 0
    assert result.details["needed_bytes"] > 0
    assert len(result.details["to_build"]) == 9  # 8 databases and the GFF3
    assert "non-commercial" in result.text and "Illumina" in result.text


@needs_fastvep
def test_too_little_free_space_is_refused_before_any_download(
    publishers, library, monkeypatch
):
    monkeypatch.setattr(
        acmg_data.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1, used=0, free=1),
    )

    with pytest.raises(api.CommandFailed, match="needs about .* free"):
        api.ref.acmg_data(_params(library))
    assert _gets(publishers) == 0


@needs_fastvep
def test_a_spliceai_vcf_of_another_assembly_is_refused(publishers, library, tmp_path):
    text = (FIXTURES / "spliceai.vcf").read_text()
    text = text.replace("##INFO", "##contig=<ID=1,length=249250621>\n##INFO", 1)
    grch37 = _bgzf(_write(tmp_path / "grch37.vcf", text))
    run_command([get_tool_path("tabix"), "-p", "vcf", str(grch37)])

    with pytest.raises(api.CommandFailed, match="spliceai:.*hg19"):
        api.ref.acmg_data(_params((library[0], grch37)))
    assert not any(n.startswith("spliceai") for n in _manifest(library)["databases"])


@pytest.mark.parametrize("chromosomes", ["chrM", "chr23", "1-25"])
def test_chromosomes_other_than_1_to_22_x_and_y_are_refused(chromosomes, tmp_path):
    with pytest.raises(UsageError, match="chr1-22, X or Y"):
        RefAcmgDataParams(ref=tmp_path, chromosomes=chromosomes)


@needs_fastvep
def test_files_an_interrupted_run_left_are_checked_and_recorded_again(
    publishers, library
):
    """#629 review: a GFF3 downloaded but not yet recorded stayed unrecorded
    (so vcf acmg never found it), and a REVEL zip deleted after it was recorded
    was never fetched again."""
    lib, _ = library
    api.ref.acmg_data(_params(library))
    manifest = _manifest(library)
    del manifest["gff3"]
    (_sa(library) / "acmg_data.json").write_text(json.dumps(manifest))
    (lib / "ref" / "acmg" / "sources" / "revel.zip").unlink()
    for path in _sa(library).glob("revel_chr1*"):
        path.unlink()

    api.ref.acmg_data(_params(library))

    manifest = _manifest(library)
    assert manifest["gff3"]["verified"].startswith("Ensembl CHECKSUMS")
    assert manifest["databases"]["revel_chr1"]["records"] == 1
    assert api.ref.status(RefStatusParams(ref=lib)).details["acmgData"]["installed"]
