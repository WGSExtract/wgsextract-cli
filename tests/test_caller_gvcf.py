"""--reference-evidence gvcf for GATK and Clair3 (#863 phase 3): real
fixture alignments, samtools and bcftools; only the callers are faked."""

import gzip
import random
import sys
from collections import Counter
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
from tests.test_clair3 import linux_x86 as linux_x86
from tests.test_clair3 import make_model
from tests.test_gatk_scatter import gatk as gatk
from wgsextract_cli import api
from wgsextract_cli.api.vcf import VcfClair3Params, VcfGatkParams
from wgsextract_cli.core.reference_evidence import (
    INDEX_ROLE,
    ROLE,
    ReferenceEvidence,
    State,
)
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.core.variant_confidence import Thresholds

pytestmark = needs_htslib

# bcftools' GRCh38 rules: a man's chrX is haploid up to 9,999.
PAR_START = 10000
LENGTH = 12000


@pytest.fixture
def sample(tmp_path):
    """hg38-named chr20, chrX and chrY (no reads on chrY), and a chrM no
    caller is asked about; a het SNP at chr20:1000, a hom-alt SNP at
    chrX:9,999 (the last haploid base) and a het SNP at chrX:10,000."""
    rng = random.Random(863)
    sequences = {
        chrom: "".join(rng.choice("ACGT") for _ in range(LENGTH))
        for chrom in ("chr20", "chrX", "chrY")
    }
    sequences["chrM"] = "".join(rng.choice("ACGT") for _ in range(500))
    ref = write_fasta(tmp_path / "hg38.fa", sequences)
    rows = ["@HD\tVN:1.6\tSO:coordinate", "@RG\tID:r\tSM:sample\tPL:ONT"]
    rows += [f"@SQ\tSN:{chrom}\tLN:{len(seq)}" for chrom, seq in sequences.items()]
    alt = {
        ("chr20", 1000): "het",
        ("chrX", PAR_START - 1): "hom",
        ("chrX", PAR_START): "het",
    }
    for chrom in ("chr20", "chrX"):
        seq = sequences[chrom]
        for i, start in enumerate(range(700, LENGTH - 300, 10)):
            bases = list(seq[start - 1 : start + 149])
            for (contig, pos), kind in alt.items():
                if contig == chrom and start <= pos < start + 150:
                    if kind == "hom" or i % 2:
                        bases[pos - start] = "A" if bases[pos - start] != "A" else "C"
            rows.append(
                f"{chrom}_{i}\t0\t{chrom}\t{start}\t60\t150M\t*\t0\t0"
                f"\t{''.join(bases)}\t{'I' * 150}\tRG:Z:r"
            )
    bam = sam_to_bam(rows, tmp_path / "sample.bam")
    return SimpleNamespace(ref=ref, bam=Path(bam), root=tmp_path, sequences=sequences)


def call(sample, caller, request, sex, region=None, **extra):
    run_wgsextract("sample", "set", "--input", str(sample.bam), "--sex", sex)
    common = {
        "input": sample.bam,
        "ref": sample.ref,
        "outdir": sample.root / "calls",
        "threads": 1,
        "region": region,
        "reference_evidence": "gvcf",
        **extra,
    }
    if caller == "gatk":
        return api.vcf.gatk(VcfGatkParams(**common), check=False)
    # The fake first: it skips on Windows, which linux_x86 would hide.
    request.getfixturevalue("fake_clair3")
    request.getfixturevalue("linux_x86")
    model = make_model(sample.root, "custom")
    return api.vcf.clair3(VcfClair3Params(**common, model=str(model)), check=False)


def records(gvcf):
    with gzip.open(gvcf, "rt") as text:
        return [line.rstrip("\n").split("\t") for line in text if line[0] != "#"]


def state(gvcf, contig, position):
    spans = ReferenceEvidence(str(gvcf)).spans(
        Thresholds(10, 20, 20, 30), [(contig, position, position)]
    )
    return next(iter(spans)).state


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("sex", ["Male", "Female"])
@pytest.mark.parametrize("caller", ["gatk", "clair3"])
def test_partitioned_gvcf_is_cut_at_the_par_edge_and_records_excluded_bases_863(
    sample, caller, sex, bounded, request
):
    """Issue #863: a man's chrX is called haploid up to the PAR and diploid
    after it, in separate runs whose reference blocks can run past their
    intervals. The gathered gVCF must claim each base once, in the ploidy
    of its partition (a cut block starting on its own reference base), and
    record a woman's chrY as excluded by policy, not
    as bases nobody could read."""
    if caller == "gatk":
        request.getfixturevalue("gatk").set(block_padding=300)
    region = "chr20:500-1500,chrX:9000-11000,chrY:100-600" if bounded else None
    result = call(sample, caller, request, sex, region)

    assert result.status == "ok", result.error
    outputs = {output.role: output for output in result.outputs}
    gvcf = outputs[ROLE]
    assert Path(gvcf.path).name == f"{caller}.g.vcf.gz"
    assert outputs[INDEX_ROLE].path == gvcf.path + ".tbi"
    assert all(".ploidy_" not in str(output.path) for output in result.outputs)
    blocks: Counter[tuple[str, int]] = Counter()
    genotypes = {}
    for fields in records(gvcf.path):
        if fields[4] != "<NON_REF>":
            continue
        end = int(fields[7].split("END=")[1].split(";")[0])
        assert fields[3] == sample.sequences[fields[0]][int(fields[1]) - 1]
        for position in range(int(fields[1]), end + 1):
            blocks[fields[0], position] += 1
            genotypes[fields[0], position] = fields[9].split(":")[0]
    assert max(blocks.values()) == 1
    haploid = sex == "Male"
    assert genotypes["chrX", PAR_START - 200] == ("0" if haploid else "0/0")
    assert genotypes["chrX", PAR_START + 200] == "0/0"
    assert state(gvcf.path, "chrX", PAR_START - 1) is State.VARIANT
    assert state(gvcf.path, "chr20", 1200) is State.CALLABLE_REFERENCE
    if sex == "Female":
        assert gvcf.metrics["ploidy_zero"] == (
            "chrY:100-600" if bounded else f"chrY:1-{LENGTH}"
        )
        assert state(gvcf.path, "chrY", 300) is State.EXCLUDED_BY_POLICY
    else:
        assert state(gvcf.path, "chrY", 300) is State.UNCOVERED
    if caller == "clair3":
        assert any(w.code == "clair3_gvcf_cost" for w in result.warnings)
        # Clair3 calls chr1-22, X and Y unless asked: chrM wasn't assessed.
        assert state(gvcf.path, "chrM", 100) is State.NOT_ASSESSED
    else:
        assert gvcf.metrics["bands"] == "10,20,30,60"
        expected = State.NOT_ASSESSED if bounded else State.UNCOVERED
        assert state(gvcf.path, "chrM", 100) is expected
    calls = [
        (c[0], c[1], c[-1].replace("|", "/"))
        for c in vcf_calls(sample.root / "calls" / f"{caller}.vcf.gz")
    ]
    assert calls == [
        ("chr20", "1000", "0/1"),
        ("chrX", str(PAR_START - 1), "1" if haploid else "1/1"),
        ("chrX", str(PAR_START), "0/1"),
    ]


@pytest.mark.parametrize("option", ["gpu", "scatter"])
def test_gatk_gvcf_refuses_gpu_and_scatter_before_calling_863(
    sample, gatk, request, option
):
    """Issue #863: GATK's gVCF comes from one CPU HaplotypeCaller run, so
    --gpu and --scatter are refused before anything runs."""
    result = call(sample, "gatk", request, "Male", "chr20:500-1500", **{option: True})

    assert result.status == "failed"
    assert "--reference-evidence" in str(result.error)
    assert gatk.runs() == []
    assert not (sample.root / "calls" / "gatk.vcf.gz").exists()


def test_a_clair3_partition_without_reads_reads_uncovered_863(sample, request):
    """Issue #863: Clair3 writes no gVCF for a partition without reads (here
    a man's chrY, haploid alone); its bases must read as uncovered, not fail
    the run or read as not assessed."""
    result = call(sample, "clair3", request, "Male", "chr20:500-1500,chrY:100-600")

    assert result.status == "ok", result.error
    gvcf = {output.role: output for output in result.outputs}[ROLE].path
    assert state(gvcf, "chrY", 300) is State.UNCOVERED
    assert state(gvcf, "chr20", 1200) is State.CALLABLE_REFERENCE


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows locks the half-written file instead of failing the write",
)
def test_a_gvcf_that_cant_be_written_reports_bcftools_error_863(sample, tmp_path):
    """#1009 review: when bcftools can't write the cut gVCF (a full disk, or
    here a folder in its place), the error names the gVCF and carries
    bcftools' own message, not a bare broken pipe."""
    from wgsextract_cli.commands.vcf.caller_ploidy import PloidyInterval
    from wgsextract_cli.commands.vcf.reference_evidence import clip_gvcf

    rows = ["##fileformat=VCFv4.2", "##contig=<ID=chr20,length=12000>"]
    rows.append("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts")
    rows += [
        f"chr20\t{p}\t.\tA\t<NON_REF>\t.\t.\tEND={p}\tGT\t0/0" for p in range(1, 5001)
    ]
    source = tmp_path / "raw.vcf"
    source.write_text("\n".join(rows) + "\n")
    target = tmp_path / "cut.g.vcf.gz"
    target.mkdir()

    with pytest.raises(WGSExtractError, match="raw.vcf to its partition failed: .+"):
        clip_gvcf(
            str(source), [PloidyInterval("chr20", 0, 12000, 2)], sample.ref, str(target)
        )
