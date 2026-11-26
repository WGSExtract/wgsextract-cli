"""vcf prepare (#1292): consumer-VCF repairs with real bcftools, tabix and samtools."""

import gzip
import hashlib
import os
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib, run_wgsextract, write_fasta
from tests.smoke_utils import operation_warnings
from wgsextract_cli import api
from wgsextract_cli.core.bgzf import is_bgzf
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib

# chr1 has an A homopolymer at 501-510, so a 1 bp deletion written at its
# right end (509 AA>A) left-aligns to 500 (CA>C).
_CHR1 = list(("ACGTTGCA" * 250)[:2000])
_CHR1[500:510] = "A" * 10
SEQS = {"chr1": "".join(_CHR1), "chr2": "TGCA" * 375, "chrM": "CAGT" * 75}
FORMAT = '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
INFO = '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"


def _base(contig: str, pos: int) -> str:
    return SEQS[contig][pos - 1]


def _alt(ref: str) -> str:
    return "C" if ref != "C" else "G"


def _record(name: str, contig: str, pos: int, info: str = "DP=10") -> str:
    ref = _base(contig, pos)
    return f"{name}\t{pos}\t.\t{ref}\t{_alt(ref)}\t50\tPASS\t{info}\tGT\t0/1\n"


# A provider's VCF: 1/2/MT names for a chr-named reference, no ##contig
# lines, records out of order, an undeclared INFO key, a multiallelic site,
# an indel that isn't left-aligned and a REF that differs from the reference.
CONSUMER_RECORDS = [
    _record("2", "chr2", 100),
    _record("1", "chr1", 300, "DP=10;XQ=7"),
    _record("1", "chr1", 200),
    _record("MT", "chrM", 50),
    f"1\t400\t.\t{_base('chr1', 400)}\tC,T\t50\tPASS\tDP=10\tGT\t1/2\n",
    "1\t509\t.\tAA\tA\t50\tPASS\tDP=10\tGT\t0/1\n",
    f"1\t600\t.\tG\t{_base('chr1', 600)}\t50\tPASS\tDP=10\tGT\t0/1\n",
]


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    return write_fasta(tmp_path / "ref" / "ref.fa", SEQS)


def _consumer_vcf(path: Path) -> Path:
    text = "##fileformat=VCFv4.2\n" + FORMAT + INFO + COLUMNS
    with gzip.open(path, "wt", newline="\n") as stream:  # plain gzip, not BGZF
        stream.write(text + "".join(CONSUMER_RECORDS))
    return path


def _valid_vcf(tmp_path: Path) -> Path:
    contigs = "".join(
        f"##contig=<ID={name},length={len(seq)}>\n" for name, seq in SEQS.items()
    )
    plain = tmp_path / "valid.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n" + contigs + FORMAT + INFO + COLUMNS
        + _record("chr1", "chr1", 200) + _record("chr2", "chr2", 100),
        newline="\n",
    )  # fmt: skip
    return Path(bgzip_index(plain))


def _records(vcf: Path) -> list[list[str]]:
    out = run_command(["bcftools", "view", "-H", str(vcf)], capture_output=True)
    return [line.split("\t") for line in out.stdout.splitlines()]


def _codes(result: api.Result) -> dict[str, dict]:
    return {warning.code: warning.details for warning in operation_warnings(result)}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_consumer_vcf_is_recompressed_renamed_declared_sorted_and_indexed(
    tmp_path, reference
):
    vcf = _consumer_vcf(tmp_path / "provider.vcf.gz")
    before = _digest(vcf)

    result = api.vcf.prepare(
        api.VcfPrepareParams(input=vcf, ref=reference, outdir=tmp_path / "out")
    )

    prepared = tmp_path / "out" / "provider_prepared.vcf.gz"
    assert _digest(vcf) == before  # the input is never modified
    assert is_bgzf(str(prepared))
    assert Path(f"{prepared}.tbi").is_file()
    header = run_command(
        ["bcftools", "view", "-h", str(prepared)], capture_output=True
    ).stdout
    assert "##contig=<ID=chr1,length=2000>" in header
    assert "##contig=<ID=chrM,length=300>" in header
    assert "##INFO=<ID=XQ," in header
    rows = _records(prepared)
    assert [(row[0], int(row[1])) for row in rows] == sorted(
        ((row[0], int(row[1])) for row in rows),
        key=lambda site: (list(SEQS).index(site[0]), site[1]),
    )
    # Every record, genotype and INFO value survives; only names change.
    renamed = {"1": "chr1", "2": "chr2", "MT": "chrM"}
    expected = sorted(
        [renamed[f[0]], *f[1:]]
        for f in (line.rstrip("\n").split("\t") for line in CONSUMER_RECORDS)
    )
    assert sorted(rows) == expected
    codes = _codes(result)
    assert codes["vcf_prepare_recompressed"]["input_compression"] == "gzip"
    assert codes["vcf_prepare_contigs_renamed"]["contigs"] == 3
    assert codes["vcf_prepare_contigs_declared"]["contigs"] == 3
    assert codes["vcf_prepare_ids_declared"]["ids"] == 1
    assert codes["vcf_prepare_sorted"]["out_of_order_records"] == 2
    assert codes["vcf_prepare_ref_mismatch"]["records"] == 1
    assert codes["vcf_prepare_not_left_aligned"]["records"] == 1
    assert result.metrics["records_in"] == result.metrics["records_out"] == 7


def test_normalize_splits_multiallelics_and_left_aligns_keeping_every_record(
    tmp_path, reference
):
    vcf = _consumer_vcf(tmp_path / "provider.vcf.gz")

    result = api.vcf.prepare(
        api.VcfPrepareParams(
            input=vcf, ref=reference, outdir=tmp_path / "out", normalize=True
        )
    )

    rows = _records(tmp_path / "out" / "provider_prepared.vcf.gz")
    sites = {(row[0], int(row[1]), row[3], row[4]): row for row in rows}
    ref400 = _base("chr1", 400)
    assert ("chr1", 400, ref400, "C") in sites and ("chr1", 400, ref400, "T") in sites
    left_aligned = sites[("chr1", 500, _base("chr1", 500) + "A", _base("chr1", 500))]
    assert "OLD_VARIANT=chr1|509|AA|A" in left_aligned[7]
    # The record whose REF differs from the reference is kept unchanged.
    assert ("chr1", 600, "G", _base("chr1", 600)) in sites
    codes = _codes(result)
    assert codes["vcf_prepare_multiallelics_split"] == {
        "records": 1,
        "added_records": 1,
    }
    assert codes["vcf_prepare_left_aligned"]["records"] == 1
    assert codes["vcf_prepare_ref_mismatch"]["records"] == 1
    assert (result.metrics["records_in"], result.metrics["records_out"]) == (7, 8)


def test_prepared_output_needs_no_further_repairs(tmp_path, reference):
    vcf = _consumer_vcf(tmp_path / "provider.vcf.gz")
    api.vcf.prepare(
        api.VcfPrepareParams(
            input=vcf, ref=reference, outdir=tmp_path / "once", normalize=True
        )
    )
    once = tmp_path / "once" / "provider_prepared.vcf.gz"

    result = api.vcf.prepare(
        api.VcfPrepareParams(
            input=once, ref=reference, outdir=tmp_path / "twice", normalize=True
        )
    )

    assert result.metrics["repairs"] == 0
    assert set(_codes(result)) == {"vcf_prepare_ref_mismatch"}
    assert _digest(tmp_path / "twice" / "provider_prepared_prepared.vcf.gz") == (
        _digest(once)
    )


def test_valid_bgzf_vcf_is_copied_with_zero_repairs(tmp_path):
    vcf = _valid_vcf(tmp_path)

    run_wgsextract(
        "vcf", "prepare", "--input", str(vcf), "--outdir", str(tmp_path / "o")
    )

    prepared = tmp_path / "o" / "valid_prepared.vcf.gz"
    assert _digest(prepared) == _digest(vcf)
    assert Path(f"{prepared}.tbi").is_file()


def test_valid_vcf_records_no_warnings(tmp_path):
    result = api.vcf.prepare(
        api.VcfPrepareParams(input=_valid_vcf(tmp_path), outdir=tmp_path / "o")
    )

    assert not operation_warnings(result)
    assert result.metrics["input_index"] == "valid"


def test_stale_index_is_rebuilt(tmp_path):
    vcf = _valid_vcf(tmp_path)
    index = Path(f"{vcf}.tbi")
    os.utime(index, ns=(1, 1))

    result = api.vcf.prepare(api.VcfPrepareParams(input=vcf, outdir=tmp_path / "o"))

    assert _codes(result)["vcf_prepare_index_rebuilt"]["input_index"] == "stale"
    counted = run_command(
        [
            "bcftools",
            "index",
            "--nrecords",
            str(tmp_path / "o" / "valid_prepared.vcf.gz"),
        ],
        capture_output=True,
    )
    assert counted.stdout.strip() == "2"


@pytest.mark.parametrize("kind", ["plain", "bcf"])
def test_plain_text_and_bcf_inputs_become_bgzf(tmp_path, kind):
    vcf = _valid_vcf(tmp_path)
    source = tmp_path / f"in.{'vcf' if kind == 'plain' else 'bcf'}"
    run_command(
        ["bcftools", "view", "-O", "v" if kind == "plain" else "b", "-o", str(source),
         str(vcf)]
    )  # fmt: skip

    result = api.vcf.prepare(api.VcfPrepareParams(input=source, outdir=tmp_path / "o"))

    prepared = tmp_path / "o" / "in_prepared.vcf.gz"
    assert is_bgzf(str(prepared))
    assert _records(prepared) == _records(vcf)
    assert _codes(result)["vcf_prepare_recompressed"]["input_compression"] == kind


def test_records_on_contigs_missing_from_ref_are_kept_unnormalized(tmp_path, reference):
    plain = tmp_path / "decoy.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=2000>\n"
        "##contig=<ID=chrUn_decoy,length=500>\n" + FORMAT + INFO + COLUMNS
        + _record("chr1", "chr1", 200)
        + "chrUn_decoy\t20\t.\tCA\tC\t50\tPASS\tDP=3\tGT\t0/1\n",
        newline="\n",
    )  # fmt: skip

    result = api.vcf.prepare(
        api.VcfPrepareParams(
            input=plain, ref=reference, outdir=tmp_path / "o", normalize=True
        )
    )

    rows = _records(tmp_path / "o" / "decoy_prepared.vcf.gz")
    assert ["chrUn_decoy", "20", ".", "CA", "C"] in [row[:5] for row in rows]
    codes = _codes(result)
    assert codes["vcf_prepare_contigs_not_in_reference"]["contigs"] == 1


def test_ftdna_filter_expressions_point_to_repair_ftdna_vcf(tmp_path):
    """#1292: legacy FTDNA FILTERs are routed to `repair ftdna-vcf`."""
    fixture = Path(__file__).parent / "fixtures" / "ftdna_vcf" / "legacy_filters.vcf"

    result = api.vcf.prepare(
        api.VcfPrepareParams(input=fixture, outdir=tmp_path / "o"), check=False
    )

    assert result.error is not None and "repair ftdna-vcf" in result.error.message
    assert not list((tmp_path / "o").glob("*.vcf.gz"))


def test_vcf_for_another_build_is_refused_before_writing(tmp_path, reference):
    plain = tmp_path / "hg19.vcf"
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=249250621>\n"
        + FORMAT + INFO + COLUMNS + _record("chr1", "chr1", 200),
        newline="\n",
    )  # fmt: skip

    result = api.vcf.prepare(
        api.VcfPrepareParams(input=plain, ref=reference, outdir=tmp_path / "o"),
        check=False,
    )

    assert result.error is not None and "different reference" in result.error.message
    assert not list((tmp_path / "o").glob("*.vcf.gz"))


def test_output_naming_the_input_is_refused(tmp_path):
    vcf = _valid_vcf(tmp_path)
    before = _digest(vcf)

    result = api.vcf.prepare(api.VcfPrepareParams(input=vcf, output=vcf), check=False)

    assert result.error is not None and "overwrite the input" in result.error.message
    assert _digest(vcf) == before


def test_vcf_whose_refs_mostly_differ_from_ref_is_refused(tmp_path, reference):
    """Without contig lengths, mostly-wrong REFs show another build."""
    plain = tmp_path / "other_build.vcf"
    wrong = "".join(
        f"chr1\t{pos}\t.\t{_alt(_base('chr1', pos))}\t{_base('chr1', pos)}"
        "\t50\tPASS\tDP=10\tGT\t0/1\n"
        for pos in range(100, 1300, 40)
    )
    plain.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1>\n" + FORMAT + INFO + COLUMNS + wrong,
        newline="\n",
    )

    result = api.vcf.prepare(
        api.VcfPrepareParams(input=plain, ref=reference, outdir=tmp_path / "o"),
        check=False,
    )

    assert result.error is not None and "another build" in result.error.message
    assert not list((tmp_path / "o").glob("*.vcf.gz"))


def test_rerun_retires_an_old_short_name_index(tmp_path):
    """#1408 review: htslib could pick a stale sample.vcf.csi over the new index."""
    vcf = _valid_vcf(tmp_path)
    stale = tmp_path / "o" / "valid_prepared.vcf.csi"
    stale.parent.mkdir()
    stale.write_bytes(b"stale")

    api.vcf.prepare(api.VcfPrepareParams(input=vcf, outdir=tmp_path / "o"))

    assert not stale.exists()
    assert Path(tmp_path / "o" / "valid_prepared.vcf.gz.tbi").is_file()


def test_bgz_output_beside_input_keeps_the_inputs_short_name_index(tmp_path):
    """#1408 review: a .vcf.bgz output must not retire valid.vcf.gz's valid.vcf.tbi."""
    vcf = _valid_vcf(tmp_path)
    shared = tmp_path / "valid.vcf.tbi"
    Path(f"{vcf}.tbi").replace(shared)

    result = api.vcf.prepare(
        api.VcfPrepareParams(input=vcf, output=tmp_path / "valid.vcf.bgz"),
        check=False,
    )

    assert result.error is not None and "shares" in result.error.message
    assert shared.is_file()
