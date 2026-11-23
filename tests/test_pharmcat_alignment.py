"""PharmCAT reference evidence with real indexed BAM/CRAM input."""

import json
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, write_fasta
from tests.test_pharmcat import (
    REF_SEQS,
    _base,
    _release,
    _vcf,
    _view,
)
from tests.test_pharmcat import (
    pharmcat_env as pharmcat_env,
)
from wgsextract_cli import api
from wgsextract_cli.core.reference_evidence import DEPTH_ONLY, ROLE, Provenance
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.main import main

pytestmark = needs_htslib


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    release = _release(tmp_path / "pharmcat")
    definitions = release / "pharmcat_positions.vcf"
    with definitions.open("a", encoding="utf-8", newline="\n") as stream:
        for pos, ref in [
            (p, _base(p))
            for p in (110, 125, 130, 135, 140, 145, 155, 160, 175, 185, 195, 199)
        ] + [(165, _base(165) + _base(166))]:
            stream.write(
                f"chr1\t{pos}\t.\t{ref}\t{'A' if ref == 'T' else 'T'}\t.\t.\t.\n"
            )
    vcf = Path(
        _vcf(
            tmp_path / "sample.vcf.gz",
            f"{{c}}\t155\t.\t{_base(155)}\tT\t.\tPASS\t.\tGT\t./.\n"
            f"{{c}}\t190\t.\t{_base(190)}\tT\t.\tPASS\t.\tGT\t0/1\n",
        )
    )
    return release, vcf, write_fasta(tmp_path / "genome.fa", REF_SEQS)


def _alignment(
    path: Path,
    *,
    sample: str = "HG002",
    contig: str = "chr1",
    chr1_length: int = 400,
    reference: Path | None = None,
) -> Path:
    sam = path.with_suffix(".sam")
    with sam.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            f"@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:{contig}\tLN:{chr1_length}\n"
        )
        if sample:
            stream.write(f"@RG\tID:one\tSM:{sample}\n")
        rg = "\tRG:Z:one" if sample else ""
        for pos, count, alt_count, mapq, quality in (
            (110, 12, 0, 60, "I"),
            (125, 4, 0, 60, "I"),
            (130, 12, 0, 255, "I"),
            (135, 12, 0, 60, "*"),
            (140, 12, 0, 60, "I"),
            (145, 12, 1, 60, "I"),
            (155, 12, 0, 60, "I"),
            (160, 12, 0, 60, "I"),
            (165, 12, 0, 60, "I"),
            (175, 12, 0, 60, "+"),
            (185, 12, 0, 10, "I"),
        ):
            for i in range(count):
                base = _base(pos) if i >= alt_count else "C"
                stream.write(
                    f"r{pos}_{i}\t0\t{contig}\t{pos}\t{mapq}\t1M\t*\t0\t0\t"
                    f"{base}\t{quality}{rg if pos != 140 else ''}\n"
                )
            if pos == 155:
                # The deletion's anchor is outside the definition at 160.
                seq = REF_SEQS["chr1"][156:159] + REF_SEQS["chr1"][162:164]
                for i in range(12):
                    stream.write(
                        f"del{i}\t0\t{contig}\t157\t60\t3M3D2M\t*\t0\t0\t"
                        f"{seq}\t{'I' * len(seq)}{rg}\n"
                    )
        # A supported insertion anchored at 199 must not become a reference call.
        start = 198
        bases = REF_SEQS["chr1"][start - 1 : start + 8]
        for i in range(12):
            seq = f"{bases[:2]}A{bases[2:]}" if i < 6 else bases
            cigar = "2M1I7M" if i < 6 else "9M"
            stream.write(
                f"indel{i}\t0\t{contig}\t{start}\t60\t{cigar}\t*\t0\t0\t"
                f"{seq}\t{'I' * len(seq)}{rg}\n"
            )
    options = ["-C", "-T", str(reference)] if reference else ["-b"]
    run_command(["samtools", "view", *options, "-o", str(path), str(sam)])
    run_command(["samtools", "index", str(path)])
    return path


@pytest.mark.parametrize("kind", ["bam", "cram"])
def test_1477_alignment_only_fills_absent_and_covered_ref_only_sites(
    tmp_path: Path, inputs: tuple[Path, Path, Path], pharmcat_env: None, kind: str
) -> None:
    release, vcf, ref = inputs
    if kind == "cram":
        write_fasta(
            ref, {name.removeprefix("chr"): seq for name, seq in REF_SEQS.items()}
        )
    cram_ref = ref if kind == "cram" else None
    aln = _alignment(
        tmp_path / f"reads.{kind}",
        reference=cram_ref,
        contig="1" if cram_ref is not None else "chr1",
    )
    if cram_ref is not None:
        # A decoder must use the supplied FASTA, not the CRAM's original UR.
        moved = ref.with_name("moved.fa")
        ref.rename(moved)
        Path(f"{ref}.fai").rename(Path(f"{moved}.fai"))
        cram_ref = moved
    out = tmp_path / "out"
    argv = ["vcf", "pharmcat", "--vcf-input", str(vcf), "--pharmcat-dir",
            str(release), "--alignment", str(aln), "--outdir", str(out),
            "--confidence-depth", "8", "20"]  # fmt: skip
    if cram_ref is not None:
        argv.extend(("--alignment-reference", str(cram_ref)))
    main(argv)
    rows = (r.split("\t") for r in _view(release / "seen.vcf.gz", "-H"))
    assert {int(row[1]): row[9] for row in rows} == {
        110: "0/0",
        125: "./.",
        130: "./.",
        135: "./.",
        140: "./.",
        145: "./.",
        155: "./.",
        160: "./.",
        165: "./.",
        175: "./.",
        185: "./.",
        190: "0/1",
        195: "./.",
        199: "./.",
    }
    report = json.loads((out / "sample.reference-evidence.json").read_text())
    assert report["source"] == "alignment"
    assert report["made_reference"] == 1
    assert report["left_missing"] == 11
    assert report["thresholds"]["min_depth"] == 8
    assert {row["pos"] for row in report["missing_positions"]} == {
        125,
        130,
        135,
        140,
        145,
        160,
        165,
        175,
        185,
        195,
        199,
    }
    manifest_path = next(out.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    summary = manifest["details"]["pharmcat_reference_evidence"]
    assert summary == {
        key: value for key, value in report.items() if key != "missing_positions"
    }
    assert any(item["path"] == str(aln) for item in manifest["inputs"])


@pytest.mark.parametrize(
    ("sample", "length", "problem"),
    [("other", 400, "sample mismatch"), ("HG002", 401, "length")],
)
def test_1477_refuses_incompatible_alignment(
    tmp_path: Path,
    inputs: tuple[Path, Path, Path],
    pharmcat_env: None,
    sample: str,
    length: int,
    problem: str,
) -> None:
    release, vcf, _ = inputs
    aln = _alignment(tmp_path / "reads.bam", sample=sample, chr1_length=length)
    with pytest.raises(WGSExtractError, match=problem):
        run_wgsextract(
            "vcf", "pharmcat", "--vcf-input", str(vcf),
            "--pharmcat-dir", str(release), "--alignment", str(aln),
        )  # fmt: skip
    assert not (release / "seen.vcf.gz").exists()


def test_1477_refuses_vcf_assembly_without_contig_lengths(
    tmp_path: Path, inputs: tuple[Path, Path, Path], pharmcat_env: None
) -> None:
    release, _, _ = inputs
    vcf = tmp_path / "grch37.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,assembly=GRCh37>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG002\n",
        newline="\n",
    )
    aln = _alignment(tmp_path / "reads.bam")
    with pytest.raises(WGSExtractError, match="GRCh38 VCF"):
        run_wgsextract(
            "vcf", "pharmcat", "--vcf-input", str(vcf),
            "--pharmcat-dir", str(release), "--alignment", str(aln),
        )  # fmt: skip


def test_1477_explicit_alignment_overrides_companion_but_not_no_calls(
    tmp_path: Path, inputs: tuple[Path, Path, Path], pharmcat_env: None
) -> None:
    release, vcf, _ = inputs
    gvcf = _vcf(
        tmp_path / "companion.g.vcf.gz",
        "{c}\t101\t.\tA\t<*>\t.\tPASS\tEND=200;MIN_DP=30\tGT:DP\t0/0:30\n",
        header=Provenance("bcftools", "1.24", DEPTH_ONLY).header()
        + '\n##INFO=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n',
    )
    producer = CommandResult(command="vcf call")
    producer.add_output(vcf, "variants")
    producer.add_output(gvcf, ROLE)
    producer.set_manifest_stem(tmp_path / "producer")
    producer.finalize(probe_tools=False)
    producer.write_manifest()
    params = api.VcfPharmcatParams(vcf_input=vcf, pharmcat_dir=release)
    companion = api.vcf.pharmcat(params)
    assert companion.details is not None
    summary = companion.details["pharmcat_reference_evidence"]
    assert isinstance(summary, dict) and summary["source"] == "companion_gvcf"
    aln = _alignment(tmp_path / "reads.bam", sample="")
    aligned = api.vcf.pharmcat(
        api.VcfPharmcatParams(vcf_input=vcf, pharmcat_dir=release, alignment=aln)
    )
    assert all(item.role != ROLE for item in aligned.inputs)
    assert any(w.code == "alignment_sample_unverified" for w in aligned.warnings)
    rows = {
        r.split("\t")[1]: r.split("\t")[9] for r in _view(release / "seen.vcf.gz", "-H")
    }
    assert rows["125"] == "./." and rows["155"] == "./."
    with pytest.raises(UsageError, match="cannot be used together"):
        api.VcfPharmcatParams(vcf_input=vcf, alignment=aln, absent_to_ref=True)
