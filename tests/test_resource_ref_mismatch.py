"""Issue #1325: a resource's stale REF rows are dropped, not fatal."""

import errno
import gzip
import json
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from itertools import chain
from pathlib import Path
from threading import Event, current_thread
from typing import Any

import pytest

from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import needs_tools, reference_for_vcf, run_wgsextract
from tests.test_vcf_annotations import (
    REVEL_HEADER,
    SAMPLE_ROWS,
    _bgzip,
    _query,
    _sample,
    _tsv,
    _vcf,
)
from wgsextract_cli.api.vcf import (
    VcfCaddParams,
    VcfChainAnnotateParams,
    VcfClinvarParams,
    VcfRevelParams,
    cadd,
    chain_annotate,
    clinvar,
    revel,
)
from wgsextract_cli.commands.vcf.matching import normalized_inputs
from wgsextract_cli.core import utils
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.main import main

pytestmark = needs_tools("samtools", "bcftools", "bgzip", "tabix")
EXCLUDED = "annotation_ref_mismatch_excluded"


@pytest.mark.parametrize(
    ("is_table", "interrupt", "source_error"),
    [(False, False, False), (True, True, False), (False, False, True)],
    ids=["vcf-downstream-failure", "table-interrupt", "vcf-source-error"],
)
def test_1585_excluded_contig_tail_does_not_delay_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_table: bool,
    interrupt: bool,
    source_error: bool,
) -> None:
    sample = _sample(tmp_path, SAMPLE_ROWS, length=1000)
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    if is_table:
        resource = _tsv(
            tmp_path / "scores.tsv.gz", "#CHROM\tPOS\tREF\tALT", ["1 100 C T"]
        )
        columns = "CHROM,POS,REF,ALT"
        excluded = "other\t100\tC\tT\n"
    else:
        resource = _vcf(
            tmp_path / "scores.vcf.gz", ["100 . C T . . . GT 0/1"],
            contig="1", length=1000,
        )  # fmt: skip
        columns = ""
        excluded = "other\t100\t.\tC\tT\t.\t.\t.\tGT\t0/1\n"

    scanning_tail, release_tail = Event(), Event()
    fail_source, source_failed = Event(), Event()
    real_open, real_popen = gzip.open, utils.popen
    children: list[subprocess.Popen[bytes]] = []

    def tail() -> Iterator[str]:
        # Never reach EOF until test cleanup; none of these rows writes to bcftools.
        yield excluded
        scanning_tail.set()
        while not release_tail.is_set():
            if fail_source.is_set():
                source_failed.set()
                raise OSError(errno.EINVAL, "resource read failed")
            yield excluded

    @contextmanager
    def resource_stream() -> Iterator[Iterator[str]]:
        with real_open(resource, "rt") as handle:
            yield chain(handle, tail())

    def open_with_tail(
        path: str | Path, mode: str = "rb", *args: Any, **kwargs: Any
    ) -> AbstractContextManager[object]:
        if path == resource and mode == "rt":
            return resource_stream()
        return real_open(path, mode, *args, **kwargs)

    def start_consumer(cmd: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if not kwargs.get("stdin"):
            return real_popen(cmd, **kwargs)
        consumer = cmd[:2] == ["bcftools", "norm"]
        if consumer:
            assert scanning_tail.wait(5)
            if not interrupt:
                cmd = [*cmd, "--invalid-annotation-test-option"]
        child = real_popen(cmd, **kwargs)
        children.append(child)
        if consumer and source_error:
            child.wait(timeout=5)
            fail_source.set()
            assert source_failed.wait(5)
        if consumer and interrupt:
            wait, caller = child.wait, current_thread()
            interrupted = False

            def interrupted_wait(timeout: float | None = None) -> int:
                nonlocal interrupted
                if current_thread() is caller and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                return wait(timeout)

            monkeypatch.setattr(child, "wait", interrupted_wait)
        return child

    monkeypatch.setattr(gzip, "open", open_with_tail)
    monkeypatch.setattr(utils, "popen", start_consumer)
    work = tmp_path / "work"
    work.mkdir()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            normalized_inputs, sample, resource, str(ref), str(work), False,
            columns=columns,
        )  # fmt: skip
        try:
            if interrupt:
                with pytest.raises(KeyboardInterrupt):
                    result.result(timeout=8)
            elif source_error:
                with pytest.raises(OSError, match="resource read failed") as error:
                    result.result(timeout=8)
                assert error.value.errno == errno.EINVAL
            else:
                with pytest.raises(WGSExtractError, match="bcftools norm"):
                    result.result(timeout=8)
            assert len(children) == (1 if is_table else 2)
            assert all(child.returncode is not None for child in children)
        finally:
            release_tail.set()


@pytest.mark.parametrize("bcftools_summary", [True, False])
def test_1325_stale_revel_ref_row_is_dropped_not_fatal(
    tmp_path, monkeypatch, bcftools_summary
):
    if not bcftools_summary:
        # PR #1343 review: bcftools < 1.22 doesn't report the records `norm -c x`
        # removes; nor may the count depend on what its stderr says.
        from wgsextract_cli.commands.vcf import matching

        real_run = matching.run_command

        def without_summary(cmd, **kwargs):
            result = real_run(cmd, **kwargs)
            if isinstance(result.stderr, str):
                result.stderr = ""
            return result

        monkeypatch.setattr(matching, "run_command", without_summary)
    sample = _sample(tmp_path, SAMPLE_ROWS, length=1000)
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    # ANNOVAR's REVEL keeps hg19 REF bases: at 200 the FASTA (and sample) has A.
    # The other rows show the table is for this build.
    table = _tsv(tmp_path / "revel_hg38.tsv.gz", REVEL_HEADER,
                 ["1 100 100 C T 0.812", "1 200 200 T G 0.5",
                  *(f"1 {p} {p} A C 0.1" for p in range(500, 530))])  # fmt: skip
    outdir = tmp_path / "out"
    result = revel(
        VcfRevelParams(input=Path(sample), revel_file=Path(table), ref=ref,
                       outdir=outdir, threads=1),
        check=False,
    )  # fmt: skip
    assert result.status == "ok", result.error
    assert _query(outdir / "revel_annotated.vcf.gz", "%ID:%REVEL\n") == [
        "snv:0.812", "diffalt:.", "block:.", "del:."
    ]  # fmt: skip
    (warning,) = [w for w in result.warnings if w.code == EXCLUDED]
    assert (warning.details["count"], warning.details["total"]) == (1, 2)
    assert warning.details["resource"] == "revel_hg38.tsv.gz"


def test_1325_chain_annotate_survives_a_stale_revel_ref_row(tmp_path):
    library = tmp_path / "lib" / "downloads"
    library.mkdir(parents=True)
    _vcf(library / "clinvar_hg38.vcf.gz",
         ["100 c1 C T . . CLNSIG=Pathogenic;CLNDN=X GT 0/1"],
         contig="1", info=("CLNSIG", "CLNDN"))  # fmt: skip
    table = _tsv(library / "revel_hg38.tsv.gz", REVEL_HEADER,
                 ["1 100 100 C T 0.9", "1 200 200 T G 0.5"])  # fmt: skip
    subprocess.run([get_tool_path("tabix"), "-s1", "-b2", "-e2", table], check=True)
    sample = _sample(tmp_path, SAMPLE_ROWS)
    reference_for_vcf(tmp_path / "lib" / "genomes" / "hg38.fa.gz", sample)
    outdir = tmp_path / "out"
    result = chain_annotate(
        VcfChainAnnotateParams(input=Path(sample), ref=tmp_path / "lib",
                               outdir=outdir, annotations="clinvar,revel"),
        check=False,
    )  # fmt: skip
    assert result.status == "ok", result.error
    assert _query(outdir / "chain_annotated.vcf.gz", "%ID:%CLNSIG:%REVEL\n") == [
        "snv:Pathogenic:0.9", "diffalt:.:.", "block:.:.", "del:.:."
    ]  # fmt: skip
    # The step's log is captured, so the chain's own result reports the drop.
    assert [w.details["count"] for w in result.warnings if w.code == EXCLUDED] == [1]


def test_1325_vcf_resource_mostly_disagreeing_with_the_fasta_is_refused(tmp_path):
    # Right contig lengths, wrong coordinates: its REF bases rarely match this
    # FASTA, and the few that match by chance would annotate wrong variants.
    positions = range(10, 250, 10)
    sample = _sample(tmp_path, [f"{p} v{p} C T . PASS . GT 0/1" for p in positions],
                     length=1000)  # fmt: skip
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    revel_vcf = _vcf(tmp_path / "revel.vcf.gz",
                     [f"{p} . {'C' if p == 10 else 'G'} T . . REVEL=0.5 GT 0/1"
                      for p in positions],
                     contig="1", length=1000, info=("REVEL",))  # fmt: skip
    outdir = tmp_path / "out"
    with pytest.raises(WGSExtractError, match="23 of 24 .*another genome build"):
        run_wgsextract("vcf", "revel", "--input", sample, "--revel-file", revel_vcf,
                       "--ref", str(ref), "--outdir", str(outdir))  # fmt: skip
    assert not (outdir / "revel_annotated.vcf.gz").exists()


def test_1325_small_query_still_refuses_a_table_for_another_build(tmp_path):
    """PR #1343 review: only a table's rows at the query's SNVs are normalized,
    so a one-SNV query must not be what decides the table's build. Like hg19 and
    hg38 at chr1's start, the table agrees with the FASTA only at first."""
    sample = _sample(tmp_path, ["5 q5 A G . PASS . GT 0/1"], length=40000)
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    rows = [f"1 {p} {p} {'A' if p <= 9000 else 'C'} G 0.5" for p in range(1, 30001)]
    table = _tsv(tmp_path / "revel_hg19.tsv.gz", REVEL_HEADER, rows)
    outdir = tmp_path / "out"
    with pytest.raises(WGSExtractError, match="revel_hg19.*another genome build"):
        run_wgsextract("vcf", "revel", "--input", sample, "--revel-file", table,
                       "--ref", str(ref), "--outdir", str(outdir))  # fmt: skip
    assert not (outdir / "revel_annotated.vcf.gz").exists()


def test_1325_multiallelic_stale_rows_do_not_outvote_matching_records(tmp_path):
    positions = range(10, 210, 10)
    sample = _sample(
        tmp_path, [f"{p} q{p} C T . PASS . GT 0/1" for p in positions], length=1000
    )
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    resource = _vcf(
        tmp_path / "revel.vcf.gz",
        [f"{p} . {'G A,C,T' if p <= 70 else 'C T'} . . REVEL=0.5 GT 0/1"
         for p in positions],
        contig="1", length=1000, info=("REVEL",),
    )  # fmt: skip
    outdir = tmp_path / "out"
    result = revel(VcfRevelParams(
        input=sample, revel_file=resource, ref=ref, outdir=outdir, min_score=0.4
    ))  # fmt: skip
    assert _query(outdir / "revel_gt_0.4.vcf.gz", "%ID\n") == [
        f"q{p}" for p in positions if p > 70
    ]
    (warning,) = [w for w in result.warnings if w.code == EXCLUDED]
    assert (warning.details["count"], warning.details["total"]) == (7, 20)


def test_1325_stale_input_revel_does_not_pass_score_filter(tmp_path):
    sample = _sample(
        tmp_path,
        ["100 snv C T . PASS REVEL=0.1 GT 0/1",
         "200 stale A G . PASS REVEL=0.9 GT 0/1",
         "300 block G <NON_REF> . PASS END=310;REVEL=0.9 GT 0/0"],
        length=1000, info=("REVEL",),
    )  # fmt: skip
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    table = _tsv(tmp_path / "revel.tsv.gz", REVEL_HEADER,
                 ["1 100 100 C T 0.812", "1 200 200 T G 0.7"])  # fmt: skip
    outdir = tmp_path / "out"
    revel(VcfRevelParams(
        input=sample, revel_file=table, ref=ref, outdir=outdir, min_score=0.5
    ))  # fmt: skip
    assert _query(outdir / "revel_annotated.vcf.gz", "%ID:%REVEL\n") == [
        "snv:0.812", "stale:.", "block:."
    ]  # fmt: skip
    assert _query(outdir / "revel_gt_0.5.vcf.gz", "%ID\n") == ["snv"]


def test_1325_cadd_refresh_drops_old_scores_but_keeps_both_passes(tmp_path):
    sample = _bgzip(
        tmp_path / "sample.vcf.gz",
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n"
        '##INFO=<ID=CADD_RAW,Number=A,Type=Float,Description="Old raw score">\n'
        '##INFO=<ID=CADD_PHRED,Number=A,Type=Float,Description="Old PHRED score">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        "chr1\t100\tsnv\tC\tT\t.\tPASS\t.\tGT\t0/1\n"
        "chr1\t200\tstale\tA\tG\t.\tPASS\tCADD_RAW=9;CADD_PHRED=99\tGT\t0/1\n"
        "chr1\t400\tdel\tCA\tC\t.\tPASS\t.\tGT\t0/1\n",
    )
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    header = "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED"
    snv = _tsv(tmp_path / "cadd.tsv.gz", header, ["1 100 C T 2.5 24", "1 200 T G 3 30"])
    indel = _tsv(tmp_path / "cadd_indel.tsv.gz", header, ["1 400 CA C 4.2 31"])
    outdir = tmp_path / "out"
    cadd(VcfCaddParams(
        input=sample, cadd_file=snv, cadd_indel_file=indel, ref=ref, outdir=outdir,
        min_score=20.5,
    ))  # fmt: skip
    assert _query(outdir / "cadd_annotated.vcf.gz", "%ID:%CADD_RAW:%CADD_PHRED\n") == [
        "snv:2.5:24", "stale:.:.", "del:4.2:31"
    ]  # fmt: skip
    assert _query(outdir / "cadd_gt_20.5.vcf.gz", "%ID\n") == ["snv", "del"]


@pytest.mark.parametrize("annotator", ["clinvar", "chain"])
def test_1325_clinvar_companion_reports_stale_resource_rows_once_per_run(
    tmp_path, annotator
):
    library = tmp_path / "lib"
    downloads = library / "downloads"
    downloads.mkdir(parents=True)
    resource = _vcf(
        downloads / "clinvar_hg38.vcf.gz",
        ["100 good C T . . CLNSIG=Pathogenic GT 0/1",
         "200 stale T G . . CLNSIG=Pathogenic GT 0/1"],
        contig="1", info=("CLNSIG",),
    )  # fmt: skip
    sample = _sample(tmp_path, SAMPLE_ROWS)
    gvcf = _vcf(Path(sample).with_name("sample.g.vcf.gz"), SAMPLE_ROWS)
    caller_result = CommandResult(command="vcf snp")
    caller_result.add_output(sample, "variants")
    caller_result.add_output(gvcf, "reference_evidence")
    caller_result.finalize(probe_tools=False)
    caller_result.write_manifest()
    reference_for_vcf(library / "genomes" / "hg38.fa.gz", sample)
    for outdir in (tmp_path / "first", tmp_path / "cached"):
        if annotator == "clinvar":
            result = clinvar(VcfClinvarParams(
                input=sample, clinvar_file=resource, ref=library, outdir=outdir,
                threads=1,
            ))  # fmt: skip
            summary = next(c for c in result.details["confidence"]
                           if c["role"] == "clinvar_pathogenic")  # fmt: skip
            assert (
                summary["reference_evidence"]["checked"]["clinvar_pathogenic_sites"][
                    "sites"
                ]
                == 1
            )
        else:
            result = chain_annotate(VcfChainAnnotateParams(
                input=sample, ref=library, outdir=outdir, annotations="clinvar",
            ))  # fmt: skip
        assert [w.details["count"] for w in result.warnings if w.code == EXCLUDED] == [
            1
        ]


def test_1325_analyze_reports_the_clinvar_ref_warning_once(tmp_path):
    rows = SAMPLE_ROWS[:2]
    sample = _sample(tmp_path, rows, length=1000)
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    gvcf = _vcf(Path(sample).with_name("sample.g.vcf.gz"), rows, length=1000)
    caller_result = CommandResult(command="vcf snp")
    caller_result.add_output(sample, "variants")
    caller_result.add_output(gvcf, "reference_evidence")
    caller_result.finalize(probe_tools=False)
    caller_result.write_manifest()
    # A plain VCF must keep the same warning identity when a step prepares a
    # compressed copy and discovery looks up the original resource.
    resource = tmp_path / "clinvar.vcf"
    resource.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=1,length=1000>\n"
        '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="Significance">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t100\tgood\tC\tT\t.\tPASS\tCLNSIG=Pathogenic\n"
        "1\t200\tstale\tT\tG\t.\tPASS\tCLNSIG=Pathogenic\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(
        f"clinvar_vcf_path = {json.dumps(str(resource))}\n"
        + state_directory_setting(tmp_path),
        encoding="utf-8",
    )
    manifest = tmp_path / "analyze-result.json"
    args = [
        "--config", str(config), "analyze", "comprehensive", "--vcf-inputs", sample,
        "--ref", str(ref), "--outdir", str(tmp_path / "out"), "--threads", "1",
        "--result-file", str(manifest),
    ]  # fmt: skip
    for extra in ([], ["--force-step", "discovery"], []):
        main([*args, *extra])
        result = json.loads(manifest.read_text(encoding="utf-8"))
        assert result["status"] == "ok", result["error"]
        (warning,) = [w for w in result["warnings"] if w["code"] == EXCLUDED]
        assert (warning["details"]["count"], warning["details"]["total"]) == (1, 2)
        summary = next(
            row for row in result["details"]["confidence"]
            if row["role"] == "significant_variants"
        )  # fmt: skip
        assert (
            summary["reference_evidence"]["checked"]["clinvar_pathogenic_sites"][
                "sites"
            ]
            == 1
        )
