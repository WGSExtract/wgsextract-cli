"""CPU DeepVariant: Google's image through Docker, or run_deepvariant on PATH.

The image is too big for CI, so a fake Docker runs fake DeepVariant tools
(tests/fake_tools.py) that plan, stage and call like the real ones, on real
reads with real samtools and bcftools.

Shared fixtures and helpers remain here; focused staging, native, execution,
GPU and reference-evidence cases live in the sibling test_deepvariant_* modules.
"""

import hashlib
import json
import os
import random
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.disk_volumes import GIB, Volumes
from tests.fake_tools import (
    FakeDeepVariant,
    FakeDocker,
    fake_nvidia_smi,
    python_tool,
    require_bare_name_fakes,
    set_gpus,
)
from tests.htslib_helpers import (
    require_runnable,
    run_wgsextract,
    sam_to_bam,
    snp_sample,
    vcf_calls,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.commands.vcf import deepvariant
from wgsextract_cli.core import (
    containers,
    native_deepvariant,
    parabricks,
)
from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.dependencies import clear_tool_caches, get_tool_path
from wgsextract_cli.core.reference_evidence import (
    ReferenceEvidence,
)
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    run_command,
)
from wgsextract_cli.core.variant_confidence import Thresholds

# Found before a test replaces PATH.
CORE_TOOLS = {tool: shutil.which(tool) for tool in ("samtools", "bcftools", "tabix")}
STAGES = ["make_examples", "call_variants", "postprocess_variants"]
ONT = "@RG\tID:rg\tSM:S\tPL:ONT\tDS:basecall_model=dna_r10.4.1_e8.2_400bps_sup@v5.0.0"
ULTIMA = (
    "@RG\tID:rg\tSM:S\tPL:ULTIMA\tFO:TGCA",
    f"\ttp:B:c{',0' * 100}\tt0:Z:{'I' * 100}",
)


@pytest.fixture
def linux_x86(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(deepvariant.platform, "machine", lambda: "x86_64")


@pytest.fixture
def dv(tmp_path, monkeypatch, linux_x86, ram):
    """Reads, Google's image in a fake Docker, 64 GiB of free RAM, and no
    run_deepvariant on PATH."""
    ram(64 * GIB)
    require_bare_name_fakes()
    require_runnable("samtools", "bcftools", "tabix")
    (tmp_path / "reads").mkdir()
    fake = FakeDeepVariant(
        tmp_path / "deepvariant", CORE_TOOLS["samtools"], CORE_TOOLS["bcftools"]
    )
    docker = FakeDocker(tmp_path / "docker" / "bin", fake.bin)
    docker.add_image(deepvariant.DEFAULT_IMAGE)
    monkeypatch.setenv(
        "PATH", f"{docker.root.parent / 'bin'}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 64 * GIB)
    # A failed container's OOM event comes at once here.
    monkeypatch.setattr(containers, "OOM_EVENT_GRACE", 0.5)
    clear_tool_caches()
    return SimpleNamespace(
        sample=snp_sample(tmp_path / "reads"),
        deepvariant=fake,
        docker=docker,
        out=tmp_path / "out",
        tmp=tmp_path,
    )


@pytest.fixture
def arm64(dv, monkeypatch):
    """Linux ARM64, with a native engine and the native image built."""
    monkeypatch.setattr(deepvariant.platform, "machine", lambda: "aarch64")
    dv.docker.set(arch="aarch64")
    dv.docker.add_image(deepvariant.DEFAULT_ARM64_IMAGE, platform="linux/arm64")
    return dv


@pytest.fixture
def apple(dv, monkeypatch):
    """An Apple Silicon Mac: Docker Desktop's Linux VM is aarch64, and only
    Google's amd64 image is pulled."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(deepvariant.platform, "machine", lambda: "arm64")
    dv.docker.set(arch="aarch64")
    return dv


def call(dv, *options, reads=None, out=None, threads="4"):
    run_wgsextract(
        "vcf", "deepvariant", "--input", str(reads or dv.sample.bam),
        "--ref", dv.sample.ref, "--outdir", str(out or dv.out), "--threads", threads,
        *options,
    )  # fmt: skip


@pytest.mark.parametrize("contig", ["chrM", "chrY", "chrX"])
@pytest.mark.parametrize("bounded", [False, True])
def test_963_par_free_haploid_calls_do_not_pass_an_empty_par_bed(dv, contig, bounded):
    dv.sample = snp_sample(dv.tmp / "hg38", contig=contig, assembly="GRCh38")
    if contig != "chrM":
        run_wgsextract("sample", "set", "--input", dv.sample.bam, "--sex", "Male")
    call(dv, *(["--region", contig] if bounded else []))
    calls = [
        (*row[:-1], row[-1].replace("|", "/"))
        for row in vcf_calls(dv.out / "deepvariant.vcf.gz")
    ]
    assert calls == dv.sample.calls
    plan = dv.deepvariant.runs("run_deepvariant")[-1]["opts"]
    # contract: PAR-free haploid selections need haploid control, not an empty BED.
    assert plan["haploid_contigs"] == contig
    assert "par_regions_bed" not in plan


def rerun(dv, *options, **kwargs):
    """Forget earlier runs, stop failing and call again."""
    dv.deepvariant.fail(None)
    dv.deepvariant.clear()
    dv.docker.clear()
    call(dv, *options, **kwargs)


def other_sample(dv, *args, **kwargs):
    work = dv.tmp / "other"
    work.mkdir()
    return snp_sample(work, *args, **kwargs)


def visible(out):
    """What a run left in ``out`` for the user, beside its step journal."""
    return sorted(
        p.name
        for p in out.iterdir()
        if not p.name.startswith(".") and not p.name.endswith(".steps.json")
    )


def dv_flags(dv):
    """The options run_deepvariant was last planned with."""
    return dv.deepvariant.runs("run_deepvariant")[-1]["opts"]


def stage_runs(dv):
    return [r for r in dv.docker.runs() if r["command"][:1] == ["/bin/bash"]]


def as_cram(sample, version, name=None):
    """``sample``'s reads as an indexed CRAM ``version``."""
    cram = str(Path(sample.bam).with_name(name or f"v{version}.cram"))
    run_command(
        ["samtools", "view", "-C", "--output-fmt-option", f"version={version}",
         "-T", sample.ref, "-o", cram, sample.bam]
    )  # fmt: skip
    run_command(["samtools", "index", "--csi" if name else "-c", cram])
    return cram


def read_names(alignment, ref, *regions):
    view = [
        "samtools",
        "view",
        *(["-M"] if regions else []),
        "-T",
        ref,
        alignment,
        *regions,
    ]
    out = run_command(view, capture_output=True).stdout
    return sorted({line.split("\t")[0] for line in out.splitlines()})


# Docker setup -------------------------------------------------------------


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("no docker", "Docker not found.*docker pull google/deepvariant"),
        ("no daemon", "permission denied.*docker group"),
        ("no image", "image isn't downloaded: docker pull google/deepvariant"),
    ],
)
def test_missing_docker_setup_is_named(dv, monkeypatch, setup, message):
    if setup == "no docker":
        tools = {str(Path(path).parent) for path in CORE_TOOLS.values()}
        monkeypatch.setenv("PATH", os.pathsep.join(tools))
    elif setup == "no daemon":
        dv.docker.set(info_error="permission denied while trying to connect")
    else:
        dv.docker.set(images={})
    with pytest.raises(WGSExtractError, match=message):
        call(dv)
    assert dv.docker.runs() == []


@pytest.mark.usefixtures("fake_pixi")
def test_windows_is_refused(monkeypatch):
    get_tool_path("run_deepvariant")  # No local runner in the isolated PATH.
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(WGSExtractError, match="Windows"):
        run_wgsextract("vcf", "deepvariant", "--input", "missing.bam")


def test_the_image_calls_reads_from_folders_it_mounts(dv):
    """The reference, reads, checkpoint and output are in separate folders,
    each mounted into the container (the fake fails on any other path)."""
    reads = dv.tmp / "bam" / "s.bam"
    reads.parent.mkdir()
    for suffix in ("", ".bai"):
        shutil.copy(dv.sample.bam + suffix, f"{reads}{suffix}")
    model = dv.tmp / "model" / "model.ckpt"
    model.parent.mkdir()
    Path(f"{model}.index").write_text("weights")

    call(
        dv, "--model-type", "WES", "--checkpoint", str(model), "-r", "chr1", reads=reads
    )

    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls
    assert visible(dv.out) == ["deepvariant.vcf.gz", "deepvariant.vcf.gz.tbi"]
    assert not (dv.out / deepvariant.WORK_DIRECTORY).exists()
    flags = dv_flags(dv)
    # contract: the chosen model, checkpoint, region and one shard per thread.
    assert (flags["model_type"], flags["customized_model"]) == ("WES", str(model))
    assert (flags["regions"], flags["num_shards"]) == ("chr1", "4")
    assert [r["command"][:2] for r in stage_runs(dv)] == [["/bin/bash", "-c"]] * 3


def test_local_run_deepvariant_runs_without_docker_our_python_or_stdin(
    dv, monkeypatch, open_stdin
):
    monkeypatch.setenv("PATH", f"{dv.deepvariant.bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", str(dv.tmp / "our-site-packages"))
    clear_tool_caches()
    call(dv)
    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls
    assert dv.docker.runs() == []
    assert dv.deepvariant.stages() == STAGES
    assert "regions" not in dv_flags(dv)
    assert dv_flags(dv)["model_type"] == "WGS"


def test_detected_ont_runs_the_ont_model_with_its_own_temporary_files(dv):
    reads = other_sample(dv, ONT)
    call(dv, reads=reads.bam)
    # contract: ONT reads get DeepVariant's ONT model.
    assert dv_flags(dv)["model_type"] == "ONT_R104"
    plan, *stages = dv.docker.runs()
    assert {r["image"] for r in [plan, *stages]} == {deepvariant.DEFAULT_IMAGE}
    work = dv.out / deepvariant.WORK_DIRECTORY
    for stage in stages:
        assert stage["env"]["TMPDIR"] == stage["env"]["HOME"] == str(work / ".tmp")
    # Planning, which a --dry-run does beside a running stage, has its own.
    assert Path(plan["env"]["TMPDIR"]).parent == work
    assert plan["env"]["TMPDIR"] != str(work / ".tmp")


@pytest.mark.parametrize(
    ("selection", "model"),
    [
        (["--pacbio"], "PACBIO"),
        (["--wes"], "WES"),
        (["--model-type", "HYBRID_PACBIO_ILLUMINA"], "HYBRID_PACBIO_ILLUMINA"),
        (["--wes", "--model-type", "PACBIO"], "PACBIO"),
        (["--pacbio", "--model-type", "WES"], "PACBIO"),
    ],
)
def test_model_options_choose_the_model(dv, selection, model):
    call(dv, *selection)
    # contract: the model DeepVariant calls with.
    assert dv_flags(dv)["model_type"] == model


def test_detected_ultima_reads_run_the_ultima_image(dv):
    dv.docker.add_image(deepvariant.DEFAULT_ULTIMA_IMAGE)
    sample = other_sample(dv, ULTIMA[0], tags=ULTIMA[1])
    call(dv, reads=sample.bam)
    # contract: Ultima reads get the Ultima model, in its own image.
    assert dv_flags(dv)["model_type"] == "ULTIMA"
    assert {r["image"] for r in dv.docker.runs()} == {deepvariant.DEFAULT_ULTIMA_IMAGE}


def test_mixed_platform_reads_need_an_explicit_model(dv):
    """#652: ONT and Illumina read groups in one BAM fell back to the WGS model."""
    mixed = other_sample(dv, f"{ONT}\n@RG\tID:ill\tSM:S\tPL:ILLUMINA")
    with pytest.raises(UsageError, match="--model-type"):
        call(dv, reads=mixed.bam)
    assert dv.docker.runs() == []
    call(dv, "--model-type", "WGS", reads=mixed.bam)
    # contract: the explicitly chosen model.
    assert dv_flags(dv)["model_type"] == "WGS"


def test_ont_conflicts_with_other_model_options(dv):
    with pytest.raises(WGSExtractError, match="cannot be combined"):
        call(dv, "--ont", "--wes")
    assert dv.docker.runs() == []


@pytest.mark.parametrize("selection", [[], ["--ont"], ["--model-type", "ONT_R104"]])
def test_apple_arm_refuses_ont_including_automatic_selection(apple, selection):
    apple.docker.add_image(deepvariant.DEFAULT_ARM64_IMAGE, platform="linux/arm64")
    reads = other_sample(apple, ONT)
    with pytest.raises(WGSExtractError, match="ONT_R104.*not qualified on ARM64"):
        call(apple, *selection, reads=reads.bam)
    assert apple.docker.runs() == []


# CRAM 3.1 staging -----------------------------------------------------------


@pytest.fixture
def big_cram(dv, monkeypatch):
    """A CRAM 3.1 that looks 1 GiB big (staging the whole of it needs ~2
    GiB) beside an output folder with 20 GiB free."""
    cram = as_cram(dv.sample, "3.1")
    dv.out.mkdir()
    volumes = Volumes(monkeypatch)
    volumes.sizes[Path(cram)] = GIB
    volumes.free[dv.out] = 20
    return SimpleNamespace(path=cram, volumes=volumes)


# Apple Silicon (#665) ---------------------------------------------------------


@pytest.fixture
def mac_native(apple, monkeypatch):
    """An Apple Silicon Mac with wgse-deepvariant on PATH and its weights
    (stand-ins, whose hashes stand for the published ones) in config.toml."""
    weights = apple.tmp / "weights"
    (weights / "wgs_small_weights").mkdir(parents=True)
    published = {}
    for name in native_deepvariant.WEIGHTS_SHA256["WGS"]:
        (weights / name).write_bytes(name.encode())
        published[name] = hashlib.sha256(name.encode()).hexdigest()
    monkeypatch.setitem(native_deepvariant.WEIGHTS_SHA256, "WGS", published)
    monkeypatch.setitem(deepvariant.settings, "deepvariant_weights", str(weights))
    monkeypatch.setenv(
        "PATH", f"{apple.deepvariant.native_bin}{os.pathsep}{os.environ['PATH']}"
    )
    clear_tool_caches()
    apple.weights = weights
    return apple


# Linux x86-64: wgse-deepvariant on the CPU or an NVIDIA GPU (#719) -----------


@pytest.fixture
def linux_native(dv, monkeypatch):
    """Linux x86-64 with wgse-deepvariant 0.2.0 on PATH, its WGS weights
    (stand-ins, whose hashes stand for the published ones), an RTX
    3090 with nothing else on it, and Google's image but no Parabricks."""
    weights = dv.tmp / "weights"
    published = {}
    for model, files in native_deepvariant.WEIGHTS_SHA256.items():
        published[model] = {}
        for name in files:
            (weights / name).parent.mkdir(parents=True, exist_ok=True)
            (weights / name).write_bytes(name.encode())
            published[model][name] = hashlib.sha256(name.encode()).hexdigest()
    monkeypatch.setattr(native_deepvariant, "WEIGHTS_SHA256", published)
    monkeypatch.setitem(deepvariant.settings, "deepvariant_weights", str(weights))
    dv.deepvariant.set_native_version("0.2.0")
    dv.gpus = dv.tmp / "gpus.json"
    set_gpus(dv.gpus, (24_576, 0))
    fake_nvidia_smi(dv.docker.root.parent / "bin", dv.gpus)
    monkeypatch.setenv(
        "PATH", f"{dv.deepvariant.native_bin}{os.pathsep}{os.environ['PATH']}"
    )
    clear_tool_caches()
    dv.weights = weights
    return dv


def native_inference(dv) -> dict:
    (planned,) = dv.deepvariant.runs("wgse-deepvariant call_variants")
    return planned["opts"]


def _mac_sysctl(dv, arm64: str) -> None:
    """The Mac's sysctl: hw.optional.arm64 is 1 on Apple Silicon, 0 on Intel."""
    body = f"import sys\nif sys.argv[1:] != ['-n', 'hw.optional.arm64']: sys.exit(1)\nprint({arm64!r})\n"
    python_tool(dv.docker.root.parent / "bin", "sysctl", body)


@pytest.fixture
def rosetta(apple, monkeypatch):
    """x86-64 Python under Rosetta on Apple Silicon: it reports x86_64."""
    monkeypatch.setattr(deepvariant.platform, "machine", lambda: "x86_64")
    _mac_sysctl(apple, "1")
    return apple


# Resources ------------------------------------------------------------------


@pytest.fixture
def genome_fai(dv):
    """Extra reference contigs model a whole genome's postprocess partitions
    without making the alignment's chr1 dictionary incompatible."""
    fai = Path(f"{dv.sample.ref}.fai")
    fai.write_text(fai.read_text() + "unobserved\t2999997000\t0\t60\t61\n")


def postprocess(dv):
    """postprocess_variants' last --cpus and --num_partitions."""
    opts = dv.deepvariant.runs("postprocess_variants")[-1]["opts"]
    return opts["cpus"], opts.get("num_partitions")


# Ultima on the GPU (#358) --------------------------------------------------


def configure_ultima_gpu(docker: FakeDocker, gpus: Path) -> None:
    docker.set(gpus=True, runtimes='{"nvidia":{},"runc":{}}')
    docker.add_image(deepvariant.DEFAULT_ULTIMA_GPU_IMAGE)
    set_gpus(gpus, (24_576, 0))
    fake_nvidia_smi(docker.root.parent / "bin", gpus)
    clear_tool_caches()


@pytest.fixture
def ultima_gpu(dv):
    """Ultima reads on a GPU host: an RTX 3090 with nothing else on it, Docker's
    NVIDIA runtime and Google's ULTIMA GPU image, but no Parabricks."""
    dv.gpus = dv.tmp / "gpus.json"
    configure_ultima_gpu(dv.docker, dv.gpus)
    dv.ultima = other_sample(dv, ULTIMA[0], tags=ULTIMA[1])
    return dv


def without_gpu_driver(monkeypatch):
    """Simulate a missing GPU driver even if the host has nvidia-smi."""
    resolve = parabricks.get_tool_path

    def no_driver(tool):
        return None if tool == "nvidia-smi" else resolve(tool)

    def unexpected_gpu_probe():
        pytest.fail("the run tried to query GPU memory without a driver")

    monkeypatch.setattr(parabricks, "get_tool_path", no_driver)
    monkeypatch.setattr(parabricks, "gpu_memory", unexpected_gpu_probe)


def gpu_call(dv, *options):
    call(dv, "--gpu", *options, reads=dv.ultima.bam)


def gpu_stages(dv):
    """The stages that ran, each with whether its container had the GPUs."""
    return [
        (re.search(r"/(\w+)\s", r["command"][2]).group(1), r["gpus"])
        for r in stage_runs(dv)
    ]


def pinned_ultima_gpu(dv):
    """The ULTIMA GPU image as Docker reports it: its ID is its pinned digest."""
    digest = deepvariant.DEFAULT_ULTIMA_GPU_IMAGE.rpartition("@")[2]
    dv.docker.add_image(deepvariant.DEFAULT_ULTIMA_GPU_IMAGE, image_id=digest)
    return digest


def recorded_images(dv):
    journal = json.loads((dv.out / "deepvariant.vcf.gz.steps.json").read_text())
    return {
        name: record["params"]["image"]
        for name, record in journal["steps"].items()
        if "image" in record.get("params", {})
    }


OTHER_DIGEST = "sha256:" + "b" * 64


def as_v085_journal(dv, image_id):
    """Rewrite the run's journal as v0.8.5 wrote it: the pinned image with
    Docker's image ID appended (#597), keyed as StepJournal keys its records."""
    path = dv.out / "deepvariant.vcf.gz.steps.json"
    journal = json.loads(path.read_text())
    for name, record in journal["steps"].items():
        if "image" not in record.get("params", {}):
            continue
        record["params"]["image"] += f"@{image_id}"
        payload = {
            "workflow": journal["workflow"],
            "step": name,
            # A running record holds neither; its stage's are 1 and none.
            "version": record.get("version", 1),
            "params": record["params"],
            "tools": record.get("tools") or {},
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        record["key"] = hashlib.sha256(text.encode()).hexdigest()
    path.write_text(json.dumps(journal))


def resume_v085(dv, image_id, *options):
    """Run, turn the journal into v0.8.5's, and run again."""
    digest = pinned_ultima_gpu(dv)
    gpu_call(dv)
    as_v085_journal(dv, image_id or digest)
    dv.deepvariant.clear()
    dv.docker.clear()
    gpu_call(dv, *options)


CPU_ID = "sha256:" + "c" * 64
CPU_DIGEST = "google/deepvariant@sha256:" + "d" * 64


def recorded_params(dv):
    journal = json.loads((dv.out / "deepvariant.vcf.gz.steps.json").read_text())
    return [
        {k: v for k, v in record["params"].items() if k.startswith("image")}
        for record in journal["steps"].values()
        if "image" in record.get("params", {})
    ]


def as_pre601_journal(dv, image_id):
    """Rewrite the run's journal as v0.8.5 to #601 wrote it for a tag: the tag
    with Docker's image ID appended, keyed as StepJournal keys its records."""
    path = dv.out / "deepvariant.vcf.gz.steps.json"
    journal = json.loads(path.read_text())
    for name, record in journal["steps"].items():
        params = record.get("params", {})
        if "image" not in params:
            continue
        params.pop("image_id", None)
        params["image"] = f"{deepvariant.DEFAULT_IMAGE}@{image_id}"
        payload = {
            "workflow": journal["workflow"],
            "step": name,
            # A running record holds neither; its stage's are 1 and none.
            "version": record.get("version", 1),
            "params": params,
            "tools": record.get("tools") or {},
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        record["key"] = hashlib.sha256(text.encode()).hexdigest()
    path.write_text(json.dumps(journal))


def ultima_gpu_run(dv, **options):
    return api.vcf.deepvariant(
        api.VcfDeepvariantParams(
            input=Path(dv.ultima.bam), ref=Path(dv.sample.ref), outdir=dv.out,
            gpu=True, **options,
        )
    )  # fmt: skip


def batch_warned(result):
    return "deepvariant_gpu_batch_size" in [w.code for w in result.warnings]


# Reference evidence (#863) ------------------------------------------------

EVIDENCE_THRESHOLDS = Thresholds(min_depth=10, high_depth=20, min_gq=20, high_gq=30)


def call_with_evidence(dv, region=None):
    """vcf deepvariant --reference-evidence gvcf; its outputs by role."""
    result = api.vcf.deepvariant(
        api.VcfDeepvariantParams(
            input=Path(dv.sample.bam), ref=Path(dv.sample.ref), outdir=dv.out,
            threads=4, reference_evidence="gvcf", region=region,
        )
    )  # fmt: skip
    return {o.role: o for o in result.outputs}


def evidence_state(gvcf, contig, position):
    spans = ReferenceEvidence(str(gvcf)).spans(
        EVIDENCE_THRESHOLDS, [(contig, position, position)]
    )
    return next(iter(spans)).state


def woman_sample(dv):
    """A synthetic GRCh38 sample with reads on chr1 and chrY, 500-2499."""
    rng = random.Random(863)
    seq = "".join(rng.choice("ACGT") for _ in range(3000))
    root = dv.tmp / "hg38"
    root.mkdir()
    ref = write_fasta(root / "ref.fa", {"chr1": seq, "chrY": seq}, assembly="GRCh38")
    lines = ["@HD\tVN:1.6\tSO:unsorted", "@SQ\tSN:chr1\tLN:3000"]
    lines += ["@SQ\tSN:chrY\tLN:3000", "@RG\tID:rg\tSM:S"]
    lines += [
        f"{chrom}{i}\t0\t{chrom}\t{start}\t60\t100M\t*\t0\t0\t"
        f"{seq[start - 1 : start + 99]}\t{'I' * 100}\tRG:Z:rg"
        for chrom in ("chr1", "chrY")
        for i, start in enumerate(range(500, 2400, 5))
    ]
    bam = sam_to_bam(lines, root / "woman.bam")
    run_wgsextract("sample", "set", "--input", bam, "--sex", "Female")
    return SimpleNamespace(ref=str(ref), bam=bam)
