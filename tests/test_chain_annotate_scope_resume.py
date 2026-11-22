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
