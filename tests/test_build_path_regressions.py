from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, sam_to_bam, write_fasta
from wgsextract_cli.core.alignment.metadata import get_vcf_build
from wgsextract_cli.core.builds import build_from_path
from wgsextract_cli.core.ref_library.assets import (
    build_hint,
    fasta_build,
    select_reference_fasta,
)
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary


@pytest.mark.parametrize(
    "name",
    [
        "notchm13.fa",
        "samplehg19.bam",
        "hg380.fa",
        "hs37d50.fa",
        "hg19backup.fa",
        "hg38p.fa",
        "hg38p14backup.fa",
    ],
)
def test_issue_1004_build_tokens_do_not_match_inside_other_words(name: str) -> None:
    assert build_from_path(name) is None


@pytest.mark.parametrize(
    ("text", "build"),
    [
        ("sample_GRCh38.bam", "hg38"),
        ("hg38p12.fa.gz", "hg38"),
        ("hg38p14.fa.gz", "hg38"),
        ("GRCh38p14.fa.gz", "hg38"),
        ("hs37d5.fa.gz", "hg19"),
        ("hs38DH.fa.gz", "hg38"),
        ("hs38d1s.fa.gz", "hg38"),
        ("hs38a.fna.gz", "hg38"),
        ("T2Tv20 chm13v2.0.fa.gz", "t2t"),
        ("Homo sapiens chromosome 1, GRCh38.p14 Primary Assembly", "hg38"),
        ("dna:chromosome chromosome:GRCh37:1:1:249250621:1", "hg19"),
        ("file:///references/Homo_sapiens.GRCh38.dna.primary_assembly.fa", "hg38"),
    ],
)
def test_issue_1004_build_tokens_preserve_reference_names_and_metadata(
    text: str, build: str
) -> None:
    assert build_from_path(text) == build


@needs_htslib
@pytest.mark.parametrize("parent", ["chm13", "hg19", "hs37d5"])
@pytest.mark.parametrize("kind", ["bam", "vcf"])
@pytest.mark.parametrize(
    ("name", "chr1_length", "expected"),
    [
        ("sample", 248956422, "hg38"),
        ("sample_GRCh38", 100, "hg38"),
        ("sample", 100, None),
    ],
    ids=["header-build", "filename-build", "unknown-stays-unknown"],
)
def test_issue_1004_input_under_misleading_parent_keeps_its_build(
    tmp_path: Path,
    parent: str,
    kind: str,
    name: str,
    chr1_length: int,
    expected: str | None,
) -> None:
    folder = tmp_path / parent / "data"
    folder.mkdir(parents=True)
    if kind == "bam":
        sample = sam_to_bam(
            ["@HD\tVN:1.6\tSO:coordinate", f"@SQ\tSN:chr1\tLN:{chr1_length}"],
            folder / f"{name}.bam",
        )
    else:
        vcf = folder / f"{name}.vcf"
        vcf.write_text(
            f"##fileformat=VCFv4.2\n##contig=<ID=chr1,length={chr1_length}>\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
        sample = bgzip_index(vcf)
    library = tmp_path / parent / "library"
    library.mkdir()

    lib = ReferenceLibrary(library, input_path=sample, skip_full_search=True)

    assert lib.build == expected


@needs_htslib
@pytest.mark.parametrize("parent", ["chm13", "hg19", "hs37d5"])
@pytest.mark.parametrize(
    ("name", "expected"), [("GRCh38.fa", "hg38"), ("custom.fa", None)]
)
def test_issue_1004_fasta_under_misleading_parent_uses_only_its_name(
    tmp_path: Path, parent: str, name: str, expected: str | None
) -> None:
    fasta = write_fasta(tmp_path / parent / "genomes" / name, {"chrTest": "ACGT"})

    assert fasta_build(fasta) == expected
    assert build_hint(fasta) == (expected or "")


@pytest.mark.parametrize(
    ("folder", "expected"), [("GRCh38", "hg38"), ("library", None)]
)
def test_issue_1004_reference_folder_hint_excludes_ancestors(
    tmp_path: Path, folder: str, expected: str | None
) -> None:
    library = tmp_path / "hg19" / "chm13" / folder
    library.mkdir(parents=True)

    assert ReferenceLibrary(library, skip_full_search=True).build == expected


def test_issue_1004_reference_selection_ignores_parent_and_partial_tokens() -> None:
    candidates = ["/refs/hg19.fa", "/refs/notchm13.fa"]

    assert select_reference_fasta(candidates, "/chm13/sample.bam") == ""
    assert select_reference_fasta(candidates, "/hg19/sample_chm13.bam") == ""


@needs_htslib
@pytest.mark.parametrize("name", ["hg38p12.fa", "hg38p14.fa", "GRCh38p14.fa"])
def test_issue_1004_patch_suffix_preserves_reference_selection_and_mismatch_check(
    tmp_path: Path, name: str
) -> None:
    library = tmp_path / "references"
    fasta = write_fasta(library / name, {"chrTest": "ACGT"})
    sample = sam_to_bam(
        ["@HD\tVN:1.6\tSO:coordinate", "@SQ\tSN:chr1\tLN:249250621"],
        tmp_path / "sample.bam",
    )

    assert fasta_build(fasta) == "hg38"
    assert select_reference_fasta(
        ["/refs/hg19.fa", str(fasta)], "sample_hg38.bam"
    ) == str(fasta)

    lib = ReferenceLibrary(library, input_path=sample, skip_full_search=True)

    assert lib.build == "hg19"
    assert lib.fasta is None


@needs_htslib
@pytest.mark.parametrize(
    "metadata",
    [
        "##reference=file:///references/Homo_sapiens.GRCh38.dna.primary_assembly.fa",
        '##contig=<ID=chrTest,length=4,assembly="GRCh38.p14">',
        "##assembly=GRCh38",
    ],
)
def test_issue_1004_vcf_assembly_metadata_survives_path_fix(
    tmp_path: Path, metadata: str
) -> None:
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(
        f"##fileformat=VCFv4.2\n{metadata}\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )

    assert get_vcf_build(bgzip_index(vcf)) == "hg38"


@needs_htslib
@pytest.mark.parametrize(
    ("reference", "build"),
    [
        ("file:///data/chm13-project/ref.fa", None),
        ("/data/hg19 work/GRCh38.fa.gz", "hg38"),
        ("C:\\chm13\\hs37d5.fa.gz", "hg19"),
        ("GRCh38", "hg38"),
        # Contig assembly metadata still counts, with the usual preference.
        ('/refs/hs37d5.fa">\n##contig=<ID=chr2,length=9,assembly="GRCh38.p14"', "hg38"),
    ],
)
def test_vcf_reference_path_counts_only_its_file_name(
    tmp_path: Path, reference: str, build: str | None
) -> None:
    """bcftools writes the reference's full path; a folder named chm13 made
    VEP and PharmCAT treat a GRCh38 VCF as T2T (found certifying a worktree
    named chm13-tests-deep-tmp)."""
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(
        f"##fileformat=VCFv4.2\n##reference={reference}\n"
        "##contig=<ID=chr1,length=1000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    assert get_vcf_build(bgzip_index(vcf)) == build


@needs_htslib
def test_build_detected_from_a_folder_name_before_is_detected_again(
    tmp_path: Path,
) -> None:
    """Review of #1299: a build recorded when the folder decided it (version 1)
    outlived the fix, so VEP and PharmCAT kept rejecting the VCF."""
    from wgsextract_cli.core import sample_workspace

    vcf = tmp_path / "sample.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##reference=file:///chm13-project/GRCh38.fa\n"
        "##contig=<ID=chr1,length=1000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    sample = bgzip_index(vcf)
    name = Path(sample).name
    fact = f'[detected."{name}".build'
    (tmp_path / sample_workspace.WORKSPACE_FILE).write_text(
        f'schema = "{sample_workspace.SCHEMA_ID}"\n[detected."{name}"]\n'
        f'fingerprint = "{sample_workspace.file_fingerprint(sample)}"\n'
        f'{fact}]\nvalue = "t2t"\nmethod = "VCF header contigs"\n'
        'confidence = "high"\nversion = 1\nby = "wgsextract 0.8"\n'
        'at = "2026-01-01T00:00:00+00:00"\n',
        newline="\n",
    )
    library = tmp_path / "library"
    library.mkdir()
    lib = ReferenceLibrary(library, input_path=sample, skip_full_search=True)
    assert lib.build == "hg38"
