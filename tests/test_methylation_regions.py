"""Region summaries use the right build, CpG anchor and coverage (#1293)."""

import csv
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from wgsextract_cli.api import methylation
from wgsextract_cli.cli import platform_support
from wgsextract_cli.core.config import save_config
from wgsextract_cli.core.methylation_annotations import island_regions, promoter_regions
from wgsextract_cli.main import main

FIXTURES = Path(__file__).parent / "fixtures" / "methylation_regions"
needs_samtools = pytest.mark.skipif(
    not shutil.which("samtools"), reason="needs samtools"
)


def test_1293_promoter_choice_and_strand_windows():
    from scripts.build_methylation_annotations import selected_transcripts

    with (FIXTURES / "transcripts.gff").open(encoding="utf-8", newline="") as lines:
        transcripts = selected_transcripts(lines, {"NC_TEST.1": "chr21"})
    rows = {
        r.name: (r.start, r.end, r.transcript, r.strand)
        for r in promoter_regions(transcripts, {"chr21": 100}, 12, 4)
    }
    assert rows == {
        "GOOD": (0, 6, "NM_1.1", "+"),
        "REVERSE": (87, 100, "NR_10.1", "-"),
        "NO_RNA": (None, None, None, None),
        "SELECT": (9, 25, "NR_7.1", "+"),
        "TRNA": (30, 46, "rna-gene-e", "-"),
    }
    with (FIXTURES / "islands.txt").open(encoding="utf-8", newline="") as lines:
        assert [
            (r.chrom, r.start, r.end) for r in island_regions(lines, ucsc_table=True)
        ] == [
            ("chr21", 49616, 50090),
            ("chr21", 236555, 236851),
        ]


@pytest.fixture
def island_params(tmp_path):
    # Synthetic bases on the correct-length hs1 chr21; the input uses the bare alias.
    fasta = tmp_path / "reference.fa"
    probe = bytearray(b"N" * 236_600)
    for start in (49616, 49620, 49624, 50089, 236555):
        probe[start : start + 2] = b"CG"
    with fasta.open("wb") as out:
        out.write(b">NC_060945.1\n")
        first_block = bytes(probe).ljust(1_000_000, b"N")
        out.writelines(
            first_block[i : i + 100] + b"\n" for i in range(0, len(first_block), 100)
        )
        for _ in range(44):
            out.write((b"N" * 100 + b"\n") * 10_000)
        out.write((b"N" * 100 + b"\n") * 906)
        out.write(b"N" * 82 + b"\n")
        out.write(b">chrM\n" + b"N" * 16_569 + b"\n")
    subprocess.run(["samtools", "faidx", str(fasta)], check=True)
    return methylation.MethylationRegionsParams(
        input=FIXTURES / "sites.bedmethyl",
        ref=fasta,
        build="t2t",
        preset="cpg-islands",
        outdir=tmp_path / "results",
    )


@needs_samtools
def test_1293_windows_precomputed_regions_report_coverage_and_nulls(
    island_params,
    monkeypatch,
):
    # Only the CLI's early platform check is simulated, not subprocess/Path behavior.
    monkeypatch.setattr(platform_support, "sys", SimpleNamespace(platform="win32"))
    params = island_params
    main(
        [
            "methylation",
            "regions",
            "-i",
            str(params.input),
            "--ref",
            str(params.ref),
            "--build",
            params.build,
            "--preset",
            params.preset,
            "-o",
            str(params.outdir),
        ]
    )
    table = next(params.outdir.glob("*.tsv"))
    with table.open(encoding="utf-8", newline="") as f:
        rows = {r["region"]: r for r in csv.DictReader(f, delimiter="\t")}
    first = rows["chr21:49616-50090"]
    assert first["status"] == "assessed"
    assert (
        first["expected_cpgs"],
        first["observed_cpgs"],
        first["qualified_cpgs"],
    ) == ("4", "4", "3")
    assert (
        first["observed_valid_calls"],
        first["valid_calls"],
        first["modified_calls"],
    ) == ("24", "20", "9")
    assert float(first["pooled_percent"]) == 45
    assert float(first["mean_percent"]) == pytest.approx(140 / 3)
    assert float(first["median_percent"]) == 40
    low = rows["chr21:236555-236851"]
    assert (low["status"], low["pooled_percent"], low["observed_valid_calls"]) == (
        "insufficient_coverage",
        "",
        "4",
    )
    assert rows["chr21:369958-370298"]["status"] == "no_reference_cpgs"
    assert any(r["status"] == "contig_absent" for r in rows.values())

    manifest = json.loads(next(params.outdir.glob("*.manifest.json")).read_text())
    assert manifest["details"]["annotation"]["source"] == "UCSC masked cpgIslandExt"
    assert (
        manifest["details"]["annotation"]["sha256"]
        == "13273652c3450c3c639f37c02caa49867526fddee3138176164bf193167cbeda"
    )


@needs_samtools
def test_1293_rejects_wrong_assembly_and_uncombined_calls(island_params, tmp_path):
    # The shared chrM name and length must not hide a different nuclear build.
    result = methylation.regions(replace(island_params, build="hg38"), check=False)
    assert result.status == "failed"
    assert (
        result.error
        and "incompatible primary accession NC_060945.1" in result.error.message
    )
    bad = tmp_path / "stranded.bedmethyl"
    bad.write_text(
        island_params.input.read_text().replace("\t.\t", "\t+\t"), newline="\n"
    )
    result = methylation.regions(replace(island_params, input=bad), check=False)
    assert result.status == "failed"
    assert result.error and "strand-combined" in result.error.message
    assert not list(island_params.outdir.glob("*.tsv"))


@needs_samtools
def test_1293_absent_modification_is_not_reported_as_low_coverage(island_params):
    result = methylation.regions(replace(island_params, hydroxymethyl=True))
    with Path(result.outputs[0].path).open(encoding="utf-8", newline="") as f:
        rows = {
            row["mod_code"]: row
            for row in csv.DictReader(f, delimiter="\t")
            if row["region"] == "chr21:49616-50090"
        }
    assert rows["m"]["status"] == "assessed"
    assert rows["h"]["status"] == "modification_not_tabulated"
    assert not rows["h"]["pooled_percent"]


@needs_samtools
def test_1293_absent_ucsc_annotation_requires_explicit_download(tmp_path):
    fasta = tmp_path / "hg38.fa"
    fasta.write_text(">chrM\n" + "N" * 16_569 + "\n", newline="\n")
    subprocess.run(["samtools", "faidx", str(fasta)], check=True)
    save_config(
        {
            "reference_library": str(tmp_path / "library"),
            "allow_reference_downloads": True,
        }
    )
    params = methylation.MethylationRegionsParams(
        input=FIXTURES / "sites.bedmethyl",
        ref=fasta,
        build="hg38",
        preset="cpg-islands",
        outdir=tmp_path / "results",
    )
    result = methylation.regions(params, check=False)
    assert result.exit_code == 2
    assert result.error and "cpgIslandExt" in result.error.message
    assert "--download" in result.error.message
    assert not (tmp_path / "library").exists()


@needs_samtools
def test_1293_parquet_bedmethyl_and_results_keep_assessment_states(
    island_params, tmp_path
):
    pq = pytest.importorskip("pyarrow.parquet")
    from wgsextract_cli.commands.methylation import bedmethyl_to_parquet

    table = tmp_path / "sites.bedmethyl.parquet"
    bedmethyl_to_parquet(
        str(island_params.input), str(table), {"reference_build": "t2t"}
    )
    result = methylation.regions(
        replace(island_params, input=table, table_format="parquet")
    )
    rows = {r["region"]: r for r in pq.read_table(result.outputs[0].path).to_pylist()}
    assert rows["chr21:49616-50090"]["pooled_percent"] == 45
    assert rows["chr21:236555-236851"]["pooled_percent"] is None
