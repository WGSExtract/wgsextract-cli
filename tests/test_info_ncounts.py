"""N counts: each reference's own, from the shipped or library table, or
its FASTA's `ref count-ns` file."""

from pathlib import Path


def _library_entry(folder: Path, name: str, lengths: dict, ns: dict) -> Path:
    """A FASTA file named `name` whose library N-count table measured `ns`."""
    from wgsextract_cli.core.ref_library import ncount_table
    from wgsextract_cli.core.ref_library.assets import sequence_signature

    counts = {seq: (length, ns.get(seq, 0)) for seq, length in lengths.items()}
    entry = ncount_table.measured_entry(name, sequence_signature(lengths), counts)
    ncount_table.add_to_library(folder, entry)
    (folder / name).write_text(">placeholder\nA\n")
    return folder / name


def test_n_counts_come_from_the_references_own_measurement(tmp_path):
    """The user's report: N counts must be the reference's own, not one
    generic table per build, and apply only to contigs of the measured length."""
    from wgsextract_cli.commands.info.ncounts import reference_n_lengths
    from wgsextract_cli.core.ref_library.assets import sequence_signature

    lengths = {"chr1": 1000, "chr20": 2000}
    fasta = _library_entry(tmp_path, "mine.fa.gz", lengths, {"chr1": 300, "chr20": 50})
    found = reference_n_lengths(lengths, sequence_signature(lengths), None)
    assert found.problem and found.problem[0] == "n_counts_unavailable"
    found = reference_n_lengths(lengths, sequence_signature(lengths), str(fasta))
    assert found == ({"1": 300, "20": 50}, None)
    # A 1 kb slice of chr20 keeps all its bases.
    sliced = {"chr1": 1000, "chr20": 1000}
    assert reference_n_lengths(sliced, sequence_signature(sliced), str(fasta)) == (
        {"1": 300},
        None,
    )


def test_one_signature_with_different_n_masks_needs_the_fasta(tmp_path, monkeypatch):
    """FASTAs can share a sequence dictionary (hs38d1 and GRCh38_Verily_v1) yet
    could mask different regions, so the signature alone can't pick counts."""
    from wgsextract_cli.commands.info.ncounts import reference_n_lengths
    from wgsextract_cli.core.config import settings
    from wgsextract_cli.core.ref_library.assets import sequence_signature

    monkeypatch.setitem(settings, "reference_library", str(tmp_path))
    genomes = tmp_path / "genomes"
    genomes.mkdir()
    lengths = {"chr1": 1000}
    a = _library_entry(genomes, "a.fa.gz", lengths, {"chr1": 10})
    _library_entry(genomes, "b.fa.gz", lengths, {"chr1": 20})
    signature = sequence_signature(lengths)
    found = reference_n_lengths(lengths, signature, None)
    assert found.n_lengths == {}
    assert found.problem and found.problem[0] == "n_counts_ambiguous"
    assert reference_n_lengths(lengths, signature, str(a)).n_lengths == {"1": 10}
    # Review of #1271: a user's own FASTA never takes another genome's counts.
    other = tmp_path / "elsewhere.fa"
    other.write_text(">chr1\nA\n")
    found = reference_n_lengths(lengths, signature, str(other))
    assert found.problem and found.problem[0] == "n_counts_unavailable"


def test_every_catalog_genome_has_measured_n_counts():
    """Each genome the library can download ships its own N counts, under the
    catalog's sequence-dictionary signature."""
    from wgsextract_cli.core.ref_library.catalog import get_available_genomes
    from wgsextract_cli.core.ref_library.ncount_table import packaged_table

    table = {e.reference: e for e in packaged_table()}
    for genome in get_available_genomes():
        entry = table[genome["final"]]
        assert entry.total[1] == sum(ns for _len, ns in entry.sequences.values())
        if genome["md5"]:
            assert entry.signature == genome["md5"], genome["final"]


def test_a_ref_fastas_own_n_counts_win_and_need_the_measured_length(tmp_path):
    """Review of #1271: a --ref FASTA's own `ref count-ns` file lost to a table
    entry sharing its signature, and applied to contigs of any length."""
    from wgsextract_cli.commands.info.ncounts import reference_n_lengths
    from wgsextract_cli.core.ref_library.assets import sequence_signature

    lengths = {"chr1": 1000, "chr2": 500}
    _library_entry(tmp_path, "catalog.fa.gz", lengths, {"chr1": 10})
    mine = tmp_path / "mine" / "mine.fa"
    mine.parent.mkdir()
    mine.write_text(">chr1\nA\n")
    (mine.parent / "mine_ncnt.csv").write_text(
        "#Seq\tNumBP\tNumNs\nchr1\t1000\t300\nchr2\t999\t50\n"
    )
    found = reference_n_lengths(lengths, sequence_signature(lengths), str(mine))
    assert found == ({"1": 300}, None)  # chr2's count is for a 999 bp chr2


def test_a_named_reference_without_these_contigs_says_so(tmp_path):
    from wgsextract_cli.commands.info.ncounts import reference_n_lengths

    fasta = _library_entry(tmp_path, "num.fa.gz", {"1": 1000}, {"1": 10})
    found = reference_n_lengths({"chr1": 1000}, None, str(fasta))
    assert found.n_lengths == {}
    assert found.problem and found.problem[0] == "n_counts_unavailable"


def test_n_counts_never_cross_contigs_or_lengths(tmp_path):
    """Review of #1271: a two-column sidecar row has no length to check, and
    chr1 and 1 in one header share a key, so neither may apply."""
    from wgsextract_cli.commands.info.ncounts import reference_n_lengths

    fasta = tmp_path / "mine.fa"
    fasta.write_text(">chr1\nA\n")
    (tmp_path / "mine_ncnt.csv").write_text("#SN,NumNs\nchr1,5000\n")
    found = reference_n_lengths({"chr1": 1000}, None, str(fasta))
    assert found.problem and found.problem[0] == "n_counts_unavailable"
    (tmp_path / "mine_ncnt.csv").write_text(
        "#Seq\tNumBP\tNumNs\nchr1\t1000\t300\n1\t500\t0\nchr2\t800\t8\n"
    )
    both = {"chr1": 1000, "1": 500, "chr2": 800}
    assert reference_n_lengths(both, None, str(fasta)).n_lengths == {"2": 8}
