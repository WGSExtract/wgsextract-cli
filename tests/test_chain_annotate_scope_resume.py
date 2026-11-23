"""vcf chain-annotate's --threads, --region/--bed and step resume (#1291), on
tiny VCFs and annotation sources with real bcftools."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, reference_for_vcf
from tests.test_vcf_annotations import (
    REVEL_HEADER,
    SAMPLE_ROWS,
    _bgzip,
    _query,
    _sample,
    _tsv,
    _vcf,
)
from wgsextract_cli import api
from wgsextract_cli.core.dependencies import get_tool_path

pytestmark = needs_tools("samtools", "bcftools", "bgzip", "tabix")

ROWS = "%CHROM:%POS:%CLNSIG:%REVEL\n"


def _revel(downloads: Path) -> None:
    table = _tsv(
        downloads / "revel_hg38.tsv.gz",
        REVEL_HEADER,
        ["1 100 100 C T 0.9", "1 400 400 CA C 0.3"],
    )
    subprocess.run([get_tool_path("tabix"), "-s1", "-b2", "-e2", table], check=True)


def _library(tmp_path: Path, sample: str) -> Path:
    """ClinVar and REVEL on '1' (the sample says 'chr1'), and a reference."""
    library = tmp_path / "lib"
    downloads = library / "downloads"
    downloads.mkdir(parents=True)
    _vcf(downloads / "clinvar_hg38.vcf.gz",
         ["100 c1 C T . . CLNSIG=Pathogenic;CLNDN=X GT 0/1"],
         contig="1", info=("CLNSIG", "CLNDN"))  # fmt: skip
    _revel(downloads)
    reference_for_vcf(library / "genomes" / "hg38.fa.gz", sample)
    return library


def _chain(sample: str, library: Path, outdir: Path, **options):
    options.setdefault("annotations", "clinvar,revel")
    return api.vcf.chain_annotate(
        api.VcfChainAnnotateParams(
            input=Path(sample), ref=library, outdir=outdir, **options
        )
    )


def _plan(result) -> dict[str, str]:
    return {item["step"]: item["action"] for item in result.details["steps"]}


@pytest.fixture
def chain_inputs(tmp_path):
    sample = _sample(tmp_path, SAMPLE_ROWS)
    return sample, _library(tmp_path, sample), tmp_path / "out"


def test_1291_region_scopes_the_chain_in_either_chr_naming(chain_inputs):
    sample, library, outdir = chain_inputs
    # The VCF names chr1; the region is given as 1.
    result = _chain(sample, library, outdir, region="1:50-250", threads=3,
                    keep_intermediates=True)  # fmt: skip

    assert _query(outdir / "chain_annotated.vcf.gz", ROWS) == [
        "chr1:100:Pathogenic:0.9", "chr1:200:.:.",
    ]  # fmt: skip
    assert result.provenance.params["region"] == "1:50-250"
    assert result.metrics["region_bases"] == 201
    # Every annotation step was given the thread budget.
    manifests = list(outdir.glob("chain_step_*_*/*.manifest.json"))
    assert {m.parent.name for m in manifests} == {
        "chain_step_1_clinvar",
        "chain_step_2_revel",
    }
    for manifest in manifests:
        document = json.loads(manifest.read_text(encoding="utf-8"))
        assert document["provenance"]["params"]["threads"] == 3


@pytest.mark.parametrize(
    ("region", "message"),
    [("chr1:300000000-300000100", "beyond the end of chr1"),
     ("chrQ", "chrQ isn't a contig")],
)  # fmt: skip
def test_1291_region_outside_the_vcf_is_refused(chain_inputs, region, message):
    sample, library, outdir = chain_inputs
    with pytest.raises(api.CommandFailed, match=message):
        _chain(sample, library, outdir, region=region)
    assert not (outdir / "chain_annotated.vcf.gz").exists()


def test_1291_bed_scopes_the_chain_and_warns_of_absent_contigs(chain_inputs, tmp_path):
    sample, library, outdir = chain_inputs
    bed = tmp_path / "panel.bed"
    bed.write_text("1\t390\t420\nchrUn_x\t0\t10\n", encoding="utf-8")

    result = _chain(sample, library, outdir, bed=bed)

    assert _query(outdir / "chain_annotated.vcf.gz", ROWS) == ["chr1:400:.:."]
    warnings = {w.code: w.details for w in result.warnings}
    assert warnings["bed_contigs_not_in_vcf"]["contigs"] == "chrUn_x"
    assert result.provenance.params["bed"] == str(bed)
    assert any(item.role == "regions_bed" for item in result.inputs)


def test_1291_rerun_resumes_after_the_failed_step(chain_inputs):
    sample, library, outdir = chain_inputs
    revel = library / "downloads" / "revel_hg38.tsv.gz"
    revel.write_bytes(b"not a REVEL table\n")
    Path(f"{revel}.tbi").unlink()

    with pytest.raises(api.CommandFailed, match="'revel' failed"):
        _chain(sample, library, outdir)

    revel.unlink()
    _revel(library / "downloads")
    result = _chain(sample, library, outdir)

    assert _plan(result) == {"clinvar": "skip", "revel": "run", "publish": "run"}
    assert _query(outdir / "chain_annotated.vcf.gz", ROWS)[0] == (
        "chr1:100:Pathogenic:0.9"
    )
    assert not list(outdir.glob("chain_step_*"))


def test_1291_finished_chain_is_skipped_until_its_settings_or_input_change(
    chain_inputs,
):
    sample, library, outdir = chain_inputs
    first = _chain(sample, library, outdir, table_format="tsv")
    assert (outdir / "chain_annotated_variants.tsv").is_file()

    again = _chain(sample, library, outdir, table_format="tsv")
    assert _plan(again) == {"publish": "skip", "table": "skip"}
    assert {o.role for o in again.outputs} >= {"annotated_vcf", "variant_table"}
    assert again.metrics["variants"] == first.metrics["variants"]

    fewer = _chain(sample, library, outdir, annotations="clinvar")
    assert _plan(fewer) == {"clinvar": "run", "publish": "run"}
    assert fewer.metrics["annotations_applied"] == "clinvar"

    _sample(Path(sample).parent.parent, SAMPLE_ROWS[:2])
    changed = _chain(sample, library, outdir, annotations="clinvar")
    assert _plan(changed) == {"clinvar": "run", "publish": "run"}
    assert _query(outdir / "chain_annotated.vcf.gz", "%POS\n") == ["100", "200"]


def test_1291_region_keeps_vcf_naming_for_a_lengthless_contig(tmp_path):
    """A ##contig line without a length and a FASTA naming it '1' (review of
    #1422): the region still selects the VCF's own chr1 calls."""
    library = tmp_path / "lib"
    downloads = library / "downloads"
    downloads.mkdir(parents=True)
    _revel(downloads)
    fasta = reference_for_vcf(
        library / "genomes" / "hg38.fa.gz",
        _vcf(tmp_path / "named1.vcf.gz", SAMPLE_ROWS, contig="1"),
    )
    header = (
        "##fileformat=VCFv4.2\n##contig=<ID=chr1>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    )
    (tmp_path / "in").mkdir()
    sample = _bgzip(
        tmp_path / "in" / "sample.vcf.gz",
        header + "".join("chr1\t" + "\t".join(r.split()) + "\n" for r in SAMPLE_ROWS),
    )
    subprocess.run([get_tool_path("tabix"), "-p", "vcf", sample], check=True)

    _chain(sample, fasta, tmp_path / "out", annotations="revel", region="chr1:90-110")

    assert _query(
        tmp_path / "out" / "chain_annotated.vcf.gz", "%CHROM:%POS:%REVEL\n"
    ) == ["chr1:100:0.9"]


def test_1291_replaced_cadd_indel_scores_rerun_a_finished_chain(tmp_path):
    """The CADD indel table is step input too (review of #1422)."""
    library = tmp_path / "lib"
    downloads = library / "downloads"
    downloads.mkdir(parents=True)
    sample = _sample(tmp_path, SAMPLE_ROWS)
    header = "#Chrom\tPos\tRef\tAlt\tRawScore\tPHRED"
    _tsv(downloads / "cadd_hg38.tsv.gz", header, ["1 100 C T 2.5 24.1"])
    indels = downloads / "cadd_indel_hg38.tsv.gz"
    _tsv(indels, header, ["1 400 CA C 4.2 31"])
    reference_for_vcf(library / "genomes" / "hg38.fa.gz", sample)
    outdir = tmp_path / "out"
    _chain(sample, library, outdir, annotations="cadd")

    for index in downloads.glob("cadd_indel_hg38.tsv.gz.*"):
        index.unlink()
    _tsv(indels, header, ["1 400 CA C 4.2 12"])
    result = _chain(sample, library, outdir, annotations="cadd")

    assert _plan(result) == {"cadd": "run", "publish": "run"}
    scores = _query(outdir / "chain_annotated.vcf.gz", "%ID:%CADD_PHRED\n")
    assert "del:12" in scores


@pytest.mark.parametrize("indexed", [True, False])
def test_1291_records_spanning_two_bed_regions_are_kept_once(tmp_path, indexed):
    """A gVCF block or deletion overlapping disjoint BED intervals comes out
    once, read by the index or streamed (review of #1422)."""
    rows = [
        "100 blk G <NON_REF> . PASS END=900 GT 0/0",
        "150 del " + "A" * 100 + " A . PASS . GT 1/1",
        "500 snv C T . PASS . GT 0/1",
    ]
    sample = _sample(tmp_path, rows)
    library = _library(tmp_path, sample)
    if not indexed:
        plain = Path(sample).with_suffix("")
        plain.write_text(
            subprocess.run(
                [get_tool_path("bcftools"), "view", sample],
                capture_output=True, text=True, check=True,
            ).stdout,
            encoding="utf-8",
        )  # fmt: skip
        sample = str(plain)
    bed = tmp_path / "panel.bed"
    bed.write_text("chr1\t119\t130\nchr1\t199\t210\nchr1\t399\t600\n", encoding="utf-8")

    _chain(sample, library, tmp_path / "out", bed=bed)

    assert _query(tmp_path / "out" / "chain_annotated.vcf.gz", "%ID\n") == [
        "blk", "del", "snv",
    ]  # fmt: skip


def test_1291_changed_confidence_thresholds_rerun_clinvar(chain_inputs, monkeypatch):
    """ClinVar labels with config.toml's thresholds (review of #1422)."""
    from wgsextract_cli.core import config

    sample, library, outdir = chain_inputs
    _chain(sample, library, outdir)
    monkeypatch.setitem(config.settings, "confidence_min_depth", 3.0)

    result = _chain(sample, library, outdir)

    assert _plan(result) == {"clinvar": "run", "revel": "run", "publish": "run"}


def test_1291_failed_forced_rebuild_stays_resumable(chain_inputs):
    """A --force-step rebuild that fails isn't mistaken for the finished chain
    before it (review of #1422)."""
    sample, library, outdir = chain_inputs
    _chain(sample, library, outdir)
    revel = library / "downloads" / "revel_hg38.tsv.gz"
    revel.write_bytes(b"not a REVEL table\n")
    Path(f"{revel}.tbi").unlink()
    with pytest.raises(api.CommandFailed, match="'revel' failed"):
        _chain(sample, library, outdir, force_steps=("revel",))

    revel.unlink()
    _revel(library / "downloads")
    result = _chain(sample, library, outdir)

    assert _plan(result) == {"clinvar": "skip", "revel": "run", "publish": "run"}
