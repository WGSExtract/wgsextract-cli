"""#1379: run lineage pilot follow-ups. Server read scope in subprocesses
and index sidecars, index parser bounds, calling ancestry through VCF
transformations, cancelled steps, chain-annotate's annotator runs and
non-regular manifest candidates."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import struct
import threading
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.htslib_helpers import needs_htslib, snp_sample
from tests.smoke_utils import run_cli
from tests.test_artifact_lineage import (
    _bam,
    _call_snps,
    _calls_with_manifest_outside,
    _clinvar_config,
    _error_code,
    _manifest,
    _other_vcf,
    _output,
)
from wgsextract_cli.core.results import load_schema
from wgsextract_cli.core.utils import run_command


def _filter(vcf: Path, outdir: Path) -> Path:
    code, _, stderr = run_cli([
        "vcf", "filter", "--vcf-input", str(vcf), "--expr", "QUAL>0",
        "--outdir", str(outdir),
    ])  # fmt: skip
    assert code == 0, stderr
    return outdir / "filtered.vcf.gz"


@needs_htslib
@pytest.mark.parametrize("called_here", [True, False])
def test_1379_filtered_calls_of_another_person_are_refused_like_the_calls(
    tmp_path: Path, called_here: bool
) -> None:
    # #1379: a VCF filtered from wgsextract's calls keeps the reads' sample,
    # so another person's reads are refused, not only warned about. Filtered
    # from a VCF nobody recorded, a name mismatch still only warns.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    vcf = calls / "snps.vcf.gz"
    if not called_here:
        (calls / "snps.vcf.manifest.json").unlink()
    filtered = _filter(vcf, tmp_path / "filtered")
    artifact = _output(_manifest(filtered.parent / "filtered.vcf.manifest.json"),
                       "filtered_vcf")["artifact"]  # fmt: skip
    assert [Path(r["path"]).name for r in artifact["called_from"]] == (
        ["s.bam"] if called_here else []
    )
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--dry-run", "--input", other,
        "--vcf-inputs", str(filtered), "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    document = json.loads(stdout)
    if called_here:
        assert code == 1
        assert document["error"]["code"] == "sample_mismatch"
    else:
        assert code == 0, stdout
        assert "sample_mismatch" in {w["code"] for w in document["warnings"]}


@needs_htslib
def test_1379_renamed_samples_lose_their_calling_ancestry(tmp_path: Path) -> None:
    # #1379: only a transformation that keeps the calls' samples carries the
    # reads they were called from; a renamed sample may be anyone's.
    from wgsextract_cli.core.results import CommandResult

    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    vcf = calls / "snps.vcf.gz"
    names = tmp_path / "names.txt"
    names.write_text("NA12878\n")
    renamed = tmp_path / "renamed.vcf.gz"
    run_command(
        ["bcftools", "reheader", "-s", str(names), "-o", str(renamed), str(vcf)]
    )
    kept = tmp_path / "kept.vcf.gz"
    run_command(["bcftools", "view", "-Oz", "-o", str(kept), str(vcf)])
    result = CommandResult(command="vcf filter")
    result.add_input(vcf, "vcf_input")
    result.add_output(renamed, "renamed_vcf")
    result.add_output(kept, "kept_vcf")
    result.finalize(probe_tools=False)
    document = json.loads(result.to_json())
    assert [Path(r["path"]).name for r in
            _output(document, "kept_vcf")["artifact"]["called_from"]] == ["s.bam"]  # fmt: skip
    assert _output(document, "renamed_vcf")["artifact"]["called_from"] == []


@needs_htslib
def test_1379_a_server_jobs_steps_never_read_a_manifest_outside_its_folders(
    tmp_path: Path,
) -> None:
    # #1379: analyze's steps run as subprocesses, which kept no read scope
    # and followed the input's manifest link out of the server's folders.
    from wgsextract_cli.server.jobs import JobManager
    from wgsextract_cli.server.operations import find

    sample = snp_sample(tmp_path / "in")
    allowed = tmp_path / "allowed"
    vcf = _calls_with_manifest_outside(tmp_path, sample, allowed)
    config = _clinvar_config(allowed, sample)
    analysis = allowed / "analysis"
    manager = JobManager(tmp_path / "jobs", config_path=config, allowed=[allowed])
    try:
        job = manager.submit(
            find("analyze/comprehensive"),
            {"vcf_inputs": [str(vcf)], "outdir": str(analysis), "threads": 1},
        )
        done = manager.wait(job.id, 600)
    finally:
        manager.shutdown()
    assert done is not None and done.status == "succeeded", done
    documents = [_manifest(p) for p in analysis.rglob("*.manifest.json")]
    steps = [d for d in documents if d["run"]["parent"]]
    assert {d["command"] for d in steps} >= {"vcf chain-annotate"}
    for document in steps:
        for record in document["inputs"]:
            if record["path"] == str(vcf):
                assert record["artifact"]["origin"] == "external"
                assert record["artifact"]["producer"] is None


@needs_htslib
def test_1379_an_index_link_out_of_the_read_scope_is_not_inspected(
    tmp_path: Path,
) -> None:
    # #1379: a .csi beside the input linking out of a server job's folders
    # (here another file's index) was opened to judge the input's index.
    from wgsextract_cli.core import read_scope
    from wgsextract_cli.core.results import CommandResult

    sample = snp_sample(tmp_path / "in")
    allowed = tmp_path / "allowed"
    vcf = _calls_with_manifest_outside(tmp_path, sample, allowed)
    Path(f"{vcf}.csi").symlink_to(f"{_other_vcf(tmp_path)}.csi")

    def index(scope: Any) -> dict[str, Any]:
        result = CommandResult(command="vcf filter")
        with scope:
            result.add_input(vcf, "vcf_input")
            result.finalize(probe_tools=False)
        return dict(json.loads(result.to_json())["inputs"][0]["artifact"]["index"])

    assert index(contextlib.nullcontext())["state"] == "mismatched"
    assert index(read_scope.limited_to_folders([allowed])) == {
        "path": f"{vcf}.tbi",
        "state": "ready",
    }


def _negative_bin_count(index: Path) -> None:
    """Rewrite the first reference's bin count as -1."""
    data = index.read_bytes()
    bai = data.startswith(b"BAI\x01")
    raw = bytearray(data if bai else gzip.decompress(data))
    at = 8  # magic, n_ref
    if not bai:
        (names,) = struct.unpack_from("<i", raw, 32)
        at = 36 + names
    struct.pack_into("<i", raw, at, -1)
    index.write_bytes(bytes(raw) if bai else gzip.compress(bytes(raw)))


@needs_htslib
@pytest.mark.parametrize("kind", ["bai", "tbi"])
def test_1379_an_index_with_a_negative_count_is_refused(
    tmp_path: Path, kind: str
) -> None:
    # #1379: a crafted index with n_bin = -1 read as ready.
    sample = snp_sample(tmp_path / "in")
    if kind == "bai":
        _negative_bin_count(Path(f"{sample.bam}.bai"))
        argv = ["vcf", "snp", "--input", sample.bam, "--ref", sample.ref,
                "--ploidy", "2", "--region", "chr1:500-2500"]  # fmt: skip
    else:
        calls = tmp_path / "calls"
        assert _call_snps(sample, calls)[0] == 0
        _negative_bin_count(calls / "snps.vcf.gz.tbi")
        argv = ["analyze", "comprehensive", "--vcf-inputs",
                str(calls / "snps.vcf.gz")]  # fmt: skip
    code, stdout, _ = run_cli(["--json", *argv, "--outdir", str(tmp_path / "out")])
    assert code == 1
    assert _error_code(stdout) == "index_mismatch"


@needs_htslib
def test_1379_chain_annotate_lists_its_annotators_runs(tmp_path: Path) -> None:
    # #1379: the annotators' manifests go with the intermediates, so only
    # chain-annotate's own manifest can name their runs.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    config = _clinvar_config(tmp_path, sample)
    out = tmp_path / "out"
    code, _, stderr = run_cli([
        "--config", str(config), "vcf", "chain-annotate", "--annotations", "clinvar",
        "--input", str(calls / "snps.vcf.gz"), "--outdir", str(out),
    ])  # fmt: skip
    assert code == 0, stderr
    assert not list(out.glob("chain_step_*"))
    document = _manifest(out / "chain_annotated.vcf.manifest.json")
    [child] = document["run"]["children"]
    assert child["step"] == "clinvar" and child["command"] == "vcf clinvar"
    assert child["status"] == "ok" and child["run"]
    assert child["run"] != document["run"]["id"]
    # Annotated from the calls: still the reads' calls.
    annotated = _output(document, "annotated_vcf")["artifact"]
    assert [Path(r["path"]).name for r in annotated["called_from"]] == ["s.bam"]


@needs_htslib
def test_1379_a_cancelled_analysis_lists_its_interrupted_step_as_cancelled(
    tmp_path: Path,
) -> None:
    # #1379: a step interrupted before its command left a result was listed
    # as failed, though the run itself was cancelled.
    from wgsextract_cli import api
    from wgsextract_cli.api.analyze import AnalyzeComprehensiveParams

    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    config = _clinvar_config(tmp_path, sample)
    token = api.CancelToken()

    def cancel_as_a_step_starts(event: Any) -> None:
        if event.stage == "step":
            token.cancel()  # before the step's command starts

    with api.scoped_config(config):
        result = api.analyze.comprehensive(
            AnalyzeComprehensiveParams(
                vcf_inputs=(calls / "snps.vcf.gz",), outdir=tmp_path / "a", threads=1
            ),
            progress=cancel_as_a_step_starts,
            cancel=token,
            check=False,
        )
    assert result.status == "cancelled"
    [step] = result.run.children
    assert step.step.startswith("annotate:") and step.status == "cancelled"
    # Its command never started: no run did the step's work, this one included.
    assert (step.run, step.command) == (None, "vcf chain-annotate")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_1379_a_pipe_named_like_a_manifest_never_blocks_the_result(
    tmp_path: Path,
) -> None:
    # #1379: the producer lookup opened every *.manifest.json beside an input.
    from wgsextract_cli.core.results import CommandResult

    vcf = tmp_path / "calls.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    os.mkfifo(tmp_path / "calls.vcf.manifest.json")
    os.mkfifo(tmp_path / "other.manifest.json")
    result = CommandResult(command="vcf filter")
    result.add_input(vcf, "vcf_input")
    finalize = threading.Thread(
        target=result.finalize, kwargs={"probe_tools": False}, daemon=True
    )
    finalize.start()
    finalize.join(30)
    assert not finalize.is_alive()
    assert json.loads(result.to_json())["inputs"][0]["artifact"]["origin"] == "external"


@needs_htslib
def test_1379_reads_vouch_only_for_vcfs_named_by_their_sample(tmp_path: Path) -> None:
    # Review of #1427: `vcf phase` writes the reads' phasing into another
    # VCF's sample; that sample name isn't the reads' to vouch for.
    from wgsextract_cli.core.results import CommandResult

    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    (calls / "snps.vcf.manifest.json").unlink()  # an external VCF
    vcf = calls / "snps.vcf.gz"
    names = tmp_path / "names.txt"
    names.write_text("lab-id-7\n")
    lab = tmp_path / "lab.vcf.gz"
    run_command(["bcftools", "reheader", "-s", str(names), "-o", str(lab), str(vcf)])
    run_command(["tabix", "-p", "vcf", str(lab)])
    phased = tmp_path / "phased.vcf.gz"
    run_command(["bcftools", "view", "-Oz", "-o", str(phased), str(lab)])
    named = tmp_path / "named.vcf.gz"
    run_command(["bcftools", "view", "-Oz", "-o", str(named), str(vcf)])
    result = CommandResult(command="vcf phase")
    result.add_input(sample.bam, "input")
    result.add_input(lab, "vcf_input")
    result.add_output(phased, "phased_vcf")
    result.add_output(named, "named_vcf")
    result.finalize(probe_tools=False)
    document = json.loads(result.to_json())
    assert _output(document, "phased_vcf")["artifact"]["called_from"] == []
    called = _output(document, "named_vcf")["artifact"]["called_from"]
    assert [Path(r["path"]).name for r in called] == ["s.bam"]


@needs_htslib
def test_1379_a_malformed_manifest_beside_calls_only_loses_their_producer(
    tmp_path: Path,
) -> None:
    # Review of #1427: a manifest whose outputs isn't a list crashed the
    # producer lookup, and with it analyze's sample check.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    (calls / "snps.vcf.manifest.json").write_text(
        json.dumps({"schema": "wgsextract.result/1", "outputs": 1})
    )
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--dry-run", "--input", other,
        "--vcf-inputs", str(calls / "snps.vcf.gz"), "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 0, stdout
    document = json.loads(stdout)
    assert "sample_mismatch" in {w["code"] for w in document["warnings"]}
    assert document["inputs"][1]["artifact"]["origin"] == "external"


@needs_htslib
def test_1379_an_external_vcf_annotated_from_recorded_calls_stays_external(
    tmp_path: Path,
) -> None:
    # Review of #1427: annotating copies IDs, not genotypes, from --ann-vcf;
    # the output's sample is still the external input's.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    external = tmp_path / "external"
    external.mkdir()
    for name in ("snps.vcf.gz", "snps.vcf.gz.tbi"):
        (external / name).write_bytes((calls / name).read_bytes())
    out = tmp_path / "out"
    code, _, stderr = run_cli([
        "vcf", "annotate", "--vcf-input", str(external / "snps.vcf.gz"),
        "--ann-vcf", str(calls / "snps.vcf.gz"), "--cols", "ID", "--outdir", str(out),
    ])  # fmt: skip
    assert code == 0, stderr
    manifest = next(out.glob("*.manifest.json"))
    annotated = _output(_manifest(manifest), "annotated")["artifact"]
    assert annotated["samples"] == ["S"] and annotated["called_from"] == []


@needs_htslib
@pytest.mark.parametrize(
    ("field", "value", "origin"),
    [
        (
            "producer",
            {"run": 123, "root": None, "command": "x", "step": None},
            "external",
        ),
        ("producer", None, "external"),  # its history explicitly unknown
        ("called_from", [{"path": None, "role": []}], "recorded"),
    ],
)
def test_1379_a_neighbouring_manifest_is_trusted_only_when_well_formed(
    tmp_path: Path, field: str, value: Any, origin: str
) -> None:
    # Review of #1427: malformed producers and ancestry were copied into the
    # next result (breaking its schema) or turned a warning into a refusal,
    # and a producer recorded as unknown credited the manifest's own run.
    sample = snp_sample(tmp_path / "in")
    calls = tmp_path / "calls"
    assert _call_snps(sample, calls)[0] == 0
    path = calls / "snps.vcf.manifest.json"
    document = json.loads(path.read_text())
    _output(document, "snps")["artifact"][field] = value
    path.write_text(json.dumps(document))
    other = _bam(tmp_path / "other.bam", 3000, sample="NA12878")
    code, stdout, _ = run_cli([
        "--json", "analyze", "comprehensive", "--dry-run", "--input", other,
        "--vcf-inputs", str(calls / "snps.vcf.gz"), "--outdir", str(tmp_path / "a"),
    ])  # fmt: skip
    assert code == 0, stdout
    result = json.loads(stdout)
    Draft202012Validator(load_schema()).validate(result)
    assert "sample_mismatch" in {w["code"] for w in result["warnings"]}
    vcf = next(r for r in result["inputs"] if r["role"] == "vcf_input")["artifact"]
    assert vcf["origin"] == origin and vcf["called_from"] == []
