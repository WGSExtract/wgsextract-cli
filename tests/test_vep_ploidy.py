"""`vep run` ploidy for calling a BAM or CRAM (#1244)."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam, vcf_calls
from tests.test_vep_run import _bam_with_snps
from tests.test_vep_run import fake_vep as fake_vep
from wgsextract_cli import api
from wgsextract_cli.api import VepRunParams

pytestmark = needs_htslib


def _vep_rs_caches(tmp_path: Path, *assemblies: str) -> None:
    for assembly in assemblies:
        (tmp_path / "vep-rs" / "homo_sapiens" / f"116_{assembly}").mkdir(parents=True)


def _t2t_bam(tmp_path: Path) -> tuple[str, str]:
    """A read-less BAM and a FASTA stand-in whose contigs are T2T-CHM13's."""
    t2t = {"chr1": 248387328, "chrX": 154259566, "chrY": 62460029, "chrM": 16569}
    fasta = tmp_path / "ref.fa"
    fasta.write_text("".join(f">{name}\nA\n" for name in t2t))
    Path(f"{fasta}.fai").write_text(
        "".join(f"{name}\t{length}\t0\t1\t2\n" for name, length in t2t.items())
    )
    bam = sam_to_bam(
        ["@HD\tVN:1.6\tSO:coordinate"]
        + [f"@SQ\tSN:{name}\tLN:{length}" for name, length in t2t.items()],
        tmp_path / "s.bam",
    )
    return str(fasta), bam


@pytest.mark.parametrize("build", ["unknown", "t2t"])
def test_1244_bam_of_a_build_without_a_ploidy_preset_is_refused(
    tmp_path, fake_vep, build
):
    """#1244: a BAM of an unrecognised build was called with GRCh37's ploidy,
    and a T2T one with GRCh38's, whose PAR coordinates aren't CHM13's. Like
    `vcf snp`, `vep run` refuses both and names the fix; a ploidy file runs."""
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "T2T-CHM13v2.0" if build == "t2t" else "GRCh38")
    fasta, bam = _t2t_bam(tmp_path) if build == "t2t" else _bam_with_snps(tmp_path)
    # With the sex known, the other build's sex-dependent preset applied.
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Male")
    params = VepRunParams(
        input=Path(bam), ref=Path(fasta), engine="vep-rs", vep_cache=tmp_path
    )

    result = api.vep.run(params, check=False)

    assert result.status != "ok"
    [failure] = [w for w in result.warnings if w.code == "input_failed"]
    assert "--ploidy-file" in failure.message
    assert f"reference build: {build}" in failure.message
    assert fake_vep() == []
    assert not (tmp_path / "wgse" / "s_vep.vcf.gz").exists()

    ploidy = tmp_path / "ploidy.txt"
    ploidy.write_text("* * * M 2\n* * * F 2\n")
    api.vep.run(replace(params, ploidy_file=ploidy))
    assert len(fake_vep()) == 1
    assert (tmp_path / "wgse" / "s_vep.vcf.gz").exists()


def test_1244_assembly_contradicting_the_alignment_is_refused(tmp_path, fake_vep):
    """Copilot review of #1333 (vep run wrong-build ploidy): `--assembly
    GRCh38` on a T2T BAM with a neutrally named FASTA bypassed the refusal and
    called it with GRCh38's ploidy."""
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38")
    fasta, bam = _t2t_bam(tmp_path)
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Male")
    params = VepRunParams(
        input=Path(bam),
        ref=Path(fasta),
        engine="vep-rs",
        vep_cache=tmp_path,
        vep_assembly="GRCh38",
    )

    result = api.vep.run(params, check=False)

    [failure] = [w for w in result.warnings if w.code == "input_failed"]
    assert "t2t" in failure.message and "--assembly" in failure.message
    assert fake_vep() == []


@pytest.mark.parametrize(
    ("name", "assembly"),
    [("ploidy.txt", None), ("ploidy_t2t.txt", "T2T-CHM13v2.0")],
)
def test_1244_unknown_build_bam_uses_the_library_ploidy_file(
    tmp_path, fake_vep, name, assembly
):
    """Copilot review of #1333 (vep run wrong-build ploidy): an unknown-build
    BAM ignored the build-agnostic ploidy.txt beside its FASTA, and with
    `--assembly T2T-CHM13v2.0` ignored ploidy_t2t.txt, so both were refused."""
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38", "T2T-CHM13v2.0")
    fasta, bam = _bam_with_snps(tmp_path)
    (tmp_path / name).write_text("* * * M 1\n* * * F 1\n")

    api.vep.run(
        VepRunParams(
            input=Path(bam),
            ref=Path(fasta),
            engine="vep-rs",
            vep_cache=tmp_path,
            vep_assembly=assembly,
        )
    )

    calls = vcf_calls(tmp_path / "wgse" / "s_vep.vcf.gz")
    assert [(c[1], c[4]) for c in calls] == [("101", "1"), ("301", "1")]
