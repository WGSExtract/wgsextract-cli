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
from tests.test_clair3 import clair3_gpu as clair3_gpu
from tests.test_clair3 import fake_clair3 as fake_clair3
from tests.test_clair3 import make_model
from tests.test_gatk_scatter import gatk as gatk
from tests.test_parabricks import gpu as gpu
from tests.test_parabricks import parabricks_x86 as parabricks_x86
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
    """GRCh38 chr20, chrX and chrY (no reads on chrY), and a chrM no
    caller is asked about; a het SNP at chr20:1000, a hom-alt SNP at
    chrX:9,999 (the last haploid base) and a het SNP at chrX:10,000."""
    rng = random.Random(863)
    sequences = {
        chrom: "".join(rng.choice("ACGT") for _ in range(LENGTH))
        for chrom in ("chr20", "chrX", "chrY")
    }
    sequences["chrM"] = "".join(rng.choice("ACGT") for _ in range(500))
    ref = write_fasta(tmp_path / "hg38.fa", sequences, assembly="GRCh38")
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
    request.getfixturevalue("fake_clair3")
    model = sample.root / "custom"
    if not model.exists():
        make_model(sample.root, "custom")
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


@pytest.mark.parametrize(("gpu", "factor"), [(False, 2.4), (True, 4.3)])
def test_clair3_gvcf_cost_is_measured_and_reported_to_gpu_runs_863(
    sample, request, gpu, factor
):
    """Issue #863: on HG002 ONT chr20:10-15 Mb the gVCF made Clair3 2.4x
    slower on the CPU and 4.3x with --gpu. Preflight scales its time by that,
    and a --gpu run is told the CPU was faster there but still calls on the
    GPU."""
    request.getfixturevalue("fake_clair3")
    host = request.getfixturevalue("clair3_gpu") if gpu else None
    results = {
        evidence: call(
            sample,
            "clair3",
            request,
            "Female",
            "chr20:500-1500",
            reference_evidence=evidence,
            outdir=sample.root / evidence,
            gpu=gpu,
        )
        for evidence in ("none", "gvcf")
    }

    assert all(r.status == "ok" for r in results.values()), results
    hours = {
        evidence: r.details["host_requirement"]["approximate_hours"]
        for evidence, r in results.items()
    }
    # Each is rounded to two significant figures.
    assert hours["gvcf"] / hours["none"] == pytest.approx(factor, rel=0.1)
    assert not any(w.code == "clair3_gvcf_cost" for w in results["none"].warnings)
    (cost,) = [w for w in results["gvcf"].warnings if w.code == "clair3_gvcf_cost"]
    assert cost.details["time_factor"] == factor
    assert ("CPU was faster" in cost.message) is gpu
    if host:
        assert {r["opts"]["use_gpu"] for r in host.clair3.runs()} == {"True"}


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


def test_a_lost_clair3_gvcf_fails_when_its_contigs_have_reads_1009(sample, request):
    """#1009 review: a header-only VCF proves no variants, not no reads. When
    Clair3 writes no gVCF for a region with reads (here chr20 between the
    SNPs), the run fails rather than reading every base as uncovered."""
    request.getfixturevalue("fake_clair3").lose_gvcf()

    result = call(sample, "clair3", request, "Female", "chr20:1200-1500")

    assert result.status == "failed"
    assert "merge_output.gvcf.gz" in str(result.error)


def test_clair3_gvcf_blocks_are_cut_on_a_gzip_reference_1009(sample, request):
    """#1009 review: Clair3 reads a plain-gzip FASTA through a decompressed
    copy; cutting its partitions' blocks at the PAR edge must still find
    each cut block's reference base instead of failing after calling."""
    folder = sample.root / "gzip"
    folder.mkdir()
    ref = folder / "hg38.fa.gz"
    with open(sample.ref, "rb") as plain, gzip.open(ref, "wb") as packed:
        packed.write(plain.read())
    (folder / "hg38.dict").write_bytes(sample.ref.with_suffix(".dict").read_bytes())
    request.getfixturevalue("fake_clair3").pad_blocks(300)
    sample.ref = ref

    result = call(sample, "clair3", request, "Male", "chrX:9000-11000")

    assert result.status == "ok", result.error
    gvcf = {output.role: output for output in result.outputs}[ROLE].path
    # Blocks begin 300 bases before the region; the first is cut at 9,000.
    assert min(int(f[1]) for f in records(gvcf)) == 9000
    for fields in records(gvcf):
        assert fields[3][0] == sample.sequences["chrX"][int(fields[1]) - 1]
    assert state(gvcf, "chrX", PAR_START + 200) is State.CALLABLE_REFERENCE


@pytest.mark.parametrize("before", ["selection", "partition"])
def test_a_deletion_starting_before_a_partition_covers_its_bases_1009(
    sample, tmp_path, before
):
    """#1009 review: a deletion at 99 (REF of three bases) covers bases
    100-101. Starting before the selection, it was dropped and those bases
    read as uncovered; it must be kept, once, however many partitions it
    reaches into. Starting in a called partition, that partition keeps it."""
    from wgsextract_cli.commands.vcf.caller_ploidy import PloidyInterval
    from wgsextract_cli.commands.vcf.reference_evidence import clip_gvcf, gather_gvcf

    seq = sample.sequences["chr20"]
    deletion = f"chr20\t99\t.\t{seq[98:101]}\t{seq[98]},<NON_REF>\t30\tPASS\t.\tGT:GQ\t0/1:30"  # fmt: skip
    rows = ["##fileformat=VCFv4.2", f"##contig=<ID=chr20,length={LENGTH}>"]
    rows += ['##INFO=<ID=END,Number=1,Type=Integer,Description="End">']
    rows += ['##FORMAT=<ID=GT,Number=1,Type=String,Description="GT">']
    rows += ['##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="GQ">']
    rows += ['##FORMAT=<ID=DP,Number=1,Type=Integer,Description="DP">']
    rows += ['##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="MIN_DP">']
    rows += ['##ALT=<ID=NON_REF,Description="Any other allele">']
    rows.append("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts")
    rows += [deletion, f"chr20\t102\t.\t{seq[101]}\t<NON_REF>\t.\t.\tEND=300\tGT:DP:GQ:MIN_DP\t0/0:30:60:30"]  # fmt: skip
    source = tmp_path / "raw.vcf"
    source.write_text("\n".join(rows) + "\n")
    first = 0 if before == "partition" else 99
    partitions = [
        [PloidyInterval("chr20", first, 100, 1)],
        [PloidyInterval("chr20", 100, 300, 2)],
    ]
    parts = []
    for index, partition in enumerate(partitions):
        parts.append(str(tmp_path / f"part{index}.g.vcf.gz"))
        clip_gvcf(str(source), partition, sample.ref, parts[-1], sum(partitions, []))
    gathered = tmp_path / "gathered.g.vcf.gz"
    gather_gvcf(parts, str(tmp_path), str(gathered))
    from wgsextract_cli.core.variant_files import ensure_vcf_indexed

    ensure_vcf_indexed(str(gathered))

    assert [f[1] for f in records(gathered)].count("99") == 1
    assert state(gathered, "chr20", 100) is State.VARIANT
    assert state(gathered, "chr20", 101) is State.VARIANT
    assert state(gathered, "chr20", 200) is State.CALLABLE_REFERENCE


def test_clair3_missed_deletion_bases_arent_callable_reference_1028(tmp_path, request):
    """Issue #1028: Clair3 leaves reads that delete a base out of its depth
    and GQ, so the bases of a deletion it saw but didn't call followed its
    no-call anchor as confident 0/0 blocks and read as callable reference.
    They must read as low quality, and reference away from it stay callable."""
    rng = random.Random(1028)
    seq = "".join(rng.choice("ACGT") for _ in range(3000))
    while seq[1999:2002] in ("AAA", "CCC", "GGG", "TTT") or seq[1999] in seq[2000:2002]:
        seq = "".join(rng.choice("ACGT") for _ in range(3000))
    ref = write_fasta(tmp_path / "hg38.fa", {"chr20": seq})
    rows = ["@HD\tVN:1.6\tSO:coordinate", "@RG\tID:r\tSM:sample\tPL:ONT"]
    rows.append("@SQ\tSN:chr20\tLN:3000")
    # 50x; half the reads delete 2,001-2,002, after the anchor at 2,000.
    for i, start in enumerate(range(100, 2800, 3)):
        cigar, bases = "150M", seq[start - 1 : start + 149]
        if i % 2 and start <= 1990 and start + 150 > 2010:
            left = 2000 - start + 1
            cigar = f"{left}M2D{150 - left}M"
            bases = seq[start - 1 : 2000] + seq[2002 : start + 151]
        rows.append(
            f"r{i}\t0\tchr20\t{start}\t60\t{cigar}\t*\t0\t0\t{bases}"
            f"\t{'I' * 150}\tRG:Z:r"
        )
    sample = SimpleNamespace(
        ref=ref, bam=Path(sam_to_bam(rows, tmp_path / "sample.bam")), root=tmp_path
    )
    request.getfixturevalue("fake_clair3").miss_indels()

    result = call(sample, "clair3", request, "Female", "chr20:1500-2500")

    assert result.status == "ok", result.error
    gvcf = {output.role: output for output in result.outputs}[ROLE].path
    assert state(gvcf, "chr20", 2000) is State.NO_CALL
    for deleted in (2001, 2002):
        assert state(gvcf, "chr20", deleted) is State.LOW_QUALITY_REFERENCE
    assert state(gvcf, "chr20", 2100) is State.CALLABLE_REFERENCE


@pytest.mark.skipif(sys.platform == "win32", reason="Parabricks fakes run by shebang")
@pytest.mark.parametrize("evidence", ["none", "gvcf"])
@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("sex", ["Male", "Female"])
def test_parabricks_deepvariant_calls_sex_chromosomes_in_ploidy_partitions_1058(
    sample, gpu, sex, bounded, evidence
):
    """Issue #1058: vcf deepvariant --gpu refused every whole genome, since
    a man's non-PAR chrX/chrY (and every chrM) isn't diploid. pbrun has
    --haploid-contigs but no PAR option, so the haploid bases run apart,
    limited by an interval file, and the runs are gathered in reference
    order with a gVCF that claims each base once."""
    region = "chr20:500-1500,chrX:9000-11000,chrY:100-600" if bounded else None
    run_wgsextract("sample", "set", "--input", str(sample.bam), "--sex", sex)
    result = api.vcf.deepvariant(
        api.VcfDeepvariantParams(
            input=sample.bam, ref=sample.ref, outdir=sample.root / "calls",
            gpu=True, region=region, reference_evidence=evidence,
        ),
        check=False,
    )  # fmt: skip

    assert result.status == "ok", result.error
    runs = {
        "haploid" if "--haploid-contigs" in args else "diploid": intervals
        for (_, args), intervals in zip(gpu.runs(), gpu.intervals(), strict=True)
    }
    haploid_args = [a for _, a in gpu.runs() if "--haploid-contigs" in a]
    male = sex == "Male"
    if bounded:
        diploid = [("chr20", 499, 1500), ("chrX", PAR_START - 1, 11000)]
        haploid = [("chrX", 8999, PAR_START - 1), ("chrY", 99, 600)] if male else []
    else:
        diploid = [("chr20", 0, LENGTH), ("chrX", PAR_START - 1, LENGTH)]
        haploid = [("chrX", 0, PAR_START - 1), ("chrY", 0, LENGTH)] if male else []
        haploid.append(("chrM", 0, 500))
    if not male:
        # A woman's chrX is diploid throughout; her chrY isn't called.
        diploid[1] = ("chrX", 8999 if bounded else 0, 11000 if bounded else LENGTH)
    # contract: the PARs are diploid and only non-PAR X/Y (and chrM) haploid.
    assert runs["diploid"] == diploid
    assert runs.get("haploid", []) == haploid
    contigs = list(dict.fromkeys(c for c, _, _ in haploid))
    # contract: pbrun treats only the haploid run's own contigs as haploid.
    assert [a[a.index("--haploid-contigs") + 1] for a in haploid_args] == (
        [",".join(contigs)] if contigs else []
    )
    calls = [(c[0], int(c[1])) for c in vcf_calls(sample.root / "calls" / "deepvariant.vcf.gz")]  # fmt: skip
    assert calls == sorted(
        {(c, s + 1) for c, s, _ in diploid + haploid},
        key=lambda call: (list(sample.sequences).index(call[0]), call[1]),
    )
    outputs = {output.role: output for output in result.outputs}
    assert all(".ploidy_" not in str(output.path) for output in result.outputs)
    if evidence == "none":
        assert ROLE not in outputs
        return
    gvcf = outputs[ROLE]
    assert Path(gvcf.path).name == "deepvariant.g.vcf.gz"
    assert gvcf.metrics["engine"] == "parabricks-deepvariant"
    covered: Counter[tuple[str, int]] = Counter()
    for fields in records(gvcf.path):
        if fields[4] == "<*>":
            end = int(fields[7].split("END=")[1].split(";")[0])
            covered.update((fields[0], p) for p in range(int(fields[1]), end + 1))
    assert max(covered.values()) == 1
    assert set(covered) == {
        (c, p) for c, s, e in diploid + haploid for p in range(s + 1, e + 1)
    }
    assert state(gvcf.path, "chrX", PAR_START + 200) is State.CALLABLE_REFERENCE
    if male:
        assert (
            f"chrX:{9000 if bounded else 1}-{PAR_START - 1}=1" in gvcf.metrics["ploidy"]
        )
    else:
        assert state(gvcf.path, "chrY", 300) is State.EXCLUDED_BY_POLICY
