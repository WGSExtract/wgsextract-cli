"""The desktop viewer must use local CLI reference evidence, never CRAM URs."""

import json
import subprocess
import sys
from pathlib import Path

from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import needs_tools, sam_to_bam, write_fasta
from wgsextract_cli.core.utils import run_command

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "gui/examples/WGSExtract/scripts/viewer_reference.py"
)


def resolve_viewer(source: Path, config: Path) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(config)],
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
        assert result["locus"] == "chrT:1-1000"
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
