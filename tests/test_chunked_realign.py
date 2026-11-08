"""#431: realign long reads in chunks of the input, resuming an interrupted
run at its first unfinished chunk, with the records of a single pass."""

import gzip
import os
import platform
import random
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, sam_to_bam, write_fasta
from wgsextract_cli.core import alignment_splice
from wgsextract_cli.core.utils import run_command

COMP = str.maketrans("ACGT", "TGCA")
MODS = "MM:Z:C+m?,0,1;\tML:B:C,250,10"
MAPPING = r"^minimap2 .*\.mmi -$"
GROUP_SORT = r"^samtools sort .*group-[0-9]+\.sort"
INDEXING = r"^(samtools|sambamba) index .*x_aligned\.bam$"
CONTIGS = {"chr1": 2_400_000, "chr2": 300_000, "chr3": 60_000}


def _revcomp(seq: str) -> str:
    return seq.translate(COMP)[::-1]


@pytest.fixture(scope="module")
def ont(tmp_path_factory):
    """A three-contig genome and an old, coordinate-sorted ONT alignment of
    it: reads on both strands with MM/ML tags, a chimeric read, a secondary
    and a supplementary record, unmapped reads, and pairs of identical reads
    stored at different places (their new records tie)."""
    rng = random.Random(431)
    root = tmp_path_factory.mktemp("ont")
    genome = {name: "".join(rng.choices("ACGT", k=n)) for name, n in CONTIGS.items()}
    ref = write_fasta(root / "ref.fa", genome)
    names = list(genome)
    records = []

    def read(contig, pos, seq, flag=0, cigar=None, name=None):
        name = name or str(uuid.UUID(int=rng.getrandbits(128)))
        stored = _revcomp(seq) if flag & 16 else seq
        cigar = cigar or f"{len(seq)}M"
        records.append(
            (contig, pos, f"{name}\t{flag}\t{contig}\t{pos}\t60\t{cigar}\t*\t0\t0"
             f"\t{stored}\t{'5' * len(seq)}\tRG:Z:r\t{MODS}")
        )  # fmt: skip
        return name

    for _ in range(240):
        contig = rng.choices(names, weights=list(CONTIGS.values()))[0]
        length = rng.randint(800, 3000)
        pos = rng.randint(1, CONTIGS[contig] - length)
        seq = genome[contig][pos - 1 : pos - 1 + length]
        flag = rng.choice([0, 16])
        read(contig, pos, _revcomp(seq) if flag else seq, flag)
    # The same chr2 sequence, also stored (misaligned) early on chr1.
    for pos in (1_000, 50_000):
        seq = genome["chr2"][150_000 + pos : 152_000 + pos]
        read("chr1", 1_200_000 + pos, seq)
        read("chr2", 150_001 + pos, seq)
    chimera = genome["chr1"][9_000:11_000] + genome["chr3"][20_000:22_000]
    name = read("chr1", 9_001, chimera, cigar="2000M2000S")
    records.append(
        ("chr3", 20_001, f"{name}\t2048\tchr3\t20001\t60\t2000H2000M\t*\t0\t0"
         f"\t{genome['chr3'][20_000:22_000]}\t{'5' * 2000}\tRG:Z:r")
    )  # fmt: skip
    records.append(
        ("chr2", 7_001, f"{name}\t256\tchr2\t7001\t0\t4000M\t*\t0\t0\t*\t*\tRG:Z:r")
    )
    records.sort(key=lambda r: (names.index(r[0]), r[1]))
    unmapped = [
        f"{uuid.UUID(int=rng.getrandbits(128))}\t4\t*\t0\t0\t*\t*\t0\t0\t{s}"
        f"\t{'5' * len(s)}\tRG:Z:r\t{MODS}"
        for s in ("".join(rng.choices("ACGT", k=1500)) for _ in range(6))
    ]
    header = [
        "@HD\tVN:1.6\tSO:coordinate",
        *(f"@SQ\tSN:{n}\tLN:{length}" for n, length in CONTIGS.items()),
        "@RG\tID:r\tPL:ONT\tSM:HG002",
    ]
    bam = sam_to_bam([*header, *(r[2] for r in records), *unmapped], root / "x.bam")
    run_command(["samtools", "index", bam])
    # Small slices, so that a CRAM's index splits its contigs, and slices
    # spanning two contigs, as for a whole genome's small contigs.
    cram = str(root / "x.cram")
    run_command(["samtools", "view", "-C", "-T", ref,
                 "--output-fmt-option", "seqs_per_slice=20",
                 "--output-fmt-option", "multi_seq_per_slice=1",
                 "-o", cram, bam])  # fmt: skip
    run_command(["samtools", "index", cram])
    return {"ref": str(ref), "bam": bam, "cram": cram}


def _chunk_size(source):
    """A chunk size splitting ``source`` into about eight chunks, some
    within a contig."""
    return str(os.path.getsize(source) // 8)


def _realign(source, ref, out, *extra):
    run_wgsextract(
        "realign", "--input", str(source), "--ref", str(ref), "--outdir", str(out),
        "--platform", "ont", "--threads", "2", "--memory", "256M",
        "--no-preflight", *extra,
    )  # fmt: skip


def _view(*argv):
    return run_command(["samtools", *argv], capture_output=True).stdout


def _records(path, ref):
    """The alignment's records, and its header without @PG lines (a chunked
    run's minimap2 @PG names its index, not the FASTA)."""
    header = [h for h in _view("view", "-H", path).splitlines() if h[:3] != "@PG"]
    return header, _view("view", "-T", ref, path)


@needs_tools("samtools", "minimap2")
@pytest.mark.parametrize("source", ["bam", "cram"])
@pytest.mark.parametrize("output", ["BAM", "CRAM"])
def test_chunked_realign_writes_the_records_of_a_single_pass(
    tmp_path, ont, source, output
):
    ref = ont["ref"]
    ext = output.lower()
    _realign(ont[source], ref, tmp_path / "whole", "--format", output,
             "--chunk-size", "0")  # fmt: skip
    _realign(ont[source], ref, tmp_path / "chunked", "--format", output,
             "--chunk-size", _chunk_size(ont[source]))  # fmt: skip

    whole = str(tmp_path / "whole" / f"x_aligned.{ext}")
    chunked = str(tmp_path / "chunked" / f"x_aligned.{ext}")
    header, records = _records(chunked, ref)
    assert (header, records) == _records(whole, ref)
    lines = records.splitlines()
    primary = [r for r in lines if not int(r.split("\t")[1]) & 0x900]
    # Each read once, the chimera's supplementary record, modification tags.
    assert len(primary) == 240 + 4 + 1 + 6
    assert any(int(r.split("\t")[1]) & 0x800 for r in lines)
    assert all("MM:Z:C+m?,0,1;" in r for r in primary)
    _view("quickcheck", chunked)
    index = chunked + (".crai" if output == "CRAM" else ".bai")
    assert os.path.isfile(index)
    assert _view("idxstats", chunked) == _view("idxstats", whole)
    assert not (tmp_path / "chunked" / f"x_aligned.{ext}.chunks").exists()


def _lines(ont, region):
    return _view("view", ont["bam"], region).splitlines(keepends=True)


@needs_tools("samtools", "minimap2")
@pytest.mark.parametrize("within_a_slice", [False, True], ids=["across", "within"])
def test_a_cram_out_of_order_despite_its_header_loses_no_reads(
    tmp_path, ont, within_a_slice
):
    """#431: a CRAM sliced by region with chrM before chrY still said
    SO:coordinate. Read by region, its chunks missed every chrY read; it is
    now realigned in one pass, every read included. #578 review: also when
    the disorder lies within one slice, whose index rows share its offsets."""
    ref = ont["ref"]
    sam = tmp_path / "unsorted.sam"
    header = _view("view", "-H", ont["bam"])
    chr1, chr2, chr3 = (_lines(ont, name) for name in ("chr1", "chr2", "chr3"))
    if within_a_slice:
        # One slice holds chr1's last reads, chr3's first, then chr2's only.
        records = chr1[:18] + chr3[:1] + chr2[:1] + chr3[1:]
    else:
        # A slice holds chr3's last reads and chr2's first: read by region,
        # htslib stops at chr3's and returns none of chr2's.
        records = chr1 + chr3 + chr2
    sam.write_text(header + "".join(records))
    cram = str(tmp_path / "unsorted.cram")
    run_command(["samtools", "view", "-C", "-T", ref,
                 "--output-fmt-option", "seqs_per_slice=20",
                 "--output-fmt-option", "multi_seq_per_slice=1",
                 "-o", cram, str(sam)])  # fmt: skip
    run_command(["samtools", "index", cram])
    _realign(cram, ref, tmp_path / "out", "--chunk-size", _chunk_size(cram))

    aligned = str(tmp_path / "out" / "unsorted_aligned.bam")
    source = int(_view("view", "-c", "-F", "0x900", "-T", ref, cram))
    assert int(_view("view", "-c", "-F", "0x900", aligned)) == source


@needs_tools("samtools", "minimap2")
def test_a_plain_text_crai_plans_chunks(tmp_path, ont):
    """#578 review: a .crai may be uncompressed; planning read it as gzip
    and the default chunked realign failed."""
    cram = tmp_path / "x.cram"
    shutil.copy(ont["cram"], cram)
    with gzip.open(ont["cram"] + ".crai", "rb") as handle:
        (tmp_path / "x.cram.crai").write_bytes(handle.read())
    size = _chunk_size(str(cram))
    _realign(cram, ont["ref"], tmp_path / "chunked", "--chunk-size", size)
    _realign(cram, ont["ref"], tmp_path / "whole", "--chunk-size", "0")
    assert _records(str(tmp_path / "chunked/x_aligned.bam"), ont["ref"]) == _records(
        str(tmp_path / "whole/x_aligned.bam"), ont["ref"]
    )


@needs_tools("samtools", "minimap2")
def test_the_sr_preset_still_marks_duplicates(tmp_path, ont):
    """#578 review: with --preset sr minimap2 output has its duplicates
    marked in one pass; the chunks didn't. sr now aligns in one pass."""
    for out, size in (("whole", "0"), ("chunked", _chunk_size(ont["bam"]))):
        _realign(ont["bam"], ont["ref"], tmp_path / out, "--preset", "sr",
                 "--chunk-size", size)  # fmt: skip
    chunked = str(tmp_path / "chunked/x_aligned.bam")
    assert _records(chunked, ont["ref"]) == _records(
        str(tmp_path / "whole/x_aligned.bam"), ont["ref"]
    )
    # The identical reads stored on chr1 and chr2: one of each pair.
    assert int(_view("view", "-c", "-f", "0x400", chunked)) >= 2


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX permissions")
@needs_tools("samtools", "minimap2")
def test_a_read_only_reference_without_fai_aligns_in_chunks(tmp_path, ont):
    """#578 review: chunked realign wrote a .fai beside --ref, failing for a
    reference in a read-only directory that one pass aligns to."""
    library = tmp_path / "library"
    library.mkdir()
    ref = library / "ref.fa"
    shutil.copy(ont["ref"], ref)
    library.chmod(0o555)
    try:
        _realign(ont["bam"], ref, tmp_path / "out",
                 "--chunk-size", _chunk_size(ont["bam"]))  # fmt: skip
    finally:
        library.chmod(0o755)
    assert sorted(p.name for p in library.iterdir()) == ["ref.fa"]
    _view("quickcheck", str(tmp_path / "out/x_aligned.bam"))


def _dying_tools(tmp_path, die, after):
    """samtools and minimap2 shims logging each call that kill the run (its
    process group and their own, as an OOM kill or power cut would) on the
    ``after``-th call whose argv matches the regex ``die``."""
    shims = tmp_path / "shims"
    shims.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("samtools", "minimap2", "sambamba"):
        real = shutil.which(tool)
        if real is None:
            continue
        script = shims / tool
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "{tool} $*" >> "{log}"\n'
            f"die={shlex.quote(die)}\n"
            f'if [ -f "{tmp_path}/die" ] && [[ "{tool} $*" =~ $die ]]; then\n'
            f'  n=$(( $(cat "{tmp_path}/count" 2>/dev/null || echo 0) + 1 ))\n'
            f'  echo $n > "{tmp_path}/count"\n'
            f"  if [ $n -ge {after} ]; then\n"
            f'    rm -f "{tmp_path}/die"\n'
            f'    "{real}" "$@" | head -c 4096\n'
            f'    kill -9 -- "-$(cat "{tmp_path}/run.pid")"\n'
            "    kill -9 0\n"
            "  fi\n"
            "fi\n"
            f'exec "{real}" "$@"\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return shims, log


def _realign_cli(tmp_path, shims, argv, die):
    (tmp_path / "count").unlink(missing_ok=True)
    if die:
        (tmp_path / "die").write_text("")
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"}
    with subprocess.Popen(
        [sys.executable, "-m", "wgsextract_cli.main", "realign", *argv],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as run:
        (tmp_path / "run.pid").write_text(str(run.pid))
        stdout, stderr = run.communicate()
    return subprocess.CompletedProcess(run.args, run.returncode, stdout, stderr)


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("samtools", "minimap2")
@pytest.mark.parametrize(
    "die, after",
    [
        # Killed aligning the third chunk: the first two are kept.
        (MAPPING, 3),
        # Killed sorting the second output group, after appending the first.
        (GROUP_SORT, 2),
        # Killed indexing the finished output, before the step is recorded.
        (INDEXING, 1),
    ],
    ids=["mid-chunk", "mid-assembly", "mid-index"],
)
def test_a_killed_chunked_realign_resumes_with_identical_output(
    tmp_path, ont, die, after
):
    """#431: a whole-genome ONT realign runs for days, and a run killed in
    its last day started over. Now it resumes at its first unfinished chunk
    or output group, and writes the same alignment."""
    ref = ont["ref"]
    shims, log = _dying_tools(tmp_path, die, after)

    def realign_to(out, die=False):
        log.write_text("")
        argv = ["--input", ont["cram"], "--ref", ref, "--outdir", str(out),
                "--platform", "ont", "--threads", "2", "--memory", "256M",
                "--chunk-size", _chunk_size(ont["cram"]), "--no-preflight"]  # fmt: skip
        run = _realign_cli(tmp_path, shims, argv, die)
        return run, log.read_text().splitlines()

    clean, clean_calls = realign_to(tmp_path / "clean")
    assert clean.returncode == 0, clean.stderr
    out = tmp_path / "out"
    killed, _ = realign_to(out, die=True)
    assert killed.returncode != 0
    workdir = out / "x_aligned.bam.chunks"
    assembly = workdir / "assembly.bam"
    if assembly.is_file():
        # As a run killed while appending a group would leave it.
        with open(assembly, "ab") as handle:
            handle.write(os.urandom(5000))

    resumed, calls = realign_to(out)
    assert resumed.returncode == 0, resumed.stderr
    assert "Running step align: an earlier run was interrupted" in resumed.stderr
    assert "Resuming the chunked alignment" in resumed.stderr
    # Only the unfinished work runs again; the aligner's index is kept.
    assert not [c for c in calls if " -d " in c and c.startswith("minimap2")]
    redone = [c for c in calls if re.search(die, c)]
    assert len(redone) == len([c for c in clean_calls if re.search(die, c)]) - (
        after - 1
    )
    if die != MAPPING:
        assert not [c for c in calls if re.search(MAPPING, c)]
    bam = str(out / "x_aligned.bam")
    assert _records(bam, ref) == _records(str(tmp_path / "clean/x_aligned.bam"), ref)
    clean_bam = str(tmp_path / "clean/x_aligned.bam")
    assert _view("idxstats", bam) == _view("idxstats", clean_bam)
    assert not workdir.exists()


def _kill_chunked_run(tmp_path, ont, out, die, after):
    """Run a chunked realign into ``out`` and kill it as ``_dying_tools``
    does; returns the arguments that rerun it."""
    shims, _ = _dying_tools(tmp_path, die, after)
    argv = ["--input", ont["cram"], "--ref", ont["ref"], "--outdir", str(out),
            "--platform", "ont", "--threads", "2", "--memory", "256M",
            "--chunk-size", _chunk_size(ont["cram"])]  # fmt: skip
    killed = _realign_cli(tmp_path, shims, [*argv, "--no-preflight"], True)
    assert killed.returncode != 0
    return argv


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("samtools", "minimap2")
def test_resuming_needs_only_the_disk_space_left_to_fill(tmp_path, ont, monkeypatch):
    """#578 review: the preflight asked a resumed run for the whole run's
    space, ignoring the chunks it had kept, so a run killed late on a nearly
    full disk could not resume."""
    from wgsextract_cli.commands import realign
    from wgsextract_cli.core.utils import WGSExtractError
    from wgsextract_cli.core.warnings import GB

    out = tmp_path / "out"
    argv = _kill_chunked_run(tmp_path, ont, out, GROUP_SORT, 2)
    kept = sum(f.stat().st_size for f in out.rglob("*.chunks/**/*") if f.is_file())
    assert kept
    asked = []
    with monkeypatch.context() as patch:
        patch.setattr(
            realign, "check_free_space", lambda _path, gb: asked.append(gb) or False
        )
        with pytest.raises(WGSExtractError, match="Insufficient free disk space"):
            run_wgsextract("realign", *argv[:5], str(tmp_path / "fresh"), *argv[6:])
    # Too little for a fresh run, enough for the rest of this one.
    free = asked[0] * GB - kept / 2
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: (10 * free, 0, free))
    with pytest.raises(WGSExtractError, match="Insufficient free disk space"):
        run_wgsextract("realign", *argv[:5], str(tmp_path / "fresh"), *argv[6:])

    run_wgsextract("realign", *argv)
    clean = tmp_path / "clean"
    _realign(ont["cram"], ont["ref"], clean, "--chunk-size", _chunk_size(ont["cram"]))
    assert _records(str(out / "x_aligned.bam"), ont["ref"]) == _records(
        str(clean / "x_aligned.bam"), ont["ref"]
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("samtools", "minimap2")
def test_a_one_pass_rerun_removes_an_interrupted_runs_chunks(tmp_path, ont):
    """#578 review: rerunning an interrupted chunked realign in one pass
    (--chunk-size 0) left the chunks' files, up to the output's size, in
    <output>.chunks for good."""
    out = tmp_path / "out"
    argv = _kill_chunked_run(tmp_path, ont, out, MAPPING, 3)
    assert (out / "x_aligned.bam.chunks").is_dir()

    run_wgsextract("realign", *argv, "--chunk-size", "0", "--no-preflight")
    assert not (out / "x_aligned.bam.chunks").exists()
    _view("quickcheck", str(out / "x_aligned.bam"))


@needs_tools("samtools")
@pytest.mark.parametrize("fmt", ["BAM", "CRAM", "CRAM 3.1"])
def test_spliced_pieces_are_one_valid_file(tmp_path, ont, fmt):
    """Pieces appended one at a time (core.alignment_splice) make the file
    samtools cat would, readable and indexable; a truncated piece is
    refused rather than appended."""
    ref = ont["ref"]
    ext = ".bam" if fmt == "BAM" else ".cram"
    options = ["-O", "BAM"] if fmt == "BAM" else ["-O", "CRAM", "-T", ref]
    if fmt == "CRAM 3.1":
        options += ["--output-fmt-option", "version=3.1"]
    pieces = []
    for number, region in enumerate(["chr1", "chr2", "chr3", "*"]):
        piece = str(tmp_path / f"piece{number}{ext}")
        run_command(["samtools", "view", "--no-PG", *options, "-o", piece,
                     ont["bam"], region])  # fmt: skip
        pieces.append(piece)
    target = str(tmp_path / f"spliced{ext}")
    size = 0
    for piece in pieces:
        size = alignment_splice.append(target, piece, size)
    # An append interrupted midway is cut back to its committed size.
    with open(target, "ab") as handle:
        handle.write(b"half a piece")
    alignment_splice.finish(target, size)

    _view("quickcheck", target)
    _view("index", target)
    concatenated = str(tmp_path / f"cat{ext}")
    _view("cat", "--no-PG", "-o", concatenated, *pieces)
    assert _view("view", "-T", ref, target) == _view("view", "-T", ref, concatenated)
    assert _view("view", "-T", ref, "-c", target, "chr2:150000-160000") == _view(
        "view", "-c", ont["bam"], "chr2:150000-160000"
    )
    truncated = str(tmp_path / f"truncated{ext}")
    with open(pieces[1], "rb") as handle:
        data = handle.read()
    with open(truncated, "wb") as handle:
        handle.write(data[:-10])
    with pytest.raises(alignment_splice.SpliceError, match="truncated"):
        alignment_splice.append(target, truncated, size)
