import gzip as gzip
import json as json
import logging as logging
import os as os
import platform as platform
import random as random
import re as re
import shutil as shutil
import sys as sys
from pathlib import Path as Path
from typing import NamedTuple as NamedTuple

import pytest as pytest

from tests.disk_volumes import GIB as GIB
from tests.disk_volumes import Volumes as Volumes
from tests.fake_tools import FakeParabricks as FakeParabricks
from tests.htslib_helpers import needs_tools as needs_tools
from tests.htslib_helpers import sam_to_bam as sam_to_bam
from tests.htslib_helpers import write_fasta as write_fasta
from tests.prepared_data import sparse_truncate as sparse_truncate
from wgsextract_cli.api.align import RealignParams as RealignParams
from wgsextract_cli.api.align import realign as realign_api
from wgsextract_cli.commands import realign as realign
from wgsextract_cli.commands.realign import RealignJob as RealignJob
from wgsextract_cli.core import parabricks as parabricks
from wgsextract_cli.core.alignment.cram_staging import cram_version as cram_version
from wgsextract_cli.core.alignment.memory import MappingMemory as MappingMemory
from wgsextract_cli.core.config import settings as settings
from wgsextract_cli.core.read_counts import AlignmentSample as AlignmentSample
from wgsextract_cli.core.read_counts import UnpairedSample as UnpairedSample
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError
from wgsextract_cli.core.utils import run_command as run_command

FRAG = 300


def _args(source, **overrides):
    values = {
        "input": source,
        "threads": 2,
        "memory": "100M",
        "no_preflight": True,
        "platform": "illumina",
    }
    values.update(overrides)
    return RealignParams(**values)


def _view(*args):
    return run_command(["samtools", *args], capture_output=True).stdout


def _old_alignment(tmp_path, genome, pairs=150):
    """A coordinate-sorted, indexed BAM of FR pairs on the genome under its old
    contig name ``old1``, with pair p0 duplicated as ``dup``."""
    rng = random.Random(11)
    starts = rng.sample(range(len(genome) - FRAG), pairs)
    names = [f"p{i}" for i in range(pairs)] + ["dup"]
    starts.append(starts[0])
    qual = "I" * 100
    lines = [
        "@HD\tVN:1.6\tSO:unsorted",
        f"@SQ\tSN:old1\tLN:{len(genome)}",
        "@RG\tID:old\tSM:OldName",
    ]
    for name, start in zip(names, starts, strict=True):
        mate = start + FRAG - 100
        lines += [
            f"{name}\t99\told1\t{start + 1}\t60\t100M\t=\t{mate + 1}\t{FRAG}\t"
            f"{genome[start : start + 100]}\t{qual}\tRG:Z:old",
            f"{name}\t147\told1\t{mate + 1}\t60\t100M\t=\t{start + 1}\t{-FRAG}\t"
            f"{genome[mate : mate + 100]}\t{qual}\tRG:Z:old",
        ]
    return sam_to_bam(lines, tmp_path / "old" / "x.bam"), pairs + 1


@pytest.fixture
def genome_and_ref(tmp_path):
    rng = random.Random(5)
    genome = "".join(rng.choice("ACGT") for _ in range(6_000))
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    ref = write_fasta(tmp_path / "new" / "ref.fa", {"chr1": genome})
    return genome, str(ref)


@needs_tools("bwa", "samtools")
def test_realign_bam_to_new_reference(tmp_path, genome_and_ref):
    genome, ref = genome_and_ref
    source, pairs = _old_alignment(tmp_path, genome)
    outdir = tmp_path / "out"

    realign.run(
        _args(source, ref=ref, outdir=str(outdir), sample="Ann", no_preflight=False)
    )

    out = outdir / "x_aligned.bam"
    assert (outdir / "x_aligned.bam.bai").is_file()
    header = _view("view", "-H", str(out))
    assert "SO:coordinate" in header
    assert f"@SQ\tSN:chr1\tLN:{len(genome)}" in header and "old1" not in header
    assert "\tSM:Ann\t" in header and "OldName" not in header
    reads = [r.split("\t") for r in _view("view", str(out)).splitlines()]
    assert len(reads) == 2 * pairs
    assert {r[2] for r in reads} == {"chr1"}
    positions = [int(r[3]) for r in reads]
    assert positions == sorted(positions)
    # p0 and dup are the same fragment: one of the two pairs is flagged.
    dups = [r[0] for r in reads if int(r[1]) & 0x400]
    assert len(dups) == 2 and len(set(dups)) == 1 and dups[0] in {"p0", "dup"}
    # The extracted FASTQs are removed once the alignment is done.
    assert sorted(p.name for p in outdir.iterdir() if p.name[0] != ".") == [
        "x_aligned.bam",
        "x_aligned.bam.bai",
        "x_aligned.bam.steps.json",
    ]


@needs_tools("bwa", "samtools")
def test_691_switching_profile_rewrites_cached_realign_output(tmp_path, genome_and_ref):
    genome, ref = genome_and_ref
    source, pairs = _old_alignment(tmp_path, genome)
    outdir = tmp_path / "out"
    output = outdir / "x_aligned.cram"
    for profile, version in (("archive", (3, 1)), ("calling", (3, 0))):
        result = realign_api(
            _args(
                source,
                ref=ref,
                outdir=outdir,
                output_profile=profile,
                native_aligners="off",
                aligner="bwa",
            )
        )
        assert result.metrics["output_profile"] == profile
        assert cram_version(str(output)) == version
        assert int(_view("view", "-T", ref, "-c", str(output))) == 2 * pairs
    mtime = output.stat().st_mtime_ns
    result = realign_api(
        _args(
            source,
            ref=ref,
            outdir=outdir,
            output_profile="calling",
            native_aligners="off",
            aligner="bwa",
        )
    )
    assert result.metrics["output_profile"] == "calling"
    assert output.stat().st_mtime_ns == mtime


@needs_tools("bwa", "samtools")
def test_realign_cram_decoded_with_library_reference_and_fastqs_kept(
    tmp_path, genome_and_ref
):
    genome, ref = genome_and_ref
    bam, pairs = _old_alignment(tmp_path, genome)
    # The CRAM's own reference is gone from where it was encoded: decoding
    # needs the library's copy, found by its sequence dictionary.
    encoded_with = write_fasta(tmp_path / "old" / "old.fa", {"old1": genome})
    cram = tmp_path / "old" / "x.cram"
    run_command(
        ["samtools", "view", "-C", "-T", str(encoded_with), "-o", str(cram), bam]
    )
    run_command(["samtools", "index", str(cram)])
    for path in tmp_path.joinpath("old").glob("old.fa*"):
        path.unlink()
    Path(bam).unlink()
    library = tmp_path / "lib"
    (library / "genomes").mkdir(parents=True)
    write_fasta(library / "genomes" / "old.fa", {"old1": genome})
    settings["reference_library"] = str(library)

    realign.run(_args(cram, ref=ref, keep_fastq=True))

    out = tmp_path / "old" / "x_aligned.bam"
    assert len(_view("view", str(out)).splitlines()) == 2 * pairs
    for mate in ("R1", "R2"):
        with gzip.open(tmp_path / "old" / f"x_{mate}.fastq.gz", "rt") as f:
            assert sum(1 for _ in f) == 4 * pairs


def test_realign_requires_existing_input(tmp_path):
    with pytest.raises(WGSExtractError, match="--input <BAM|CRAM> is required"):
        _args(None, input=None)
    with pytest.raises(WGSExtractError, match="--input"):
        realign.run(_args(tmp_path / "missing.bam"))


def test_cram_input_ref_must_exist(tmp_path):
    with pytest.raises(WGSExtractError, match="--input-ref"):
        realign._input_cram_reference(
            RealignJob(input="in.cram", input_ref=str(tmp_path / "gone.fa"))
        )
    (tmp_path / "old.fa").write_text(">c\nA\n")
    opt = realign._input_cram_reference(
        RealignJob(input="in.CRAM", input_ref=str(tmp_path / "old.fa"))
    )
    assert opt == ["-T", str(tmp_path / "old.fa")]


def test_existing_fastqs_need_both_mates_and_prefer_the_input_dir(tmp_path):
    bam_dir, outdir = tmp_path / "bam", tmp_path / "out"
    bam_dir.mkdir()
    outdir.mkdir()
    source = bam_dir / "x.bam"
    source.write_bytes(b"")
    (bam_dir / "x_R1.fastq.gz").write_bytes(b"")
    assert realign._find_existing_fastqs(str(source), str(outdir)) is None

    for name in ("x_1.fq.gz", "x_2.fq.gz"):
        (outdir / name).write_bytes(b"")
    assert realign._find_existing_fastqs(str(source), str(outdir)) == (
        str(outdir / "x_1.fq.gz"),
        str(outdir / "x_2.fq.gz"),
    )

    for name in ("x.r1.fq.gz", "x.r2.fq.gz"):
        (bam_dir / name).write_bytes(b"")
    assert realign._find_existing_fastqs(str(source), str(outdir)) == (
        str(bam_dir / "x.r1.fq.gz"),
        str(bam_dir / "x.r2.fq.gz"),
    )


def _write_alignment(args):
    """What a finished alignment leaves: the BAM and its index."""
    outdir = args.outdir or str(Path(args.input).parent)
    out = realign.align_cmd.aligned_output_path(outdir, args.r1, args.r2, args.format)
    Path(out).write_text("aligned")
    Path(out + ".bai").write_text("index")


def _visible(directory):
    """A directory's files, less the step journal and hidden lock files."""
    return [
        p
        for p in Path(directory).iterdir()
        if not p.name.startswith(".") and not p.name.endswith(".steps.json")
    ]


def _published(staged):
    path = Path(staged)
    match = re.fullmatch(r"\.(.+)\.[0-9a-f]{32}\.partial(.*)", path.name)
    return str(path.with_name(match[1] + match[2])) if match else staged


class _Fakes:
    """Stand-ins for extraction and alignment that record what realign asked."""

    def __init__(self, monkeypatch):
        self.extracted = []
        self.aligned = []
        self.streamed = []
        self.align_error = None
        monkeypatch.setattr(realign, "verify_dependencies", lambda _tools: None)
        monkeypatch.setattr(realign, "log_dependency_info", lambda _tools: None)
        monkeypatch.setattr(realign, "alignments_to_fastq", self.extract)
        monkeypatch.setattr(realign.align_cmd, "run", self.align)
        monkeypatch.setattr(realign.align_cmd, "align_bwa", self.stream)
        monkeypatch.setattr(
            realign, "alignment_sample", lambda *_a, **_k: AlignmentSample(100, 400)
        )
        monkeypatch.setattr(
            realign, "unpaired_sample", lambda *_a, **_k: UnpairedSample(2, 0, None)
        )
        monkeypatch.setattr(realign.align_cmd, "available_memory", lambda: 1024**4)
        monkeypatch.setattr(realign, "available_memory", lambda: 1024**4)

    def extract(self, _input, r1, r2, **_kwargs):
        # Extraction writes staged copies; record the FASTQs they become.
        self.extracted.append(tuple(map(_published, (r1, r2))))
        for path in (r1, r2):
            Path(path).write_text("@r\nA\n+\nI\n")

    def align(self, args):
        self.aligned.append((args.r1, args.r2))
        if self.align_error:
            raise self.align_error
        _write_alignment(args)

    def stream(self, args, **kwargs):
        self.streamed.append(kwargs)
        self.align(args)


_DISK = re.compile(r"Host requirement: this realign run needs (<0\.1|~[\d.]+) GiB disk")


def _disk_needs(caplog) -> list[float]:
    """The GiB of disk each host requirement line asks for (0 for <0.1)."""
    found = [_DISK.search(r.getMessage()) for r in caplog.records]
    return [0.0 if m[1] == "<0.1" else float(m[1][1:]) for m in found if m]


def _preflight_gib(caplog, kind: str) -> float:
    """The first estimate of a "Preflight memory"/"Preflight disk" report line."""
    line = next(
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith(f"Preflight {kind}")
    )
    return float(line.split()[3])


class _DiskUsage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.fixture
def roomy_disk(monkeypatch):
    """A disk with room for any estimate, so the pre-flight check passes."""
    roomy = _DiskUsage(2**60, 0, 2**60)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: roomy)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "x.bam"
    path.write_bytes(b"data")
    return path


# --- resuming after a crash ---------------------------------------------------


def _dying_tools(tmp_path, log):
    """PATH shims for the aligner, samtools and sambamba that log each call's
    subcommand and, when it matches the pattern in ``tmp_path/die``, SIGKILL
    the wgsextract run's process group and their own mid-call, as an OOM kill
    or power cut would (or send it ``tmp_path/signal``, such as INT for a
    Ctrl-C)."""
    import shutil
    import stat

    shims = tmp_path / "shims"
    shims.mkdir()
    for tool in ("bwa", "samtools", "sambamba"):
        real = shutil.which(tool)
        if real is None:
            continue
        script = shims / tool
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "{tool} $1" >> "{log}"\n'
            f'die="$(cat "{tmp_path}/die" 2>/dev/null)"\n'
            f'if [ -n "$die" ] && [[ "{tool} $1" =~ ^($die)$ ]]; then\n'
            f'  rm -f "{tmp_path}/die"\n'
            # Half an output stream, then the crash. A tool writing -o FILE
            # (sort) is left as a crash while writing would leave it: half
            # the file and no index yet.
            '  out=""; prev=""\n'
            '  for a in "$@"; do [ "$prev" = -o ] && out="$a"; prev="$a"; done\n'
            '  if [ -n "$out" ]; then\n'
            f'    "{real}" "$@"\n'
            '    file="${out%%##idx##*}"\n'
            '    [[ "$out" == *"##idx##"* ]] && rm -f "${out#*##idx##}"\n'
            '    head -c $(( $(wc -c < "$file") / 2 )) "$file" > "$file.cut"\n'
            '    mv "$file.cut" "$file"\n'
            "  else\n"
            f'    "{real}" "$@" | head -c 4096\n'
            "  fi\n"
            f'  sig="$(cat "{tmp_path}/signal" 2>/dev/null || echo 9)"\n'
            f'  kill -"$sig" -- "-$(cat "{tmp_path}/run.pid")"\n'
            "  kill -9 0\n"
            "fi\n"
            f'exec "{real}" "$@"\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return shims


def _realign_cli(
    argv, shims, die=None, signal="9", available_memory=None, effective_uid=None
):
    import os
    import subprocess
    import sys

    if die:
        (shims.parent / "die").write_text(die + "\n")
        (shims.parent / "signal").write_text(signal + "\n")
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"}
    command = [sys.executable, "-m", "wgsextract_cli.main"]
    if available_memory is not None or effective_uid is not None:
        # OS boundary: force file extraction, as on a 16 GB Mac with apps open.
        setup = "import runpy; "
        if available_memory is not None:
            setup += (
                "import psutil; memory = psutil.virtual_memory(); "
                f"psutil.virtual_memory = lambda: memory._replace(available={available_memory}); "
            )
        if effective_uid is not None:
            # OS boundary: simulate a foreign owner without privileged chown.
            setup += f"import os; os.geteuid = lambda: {effective_uid}; "
        command = [
            sys.executable,
            "-c",
            setup + "runpy.run_module('wgsextract_cli.main', run_name='__main__')",
        ]
    with subprocess.Popen(
        [*command, "realign", *argv],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # its own process group, for the shim to kill
    ) as run:
        (shims.parent / "run.pid").write_text(str(run.pid))
        stdout, stderr = run.communicate()
    return subprocess.CompletedProcess(run.args, run.returncode, stdout, stderr)


def _records(bam):
    header = [h for h in _view("view", "-H", str(bam)).splitlines() if h[:3] != "@PG"]
    return header, _view("view", str(bam))
