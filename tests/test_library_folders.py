"""reference_genome_library holds only reference genomes; dataset_library holds
the user's datasets, and the old genome_library key still names it."""

from pathlib import Path

from wgsextract_cli.core import config
from wgsextract_cli.core.config import dataset_library, settings
from wgsextract_cli.core.reference_assets import (
    library_of_genomes_dir,
    reference_genomes_dir,
    reference_library_of,
)
from wgsextract_cli.core.reference_resolver import library_roots


def test_without_the_setting_genomes_stay_inside_the_library(tmp_path):
    settings.pop("reference_genome_library", None)
    assert reference_genomes_dir(tmp_path / "lib") == str(tmp_path / "lib" / "genomes")
    assert library_of_genomes_dir(tmp_path / "lib" / "genomes") == str(tmp_path / "lib")


def test_the_configured_library_keeps_its_genomes_in_their_own_folder(tmp_path):
    lib, genomes = tmp_path / "reference", tmp_path / "genomes"
    lib.mkdir()
    genomes.mkdir()
    fasta = genomes / "hg38.fa"
    fasta.write_text(">chr1\nACGT\n")
    settings["reference_library"] = str(lib)
    settings["reference_genome_library"] = str(genomes)

    assert reference_genomes_dir(lib) == str(genomes)
    assert library_of_genomes_dir(genomes) == str(lib)
    assert reference_library_of(fasta) == str(lib)
    roots = library_roots(str(fasta))
    assert roots == (str(lib), str(fasta), None)
    # Another library named explicitly keeps its own genomes folder.
    other = tmp_path / "other"
    assert reference_genomes_dir(other) == str(other / "genomes")


def test_dataset_library_reads_the_old_key(tmp_path, monkeypatch):
    settings.pop("dataset_library", None)
    settings["genome_library"] = str(tmp_path / "people")
    assert dataset_library() == str(tmp_path / "people")
    settings["dataset_library"] = str(tmp_path / "datasets")
    assert dataset_library() == str(tmp_path / "datasets")


def test_config_file_genome_library_loads_as_dataset_library(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(f'genome_library = "{Path(tmp_path, "people").as_posix()}"\n')
    monkeypatch.setattr(config, "get_config_path", lambda: path)
    loaded = config.load_config()
    assert Path(loaded["dataset_library"]) == tmp_path / "people"
