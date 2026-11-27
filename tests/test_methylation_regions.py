"""Region summaries use the right build, CpG anchor and coverage (#1293)."""

import csv
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from wgsextract_cli.api import methylation
from wgsextract_cli.cli import platform_support
from wgsextract_cli.core.config import save_config
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.methylation_annotations import island_regions, promoter_regions
from wgsextract_cli.core.results import CommandResult
from wgsextract_cli.main import main

FIXTURES = Path(__file__).parent / "fixtures" / "methylation_regions"
needs_samtools = pytest.mark.skipif(
    not shutil.which("samtools"), reason="needs samtools"
)
needs_modkit = pytest.mark.skipif(
    sys.platform == "win32" or not get_tool_path("modkit"),
    reason="modkit's Pixi environment is not installed or supported",
)


def _hp_regions_input(tmp_path, params, *, edge_ps=49001, with_hp=True, with_hp2=True):
    contig = "NC_060945.1"
    records = [
        "@HD\tVN:1.6\tSO:coordinate\n",
        f"@SQ\tSN:{contig}\tLN:45090682\n",
        "@RG\tID:ont\tSM:synthetic\tPL:ONT\n",
    ]

    def add(start, seq, *, hp, ps, count, prob, reverse=False, duplicate=False):
        bases = seq.count("G" if reverse else "C")
        for _ in range(count):
            tags = ["RG:Z:ont", "MM:Z:C+m," + ",".join(["0"] * bases) + ";"]
            tags.append("ML:B:C," + ",".join([str(prob)] * bases))
            if with_hp and hp:
                tags.append(f"HP:i:{hp}")
                if ps is not None:
                    tags.append(f"PS:i:{ps}")
            row = [
                f"read{len(records)}",
                str((16 if reverse else 0) | (1024 if duplicate else 0)),
                contig,
                str(start + 1),
                "60",
                f"{len(seq)}M",
                "*",
                "0",
                "0",
                seq,
                "I" * len(seq),
                *tags,
            ]
            records.append("\t".join(row) + "\n")

    add(49616, "CGAACGAACGAA", hp=1, ps=49001, count=5, prob=255)
    if with_hp2:
        add(49616, "CGAACGAACGAA", hp=2, ps=49001, count=5, prob=0)
    add(49616, "CGAACGAACGAA", hp=None, ps=None, count=2, prob=255)
    if with_hp:
        add(49616, "CG", hp=1, ps=123, count=1, prob=0, duplicate=True)
    add(50090, "G", hp=1, ps=edge_ps, count=5, prob=0, reverse=True)
    add(236555, "CG", hp=1, ps=236500, count=5, prob=255)
    if with_hp2:
        add(236555, "CG", hp=2, ps=236500, count=2, prob=0)
    sam, bam = tmp_path / "reads.sam", tmp_path / "reads.bam"
    sam.write_text("".join(records), newline="\n")
    subprocess.run(["samtools", "view", "-b", "-o", str(bam), str(sam)], check=True)
    subprocess.run(["samtools", "index", str(bam)], check=True)
    return replace(params, input=bam, partition_hp=True, min_cpgs=1, threads=1)


def _hp_tsv_rows(result: CommandResult) -> dict[tuple[str, str], dict[str, str]]:
    [table] = [
        o.path for o in result.outputs if o.role == "methylation_regions_haplotypes"
    ]
    with Path(table).open(encoding="utf-8", newline="") as handle:
        return {
            (row["region"], row["partition"]): row
            for row in csv.DictReader(handle, delimiter="\t")
        }


@needs_samtools
@needs_modkit
def test_1293_haplotypes_use_common_cpgs_and_keep_unphased_counts(
    tmp_path, island_params
):
    params = _hp_regions_input(tmp_path, island_params)
    # The input basename can match the region summarizer's scratch FASTA.
    ref = tmp_path / "reference-regions.fa"
    ref_size = params.ref.stat().st_size
    params.ref.rename(ref)
    params.ref.with_suffix(".fa.fai").rename(ref.with_suffix(".fa.fai"))
    blocks = tmp_path / "phased.blocks.tsv"
    blocks.write_text(
        "contig\tphase_set\tfirst\tlast\tspan_bp\tvariants\n"
        "chr21\t49001\t49001\t51000\t1999\t4\n"
        "chr21\t236500\t236500\t237000\t500\t4\n",
        newline="\n",
    )
    result = methylation.regions(replace(params, phase_blocks=blocks, ref=ref))
    rows = _hp_tsv_rows(result)
    region = "chr21:49616-50090"
    combined, hp1, hp2, unphased = (
        rows[region, group] for group in ("combined", "hp1", "hp2", "unphased")
    )
    assert combined["phase_status"] == "single_block"
    assert combined["phase_set"] == "49001"
    assert combined["shared_qualified_cpgs"] == "3"
    assert float(combined["hp1_minus_hp2_pp"]) == 100
    assert float(hp1["pooled_percent"]) == 75
    assert float(hp2["pooled_percent"]) == 0
    assert unphased["observed_valid_calls"] == "6"
    low = "chr21:236555-236851"
    assert rows[low, "hp2"]["status"] == "insufficient_coverage"
    assert rows[low, "hp2"]["pooled_percent"] == ""
    assert rows[low, "combined"]["hp1_minus_hp2_pp"] == ""
    absent = [row for row in rows.values() if row["status"] == "contig_absent"]
    assert absent and {row["phase_status"] for row in absent} == {"contig_absent"}
    assert result.details["haplotype_partition"]["phase_block_source"] == str(blocks)
    assert ref.stat().st_size == ref_size


@needs_samtools
@needs_modkit
@pytest.mark.skipif(not shutil.which("bgzip"), reason="needs bgzip")
def test_1293_cram_parquet_preserves_absent_haplotype_and_block_bounds(
    tmp_path, island_params
):
    pq = pytest.importorskip("pyarrow.parquet")
    params = _hp_regions_input(tmp_path, island_params, with_hp2=False)
    cram = tmp_path / "reads.cram"
    subprocess.run(
        [
            "samtools",
            "view",
            "-C",
            "-T",
            str(params.ref),
            "-o",
            str(cram),
            str(params.input),
        ],
        check=True,
    )
    subprocess.run(["samtools", "index", str(cram)], check=True)
    compressed_ref = tmp_path / "reference.bgzf"
    with compressed_ref.open("wb") as handle:
        subprocess.run(["bgzip", "-c", str(params.ref)], stdout=handle, check=True)
    subprocess.run(["samtools", "faidx", str(compressed_ref)], check=True)
    blocks = tmp_path / "phased.blocks.tsv"
    blocks.write_text(
        "contig\tphase_set\tfirst\tlast\tspan_bp\tvariants\n"
        "chr21\t49001\t49001\t49800\t799\t4\n"
        "chr21\t49801\t49801\t51000\t1199\t4\n",
        newline="\n",
    )
    result = methylation.regions(
        replace(
            params,
            input=cram,
            ref=compressed_ref,
            phase_blocks=blocks,
            table_format="parquet",
        )
    )
    [output] = result.outputs
    with pq.ParquetFile(output.path) as table:
        rows = {
            row["partition"]: row
            for row in table.read().to_pylist()
            if row["region"] == "chr21:49616-50090"
        }
        details = json.loads(table.schema_arrow.metadata[b"region_summary"])
    assert rows["combined"]["phase_status"] == "cross_block"
    assert rows["combined"]["hp1_minus_hp2_pp"] is None
    assert rows["hp1"]["pooled_percent"] == 75
    assert rows["hp2"]["status"] == "no_valid_calls"
    assert rows["hp2"]["pooled_percent"] is None
    assert details["haplotype_partition"]["phase_block_source"] == str(blocks)


@needs_samtools
@needs_modkit
@pytest.mark.parametrize(
    ("edge_ps", "status"), [(49991, "cross_block"), (None, "missing_ps")]
)
def test_1293_phase_ambiguity_withholds_delta(tmp_path, island_params, edge_ps, status):
    params = _hp_regions_input(tmp_path, island_params, edge_ps=edge_ps)
    rows = _hp_tsv_rows(methylation.regions(params))
    combined = rows["chr21:49616-50090", "combined"]
    assert combined["phase_status"] == status
    assert combined["shared_qualified_cpgs"] == "3"
    assert combined["hp1_minus_hp2_pp"] == ""


@needs_samtools
@needs_modkit
def test_1293_untagged_alignment_names_the_missing_hp_tags(tmp_path, island_params):
    params = _hp_regions_input(tmp_path, island_params, with_hp=False)
    result = methylation.regions(params, check=False)
    assert result.status == "failed"
    assert result.error and "HP:i:1 or HP:i:2" in result.error.message
    assert "vcf phase --haplotag" in result.error.message
    assert not list(params.outdir.glob("*.tsv"))


@needs_samtools
def test_1293_windows_only_guards_alignment_partitioning(
    tmp_path, island_params, monkeypatch
):
    from wgsextract_cli.commands import methylation as pileup

    params = _hp_regions_input(tmp_path, island_params)
    monkeypatch.setattr(pileup, "sys", SimpleNamespace(platform="win32"))
    result = methylation.regions(params, check=False)
    assert result.error and "modkit" in result.error.message
    assert "WSL2" in result.error.message


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


@pytest.fixture
def imprinted_params(tmp_path: Path) -> methylation.MethylationRegionsParams:
    fasta = tmp_path / "hg38-chr15.fa"
    start, end, length = 24_954_856, 24_956_829, 101_991_189
    # Keep the real GRCh38 contig length and DMR boundaries; bases are synthetic.
    sequence = bytearray(b"N" * 1_000_000)
    for pos in (start - 2, start, start + 4, start + 8, end):
        offset = pos % len(sequence)
        sequence[offset : offset + 2] = b"CG"
    with fasta.open("wb") as out:
        out.write(b">chr15\n")
        blank = (b"N" * 100 + b"\n") * 10_000
        for mb in range(length // len(sequence)):
            if mb == start // len(sequence):
                out.writelines(
                    sequence[i : i + 100] + b"\n" for i in range(0, len(sequence), 100)
                )
            else:
                out.write(blank)
        tail = length % len(sequence)
        out.write((b"N" * 100 + b"\n") * (tail // 100))
        out.write(b"N" * (tail % 100) + b"\n")
    subprocess.run(["samtools", "faidx", str(fasta)], check=True)
    bed = tmp_path / "calls.bedmethyl"
    bed.write_text(
        "".join(
            f"15\t{pos}\t{pos + 1}\tm\t10\t.\t{pos}\t{pos + 1}\t255,0,0"
            f"\t10\t{modified * 10}\t{modified}\t{10 - modified}\t0\t0\t0\t0\t0\n"
            for pos, modified in (
                (start - 2, 10),
                (start, 8),
                (start + 4, 0),
                (start + 8, 4),
                (end, 10),
            )
        ),
        newline="\n",
    )
    return methylation.MethylationRegionsParams(
        input=bed,
        ref=fasta,
        build="hg38",
        preset="imprinted-dmrs",
        outdir=tmp_path / "results",
    )


@needs_samtools
def test_1583_pooled_imprinted_dmrs_keep_source_boundaries_and_provenance(
    imprinted_params: methylation.MethylationRegionsParams,
) -> None:
    result = methylation.regions(imprinted_params)
    with Path(result.outputs[0].path).open(encoding="utf-8", newline="") as handle:
        rows = {row["region"]: row for row in csv.DictReader(handle, delimiter="\t")}
    assert len(rows) == 14
    assert "MEG3/DLK1:IG-DMR" not in rows
    assert rows["MEG3:TSS-DMR"]["status"] == "contig_absent"
    assert {name for name in rows if name.startswith("GNAS")} == {
        "GNAS-NESP:TSS-DMR",
        "GNAS-AS1:TSS-DMR",
        "GNAS-XL:Ex1-DMR",
        "GNAS A/B:TSS-DMR",
    }
    row = rows["SNURF:TSS-DMR"]
    assert (row["start"], row["end"]) == ("24954856", "24956829")
    assert (row["expected_cpgs"], row["qualified_cpgs"], row["valid_calls"]) == (
        "3",
        "3",
        "30",
    )
    assert float(row["pooled_percent"]) == 40
    assert result.details
    annotation = result.details["annotation"]
    assert isinstance(annotation, dict)
    assert annotation["doi"] == "10.1186/s13148-022-01358-9"
    assert "creativecommons.org/licenses/by/4.0" in annotation["license"]
    assert annotation["asset_sha256"]


@needs_samtools
@needs_modkit
def test_1583_haplotagged_imprinted_dmr_reports_delta_without_parent_labels(
    imprinted_params: methylation.MethylationRegionsParams,
    tmp_path: Path,
) -> None:
    sam, bam = tmp_path / "reads.sam", tmp_path / "reads.bam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr15\tLN:101991189\n"
        "@RG\tID:ont\tSM:synthetic\tPL:ONT\n"
        + "".join(
            f"read{hp}-{i}\t0\tchr15\t24954857\t60\t12M\t*\t0\t0\tCGAACGAACGAA"
            f"\tIIIIIIIIIIII\tRG:Z:ont\tMM:Z:C+m,0,0,0;\tML:B:C,{prob},{prob},{prob}"
            f"\tHP:i:{hp}\tPS:i:24954001\n"
            for hp, prob, depth in ((1, 255, 5), (2, 0, 7))
            for i in range(depth)
        ),
        newline="\n",
    )
    subprocess.run(["samtools", "view", "-b", "-o", str(bam), str(sam)], check=True)
    subprocess.run(["samtools", "index", str(bam)], check=True)
    result = methylation.regions(
        replace(imprinted_params, input=bam, partition_hp=True, threads=1)
    )
    rows = _hp_tsv_rows(result)
    snurf = {part: row for (name, part), row in rows.items() if name == "SNURF:TSS-DMR"}
    assert set(snurf) == {"combined", "hp1", "hp2", "unphased"}
    assert snurf["combined"]["phase_status"] == "single_ps"
    assert snurf["combined"]["shared_qualified_cpgs"] == "3"
    assert float(snurf["combined"]["pooled_percent"]) == pytest.approx(100 * 5 / 12)
    assert float(snurf["combined"]["hp1_minus_hp2_pp"]) == 100


def test_1583_cli_rejects_t2t_for_imprinted_dmrs_before_reading_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "methylation",
                "regions",
                "-i",
                str(tmp_path / "calls.bedmethyl"),
                "--ref",
                str(tmp_path / "hs1.fa"),
                "--build",
                "t2t",
                "--preset",
                "imprinted-dmrs",
                "-o",
                str(tmp_path / "results"),
            ]
        )
    assert error.value.code == 2
    assert "use --build hg38" in capsys.readouterr().err
    assert not list((tmp_path / "results").glob("*.methylation.tsv"))


@needs_samtools
def test_1293_windows_precomputed_regions_report_coverage_and_nulls(
    island_params,
    monkeypatch,
):
    # Only the CLI's early platform check is simulated, not subprocess/Path behavior.
    monkeypatch.setattr(platform_support, "sys", SimpleNamespace(platform="win32"))
    bed = island_params.outdir.parent / "sample.bam.bedmethyl"
    shutil.copyfile(island_params.input, bed)
    params = replace(island_params, input=bed)
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
    table = params.outdir / "sample.bam.t2t.cpg-islands.methylation.tsv"
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

    manifest = json.loads(
        next(params.outdir.glob("manifests/*.manifest.json")).read_text()
    )
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
