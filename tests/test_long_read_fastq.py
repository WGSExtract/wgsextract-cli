"""#1070: long-read FASTQs must never silently select short-read alignment."""

import gzip
import json
import random

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam, write_fasta, write_read_pairs
from wgsextract_cli.commands.alignment.reads import resolve_platform
from wgsextract_cli.core import sample_workspace, sequencing
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.main import main


def _fastq(path, names, lengths=(1000, 1500, 2000), quality="I", genome=None):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt") as handle:
        for i, length in enumerate(lengths):
            seq = (
                genome[100 + i * 2000 : 100 + i * 2000 + length]
                if genome
                else "A" * length
            )
            handle.write(f"@{names[i]}\n{seq}\n+\n{quality * length}\n")
    return path


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize(
    ("names", "quality", "expected", "method"),
    [
        ([f"renamed{i} runid=abc ch=12" for i in range(3)], "5", "ont", "header tags"),
        (
            [f"m84000_230101_120000_s1/{i}/ccs" for i in range(3)],
            "I",
            "pacbio",
            "read name",
        ),
        (
            [f"renamed{i} np=12 rq=0.999" for i in range(3)],
            "I",
            "pacbio",
            "header tags",
        ),
        ([f"SRR123.{i}" for i in range(3)], "]", "pacbio", "quality and length"),
    ],
)
def test_1070_long_fastq_platform_is_shared_and_recorded(
    tmp_path, compressed, names, quality, expected, method
):
    path = _fastq(
        tmp_path / ("reads.fq.gz" if compressed else "reads.fq"), names, quality=quality
    )
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert sequencing.input_platform(path) == expected
    assert resolve_platform(None, str(path)) == expected
    fact = workspace.detected("platform", path)
    assert fact is not None and method in fact.method
    assert fact.statistics["median_length"] == 1500
    assert fact.statistics["n50_length"] == 1500
    assert fact.statistics["mean_q"] == ord(quality) - 33
    assert resolve_platform("clr", str(path)) == "clr"


@pytest.mark.parametrize(
    ("tags", "quality"),
    [("", "5"), ("", "I"), ("np=1 rq=0.8", "5"), ("np=12 runid=abc", "I")],
)
def test_1070_ambiguous_long_reads_require_platform_but_override_wins(
    tmp_path, tags, quality
):
    path = _fastq(
        tmp_path / "ambiguous.fq", [f"r{i} {tags}" for i in range(3)], quality=quality
    )
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    with pytest.raises(
        UsageError, match="--platform ont.*--platform hifi.*--platform clr"
    ):
        resolve_platform(None, str(path))
    fact = workspace.detected("platform", path)
    assert fact.value == "Unknown" and fact.confidence == "low"
    assert fact.statistics["median_length"] == 1500
    result = CommandResult("align")
    with collecting(result):
        assert resolve_platform("ont", str(path)) == "ont"
    evidence = result.provenance.params["platform_detection"][str(path)]
    assert evidence["statistics"]["mean_q"] == ord(quality) - 33
    assert evidence["effective_platform"] == "ont"
    workspace.set_overrides({"platform": "ont"}, path)
    assert resolve_platform(None, str(path)) == "ont"
    workspace.set_overrides({"platform": None}, path)
    with pytest.raises(UsageError, match="Ambiguous long-read"):
        resolve_platform(None, str(path))


def test_1070_workspace_override_retains_detected_stats_in_manifest(tmp_path):
    path = _fastq(tmp_path / "reads.fq", [f"r{i} np=12 rq=0.999" for i in range(3)])
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({"platform": "ont"}, path)
    for _ in range(2):
        result = CommandResult("align")
        with collecting(result):
            assert resolve_platform(None, str(path)) == "ont"
        evidence = result.provenance.params["platform_detection"][str(path)]
        assert evidence["platform"] == "pacbio"
        assert evidence["effective_platform"] == "ont"
        assert evidence["statistics"]["median_length"] == 1500
        assert evidence["method"] == "FASTQ PacBio header tags"


@pytest.mark.parametrize("length", [150, 300])
def test_1070_renamed_short_paired_reads_keep_short_defaults(tmp_path, length):
    paths = []
    for mate in (1, 2):
        path = _fastq(
            tmp_path / f"reads_{mate}.fq.gz",
            [f"SRR123.{i}/{mate}" for i in range(3)],
            lengths=(length,) * 3,
        )
        assert sequencing.input_platform(path) is None
        assert resolve_platform(None, str(path)) == "illumina"
        paths.append(str(path))
    assert resolve_platform(None, paths[0], r2=paths[1]) == "illumina"


def test_1070_mixed_length_mates_cannot_hide_long_read_platform(tmp_path):
    first = _fastq(tmp_path / "r1.fq", ["r0/1", "r1/1", "r2/1"], lengths=(100,) * 3)
    second = _fastq(tmp_path / "r2.fq", [f"r{i}/2 runid=abc" for i in range(3)])
    with pytest.raises(UsageError, match="conflicting platform"):
        resolve_platform(None, str(first), r2=str(second))


def test_1070_platform_sample_cache_invalidates_for_replaced_fastq(tmp_path):
    path = _fastq(tmp_path / "reads.fq", [f"renamed{i} np=12" for i in range(3)])
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert resolve_platform(None, str(path)) == "pacbio"
    _fastq(path, [f"renamed{i} runid=abc" for i in range(3)])
    assert resolve_platform(None, str(path)) == "ont"


def test_1070_sampling_is_bounded_even_when_later_reads_differ(tmp_path):
    path = tmp_path / "reads.fq.gz"
    with gzip.open(path, "wt") as handle:
        for i in range(4200):
            tag = "runid=abc" if i < 4096 else "np=12"
            handle.write(f"@r{i} {tag}\n{'A' * 1000}\n+\n{'5' * 1000}\n")
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert resolve_platform(None, str(path)) == "ont"
    fact = workspace.detected("platform", path)
    assert fact.statistics["sampled_reads"] == 4096


@needs_tools("samtools", "bwa", "samblaster")
def test_1070_renamed_short_paired_gzip_alignment_keeps_bwa(tmp_path):
    genome = "".join(random.Random(1070).choices("ACGT", k=8000))
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": genome})
    first, second = tmp_path / "r1.fq", tmp_path / "r2.fq"
    write_read_pairs(genome, first, second, [100, 2100, 4100])
    for path in (first, second):
        with gzip.open(str(path) + ".gz", "wb") as handle:
            handle.write(path.read_bytes())
    out = tmp_path / "aligned"
    main(
        [
            "align",
            "--r1",
            str(first) + ".gz",
            "--r2",
            str(second) + ".gz",
            "--ref",
            str(ref),
            "--outdir",
            str(out),
            "--threads",
            "1",
            "--memory",
            "256M",
        ]
    )
    [aligned] = out.glob("*_aligned.bam")
    header = run_command(
        ["samtools", "view", "-H", aligned], capture_output=True
    ).stdout
    assert "\tPL:ILLUMINA" in header and "minimap2" not in header
    assert (
        run_command(
            ["samtools", "view", "-c", "-F", "0x904", aligned], capture_output=True
        ).stdout.strip()
        == "6"
    )


@needs_tools("samtools", "minimap2")
@pytest.mark.parametrize("realign", [False, True], ids=["align", "realign-fastq"])
@pytest.mark.parametrize(
    ("tags", "preset", "platform"),
    [
        ("runid=abc ch=12", "map-ont", "ONT"),
        ("np=12 rq=0.999", "map-hifi", "PACBIO"),
        ("native", "map-hifi", "PACBIO"),
    ],
)
def test_1070_long_fastq_maps_with_detected_preset_and_manifest(
    tmp_path, realign, tags, preset, platform
):
    genome = "".join(random.Random(1070).choices("ACGT", k=8000))
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": genome})
    reads = _fastq(
        tmp_path / "renamed.fq.gz",
        [
            f"m84000_230101_120000_s1/{i}/ccs" if tags == "native" else f"r{i} {tags}"
            for i in range(3)
        ],
        genome=genome,
    )
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    extra = []
    if realign:
        bam = sam_to_bam(
            [
                "@SQ\tSN:chr1\tLN:8000",
                f"short\t4\t*\t0\t0\t*\t*\t0\t0\t{genome[:100]}\t{'I' * 100}",
            ],
            tmp_path / "original.bam",
        )
        extra = ["--input", bam, "--no-preflight"]
    out = tmp_path / "aligned"
    main(
        [
            "realign" if realign else "align",
            *extra,
            "--r1",
            str(reads),
            "--ref",
            str(ref),
            "--aligner",
            "minimap2",
            "--outdir",
            str(out),
            "--threads",
            "1",
            "--memory",
            "256M",
        ]
    )
    [aligned] = out.glob("*_aligned.bam")
    header = run_command(
        ["samtools", "view", "-H", aligned], capture_output=True
    ).stdout
    # contract: ONT and HiFi require their matching minimap2 scoring preset.
    assert f"-ax {preset}" in header
    assert f"\tPL:{platform}" in header
    records = run_command(["samtools", "view", aligned], capture_output=True).stdout
    primary = [
        r.split("\t") for r in records.splitlines() if not int(r.split("\t")[1]) & 0x900
    ]
    assert sorted((r[2], r[3]) for r in primary) == [
        ("chr1", "101"),
        ("chr1", "2101"),
        ("chr1", "4101"),
    ]
    manifest = json.loads(next(out.glob("*.manifest.json")).read_text())
    detection = manifest["provenance"]["params"]["platform_detection"][str(reads)]
    assert detection["statistics"]["median_length"] == 1500
    assert detection["statistics"]["mean_q"] == 40
    assert ("read name" if tags == "native" else "header tags") in detection["method"]
