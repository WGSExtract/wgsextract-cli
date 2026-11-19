"""The reference library's downloads/ folder, which replaced ref/.

New downloads go in downloads/; a library made before the rename keeps its
data in ref/, which is still read.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.htslib_helpers import run_wgsextract
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


def test_gene_map_delete_removes_copies_in_both_folders(tmp_path):
    """Review of the rename: a refreshed map in downloads/ hid the older ref/
    copy from `--delete`, so `vcf filter --gene` kept resolving genes."""
    maps = [
        _put(tmp_path / folder / f"genes_{build}.tsv", GENES.format(start=1))
        for folder in ("downloads", "ref")
        for build in ("hg19", "hg38")
    ]

    run_wgsextract("ref", "gene-map", "--delete", "--ref", str(tmp_path))

    assert not any(path.exists() for path in maps)


def test_bootstrap_keeps_a_ref_folder_linked_elsewhere(tmp_path):
    """Review of the rename: an empty ref/ symlink crashed `ref bootstrap`."""
    target = tmp_path / "external"
    target.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    try:
        (library / "ref").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")

    normalize_bootstrap_layout(str(library))

    assert (library / "ref").is_symlink() and target.is_dir()


def test_interrupted_download_in_ref_resumes_there(tmp_path):
    """Review of the rename: an older library's ref/*.partial was abandoned and
    the multi-GB download restarted in downloads/."""
    pyBigWig = pytest.importorskip("pyBigWig")
    from wgsextract_cli.core import bigwig_resources

    partial = _put(tmp_path / "ref" / "phylop_hg19.bw.partial", "half")
    resumed: list[str] = []

    def finish(_url, dest, *_args):
        resumed.append(dest)
        bw = pyBigWig.open(dest, "w")
        bw.addHeader([("chr1", 100)])
        bw.addEntries("chr1", 0, values=[1.0], span=1, step=1)
        bw.close()
        return True

    with patch.object(bigwig_resources, "download_file", side_effect=finish):
        assert bigwig_resources.download_phylop(str(tmp_path), "hg19")

    assert resumed == [str(partial.with_suffix(""))]
    assert not (tmp_path / "downloads" / "phylop_hg19.bw").exists()
