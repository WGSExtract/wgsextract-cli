"""`wgsextract ref needs -- COMMAND`: the reference data a command line needs
and the library lacks, so the desktop app can offer to install it before the
run (a CRAM whose hs37d5 wasn't installed ran `info` and reported "Could not
compute" for every read-based field)."""

import json
from pathlib import Path

import pytest

from tests.htslib_helpers import sam_to_bam
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.main import main

pytestmark = pytest.mark.skipif(
    not get_tool_path("samtools"), reason="samtools not available"
)

# 86 sequences with hs37d5's chr1 length: the catalog's hs37d5 by contig count.
HS37D5_LIKE = [("1", 249250621)] + [(f"c{i}", 1000 + i) for i in range(85)]
READ = "r1\t0\tc0\t1\t60\t4M\t*\t0\t0\tACGT\tIIII"


def _header(contigs):
    return ["@HD\tVN:1.6\tSO:coordinate"] + [
        f"@SQ\tSN:{name}\tLN:{length}" for name, length in contigs
    ]


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "reference"
    (lib / "genomes").mkdir(parents=True)
    return lib


def _install_fasta(library: Path, name: str, contigs) -> Path:
    """An indexed FASTA in the library with these sequence lengths (the
    sequence itself is never read)."""
    fasta = library / "genomes" / name
    fasta.write_text(">c0\nACGT\n")
    Path(f"{fasta}.fai").write_text(
        "".join(f"{n}\t{ln}\t0\t60\t61\n" for n, ln in contigs)
    )
    return fasta


def _cram(tmp_path: Path, contigs) -> str:
    """A real CRAM with this sequence dictionary (written against a throwaway
    FASTA so the library under test doesn't hold its reference)."""
    import subprocess

    work = tmp_path / "writer"
    work.mkdir()
    ref = work / "ref.fa"
    ref.write_text("".join(f">{n}\n{'A' * min(ln, 50)}\n" for n, ln in contigs))
    samtools = get_tool_path("samtools")
    subprocess.run([samtools, "faidx", str(ref)], check=True)
    sam = work / "in.sam"
    sam.write_text("\n".join(_header(contigs) + [READ.replace("ACGT", "AAAA")]) + "\n")
    cram = tmp_path / "sample.cram"
    subprocess.run(
        [samtools, "view", "-C", "-T", str(ref), "-o", str(cram), str(sam)],
        check=True,
    )
    return str(cram)


def _needs(capsys, library: Path, *command: str) -> list[dict]:
    config = library.parent / "config.toml"
    config.write_text(f"reference_library = {json.dumps(str(library))}\n")
    capsys.readouterr()
    main(["--config", str(config), "--json", "ref", "needs", "--", *command])
    return json.loads(capsys.readouterr().out)["details"]["missing"]


def test_info_on_a_cram_whose_reference_is_missing_names_its_install(
    tmp_path, library, capsys
):
    cram = _cram(tmp_path, HS37D5_LIKE)

    missing = _needs(capsys, library, "info", "--input", cram)

    assert [(m["kind"], m["name"]) for m in missing] == [("genome", "hs37d5")]
    assert missing[0]["installArguments"] == ["ref", "library", "--install", "hs37d5"]


def test_the_installed_reference_satisfies_a_cram(tmp_path, library, capsys):
    cram = _cram(tmp_path, HS37D5_LIKE)
    _install_fasta(library, "hs37d5.fa.gz", HS37D5_LIKE)

    assert _needs(capsys, library, "info", "--input", cram) == []


def test_another_genome_of_the_same_build_cannot_decode_a_cram(
    tmp_path, library, capsys
):
    cram = _cram(tmp_path, HS37D5_LIKE)
    _install_fasta(library, "hg19.fa.gz", [("chr1", 249250621), ("chrM", 16571)])

    missing = _needs(capsys, library, "info", "--input", cram)

    assert [m["id"] for m in missing] == ["hs37d5"]


def test_a_bam_needs_no_reference_to_read_but_one_to_call_variants(
    tmp_path, library, capsys
):
    bam = sam_to_bam(_header(HS37D5_LIKE) + [READ], tmp_path / "sample.bam")

    assert _needs(capsys, library, "info", "--input", bam) == []
    calls = _needs(capsys, library, "vcf", "snp", "--input", bam)
    assert [m["id"] for m in calls] == ["hs37d5"]


def test_an_explicit_fasta_is_trusted(tmp_path, library, capsys):
    cram = _cram(tmp_path, HS37D5_LIKE)
    fasta = tmp_path / "mine.fa"
    fasta.write_text(">1\nA\n")

    assert _needs(capsys, library, "info", "--input", cram, "--ref", str(fasta)) == []


def test_a_header_only_command_needs_nothing(tmp_path, library, capsys):
    cram = _cram(tmp_path, HS37D5_LIKE)

    assert _needs(capsys, library, "bam", "identify", "--input", cram) == []


def test_a_command_line_that_does_not_parse_needs_nothing(library, capsys):
    assert _needs(capsys, library, "info", "--no-such-option") == []


def test_missing_annotation_data_names_its_download(tmp_path, library, capsys):
    vcf = tmp_path / "calls.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )

    missing = _needs(capsys, library, "vcf", "clinvar", "--vcf-input", str(vcf))

    assert [(m["kind"], m["id"]) for m in missing] == [("annotation", "clinvar")]
    assert missing[0]["installArguments"][:2] == ["ref", "clinvar"]


def test_report_json_prints_only_the_missing_list(tmp_path, library, capsys):
    cram = _cram(tmp_path, HS37D5_LIKE)
    config = library.parent / "config.toml"
    config.write_text(f"reference_library = {json.dumps(str(library))}\n")
    capsys.readouterr()

    main(
        [
            "--config",
            str(config),
            "ref",
            "needs",
            "--report-json",
            "--",
            "info",
            "--input",
            cram,
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in report["missing"]] == ["hs37d5"]
