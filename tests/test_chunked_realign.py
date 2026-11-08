"""#431: realign long reads in chunks of the input, resuming an interrupted
run at its first unfinished chunk, with the records of a single pass."""

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
    # Small slices, so that a CRAM's index splits its contigs.
    cram = str(root / "x.cram")
    run_command(["samtools", "view", "-C", "-T", ref, "--output-fmt-option",
                 "seqs_per_slice=20", "-o", cram, bam])  # fmt: skip
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


def _dying_tools(tmp_path, die, after):
    """samtools and minimap2 shims logging each call that kill the run (its
    process group and their own, as an OOM kill or power cut would) on the
    ``after``-th call whose argv matches the regex ``die``."""
    shims = tmp_path / "shims"
    shims.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("samtools", "minimap2"):
        real = shutil.which(tool)
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
    ],
    ids=["mid-chunk", "mid-assembly"],
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
    if die == GROUP_SORT:
        assert not [c for c in calls if re.search(MAPPING, c)]
    bam = str(out / "x_aligned.bam")
    assert _records(bam, ref) == _records(str(tmp_path / "clean/x_aligned.bam"), ref)
    clean_bam = str(tmp_path / "clean/x_aligned.bam")
    assert _view("idxstats", bam) == _view("idxstats", clean_bam)
    assert not workdir.exists()


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
