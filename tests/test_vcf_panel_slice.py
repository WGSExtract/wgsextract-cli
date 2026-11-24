"""Issue #1290: gene and BED panels slice VCF records offline."""

import csv
import gc
import gzip
import hashlib
import json
import sqlite3
import warnings
from contextlib import closing
from pathlib import Path

import pytest

from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    reference_for_vcf,
    run_wgsextract,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.core.results import manifest_path_for
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.main import main

_FIXTURE = Path(__file__).parent / "fixtures" / "vcf_panel_grch38.vcf"
pytestmark = needs_htslib


@pytest.fixture(params=["chr", "bare"])
def panel_inputs(tmp_path, request):
    prefixed = request.param == "chr"
    text = _FIXTURE.read_text(encoding="utf-8")
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(text if prefixed else text.replace("chr", ""), encoding="utf-8")
    bed = tmp_path / "panel.bed"
    bed_chrom = "" if prefixed else "chr"
    bed.write_text(
        f"{bed_chrom}1\t99\t100\n{bed_chrom}17\t43044499\t43044500\n",
        encoding="utf-8",
    )
    genes = tmp_path / "genes.txt"
    genes.write_text("BRCA2,\nBRCA1 # comment\n", encoding="utf-8")
    return bgzip_index(vcf), bed, genes


def _ids(vcf):
    return run_command(
        ["bcftools", "query", "-f", r"%ID\n", str(vcf)], capture_output=True
    ).stdout.splitlines()


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (("--genes", "BRCA1,BRCA2"), ["brca2", "brca1", "lowqual_brca1"]),
        (("--gene-file", "{genes}"), ["brca2", "brca1", "lowqual_brca1"]),
        (("--preset", "acmg-sf"), ["brca2", "tp53", "brca1", "lowqual_brca1"]),
        (("--bed", "{bed}"), ["bed", "brca1"]),
    ],
)
def test_1290_filter_reads_bundled_panels_and_bed(
    panel_inputs, tmp_path, selection, expected
):
    vcf, bed, genes = panel_inputs
    out = tmp_path / "filter"
    run_wgsextract(
        "vcf", "filter", "--vcf-input", vcf, "--outdir", str(out),
        *(part.format(bed=bed, genes=genes) for part in selection),
    )  # fmt: skip
    assert _ids(out / "filtered.vcf.gz") == expected


def test_1290_filter_unions_sources_before_region_and_expression(
    panel_inputs, tmp_path
):
    vcf, bed, genes = panel_inputs
    genes.write_text("RAD51C\n", encoding="utf-8")
    out = tmp_path / "filter"
    main(
        [
            "vcf", "filter", "--vcf-input", vcf, "--outdir", str(out),
            "--gene", "BRCA1", "--gene", "BRCA2", "--gene-file", str(genes),
            "--bed", str(bed), "--region", "chr1,13,17:43044500-43044501",
            "--expr", "QUAL>30",
        ]
    )  # fmt: skip
    result = out / "filtered.vcf.gz"
    assert _ids(result) == ["bed", "brca2", "brca1"]
    manifest = json.loads(manifest_path_for(result).read_text(encoding="utf-8"))
    panel = manifest["details"]["panel"]
    assert panel["genes"] == ["BRCA1", "BRCA2", "RAD51C"]
    assert panel["reference_build"] == "hg38"
    assert panel["bed_sha256"] == hashlib.sha256(bed.read_bytes()).hexdigest()
    prefix = "chr" if bed.read_bytes().startswith(b"1\t") else ""
    assert {(i["chrom"], i["start"], i["end"]) for i in panel["intervals"]} == {
        (f"{prefix}1", 99, 100),
        (f"{prefix}13", 32315076, 32400268),
        (f"{prefix}17", 43044499, 43044501),
    }
    assert manifest["metrics"]["region_intervals"] == 3


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("bed", [100, 43044500]),
        ("preset", [32316000, 7669000, 43044500, 43044501]),
    ],
)
def test_1290_table_keeps_provenance_and_annotations_when_sliced(
    panel_inputs, tmp_path, selection, expected
):
    vcf, bed, _ = panel_inputs
    out = tmp_path / "table"
    options = {"bed": bed} if selection == "bed" else {"presets": ("acmg-sf",)}
    result = api.vcf.table(
        api.VcfTableParams(vcf_input=Path(vcf), outdir=out, **options)
    )
    with (out / "sample_variants.tsv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert [int(row["pos"]) for row in rows] == expected
    assert (
        next(row for row in rows if row["pos"] == "43044500")["clinvar_significance"]
        == "Benign"
    )
    with closing(sqlite3.connect(out / "sample_variants.sqlite")) as db:
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
    assert metadata["sample"] == "TEST"
    assert metadata["reference_build"] == "hg38"
    assert (
        metadata["source_sha256"] == hashlib.sha256(Path(vcf).read_bytes()).hexdigest()
    )
    assert metadata["annotations"] == "clinvar"
    assert result.metrics["rows"] == len(expected)


@pytest.mark.parametrize("broken_index", [False, True], ids=["success", "read-error"])
def test_1473_vcf_table_api_does_not_leak_a_pipe(
    tmp_path: Path, broken_index: bool
) -> None:
    plain = tmp_path / "sample.vcf"
    plain.write_bytes(_FIXTURE.read_bytes())
    vcf = Path(bgzip_index(plain))
    out = tmp_path / "out"
    if broken_index:
        Path(f"{vcf}.tbi").write_text("not a tabix index", encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        result = api.vcf.table(
            api.VcfTableParams(input=vcf, outdir=out, region="chr1:100-100"),
            check=False,
        )
        gc.collect()

    assert not [w.message for w in caught if issubclass(w.category, ResourceWarning)]
    table = out / "sample_variants.tsv"
    if broken_index:
        assert result.exit_code == 1
        assert result.error is not None
        assert "bcftools failed reading" in result.error.message
        assert "index" in result.error.message
        assert not table.exists()
    else:
        assert result.exit_code == 0
        with table.open(encoding="utf-8", newline="") as stream:
            assert [row["pos"] for row in csv.DictReader(stream, delimiter="\t")] == [
                "100"
            ]


@pytest.mark.parametrize("command", ["filter", "table"])
def test_1290_disjoint_panel_and_region_return_header_only(
    panel_inputs, tmp_path, command
):
    vcf, _, _ = panel_inputs
    out = tmp_path / "empty"
    run_wgsextract(
        "vcf", command, "--vcf-input", vcf, "--outdir", str(out),
        "--gene", "BRCA1", "--region", "1",
    )  # fmt: skip
    if command == "filter":
        filtered = out / "filtered.vcf.gz"
        assert _ids(filtered) == []
        assert (
            run_command(
                ["bcftools", "query", "-l", str(filtered)], capture_output=True
            ).stdout.strip()
            == "TEST"
        )
    else:
        with (out / "sample_variants.tsv").open(encoding="utf-8", newline="") as stream:
            rows = csv.DictReader(stream, delimiter="\t")
            assert rows.fieldnames is not None and "rsid" in rows.fieldnames
            assert list(rows) == []


def test_1290_errors_name_unknown_and_ambiguous_genes(tmp_path):
    with pytest.raises(UsageError) as error:
        run_wgsextract(
            "vcf", "filter", "--vcf-input", str(_FIXTURE), "--outdir", str(tmp_path),
            "--genes", "UNKNOWN1290,FANCD",
        )  # fmt: skip
    assert "UNKNOWN1290" in str(error.value)
    assert "FANCD: an alias of several genes" in str(error.value)
    assert not (tmp_path / "filtered.vcf.gz").exists()


def test_1290_does_not_guess_build_from_vcf_filename(tmp_path):
    vcf = tmp_path / "misleading_GRCh38.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr17,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        encoding="utf-8",
    )
    with pytest.raises(UsageError, match="Cannot determine the VCF's assembly"):
        run_wgsextract(
            "vcf", "table", "--vcf-input", str(vcf), "--outdir", str(tmp_path),
            "--gene", "BRCA1",
        )  # fmt: skip


def test_1290_uses_assembly_metadata_and_pads_gene_not_bed(tmp_path):
    vcf = tmp_path / "sample_hg19.vcf"
    header = (
        '##fileformat=VCFv4.2\n##contig=<ID=chr17,assembly="GRCh38.p14">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    )
    vcf.write_text(
        header
        + "chr17\t43044293\toutside_padding\tA\tC\t50\tPASS\t.\n"
        + "chr17\t43044294\tone_base_padding\tA\tC\t50\tPASS\t.\n"
        + "chr17\t45000000\tdeletion\tAAA\tA\t50\tPASS\t.\n"
        + "chr17\t45000003\toutside_bed\tA\tC\t50\tPASS\t.\n",
        encoding="utf-8",
    )
    bed = tmp_path / "targets.bed.gz"
    with gzip.open(bed, "wt", encoding="utf-8") as stream:
        stream.write("17\t45000001\t45000002\n")
    api.vcf.filter(
        api.VcfFilterParams(
            vcf_input=vcf, outdir=tmp_path, gene="BRCA1", bed=bed, pad_kb=0.001
        )
    )
    assert _ids(tmp_path / "filtered.vcf.gz") == ["one_base_padding", "deletion"]


@pytest.mark.parametrize("declared", ["chr17", "chr1", None])
def test_1290_unrelated_default_reference_does_not_clip_lengthless_vcf(
    tmp_path, declared
):
    vcf = tmp_path / "input.vcf"
    header = "##fileformat=VCFv4.2\n##assembly=GRCh38.p14\n"
    if declared:
        header += f"##contig=<ID={declared}>\n"
    vcf.write_text(
        header
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        + "chr17\t43044500\tbrca1\tA\tC\t50\tPASS\t.\n",
        encoding="utf-8",
    )
    other = write_fasta(tmp_path / "mini.fa", {"chr17": "A" * 100})
    config = tmp_path / "config.toml"
    config.write_text(
        state_directory_setting(tmp_path)
        + f"reference_fasta = {json.dumps(str(other))}\n",
        encoding="utf-8",
    )
    main(
        [
            "--config", str(config), "vcf", "filter", "--vcf-input", str(vcf),
            "--outdir", str(tmp_path), "--gene", "BRCA1",
        ]
    )  # fmt: skip
    assert _ids(tmp_path / "filtered.vcf.gz") == ["brca1"]


def test_1290_build_resolution_can_use_an_indexed_reference_in_a_library(tmp_path):
    vcf = tmp_path / "in_chr17.vcf"
    template = (
        "##fileformat=VCFv4.2\n##contig=<ID=chr17{length}>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr17\t43044500\tbrca1\tA\tC\t50\tPASS\t.\n"
    )
    vcf.write_text(template.format(length=",length=83257441"), encoding="utf-8")
    library = tmp_path / "reference"
    reference_for_vcf(library / "genomes" / "custom.fa.gz", vcf)
    vcf.write_text(template.format(length=""), encoding="utf-8")
    api.vcf.filter(
        api.VcfFilterParams(vcf_input=vcf, outdir=tmp_path, gene="BRCA1", ref=library)
    )
    assert _ids(tmp_path / "filtered.vcf.gz") == ["brca1"]


def test_1290_long_deletion_overlaps_disjoint_targets_only_once(tmp_path):
    vcf = tmp_path / "deletion.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=1,length=248956422>\n"
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End position">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t10\tlong_deletion\tA\t<DEL>\t50\tPASS\tEND=2000000\n"
        "1\t21\tfirst\tA\tT\t50\tPASS\t.\n"
        "1\t26\toutside\tA\tT\t50\tPASS\t.\n"
        "1\t1000001\tsecond\tA\tT\t50\tPASS\t.\n",
        encoding="utf-8",
    )
    bed = tmp_path / "targets.bed"
    bed.write_text("chr1\t20\t21\nchr1\t1000000\t1000001\n", encoding="utf-8")

    result = api.vcf.filter(
        api.VcfFilterParams(vcf_input=vcf, outdir=tmp_path, bed=bed)
    )
    filtered = tmp_path / "filtered.vcf.gz"
    assert _ids(filtered) == ["long_deletion", "first", "second"]
    assert next(o for o in result.outputs if o.role == "filtered_vcf").index
