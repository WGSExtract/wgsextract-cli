"""Focused server jobs regressions."""

import json
import shutil
import subprocess

import pytest

from tests.test_server import (
    FAKE_DIR,
    JOB_VALIDATOR,
    VALIDATOR,
    _serve,
)
from tests.test_server import (
    client as client,
)
from tests.test_server import (
    fake_data as fake_data,
)
from tests.test_server import (
    slow_coverage as slow_coverage,
)
from tests.test_server import (
    variant_tables as variant_tables,
)
from tests.test_server import (
    work as work,
)
from wgsextract_cli.core import results
from wgsextract_cli.core.utils import WGSExtractError
from wgsextract_cli.server.jobs import JobManager
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    params_from_json,
)


def test_jobs_writing_to_one_folder_run_one_at_a_time(
    tmp_path, work, fake_data, slow_coverage
):
    # PR #422 review: with --max-jobs 2, two jobs saving the same fixed file
    # names in one folder ran at once and overwrote each other's outputs.
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    params = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        first = client.request("POST", "/ops/info/calculate-coverage", params)[1]
        same = client.request("POST", "/ops/info/calculate-coverage", params)[1]
        apart = client.request(
            "POST", "/ops/info/calculate-coverage", {**params, "outdir": str(other)}
        )[1]
        assert (first["status"], same["status"], apart["status"]) == (
            "running",
            "queued",
            "running",
        )
        with pytest.raises(WGSExtractError, match="Another wgsextract server"):
            JobManager(tmp_path / "state" / "jobs", config_path=None)


@pytest.mark.parametrize(
    ("op", "into_work"),
    [
        (
            "bam/merge",
            lambda work, bam: {
                "inputs": [str(bam), str(bam)],
                "output": str(work / "m.bam"),
            },
        ),
        ("qc/multiqc", lambda work, bam: {"input": str(work)}),
    ],
    ids=["named-output", "folder-input"],
)
def test_a_job_writing_into_a_busy_folder_waits(
    tmp_path, work, fake_data, slow_coverage, op, into_work
):
    # PR #445 review: only the outdir was serialised, so a job that writes into
    # a busy folder by an output path or a folder input ran at the same time.
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST", f"/ops/{op}", {**into_work(work, bam), "outdir": str(other)}
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_a_home_relative_folder_input_locks_the_folder_it_names(
    tmp_path, work, fake_data, slow_coverage, monkeypatch
):
    # PR #458 re-review: the lock read `~/reports` unexpanded, so it locked
    # the folder above, not the reports folder the job writes into.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(work.parent))
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST",
            "/ops/qc/multiqc",
            {"input": f"~/{work.name}", "outdir": str(other)},
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


@pytest.mark.parametrize(
    ("op", "into_work"),
    [
        (
            "analyze/comprehensive",
            lambda work, other: {
                "vcf_inputs": [str(work / "x.vcf")],
                "outdir": str(other),
            },
        ),
        (
            "analyze/batch-gen",
            lambda work, other: {
                "directory": str(other),
                "output": str(work / "b.csv"),
            },
        ),
    ],
    ids=["vcf-prepared-beside", "batch-file"],
)
def test_an_analyze_job_writing_into_a_busy_folder_waits(
    tmp_path, work, fake_data, slow_coverage, op, into_work
):
    # PR #463 review: comprehensive compresses and indexes a plain VCF beside
    # itself, and batch-gen writes its batch file, yet only outdir was locked.
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request("POST", f"/ops/{op}", into_work(work, other))
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_a_repair_writing_its_output_into_a_busy_folder_waits(
    tmp_path, work, fake_data, slow_coverage
):
    # #478: a repair's --output may name a file outside its outdir.
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST",
            "/ops/repair/ftdna-vcf",
            {
                "input": str(FAKE_DIR / "fake.vcf.gz"),
                "output": str(work / "r.vcf"),
                "outdir": str(other),
            },
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_an_example_download_into_a_busy_library_waits(
    tmp_path, work, fake_data, slow_coverage
):
    # #478: example-genome download writes into <target_root>/test-1000genomes,
    # not an outdir, so that folder is locked.
    bam, ref = fake_data
    collection = work / "test-1000genomes"
    collection.mkdir()
    with _serve(tmp_path, [work, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(collection)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST",
            "/ops/example-genome/download",
            {"target_root": str(work), "dry_run": True},
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_hla_typing_building_its_library_index_waits_for_a_busy_library(
    tmp_path, work, fake_data, slow_coverage
):
    # #478: `typing hla` without --hla-index builds its T1K index in the
    # reference library, outside its outdir, so that folder is locked too.
    bam, ref = fake_data
    library = work / "library"
    index = library / "ref" / "t1k_hla"
    index.mkdir(parents=True)
    (library / "genomes").mkdir()
    shutil.copy(ref, library / "genomes" / "ref.fa")
    other = tmp_path / "other"
    other.mkdir()
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(index)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST",
            "/ops/typing/hla",
            {
                "input": str(bam),
                "ref": str(library / "genomes" / "ref.fa"),
                "outdir": str(other),
            },
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_qc_ancestry_fetching_its_library_reference_waits_for_a_busy_folder(
    tmp_path, work, fake_data, slow_coverage
):
    # #683 review: the first `qc ancestry` downloads its reference into
    # <library>/ancestry; two such jobs at once raced on one .partial file.
    bam, ref = fake_data
    library = work / "library"
    (library / "ancestry").mkdir(parents=True)
    (library / "genomes").mkdir()
    shutil.copy(ref, library / "genomes" / "ref.fa")
    other = tmp_path / "other"
    other.mkdir()
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(library / "ancestry")}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST",
            "/ops/qc/ancestry",
            {
                "input": str(bam),
                "ref": str(library / "genomes" / "ref.fa"),
                "outdir": str(other),
            },
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_a_methylation_run_waits_while_its_reference_folder_is_busy(
    tmp_path, work, fake_data, slow_coverage
):
    # #493 review: an unindexed reference is indexed beside itself.
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    shutil.copy(ref, work / "ref.fa")
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        request = {"input": str(bam), "ref": str(work / "ref.fa")}
        status, second = client.request(
            "POST", "/ops/methylation/pileup", {**request, "outdir": str(other)}
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_a_vep_run_on_a_vcf_in_a_busy_folder_waits(
    tmp_path, work, fake_data, slow_coverage
):
    # #478: VEP indexes an unindexed VCF beside itself, outside its outdir.
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    vcf = work / "s.vcf.gz"
    shutil.copy(FAKE_DIR / "fake.vcf.gz", vcf)
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST", "/ops/vep/run", {"input": str(vcf), "outdir": str(other)}
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_a_vep_download_into_a_busy_cache_folder_waits(
    tmp_path, work, fake_data, slow_coverage
):
    # #478: `vep download` unpacks into its cache folder, not an outdir.
    bam, ref = fake_data
    cache = work / "vep"
    cache.mkdir()
    with _serve(tmp_path, [work, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(cache)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST", "/ops/vep/download", {"vep_cache": str(cache)}
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_an_example_download_waits_for_a_job_in_its_example_folder(
    tmp_path, work, fake_data, slow_coverage
):
    # PR #494 review: a forced download deletes files in the example folder,
    # and folder locks match exactly, so locking the collection alone let a
    # job writing in that example folder run alongside it.
    bam, ref = fake_data
    example = work / "test-1000genomes" / "phase3-chrmt-vcf"
    example.mkdir(parents=True)
    with _serve(tmp_path, [work, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(example)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request(
            "POST",
            "/ops/example-genome/download",
            {
                "target_root": str(work),
                "example_ids": ["phase3-chrmt-vcf"],
                "dry_run": True,
            },
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


@pytest.mark.parametrize("op", ["vep/download", "vep/verify"])
def test_a_vep_cache_request_checks_the_cache_it_falls_back_to(
    tmp_path, work, monkeypatch, op
):
    # #478: with no vep_cache, the configured cache (else ~/.vep) is the one
    # read or written, so it must be an allowed folder too.
    from wgsextract_cli.core.config import settings

    outside = tmp_path / "outside"
    monkeypatch.setitem(settings, "vep_cache_directory", str(outside))
    with _serve(tmp_path, [work]) as client:
        status, body = client.request("POST", f"/ops/{op}", {})
        assert (status, body["error"]["code"]) == (403, "path_not_allowed"), body
    assert not outside.exists()
    # Naming an allowed cache passes (checked directly: a job would download).
    PathGuard([work]).check(params_from_json(find(op), {"vep_cache": str(work)}))


@pytest.mark.parametrize("op", ["vep/download", "vep/verify"])
def test_a_vep_cache_request_checks_the_configured_fasta_library_cache(
    tmp_path, work, monkeypatch, op
):
    # PR #498 review: with only reference_fasta configured, the cache is in
    # that FASTA's library, as on the CLI, so that is the folder checked.
    from wgsextract_cli.core.config import settings

    genomes = tmp_path / "lib" / "genomes"
    genomes.mkdir(parents=True)
    (genomes / "hg38.fa").write_text(">chr1\nA\n")
    monkeypatch.setitem(settings, "reference_fasta", str(genomes / "hg38.fa"))
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([work]).check(params_from_json(find(op), {}))
    PathGuard([tmp_path / "lib"]).check(params_from_json(find(op), {}))


@pytest.mark.parametrize("escape", [None, "test-1000genomes", "phase3-chrmt-vcf"])
def test_an_example_download_stays_in_the_allowed_folders(
    client, tmp_path, work, escape
):
    # PR #494 review: the guard saw only target_root, so an omitted one (the
    # configured or built-in library) or a symlinked collection or example
    # folder inside an allowed one sent the download elsewhere.
    body: dict = {"example_ids": ["phase3-chrmt-vcf"], "dry_run": True}
    if escape is not None:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = work / "test-1000genomes"
        if escape != link.name:
            link.mkdir()
            link = link / escape
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("can't create symlinks here")
        body["target_root"] = str(work)
    status, response = client.request("POST", "/ops/example-genome/download", body)
    assert (status, response["error"]["code"]) == (403, "path_not_allowed")


def test_example_genome_list_answers_with_the_catalog(tmp_path, work):
    with _serve(tmp_path, [work]) as client:
        status, body = client.request(
            "POST",
            "/ops/example-genome/list?wait=60",
            {"target_root": str(work), "tag": ["pacbio"]},
        )
    assert (status, body["status"]) == (200, "succeeded"), body
    details = body["result"]["details"]
    assert details["collection"] == str(work.resolve() / "test-1000genomes")
    assert details["examples"]
    assert all("pacbio" in example["tags"] for example in details["examples"])


def test_a_vep_folder_run_refuses_a_link_out_of_the_allowed_folders(tmp_path, work):
    # Copilot review of #491: the guard checks the folder, not the files
    # VEP finds in it.
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(FAKE_DIR / "fake.vcf.gz", outside / "secret.vcf.gz")
    batch = work / "batch"
    batch.mkdir()
    shutil.copy(FAKE_DIR / "fake.vcf.gz", batch / "a.vcf.gz")
    try:
        (batch / "b.vcf.gz").symlink_to(outside / "secret.vcf.gz")
    except OSError:
        pytest.skip("symlinks are not available")
    request = {"input": str(batch), "outdir": str(work)}
    with _serve(tmp_path, [work]) as client:
        status, job = client.request("POST", "/ops/vep/run?wait=120", request)
    assert (status, job["status"], job["exit_code"]) == (200, "failed", 1), job
    assert "b.vcf.gz leads outside" in job["error"]["message"]
    assert not list(work.glob("*_vep*"))


def test_a_server_request_cannot_pass_raw_vep_arguments():
    # #478: --vep-args can name files VEP reads or writes (--custom, -o),
    # which the path guard can't see.
    with pytest.raises(RequestError, match="can't be set by a server request"):
        params_from_json(find("vep/run"), {"input": "a.vcf.gz", "vep_args": "-o x"})


def test_home_relative_paths_reach_the_job_expanded(tmp_path, work, monkeypatch):
    # PR #463 review: the guard approved ~/x as the home folder's; the job
    # must write there too, not in a `~` folder in its working folder.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(work.parent))
    shutil.copy(FAKE_DIR / "fake.bam", work / "s1.bam")
    request = {"directory": f"~/{work.name}", "output": f"~/{work.name}/b.csv"}
    with _serve(tmp_path, [work]) as client:
        status, job = client.request("POST", "/ops/analyze/batch-gen?wait=120", request)
    assert (status, job["status"]) == (200, "succeeded"), job
    assert "s1" in (work / "b.csv").read_text()


def test_a_trio_saving_beside_its_proband_waits_for_that_folder(
    tmp_path, work, fake_data, slow_coverage
):
    # PR #452 review: the scheduler ignored `proband`, so a trio saving beside
    # its child ran alongside a job writing the same folder.
    bam, ref = fake_data
    with _serve(tmp_path, [work, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        family = {r: str(work / f"{r}.vcf.gz") for r in ("proband", "mother", "father")}
        status, trio = client.request("POST", "/ops/vcf/trio", family)
        assert status == 202, trio
        assert (first["status"], trio["status"]) == ("running", "queued")


def test_a_ref_job_saving_beside_its_fasta_waits_for_that_folder(
    tmp_path, work, fake_data, slow_coverage
):
    # PR #458 review: ref count-ns saves beside its FASTA, yet the server
    # wanted an outdir and didn't serialise the FASTA's folder.
    bam, ref = fake_data
    fasta = work / "ref.fa"
    shutil.copy(ref, fasta)
    with _serve(tmp_path, [work, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, count = client.request("POST", "/ops/ref/count-ns", {"ref": str(fasta)})
        assert status == 202, count
        assert (first["status"], count["status"]) == ("running", "queued")


def test_a_phase_job_saving_beside_its_reads_waits_for_that_folder(
    tmp_path, work, fake_data, slow_coverage
):
    # PR #464 review: vcf phase writes beside its BAM, but the lock took the
    # VCF's folder, so it ran alongside a job writing the BAM's folder.
    bam, ref = fake_data
    reads = work / "reads.bam"
    shutil.copy(bam, reads)
    calls_dir = tmp_path / "calls"
    calls_dir.mkdir()
    calls = calls_dir / "calls.vcf.gz"
    calls.write_bytes(b"")
    with _serve(tmp_path, [work, calls_dir, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, phase = client.request(
            "POST",
            "/ops/vcf/phase",
            {"input": str(reads), "vcf_input": str(calls), "ref": str(ref)},
        )
        assert status == 202, phase
        assert (first["status"], phase["status"]) == ("running", "queued")


@pytest.mark.parametrize(
    ("op", "params"),
    [
        ("ref/gene-map", lambda library: {"ref": str(library), "delete": True}),
        ("ref/verify", lambda library: {"ref": str(library)}),
    ],
    ids=["installer", "library-folder-fasta"],
)
def test_a_library_job_waits_for_a_job_writing_its_genomes(
    tmp_path, work, fake_data, slow_coverage, op, params
):
    # PR #458 review: installers locked the default output folder, not their
    # library, and `ref verify` of a library folder writes .fai/.gzi into its
    # genomes/, so they ran alongside jobs writing those folders.
    bam, ref = fake_data
    library = work / "library"
    (library / "genomes").mkdir(parents=True)
    shutil.copy(ref, library / "genomes" / "ref.fa")
    # The installer deletes, which downloads nothing.
    with _serve(
        tmp_path, [work, FAKE_DIR], max_jobs=2, allow_destructive=True
    ) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(library / "genomes")}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request("POST", f"/ops/{op}", params(library))
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")


def test_operations_and_openapi_follow_the_typed_api(client):
    status, body = client.request("GET", "/ops")
    ops = {op["op"]: op["kind"] for op in body["ops"]}
    assert status == 200
    assert ops["info"] == "query" and ops["vcf/snp"] == "job"
    status, body = client.request("GET", "/ops/vcf/snp")
    assert {"input", "ref", "region", "ploidy"} <= set(body["params"]["properties"])
    assert body["params"]["required"] == ["input"]
    # Settings that name a program to run stay out of reach of requests.
    _, body = client.request("GET", "/ops/lineage/mt-haplogroup")
    assert "haplogrep_path" not in body["params"]["properties"]

    status, document = client.request("GET", "/openapi.json")
    assert status == 200 and document["openapi"].startswith("3.1")
    assert {f"/v1/ops/{op}" for op in ops} <= set(document["paths"])
    run = document["paths"]["/v1/ops/vcf/snp"]["post"]
    schema = run["requestBody"]["content"]["application/json"]["schema"]
    assert document["components"]["schemas"][schema["$ref"].split("/")[-1]][
        "properties"
    ]["region"]


def test_vcf_snp_runs_as_a_job_with_progress_and_a_manifest(client, work, fake_data):
    bam, ref = fake_data
    params = {
        "input": str(bam),
        "ref": str(ref),
        "outdir": str(work),
        "region": "chrM",
        "ploidy": "1",
    }
    status, job = client.request("POST", "/ops/vcf/snp", params)
    JOB_VALIDATOR.validate(job)
    assert status == 202 and job["status"] in ("queued", "running")

    events = client.events(job["id"])
    event_validator = JOB_VALIDATOR.evolve(
        schema={**JOB_VALIDATOR.schema, "$ref": "#/$defs/JobEvent"}
    )
    for event in events:
        event_validator.validate(event)
    statuses = [e["status"] for e in events if e["type"] == "status"]
    assert statuses[0] == "queued" and statuses[-1] == "succeeded"
    assert any(e["type"] == "progress" for e in events)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    status, job = client.request("GET", f"/jobs/{job['id']}")
    JOB_VALIDATOR.validate(job)
    assert (status, job["status"], job["exit_code"]) == (200, "succeeded", 0)
    vcf = work / "snps.vcf.gz"
    assert str(vcf) in [o["path"] for o in job["result"]["outputs"]]
    calls = subprocess.run(
        ["bcftools", "view", "-H", str(vcf)], capture_output=True, text=True
    )
    assert calls.returncode == 0 and calls.stdout.startswith("chrM")
    manifest = json.loads(results.manifest_path_for(vcf).read_text())
    VALIDATOR.validate(manifest)
    assert manifest["status"] == "ok"
    # Compact by default; provenance with full=1.
    assert "provenance" not in job["result"]
    _, full = client.request("GET", f"/jobs/{job['id']}?full=1")
    JOB_VALIDATOR.validate(full)
    assert full["result"]["provenance"]["params"]["region"] == "chrM"


def test_info_query_waits_and_reports_failures_as_failed_jobs(client, work, fake_data):
    bam, _ = fake_data
    status, job = client.request(
        "POST", "/ops/info?wait=120", {"input": str(bam), "outdir": str(work)}
    )
    assert (status, job["status"]) == (200, "succeeded")
    assert job["result"]["metrics"]["sorted"] is True

    status, job = client.request(
        "POST", "/ops/info?wait=120", {"input": str(work / "missing.bam")}
    )
    assert (status, job["status"], job["exit_code"]) == (200, "failed", 1)
    JOB_VALIDATOR.validate(job)
    assert "missing.bam" in job["error"]["message"]


def test_a_manifest_written_into_a_busy_folder_waits(
    tmp_path, work, fake_data, slow_coverage
):
    # #478: sign create --output writes outside its outdir, so the output's
    # folder is locked too.
    bam, ref = fake_data
    busy, outdir = work / "busy", work / "manifests"
    busy.mkdir()
    with _serve(tmp_path, [work, FAKE_DIR], max_jobs=2) as client:
        request = {"input": str(bam), "ref": str(ref), "outdir": str(busy)}
        first = client.request("POST", "/ops/info/calculate-coverage", request)[1]
        status, second = client.request(
            "POST",
            "/ops/sign/create",
            {
                "paths": [str(bam)],
                "output": str(busy / "SHA256SUMS"),
                "outdir": str(outdir),
            },
        )
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")
