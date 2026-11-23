"""`vep run` ploidy for calling a BAM or CRAM (#1244)."""

import gzip
from dataclasses import replace
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam, vcf_calls
from tests.test_vep_run import _bam_with_snps
from tests.test_vep_run import fake_vep as fake_vep
from wgsextract_cli import api
from wgsextract_cli.api import VepRunParams
from wgsextract_cli.core.ref_library.assets import sequence_signature

pytestmark = needs_htslib


def _vep_rs_caches(tmp_path: Path, *assemblies: str) -> None:
    for assembly in assemblies:
        (tmp_path / "vep-rs" / "homo_sapiens" / f"116_{assembly}").mkdir(parents=True)


_T2T = {"chr1": 248387328, "chrX": 154259566, "chrY": 62460029, "chrM": 16569}


def _read_less_bam(
    tmp_path: Path, contigs: dict[str, int], bam_contigs: list[str] | None = None
) -> tuple[str, str]:
    """A FASTA stand-in indexed with ``contigs`` and a read-less BAM whose
    header lists ``bam_contigs`` of them (default: all)."""
    fasta = tmp_path / "ref.fa"
    fasta.write_text("".join(f">{name}\nA\n" for name in contigs))
    Path(f"{fasta}.fai").write_text(
        "".join(f"{name}\t{length}\t0\t1\t2\n" for name, length in contigs.items())
    )
    bam = sam_to_bam(
        ["@HD\tVN:1.6\tSO:coordinate"]
        + [f"@SQ\tSN:{name}\tLN:{contigs[name]}" for name in bam_contigs or contigs],
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
    fasta, bam = (
        _read_less_bam(tmp_path, _T2T) if build == "t2t" else _bam_with_snps(tmp_path)
    )
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


@pytest.mark.parametrize("shown_by", ["header", "reference"])
def test_1244_assembly_contradicting_the_alignment_is_refused(
    tmp_path, fake_vep, shown_by
):
    """Copilot reviews of #1333 (vep run wrong-build ploidy): `--assembly
    GRCh38` on a T2T BAM with a neutrally named FASTA bypassed the refusal and
    called it with GRCh38's ploidy, also when only the FASTA's index showed
    T2T because the BAM's header lacked chr1."""
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38")
    bam_contigs = None if shown_by == "header" else ["chrX", "chrY", "chrM"]
    fasta, bam = _read_less_bam(tmp_path, _T2T, bam_contigs)
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


def test_1346_cached_contig_count_does_not_choose_ploidy_or_override_assembly(
    tmp_path, fake_vep
):
    """Copilot review of #1333 (vep run wrong-build ploidy): an 86-contig
    header without chr1, which only its contig count calls hg19, refused
    `--assembly GRCh38` despite a matching FASTA and an explicit ploidy."""
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38")
    fasta, bam = _read_less_bam(tmp_path, {f"c{i}": 1000 for i in range(1, 87)})

    params = VepRunParams(
        input=Path(bam), ref=Path(fasta), engine="vep-rs", vep_cache=tmp_path
    )
    for _ in range(2):  # The second run reads the weak build from the workspace.
        result = api.vep.run(params, check=False)
        [failure] = [w for w in result.warnings if w.code == "input_failed"]
        assert "not confirmed" in failure.message and "--assembly" in failure.message

    api.vep.run(replace(params, vep_assembly="GRCh38", ploidy="2"))

    assert len(fake_vep()) == 1
    assert (tmp_path / "wgse" / "s_vep.vcf.gz").exists()


def test_1346_vep_refuses_a_filename_only_preset_until_assembly_is_given(
    tmp_path, fake_vep
):
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38")
    fasta, bam = _bam_with_snps(tmp_path)
    named_ref = Path(fasta).with_name("hg38.fa")
    Path(fasta).rename(named_ref)
    params = VepRunParams(
        input=Path(bam), ref=named_ref, engine="vep-rs", vep_cache=tmp_path
    )

    result = api.vep.run(params, check=False)

    [failure] = [w for w in result.warnings if w.code == "input_failed"]
    assert result.status == "failed"
    assert "not confirmed" in failure.message
    assert "--assembly" in failure.message and "--ploidy-file" in failure.message
    assert not (tmp_path / "wgse" / "s_vep.vcf.gz").exists()

    api.vep.run(replace(params, vep_assembly="GRCh38"))
    assert [row[1] for row in vcf_calls(tmp_path / "wgse" / "s_vep.vcf.gz")] == [
        "101", "301",
    ]  # fmt: skip


@pytest.mark.parametrize("assembly", ["GRCh38", "T2T-CHM13v2.0", None])
def test_1435_vep_refuses_t2t_reference_for_grch38_bam(tmp_path, fake_vep, assembly):
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38", "T2T-CHM13v2.0")
    fasta, _ = _read_less_bam(tmp_path, _T2T)
    bam = sam_to_bam(["@SQ\tSN:chr1\tLN:248956422"], tmp_path / "grch38.bam")

    result = api.vep.run(
        VepRunParams(
            input=Path(bam), ref=Path(fasta), engine="vep-rs", vep_cache=tmp_path,
            vep_assembly=assembly, ploidy="2",
        ),
        check=False,
    )  # fmt: skip

    [failure] = [w for w in result.warnings if w.code == "input_failed"]
    assert result.status == "failed"
    assert "alignment header is hg38" in failure.message
    assert "--ref is t2t" in failure.message
    if assembly:
        assert f"--assembly is {assembly}" in failure.message
    assert fake_vep() == []


def test_1346_stale_header_md5_does_not_choose_ploidy_over_contig_lengths(
    tmp_path, fake_vep
):
    fake_vep.install(tool="vep-rs")
    _vep_rs_caches(tmp_path, "GRCh38", "T2T-CHM13v2.0")
    fasta, _ = _read_less_bam(tmp_path, _T2T)
    bam = sam_to_bam(
        [f"@SQ\tSN:{name}\tLN:{length}" for name, length in _T2T.items()]
        + ["@CO\tMD5:bd894134bddba260df88a90123a2ee9c"],
        tmp_path / "t2t-with-grch38-tag.bam",
    )

    result = api.vep.run(
        VepRunParams(
            input=Path(bam), ref=Path(fasta), engine="vep-rs", vep_cache=tmp_path
        ),
        check=False,
    )

    [failure] = [w for w in result.warnings if w.code == "input_failed"]
    assert (
        "reference build: t2t" in failure.message and "--ploidy-file" in failure.message
    )
    assert fake_vep() == []


@pytest.mark.parametrize(
    "override",
    [
        "--assembly GRCh37",
        "-FAS=foreign.fa",
        "--as GRCh37",
        "--fa foreign.fa",
        "--grch37",
        "--grc",
    ],
)
def test_1435_raw_vep_arguments_cannot_replace_validated_inputs(
    tmp_path, fake_vep, override
):
    fasta, bam = _bam_with_snps(tmp_path)
    result = api.vep.run(
        VepRunParams(
            input=Path(bam), ref=Path(fasta), engine="ensembl", vep_cache=tmp_path,
            vep_assembly="GRCh38", ploidy="2", vep_args=override,
        ),
        check=False,
    )  # fmt: skip

    assert result.status == "failed"
    assert "--vep-args" in str(result.error) and "--assembly" in str(result.error)
    assert fake_vep() == []


def test_1435_vep_detects_an_unindexed_gzip_reference_by_signature(
    tmp_path, fake_vep, monkeypatch
):
    from wgsextract_cli.core.constants import REFERENCE_MODELS

    fasta = tmp_path / "reference.fa.gz"
    fasta.write_bytes(gzip.compress(b">NC_000001.11\nAAAA\n>NC_000002.12\nCCCC\n"))
    signature = sequence_signature({"NC_000001.11": 4, "NC_000002.12": 4})
    monkeypatch.setitem(REFERENCE_MODELS, signature, ["hg38", "", "catalog.fa.gz"])
    bam = sam_to_bam(["@SQ\tSN:NC_000002.12\tLN:4"], tmp_path / "subset.bam")

    result = api.vep.run(
        VepRunParams(
            input=Path(bam), ref=fasta, engine="ensembl", vep_cache=tmp_path,
            vep_assembly="GRCh37", ploidy="2",
        ),
        check=False,
    )  # fmt: skip

    [failure] = [w for w in result.warnings if w.code == "input_failed"]
    assert (
        "--ref is hg38" in failure.message and "--assembly is GRCh37" in failure.message
    )
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
