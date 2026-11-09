"""#431: realign long reads in chunks of the input, resuming an interrupted
run at its first unfinished chunk, with the records of a single pass."""

import contextlib
import gzip
import json
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
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, sam_to_bam, write_fasta
from tests.http_server import serving
from wgsextract_cli.core import alignment_splice
from wgsextract_cli.core.chunked_alignment import ChunkPlan, chunked_disk
from wgsextract_cli.core.resource_estimates import GIB
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command

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


def test_disk_for_an_input_naming_contigs_differently_counts_the_largest_group():
    """#578 review: realigning GRCh37 names (1, 2, ...) to hs38's (chr1, ...)
    matched no input contig to an output group, so the preflight left out the
    largest group sorted beside the output: about 24 GiB short of 200 GiB."""
    reference = [(f"chr{n}", 100_000_000) for n in range(1, 25)]
    sizes = {f"chr{n}": 4 * GIB for n in range(1, 25)}
    plan = ChunkPlan([], [], sizes)
    renamed = ChunkPlan([], [], {name[3:]: size for name, size in sizes.items()})
    output, spill = 200 * GIB, 300 * GIB

    same = chunked_disk(output, spill, GIB, plan, reference)
    assert chunked_disk(output, spill, GIB, renamed, reference) >= same
    assert same[0] > output * (1 + 1 / 25) and same[1] > spill / 25


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


def _realign_cli(tmp_path, shims, argv, die, cwd=None):
    (tmp_path / "count").unlink(missing_ok=True)
    if die:
        (tmp_path / "die").write_text("")
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"}
    with subprocess.Popen(
        [sys.executable, "-m", "wgsextract_cli.main", "realign", *argv],
        env=env,
        cwd=cwd,
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


def _samtools_reads_urls():
    path = shutil.which("samtools")
    if path is None:
        return False
    done = subprocess.run([path, "--version"], capture_output=True, text=True,
                          errors="replace", check=False)  # fmt: skip
    return "libcurl=yes" in done.stdout


# Windows' samtools is built without libcurl: realign says so (check_url).
needs_url_samtools = pytest.mark.skipif(
    not _samtools_reads_urls(), reason="samtools without http(s) support"
)


@contextlib.contextmanager
def _serving(ont, source, *, etags=True):
    """A server of ``ont[source]`` and its index, with ETags as S3 sends."""
    local = Path(ont[source])
    index = Path(str(local) + (".crai" if source == "cram" else ".bai"))
    files = {local.name: local.read_bytes(), index.name: index.read_bytes()}
    with serving(None, files) as server:
        server.etags = etags
        yield server, local, server.url_for(local.name)


@pytest.mark.skipif(platform.system() == "Windows", reason="bash PATH shims")
@needs_tools("samtools", "minimap2")
@needs_url_samtools
@pytest.mark.parametrize("source", ["bam", "cram"])
def test_a_url_realigns_in_chunks_like_the_local_file(tmp_path, ont, source):
    """#431: the GIAB HG002 ONT CRAM is 104 GB, too big to keep beside its
    realignment on the Mac that runs it. realign now reads it over https by
    range, and resumes a run killed mid-chunk. Without --outdir the output
    goes to the current folder; only the input's index is downloaded there,
    and removed after."""
    work = tmp_path / "work"
    work.mkdir()
    shims, _ = _dying_tools(tmp_path, MAPPING, 3)
    with _serving(ont, source) as (server, local, url):
        size = _chunk_size(str(local))
        argv = ["--input", url, "--ref", ont["ref"], "--threads", "2",
                "--memory", "256M", "--chunk-size", size, "--no-preflight"]  # fmt: skip
        killed = _realign_cli(tmp_path, shims, argv, True, cwd=work)
        resumed = _realign_cli(tmp_path, shims, argv, False, cwd=work)
    assert killed.returncode != 0
    assert resumed.returncode == 0, resumed.stderr
    assert "Detected ont reads" in resumed.stderr
    assert "Resuming the chunked alignment" in resumed.stderr

    _realign(local, ont["ref"], tmp_path / "local", "--chunk-size", size)
    assert _records(str(work / "x_aligned.bam"), ont["ref"]) == _records(
        str(tmp_path / "local" / "x_aligned.bam"), ont["ref"]
    )
    manifest = json.loads((work / "x_aligned.bam.manifest.json").read_text())
    assert [i["path"] for i in manifest["inputs"] if i["role"] == "input"] == [url]
    assert sorted(p.name for p in work.iterdir() if not p.name.startswith(".x_")) == [
        "x_aligned.bam", "x_aligned.bam.bai", "x_aligned.bam.manifest.json",
        "x_aligned.bam.steps.json",
    ]  # fmt: skip


@needs_tools("samtools", "minimap2")
@needs_url_samtools
def test_a_changed_url_is_realigned_and_an_unchanged_one_is_not(tmp_path, ont):
    """#431: the step journal fingerprints a URL by its HEAD (ETag, else
    Last-Modified): a rerun skips an unchanged remote input and redoes a
    changed one."""
    out = tmp_path / "out"
    with _serving(ont, "cram") as (server, local, url):
        _realign(url, ont["ref"], out, "--chunk-size", _chunk_size(str(local)))
        journal = out / "x_aligned.bam.steps.json"
        first = json.loads(journal.read_text())["steps"]["align"]["run_id"]
        _realign(url, ont["ref"], out, "--chunk-size", _chunk_size(str(local)))
        assert json.loads(journal.read_text())["steps"]["align"]["run_id"] == first

        fewer = tmp_path / "fewer.cram"
        run_command(["samtools", "view", "-C", "-T", ont["ref"], "-o", fewer,
                     str(local), "chr2"])  # fmt: skip
        run_command(["samtools", "index", fewer])
        server.files.update(
            {local.name: fewer.read_bytes(),
             local.name + ".crai": Path(f"{fewer}.crai").read_bytes()}
        )  # fmt: skip
        _realign(url, ont["ref"], out, "--chunk-size", "0")
    assert json.loads(journal.read_text())["steps"]["align"]["run_id"] != first
    chroms = {
        r.split("\t")[2] for r in _view("view", str(out / "x_aligned.bam")).splitlines()
    }
    assert chroms <= {"chr2", "*"}


@needs_tools("samtools", "minimap2")
@needs_url_samtools
def test_a_url_without_etag_or_date_is_never_assumed_unchanged(tmp_path, ont):
    """#580 review: without a validator a URL was known by its size alone, so
    a file replaced by one of the same length was skipped as done."""
    out = tmp_path / "out"
    with _serving(ont, "cram", etags=False) as (_, local, url):
        _realign(url, ont["ref"], out, "--chunk-size", "0")
        journal = out / "x_aligned.bam.steps.json"
        first = json.loads(journal.read_text())["steps"]["align"]["run_id"]
        _realign(url, ont["ref"], out, "--chunk-size", "0")
    assert json.loads(journal.read_text())["steps"]["align"]["run_id"] != first


@needs_tools("samtools", "minimap2")
@needs_url_samtools
@pytest.mark.parametrize("status", [404, 500])
def test_only_a_missing_index_makes_a_url_align_in_one_pass(tmp_path, ont, status):
    """#580 review: any failure fetching the index (a timeout, a 5xx) fell
    back to one pass, losing the chunks' resume for a whole genome. Only a
    missing index (404, or S3's 403) does; other failures stop the run."""
    out = tmp_path / "out"
    with _serving(ont, "cram") as (server, local, url):
        server.statuses[local.name + ".crai"] = status
        size = _chunk_size(str(local))
        if status == 500:
            with pytest.raises(WGSExtractError, match="Can't download"):
                _realign(url, ont["ref"], out, "--chunk-size", size)
            return
        _realign(url, ont["ref"], out, "--chunk-size", size)
    reads = int(_view("view", "-c", "-F", "0x900", str(out / "x_aligned.bam")))
    assert reads == int(_view("view", "-c", "-F", "0x900", ont["bam"]))
    assert not (out / "x_aligned.bam.chunks").exists()


@needs_tools("samtools")
@pytest.mark.parametrize(
    "suffix", ["?X-Amz-Signature=abc", "#part"], ids=["query", "fragment"]
)
def test_a_url_with_a_query_or_credentials_is_refused(tmp_path, ont, suffix):
    """#580 review: a signed URL's token would reach the manifest and
    journal, and its query hid the file's format; only public URLs are
    read."""
    with _serving(ont, "cram") as (_, _local, url):
        for address in (url + suffix, url.replace("://", "://user:pw@")):
            with pytest.raises(UsageError, match="public http"):
                run_wgsextract("realign", "--input", address, "--ref", ont["ref"],
                               "--outdir", str(tmp_path / "out"))  # fmt: skip
    assert not (tmp_path / "out").exists()


@needs_tools("samtools")
@needs_url_samtools
def test_a_url_of_short_reads_is_refused(tmp_path, ont):
    """#431: only long reads stream from a URL; short-read realign extracts
    and collates its input, which needs a local file."""
    with (
        _serving(ont, "bam") as (_, _local, url),
        pytest.raises(UsageError, match="ONT and PacBio"),
    ):
        run_wgsextract(
            "realign", "--input", url, "--ref", ont["ref"],
            "--outdir", str(tmp_path), "--platform", "illumina",
        )  # fmt: skip
    assert list(tmp_path.iterdir()) == []


SECRET = "SECRETSIG"


def _secret_urls(url):
    return [f"{url}?X-Amz-Signature={SECRET}",
            url.replace("://", f"://user:{SECRET}@")]  # fmt: skip


def _files_mentioning(directory, text):
    return [
        str(path)
        for path in Path(directory).rglob("*")
        if path.is_file() and text.encode() in path.read_bytes()
    ]


@needs_tools("samtools")
def test_a_signed_url_leaks_its_token_nowhere(tmp_path, ont):
    """#580 review: the CLI recorded a signed URL as the run's input before
    refusing it, so its token reached --json, the result file and the last
    run's state; a URL with credentials printed them in "Can't reach". The
    URL is now refused before anything records it."""
    from wgsextract_cli import api
    from wgsextract_cli.api._runtime import CommandFailed
    from wgsextract_cli.core.last_run import state_directory

    with _serving(ont, "cram") as (_, _local, url):
        for address in _secret_urls(url):
            result_file = tmp_path / "result.json"
            done = subprocess.run(
                [sys.executable, "-m", "wgsextract_cli.main", "--json",
                 "--result-file", str(result_file), "realign", "--input", address,
                 "--ref", ont["ref"], "--outdir", str(tmp_path / "out")],
                capture_output=True, text=True, errors="replace", cwd=tmp_path,
                check=False,
            )  # fmt: skip
            assert done.returncode != 0
            assert "public http" in done.stdout + done.stderr
            assert SECRET not in done.stdout + done.stderr
            assert result_file.is_file()

            params = api.RealignParams(input=address, ref=Path(ont["ref"]),
                                       outdir=tmp_path / "out")  # fmt: skip
            with pytest.raises(CommandFailed, match="public http") as failed:
                api.align.realign(params)
            assert SECRET not in failed.value.result.to_json()
    assert _files_mentioning(tmp_path, SECRET) == []
    assert _files_mentioning(state_directory(), SECRET) == []
