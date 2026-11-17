"""``vcf call``: several variant types in one run, optionally merged."""

import json
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib
from tests.smoke_utils import ensure_fake_data, run_cli
from wgsextract_cli.commands.vcf.call import merge_vcfs
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command

CLI_ROOT = Path(__file__).resolve().parents[1]
FAKE_DIR = CLI_ROOT / "out" / "fake_30x"

_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=chr1,length=1000>\n"
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    "{info}"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
)


def _vcf(path: Path, sample: str, records: list[str], info: str = "") -> str:
    path.write_text(_HEADER.format(sample=sample, info=info) + "".join(records))
    return bgzip_index(path)


def _query(vcf: str, fmt: str) -> list[str]:
    out = run_command(
        [get_tool_path("bcftools"), "query", "-f", fmt, vcf], capture_output=True
    )
    return out.stdout.splitlines()


@needs_htslib
def test_merge_keeps_every_record_in_position_order_under_one_sample(tmp_path):
    # Sniffles names its sample after the file, so an SV VCF's sample can
    # differ from the bcftools calls'; the merge must still be one sample.
    snps = _vcf(
        tmp_path / "snps.vcf",
        "HG002",
        ["chr1\t100\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n",
         "chr1\t500\t.\tC\tT\t50\tPASS\t.\tGT\t1/1\n"],
    )  # fmt: skip
    sv = _vcf(
        tmp_path / "sv.vcf",
        "fake",
        ["chr1\t300\t.\tN\t<DEL>\t40\tPASS\tSVTYPE=DEL;END=400\tGT\t0/1\n"],
        info='##INFO=<ID=SVTYPE,Number=1,Type=String,Description="SV type">\n'
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n',
    )
    merged = str(tmp_path / "variants.vcf.gz")

    merge_vcfs([snps, sv], merged)

    assert _query(merged, "%POS\t%ALT[\t%GT]\n") == [
        "100\tG\t0/1",
        "300\t<DEL>\t0/1",
        "500\tT\t1/1",
    ]
    samples = run_command(
        [get_tool_path("bcftools"), "query", "-l", merged], capture_output=True
    )
    assert samples.stdout.split() == ["HG002"]
    assert Path(merged + ".tbi").exists()


def test_unknown_type_is_a_usage_error():
    rc, _stdout, stderr = run_cli(
        ["vcf", "call", "--input", "x.bam", "--types", "snp,snv"]
    )
    assert rc == 2
    assert "not snv" in stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Delly is refused on Windows")
def test_windows_refuses_sv_as_a_usage_error():
    rc, _stdout, stderr = run_cli(
        ["vcf", "call", "--input", "x.bam", "--types", "snp,sv"]
    )
    assert rc == 2
    assert "delly is not supported on Windows" in stderr


def _call(tmp_path, *extra):
    ensure_fake_data(str(FAKE_DIR))
    rc, stdout, stderr = run_cli(
        [
            "vcf", "call",
            "--input", str(FAKE_DIR / "fake.bam"),
            "--ref", str(FAKE_DIR / "fake_ref.fa"),
            "--outdir", str(tmp_path),
            "--types", "snp,indel",
            "--ploidy", "2",
            "--json",
            *extra,
        ]
    )  # fmt: skip
    assert rc == 0, stderr
    document = json.loads(stdout)
    return [o["role"] for o in document["outputs"]], document


def _vcfs(folder):
    return sorted(p.name for p in folder.glob("*.vcf.gz"))


@needs_htslib
def test_merge_leaves_only_the_merged_vcf(tmp_path):
    roles, document = _call(tmp_path, "--merge")
    assert roles == ["merged_variants"]
    assert _vcfs(tmp_path) == ["variants.vcf.gz"]
    assert (tmp_path / "variants.vcf.gz.tbi").exists()
    assert document["outputs"][0]["metrics"]["variants"] > 0


@needs_htslib
def test_gvcf_replaces_the_snp_and_indel_vcfs(tmp_path):
    roles, _document = _call(tmp_path, "--reference-evidence", "gvcf")
    assert roles == ["reference_evidence", "reference_evidence_index"]
    assert _vcfs(tmp_path) == ["bcftools.g.vcf.gz"]


@needs_htslib
def test_the_run_counts_every_type_it_called(tmp_path):
    # PR 1060 review: each step overwrote the run's count with its own.
    _roles, document = _call(tmp_path)
    per_type = {o["role"]: o["metrics"]["variants"] for o in document["outputs"]}
    assert document["metrics"]["variants"] == per_type["snps"] + per_type["indels"]


@needs_htslib
def test_merge_with_one_type_still_writes_the_merged_vcf(tmp_path):
    # PR 1060 review: one part used to skip --merge silently.
    roles, _document = _call(tmp_path, "--merge", "--types", "snp")
    assert roles == ["merged_variants"]
    assert _vcfs(tmp_path) == ["variants.vcf.gz"]
