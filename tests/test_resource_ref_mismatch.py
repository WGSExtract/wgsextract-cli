"""Issue #1325: a resource's stale REF rows are dropped, not fatal."""

import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, reference_for_vcf, run_wgsextract
from tests.test_vcf_annotations import (
    REVEL_HEADER,
    SAMPLE_ROWS,
    _query,
    _sample,
    _tsv,
    _vcf,
)
from wgsextract_cli.api.vcf import (
    VcfChainAnnotateParams,
    VcfRevelParams,
    chain_annotate,
    revel,
)
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import WGSExtractError

pytestmark = needs_tools("samtools", "bcftools", "bgzip", "tabix")
EXCLUDED = "annotation_ref_mismatch_excluded"


def test_1325_stale_revel_ref_row_is_dropped_not_fatal(tmp_path):
    sample = _sample(tmp_path, SAMPLE_ROWS, length=1000)
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    # ANNOVAR's REVEL keeps hg19 REF bases: at 200 the FASTA (and sample) has A.
    table = _tsv(tmp_path / "revel_hg38.tsv.gz", REVEL_HEADER,
                 ["1 100 100 C T 0.812", "1 200 200 T G 0.5"])  # fmt: skip
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
    assert warning.details == {
        "count": 1, "total": 2, "resource": "revel_hg38.tsv.gz"
    }  # fmt: skip


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


def test_1325_resource_mostly_disagreeing_with_the_fasta_is_refused(tmp_path):
    # A table for another build: its REF bases rarely match this FASTA, and the
    # few that match by chance would annotate the wrong variants.
    positions = range(10, 250, 10)
    sample = _sample(tmp_path, [f"{p} v{p} C T . PASS . GT 0/1" for p in positions],
                     length=1000)  # fmt: skip
    ref = reference_for_vcf(tmp_path / "ref" / "ref.fa.gz", sample)
    rows = [f"1 {p} {p} {'C' if p == 10 else 'G'} T 0.5" for p in positions]
    table = _tsv(tmp_path / "revel.tsv.gz", REVEL_HEADER, rows)
    outdir = tmp_path / "out"
    with pytest.raises(WGSExtractError, match="23 of 24 .*another genome build"):
        run_wgsextract("vcf", "revel", "--input", sample, "--revel-file", table,
                       "--ref", str(ref), "--outdir", str(outdir))  # fmt: skip
    assert not (outdir / "revel_annotated.vcf.gz").exists()
