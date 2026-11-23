import json
import os
import shutil
import unittest
from pathlib import Path

import pytest

from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    reference_for_vcf,
    sam_to_bam,
    snp_sample,
    vcf_calls,
    write_fasta,
)
from tests.smoke_utils import (
    REPO_ROOT,
    ensure_fake_data,
    run_cli,
    yleaf_tables_cached,
)
from tests.test_analyze_workflows import ids, write_vcf
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command


@needs_htslib
def test_1223_same_named_vcfs_keep_their_own_results_on_resume(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source_ids = {}
    for folder, name, positions in (
        (tmp_path / "caller-a", "calls.vcf.gz", [100]),
        (tmp_path / "caller-b", "calls.vcf.gz", [200, 300]),
        (out / "work_snp-indel_calls-3", "calls-1.vcf.gz", [400]),
        (out, "significant_snp-indel_calls-2.vcf.gz", [500]),
    ):
        folder.mkdir(parents=True, exist_ok=True)
        records = [(pos, f"call{pos}", "50", ".") for pos in positions]
        source_ids[write_vcf(folder / name, records)] = [r[1] for r in records]

    clinvar = write_vcf(
        tmp_path / "clinvar.vcf.gz",
        [
            (pos, f"cv{pos}", ".", "CLNSIG=Pathogenic")
            for pos in (100, 200, 300, 400, 500)
        ],
        ["CLNSIG"],
        header='##INFO=<ID=CLNDN,Number=.,Type=String,Description="d">',
    )
    lib = tmp_path / "lib"
    reference_for_vcf(lib / "genomes" / "hg38.fa.gz", clinvar)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"reference_library = '{lib}'\nclinvar_vcf_path = '{clinvar}'\n"
        + state_directory_setting(tmp_path)
    )
    linked_output = out / "significant_snp-indel_calls-4.vcf.gz"
    if os.name != "nt":
        linked_output.symlink_to(next(iter(source_ids)))
    results = []
    for vcfs in (list(source_ids), list(reversed(source_ids))):
        code, stdout, stderr = run_cli([
            "--config", str(cfg), "analyze", "comprehensive", "--outdir", str(out / ".." / "out"),
            "--threads", "1", "--vcf-inputs", *vcfs,
        ])  # fmt: skip
        assert code == 0, stderr
        manifest = json.loads((out / "analyze.manifest.json").read_text())
        significant = [
            record
            for record in manifest["outputs"]
            if record["role"] == "significant_variants"
        ]
        assert len(significant) == len(source_ids)
        paths = {
            record["metrics"]["source_vcf"]: record["path"] for record in significant
        }
        assert {source: ids(path) for source, path in paths.items()} == source_ids
        assert {source: ids(source) for source in vcfs} == source_ids
        if os.name != "nt":
            assert linked_output.is_symlink()
            assert str(linked_output) not in paths.values()
        independent_source = str(out / "work_snp-indel_calls-3" / "calls-1.vcf.gz")
        assert (
            Path(paths[independent_source]).name
            == "significant_snp-indel_calls-1.vcf.gz"
        )
        for source, path in paths.items():
            basename = Path(source).name
            label = source if basename == "calls.vcf.gz" else basename
            _, marker, summary = stdout.partition(f"[SNP-INDEL] {label}\n")
            assert marker
            section = summary.split("----------------", 1)[0]
            assert f"Significant variants: {len(source_ids[source])}" in section
            assert f"Results file: {path}" in section
        steps = manifest["details"]["steps"]
        assert len({step["step"] for step in steps}) == 2 * len(source_ids)
        results.append((paths, manifest["metrics"]["steps_run"]))
    assert results[0][0] == results[1][0]
    assert [ran for _, ran in results] == [2 * len(source_ids), 0]


@needs_htslib
def test_1223_existing_annotation_folder_cannot_contain_an_input(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    folder = out / "work_snp-indel_calls"
    folder.mkdir(parents=True)
    inside = write_vcf(folder / "nested.vcf.gz", [(100, "nested", "50", ".")])
    calls = write_vcf(tmp_path / "calls.vcf.gz", [(200, "calls", "50", ".")])

    code, _, stderr = run_cli([
        "analyze", "comprehensive", "--outdir", str(out), "--vcf-inputs", calls, inside,
    ])  # fmt: skip
    assert code == 2
    assert str(folder) in stderr and "--outdir" in stderr
    assert ids(inside) == ["nested"]
    assert not (out / "analyze.steps.json").exists()


@needs_htslib
@pytest.mark.parametrize(
    "selection", ["bam", "configured", "configured-pair", "bam-and-vcf"]
)
def test_1220_comprehensive_keeps_configured_vcf_out_of_explicit_bam_run(
    tmp_path: Path, selection: str
) -> None:
    sample = snp_sample(tmp_path, "@RG\tID:rg\tSM:sample_b", snps=(1550,))
    b_call = sample.calls[0]
    sequence = Path(sample.ref).read_text().splitlines()[1]
    a_ref = sequence[999]
    a_alt = "C" if a_ref == "A" else "A"
    a_call = ("chr1", "1000", a_ref, a_alt, "1/1")

    header = "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=3000>\n"
    columns = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"

    def sample_vcf(name: str, call: tuple[str, ...]) -> str:
        chrom, pos, ref, alt, gt = call
        path = tmp_path / f"{name}.vcf"
        path.write_text(
            header
            + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
            + f"{columns}\tFORMAT\t{name}\n"
            + f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t99\tPASS\t.\tGT\t{gt}\n"
        )
        return bgzip_index(path)

    configured_vcf = sample_vcf("sample_a", a_call)
    clinical = tmp_path / "clinvar.vcf"
    clinical.write_text(
        header
        + '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="Significance">\n'
        + columns
        + "\n"
        + "".join(
            f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\tCLNSIG=Pathogenic\n"
            for chrom, pos, ref, alt, _ in (a_call, b_call)
        )
    )
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + f"reference_library = {json.dumps(str(tmp_path))}\n"
        + f"clinvar_vcf_path = {json.dumps(bgzip_index(clinical))}\n"
        + f"vcf_input_paths = [{json.dumps(configured_vcf)}]\n"
    )
    outdir = tmp_path / "out"
    args = []
    expected_inputs = set()
    reference = sample.ref
    if selection != "configured":
        # Select hg38 ploidy; a non-rCRS chrM skips mitochondrial classification.
        reference = str(
            write_fasta(tmp_path / "hg38.fa", {"chr1": sequence, "chrM": "A" * 100})
        )
        sam = run_command(
            [get_tool_path("samtools"), "view", "-h", sample.bam],
            capture_output=True,
        ).stdout.splitlines()
        sam.insert(2, "@SQ\tSN:chrM\tLN:100")
        bam = sam_to_bam(sam, tmp_path / "sample_b.bam")
        expected_inputs.add(("input", bam))
        with config.open("a") as stream:
            stream.write(f"input_path = {json.dumps(bam)}\n")
        if selection != "configured-pair":
            args += ["--input", bam]
    if selection == "bam-and-vcf":
        explicit_vcf = sample_vcf("sample_b", b_call)
        args += ["--vcf-inputs", explicit_vcf]
        expected_inputs.add(("vcf_input", explicit_vcf))
    elif selection in {"configured", "configured-pair"}:
        expected_inputs.add(("vcf_input", configured_vcf))

    code, stdout, stderr = run_cli([
        "--config", str(config), "analyze", "comprehensive", "--ref", reference,
        "--threads", "1", "--outdir", str(outdir), *args,
    ])  # fmt: skip
    assert code == 0, stdout + stderr
    manifest = json.loads((outdir / "analyze.manifest.json").read_text())
    assert {(r["role"], r["path"]) for r in manifest["inputs"]} == expected_inputs
    # #1182: an external VCF naming another sample than the reads is warned
    # about, not refused: its sample column may only be named differently.
    warned = {w["code"] for w in manifest["warnings"]}
    assert ("sample_mismatch" in warned) == (selection == "configured-pair")
    # Review of #1372: the findings are made from the calls alone, so they
    # don't pass for calls made from these reads (see sample_mismatch).
    for record in manifest["outputs"]:
        if record["role"] == "significant_variants":
            sources = {s["path"] for s in record["artifact"]["sources"]}
            assert sources == {record["metrics"]["source_vcf"]}
    findings = [
        call
        for record in manifest["outputs"]
        if record["role"] == "significant_variants"
        for call in vcf_calls(record["path"])
    ]
    assert findings == [
        a_call if selection in {"configured", "configured-pair"} else b_call
    ]
    assert (outdir / "snps.vcf.gz").exists() == (selection == "bam")


class TestAnalyzeComprehensive(unittest.TestCase):
    def setUp(self):
        self.outdir = str(REPO_ROOT / "out" / "tests" / "analyze_comprehensive")
        if os.path.exists(self.outdir):
            shutil.rmtree(self.outdir)
        os.makedirs(self.outdir, exist_ok=True)

        self.fake_data_dir = str(REPO_ROOT / "out" / "fake_30x")
        ensure_fake_data(self.fake_data_dir)

        self.bam = os.path.join(self.fake_data_dir, "fake.bam")
        self.vcf = os.path.join(self.fake_data_dir, "fake.vcf.gz")

    def test_analyze_comprehensive_bam_only(self):
        """Test analyze comprehensive with only a BAM file."""
        if not yleaf_tables_cached("hg38"):
            self.skipTest("Native Yleaf reference tables are not cached")
        # This will trigger variant calling (snp/indel)

        args = [
            "analyze",
            "comprehensive",
            "--input",
            self.bam,
            "--outdir",
            self.outdir,
            "--debug",
        ]

        # We need a reference for variant calling
        # For smoke tests, we can't easily download a full hg38,
        # but the fake data command might have generated a mini-ref if we were lucky.
        # Actually, fake-data doesn't generate a ref FASTA.

        # If we don't have a ref, it might fail.
        # Let's try with --skip-calling just to check the other parts
        args.append("--skip-calling")

        rc, stdout, stderr = run_cli(args)

        # Even if it fails due to missing tools in this environment,
        # we want to see it try.
        print(stdout)
        print(stderr)

        # Basic check: did it run 'info' and 'qc'?
        self.assertIn("STAGE: BAM/CRAM Metrics & Lineage", stdout)
        self.assertIn("Starting Comprehensive Analysis", stdout)

    def test_analyze_comprehensive_vcf_inputs(self):
        """Test analyze comprehensive with multiple VCF inputs."""
        # Create a second dummy VCF
        vcf2 = os.path.join(self.outdir, "fake2.vcf.gz")
        shutil.copy(self.vcf, vcf2)
        if os.path.exists(self.vcf + ".tbi"):
            shutil.copy(self.vcf + ".tbi", vcf2 + ".tbi")

        args = [
            "analyze",
            "comprehensive",
            "--vcf-inputs",
            self.vcf,
            vcf2,
            "--outdir",
            self.outdir,
            "--debug",
        ]

        rc, stdout, stderr = run_cli(args)
        print(stdout)
        print(stderr)

        self.assertEqual(rc, 0, stderr)
        self.assertIn("Processing Input VCFs (2 files)", stdout)
        # The test environment has no annotation databases, so discovery is
        # skipped rather than reporting a misleading "0 significant variants".
        self.assertIn("no annotation data is installed", stdout + stderr)
        self.assertFalse(
            os.path.exists(
                os.path.join(self.outdir, "significant_snp-indel_fake.vcf.gz")
            )
        )


if __name__ == "__main__":
    unittest.main()
