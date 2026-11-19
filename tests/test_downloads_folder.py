"""The reference library's downloads/ folder, which replaced ref/.

New downloads go in downloads/; a library made before the rename keeps its
data in ref/, which is still read.
"""

from pathlib import Path

from wgsextract_cli.commands.genotyping.hla import index_folder
from wgsextract_cli.core.gene_map import gene_coords
from wgsextract_cli.core.ref_library.processing import normalize_bootstrap_layout
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary

GENES = "symbol\tchrom\tstart\tend\nBRCA1\tchr17\t{start}\t43125483\n"


def _library(root: Path) -> ReferenceLibrary:
    fasta = root / "genomes" / "hg38.fa"
    fasta.parent.mkdir(parents=True)
    fasta.write_text(">chr17\nACGT\n")
    return ReferenceLibrary(str(fasta))


def _put(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_older_library_data_in_ref_is_still_used(tmp_path):
    clinvar = _put(tmp_path / "ref" / "clinvar_hg38.vcf.gz")
    _put(tmp_path / "ref" / "genes_hg38.tsv", GENES.format(start=43044295))
    _put(tmp_path / "ref" / "t1k_hla" / "hla_dna_seq.fa", ">A*01:01\nACGT\n")

    assert _library(tmp_path).clinvar_vcf == str(clinvar)
    assert gene_coords(str(tmp_path), "BRCA1") == "chr17:43044295-43125483"
    assert index_folder(str(tmp_path)) == str(tmp_path / "ref" / "t1k_hla")


def test_downloads_folder_wins_over_an_older_ref_copy(tmp_path):
    _put(tmp_path / "ref" / "clinvar_hg38.vcf.gz")
    clinvar = _put(tmp_path / "downloads" / "clinvar_hg38.vcf.gz")
    _put(tmp_path / "ref" / "genes_hg38.tsv", GENES.format(start=1))
    _put(tmp_path / "downloads" / "genes_hg38.tsv", GENES.format(start=43044295))

    assert _library(tmp_path).clinvar_vcf == str(clinvar)
    assert gene_coords(str(tmp_path), "BRCA1") == "chr17:43044295-43125483"


def test_new_hla_index_goes_in_downloads(tmp_path):
    assert index_folder(str(tmp_path)) == str(tmp_path / "downloads" / "t1k_hla")


def test_bootstrap_drops_its_empty_ref_folder_but_keeps_user_data(tmp_path):
    empty, used = tmp_path / "empty", tmp_path / "used"
    (empty / "reference" / "ref").mkdir(parents=True)
    kept = _put(used / "ref" / "clinvar_hg38.vcf.gz")

    for library in (empty, used):
        normalize_bootstrap_layout(str(library))

    assert not (empty / "ref").exists() and not (empty / "reference").exists()
    assert kept.is_file()
