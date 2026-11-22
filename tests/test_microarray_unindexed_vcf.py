"""`microarray` from unindexed VCF input: a plain .vcf or a .bcf without an
index, on the simulation's tiny library."""

from pathlib import Path

import pytest

from tests import test_microarray_simulation as sim
from tests.htslib_helpers import needs_htslib
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib
library = sim.library
_synthetic_templates_only = sim._synthetic_templates_only


def _unindexed_copy(vcf_gz: str, form: str, folder: Path) -> str:
    """The calls of an indexed ``vcf_gz`` as an unindexed plain VCF or BCF."""
    folder.mkdir()
    out = str(folder / Path(vcf_gz).name.replace(".vcf.gz", f".{form}"))
    kind = {"vcf": "-Ov", "bcf": "-Ob"}[form]
    run_command([get_tool_path("bcftools"), "view", kind, "-o", out, vcf_gz])
    return out


@pytest.mark.parametrize("naming", ["bare", "chr"])
def test_issue_1242_unindexed_vcf_forms_get_the_bgzipped_kit(tmp_path, library, naming):
    """Issue #1242: a plain .vcf or unindexed .bcf skipped contig-name
    normalisation, so calls named unlike the targets (1, not chr1) became
    no-calls, and --region failed for lack of an index."""
    fasta, _ = library
    to_chr = {bare: chrom for chrom, bare in sim.CHROM_23ANDME.items()}
    named = to_chr if naming == "chr" else {}
    vcf_gz = sim._vcf(
        tmp_path / "female.vcf",
        tuple(named.get(c, c) for c in ("1", "X", "Y", "MT")),
        [
            named.get(r.split()[0], r.split()[0]) + r[r.index(" ") :]
            for r in sim.FEMALE_VCF
        ],
    )
    sources = {"vcf.gz": vcf_gz}
    for form in ("vcf", "bcf"):
        sources[form] = _unindexed_copy(vcf_gz, form, tmp_path / form)
    originals = {form: Path(path).read_bytes() for form, path in sources.items()}
    region = "X" if naming == "chr" else "chrX"  # the other naming style

    kits, regional = {}, {}
    for form, source in sources.items():
        sim._microarray(source, fasta, tmp_path / f"out-{form}")
        kits[form] = (tmp_path / f"out-{form}/female_23andMe_V3.txt").read_text()
        sim._microarray(source, fasta, tmp_path / f"region-{form}", "--region", region)
        regional[form] = sim._genotypes(
            tmp_path / f"region-{form}/female_CombinedKit.txt"
        )

    called = sim._genotypes(tmp_path / "out-vcf.gz/female_23andMe_V3.txt")
    assert {k: v for k, v in called.items() if v != "--"} == {
        "rsHet": "AC",
        "rsAlt": "CC",
        "rsX": "AG",
    }
    assert kits["vcf"] == kits["vcf.gz"]
    assert kits["bcf"] == kits["vcf.gz"]
    assert regional == dict.fromkeys(sources, {"rsX": "AG"})
    for form in ("vcf", "bcf"):
        # The input is only read: no index or compressed copy beside it.
        assert Path(sources[form]).read_bytes() == originals[form]
        assert [p.name for p in (tmp_path / form).iterdir()] == [
            Path(sources[form]).name
        ]
        # Nor is the run's indexed working copy left in the output folder.
        assert not list((tmp_path / f"out-{form}").rglob("input.vcf.gz"))
