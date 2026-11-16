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
import signal
import threading
import time
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
chr1\t1668\t.\tGA\tG\t.\tPASS\tAF_joint=0.3;AC_joint=300000;AN_joint=1000000;nhomalt_joint=50000
"""
# A one-base deletion in the AAAAAA at 1669-1674, written right-shifted:
# gnomAD has it left-aligned, at 1668.
SHIFTED_DELETION = "1\t1673\t.\tAA\tA\t50\tPASS\t.\tGT\t0/1\n"
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
    gnomad_vcf = _bgzf(_write(tmp_path / "gnomad.vcf", GNOMAD))
    run_command([get_tool_path("tabix"), "-p", "vcf", str(gnomad_vcf)])
    gnomad = gnomad_vcf.read_bytes()
    spliceai = _spliceai(tmp_path / "served")
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
        "gnomad/chr1.vcf.bgz.tbi": Path(f"{gnomad_vcf}.tbi").read_bytes(),
        "spliceai.vcf.gz": spliceai.read_bytes(),
        "spliceai.vcf.gz.tbi": Path(f"{spliceai}.tbi").read_bytes(),
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
            "SPLICEAI_URL": "spliceai.vcf.gz",
        }.items():
            monkeypatch.setattr(acmg_data, name, server.url_for(path))
        yield server


def _spliceai(folder: Path) -> Path:
    """The fixture SpliceAI VCF, GRCh38 by its header, bgzipped and indexed."""
    folder.mkdir(exist_ok=True)
    text = (FIXTURES / "spliceai.vcf").read_text()
    text = text.replace("##INFO", "##contig=<ID=1,length=248956422>\n##INFO", 1)
    spliceai = _bgzf(_write(folder / "spliceai.vcf", text))
    run_command([get_tool_path("tabix"), "-p", "vcf", str(spliceai)])
    return spliceai


@pytest.fixture
def library(tmp_path):
    """A library holding the one-gene genome, and a GRCh38 SpliceAI VCF."""
    lib = tmp_path / "lib"
    (lib / "genomes").mkdir(parents=True)
    shutil.copy(GENE / "genome.fa", lib / "genomes" / "genome.fa")
    return lib, _spliceai(tmp_path)


def _params(library, **extra) -> RefAcmgDataParams:
    lib, spliceai = library
    return RefAcmgDataParams(
        **{"ref": lib, "chromosomes": "chr1", "spliceai_file": spliceai, "threads": 2}
        | extra
    )


def _sa(library) -> Path:
    return library[0] / "ref" / "acmg" / "sa"


def _manifest(library) -> dict:
    return json.loads((_sa(library) / "acmg_data.json").read_text())


def _gets(server: RangeServer) -> int:
    return sum(method == "GET" for method, _ in server.requests)


def _acmg(vcf: Path) -> dict[int, dict[str, str]]:
    """Each record's ACMG_* INFO values, by position."""
    out = {}
    with gzip.open(vcf, "rt") as f:
        for line in f:
            if not line.startswith("#"):
                cols = line.split("\t")
                info = dict(kv.partition("=")[::2] for kv in cols[7].split(";"))
                out[int(cols[1])] = {
                    k: v for k, v in info.items() if k.startswith("ACMG")
                }
    return out


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
    with pytest.raises(UsageError, match="chr1-22"):
        RefAcmgDataParams(ref=tmp_path, chromosomes=chromosomes)


@pytest.mark.parametrize("chromosomes", ["22-1", "1-1000000000", "chr5-3"])
def test_a_descending_or_enormous_range_is_refused_before_it_is_expanded(
    chromosomes, tmp_path
):
    """#1010 review: 1-1000000000 built a billion names before it was checked
    (in a server's request handler), and 22-1 chose no chromosome at all,
    leaving every per-chromosome database unbuilt without a word."""
    with pytest.raises(UsageError, match="runs upwards within chr1-22"):
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


def test_a_dry_run_leaves_the_last_builds_result_manifest(publishers, library):
    """#1010 review: a dry run with ref/acmg already there replaced the last
    build's acmg_data.manifest.json with its own empty inventory."""
    acmg = library[0] / "ref" / "acmg"
    acmg.mkdir(parents=True)
    previous = acmg / "acmg_data.manifest.json"
    previous.write_text('{"outputs": ["the last build"]}\n')

    api.ref.acmg_data(_params(library, dry_run=True))

    assert previous.read_text() == '{"outputs": ["the last build"]}\n'


@pytest.mark.parametrize("damage", ["{not json", '{"databases": {"x": 1}}'])
def test_ref_status_reports_an_unreadable_acmg_manifest_instead_of_failing(
    library, damage
):
    """#1010 review: an invalid acmg_data.json made all of `ref status` (and
    the app's --values call) fail."""
    sa = _sa(library)
    sa.mkdir(parents=True)
    (sa / "acmg_data.json").write_text(damage)

    status = api.ref.status(RefStatusParams(ref=library[0]))

    acmg = status.details["acmgData"]
    assert not acmg["installed"] and acmg["problem"]
    assert "unusable" in status.text


def test_revel_is_cut_one_chromosome_at_a_time(tmp_path):
    """#1010 review: every chromosome's REVEL CSV was written before any was
    built, about 6.5 GB for the genome, beyond the disk check's estimate; a
    CSV now waits for its slot, so those built and removed are all there is."""
    rows = (FIXTURES / "revel.csv").read_text().splitlines(keepends=True)
    header, row = rows[0], rows[1]
    body = "".join(row.replace("1,", f"{c},", 1) for c in ("1", "2", "3"))
    zipped = tmp_path / "revel.zip"
    with zipfile.ZipFile(zipped, "w") as archive:
        archive.writestr("revel_with_transcript_ids", header + body)
    parts = tmp_path / "parts"
    present = []

    for chromosome, part in acmg_data.split_revel(
        zipped,
        ["chr1", "chr3"],
        parts,
        lambda: present.append(len(list(parts.iterdir()))),
    ):
        assert part.read_text().count("\n") == 2, chromosome  # header and row
        part.unlink()

    assert present == [0, 0]  # nothing left waiting when the next is started


@needs_fastvep
def test_spliceai_provenance_and_sha256_are_those_of_the_file_used(
    publishers, library, tmp_path
):
    """#1010 review: a --spliceai-file was recorded (and copied into
    acmg_summary.json) as Ensembl's MANE SNVs, and had no SHA-256."""
    lib, spliceai = library
    api.ref.acmg_data(_params(library))

    recorded = _manifest(library)["sources"]["spliceai"]
    assert recorded["url"] == "" and "--spliceai-file" in recorded["label"]
    entry = recorded["files"][spliceai.name]
    assert entry["sha256"] == hashlib.sha256(spliceai.read_bytes()).hexdigest()


@needs_fastvep
def test_a_new_clinvar_release_is_fetched_with_the_download_cache_on(
    publishers, library, tmp_path, monkeypatch
):
    """#1010 review: with [dev] download_cache on, the URL-keyed cache gave
    back last week's ClinVar, which then failed the new release's MD5 on
    every rerun."""
    from wgsextract_cli.core.config import settings

    monkeypatch.setitem(settings, "dev", {"download_cache": True})
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    api.ref.acmg_data(_params(library))
    newer = gzip.compress((FIXTURES / "clinvar.vcf").read_bytes() + b"\n", mtime=0)
    publishers.files["clinvar.vcf.gz"] = newer
    publishers.files["clinvar.vcf.gz.md5"] = (
        f"{_md5(newer)}  /pub/clinvar_20261115.vcf.gz\n".encode()
    )
    manifest = _manifest(library)
    del manifest["databases"]["clinvar"]
    (_sa(library) / "acmg_data.json").write_text(json.dumps(manifest))

    api.ref.acmg_data(_params(library))

    assert _manifest(library)["sources"]["clinvar"]["release"] == "20261115"


@needs_fastvep
def test_a_phylop_download_whose_build_failed_is_kept_for_the_rerun(
    publishers, library
):
    """#1010 review: the cleanup after a failed source deleted the phyloP
    download (and any resumable .partial) the recovery message says a rerun
    resumes, so the rerun downloaded it all again."""
    broken = gzip.compress(b"not a wigFix file\n", mtime=0)
    publishers.files["phylop/chr1.phyloP100way.wigFix.gz"] = broken
    publishers.files["phylop/md5sum.txt"] = (
        f"{_md5(broken)}  chr1.phyloP100way.wigFix.gz\n".encode()
    )

    with pytest.raises(api.CommandFailed, match="phylop_chr1"):
        api.ref.acmg_data(_params(library))
    publishers.paths.clear()
    with pytest.raises(api.CommandFailed, match="phylop_chr1"):
        api.ref.acmg_data(_params(library))

    # Only the plan's HEAD for its size: no second download.
    assert publishers.paths.count("phylop/chr1.phyloP100way.wigFix.gz") == 1


@needs_fastvep
def test_the_library_drive_is_checked_when_the_databases_go_elsewhere(
    publishers, library, tmp_path, monkeypatch
):
    """#1010 review: only --sa-dir's drive was checked, so a roomy external
    --sa-dir let the downloads fill a nearly full library drive."""
    lib, _ = library
    real = shutil.disk_usage

    def usage(path):
        free = 1 if Path(path).is_relative_to(lib) else 10**15
        return SimpleNamespace(total=real(path).total, used=0, free=free)

    monkeypatch.setattr(acmg_data.shutil, "disk_usage", usage)

    with pytest.raises(api.CommandFailed, match=f"needs about .* free for {lib}"):
        api.ref.acmg_data(_params(library, sa_dir=tmp_path / "external"))
    assert _gets(publishers) == 0


@needs_fastvep
def test_ctrl_c_stops_the_queued_builds_and_the_preparation(publishers, library):
    """#1010 review: on Ctrl-C or SIGTERM the executors waited for every
    queued chromosome, so builds kept starting after the user stopped."""
    publishers.delays["gnomad/chr1.vcf.bgz"] = 2.0
    main = threading.get_ident()

    def interrupt_once_streaming() -> None:
        # The signal arrives as gnomAD's stream starts (after the plan's and
        # the step's HEAD), the other builds queued behind it.
        while publishers.paths.count("gnomad/chr1.vcf.bgz") < 2:
            time.sleep(0.05)
        signal.pthread_kill(main, signal.SIGINT)

    watcher = threading.Thread(target=interrupt_once_streaming, daemon=True)
    watcher.start()
    with pytest.raises(KeyboardInterrupt):
        api.ref.acmg_data(_params(library, threads=1))

    built = _manifest(library)["databases"]
    assert not [name for name in built if name.endswith("_chr1")]
    time.sleep(1)
    assert _manifest(library)["databases"] == built


def _link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")


def test_a_server_checks_the_library_and_folders_ref_acmg_data_writes(
    tmp_path, monkeypatch
):
    """#1010 review: with ref omitted the configured library wasn't checked
    against serve --allow, and a ref/acmg or sa folder linked out of an
    allowed library led the downloads and databases outside it."""
    from wgsextract_cli.core.config import settings
    from wgsextract_cli.server.operations import (
        PathGuard,
        RequestError,
        find,
        params_from_json,
    )

    op = find("ref/acmg-data")
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    allowed.mkdir()
    monkeypatch.setitem(settings, "reference_library", str(outside / "lib"))
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([allowed]).check(params_from_json(op, {}))

    lib = allowed / "lib"
    PathGuard([allowed]).check(params_from_json(op, {"ref": str(lib)}))
    _link(lib / "ref" / "acmg", outside / "acmg")
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([allowed]).check(params_from_json(op, {"ref": str(lib)}))

    other = allowed / "other"
    _link(other / "ref" / "acmg" / "sa", outside / "sa")
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([allowed]).check(params_from_json(op, {"ref": str(other)}))


def test_a_server_checks_the_gff3_vcf_acmg_finds_through_its_databases(tmp_path):
    """#1010 review: an allowed databases folder whose acmg_data.json names a
    GFF3 elsewhere made vcf acmg read it, and write fastVEP's transcript cache
    beside it, outside serve --allow and without the job lock on that folder."""
    from wgsextract_cli.server.operations import (
        PathGuard,
        RequestError,
        find,
        params_from_json,
    )

    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    sa = allowed / "lib" / "ref" / "acmg" / "sa"
    sa.mkdir(parents=True)
    outside.mkdir()
    gff3 = outside / GFF3
    gff3.write_bytes(b"")
    (sa / "acmg_data.json").write_text(json.dumps({"gff3": {"file": str(gff3)}}))
    vcf = allowed / "in.vcf.gz"
    request = {"input": str(vcf), "research_acmg": True}
    op = find("vcf/acmg")

    for given in ({"sa_dir": str(sa)}, {"ref": str(allowed / "lib")}):
        params = params_from_json(op, {**request, **given})
        with pytest.raises(RequestError, match="outside the folders"):
            PathGuard([allowed]).check(params)
        assert outside in set(params.write_folders())
    PathGuard([tmp_path]).check(params_from_json(op, {**request, "sa_dir": str(sa)}))


@pytest.mark.parametrize("linked", ["ref/acmg/sa/sites", "ref/spliceai_hg38.vcf.gz"])
def test_a_server_checks_the_per_run_cache_and_spliceai_vcf_vcf_acmg_uses(
    tmp_path, linked
):
    """Issue 629: per-run site data writes the databases folder's sites/ cache
    and reads the library's SpliceAI VCF; either linked out of an allowed
    library would lead the run outside serve --allow."""
    from wgsextract_cli.server.operations import (
        PathGuard,
        RequestError,
        find,
        params_from_json,
    )

    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    lib = allowed / "lib"
    (lib / "ref" / "acmg" / "sa").mkdir(parents=True)
    _link(lib / linked, outside / "target")
    op = find("vcf/acmg")
    request = {"input": str(allowed / "in.vcf.gz"), "research_acmg": True}

    params = params_from_json(op, {**request, "ref": str(lib)})
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([allowed]).check(params)
    library = {**request, "ref": str(lib), "site_data": "library"}
    PathGuard([allowed]).check(params_from_json(op, library))
    # The job lock covers the cache a per-run job writes.
    assert lib / "ref" / "acmg" / "sa" / "sites" in set(params.write_folders())


def _input(tmp_path: Path, extra: str = "") -> Path:
    lines = (FIXTURES / "in_vep.vcf").read_text().splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.startswith("1\t2500\t"))
    vcf = _bgzf(
        _write(tmp_path / "in_vep.vcf", "".join([*lines[:at], extra, *lines[at:]]))
    )
    run_command([get_tool_path("tabix"), "-p", "vcf", str(vcf)])
    return vcf


def _acmg_run(lib: Path, vcf: Path, out: Path, site_data: str = "auto"):
    return api.vcf.acmg(
        VcfAcmgParams(
            research_acmg=True,
            input=vcf,
            ref=lib / "genomes" / "genome.fa",
            outdir=out,
            site_data=site_data,
        ),
        check=False,
    )


@needs_fastvep
def test_per_run_gnomad_and_spliceai_classify_as_the_full_databases_do(
    publishers, library, tmp_path
):
    # Issue 629: a whole genome's gnomAD and SpliceAI databases are tens of GB;
    # read per run, only the input's sites are fetched and built.
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    manifest = _manifest(library)
    assert manifest["site_data"] == "per-run"
    built = {e["source"] for e in manifest["databases"].values()}
    assert not built & {"gnomad", "spliceai"}
    assert "gnomad/chr1.vcf.bgz" not in publishers.paths
    status = api.ref.status(RefStatusParams(ref=lib)).details["acmgData"]
    assert status["installed"] and status["siteData"] == "per-run"

    vcf = _input(tmp_path, SHIFTED_DELETION)
    first = _acmg_run(lib, vcf, tmp_path / "per_run")
    assert first.exit_code == 0, first.error
    per_run = _acmg(tmp_path / "per_run" / "acmg_annotated.vcf.gz")
    # gnomAD holds the deletion left-aligned, 5 bases before where it's written.
    assert per_run[1673]["ACMG_CLASS"] == "B"
    assert per_run[1150]["ACMG_CLASS"] == "B"
    summary = json.loads((tmp_path / "per_run" / "acmg_summary.json").read_text())
    read = summary["site_data"]["per_run"]
    assert read["gnomad"]["chr1"]["records"] == 2
    assert read["gnomad"]["chr1"]["bytes_read"] > 0
    assert read["spliceai"]["chr1"]["cached"] is False
    assert "acmg_data_terms" in {w.code for w in first.warnings}

    # The same sites again: the databases are reused, nothing is read.
    gets = len(publishers.gets())
    again = _acmg_run(lib, vcf, tmp_path / "again")
    assert len(publishers.gets()) == gets
    assert _acmg(tmp_path / "again" / "acmg_annotated.vcf.gz") == per_run
    summary = json.loads((tmp_path / "again" / "acmg_summary.json").read_text())
    assert summary["site_data"]["per_run"]["gnomad"]["chr1"]["cached"] is True
    assert again.exit_code == 0

    # Built whole, they give every record the same class and criteria.
    api.ref.acmg_data(_params(library, site_data="library"))
    full = _acmg_run(lib, vcf, tmp_path / "library")
    assert full.exit_code == 0, full.error
    assert _acmg(tmp_path / "library" / "acmg_annotated.vcf.gz") == per_run
    summary = json.loads((tmp_path / "library" / "acmg_summary.json").read_text())
    assert summary["site_data"]["per_run"] == {}

    # Per run beside them: theirs are left out, and left as they were (a
    # per-run SpliceAI database was once copied over the library's).
    forced = _acmg_run(lib, vcf, tmp_path / "forced", site_data="per-run")
    assert forced.exit_code == 0, forced.error
    assert _acmg(tmp_path / "forced" / "acmg_annotated.vcf.gz") == per_run
    for entry in _manifest(library)["databases"].values():
        if entry.get("sha256"):
            data = (_sa(library) / entry["file"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]


@needs_fastvep
@pytest.mark.parametrize(
    ("missing", "named"),
    [("gnomad/chr1.vcf.bgz", "lack gnomAD"),
     ("spliceai.vcf.gz", "SpliceAI")],
)  # fmt: skip
def test_per_run_refuses_when_the_publisher_has_no_data(
    publishers, library, tmp_path, missing, named
):
    # A source that can't be read per run is refused, as a library without it
    # is: fastVEP alone would classify without it, which reads as VUS.
    lib, _ = library
    api.ref.acmg_data(_params(library, site_data="per-run"))
    publishers.statuses[missing] = 404
    result = _acmg_run(lib, _input(tmp_path), tmp_path / "out")
    assert result.exit_code == 1
    assert result.error is not None and named in result.error.message
    assert not (tmp_path / "out" / "acmg_annotated.vcf.gz").exists()
