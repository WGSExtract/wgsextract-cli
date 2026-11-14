"""Review regressions for sex aliases, exclusions and policy publication."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fake_tools import require_bare_name_fakes
from tests.htslib_helpers import (
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from tests.test_caller_ploidy import sex_par as sex_par
from tests.test_clair3 import fake_clair3 as fake_clair3
from tests.test_clair3 import make_model
from tests.test_deepvariant import dv as dv
from tests.test_deepvariant import linux_x86 as linux_x86
from tests.test_gatk_scatter import gatk as gatk
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfClair3Params, VcfDeepvariantParams, VcfGatkParams
from wgsextract_cli.commands.vcf.caller_ploidy import PloidyInterval, write_intervals
from wgsextract_cli.core.sample_workspace import SampleWorkspace
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


def _sample(folder: Path, names: tuple[str, ...], build: str):
    folder.mkdir()
    seq = "ACGT" * 750
    ref = write_fasta(folder / f"{build}.fa", dict.fromkeys(names, seq))
    rows = ["@HD\tVN:1.6\tSO:coordinate", "@RG\tID:r\tSM:S\tPL:ONT"]
    rows += [f"@SQ\tSN:{name}\tLN:3000" for name in names]
    sample = seq[:999] + "A" + seq[1000:]
    for chrom in names:
        for i in range(30):
            start = 920 + i
            rows.append(
                f"{chrom}_{i}\t0\t{chrom}\t{start}\t60\t150M\t*\t0\t0"
                f"\t{sample[start - 1 : start + 149]}\t{'I' * 150}\tRG:Z:r"
            )
    bam = sam_to_bam(rows, folder / "s.bam")
    return SimpleNamespace(ref=ref, bam=Path(bam))


@pytest.mark.parametrize("caller", ["gatk", "deepvariant", "clair3"])
@pytest.mark.parametrize("case", ["refseq-male", "uppercase-female", "refseq-unknown"])
def test_sex_par_alias_y_exclusion_and_mitochondrial_calls(
    sex_par, caller, case, request
):
    require_bare_name_fakes()
    old = sex_par.ref.stem == "hg19"
    if case == "uppercase-female":
        x, y, mt, sex = "CHRX", "CHRY", "M", "Female"
    else:
        x = "NC_000023.10" if old else "NC_000023.11"
        y = "NC_000024.9" if old else "NC_000024.10"
        mt = "NC_012920.1" if case == "refseq-male" else "chrMT"
        sex = "Male" if case == "refseq-male" else None
    sample = _sample(sex_par.root / "aliases", ("chr20", x, y, mt), sex_par.ref.stem)
    if sex:
        run_wgsextract("sample", "set", "--input", str(sample.bam), "--sex", sex)
    out = sex_par.root / "calls with spaces, and commas"
    common = {"input": sample.bam, "ref": sample.ref, "outdir": out, "threads": 1}
    if caller == "gatk":
        request.getfixturevalue("gatk")
        result = api.vcf.gatk(VcfGatkParams(**common))
    elif caller == "clair3":
        request.getfixturevalue("linux_x86")
        request.getfixturevalue("fake_clair3")
        model = make_model(sex_par.root, "custom")
        result = api.vcf.clair3(
            VcfClair3Params(
                **common, model=str(model), region=",".join(("chr20", x, y, mt))
            )
        )
    else:
        request.getfixturevalue("dv")
        result = api.vcf.deepvariant(VcfDeepvariantParams(**common, model_type="WGS"))
    assert result.status == "ok", result.error
    calls = {
        row[0]: row[-1].replace("|", "/") for row in vcf_calls(out / f"{caller}.vcf.gz")
    }
    haploid = "1/1" if caller == "deepvariant" else "1"
    assert calls["chr20"] == "1/1" and calls[mt] == haploid
    assert calls[x] == (haploid if sex == "Male" else "1/1")
    if sex == "Male":
        assert calls[y] == haploid
    else:
        assert y not in calls
    if sex is None:
        assert any(w.code == "caller_sex_unknown" for w in result.warnings)


def test_custom_open_bound_is_split_at_its_finite_end(sex_par, request):
    require_bare_name_fakes()
    request.getfixturevalue("gatk")
    (sex_par.root / f"ploidy_{sex_par.ref.stem}.txt").write_text(
        f"chrX * {sex_par.boundary} M 1\n* * * * 2\n"
    )
    run_wgsextract("sample", "set", "--input", str(sex_par.bam), "--sex", "Male")
    out = sex_par.root / "open-bound"
    api.vcf.gatk(
        VcfGatkParams(input=sex_par.bam, ref=sex_par.ref, outdir=out, threads=1)
    )
    calls = {row[1]: row[-1] for row in vcf_calls(out / "gatk.vcf.gz")}
    assert calls[str(sex_par.boundary)] == "1"
    assert calls[str(sex_par.boundary + 1)] == "0/1"


def test_installed_neutral_autosomal_table_does_not_infer_sex(sex_par, request):
    require_bare_name_fakes()
    request.getfixturevalue("gatk")
    (sex_par.root / f"ploidy_{sex_par.ref.stem}.txt").write_text(
        "chr20 1 1500 M 2\n* * * M 2\n* * * F 2\n"
    )
    out = sex_par.root / "neutral"
    result = api.vcf.gatk(
        VcfGatkParams(
            input=sex_par.bam, ref=sex_par.ref, outdir=out, region="chr20", threads=1
        )
    )
    assert vcf_calls(out / "gatk.vcf.gz")[0][-1] == "0/1"
    assert not any(w.code == "caller_sex_unknown" for w in result.warnings)
    assert SampleWorkspace(sex_par.root).detected("sex", str(sex_par.bam)) is None


def test_concurrent_policy_publication_never_exposes_partial_beds(tmp_path):
    intervals = [PloidyInterval("chrX", i * 2, i * 2 + 1, 1) for i in range(1000)]

    def publish(_):
        path = Path(write_intervals(tmp_path / "policy.bed", intervals))
        return path.read_bytes()

    with ThreadPoolExecutor(max_workers=4) as pool:
        observed = list(pool.map(publish, range(16)))
    assert all(content == observed[0] for content in observed)
    assert len(observed[0].splitlines()) == 1000
    assert b"\r" not in observed[0]


def test_whole_reference_clair3_ploidy_does_not_add_alt_or_decoy_calls(
    sex_par, request
):
    require_bare_name_fakes()
    request.getfixturevalue("linux_x86")
    request.getfixturevalue("fake_clair3")
    extra = ("chrUn_decoy", "chr1_KI000001v1_alt")
    sequences = {**sex_par.sequences, **dict.fromkeys(extra, "ACGT" * 750)}
    ref = write_fasta(sex_par.root / f"scope_{sex_par.ref.stem}.fa", sequences)
    headers = [row for row in sex_par.rows if row.startswith("@")]
    headers += [f"@SQ\tSN:{name}\tLN:3000" for name in extra]
    rows = headers + [row for row in sex_par.rows if not row.startswith("@")]
    for chrom in extra:
        seq = sequences[chrom]
        mutated = seq[:999] + "A" + seq[1000:]
        for i in range(30):
            start = 920 + i
            rows.append(
                f"{chrom}_{i}\t0\t{chrom}\t{start}\t60\t150M\t*\t0\t0"
                f"\t{mutated[start - 1 : start + 149]}\t{'I' * 150}\tRG:Z:r"
            )
    bam = Path(sam_to_bam(rows, sex_par.root / "with-decoys.bam"))
    run_wgsextract("sample", "set", "--input", str(bam), "--sex", "Male")
    model = make_model(sex_par.root, "custom")
    out = sex_par.root / "no-decoys"
    api.vcf.clair3(
        VcfClair3Params(input=bam, ref=ref, outdir=out, model=str(model), threads=1)
    )
    assert {row[0] for row in vcf_calls(out / "clair3.vcf.gz")} == {"chr20", "chrX"}


def test_whole_genome_cram30_ploidy_preserves_direct_gatk_input(sex_par, request):
    require_bare_name_fakes()
    fake = request.getfixturevalue("gatk")
    fake.set(reads_cram=True, require_cram=True)
    cram = sex_par.root / "compatible.cram"
    run_command(
        [
            "samtools",
            "view",
            "-O",
            "CRAM,version=3.0",
            "-T",
            str(sex_par.ref),
            "-o",
            str(cram),
            str(sex_par.bam),
        ]
    )
    run_command(["samtools", "index", str(cram)])
    run_wgsextract("sample", "set", "--input", str(cram), "--sex", "Male")
    out = sex_par.root / "direct-cram"
    result = api.vcf.gatk(
        VcfGatkParams(input=cram, ref=sex_par.ref, outdir=out, threads=1)
    )
    assert result.status == "ok"
    calls = {row[1]: row[-1] for row in vcf_calls(out / "gatk.vcf.gz")}
    assert calls[str(sex_par.boundary)] == "1"
    assert calls[str(sex_par.boundary + 1)] == "0/1"
