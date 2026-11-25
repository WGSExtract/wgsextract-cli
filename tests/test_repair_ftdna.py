"""repair ftdna-bam / ftdna-vcf on tiny files read back with samtools/bcftools."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    run_wgsextract,
    write_fasta,
)
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.main import main

pytestmark = needs_htslib

_GENOME = "ACGTTGCAAC" * 30  # 300 bp
_SEQ, _QUAL = _GENOME[100:120], "ABCDEFGHIJKLMNOPQRST"


def _tool(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        [get_tool_path(args[0]), *args[1:]],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _ftdna_bam(path: Path, fmt: str = "-b", ref: Path | None = None) -> str:
    """A BAM (or CRAM) whose read names have spaces, as FTDNA Big Y BAMs do."""
    sam = (
        "@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chrY\tLN:300\n"
        "@RG\tID:rg1\tSM:kit123\n"
        f"FTDNA read 7\t99\tchrY\t101\t60\t20M\t=\t131\t50\t{_SEQ}\t{_QUAL}\tRG:Z:rg1\n"
        f"FTDNA read 9\t0\tchrY\t111\t60\t20M\t*\t0\t0\t{_GENOME[110:130]}\t{_QUAL}"
        "\tRG:Z:rg1\tNM:i:0\n"
        f"FTDNA read 7\t147\tchrY\t131\t60\t20M\t=\t101\t-50\t{_GENOME[130:150]}"
        f"\t{_QUAL}\tRG:Z:rg1\n"
    )
    ref_opt = ["-T", str(ref)] if ref else []
    _tool("samtools", "view", fmt, *ref_opt, "-o", str(path), "-", stdin=sam)
    return str(path)


def _records(path: str, *opts: str) -> list[list[str]]:
    return [
        line.split("\t")
        for line in _tool("samtools", "view", *opts, path).split("\n")
        if line
    ]


def test_repair_bam_replaces_spaces_in_read_names_only(tmp_path):
    bam = _ftdna_bam(tmp_path / "bigy.bam")
    run_wgsextract(
        "repair", "ftdna-bam", "--input", bam, "--outdir", str(tmp_path / "o")
    )
    out = str(tmp_path / "o" / "bigy_repaired.bam")
    assert [r[0] for r in _records(out)] == [
        "FTDNA:read:7",
        "FTDNA:read:9",
        "FTDNA:read:7",
    ]
    assert [r[1:] for r in _records(out)] == [r[1:] for r in _records(bam)]
    header = _tool("samtools", "view", "-H", out)
    assert "@SQ\tSN:chrY\tLN:300" in header and "@RG\tID:rg1\tSM:kit123" in header
    # Mates still pair up under the repaired name.
    assert _tool("samtools", "view", "-c", "-f", "1", out).strip() == "2"


def test_repair_bam_reads_cram_with_reference(tmp_path):
    ref = write_fasta(tmp_path / "ref.fa", {"chrY": _GENOME})
    cram = _ftdna_bam(tmp_path / "bigy.cram", "-C", ref)
    out = tmp_path / "fixed.bam"
    run_wgsextract(
        "repair", "ftdna-bam", "--input", cram, "--ref", str(ref), "--output", str(out)
    )
    assert [r[9] for r in _records(str(out))] == [
        r[9] for r in _records(cram, "-T", str(ref))
    ]
    assert _records(str(out))[0][0] == "FTDNA:read:7"


def test_repair_bam_cram_with_missing_reference_fails(tmp_path):
    cram = tmp_path / "bigy.cram"
    cram.write_bytes(b"")
    with pytest.raises(WGSExtractError, match="No reference FASTA"):
        run_wgsextract(
            "repair", "ftdna-bam", "--input", str(cram), "--ref",
            str(tmp_path / "nope.fa"),
        )  # fmt: skip


def test_repair_bam_unreadable_input_fails_without_output(tmp_path):
    bad = tmp_path / "bad.bam"
    bad.write_text("not a bam\n")
    with pytest.raises(WGSExtractError, match="FTDNA BAM repair failed"):
        run_wgsextract("repair", "ftdna-bam", "--input", str(bad))
    assert not (tmp_path / "bad_repaired.bam").exists()


def test_repair_bam_unreadable_input_names_the_reader(tmp_path):
    bad = tmp_path / "bad.bam"
    bad.write_text("not a bam\n")
    with pytest.raises(WGSExtractError, match=r"samtools view -h \(exit"):
        run_wgsextract("repair", "ftdna-bam", "--input", str(bad))


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root"
)
def test_repair_bam_writer_failure_is_reported(tmp_path, capsys):
    bam = _ftdna_bam(tmp_path / "ok.bam")
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    output = locked / "y.bam"
    try:
        with pytest.raises(SystemExit) as exit_info:
            main(
                [
                    "repair",
                    "ftdna-bam",
                    "--input",
                    bam,
                    "--output",
                    str(output),
                ]
            )
    finally:
        locked.chmod(0o700)
    assert exit_info.value.code == 1
    message = capsys.readouterr().err
    assert "Permission denied" in message and str(output) in message
    assert list(locked.iterdir()) == []


def test_repair_bam_requires_existing_input(tmp_path):
    with pytest.raises(WGSExtractError, match="does not exist"):
        run_wgsextract("repair", "ftdna-bam", "--input", str(tmp_path / "x.bam"))


def test_1087_repair_cli_streams_stdin_to_stdout_without_input(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("")
    sam = "@SQ\tSN:chrY\tLN:300\nA B C\t4\t*\t0\t0\t*\t*\t0\t0\tAC\tII\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(sam))
    monkeypatch.setattr(sys, "stdout", out := io.StringIO())
    main(["--config", str(config), "repair", "ftdna-bam"])
    assert out.getvalue() == sam.replace("A B C", "A:B:C")

    vcf = "##x=a b\n#CHROM\tPOS\nchr1\t5\t.\tA\tG\t9\tDP=1\t.\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(vcf))
    monkeypatch.setattr(sys, "stdout", out := io.StringIO())
    main(["--config", str(config), "repair", "ftdna-vcf"])
    lines = out.getvalue().splitlines()
    assert lines[0] == "##x=a b" and lines[-1].split("\t")[6:8] == [
        "DP1",
        "FTDNA_FILTER=DP%3D1",
    ]
    assert any(line.startswith("##FILTER=<ID=DP1,") for line in lines)


_VCF = (
    "##fileformat=VCFv4.2\n##contig=<ID=chrY,length=300>\n"
    '##FILTER=<ID=PASS,Description="All filters passed">\n'
    '##FILTER=<ID=LowQual,Description="low">\n'
    '##FILTER=<ID=q10,Description="q">\n'
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="d">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tkit123\n"
    "chrY\t101\trs1\tA\tG\t50\tPASS\tDP=7\tGT\t1\n"
    "chrY\t105\t.\tT\tC\t3\tLow Qual;q<10\tDP=2\tGT\t1\n"
)


def _filters(vcf: Path) -> list[str]:
    return _tool(
        "bcftools", "query", "-f", "%POS %FILTER %INFO/DP [%GT]\n", str(vcf)
    ).splitlines()


@pytest.mark.parametrize("suffix", [".vcf", ".vcf.gz"])
def test_1185_uninterpretable_filter_is_not_guessed_into_a_declared_id(
    tmp_path, suffix
):
    # Stripping "Low Qual;q<10" to "LowQual;q10" borrowed the meanings of
    # declared filters the expression may not have had.
    src = tmp_path / "kit.vcf"
    src.write_text(_VCF)
    if suffix == ".vcf.gz":
        src = Path(bgzip_index(src))
    run_wgsextract("repair", "ftdna-vcf", "--input", str(src))
    out = tmp_path / "WGSEv6" / "kit_repaired.vcf"
    assert _filters(out) == ["101 PASS 7 1", "105 FTDNA_UNKNOWN 2 1"]
    assert out.read_text().splitlines()[:7] == _VCF.splitlines()[:7]


@pytest.mark.parametrize("configured_temp", [False, True])
@pytest.mark.parametrize("aliased", [False, True], ids=["direct", "symlink"])
def test_1093_repair_vcf_keeps_its_bcf_working_copy_off_system_temp(
    tmp_path, capsys, monkeypatch, configured_temp, aliased
):
    from tests.test_temp_directory import _python_writes, _wgsextract

    plain = tmp_path / "kit.vcf"
    plain.write_text(_VCF.replace("Low Qual;q<10", "LowQual"))
    bcf = tmp_path / "kit.bcf"
    _tool("bcftools", "view", "-Ob", "-o", str(bcf), str(plain))
    out = tmp_path / "fixed" / "kit.vcf"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out.parent.mkdir()
    if aliased:
        for folder in (scratch, out.parent):
            actual = folder.with_name(folder.name + "-actual")
            folder.rename(actual)
            try:
                folder.symlink_to(actual, target_is_directory=True)
            except OSError as error:
                pytest.skip(f"Symlinks unavailable: {error}")
    options = ["--tmp-dir", str(scratch)] if configured_temp else []
    selected = scratch if configured_temp else out.parent
    with _python_writes(monkeypatch) as written:
        result = _wgsextract(
            capsys,
            "repair",
            "ftdna-vcf",
            "--input",
            str(bcf),
            "--output",
            str(out),
            *options,
        )
    assert result["status"] == "ok", result["error"]
    assert _filters(out) == ["101 PASS 7 1", "105 LowQual 2 1"]
    relative = [
        path.relative_to(selected.resolve())
        for path in written
        if path.is_relative_to(selected.resolve())
    ]
    assert any(path.suffix == ".vcf" and len(path.parts) > 1 for path in relative), (
        sorted(map(str, written))
    )
    assert not any(p.name.startswith(".wgse") for p in out.parent.iterdir())
    assert list(scratch.iterdir()) == []


def test_repair_vcf_requires_existing_input(tmp_path):
    with pytest.raises(WGSExtractError, match="does not exist"):
        run_wgsextract("repair", "ftdna-vcf", "--input", str(tmp_path / "x.vcf"))


def test_repair_vcf_in_place_keeps_the_records(tmp_path):
    vcf = tmp_path / "kit.vcf"
    vcf.write_text(_VCF)
    try:
        run_wgsextract("repair", "ftdna-vcf", "--input", str(vcf), "--output", str(vcf))
    except WGSExtractError:
        pass  # refusing is fine; destroying the input is not
    assert vcf.read_text().count("chrY\t") == 2


@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_1224_repair_vcf_refuses_output_linked_to_input(tmp_path, capsys, link_kind):
    source = tmp_path / "kit.vcf"
    original = _VCF.encode()
    source.write_bytes(original)
    output = tmp_path / "fixed.vcf"
    if link_kind == "hardlink":
        output.hardlink_to(source)
    else:
        try:
            output.symlink_to(source)
        except OSError as error:
            pytest.skip(f"Symlinks unavailable: {error}")

    with pytest.raises(SystemExit) as exit_info:
        main(["repair", "ftdna-vcf", "--input", str(source), "--output", str(output)])

    assert exit_info.value.code == 1
    assert "overwrite the input" in capsys.readouterr().err
    assert output.samefile(source)
    assert source.read_bytes() == output.read_bytes() == original


def test_1224_failed_vcf_repair_preserves_previous_output(tmp_path, capsys):
    source = tmp_path / "bad.vcf"
    source.write_text(_VCF + "chrY\t200\t.\tA\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    output = dest / "fixed.vcf"
    original = _VCF.replace("Low Qual;q<10", "LowQual").encode()
    output.write_bytes(original)

    with pytest.raises(SystemExit) as exit_info:
        main(["repair", "ftdna-vcf", "--input", str(source), "--output", str(output)])

    assert exit_info.value.code == 1
    assert "columns" in capsys.readouterr().err
    assert output.read_bytes() == original
    assert list(dest.iterdir()) == [output]


def test_1224_repair_vcf_refuses_to_replace_output_symlink(tmp_path, capsys):
    source = tmp_path / "kit.vcf"
    source.write_text(_VCF, encoding="utf-8")
    target = tmp_path / "saved.vcf"
    target.write_bytes(b"existing output")
    output = tmp_path / "fixed.vcf"
    try:
        output.symlink_to(target)
    except OSError as error:
        pytest.skip(f"Symlinks unavailable: {error}")

    with pytest.raises(SystemExit) as exit_info:
        main(["repair", "ftdna-vcf", "--input", str(source), "--output", str(output)])

    assert exit_info.value.code == 1
    assert "regular file" in capsys.readouterr().err
    assert output.is_symlink()
    assert target.read_bytes() == b"existing output"


def test_1224_failed_bam_repair_preserves_previous_output(tmp_path):
    source = tmp_path / "bad.bam"
    source.write_text("not a bam\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    output = dest / "fixed.bam"
    _ftdna_bam(output)
    original = output.read_bytes()

    with pytest.raises(WGSExtractError, match="samtools view -h"):
        run_wgsextract(
            "repair", "ftdna-bam", "--input", str(source), "--output", str(output)
        )
    assert output.read_bytes() == original
    assert list(dest.iterdir()) == [output]


def test_1224_bam_repair_is_reproducible_at_the_same_destination(tmp_path):
    source = _ftdna_bam(tmp_path / "kit.bam")
    output = tmp_path / "fixed.bam"
    args = ["repair", "ftdna-bam", "--input", source, "--output", str(output)]

    main(args)
    first = output.read_bytes()
    main(args)

    assert output.read_bytes() == first
    assert _records(str(output))[0][0] == "FTDNA:read:7"


def test_1224_vcf_repair_leaves_index_like_neighbors_alone(tmp_path):
    output = tmp_path / "fixed.vcf"
    source = tmp_path / "fixed.vcf.idx"
    original = _VCF.encode()
    source.write_bytes(original)
    other = tmp_path / "fixed.vcf.tbi"
    other.mkdir()

    main(["repair", "ftdna-vcf", "--input", str(source), "--output", str(output)])

    assert source.read_bytes() == original
    assert other.is_dir()
    assert output.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and path lengths")
def test_1224_vcf_replacement_preserves_private_mode_and_accepts_long_names(tmp_path):
    source = tmp_path / "kit.vcf"
    source.write_text(_VCF, encoding="utf-8")
    output = tmp_path / ("a" * 220 + ".vcf")
    output.write_bytes(b"previous output")
    output.chmod(0o600)

    main(["repair", "ftdna-vcf", "--input", str(source), "--output", str(output)])

    assert _filters(output) == ["101 PASS 7 1", "105 FTDNA_UNKNOWN 2 1"]
    assert output.stat().st_mode & 0o777 == 0o600


def test_repair_cram_finds_the_reference_beside_it(tmp_path, monkeypatch):
    elsewhere = tmp_path / "made_on_another_machine"
    elsewhere.mkdir()
    ref = write_fasta(elsewhere / "ref.fa", {"chrY": _GENOME})
    kit = tmp_path / "kit"
    kit.mkdir()
    cram = _ftdna_bam(kit / "bigy.cram", "-C", ref)
    shutil.copy(ref, kit / "ref.fa")
    shutil.copy(f"{ref}.fai", kit / "ref.fa.fai")
    shutil.rmtree(elsewhere)
    # Keep htslib off the network: only an (empty) local reference cache.
    monkeypatch.setenv("REF_PATH", str(tmp_path / "no_cache" / "%s"))
    # A configured (here empty) reference library, as every install has.
    (tmp_path / "library").mkdir()
    monkeypatch.setitem(settings, "reference_library", str(tmp_path / "library"))
    run_wgsextract("repair", "ftdna-bam", "--input", cram)
    assert _records(str(kit / "WGSEv6" / "bigy_repaired.bam"))[0][0] == "FTDNA:read:7"


_CORPUS = Path(__file__).parent / "fixtures" / "ftdna_vcf" / "legacy_filters.vcf"


def _repair_json(capsys, src: Path, out: Path) -> dict:
    try:
        main(
            ["repair", "ftdna-vcf", "--input", str(src), "--output", str(out), "--json"]
        )
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


def _bcftools(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [get_tool_path("bcftools"), *args], capture_output=True, text=True, check=True
    )


def _filter_descriptions(vcf: Path) -> dict[str, str]:
    meanings = {}
    for line in _bcftools("view", "-h", str(vcf)).stdout.splitlines():
        if line.startswith("##FILTER=<"):
            filter_id = re.search(r"[<,]ID=([^,>]+)", line)
            description = re.search(r'Description="([^"]*)"', line)
            assert filter_id and description, line
            meanings[filter_id[1]] = description[1]
    return meanings


def test_1185_legacy_ftdna_filters_become_declared_ids_that_keep_their_meaning(
    tmp_path, capsys
):
    out = tmp_path / "fixed.vcf"
    result = _repair_json(capsys, _CORPUS, out)
    assert result["status"] == "ok", result["error"]
    query = _bcftools(
        "query",
        "-f",
        "%POS %FILTER %INFO/FTDNA_FILTER %INFO/HG %INFO/ISOGG\n",
        str(out),
    )
    # bcftools adds (and warns about) any FILTER the header doesn't declare.
    assert "not defined in the header" not in query.stderr
    rows = {r.split()[0]: r.split()[1:] for r in query.stdout.splitlines()}
    assert {pos: row[0] for pos, row in rows.items()} == {
        "101": "PASS", "102": ".", "103": "DP1", "104": "DPM", "105": "DPF",
        "106": "Q40", "107": "QF", "108": "GTL", "109": "DP1;Q40",
        "110": "LowQual;DP1", "111": "q10;s50", "112": "FTDNA_UNKNOWN",
        "113": "FTDNA_UNKNOWN", "114": "DP1", "115": "PASS", "116": "DP1", "117": ".",
        "118": "VQSRTrancheSNP99.90to100.00+",
    }  # fmt: skip
    assert rows["109"][1] == "DP%3D1,QUAL%3D19.1"  # original evidence kept
    assert rows["112"][1] == "Low%20Coverage"
    assert rows["101"][2:] == ["R-M269", "R1b1a1b"] and rows["111"][2] == "R-L21"

    meanings = _filter_descriptions(out)
    used = {i for row in rows.values() for i in row[0].split(";")} - {"PASS", "."}
    assert used <= meanings.keys()
    assert "single read" in meanings["DP1"] and "below 40" in meanings["Q40"]
    assert "more than 750" in meanings["DPM"] and "unknown" in meanings["s50"]
    assert meanings["LowQual"] == "Low quality"  # the input's own meaning stays
    codes = {w["code"] for w in result["warnings"]}
    assert {"ftdna_vcf_unknown_filter", "ftdna_vcf_undeclared_filter"} <= codes
    assert result["metrics"]["unknown_filter_records"] == 2


def test_1185_already_valid_vcf_is_copied_unchanged(tmp_path, capsys):
    declared = "".join(
        f'##INFO=<ID={key},Number=1,Type=String,Description="{key} haplogroup">\n'
        for key in ("HG", "ISOGG")
    )
    valid = tmp_path / "valid.vcf"
    valid.write_text(
        "".join(
            line
            for line in _CORPUS.read_text().splitlines(keepends=True)
            if line.startswith("#") or line.split("\t")[6] in ("PASS", ".", "LowQual")
        ).replace("#CHROM", declared + "#CHROM")
    )
    out = tmp_path / "fixed.vcf"
    result = _repair_json(capsys, valid, out)
    assert out.read_text() == valid.read_text()
    assert not [w for w in result["warnings"] if w["code"].startswith("ftdna_vcf")]
    assert result["metrics"]["repaired_records"] == 0


def test_1185_mapped_id_already_declared_with_another_meaning_is_renamed(
    tmp_path, capsys
):
    src = tmp_path / "conflict.vcf"
    src.write_text(
        _CORPUS.read_text().replace(
            "##FILTER=<ID=q10,",
            # The declaration's ID isn't its first attribute.
            '##FILTER=<Description="Depth below 1x, in window",ID=DP1>\n'
            "##FILTER=<ID=q10,",
        )
    )
    out = tmp_path / "fixed.vcf"
    result = _repair_json(capsys, src, out)
    meanings = _filter_descriptions(out)
    assert meanings["DP1"] == "Depth below 1x, in window"
    assert "single read" in meanings["DP1_WGSE"]
    filters = _bcftools("query", "-f", "%POS %FILTER\n", str(out)).stdout
    assert "103 DP1_WGSE\n" in filters
    assert any(w["code"] == "ftdna_vcf_id_conflict" for w in result["warnings"])


def test_1185_malformed_record_fails_without_partial_output(
    tmp_path, capsys, monkeypatch
):
    bad = tmp_path / "bad.vcf"
    bad.write_text(_CORPUS.read_text() + "chrY\t200\t.\tA\n")
    out = tmp_path / "fixed.vcf"
    result = _repair_json(capsys, bad, out)
    assert result["status"] == "failed" and "Line 29" in result["error"]["message"]
    assert not out.exists()

    config = tmp_path / "config.toml"
    config.write_text("")
    monkeypatch.setattr(sys, "stdin", io.StringIO(bad.read_text()))
    monkeypatch.setattr(sys, "stdout", stream := io.StringIO())
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(config), "repair", "ftdna-vcf"])
    assert exit_info.value.code != 0 and stream.getvalue() == ""


def test_1185_bare_carriage_return_in_a_streamed_record_is_not_split(
    tmp_path, monkeypatch
):
    # Replaying the spool split a record at a bare CR and crashed after the
    # header had been written to stdout.
    config = tmp_path / "config.toml"
    config.write_text("")
    vcf = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchrY\t5\t.\tA\tG\t9\tDP=1\tX=a\rb\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(vcf))
    monkeypatch.setattr(sys, "stdout", stream := io.StringIO())
    main(["--config", str(config), "repair", "ftdna-vcf"])
    record = stream.getvalue().split("\n")[-2].split("\t")
    assert record[6:8] == ["DP1", "X=a\rb;FTDNA_FILTER=DP%3D1"]
