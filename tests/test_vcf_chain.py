"""Targeted annotation chains and step resume (issue #1291)."""

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, reference_for_vcf
from tests.smoke_utils import run_cli, state_directory_setting
from tests.test_vcf_annotations import (
    REVEL_HEADER,
    SAMPLE_ROWS,
    _bgzip,
    _query,
    _sample,
    _tsv,
    _vcf,
)
from wgsextract_cli.core.dependencies import get_tool_path

pytestmark = needs_tools("samtools", "bcftools", "bgzip", "tabix")
ChainCase = tuple[list[str], Path, str]


def _tool(name: str) -> str:
    path = get_tool_path(name)
    assert path is not None
    return path


@pytest.fixture
def chain_case(tmp_path: Path) -> ChainCase:
    sample = _sample(
        tmp_path, ["45 shifted AA A . PASS . GT 0/1", *SAMPLE_ROWS], length=1000
    )
    with gzip.open(sample, "rt") as f:
        content = f.read()
    content = content.replace(
        "##contig=<ID=chr1,length=1000>\n",
        "##contig=<ID=chr1,length=1000>\n##contig=<ID=chr2,length=1000>\n",
    )
    _bgzip(Path(sample), content + "chr2\t100\tofftarget\tA\tG\t.\tPASS\t.\tGT\t0/1\n")
    subprocess.run([_tool("tabix"), "-f", "-p", "vcf", sample], check=True)
    ref = reference_for_vcf(tmp_path / "ref" / "hg38.fa.gz", sample)
    clinvar = _vcf(tmp_path / "clinvar.vcf.gz",
                   ["100 c1 C T . . CLNSIG=Pathogenic;CLNDN=X GT 0/1"],
                   contig="1", length=1000, info=("CLNSIG", "CLNDN"))  # fmt: skip
    revel = tmp_path / "revel.tsv.gz"
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + "cpu_threads = 1\n"
        + f"clinvar_vcf_path = {json.dumps(clinvar)}\n"
        + f"revel_tsv_path = {json.dumps(str(revel))}\n",
        encoding="utf-8",
    )
    args = ["--json", "--config", str(config), "--ref", str(ref),
            "vcf", "chain-annotate", "--input", sample]  # fmt: skip
    return args, revel, sample


def test_1291_targeted_chain_matches_full_chain_then_slice(
    tmp_path: Path, chain_case: ChainCase
) -> None:
    args, revel, _ = chain_case
    _tsv(revel, REVEL_HEADER, ["1 100 100 C T 0.9"])
    full = tmp_path / "full"
    code, _, errors = run_cli([*args, "--annotations", "clinvar,revel",
                               "--outdir", str(full)])  # fmt: skip
    assert code == 0, errors
    sorted_bed = tmp_path / "sorted.bed"
    sorted_bed.write_text("chr1\t0\t1\nchr1\t99\t100\nchr1\t303\t310\n")
    full_slice = subprocess.check_output(
        [_tool("bcftools"), "view", "-H", "-T", str(sorted_bed),
         "--targets-overlap", "1", str(full / "chain_annotated.vcf.gz")], text=True
    )  # fmt: skip
    assert [r.split("\t")[2] for r in full_slice.splitlines()] == [
        "shifted",
        "snv",
        "block",
    ]

    bed = tmp_path / "unsorted.bed.gz"
    with gzip.open(bed, "wt") as f:
        f.write("1\t309\t310\nchr1\t99\t100\nchr1\t304\t307\n1\t0\t1\n1\t303\t306\n")
    for flag, value, threads in (
        ("--region", "1:1,1:100,1:304-310", "2"),
        ("--bed", str(bed), "auto"),
    ):
        out = tmp_path / flag.removeprefix("--")
        code, stdout, errors = run_cli([
            *args, "--annotations", "clinvar,revel", "--outdir", str(out),
            "--threads", threads, "--keep-intermediates", flag, value,
        ])  # fmt: skip
        assert code == 0, (stdout, errors)
        result = json.loads(stdout)
        assert result["details"]["scope"][flag.removeprefix("--")] == value
        (selected,) = (out / "chain_select").glob("*.vcf.gz")
        assert _query(selected, "%CHROM\n") == ["chr1"] * 5
        target = out / "chain_annotated.vcf.gz"
        assert (
            subprocess.check_output(
                [_tool("bcftools"), "view", "-H", str(target)], text=True
            )
            == full_slice
        )
        for child in ("clinvar", "revel"):
            (manifest,) = out.glob(f"chain_step_*_{child}/*.manifest.json")
            expected = None if threads == "auto" else int(threads)
            assert (
                json.loads(manifest.read_text())["provenance"]["params"]["threads"]
                == expected
            )

    (retained,) = (out / "chain_trim").glob("*.vcf.gz")
    before = retained.read_bytes()
    code, _, errors = run_cli([
        *args[:-1], str(retained), "--annotations", "clinvar,revel",
        "--outdir", str(out), "--bed", str(bed),
    ])  # fmt: skip
    assert code != 0
    assert "another --outdir" in errors
    assert retained.read_bytes() == before


@pytest.mark.parametrize("region", ["chr1:800-900", "chrNotPresent"])
def test_1291_empty_scope_writes_an_indexed_vcf_and_table(
    tmp_path: Path, chain_case: ChainCase, region: str
) -> None:
    args, revel, _ = chain_case
    _tsv(revel, REVEL_HEADER, ["1 100 100 C T 0.9"])
    out = tmp_path / "empty"
    code, stdout, errors = run_cli([
        *args, "--annotations", "clinvar,revel", "--outdir", str(out),
        "--region", region, "--to-table",
    ])  # fmt: skip
    assert code == 0, (stdout, errors)
    result = json.loads(stdout)
    roles = {item["role"]: Path(item["path"]) for item in result["outputs"]}
    assert _query(roles["annotated_vcf"]) == []
    assert Path(str(roles["annotated_vcf"]) + ".tbi").is_file()
    assert "clinvar_significance" in roles["variant_table"].read_text().splitlines()[0]
    assert roles["variant_database"].is_file()


def test_1291_empty_target_keeps_the_bigwig_annotation_header(
    tmp_path: Path, chain_case: ChainCase
) -> None:
    pybigwig = pytest.importorskip("pyBigWig")
    args, _, _ = chain_case
    bigwig = str(tmp_path / "gerp.bw")
    with pybigwig.open(bigwig, "w") as bw:
        bw.addHeader([("1", 1000), ("2", 1000)])
        bw.addEntries("1", 99, values=[2.5], span=1, step=1)
    with (tmp_path / "config.toml").open("a", encoding="utf-8") as f:
        f.write(f"gerp_bigwig_path = {json.dumps(bigwig)}\n")
    out = tmp_path / "empty-gerp"
    code, stdout, errors = run_cli([
        *args, "--annotations", "gerp", "--outdir", str(out),
        "--region", "chr1:800-900",
    ])  # fmt: skip
    assert code == 0, (stdout, errors)
    vcf = out / "chain_annotated.vcf.gz"
    assert _query(vcf) == []
    assert "##INFO=<ID=GERP," in subprocess.check_output(
        [_tool("bcftools"), "view", "-h", str(vcf)], text=True
    )


def test_1291_chain_resumes_after_failed_child_and_invalidates_downstream(
    tmp_path: Path, chain_case: ChainCase
) -> None:
    args, revel, sample = chain_case
    out = tmp_path / "resume"
    revel.write_text("not gzip")
    base = [*args, "--outdir", str(out), "--keep-intermediates"]
    code, _, errors = run_cli([*base, "--annotations", "clinvar,revel"])
    assert code != 0
    assert "Annotation step 'revel' failed" in errors
    assert not (out / "chain_annotated.vcf.gz").exists()
    revisited = out / "chain_step_1_clinvar" / "clinvar_annotated.vcf.gz"
    original_mtime = revisited.stat().st_mtime_ns
    _tsv(revel, REVEL_HEADER, ["1 100 100 C T 0.9"])

    def rerun(annotations: str, *options: str) -> list[tuple[str, str]]:
        code, stdout, errors = run_cli([*base, "--annotations", annotations, *options])
        assert code == 0, (stdout, errors)
        return [
            (row["step"], row["action"])
            for row in json.loads(stdout)["details"]["steps"]
        ]

    assert rerun("clinvar,revel", "--threads", "2") == [
        ("clinvar:1", "skip"),
        ("revel:2", "run"),
    ]
    assert revisited.stat().st_mtime_ns == original_mtime
    final = out / "chain_annotated.vcf.gz"
    assert "snv:Pathogenic:0.9" in _query(final, "%ID:%CLNSIG:%REVEL\n")
    assert rerun("clinvar,revel", "--force-step", "clinvar") == [
        ("clinvar:1", "run"),
        ("revel:2", "run"),
    ]
    assert rerun("revel,clinvar") == [("revel:1", "run"), ("clinvar:2", "run")]

    with gzip.open(sample, "rt") as f:
        text = f.read()
    _bgzip(Path(sample), text.replace("\tsnv\t", "\tchanged\t"))
    subprocess.run([_tool("tabix"), "-f", "-p", "vcf", sample], check=True)
    assert rerun("revel,clinvar") == [("revel:1", "run"), ("clinvar:2", "run")]
    assert "changed:Pathogenic:0.9" in _query(final, "%ID:%CLNSIG:%REVEL\n")
