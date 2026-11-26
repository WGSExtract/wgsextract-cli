"""Prepared inputs must not become persistent output or resource identities."""

import errno
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from tests.home_isolation import isolate_home
from tests.htslib_helpers import needs_tools, write_fasta
from tests.test_pharmcat import SNPS, _release, _view
from tests.test_pharmcat import _vcf as pharmcat_vcf
from tests.test_pharmcat import pharmcat_env as pharmcat_env
from tests.test_temp_directory import (
    _files_seen_under,
    _python_writes,
    _tree,
    _wgsextract,
)
from tests.test_trio_vcfs import _vcf
from wgsextract_cli import api
from wgsextract_cli.commands.vcf.annotation_helpers import prepare_tabix_annotation
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import run_command

pytestmark = needs_tools("bcftools", "bgzip", "tabix", "samtools")


@pytest.mark.parametrize("literal_exists", [False, True])
def test_1093_api_annotation_expands_tilde_before_creating_input_scratch(
    tmp_path, monkeypatch, literal_exists
):
    home = tmp_path / "home"
    home.mkdir()
    isolate_home(monkeypatch, home)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(settings, "temp_directory", raising=False)
    literal = (tmp_path / "~" / "out").resolve()
    if literal_exists:
        literal.mkdir(parents=True)
    source = _plain_vcf(tmp_path / "lab" / "sample.vcf")
    resource = _plain_vcf(tmp_path / "resources" / "gnomad.vcf", frequency=True)
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 1000})
    with _python_writes(monkeypatch) as written:
        result = api.vcf.gnomad(
            api.VcfGnomadParams(
                vcf_input=source, gnomad_file=resource, ref=ref, outdir=Path("~/out"),
                threads=1,
            )
        )  # fmt: skip
    expected = (home / "out").resolve()
    assert result.status == "ok"
    assert (expected / "gnomad_annotated.vcf.gz").is_file()
    assert any(
        path.is_relative_to(expected) and path.name.endswith(".vcf.gz.tmp")
        for path in written
    )
    assert not any(path.is_relative_to(literal) for path in written)
    assert not literal.exists() or list(literal.iterdir()) == []
    assert not any(path.name.startswith(".wgse") for path in expected.iterdir())


def test_1093_pharmcat_reports_keep_the_supplied_plain_symlink_basename(
    tmp_path, pharmcat_env
):
    release = _release(tmp_path / "pharmcat")
    archived = tmp_path / "archive" / "0001.vcf"
    archived.parent.mkdir()
    archived.write_text(
        "\n".join(_view(pharmcat_vcf(tmp_path / "fixture.vcf.gz", SNPS))) + "\n"
    )
    source = tmp_path / "sample.vcf"
    try:
        source.symlink_to(archived)
    except OSError as error:
        pytest.skip(f"Symlinks unavailable: {error}")
    output = tmp_path / "out"
    api.vcf.pharmcat(
        api.VcfPharmcatParams(vcf_input=source, pharmcat_dir=release, outdir=output)
    )
    assert (output / "sample.report.html").is_file()
    assert (output / "sample.phenotype.json").is_file()
    assert not (output / "0001.report.html").exists()
    assert list(archived.parent.iterdir()) == [archived]


@pytest.mark.parametrize("backend", ["cli", "api"])
@pytest.mark.parametrize("configured_temp", [False, True])
def test_1093_plain_vcf_copy_uses_owned_output_fallback_and_leaves_inputs_untouched(
    capsys, tmp_path, monkeypatch, backend, configured_temp
):
    lab = tmp_path / "lab"
    lab.mkdir()
    source = lab / "s.vcf"
    source.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\tc\tA\tG\t50\tPASS\t.\nchr1\t20\td\tA\tT\t5\tPASS\t.\n",
        newline="\n",
    )
    before = _tree(lab)
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    scratch.mkdir()
    with _files_seen_under(scratch, output) as seen:
        if backend == "api":
            if configured_temp:
                monkeypatch.setitem(settings, "temp_directory", str(scratch))
            result = api.vcf.filter(
                api.VcfFilterParams(vcf_input=source, expr="QUAL>20", outdir=output)
            ).to_dict()
        else:
            argv = ["vcf", "filter", "--vcf-input", str(source), "--expr",
                    "QUAL>20", "--outdir", str(output)]  # fmt: skip
            if configured_temp:
                argv += ["--tmp-dir", str(scratch)]
            result = _wgsextract(capsys, *argv)
    assert result["status"] == "ok", result["error"]
    assert _tree(lab) == before
    assert sorted(p.name for p in output.iterdir()) == [
        "filtered.vcf.gz",
        "filtered.vcf.gz.tbi",
        "manifests",
    ]
    queried = run_command(
        ["bcftools", "query", "-f", "%ID\n", str(output / "filtered.vcf.gz")],
        capture_output=True,
    )
    assert queried.stdout.split() == ["c"]
    assert any(name.endswith("s.vcf.gz") for name in seen[scratch]) is configured_temp
    assert (
        any(name.endswith("s.vcf.gz") for name in seen[output]) is not configured_temp
    )
    assert list(scratch.iterdir()) == []


def test_1093_plain_table_preparation_is_reused_without_writing_beside_input(tmp_path):
    plain = tmp_path / "revel.tsv"
    header = "#Chr\tStart\tEnd\tRef\tAlt\tREVEL"
    plain.write_text(f"{header}\n1\t100\t100\tC\tT\t0.5\n")
    prepared = prepare_tabix_annotation(str(plain), "REVEL")
    assert Path(prepared).name == f"{plain.name}.gz"
    assert not Path(prepared).is_relative_to(tmp_path)
    assert list(tmp_path.iterdir()) == [plain]
    assert gzip.open(prepared, "rt").read().endswith("0.5\n")
    first = Path(prepared).stat().st_mtime_ns
    assert prepare_tabix_annotation(str(plain), "REVEL") == prepared
    assert Path(prepared).stat().st_mtime_ns == first
    plain.write_text(f"{header}\n1\t100\t100\tC\tT\t0.75\n")
    os.utime(plain, ns=(first + 10**9, first + 10**9))
    prepare_tabix_annotation(str(plain), "REVEL")
    assert gzip.open(prepared, "rt").read().endswith("0.75\n")
    index = Path(f"{prepared}.tbi")
    assert index.stat().st_mtime_ns >= Path(prepared).stat().st_mtime_ns


def _plain_vcf(path: Path, *, frequency: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    )
    if frequency:
        header += '##INFO=<ID=AF,Number=A,Type=Float,Description="Frequency">\n'
    info = "AF=0.25" if frequency else "."
    path.write_text(
        header
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        + f"chr1\t10\trs10\tA\tG\t50\tPASS\t{info}\tGT\t0/1\n"
        + f"chr1\t30\trs30\tA\tT\t50\tPASS\t{info}\tGT\t0/1\n",
        newline="\n",
    )
    return path


@pytest.mark.parametrize("alias", [False, True], ids=["plain", "symlink"])
def test_1093_region_table_keeps_original_name_provenance_and_default_outputs(
    tmp_path, capsys, alias
):
    source = _plain_vcf(tmp_path / "lab" / "sample.vcf")
    if alias:
        archived = tmp_path / "archive" / "0001.vcf"
        archived.parent.mkdir()
        source.rename(archived)
        try:
            source.symlink_to(archived)
        except OSError as error:
            pytest.skip(f"Symlinks unavailable: {error}")
    original = source.read_bytes()
    result = _wgsextract(
        capsys, "vcf", "table", "--vcf-input", str(source), "--region", "chr1:1-20"
    )
    assert result["status"] == "ok", result["error"]
    output = source.parent / "WGSEv6"
    assert (output / "sample_variants.tsv").is_file()
    with sqlite3.connect(output / "sample_variants.sqlite") as db:
        assert db.execute("SELECT pos FROM variants").fetchall() == [(10,)]
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
    assert metadata["input"] == str(source)
    assert metadata["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original
    assert not any(p.name.startswith(".wgse") for p in output.iterdir())
    assert sorted(p.name for p in source.parent.iterdir()) == ["WGSEv6", "sample.vcf"]


def test_1093_annotation_manifest_keeps_the_supplied_plain_resource_path(
    tmp_path, capsys
):
    source = _plain_vcf(tmp_path / "lab" / "sample.vcf")
    annotation = _plain_vcf(tmp_path / "resources" / "frequencies.vcf", frequency=True)
    result = _wgsextract(
        capsys, "vcf", "annotate", "--vcf-input", str(source),
        "--ann-vcf", str(annotation), "--cols", "INFO/AF",
        "--outdir", str(tmp_path / "out"),
    )  # fmt: skip
    assert result["status"] == "ok", result["error"]
    recorded = next(item for item in result["inputs"] if item["role"] == "annotation")
    assert recorded["path"] == str(annotation)
    assert Path(recorded["path"]).is_file()
    assert list(annotation.parent.iterdir()) == [annotation]


@pytest.mark.parametrize("reference_source", ["adjacent", "manifest"])
def test_1093_plain_proband_keeps_adjacent_gvcf_reference_evidence(
    tmp_path, capsys, reference_source
):
    lab = tmp_path / "lab"
    lab.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    ref_folder = lab if reference_source == "adjacent" else tmp_path / "reference"
    ref = write_fasta(ref_folder / "ref.fa", {"chr1": "T" * 100_000})
    if reference_source == "manifest":
        (lab / "manifest.json").write_text(json.dumps({"files": {"ref": str(ref)}}))
    child = Path(_vcf(lab / "child.vcf.gz", ["500 dn T A 0/1:50"]))
    plain = child.with_suffix("")
    with gzip.open(child, "rb") as stream:
        plain.write_bytes(stream.read())
    child.unlink()
    Path(str(child) + ".tbi").unlink()
    mother = _vcf(
        lab / "mother.vcf.gz",
        ["100 . T <*> 0/0:40|END=1000"],
        header='##ALT=<ID=*,Description="Any other allele">\n',
    )
    father = _vcf(lab / "father.vcf.gz", ["500 . T . 0/0:50"])
    result = _wgsextract(
        capsys, "vcf", "trio", "--proband", str(plain),
        "--mother", mother, "--father", father, "--mode", "denovo",
        "--outdir", str(tmp_path / "out"), "--ref", str(library),
    )  # fmt: skip
    assert result["status"] == "ok", result["error"]
    queried = run_command(
        ["bcftools", "query", "-f", "%ID[ %GT]\n",
         str(tmp_path / "out" / "trio_denovo.vcf.gz")],
        capture_output=True,
    )  # fmt: skip
    assert queried.stdout.split() == ["dn", "0/1", "0/0", "0/0"]
    assert not any(w["code"] == "gvcf_reference_missing" for w in result["warnings"])
    assert not Path(str(plain) + ".gz").exists()


@pytest.mark.parametrize("use_tmp", [False, True], ids=["output-fallback", "tmp-dir"])
@pytest.mark.parametrize("resource_format", ["vcf", "tsv"])
def test_1093_resource_cache_survives_run_cleanup_and_temporary_copy_identities(
    tmp_path, capsys, monkeypatch, use_tmp, resource_format
):
    source = _plain_vcf(tmp_path / "lab" / "sample.vcf")
    annotation = _plain_vcf(tmp_path / "resources" / "gnomad.vcf", frequency=True)
    if resource_format == "tsv":
        annotation.unlink()
        annotation = annotation.with_suffix(".tsv")
        annotation.write_text("#CHROM\tPOS\tREF\tALT\tAF\nchr1\t10\tA\tG\t0.25\n")
    ref = write_fasta(tmp_path / "reference" / "ref.fa", {"chr1": "A" * 1000})
    scratch, output = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    options = ["--tmp-dir", str(scratch)] if use_tmp else []
    real_replace = os.replace

    def cross_volume(source_path, destination):
        if Path(source_path).is_relative_to(scratch) and not Path(
            destination
        ).is_relative_to(scratch):
            raise OSError(errno.EXDEV, "separate temporary volume")
        real_replace(source_path, destination)

    if use_tmp:
        monkeypatch.setattr(os, "replace", cross_volume)
    for cached in (False, True):
        result = _wgsextract(
            capsys, "vcf", "gnomad", "--vcf-input", str(source),
            "--gnomad-file", str(annotation), "--ref", str(ref),
            "--outdir", str(output), "--threads", "1", *options,
        )  # fmt: skip
        assert result["status"] == "ok", result["error"]
        assert result["metrics"]["annotation_normalization_cache_hit"] is cached
        queried = run_command(
            ["bcftools", "query", "-r", "chr1:10-10", "-f", "%GNOMAD_AF\n",
             str(output / "gnomad_annotated.vcf.gz")],
            capture_output=True,
        )  # fmt: skip
        assert queried.stdout.strip() == "0.25"
        assert list(scratch.iterdir()) == []
        assert sorted(p.name for p in output.iterdir()) == [
            "gnomad_annotated.vcf.gz",
            "gnomad_annotated.vcf.gz.tbi",
            "manifests",
        ]
    assert list(source.parent.iterdir()) == [source]
    assert list(annotation.parent.iterdir()) == [annotation]
