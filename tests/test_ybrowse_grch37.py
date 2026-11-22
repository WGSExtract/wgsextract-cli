"""GRCh37 targeted Y VCF lifted from the installed GRCh38 yBrowse sites (#1186)."""

import gzip
import json
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract, sam_to_bam, write_fasta
from tests.test_ybrowse import mini_snapshot
from wgsextract_cli.core import ybrowse, ybrowse_grch37
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.network_policy import DownloadRequired
from wgsextract_cli.core.utils import WGSExtractError, run_command

Y38 = ybrowse.Y_LENGTH
Y37 = ybrowse_grch37.Y_LENGTH
# A synthetic GRCh38 -> GRCh37 chain (0-based, half-open blocks): +100 for
# 0-1000; 1000-1100 reverse onto 5000-5100 (GRCh37 pos = 6101 - GRCh38 pos);
# 2000-2010 onto two places, one of which 2100-2110 lands on alone; 3000-3010 and 3100-3110 onto the same place, and
# 4000-4010 and 4100-4110 too.
CHAIN = "".join(
    f"chain {score} chrY {Y38} + {start} {end} Y {Y37} {strand} {q} {q + end - start} "
    f"{score}\n{end - start}\n\n"
    for score, start, end, strand, q in (
        (100, 0, 1000, "+", 100),
        (90, 1000, 1100, "-", Y37 - 5100),
        (80, 2000, 2010, "+", 7000),
        (70, 2000, 2010, "+", 7100),
        (65, 2100, 2110, "+", 7000),
        (60, 3000, 3010, "+", 8000),
        (50, 3100, 3110, "+", 8000),
        (40, 4000, 4010, "+", 9000),
        (30, 4100, 4110, "+", 9000),
    )
)
# Synthetic GRCh38 alleles: where each lands on GRCh37 and what happens to it.
ROWS = [
    "chrY\t11\tSYN_C\tA\tC\t.\t.\tHG=R-C;ISOGG=R1;AA=A\tGT\t0",  # 111, kept
    "chrY\t11\tSYN_ALIAS\tA\tC\t.\t.\tHG=R-C2;AA=A\tGT\t0",  # its alias
    "chrY\t21\tSYN_INV\tA\tT\t.\t.\tHG=R-INV;AA=A\tGT\t0",  # 121 is T: swapped
    "chrY\t31\tSYN_NEITHER\tA\tG\t.\t.\tHG=R-X\tGT\t0",  # 131 is C: dropped
    "chrY\t55\tSYN_PAR\tA\tG\t.\t.\tHG=R-X\tGT\t0",  # 155 is N: dropped
    "chrY\t1051\tSYN_REV\tA\tG\t.\t.\tHG=R-REV;AA=A\tGT\t0",  # 5050, complemented
    "chrY\t2005\tSYN_MULTI\tA\tG\t.\t.\tHG=R-X\tGT\t0",  # placed twice: dropped
    # Placed once, but where SYN_MULTI may land: dropped as ambiguous too.
    "chrY\t2105\tSYN_MULTI_PARTNER\tA\tG\t.\t.\tHG=R-X\tGT\t0",
    "chrY\t3005\tSYN_COL1\tA\tG\t.\t.\tHG=R-X\tGT\t0",  # both on 8005: dropped
    "chrY\t3105\tSYN_COL2\tA\tG\t.\t.\tHG=R-X\tGT\t0",
    # A collision drops both sites even when only one passes the allele check.
    "chrY\t4005\tSYN_COL3\tA\tG\t.\t.\tHG=R-X\tGT\t0",
    "chrY\t4105\tSYN_COL4\tC\tG\t.\t.\tHG=R-X\tGT\t0",
    "chrY\t9001\tSYN_UNLIFTED\tA\tG\t.\t.\tHG=R-X\tGT\t0",  # no block
]


def grch37_y(path: Path, name: str) -> Path:
    """An all-A GRCh37-length chrY: 121 T, 131 C, 150-160 N (a masked PAR), 5050 T."""
    bases = bytearray(b"A" * Y37)
    bases[120], bases[130], bases[5049] = ord("T"), ord("C"), ord("T")
    bases[149:160] = b"N" * 11
    return write_fasta(path, {name: bases.decode()})


def library_with_grch37(
    tmp_path: Path, name: str = "Y", *, name_fasta: bool = True
) -> tuple[Path, Path]:
    """A library holding a GRCh37 FASTA and the GRCh37 sites lifted from ROWS,
    the FASTA named with --ref, or found in the library's genomes folder."""
    library = tmp_path / "library"
    fasta = grch37_y(library / "genomes" / "b37.fa", name)
    chain = tmp_path / "test.chain"
    chain.write_text(CHAIN, encoding="utf-8")
    run_wgsextract(
        "ref", "ybrowse", "--install", "--grch37",
        "--snapshot", str(mini_snapshot(tmp_path / "mini.vcf", ROWS)),
        "--chain", str(chain), "--ref", str(fasta if name_fasta else library),
    )  # fmt: skip
    return library, fasta


@needs_htslib
def test_issue_1186_grch37_install_lifts_checks_and_counts_every_allele(tmp_path):
    library, _ = library_with_grch37(tmp_path, name_fasta=False)
    directory, manifest = ybrowse_grch37.installed_grch37(library)
    with gzip.open(directory / "sites.vcf.gz", "rt") as stream:
        records = [line.split("\t") for line in stream if not line.startswith("#")]
    assert [r[1:5] + [r[7].rstrip()] for r in records] == [
        ["111", "SYN_ALIAS;SYN_C", "A", "C", "HG=R-C,R-C2;ISOGG=R1;AA=A"],
        # GRCh37 has the GRCh38 ALT: the alleles swap, the ancestral allele stays.
        ["121", "SYN_INV", "T", "A", "HG=R-INV;AA=A"],
        # A reverse-strand block complements the alleles and AA.
        ["5050", "SYN_REV", "T", "C", "HG=R-REV;AA=T"],
    ]
    assert manifest["counts"] == {
        "grch38_alleles": 12,
        "unlifted": 1,
        "multi_hit_or_off_Y": 1,
        "reverse_strand": 1,
        "ref_alt_inverted": 1,
        "neither_allele_matches": 1,
        "grch37_reference_N": 1,
        "many_to_one": 5,
        "conflicting_ref": 0,
        "grch37_alleles": 3,
    }
    assert manifest["liftover"]["kind"] == "user-supplied"
    assert manifest["reference"]["fasta"] == "b37.fa"
    # A chain between other builds is refused, leaving the lifted sites as they were.
    wrong = tmp_path / "wrong.chain"
    wrong.write_text(CHAIN.replace(f"Y {Y37}", f"Y {Y37 + 1}"), encoding="utf-8")
    with pytest.raises(WGSExtractError, match="not a chain between these builds"):
        run_wgsextract(
            "ref", "ybrowse", "--install", "--grch37", "--chain", str(wrong),
            "--ref", str(library),
        )  # fmt: skip
    assert ybrowse_grch37.installed_grch37(library)[0] == directory
    # A different GRCh38 snapshot leaves the lifted sites stale until re-derived.
    ybrowse.install_snapshot(library, mini_snapshot(tmp_path / "new.vcf", ROWS[:2]))
    with pytest.raises(WGSExtractError, match="no longer installed.*--grch37"):
        ybrowse_grch37.installed_grch37(library)


@needs_htslib
@pytest.mark.parametrize("name", ["Y", "chrY"])
def test_issue_1186_grch37_targeted_call_annotates_lifted_markers(
    tmp_path, monkeypatch, name
):
    library, reference = library_with_grch37(tmp_path, name)
    monkeypatch.setitem(settings, "reference_library", str(library))
    lines = [f"@SQ\tSN:{name}\tLN:{Y37}"] + [
        f"r{i}\t{16 if i % 2 else 0}\t{name}\t111\t60\t20M\t*\t0\t0"
        "\tCAAAAAAAAATAAAAAAAAA\tIIIIIIIIIIIIIIIIIIII"
        for i in range(30)
    ]
    bam = sam_to_bam(lines, tmp_path / "sample.bam")
    out = tmp_path / "output"
    run_wgsextract(
        "extract", "ydna-vcf", "--ybrowse", "--input", bam, "--ref", str(reference),
        "--outdir", str(out), "--threads", "2",
    )  # fmt: skip
    output = out / "sample_Y_ybrowse.vcf.gz"
    records = run_command(
        ["bcftools", "query", "-f",
         "%POS\t%ID\t%REF\t%ALT\t%INFO/HG\t%INFO/ISOGG\t%INFO/AA[\t%GT]\n",
         str(output)],
        capture_output=True,
    ).stdout.splitlines()  # fmt: skip
    assert "111\tSYN_ALIAS;SYN_C\tA\tC\tR-C,R-C2\tR1\tA\t1" in records
    assert "121\tSYN_INV\tT\tA\tR-INV\t.\tA\t0" in records
    # No reads cover the reverse-strand marker: it stays uncalled.
    assert not any(r.startswith("5050\t") and not r.endswith("\t.") for r in records)
    assert {r.split("\t")[0] for r in records} <= {"111", "121", "5050"}
    header = run_command(
        ["bcftools", "view", "-h", str(output)], capture_output=True
    ).stdout
    assert f'##copyright="{ybrowse.COPYRIGHT}"' in header
    assert '##ybrowse_liftover_chain="test.chain"' in header
    assert f"##ybrowse_scope={ybrowse_grch37.SCOPE};" in header
    counts = json.loads(header.split("##ybrowse_liftover_counts=")[1].split("\n")[0])
    assert counts["grch37_alleles"] == 3


@needs_htslib
def test_issue_1186_missing_chain_needs_download_and_installs_nothing(tmp_path):
    library = tmp_path / "library"
    ybrowse.install_snapshot(library, mini_snapshot(tmp_path / "mini.vcf", ROWS))
    with pytest.raises(DownloadRequired, match="GRCh38_to_GRCh37.chain.gz.*--download"):
        run_wgsextract("ref", "ybrowse", "--install", "--grch37", "--ref", str(library))
    # A library copy that isn't Ensembl's pinned chain is refused, not used.
    ybrowse_grch37.library_chain(library).write_text(CHAIN, encoding="utf-8")
    with pytest.raises(WGSExtractError, match="not Ensembl's pinned"):
        ybrowse_grch37.lift_chain(library, None, download=True)
    assert not (library / "ybrowse" / "hg19").exists()
    with pytest.raises(WGSExtractError, match="No GRCh37 yBrowse resource"):
        ybrowse_grch37.installed_grch37(library)
