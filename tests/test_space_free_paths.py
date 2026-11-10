from pathlib import Path

import pytest

from wgsextract_cli.core.space_free_paths import (
    READS_INDEXES,
    REFERENCE_INDEXES,
    space_free_folder,
    space_free_link,
)


def test_a_compressed_reference_gets_only_its_own_indexes(tmp_path):
    """#632 review: ``my ref.fa.gz`` must not take ``my ref.fa.fai`` (the
    index of a different, uncompressed FASTA) as its index."""
    home = tmp_path / "John Smith"
    home.mkdir()
    for name in ("my ref.fa.gz", "my ref.fa.fai", "my ref.fa.gz.gzi"):
        (home / name).write_text(name)
    with space_free_folder("Tool") as links:
        link = Path(
            space_free_link(
                home / "my ref.fa.gz", links / "ref.fa.gz", REFERENCE_INDEXES
            )
        )
        assert sorted(p.name for p in links.iterdir()) == ["ref.fa.gz", "ref.fa.gz.gzi"]
        assert Path(f"{link}.gzi").read_text() == "my ref.fa.gz.gzi"


@pytest.mark.parametrize("reads", ["my reads.bam", "my reads.CRAM"])
def test_reads_take_an_index_named_without_their_suffix(tmp_path, reads):
    home = tmp_path / "John Smith"
    home.mkdir()
    (home / reads).write_text("reads")
    index = ".crai" if reads.lower().endswith(".cram") else ".bai"
    (home / reads).with_suffix(index).write_text("index")
    with space_free_folder("Tool") as links:
        link = space_free_link(home / reads, links / "reads", READS_INDEXES)
        assert Path(f"{link}{index}").read_text() == "index"


def test_paths_without_spaces_are_used_as_they_are(tmp_path):
    (tmp_path / "reads.bam").write_text("reads")
    with space_free_folder("Tool") as links:
        assert space_free_link(tmp_path / "reads.bam", links / "x.bam") == str(
            tmp_path / "reads.bam"
        )
        assert not any(links.iterdir())
