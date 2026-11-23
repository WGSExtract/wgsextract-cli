"""#1182: run lineage and typed BAM/VCF artifacts in result manifests, and
consumers refusing incompatible artifacts before they run."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    reference_for_vcf,
    sam_to_bam,
    snp_sample,
    write_fasta,
)
from tests.smoke_utils import run_cli, state_directory_setting
from wgsextract_cli.core.results import load_schema
from wgsextract_cli.core.utils import run_command

HG38_CHR1 = 248956422
HG19_CHR1 = 249250621


def _manifest(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text())
    Draft202012Validator(load_schema()).validate(document)
    return document


def _output(document: dict[str, Any], role: str) -> dict[str, Any]:
    return next(r for r in document["outputs"] if r["role"] == role)


def _call_snps(sample: Any, outdir: Path, *extra: str) -> tuple[int, str]:
    code, _, stderr = run_cli([
        "vcf", "snp", "--input", sample.bam, "--ref", sample.ref,
        "--outdir", str(outdir), "--ploidy", "2", "--threads", "1", *extra,
    ])  # fmt: skip
    return code, stderr


def _clinvar_config(tmp_path: Path, sample: Any) -> Path:
    """A reference library and a ClinVar VCF naming the sample's SNPs."""
    plain = tmp_path / "clinvar.vcf"
    rows = [
        f"chr1\t{pos}\tcv{pos}\t{ref}\t{alt}\t.\t.\tCLNSIG=Pathogenic;CLNDN=d"
        for _chrom, pos, ref, alt, _gt in sample.calls
    ]
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=3000>\n"
        '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="s">\n'
        '##INFO=<ID=CLNDN,Number=.,Type=String,Description="d">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" + "\n".join(rows) + "\n"
    )
    clinvar = bgzip_index(plain)
    lib = tmp_path / "lib"
    reference_for_vcf(lib / "genomes" / "hg38.fa.gz", clinvar)
    config = tmp_path / "config.toml"
    config.write_text(
        f"reference_library = '{lib}'\nclinvar_vcf_path = '{clinvar}'\n"
        + state_directory_setting(tmp_path)
    )
    return config


def _analyze(config: Path, vcf: Path, outdir: Path) -> list[str]:
    return ["--config", str(config), "analyze", "comprehensive", "--threads", "1",
            "--vcf-inputs", str(vcf), "--outdir", str(outdir)]  # fmt: skip


@needs_htslib
def test_1182_bam_to_vcf_to_analyze_links_runs_sources_and_indexes(
    tmp_path: Path,
) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "out" / "calls"
    code, stderr = _call_snps(sample, calls)
    assert code == 0, stderr

    producer = _manifest(calls / "snps.vcf.manifest.json")
    run = producer["run"]
    assert run["root"] == run["id"] and run["parent"] is None
    bam = producer["inputs"][0]
    assert bam["artifact"]["origin"] == "external"
    assert bam["artifact"]["samples"] == ["S"]
    assert bam["artifact"]["index"]["state"] == "ready"
    snps = _output(producer, "snps")["artifact"]
    assert snps["origin"] == "this_run" and snps["lifecycle"] == "final"
    assert snps["samples"] == ["S"]
    assert snps["index"] == {"path": f"{calls / 'snps.vcf.gz'}.tbi", "state": "ready"}
    assert snps["producer"]["run"] == run["id"]
    assert snps["reference"] == producer["provenance"]["reference"]["fingerprint"]
    assert [(s["path"], s["fingerprint"]) for s in snps["sources"]] == [
        (bam["path"], bam["fingerprint"])
    ]

    config = _clinvar_config(tmp_path, sample)
    analysis = tmp_path / "out" / "analysis"
    argv = _analyze(config, calls / "snps.vcf.gz", analysis)
    code, _, stderr = run_cli(argv)
    assert code == 0, stderr
    parent = _manifest(analysis / "analyze.manifest.json")
    # The input VCF links back to the run that called it.
    vcf_input = parent["inputs"][0]["artifact"]
    assert vcf_input["origin"] == "recorded"
    assert vcf_input["producer"]["run"] == run["id"]
    children = parent["run"]["children"]
    assert children and all(c["status"] == "ok" for c in children)
    annotate = next(c for c in children if c["command"] == "vcf chain-annotate")
    # A step done in this process names this run.
    assert {c["run"] for c in children if c["command"] == "analyze comprehensive"} == {
        parent["run"]["id"]
    }
    child = next(
        document
        for path in analysis.rglob("*.manifest.json")
        if (document := _manifest(path))["run"]["id"] == annotate["run"]
    )
    assert child["run"]["parent"] == child["run"]["root"] == parent["run"]["id"]
    assert child["run"]["step"] == annotate["step"]
    # The step's VCF, listed in the parent's manifest, names the step's run
    # and, through its source, the run that called the input.
    carried = [
        r["artifact"]
        for r in parent["outputs"]
        if r.get("artifact") and r["artifact"]["producer"]["run"] == child["run"]["id"]
    ]
    assert carried and carried[0]["sources"][0]["producer"] == run["id"]

    # Resumed: every step is reused and still names the run that did it.
    code, _, stderr = run_cli(argv)
    assert code == 0, stderr
    resumed = _manifest(analysis / "analyze.manifest.json")
    assert resumed["run"]["id"] != parent["run"]["id"]
    reused = {c["step"]: c for c in resumed["run"]["children"]}
    assert {c["status"] for c in reused.values()} == {"reused"}
    assert reused[annotate["step"]]["run"] == annotate["run"]
    assert reused[annotate["step"]]["reason"].startswith("complete")
    # Their files name the earlier runs that wrote them, never this one.
    written = [r["artifact"] for r in resumed["outputs"] if r.get("artifact")]
    assert written
    assert all(a["origin"] == "recorded" for a in written)
    assert {a["producer"]["run"] for a in written} <= {
        parent["run"]["id"],
        annotate["run"],
    }


@needs_htslib
def test_1182_resuming_a_journal_without_lineage_never_credits_the_resuming_run(
    tmp_path: Path,
) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    config = _clinvar_config(tmp_path, sample)
    analysis = tmp_path / "analysis"
    argv = _analyze(config, calls / "snps.vcf.gz", analysis)
    code, _, stderr = run_cli(argv)
    assert code == 0, stderr
    earlier = _manifest(analysis / "analyze.manifest.json")["run"]
    earlier_runs = {earlier["id"]} | {c["run"] for c in earlier["children"]}
    # A journal written before run lineage: no step names who wrote its files.
    for journal in analysis.glob("*.steps.json"):
        document = json.loads(journal.read_text())
        for step in document["steps"].values():
            for output in step.get("outputs") or []:
                output.pop("result_artifact", None)
                output.pop("result_producer", None)
        journal.write_text(json.dumps(document))

    code, _, stderr = run_cli(argv)
    assert code == 0, stderr
    resumed = _manifest(analysis / "analyze.manifest.json")
    assert {c["status"] for c in resumed["run"]["children"]} == {"reused"}
    written = [r["artifact"] for r in resumed["outputs"] if r.get("artifact")]
    assert written
    # Recovered from the earlier runs' manifests beside them, not this run.
    assert all(a["origin"] == "recorded" for a in written)
    assert {a["producer"]["run"] for a in written} <= earlier_runs


@needs_htslib
def test_1182_failed_child_run_is_attributed_in_both_manifests(
    tmp_path: Path,
) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    config = _clinvar_config(tmp_path, sample)
    clinvar = tmp_path / "clinvar.vcf.gz"
    clinvar.write_bytes(gzip.compress(b"not a vcf\n"))  # the annotation fails
    analysis = tmp_path / "analysis"
    code, _, _ = run_cli(_analyze(config, calls / "snps.vcf.gz", analysis))
    assert code == 1
    parent = _manifest(analysis / "analyze.manifest.json")
    failed = [c for c in parent["run"]["children"] if c["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["run"]
    # chain-annotate, the step's command, ran vcf clinvar, which failed.
    assert failed[0]["command"] == "vcf chain-annotate"
    clinvar = _manifest(next(analysis.rglob("*.failed.manifest.json")))
    assert clinvar["status"] == "failed"
    assert clinvar["run"]["parent"] == failed[0]["run"]
    assert clinvar["run"]["root"] == parent["run"]["id"]
    assert clinvar["run"]["step"] == "clinvar"


@needs_htslib
def test_1182_merge_keeps_discarded_parts_as_temp_sources_not_outputs(
    tmp_path: Path,
) -> None:
    sample = snp_sample(tmp_path / "in")
    out = tmp_path / "out"
    code, _, stderr = run_cli([
        "vcf", "call", "--types", "snp,indel", "--merge", "--input", sample.bam,
        "--ref", sample.ref, "--outdir", str(out), "--ploidy", "2", "--threads", "1",
    ])  # fmt: skip
    assert code == 0, stderr
    document = _manifest(out / "variants.vcf.manifest.json")
    assert not (out / "snps.vcf.gz").exists()
    assert {r["role"] for r in document["outputs"] if r.get("artifact")} == {
        "merged_variants"
    }
    merged = _output(document, "merged_variants")["artifact"]
    assert merged["lifecycle"] == "final"
    sources = {Path(s["path"]).name: s for s in merged["sources"]}
    assert set(sources) == {"snps.vcf.gz", "indels.vcf.gz", "s.bam"}
    parts = {name: s for name, s in sources.items() if name.endswith(".vcf.gz")}
    assert all(
        s["lifecycle"] == "temp" and not s["retained"] and s["fingerprint"]
        for s in parts.values()
    )
    assert {s["producer"] for s in parts.values()} == {document["run"]["id"]}


def _bam(path: Path, chr1: int, sample: str = "S", contigs: int = 1) -> str:
    lines = ["@HD\tVN:1.6\tSO:coordinate", f"@SQ\tSN:chr1\tLN:{chr1}"]
    lines += [f"@SQ\tSN:chr{n}\tLN:1000" for n in range(2, contigs + 1)]
    lines += [f"@RG\tID:rg\tSM:{sample}"]
    return sam_to_bam(lines, path)


def _error_code(stdout: str) -> str:
    return str(json.loads(stdout)["error"]["code"])


@needs_htslib
@pytest.mark.parametrize(
    ("bam_chr1", "fai_chr1", "code"),
    [(HG38_CHR1, HG19_CHR1, "assembly_mismatch"), (3000, 2999, "reference_mismatch")],
)
def test_1182_caller_refuses_another_assembly_or_reference_before_calling(
    tmp_path: Path, bam_chr1: int, fai_chr1: int, code: str
) -> None:
    bam = _bam(tmp_path / "s.bam", bam_chr1)
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 3000})
    Path(f"{ref}.fai").write_text(f"chr1\t{fai_chr1}\t6\t3000\t3001\n")
    exit_code, stdout, _ = run_cli([
        "--json", "vcf", "snp", "--input", bam, "--ref", str(ref), "--ploidy", "2",
        "--outdir", str(tmp_path / "out"),
    ])  # fmt: skip
    assert exit_code == 1
    assert _error_code(stdout) == code
    assert not list((tmp_path / "out").glob("snps.vcf.gz*"))


@needs_htslib
@pytest.mark.parametrize("problem", ["stale", "mismatched", "missing"])
def test_1182_caller_refuses_an_unusable_bam_index_before_calling(
    tmp_path: Path, problem: str
) -> None:
    sample = snp_sample(tmp_path / "in")
    bam = Path(sample.bam)
    index = Path(f"{bam}.bai")
    extra: list[str] = []
    if problem == "stale":
        # Rewritten (recompressed) after it was indexed: the old index points
        # into blocks the BAM no longer has.
        rewritten = tmp_path / "rewritten.bam"
        run_command(["samtools", "view", "-u", "-o", str(rewritten), str(bam)])
        os.replace(rewritten, bam)
        os.utime(bam, (index.stat().st_mtime + 10,) * 2)
    elif problem == "mismatched":
        # The index of another BAM, with another sequence dictionary.
        other = _bam(tmp_path / "other.bam", 3000, contigs=2)
        shutil.copyfile(f"{other}.bai", index)
    else:
        index.unlink()
        extra = ["--region", "chr1:500-2500"]  # a region is read through it
    code, stdout, _ = run_cli([
        "--json", "vcf", "snp", "--input", str(bam), "--ref", sample.ref,
        "--ploidy", "2", "--outdir", str(tmp_path / "out"), *extra,
    ])  # fmt: skip
    assert code == 1
    expected = {"stale": "index_stale", "mismatched": "index_mismatch"}
    assert _error_code(stdout) == expected.get(problem, "index_missing")
    assert not (tmp_path / "out" / "snps.vcf.gz").exists()


@needs_htslib
def test_1182_an_index_older_than_its_downloaded_bam_is_still_used(
    tmp_path: Path,
) -> None:
    # Downloads and copies often leave the index older than the BAM; its
    # blocks are still the BAM's, so it is not stale.
    sample = snp_sample(tmp_path / "in")
    index = Path(f"{sample.bam}.bai")
    os.utime(sample.bam, (index.stat().st_mtime + 10,) * 2)
    code, stderr = _call_snps(sample, tmp_path / "out", "--region", "chr1:500-2500")
    assert code == 0, stderr
    document = _manifest(tmp_path / "out" / "snps.vcf.manifest.json")
    assert document["inputs"][0]["artifact"]["index"]["state"] == "ready"


@needs_htslib
def test_1182_unindexed_bam_still_calls_the_whole_file(tmp_path: Path) -> None:
    sample = snp_sample(tmp_path / "in")
    Path(f"{sample.bam}.bai").unlink()
    code, stderr = _call_snps(sample, tmp_path / "out")
    assert code == 0, stderr
    document = _manifest(tmp_path / "out" / "snps.vcf.manifest.json")
    assert document["inputs"][0]["artifact"]["index"] == {
        "path": None,
        "state": "missing",
    }


@needs_htslib
def test_1182_analyze_refuses_another_persons_calls_before_any_step(
    tmp_path: Path,
) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    analysis = tmp_path / "analysis"
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--input", other,
        "--vcf-inputs", str(calls / "snps.vcf.gz"), "--outdir", str(analysis),
    ])  # fmt: skip
    assert code == 1
    assert _error_code(stdout) == "sample_mismatch"
    assert not (analysis / "analyze.steps.json").exists()


@needs_htslib
def test_1182_analyze_refuses_a_vcf_index_of_another_file(tmp_path: Path) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    plain = tmp_path / "other.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chrX,length=3000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchrX\t5\t.\tA\tG\t.\t.\t.\n"
    )
    other = bgzip_index(plain)
    shutil.copyfile(f"{other}.tbi", calls / "snps.vcf.gz.tbi")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive",
        "--vcf-inputs", str(calls / "snps.vcf.gz"), "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 1
    assert _error_code(stdout) == "index_mismatch"


@needs_htslib
def test_1182_api_result_carries_the_run_and_artifacts_of_its_manifest(
    tmp_path: Path,
) -> None:
    from wgsextract_cli import api
    from wgsextract_cli.api.vcf import VcfSnpParams

    sample = snp_sample(tmp_path / "in")
    out = tmp_path / "out"
    result = api.vcf.snp(
        VcfSnpParams(
            input=Path(sample.bam), ref=Path(sample.ref), outdir=out,
            ploidy="2", threads=1,
        )
    )  # fmt: skip
    document = json.loads(result.to_json())
    Draft202012Validator(load_schema()).validate(document)
    snps = _output(document, "snps")["artifact"]
    assert snps["producer"]["run"] == result.run.id == result.run.root
    assert snps["index"]["state"] == "ready"
    assert _manifest(out / "snps.vcf.manifest.json")["run"] == document["run"]


@needs_htslib
def test_1182_bcf_index_refusal_names_the_bcftools_fix(tmp_path: Path) -> None:
    def bcf(name: str, *contigs: str) -> Path:
        plain = tmp_path / f"{name}.vcf"
        plain.write_text(
            "##fileformat=VCFv4.2\n"
            + "".join(f"##contig=<ID={c},length=3000>\n" for c in contigs)
            + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            + "".join(f"{c}\t5\t.\tA\tG\t.\t.\t.\n" for c in contigs)
        )
        out = tmp_path / f"{name}.bcf"
        run_command(["bcftools", "view", "-Ob", "-o", str(out), str(plain)])
        run_command(["bcftools", "index", str(out)])
        return out

    calls, other = bcf("calls", "chr1"), bcf("other", "chr1", "chrX")
    shutil.copyfile(f"{other}.csi", f"{calls}.csi")  # another BCF's index
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--vcf-inputs", str(calls),
        "--outdir", str(tmp_path / "out"),
    ])  # fmt: skip
    assert code == 1
    error = json.loads(stdout)["error"]
    assert error["code"] == "index_mismatch"
    # Review of #1372: tabix can't index a BCF.
    assert f"bcftools index {calls}" in error["message"]


@needs_htslib
def test_1182_vcf_contigs_declared_without_length_keep_their_index(
    tmp_path: Path,
) -> None:
    # bcftools declares a contig it meets without a header line as
    # ##contig=<ID=chrM>; the index naming it is still the VCF's own.
    plain = tmp_path / "calls.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=3000>\n##contig=<ID=chrM>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t5\t.\tA\tG\t.\t.\t.\nchrM\t5\t.\tA\tG\t.\t.\t.\n"
    )
    vcf = bgzip_index(plain)
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--dry-run", "--vcf-inputs", vcf,
        "--outdir", str(tmp_path / "out"),
    ])  # fmt: skip
    assert code == 0, stdout
    assert json.loads(stdout)["inputs"][0]["artifact"]["index"]["state"] == "ready"


@needs_htslib
def test_1182_a_damaged_index_is_refused_not_a_crash(tmp_path: Path) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    index = calls / "snps.vcf.gz.tbi"
    data = bytearray(index.read_bytes())
    data[20:40] = b"\xff" * 20  # a partial download's garbage
    index.write_bytes(bytes(data))
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive",
        "--vcf-inputs", str(calls / "snps.vcf.gz"), "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 1
    assert _error_code(stdout) == "index_mismatch"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_1182_a_piped_vcf_input_is_never_opened_for_its_artifact(
    tmp_path: Path,
) -> None:
    from wgsextract_cli.core.results import CommandResult

    fifo = tmp_path / "calls.vcf.gz"
    os.mkfifo(fifo)
    result = CommandResult(command="vcf filter")
    result.add_input(fifo, "vcf_input")
    # Opening the pipe would block: fail instead of hanging the suite.
    finalize = threading.Thread(
        target=result.finalize, kwargs={"probe_tools": False}, daemon=True
    )
    finalize.start()
    finalize.join(30)
    assert not finalize.is_alive()
    assert json.loads(result.to_json())["inputs"][0].get("artifact") is None


def _other_vcf(tmp_path: Path) -> str:
    plain = tmp_path / "other.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chrX,length=3000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchrX\t5\t.\tA\tG\t.\t.\t.\n"
    )
    vcf = bgzip_index(plain)
    run_command(["tabix", "-C", "-p", "vcf", vcf])
    return vcf


@needs_htslib
@pytest.mark.parametrize("bad", ["csi", "garbage"])
def test_1182_every_index_a_reader_may_pick_is_checked(
    tmp_path: Path, bad: str
) -> None:
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    vcf = calls / "snps.vcf.gz"
    if bad == "csi":
        # Review of #1372: htslib picks a CSI before the valid TBI beside it.
        shutil.copyfile(f"{_other_vcf(tmp_path)}.csi", f"{vcf}.csi")
        index = f"{vcf}.csi"
    else:
        # Review of #1372: a readable gzip wrapper around no index format.
        index = f"{vcf}.tbi"
        Path(index).write_bytes(gzip.compress(b"not an index"))
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--vcf-inputs", str(vcf),
        "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 1
    error = json.loads(stdout)["error"]
    assert error["code"] == "index_mismatch"
    assert index in error["message"]


@needs_htslib
def test_1182_vcf_call_outputs_share_one_manifest_but_keep_their_producer(
    tmp_path: Path,
) -> None:
    # Review of #1372: indels.vcf.gz is listed in snps.vcf.manifest.json, so
    # another person's reads must still refuse it rather than only warn.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    code, _, stderr = run_cli([
        "vcf", "call", "--types", "snp,indel", "--input", sample.bam,
        "--ref", sample.ref, "--outdir", str(calls), "--ploidy", "2",
        "--threads", "1",
    ])  # fmt: skip
    assert code == 0, stderr
    assert not (calls / "indels.vcf.manifest.json").exists()
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--input", other,
        "--vcf-inputs", str(calls / "indels.vcf.gz"),
        "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 1
    assert _error_code(stdout) == "sample_mismatch"


@needs_htslib
def test_1182_a_filtered_vcf_naming_another_sample_only_warns(tmp_path: Path) -> None:
    # Review of #1372: a VCF filtered from calls keeps their sample name but
    # wasn't called from reads here, so a name mismatch only warns.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    filtered = tmp_path / "filtered"
    code, _, stderr = run_cli([
        "vcf", "filter", "--vcf-input", str(calls / "snps.vcf.gz"),
        "--expr", "QUAL>0", "--outdir", str(filtered),
    ])  # fmt: skip
    assert code == 0, stderr
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--dry-run", "--input", other,
        "--vcf-inputs", str(filtered / "filtered.vcf.gz"),
        "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 0, stdout
    document = json.loads(stdout)
    assert "sample_mismatch" in {w["code"] for w in document["warnings"]}


@needs_htslib
def test_1182_bcf_contigs_declared_without_length_keep_their_index(
    tmp_path: Path,
) -> None:
    # Review of #1372: a BCF's CSI counts the lengthless ##contig=<ID=chrM> too.
    plain = tmp_path / "calls.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=3000>\n##contig=<ID=chrM>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t5\t.\tA\tG\t.\t.\t.\nchrM\t5\t.\tA\tG\t.\t.\t.\n"
    )
    bcf = tmp_path / "calls.bcf"
    run_command(["bcftools", "view", "-Ob", "-o", str(bcf), str(plain)])
    run_command(["bcftools", "index", str(bcf)])
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--dry-run", "--vcf-inputs", str(bcf),
        "--outdir", str(tmp_path / "out"),
    ])  # fmt: skip
    assert code == 0, stdout
    assert json.loads(stdout)["inputs"][0]["artifact"]["index"]["state"] == "ready"


@needs_htslib
def test_1182_a_server_job_never_reads_a_manifest_outside_its_folders(
    tmp_path: Path,
) -> None:
    # Review of #1372: the manifest beside an input may link elsewhere.
    from wgsextract_cli.core import read_scope
    from wgsextract_cli.core.results import CommandResult

    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    vcf = allowed / "snps.vcf.gz"
    shutil.copyfile(calls / "snps.vcf.gz", vcf)
    shutil.copyfile(calls / "snps.vcf.gz.tbi", f"{vcf}.tbi")
    manifest = json.loads((calls / "snps.vcf.manifest.json").read_text())
    for record in manifest["outputs"]:
        record["path"] = record["path"].replace(str(calls), str(allowed))
    outside = tmp_path / "outside.manifest.json"
    outside.write_text(json.dumps(manifest))
    (allowed / "snps.vcf.manifest.json").symlink_to(outside)

    def origin(scope: Any) -> str:
        result = CommandResult(command="vcf filter")
        with scope:
            result.add_input(vcf, "vcf_input")
            result.finalize(probe_tools=False)
        return str(json.loads(result.to_json())["inputs"][0]["artifact"]["origin"])

    resolved = allowed.resolve()
    inside = read_scope.limited_to(lambda p: Path(p).resolve().is_relative_to(resolved))
    assert origin(contextlib.nullcontext()) == "recorded"
    assert origin(inside) == "external"


@needs_htslib
def test_1182_merged_calls_of_another_person_are_refused(tmp_path: Path) -> None:
    # Review of #1372: the merge is called from the reads, through its parts.
    sample = snp_sample(tmp_path / "in")
    out = tmp_path / "out"
    code, _, stderr = run_cli([
        "vcf", "call", "--types", "snp,indel", "--merge", "--input", sample.bam,
        "--ref", sample.ref, "--outdir", str(out), "--ploidy", "2", "--threads", "1",
    ])  # fmt: skip
    assert code == 0, stderr
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--input", other,
        "--vcf-inputs", str(out / "variants.vcf.gz"), "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 1
    assert _error_code(stdout) == "sample_mismatch"


@needs_htslib
def test_1182_a_cram_with_a_plain_text_crai_is_still_called(tmp_path: Path) -> None:
    # Review of #1372: an uncompressed .crai is a usable CRAM index.
    sample = snp_sample(tmp_path / "in")
    crai = Path(f"{sample.cram}.crai")
    crai.write_bytes(gzip.decompress(crai.read_bytes()))
    code, _, stderr = run_cli([
        "vcf", "snp", "--input", sample.cram, "--ref", sample.ref, "--ploidy", "2",
        "--threads", "1", "--region", "chr1:500-2500", "--outdir", str(tmp_path / "out"),
    ])  # fmt: skip
    assert code == 0, stderr
