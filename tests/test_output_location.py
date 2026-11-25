"""Outputs go to a WGSEv6/ folder beside the lab's primary input, so the folder
of original lab files stays untouched (only indexes may be added there)."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from wgsextract_cli.api import ExtractRegionParams
from wgsextract_cli.api._params import with_config_defaults
from wgsextract_cli.api.analyze import AnalyzeComprehensiveParams
from wgsextract_cli.api.panel import PanelBedParams
from wgsextract_cli.api.qc import QcAncestryParams, RelateParams
from wgsextract_cli.api.vcf import VcfPhaseParams, VcfTableParams
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.output_location import (
    default_output_directory,
    primary_overwrite_warning,
)


@pytest.fixture(autouse=True)
def _no_output_setting():
    settings.pop("output_directory", None)


def _outdir(tmp_path: Path, name: str) -> Path | None:
    params = ExtractRegionParams(input=tmp_path / name, target="mt", formats="fasta")
    return with_config_defaults(params).outdir


def test_outputs_go_to_wgse_beside_the_lab_file(tmp_path):
    assert _outdir(tmp_path, "lab/HG002.bam") == tmp_path / "lab" / "WGSEv6"


# The v0.8.8 folder was wgse/: its outputs must not get a nested WGSEv6/.
@pytest.mark.parametrize("folder", ["WGSEv6", "wgse"])
def test_an_input_already_in_an_output_folder_keeps_its_outputs_there(tmp_path, folder):
    assert _outdir(tmp_path, f"lab/{folder}/HG002.bam") == tmp_path / "lab" / folder


def test_alignments_then_reads_then_variants_place_the_folder(tmp_path):
    bam, fastq, vcf, chip = (
        tmp_path / d / f
        for d, f in [
            ("a", "x.cram"),
            ("r", "x_R1.fq.gz"),
            ("v", "x.vcf.gz"),
            ("c", "x.txt"),
        ]
    )
    assert default_output_directory([vcf, fastq, bam]) == tmp_path / "a" / "WGSEv6"
    assert default_output_directory([chip, vcf, (fastq,)]) == tmp_path / "r" / "WGSEv6"
    assert default_output_directory([chip, vcf]) == tmp_path / "v" / "WGSEv6"
    assert default_output_directory([chip]) == tmp_path / "c" / "WGSEv6"
    # A reference FASTA or a URL decides nothing.
    assert default_output_directory([tmp_path / "hg38.fa", "https://x/y.bam"]) is None


def test_writing_a_primary_kind_into_the_lab_folder_warns(tmp_path):
    lab = tmp_path / "lab"
    bam = lab / "HG002.bam"
    assert primary_overwrite_warning(lab / "HG002.vcf.gz", [bam], lab / "WGSEv6")
    # Indexes and outputs in the output folder are fine, as is a lab folder the
    # user chose as the output folder.
    assert primary_overwrite_warning(lab / "HG002.bam.bai", [bam], None) is None
    assert (
        primary_overwrite_warning(lab / "WGSEv6" / "x.vcf", [bam], lab / "WGSEv6")
        is None
    )
    assert primary_overwrite_warning(lab / "HG002.vcf.gz", [bam], lab) is None


def test_1087_auxiliary_text_and_sites_vcf_do_not_choose_output_folder(tmp_path):
    gene_list = PanelBedParams(gene_file=tmp_path / "lists" / "genes.txt")
    sketches = RelateParams(
        inputs=(tmp_path / "sample.somalier",),
        sites=tmp_path / "reference" / "sites.vcf.gz",
    )
    assert with_config_defaults(gene_list).outdir is None
    assert with_config_defaults(sketches).outdir is None


def test_1087_vcf_override_places_outputs_beside_the_file_actually_read(tmp_path):
    old = tmp_path / "old" / "sample.vcf"
    current = tmp_path / "current" / "sample.vcf"
    assert (
        with_config_defaults(VcfTableParams(input=old, vcf_input=current)).outdir
        == current.parent / "WGSEv6"
    )
    reads = tmp_path / "reads" / "sample.bam"
    assert (
        with_config_defaults(VcfPhaseParams(input=reads, vcf_input=current)).outdir
        == reads.parent / "WGSEv6"
    )


def test_1087_working_folder_workflows_keep_their_declared_output_default(tmp_path):
    params = AnalyzeComprehensiveParams(input=tmp_path / "lab" / "sample.bam")
    assert with_config_defaults(params).outdir is None


def test_1087_zipped_microarray_outputs_do_not_land_in_lab_folder(tmp_path):
    archive = tmp_path / "genome.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("genome.txt", "# rsid\tchromosome\tposition\tgenotype\n")
    assert (
        with_config_defaults(QcAncestryParams(input=archive)).outdir
        == tmp_path / "WGSEv6"
    )
    resource = tmp_path / "reference.zip"
    with ZipFile(resource, "w") as zipped:
        zipped.writestr("reference.fa", ">chr1\nACGT\n")
    assert default_output_directory([resource]) is None
    reports = tmp_path / "fastqc.zip"
    with ZipFile(reports, "w") as zipped:
        zipped.writestr("report/summary.txt", "PASS\tSummary\tsample.fastq\n")
        zipped.writestr("report/fastqc_data.txt", "##FastQC\n")
    assert default_output_directory([reports]) is None


def test_bam_index_keeps_lab_folder_free_of_manifests_and_info_json(tmp_path):
    """Even when `bam index` writes .bai beside the source BAM in the lab folder,
    its manifest goes into WGSEv6/manifests/ — never into the lab folder."""
    import shutil

    from tests.htslib_helpers import sam_to_bam
    from wgsextract_cli import api
    from wgsextract_cli.api import BamIndexParams, InfoParams

    if not shutil.which("samtools"):
        pytest.skip("samtools not found in PATH")
    lab = tmp_path / "lab"
    lab.mkdir()
    bam = sam_to_bam(
        [
            "@SQ\tSN:chr1\tLN:500",
            f"r1\t0\tchr1\t10\t60\t50M\t*\t0\t0\t{'A' * 50}\t{'I' * 50}",
        ],
        lab / "sample.bam",
    )
    Path(f"{bam}.bai").unlink(missing_ok=True)
    (lab / "sample.sam").unlink(missing_ok=True)
    api.bam.index(BamIndexParams(input=bam))
    assert (lab / "sample.bam.bai").is_file()
    assert sorted(p.name for p in lab.iterdir()) == [
        "WGSEv6",
        "sample.bam",
        "sample.bam.bai",
    ]
    assert (lab / "WGSEv6" / "manifests" / "sample.bam.bai.manifest.json").is_file()

    api.info.info(InfoParams(input=bam))
    assert sorted(p.name for p in lab.iterdir()) == [
        "WGSEv6",
        "sample.bam",
        "sample.bam.bai",
    ]
    assert (lab / "WGSEv6" / "manifests" / "sample.bam.wgse_info.json").is_file()
