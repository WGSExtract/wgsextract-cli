"""Sex-PAR regression: real fixture alignments, only expensive callers faked."""

import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.htslib_helpers import (
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from tests.test_clair3 import fake_clair3 as fake_clair3
from tests.test_clair3 import make_model
from tests.test_deepvariant import apple as apple
from tests.test_deepvariant import dv as dv
from tests.test_deepvariant import linux_x86 as linux_x86
from tests.test_deepvariant import mac_native as mac_native
from tests.test_gatk_scatter import gatk as gatk
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfClair3Params, VcfDeepvariantParams, VcfGatkParams

pytestmark = needs_htslib


@pytest.fixture(params=["hg38", "hg19"])
def sex_par(tmp_path, request):
    rng = random.Random(850)
    boundary = 9999 if request.param == "hg38" else 60000
    sequences = {
        chrom: "".join(rng.choice("ACGT") for _ in range(boundary + 2000))
        for chrom in ("chr20", "chrX")
    }
    ref = write_fasta(tmp_path / f"{request.param}.fa", sequences)
    rows = ["@HD\tVN:1.6\tSO:coordinate", "@RG\tID:r\tSM:sample\tPL:ONT"]
    rows += [f"@SQ\tSN:{chrom}\tLN:{len(seq)}" for chrom, seq in sequences.items()]
    for chrom, positions in (("chr20", [1000]), ("chrX", [boundary, boundary + 1])):
        sample_bases = list(sequences[chrom])
        for pos in positions:
            sample_bases[pos - 1] = "A" if sample_bases[pos - 1] != "A" else "C"
        sample = "".join(sample_bases)
        for pos in positions:
            for i in range(30):
                start = pos - 70 + i
                read = sample[start - 1 : start + 149]
                rows.append(
                    f"{chrom}_{pos}_{i}\t0\t{chrom}\t{start}\t60\t150M\t*\t0\t0"
                    f"\t{read}\t{'I' * 150}\tRG:Z:r"
                )
    bam = sam_to_bam(rows, tmp_path / "sample.bam")
    return SimpleNamespace(ref=ref, bam=Path(bam), root=tmp_path, boundary=boundary)


@pytest.mark.parametrize("sex", ["Male", "Female", "Unknown"])
@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("caller", ["gatk", "clair3", "deepvariant"])
def test_sex_par_850_caller_genotypes_preserve_par_and_autosomes(
    sex_par, caller, sex, bounded, request
):
    if sex != "Unknown":
        run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", sex)
    common = {
        "input": sex_par.bam,
        "ref": sex_par.ref,
        "outdir": sex_par.root / "calls",
        "threads": 1,
        "region": f"chr20:900-1100,chrX:{sex_par.boundary - 200}-{sex_par.boundary + 200}"
        if bounded
        else None,
    }
    if caller == "gatk":
        request.getfixturevalue("gatk")
        result = api.vcf.gatk(VcfGatkParams(**common))
    elif caller == "clair3":
        request.getfixturevalue("linux_x86")
        request.getfixturevalue("fake_clair3")
        model = make_model(sex_par.root, "custom")
        result = api.vcf.clair3(VcfClair3Params(**common, model=str(model)))
    else:
        fake = request.getfixturevalue("dv")
        if bounded:
            common["region"] = str(common["region"]).replace(",", " ")
        result = api.vcf.deepvariant(VcfDeepvariantParams(**common, model_type="WGS"))
        plans = fake.deepvariant.runs("run_deepvariant")
        if sex == "Male":
            # contract: only sex-chromosome non-PAR is haploid, never autosomes.
            assert plans[-1]["opts"]["haploid_contigs"] == "chrX"
    assert result.status == "ok", result.error
    observed = [
        (c[0], c[1], c[-1].replace("|", "/"))
        for c in vcf_calls(sex_par.root / "calls" / f"{caller}.vcf.gz")
    ]
    assert observed == [
        ("chr20", "1000", "1/1"),
        (
            "chrX",
            str(sex_par.boundary),
            "1" if sex == "Male" and caller != "deepvariant" else "1/1",
        ),
        ("chrX", str(sex_par.boundary + 1), "1/1"),
    ]
    assert all(".ploidy_" not in str(output.path) for output in result.outputs)
    if sex == "Unknown":
        assert any(w.code == "caller_sex_unknown" for w in result.warnings)


def test_sex_par_850_gpu_gatk_refuses_unsupported_haploid_policy(sex_par):
    run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", "Male")
    result = api.vcf.gatk(
        VcfGatkParams(
            input=sex_par.bam,
            ref=sex_par.ref,
            outdir=sex_par.root / "refused",
            region="chrX",
            gpu=True,
            threads=1,
        ),
        check=False,
    )
    assert result.status == "failed"
    assert "sex/PAR ploidy" in str(result.error)
    assert not (sex_par.root / "refused" / "gatk.vcf.gz").exists()


def test_sex_par_850_native_deepvariant_refuses_unsupported_policy(sex_par, request):
    request.getfixturevalue("mac_native")
    run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", "Male")
    result = api.vcf.deepvariant(
        VcfDeepvariantParams(
            input=sex_par.bam,
            ref=sex_par.ref,
            outdir=sex_par.root / "refused",
            region="chrX",
            model_type="WGS",
            threads=1,
        ),
        check=False,
    )
    assert result.status == "failed"
    assert "wgse-deepvariant does not support this sex/PAR" in str(result.error)
    assert not (sex_par.root / "refused" / "deepvariant.vcf.gz").exists()


@pytest.mark.parametrize("caller", ["gatk", "clair3", "deepvariant"])
def test_sex_par_850_single_sex_custom_table_is_not_silently_diploid(
    sex_par, caller, request
):
    for build in ("hg19", "hg38"):
        (sex_par.root / f"ploidy_{build}.txt").write_text("chr20 1 1000 F 1\n")
    run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", "Male")
    common = {
        "input": sex_par.bam,
        "ref": sex_par.ref,
        "outdir": sex_par.root / "refused",
        "region": "chr20",
        "threads": 1,
    }
    if caller == "gatk":
        result = api.vcf.gatk(VcfGatkParams(**common), check=False)
    elif caller == "clair3":
        request.getfixturevalue("linux_x86")
        request.getfixturevalue("fake_clair3")
        result = api.vcf.clair3(VcfClair3Params(**common), check=False)
    else:
        request.getfixturevalue("dv")
        result = api.vcf.deepvariant(
            VcfDeepvariantParams(**common, model_type="WGS"), check=False
        )
    assert result.status == "failed"
    assert "single-sex" in str(result.error)
    assert not (sex_par.root / "refused" / f"{caller}.vcf.gz").exists()


def test_sex_par_850_dry_run_cannot_rewrite_an_interrupted_runs_policy(
    sex_par, request
):
    fake = request.getfixturevalue("dv")
    run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", "Male")
    fake.deepvariant.fail("call_variants")
    params = VcfDeepvariantParams(
        input=sex_par.bam,
        ref=sex_par.ref,
        outdir=sex_par.root / "calls",
        region="chrX",
        model_type="WGS",
        threads=1,
    )
    result = api.vcf.deepvariant(params, check=False)
    assert result.status == "failed"
    # contract: the PAR BED recorded by a caller must remain its original policy.
    original = Path(
        fake.deepvariant.runs("run_deepvariant")[-1]["opts"]["par_regions_bed"]
    )
    content = original.read_text()
    from dataclasses import replace

    planned = api.vcf.deepvariant(
        replace(
            params,
            dry_run=True,
            region=f"chrX:{sex_par.boundary - 20}-{sex_par.boundary + 50}",
        )
    )
    assert planned.status == "ok"
    assert original.read_text() == content
