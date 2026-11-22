"""The desktop viewer must use local CLI reference evidence, never CRAM URs."""

import json
import re
import subprocess
import sys
from pathlib import Path

from tests.htslib_helpers import needs_tools, sam_to_bam, write_fasta
from tests.smoke_utils import state_directory_setting
from wgsextract_cli.core.utils import run_command

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "gui/examples/WGSExtract/scripts/viewer_reference.py"
)


def parse_locus(locus: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"([^:]+):(\d+)-(\d+)", locus)
    assert match is not None, f"Unexpected locus format: {locus!r}"
    return match.group(1), int(match.group(2)), int(match.group(3))


def resolve_viewer(source: Path, config: Path, *extra_args: str) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(config), *extra_args],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed: object = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    assert all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    )
    return {str(key): str(value) for key, value in parsed.items()}


@needs_tools("samtools", "bcftools")
def test_1090_viewer_matches_bam_cram_vcf_with_cli_library(tmp_path: Path) -> None:
    library = tmp_path / "references"
    library.mkdir()
    fasta = Path(
        write_fasta(
            library / "exact.fa",
            {"chrT": "ACGT" * 1000, "chrU": "GCTA" * 100, "chrV": "ACGT"},
        )
    )
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + f"reference_library = {json.dumps(str(library))}\n",
        encoding="utf-8",
    )
    bam = Path(
        sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chrT\tLN:4000",
                "@SQ\tSN:chrU\tLN:400",
                f"one\t0\tchrT\t101\t60\t100M\t*\t0\t0\t{'ACGT' * 25}\t{'I' * 100}",
            ],
            tmp_path / "reads.bam",
        )
    )
    cram = tmp_path / "reads.cram"
    run_command(["samtools", "view", "-C", "-T", str(fasta), "-o", str(cram), str(bam)])
    dictionary = library / "exact.dict"
    run_command(["samtools", "dict", "-o", str(dictionary), str(fasta)])
    vcf = tmp_path / "variants.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chrT,length=4000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chrT\t101\t.\tA\tG\t60\tPASS\t.\n",
        encoding="utf-8",
    )
    gvcf = tmp_path / "variants.gvcf.gz"
    run_command(["bcftools", "view", "-Oz", "-o", str(gvcf), str(vcf)])
    for source in [bam, cram, vcf, gvcf]:
        result = resolve_viewer(source, config)
        assert Path(result["fasta"]) == fasta
        contig, start, end = parse_locus(result["locus"])
        assert contig == "chrT"
        assert end - start + 1 == 100
        if source == bam:
            # sam_to_bam indexes reads.bam; its only read is at 101-200.
            assert (start, end) == (101, 200)
        else:
            assert 1000 <= start and end <= 3000
    dictionary.unlink()
    assert resolve_viewer(cram, config) == {"problem": "referenceUnverified"}
    wrong = Path(
        write_fasta(
            tmp_path / "wrong.fa", {"chrT": "TGCA" * 1000, "chrU": "GCTA" * 100}
        )
    )
    run_command(["samtools", "dict", "-o", str(dictionary), str(wrong)])
    assert resolve_viewer(cram, config) == {"problem": "referenceMissing"}
    fasta.unlink()
    wrong.unlink()
    assert resolve_viewer(bam, config) == {"problem": "referenceMissing"}


@needs_tools("samtools")
def test_viewer_default_locus_opens_100bp_middle_window_with_reads(
    tmp_path: Path,
) -> None:
    library = tmp_path / "references"
    library.mkdir()
    fasta = Path(
        write_fasta(
            library / "exact.fa",
            {"chrT": "ACGT" * 1000, "chrU": "GCTA" * 100},
        )
    )
    dictionary = library / "exact.dict"
    run_command(["samtools", "dict", "-o", str(dictionary), str(fasta)])
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + f"reference_library = {json.dumps(str(library))}\n",
        encoding="utf-8",
    )

    # Indexed BAM and CRAM covered across the middle of chrT: opens a random
    # 100 bp window in the middle [1000, 3000].
    middle_reads = [
        f"r{pos}\t0\tchrT\t{pos}\t60\t100M\t*\t0\t0\t{'ACGT' * 25}\t{'I' * 100}"
        for pos in range(1000, 2950, 50)
    ]
    covered_bam = Path(
        sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chrT\tLN:4000",
                "@SQ\tSN:chrU\tLN:400",
                *middle_reads,
            ],
            tmp_path / "covered.bam",
        )
    )
    run_command(["samtools", "index", str(covered_bam)])
    covered_cram = tmp_path / "covered.cram"
    run_command(
        [
            "samtools",
            "view",
            "-C",
            "-T",
            str(fasta),
            "-o",
            str(covered_cram),
            str(covered_bam),
        ]
    )
    run_command(["samtools", "index", str(covered_cram)])

    seen_starts: set[int] = set()
    for source in [covered_bam, covered_cram]:
        for _ in range(5):
            result = resolve_viewer(source, config)
            assert Path(result["fasta"]) == fasta
            contig, start, end = parse_locus(result["locus"])
            assert contig == "chrT"
            assert end - start + 1 == 100
            assert 1000 <= start and end <= 3000
            seen_starts.add(start)
    assert len(seen_starts) > 1, "Default middle locus should vary across opens."

    # Sparse indexed BAM and CRAM with reads only near the start fall back to a
    # 100 bp window covering the first mapped read rather than an empty gap.
    sparse_bam = Path(
        sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chrT\tLN:4000",
                "@SQ\tSN:chrU\tLN:400",
                f"early\t0\tchrT\t101\t60\t100M\t*\t0\t0\t{'ACGT' * 25}\t{'I' * 100}",
            ],
            tmp_path / "sparse.bam",
        )
    )
    run_command(["samtools", "index", str(sparse_bam)])
    sparse_cram = tmp_path / "sparse.cram"
    run_command(
        [
            "samtools",
            "view",
            "-C",
            "-T",
            str(fasta),
            "-o",
            str(sparse_cram),
            str(sparse_bam),
        ]
    )
    run_command(["samtools", "index", str(sparse_cram)])
    assert resolve_viewer(sparse_bam, config)["locus"] == "chrT:101-200"
    assert resolve_viewer(sparse_cram, config)["locus"] == "chrT:101-200"

    # Empty / all-unmapped indexed BAM and CRAM return a 100 bp middle locus
    # without scanning the file.
    unmapped_bam = Path(
        sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chrT\tLN:4000",
                "@SQ\tSN:chrU\tLN:400",
                f"u1\t4\t*\t0\t0\t*\t*\t0\t0\t{'ACGT' * 25}\t{'I' * 100}",
            ],
            tmp_path / "unmapped.bam",
        )
    )
    run_command(["samtools", "index", str(unmapped_bam)])
    unmapped_cram = tmp_path / "unmapped.cram"
    run_command(
        [
            "samtools",
            "view",
            "-C",
            "-T",
            str(fasta),
            "-o",
            str(unmapped_cram),
            str(unmapped_bam),
        ]
    )
    run_command(["samtools", "index", str(unmapped_cram)])
    for source in [unmapped_bam, unmapped_cram]:
        contig, start, end = parse_locus(resolve_viewer(source, config)["locus"])
        assert contig == "chrT"
        assert end - start + 1 == 100
        assert 1000 <= start and end <= 3000

    # Explicit-locus callers pass --no-locus to skip probing.
    assert resolve_viewer(covered_bam, config, "--no-locus")["locus"] == "chrT:1-100"

    # Contig shorter than 100 bp opens the whole contig.
    tiny_fasta = Path(write_fasta(library / "tiny.fa", {"chrTiny": "ACGT" * 10}))
    run_command(["samtools", "dict", "-o", str(library / "tiny.dict"), str(tiny_fasta)])
    tiny_bam = Path(
        sam_to_bam(
            [
                "@HD\tVN:1.6\tSO:coordinate",
                "@SQ\tSN:chrTiny\tLN:40",
                f"t1\t0\tchrTiny\t1\t60\t40M\t*\t0\t0\t{'ACGT' * 10}\t{'I' * 40}",
            ],
            tmp_path / "tiny.bam",
        )
    )
    run_command(["samtools", "index", str(tiny_bam)])
    assert resolve_viewer(tiny_bam, config)["locus"] == "chrTiny:1-40"
