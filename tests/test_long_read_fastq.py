"""#1070: long-read FASTQs must never silently select short-read alignment."""

import gzip
import json
import os
import random
import subprocess
import sys

import pytest

from tests.htslib_helpers import needs_tools, sam_to_bam, write_fasta, write_read_pairs
from wgsextract_cli.commands.alignment.reads import resolve_platform
from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.alignment import sequencing as sequencing
from wgsextract_cli.core.alignment.fastq import validate_fastq
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
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
    if "read name" in method:
        assert fact.statistics is None
    else:
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


@pytest.mark.parametrize("length", [150, 1500])
def test_1070_wrapped_fastq_keeps_supported_platform_detection(tmp_path, length):
    path = tmp_path / "wrapped.fq"
    sequence = "\n".join(["A" * 50] * (length // 50))
    quality = "\n".join(["5" * 50] * (length // 50))
    path.write_text(f"@renamed runid=abc\n{sequence}\n+\n{quality}\n")
    assert validate_fastq(str(path)).read_length == length
    assert resolve_platform(None, str(path)) == ("ont" if length > 300 else "illumina")


@pytest.mark.parametrize(
    ("name", "compressed"),
    [
        ("reads.data", False),
        ("reads", False),
        ("reads.fq", True),
        ("reads.fq.gz", False),
    ],
)
def test_1070_fastq_content_and_gzip_magic_not_suffix_choose_platform(
    tmp_path, name, compressed
):
    path = _fastq(tmp_path / "original.fq", [f"r{i} runid=abc" for i in range(3)])
    data = path.read_bytes()
    reads = tmp_path / name
    reads.write_bytes(gzip.compress(data) if compressed else data)
    assert resolve_platform(None, str(reads)) == "ont"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX named pipes")
def test_1070_platform_probes_leave_both_named_pipe_inputs_untouched(tmp_path):
    pipe = tmp_path / "reads.fq"
    os.mkfifo(pipe)
    first = _fastq(tmp_path / "first.fq", ["r0", "r1", "r2"], lengths=(100,) * 3)
    script = (
        "import sys; from wgsextract_cli.commands.alignment.reads import resolve_platform;"
        "p, r = sys.argv[1:];"
        "assert resolve_platform('ont', p) == 'ont';"
        "assert resolve_platform(None, p) == 'illumina';"
        "assert resolve_platform(None, r, r2=p) == 'illumina'"
    )
    subprocess.run(
        [sys.executable, "-c", script, str(pipe), str(first)],
        check=True,
        timeout=10,
        capture_output=True,
    )


def test_1070_sample_prefix_edits_invalidate_workspace_platform_evidence(tmp_path):
    path = tmp_path / "large.fq"
    record = b"@renamed\n" + b"A" * 1000 + b"\n+\n" + b"5" * 1000 + b"\n"
    with path.open("wb") as stream:
        stream.write(record * 900)
        offset = stream.tell() + len(b"@renamed ")
        stream.write(
            b"@renamed runid=abc\n" + b"A" * 1000 + b"\n+\n" + b"5" * 1000 + b"\n"
        )
        stream.write(record * 21600)
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert resolve_platform(None, str(path)) == "ont"
    previous = workspace.detected("platform", path)
    for tag in (b"np=000012", b"np=000013"):
        with path.open("r+b") as stream:
            stream.seek(offset)
            stream.write(tag)
        assert resolve_platform(None, str(path)) == "pacbio"
        current = workspace.detected("platform", path)
        assert current is not None
        assert current.fingerprint == previous.fingerprint
        assert current.evidence_fingerprint != previous.evidence_fingerprint
        previous = current


@pytest.mark.parametrize("name", ["renamed", "E100000001L1C001R001000001"])
def test_1070_manifest_effective_platform_is_final_short_read_policy(tmp_path, name):
    path = _fastq(tmp_path / "reads.fq", [name] * 3, lengths=(150,) * 3)
    result = CommandResult("align")
    with collecting(result):
        assert resolve_platform(None, str(path)) == "illumina"
    evidence = result.provenance.params["platform_detection"][str(path)]
    assert evidence["effective_platform"] == "illumina"
    assert evidence["platform"] == (None if name == "renamed" else "dnbseq")


@pytest.mark.parametrize("data", [b"@bad\nAC\n+\nI\n", b"\x1f\x8b\x08broken"])
def test_1070_damaged_sample_defers_to_fastq_validation_without_raw_errors(
    tmp_path, data
):
    path = tmp_path / "damaged.fq"
    path.write_bytes(data)
    assert resolve_platform(None, str(path)) == "illumina"
    with pytest.raises(WGSExtractError):
        validate_fastq(str(path))


@pytest.mark.parametrize("length", [301, 400, 600, 999])
def test_1070_renamed_longer_short_reads_are_not_refused(tmp_path, length):
    path = _fastq(
        tmp_path / "reads.fq",
        ["SRR123.1", "SRR123.2", "SRR123.3"],
        lengths=(length,) * 3,
    )
    assert sequencing.input_platform(path) is None
    assert resolve_platform(None, str(path)) == "illumina"


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("bad_record", ["@empty\n\n+\n\n", "@bad\nAC\n+\nI\n"])
def test_1070_native_ont_name_survives_empty_or_malformed_sample_record(
    tmp_path, compressed, bad_record
):
    path = tmp_path / ("ont.fq.gz" if compressed else "ont.fq")
    opener = gzip.open if compressed else open
    uuid = "a18d071b-8bb9-42fe-b929-1f24cfe1d052"
    with opener(path, "wt") as stream:
        stream.write(f"@{uuid}\n{'A' * 1500}\n+\n{'I' * 1500}\n")
        stream.write(bad_record)
        stream.write(f"@renamed\n{'A' * 2000}\n+\n{'I' * 2000}\n")
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert sequencing.input_platform(path) == "ont"
    assert resolve_platform(None, str(path)) == "ont"
    fact = workspace.detected("platform", path)
    assert fact.method == "first read name pattern"
    assert fact.statistics is None


def test_1070_renamed_ont_sample_skips_zero_length_records(tmp_path):
    path = _fastq(tmp_path / "ont.fq", [f"r{i} runid=abc" for i in range(3)])
    with path.open("a") as stream:
        stream.write("@empty\n\n+\n\n")
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert resolve_platform(None, str(path)) == "ont"
    fact = workspace.detected("platform", path)
    assert fact.statistics["sampled_reads"] == 3
    assert fact.statistics["median_length"] == 1500


@pytest.mark.parametrize(
    ("quality", "expected"), [("~", "pacbio"), ("I" * 18 + "5" + "S", None)]
)
def test_1070_realistic_hifi_tail_and_capped_high_quality_ont_stay_distinct(
    tmp_path, quality, expected
):
    path = tmp_path / "renamed.fq"
    rng = random.Random(1070)
    lengths = [int(rng.lognormvariate(9.74, 0.55)) for _ in range(400)]
    with path.open("w") as stream:
        for number, length in enumerate(lengths):
            scores = (quality * (length // len(quality) + 1))[:length]
            stream.write(f"@SRR123.{number}\n{'A' * length}\n+\n{scores}\n")
    workspace = sample_workspace.SampleWorkspace(tmp_path)
    workspace.set_overrides({})
    assert sequencing.input_platform(path) == expected
    fact = workspace.detected("platform", path)
    assert fact.statistics["median_length"] > 10_000
    if expected == "pacbio":
        assert resolve_platform(None, str(path)) == "pacbio"
        assert fact.statistics["mean_q"] == 93
    else:
        assert fact.statistics["mean_q"] > 30
        assert fact.statistics["max_q"] == 50
        with pytest.raises(UsageError, match="Ambiguous long-read"):
            resolve_platform(None, str(path))


@pytest.mark.parametrize("length", [150, 300, 301, 600])
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


@needs_tools("samtools", "bwa")
def test_1070_renamed_301bp_fastq_aligns_without_platform_override(tmp_path):
    genome = "".join(random.Random(1070).choices("ACGT", k=8000))
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": genome})
    reads = _fastq(
        tmp_path / "miseq.fq.gz",
        ["SRR123.1", "SRR123.2", "SRR123.3"],
        lengths=(301,) * 3,
        genome=genome,
    )
    out = tmp_path / "aligned"
    main(
        [
            "align",
            "--r1",
            str(reads),
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
    assert (
        run_command(
            ["samtools", "view", "-c", "-F", "0x904", aligned], capture_output=True
        ).stdout.strip()
        == "3"
    )
    header = run_command(
        ["samtools", "view", "-H", aligned], capture_output=True
    ).stdout
    assert "\tPL:ILLUMINA" in header and "minimap2" not in header


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
    if tags == "native":
        assert detection["statistics"] is None
    else:
        assert detection["statistics"]["median_length"] == 1500
        assert detection["statistics"]["mean_q"] == 40
    assert ("read name" if tags == "native" else "header tags") in detection["method"]
