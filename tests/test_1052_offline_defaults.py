"""#1052: local operations must not even attempt DNS or a socket connection."""

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.home_isolation import isolated_home_env, state_directory_setting
from tests.htslib_helpers import (
    needs_htslib,
    needs_tools,
    run_wgsextract,
    sam_to_bam,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.ref_library.remote_sizes import CACHE_FILE
from wgsextract_cli.core.results import CommandResult, collecting
from wgsextract_cli.core.utils import run_command
from wgsextract_cli.server.operations import RequestError, find, params_from_json


@pytest.fixture
def blocked_network(monkeypatch):
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("#1052: a default command attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield attempts
    assert not attempts, (
        "#1052: catching a connection error does not make a command offline"
    )


@needs_htslib
def test_1591_default_cli_commands_never_attempt_network(tmp_path: Path) -> None:
    bam = sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:1000",
            "@SQ\tSN:chrM\tLN:16569",
            "fake1\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII",
            "fakeMT\t0\tchrM\t1\t60\t4M\t*\t0\t0\tACGT\tIIII",
        ],
        tmp_path / "fake.bam",
    )
    vcf = tmp_path / "fake.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=1000>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tfake\n"
        "chr1\t20\trsFake\tA\tG\t50\tPASS\t.\tGT:DP:GQ\t0/1:30:60\n"
    )
    fasta = write_fasta(
        tmp_path / "reference" / "genomes" / "hg19.fa",
        {"chr1": "A" * 1000},
        assembly="GRCh37",
    )
    library = fasta.parent.parent
    templates = library / "microarray" / "raw_file_templates" / "body"
    templates.mkdir(parents=True)
    (templates / "FTDNA_V1_Affy.csv").write_text("rsFake,1,20,\n")
    table = tmp_path / "chips.tab"
    table.write_text("chr1\t20\trsFake\n")
    run_command([get_tool_path("bgzip"), "-f", str(table)])
    targets = tmp_path / "chips.tab.gz"
    run_command([get_tool_path("tabix"), "-s1", "-b2", "-e2", str(targets)])
    prepared = tmp_path / "prepared.vcf.gz"
    config = tmp_path / "config.toml"
    config.write_text(state_directory_setting(tmp_path), encoding="utf-8")
    commands = [
        (["--version"], 0),
        (["help", "--all", "--json"], 0),
        (["info", "--input", bam], 0),
        (["extract", "mt-bam", "--input", bam], 0),
        (["vcf", "prepare", "--vcf-input", str(vcf), "--output", str(prepared)], 0),
        (["vcf", "info", "--vcf-input", str(prepared)], 0),
        (["ref", "status", "--ref", str(library)], 0),
        (["ref", "microarray-targets", "--ref", str(library)], 2),
        (["microarray", "--input", str(vcf), "--ref", str(fasta),
          "--ref-vcf-tab", str(targets)], 0),
        (["report", "--input", bam, "--outdir", str(tmp_path)], 0),
    ]  # fmt: skip
    # Start fresh and instrument Python sockets before importing the CLI.
    # The network-namespace extraction test below covers native subprocesses.
    # Keep tool search paths, not the caller's config, proxy or opt-in settings.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR"}
    }
    env.update(isolated_home_env(tmp_path / "home"))
    env.update(dict.fromkeys(("TEMP", "TMP", "TMPDIR"), str(tmp_path)))
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import socket
import sys

attempts = []

def refuse(*args, **kwargs):
    attempts.append(" ".join(command[:2]))
    raise AssertionError("network blocked by offline regression")

for name in ("connect", "connect_ex"):
    setattr(socket.socket, name, refuse)
for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex",
             "gethostbyaddr", "getnameinfo"):
    setattr(socket, name, refuse)

command = ["startup"]
try:
    sys.path.insert(0, sys.argv[3])
    from wgsextract_cli.main import main

    for command, expected in json.loads(sys.argv[2]):
        code = 0
        try:
            main(["--config", sys.argv[1], *command])
        except SystemExit as exc:
            code = exc.code
        assert code == expected, (command, code)
finally:
    assert not attempts, f"Unexpected network attempts: {attempts}"
""",
            str(config),
            json.dumps(commands),
            str(Path(__file__).resolve().parents[1] / "src"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr
    assert "hg18ToHg19.over.chain.gz" in run.stderr and "--download" in run.stderr
    output = tmp_path / "WGSEv6"
    assert (output / "fake_mtDNA.bam").is_file()
    assert "rsFake\t1\t20\tAG" in (output / "fake_CombinedKit.txt").read_text()
    assert (tmp_path / "fake_personal_report.html").is_file()


@needs_htslib
def test_1052_default_inspection_and_extraction_use_real_tools_offline(
    tmp_path, blocked_network
):
    sam = tmp_path / "fake.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:chr1\tLN:1000\n@SQ\tSN:chrM\tLN:16569\n"
        "fake1\t0\tchr1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n"
        "fakeMT\t0\tchrM\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n"
    )
    bam = sam_to_bam(sam.read_text().splitlines(), tmp_path / "fake.bam")
    info = api.info.info(api.InfoParams(input=Path(bam), outdir=tmp_path))
    assert info.exit_code == 0
    extracted = api.extract.mt_bam(
        api.MtBamParams(input=Path(bam), outdir=tmp_path, threads=1)
    )
    assert extracted.exit_code == 0
    from wgsextract_cli.core.utils import run_command

    output = tmp_path / "fake_mtDNA.bam"
    reads = run_command(["samtools", "view", str(output)], capture_output=True).stdout
    assert "fakeMT" in reads and "fake1" not in reads
    assert not blocked_network


def test_1052_empty_library_status_does_not_probe_servers(tmp_path, blocked_network):
    settings["reference_library"] = str(tmp_path / "library")
    result = api.ref.library_list(api.RefLibraryListParams(ref=tmp_path / "library"))
    assert result.exit_code == 0
    assert not blocked_network


def test_1052_reference_genomes_page_lists_offline_and_keeps_stale_cached_sizes(
    tmp_path, blocked_network
):
    page = json.loads(
        (
            Path(__file__).parents[1]
            / "gui/examples/WGSExtract/pages/reference-genomes.json"
        ).read_text(encoding="utf-8")
    )
    control = next(
        control
        for section in page["sections"]
        for control in section.get("controls", [])
        if control["id"] == "reference_genomes"
    )
    library = tmp_path / "library"
    library.mkdir()
    command = [*control["dataSource"]["arguments"], "--ref", str(library)]
    empty = CommandResult(command="ref library")
    with collecting(empty):
        run_wgsextract(*command)
    assert empty.details is not None
    genomes = empty.details["genomes"]
    assert genomes
    url = next(genome["url"] for genome in genomes if genome["url"])
    (library / CACHE_FILE).write_text(
        json.dumps({url: {"bytes": 123456, "fetched_at": 0}}),
        encoding="utf-8",
    )
    cached = CommandResult(command="ref library")
    with collecting(cached):
        run_wgsextract(*command)
    assert cached.details is not None
    genome = next(g for g in cached.details["genomes"] if g["url"] == url)
    assert genome["remote_bytes"] == 123456
    assert not blocked_network


@pytest.mark.parametrize(
    "page_name", ["extract", "vcf", "microarray", "library", "datasets"]
)
def test_1052_1060_page_catalog_data_sources_stay_offline(
    tmp_path, blocked_network, page_name
):
    page = Path(__file__).parents[1] / f"gui/examples/WGSExtract/pages/{page_name}.json"
    pending = [json.loads(page.read_text(encoding="utf-8"))]
    commands = set()
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            source = node.get("dataSource")
            if (
                isinstance(source, dict)
                and source.get("path") == "scripts/run-wgsextract.sh"
            ):
                commands.add(tuple(source["arguments"]))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    assert commands
    library = tmp_path / "library"
    library.mkdir()
    for command in commands:
        result = CommandResult(command=" ".join(command))
        with collecting(result):
            run_wgsextract(*command, "--ref", str(library))
    assert not blocked_network


@needs_htslib
def test_1052_1060_gui_default_vcf_call_uses_real_tools_offline(
    tmp_path, blocked_network
):
    page = json.loads(
        (
            Path(__file__).parents[1] / "gui/examples/WGSExtract/pages/extract.json"
        ).read_text(encoding="utf-8")
    )
    section = next(s for s in page["sections"] if s["id"] == "variant-calling")
    types = next(c for c in section["controls"] if c["id"] == "vcf_call_types")
    action = next(a for a in section["actions"] if a["id"] == "vcf-call")
    sequence = "ACGT" * 250
    ref = write_fasta(tmp_path / "fake_hg38.fa", {"chr1": sequence}, assembly="GRCh38")
    read = sequence[:39] + "G" + sequence[40:100]
    bam = sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chr1\tLN:1000",
            *(
                f"fake{i}\t0\tchr1\t1\t60\t100M\t*\t0\t0\t{read}\t{'I' * 100}"
                for i in range(20)
            ),
        ],
        tmp_path / "fake.bam",
    )
    out = tmp_path / "calls"
    values = {
        "{{bam_path}}": str(bam),
        "{{vcf_call_types}}": ",".join(
            option["id"] for option in types["options"] if option.get("selected")
        ),
        "{{ref_path}}": str(ref),
        "{{out_dir}}": str(out),
    }
    arguments = [values.get(arg, arg) for arg in action["command"]["arguments"]]
    for group in action["command"].get("optionalArguments", []):
        expanded = [values.get(arg, arg) for arg in group]
        if not any("{{" in arg for arg in expanded):
            arguments.extend(expanded)
    with collecting(CommandResult(command="vcf call")):
        run_wgsextract(*arguments)
    from wgsextract_cli.core.utils import run_command

    snps = out / "snps.vcf.gz"
    assert (out / "indels.vcf.gz").is_file()
    observed = run_command(
        ["bcftools", "query", "-f", "%CHROM\\t%POS\\t%ALT\\n", str(snps)],
        capture_output=True,
    )
    assert observed.stdout.splitlines() == ["chr1\t40\tG"]
    assert not blocked_network


@pytest.mark.parametrize("configured", [False, True])
def test_1052_missing_ancestry_names_data_and_confirmation_without_connecting(
    tmp_path, blocked_network, configured
):
    settings["allow_reference_downloads"] = configured
    sample = tmp_path / "fake.vcf"
    sample.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tfake\n"
        "1\t10\t.\tA\tG\t50\tPASS\t.\tGT\t0/1\n"
    )
    result = api.qc.ancestry(
        api.QcAncestryParams(
            input=sample,
            ref=tmp_path / "library",
            download=False if configured else None,
        ),
        check=False,
    )
    assert result.exit_code == 2
    assert result.error is not None
    assert "HGDP+1KG" in result.error.message
    assert "--download" in result.error.message
    assert "ref ancestry --install" in result.error.message
    assert not blocked_network


@needs_htslib
def test_1052_missing_y_tables_and_pgs_weights_do_not_connect(
    tmp_path, blocked_network
):
    settings["reference_library"] = str(tmp_path / "library")
    sample = Path(__file__).parent / "fixtures/pgs/sample.vcf"
    y = api.lineage.y_haplogroup(
        api.YHaplogroupParams(
            input=sample, build="hg38", outdir=tmp_path / "y", no_ref_inference=True
        ),
        check=False,
    )
    assert y.exit_code == 2, y.error
    assert y.error and "Yleaf" in y.error.message and "--download" in y.error.message
    pgs = api.score.pgs(
        api.ScorePgsParams(
            input=sample, build="GRCh38", pgs_id="PGS999999", outdir=tmp_path / "pgs"
        ),
        check=False,
    )
    assert pgs.exit_code == 2
    assert pgs.error and "PGS Catalog" in pgs.error.message
    assert "--download" in pgs.error.message
    assert not blocked_network


def test_1052_read_only_mcp_refuses_download_permission(tmp_path):
    with pytest.raises(RequestError) as error:
        params_from_json(
            find("qc/ancestry"),
            {"input": str(tmp_path / "fake.vcf"), "download": True},
            read_only=True,
        )
    assert getattr(error.value, "code", None) == "read_only"


@pytest.fixture
def network_namespace():
    unshare = shutil.which("unshare")
    if not unshare:
        pytest.skip("host has no unprivileged network-namespace sandbox")
    sandbox = [unshare, "--user", "--map-root-user", "--net"]
    probe = subprocess.run([*sandbox, "true"], capture_output=True)
    if probe.returncode:
        pytest.skip("host disallows unprivileged network namespaces")
    return sandbox


@needs_htslib
def test_1052_subprocess_extraction_works_in_a_network_namespace(
    tmp_path, network_namespace
):
    bam = sam_to_bam(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:chrM\tLN:16569",
            "fakeMT\t0\tchrM\t1\t60\t4M\t*\t0\t0\tACGT\tIIII",
        ],
        tmp_path / "fake.bam",
    )
    run = subprocess.run(
        [
            *network_namespace,
            sys.executable,
            "-m",
            "wgsextract_cli.main",
            "extract",
            "mt-bam",
            "--input",
            bam,
            "--outdir",
            str(tmp_path),
            "--threads",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "fake_mtDNA.bam").is_file()


def test_1052_runtime_launcher_never_resolves_or_uses_an_uninstalled_environment(
    tmp_path, network_namespace
):
    pixi = os.environ.get("PIXI_EXE") or shutil.which("pixi")
    if not pixi:
        pytest.skip("real Pixi is not installed")
    project = tmp_path / "app"
    project.mkdir()
    manifest = project / "pixi.toml"
    original = (
        '[workspace]\nname = "offline-1052"\nversion = "0.1.0"\n'
        'channels = []\nplatforms = ["linux-64", "linux-aarch64"]\n'
    )
    manifest.write_text(original)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PIXI_PROJECT_")
    }
    env.update(
        PIXI=pixi,
        PIXI_HOME=str(tmp_path / "pixi-home"),
        PIXI_CACHE_DIR=str(tmp_path / "pixi-cache"),
        WGSEXTRACT_APP_DIR=str(project),
    )
    subprocess.run(
        [*network_namespace, pixi, "lock", "--manifest-path", str(manifest)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            *network_namespace,
            pixi,
            "install",
            "--frozen",
            "--manifest-path",
            str(manifest),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    lock = project / "pixi.lock"
    before = lock.read_bytes()
    manifest.write_text(
        original.replace(
            "channels = []",
            'channels = ["https://example.invalid/privacy-channel"]',
        )
        + '\n[dependencies]\nprivacy-network-sentinel = "*"\n'
    )
    launcher = (
        Path(__file__).parents[1]
        / "gui/examples/WGSExtract/scripts/posix/run-wgsextract-env.sh"
    )
    command = [
        *network_namespace,
        "sh",
        str(launcher),
        sys.executable,
        "-c",
        "print('offline-1052')",
    ]
    run = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert "offline-1052" in run.stdout
    assert lock.read_bytes() == before
    (project / ".pixi/envs/default/conda-meta/pixi").unlink()
    refused = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=30
    )
    assert refused.returncode == 127
    assert "setup" in refused.stderr.lower()
    assert "offline-1052" not in refused.stdout


@needs_tools("samtools", "bwa-mem2")
def test_1052_ref_index_builds_locally_even_when_a_prebuilt_download_exists(
    tmp_path, monkeypatch, blocked_network
):
    from wgsextract_cli.core import bwa_mem2

    ref = write_fasta(tmp_path / "fake.fa", {"chr1": "ACGT" * 1000})
    fingerprint = bwa_mem2.contig_fingerprint(str(ref))
    monkeypatch.setitem(
        bwa_mem2.PREBUILT_INDEXES,
        fingerprint,
        bwa_mem2.PrebuiltIndex(
            "tiny public reference", "https://example.invalid/fake.fa", {}
        ),
    )
    result = api.ref.index(api.RefIndexParams(ref=ref, aligner="bwa-mem2"))
    assert result.exit_code == 0
    assert bwa_mem2.has_index(str(ref))
    assert not blocked_network


@needs_htslib
def test_1052_explicit_real_benchmark_download_is_not_blocked(tmp_path):
    import io
    import zipfile

    from tests.http_server import serving

    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("README", "deliberately lacks benchmark inputs")
    cache = tmp_path / "datasets"
    with serving(body.getvalue()) as server:
        result = api.benchmark.benchmark(
            api.BenchmarkParams(
                dataset="real",
                profile="smoke",
                outdir=tmp_path / "benchmark",
                dataset_url=server.url_for("dataset.zip"),
                dataset_sha256="",
                dataset_cache_dir=cache,
                threads=1,
            ),
            check=False,
        )
    assert (cache / "dataset.zip").read_bytes() == body.getvalue()
    assert result.error is not None
    assert "--download" not in result.error.message
