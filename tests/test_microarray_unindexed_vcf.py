"""`microarray` from unindexed VCF input: a plain .vcf or a .bcf without an
index, on the simulation's tiny library."""

import json
from pathlib import Path

import pytest

from tests import test_microarray_simulation as sim
from tests.htslib_helpers import needs_htslib
from wgsextract_cli import api
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


def _plain_vcf(
    path: Path, records: list[str], *, declare: bool = True, samples=("S",)
) -> str:
    """A plain VCF of ``records`` (one GT per sample, each with the depth and
    quality the simulation's VCFs carry), optionally without ##contig lines."""
    body = []
    for record in records:
        parts = record.split()
        body.append(
            "\t".join(parts[:8] + ["GT:DP:GQ"] + [f"{gt}:30:60" for gt in parts[9:]])
        )
    contigs = sorted({r.split()[0] for r in records}) if declare else []
    path.write_text(
        "##fileformat=VCFv4.2\n"
        + "".join(f"##contig=<ID={c},length={sim.LENGTH}>\n" for c in contigs)
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        + '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        + '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
        + "\t".join(samples)
        + "\n"
        + "".join(line + "\n" for line in body)
    )
    return str(path)


# Two calls at rsAlt: the second is the one a kit from the indexed VCF keeps.
CONFLICTING = [
    *sim.FEMALE_VCF[:1],
    "1 2000 . A G 50 PASS . GT 1/1",
    *sim.FEMALE_VCF[1:],
]


@pytest.mark.parametrize(
    ("plain_records", "declare"),
    [(CONFLICTING, True), (sim.FEMALE_VCF, False), (sim.FEMALE_VCF[::-1], False)],
    ids=["same-position calls", "no contig lines", "unsorted, no contig lines"],
)
def test_issue_1242_plain_vcf_layouts_get_the_bgzipped_kit(
    tmp_path, library, plain_records, declare
):
    """Review of the #1242 fix: preparing every plain VCF with bcftools sort
    reordered calls at one position, so a different call won, and rejected
    VCFs without ##contig lines, which bcftools otherwise reads."""
    fasta, _ = library
    indexed = sorted(plain_records, key=lambda r: (r.split()[0], int(r.split()[1])))
    (tmp_path / "indexed").mkdir()
    vcf_gz = sim._vcf(tmp_path / "indexed/female.vcf", ("1", "X"), indexed)
    plain = _plain_vcf(tmp_path / "female.vcf", plain_records, declare=declare)

    sim._microarray(vcf_gz, fasta, tmp_path / "out-indexed")
    sim._microarray(plain, fasta, tmp_path / "out-plain")

    kit = (tmp_path / "out-plain/female_23andMe_V3.txt").read_text()
    assert kit == (tmp_path / "out-indexed/female_23andMe_V3.txt").read_text()
    assert "rsAlt\t1\t2000\tCC" in kit  # the later of two calls at one position


def test_issue_1242_plain_multi_sample_vcf_exports_the_chosen_sample(tmp_path, library):
    fasta, _ = library
    joint = [
        r.rsplit(" ", 1)[0] + " 0/0 " + r.rsplit(" ", 1)[1] for r in sim.FEMALE_VCF
    ]
    plain = _plain_vcf(tmp_path / "joint.vcf", joint, samples=("S1", "S2"))
    sim._microarray(plain, fasta, tmp_path / "out", "--sample", "S2")
    sim._microarray(
        plain, fasta, tmp_path / "region", "--sample", "S2", "--region", "chrX"
    )

    called = sim._genotypes(tmp_path / "out/joint_S2_23andMe_V3.txt")
    assert {k: v for k, v in called.items() if v != "--"} == {
        "rsHet": "AC",
        "rsAlt": "CC",
        "rsX": "AG",
    }
    assert sim._genotypes(tmp_path / "region/joint_S2_CombinedKit.txt") == {"rsX": "AG"}
    assert not list((tmp_path / "out").rglob("input.vcf.gz"))


def test_issue_1242_kits_made_before_the_fix_are_regenerated(tmp_path, library):
    """Kits made from a plain VCF before #1242 was fixed were all no-calls;
    a rerun must not reuse them."""
    fasta, _ = library
    params = api.MicroarrayParams(
        input=Path(_plain_vcf(tmp_path / "female.vcf", sim.FEMALE_VCF)),
        ref=Path(fasta),
        outdir=tmp_path / "out",
        formats="23andme_v3",
    )
    api.microarray.generate(params)
    sidecars = list((tmp_path / "out").glob("*.inputs.json"))
    assert sidecars
    for sidecar in sidecars:
        saved = json.loads(sidecar.read_text())
        del saved["inputs"]["vcf_preparation_policy"]
        sidecar.write_text(json.dumps(saved))

    rerun = api.microarray.generate(params)

    reused = {Path(o.path).name: o.metrics.get("reused") for o in rerun.outputs}
    assert not reused["female_23andMe_V3.txt"]
    assert not reused["female_CombinedKit.txt"]
