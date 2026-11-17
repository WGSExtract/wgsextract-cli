"""Real htslib decoding for readers which ignore their supplied reference."""

import gzip
import os
import shutil

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam, write_fasta
from wgsextract_cli.core.alignment.cram_reference import cram_reference_cache
from wgsextract_cli.core.utils import WGSExtractError, run_command


@needs_tools("samtools")
@pytest.mark.parametrize(
    "suffix", ["", ".gz", ".bgz"], ids=["plain", "gzip", "bgzf-name"]
)
def test_1061_missing_ur_decodes_from_local_reference(tmp_path, monkeypatch, suffix):
    encoding = write_fasta(
        tmp_path / "gone.fa", {"chr1": "aCgT" * 1000, "chr2": "TGCA" * 1000}
    )
    reference = tmp_path / "reference.fa"
    shutil.copyfile(encoding, reference)
    shutil.copyfile(str(encoding) + ".fai", str(reference) + ".fai")
    bam = sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:4000",
            "@SQ\tSN:chr2\tLN:4000",
            f"one\t0\tchr1\t101\t60\t100M\t*\t0\t0\t{'ACGT' * 25}\t{'I' * 100}",
            f"two\t0\tchr2\t101\t60\t100M\t*\t0\t0\t{'TGCA' * 25}\t{'I' * 100}",
        ],
        tmp_path / "reads.bam",
    )
    cram = str(tmp_path / "reads.cram")
    run_command(["samtools", "view", "-C", "-T", str(encoding), "-o", cram, bam])
    for path in tmp_path.glob("gone.fa*"):
        path.unlink()
    if suffix:
        with gzip.open(str(reference) + suffix, "wb") as target:
            target.write(reference.read_bytes())
        shutil.copyfile(str(reference) + ".fai", str(reference) + suffix + ".fai")
        reference = reference.with_name(reference.name + suffix)
    monkeypatch.setenv("REF_PATH", str(tmp_path / "missing"))
    monkeypatch.setenv("REF_CACHE", str(tmp_path / "missing-cache"))
    before = (os.environ["REF_PATH"], os.environ["REF_CACHE"])
    work = tmp_path / (
        "cache:100%s with spaces" if os.name != "nt" else "cache with spaces"
    )
    work.mkdir()
    with cram_reference_cache(cram, str(reference), work) as cache:
        # No -T: this is the reader behaviour that broke native DeepVariant.
        result = run_command(
            ["samtools", "view", cram],
            capture_output=True,
            env={**os.environ, "REF_PATH": f"{cache.name}/%s", "REF_CACHE": ""},
            cwd=work,
        )
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        assert [(row[0], row[9]) for row in rows] == [
            ("one", "ACGT" * 25),
            ("two", "TGCA" * 25),
        ]
    assert list(work.iterdir()) == []
    assert (os.environ["REF_PATH"], os.environ["REF_CACHE"]) == before
    wrong = write_fasta(
        tmp_path / "wrong.fa", {"chr1": "TGCA" * 1000, "chr2": "TGCA" * 1000}
    )
    with (
        pytest.raises(WGSExtractError, match="M5"),
        cram_reference_cache(cram, str(wrong), work),
    ):
        pytest.fail("A mismatched reference must not reach the native reader")
    assert list(work.iterdir()) == []
