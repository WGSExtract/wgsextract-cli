"""Regression tests for the byte-exact FASTQ archive in #1432."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from wgsextract_cli import api
from wgsextract_cli.main import main

R1 = (
    b"@same 1:N:0:AC+TG\nACNN\n+\nII##\n"
    b"@same 1:N:0:AC+TG\nACGT\n+\nIIII\n"
    b"@same 1:N:0:AC+TG\nAAAA\n+\nIIII\n"
    b"@dual 1:N:0:AC+TG\nACG\n+\nIII\n"
    b"@dup/1 other \ttext\r\nacR.Y\r\n+some text\r\n~@%#+\r\n"
    b"@dup/1\nG\n+\n!"
)
R2 = (
    b"@same 2:N:0:AC+TG\nTNAC\n+\n#III\n"
    b"@same 2:N:0:AC+TG\nTGCA\n+\nIIII\n"
    b"@same 2:N:0:AC+TG\nTTTT\n+\nIIII\n"
    b"@dual 2:N:0:CA+GT\nTGC\n+\n#II\n"
    b"@dup/2 2:N:0:TAG-TTT\r\nNry\r\n+odd\r\nIII\r\n"
    b"@dup/2\r\nAC\n+\r\n!#"
)


def test_fastq_archive_1432_preserves_noncanonical_bytes_and_duplicate_names(
    tmp_path, capsys
):
    r1 = tmp_path / "first.fq"
    r2 = tmp_path / "second.fq.gz"
    archive = tmp_path / "reads.wgfq"
    restored = (tmp_path / "restored1.fq", tmp_path / "restored2.fq")
    r1.write_bytes(R1)
    split = R2.index(b"@dup")
    gz = gzip.compress(R2[:split]) + gzip.compress(R2[split:])
    r2.write_bytes(gz)
    assert main(
        [
            "fastq",
            "archive",
            "--r1",
            str(r1),
            "--r2",
            str(r2),
            "--output",
            str(archive),
            "--json",
        ]
    ) in (None, 0)
    result = json.loads(capsys.readouterr().out)
    assert {item["role"] for item in result["inputs"]} == {"fastq_r1", "fastq_r2"}
    assert {item["role"] for item in result["outputs"]} == {"fastq_archive"}
    result = api.fastq.restore(
        api.FastqRestoreParams(input=archive, output=restored[0], output_r2=restored[1])
    )
    assert [item.role for item in result.inputs] == ["fastq_archive"]
    assert {item.role for item in result.outputs} == {"fastq_r1", "fastq_r2"}
    assert (restored[0].read_bytes(), restored[1].read_bytes()) == (R1, R2)
    assert (r1.read_bytes(), r2.read_bytes()) == (R1, gz)
    assert archive.exists()


@pytest.mark.parametrize(
    ("offset", "detail"),
    [(20, "block checksum"), (-1, "SHA-256")],
    ids=["payload", "footer"],
)
def test_fastq_archive_1432_checks_digest_before_publishing_either_mate(
    tmp_path, offset, detail
):
    r1, r2, archive = (tmp_path / name for name in ("r1.fq", "r2.fq", "reads.wgfq"))
    r1.write_bytes(R1)
    r2.write_bytes(R2)
    api.fastq.archive(api.FastqArchiveParams(r1=r1, r2=r2, output=archive))
    contents = bytearray(archive.read_bytes())
    contents[offset] ^= 1
    archive.write_bytes(contents)
    first, second = (tmp_path / name for name in ("restored1.fq", "restored2.fq"))
    result = api.fastq.restore(
        api.FastqRestoreParams(input=archive, output=first, output_r2=second),
        check=False,
    )
    assert result.error and result.error.code == "fastq_restore_failed"
    assert detail in result.error.message
    assert not first.exists() and not second.exists()
    assert archive.read_bytes() == contents


@pytest.mark.parametrize(
    "wrong",
    (b"@beta/2\nA\n+\n!\n", b"@alpha/1\nA\n+\n!\n", b""),
    ids=["different-name", "wrong-orientation", "different-count"],
)
def test_fastq_archive_1432_rejects_wrong_pairing(tmp_path, wrong):
    r1, r2, archive = (tmp_path / name for name in ("r1.fq", "r2.fq", "reads.wgfq"))
    r1.write_bytes(b"@alpha/1\nA\n+\n!\n")
    r2.write_bytes(wrong)
    result = api.fastq.archive(
        api.FastqArchiveParams(r1=r1, r2=r2, output=archive), check=False
    )
    assert result.error and result.error.code == "fastq_archive_failed"
    assert "pair 1" in result.error.message
    assert not archive.exists()


def test_fastq_archive_1432_restores_single_end_and_refuses_to_replace_a_source(
    tmp_path,
):
    reads, archive, restored = (
        tmp_path / name for name in ("r.fq", "r.wgfq", "restored.fq")
    )
    original = b"@plain\nACGTT\n+\n!@#$%\n@plain\nNG\n+\n##\n"
    reads.write_bytes(original)
    api.fastq.archive(api.FastqArchiveParams(r1=reads, output=archive))
    result = api.fastq.restore(
        api.FastqRestoreParams(input=archive, output=reads), check=False
    )
    assert result.error and result.error.code == "fastq_output_conflict"
    assert reads.read_bytes() == original
    api.fastq.restore(api.FastqRestoreParams(input=archive, output=restored))
    assert restored.read_bytes() == original


def test_fastq_archive_1432_refuses_nested_restore_paths_before_writing(tmp_path):
    r1, r2, archive = (tmp_path / name for name in ("r1.fq", "r2.fq", "reads.wgfq"))
    r1.write_bytes(R1)
    r2.write_bytes(R2)
    api.fastq.archive(api.FastqArchiveParams(r1=r1, r2=r2, output=archive))
    first = tmp_path / "first"
    result = api.fastq.restore(
        api.FastqRestoreParams(input=archive, output=first, output_r2=first / "r2.fq"),
        check=False,
    )
    assert result.error and result.error.code == "fastq_output_conflict"
    assert not first.exists()


def test_fastq_archive_1432_failure_card_cannot_overwrite_a_mate(tmp_path):
    r1 = tmp_path / "r1.fq"
    r1.write_bytes(b"@alpha/1\nA\n+\n!\n")
    r2 = tmp_path / "r1.fq_fastq_archive.card.html"
    original = b"@beta/2\nAC\n+\n!!\n"
    r2.write_bytes(original)
    with pytest.raises(SystemExit) as error:
        main(
            [
                "fastq",
                "archive",
                "--r1",
                str(r1),
                "--r2",
                str(r2),
                "--output",
                str(tmp_path / "new.wgfq"),
                "--outdir",
                str(tmp_path),
                "--card",
            ]
        )
    assert error.value.code == 1
    assert r2.read_bytes() == original


def test_fastq_archive_1432_refuses_missing_newline_between_blocks(tmp_path):
    first, second = b"@a\nA\n+\n!", b"@b\nT\n+\n#\n"
    archives = []
    for i, content in enumerate((first, second)):
        reads = tmp_path / f"r{i}.fq"
        archive = tmp_path / f"r{i}.wgfq"
        reads.write_bytes(content)
        api.fastq.archive(api.FastqArchiveParams(r1=reads, output=archive))
        archives.append(archive.read_bytes())
    # Use two individually valid blocks and recompute the stream checksum.
    invalid = tmp_path / "joined.wgfq"
    invalid.write_bytes(
        archives[0][:-40] + archives[1][7:-32] + hashlib.sha256(first + second).digest()
    )
    restored = tmp_path / "restore.fq"
    result = api.fastq.restore(
        api.FastqRestoreParams(input=invalid, output=restored), check=False
    )
    assert result.error and result.error.code == "fastq_restore_failed"
    assert "line ending" in result.error.message
    assert not restored.exists()


def test_fastq_archive_1432_restores_two_bit_streams_with_sparse_literals(tmp_path):
    reads, archive, restored = (
        tmp_path / name for name in ("r.fq", "r.wgfq", "restored.fq")
    )
    bases = b"ACGT" * 15 + b"r" + b"TGCA" * 34 + b"N"
    quality = b"F:,#" * 31 + b"~" + b"#:F," * 18 + b"!"
    original = b"@packed\n" + bases + b"\n+packed\n" + quality
    reads.write_bytes(original)
    api.fastq.archive(api.FastqArchiveParams(r1=reads, output=archive))
    api.fastq.restore(api.FastqRestoreParams(input=archive, output=restored))
    assert restored.read_bytes() == original


def test_fastq_archive_1432_expands_home_paths_in_the_python_api(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    original = b"@home\nA\n+\n!\n"
    (tmp_path / "r.fq").write_bytes(original)
    api.fastq.archive(
        api.FastqArchiveParams(r1=Path("~/r.fq"), output=Path("~/r.wgfq"))
    )
    result = api.fastq.restore(
        api.FastqRestoreParams(input=Path("~/r.wgfq"), output=Path("~/restored.fq"))
    )
    assert (tmp_path / "restored.fq").read_bytes() == original
    assert Path(result.outputs[0].path) == tmp_path / "restored.fq"


def test_fastq_archive_1432_preserves_a_line_at_the_documented_limit(tmp_path):
    reads, archive, restored = (
        tmp_path / name for name in ("r.fq", "r.wgfq", "restored.fq")
    )
    bases = b"A" * ((8 << 20) - 1)
    original = b"@long\n" + bases + b"\n+\n" + b"F" * len(bases) + b"\n"
    reads.write_bytes(original)
    api.fastq.archive(api.FastqArchiveParams(r1=reads, output=archive))
    api.fastq.restore(api.FastqRestoreParams(input=archive, output=restored))
    assert restored.read_bytes() == original


def test_fastq_archive_1432_allows_distinct_case_on_case_sensitive_storage(tmp_path):
    (tmp_path / "case-probe").touch()
    if (tmp_path / "CASE-PROBE").exists():
        pytest.skip("test storage is case-insensitive")
    r1, r2, archive = (tmp_path / name for name in ("r1.fq", "r2.fq", "reads.wgfq"))
    r1.write_bytes(R1)
    r2.write_bytes(R2)
    api.fastq.archive(api.FastqArchiveParams(r1=r1, r2=r2, output=archive))
    first, second = (tmp_path / name for name in ("mate.fq", "MATE.fq"))
    api.fastq.restore(
        api.FastqRestoreParams(input=archive, output=first, output_r2=second)
    )
    assert (first.read_bytes(), second.read_bytes()) == (R1, R2)
