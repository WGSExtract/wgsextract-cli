"""Focused server_paths schema regressions."""

import typing

import pytest

from tests.test_server import (
    _ALIGN_REQUESTS,
    _CALLER_OPS,
    _CALLER_PATHS,
    _COMPANIONS,
    _DATA,
    _EVERY_PATH,
    _EXAMPLE_PATHS,
    _METHYLATION_PATHS,
    _PET_ANALYZE_PATHS,
    _PET_ANALYZE_REQUESTS,
    _REF_PATHS,
    _REPAIR_PATHS,
    _SIGN_PATHS,
    _SIGN_REQUESTS,
    _TYPING_PATHS,
    _VCF_PATHS,
    _VEP_PATHS,
    _base_request,
    _path_value,
    _RecordingFlavour,
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
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    params_from_json,
)


@pytest.mark.parametrize(
    ("op", "field"),
    [
        *(("align", f) for f in ("r1", "r2", "input", "ref", "outdir")),
        *(("realign", f) for f in ("input", "r1", "r2", "input_ref", "ref", "outdir")),
    ],
)
@pytest.mark.parametrize(
    "value", ["D:evil.fq", r"D:\evil.fq", r"\\host\share\evil.fq", "evil.fq"]
)
def test_path_guard_covers_every_alignment_path(op, field, value):
    # PR #445: align and realign became server jobs; each file they read or
    # write must be inside an allowed folder, drive-relative names included.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {**_ALIGN_REQUESTS[op], "outdir": r"C:\data"}
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _VCF_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _VCF_PATHS],
)
@pytest.mark.parametrize(
    "value", ["D:evil.vcf", r"D:\evil.vcf", r"\\host\share\evil.vcf", "evil.vcf"]
)
def test_path_guard_covers_every_vcf_path(op, field, value):
    # #346: the VCF-file commands became server operations; each file they
    # read or write must be inside an allowed folder, drive-relative included.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"vcf_input": r"C:\data\in.vcf.gz", "outdir": r"C:\data"}
    guard.check(params_from_json(find(op), allowed))
    item = next(f for f in find(op).fields() if f.name == field)
    bad = [value] if typing.get_origin(item.base_type) is tuple else value
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: bad}))
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _REF_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _REF_PATHS],
)
@pytest.mark.parametrize(
    "value", ["D:evil.fa", r"D:\evil.fa", r"\\host\share\evil.fa", "evil.fa"]
)
def test_path_guard_covers_every_ref_path(op, field, value):
    # #346: the ref commands became server operations; each file or library
    # they read or write must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], allow_destructive=True, pathmod=_RecordingFlavour())
    paths = [item.name for name, item in _REF_PATHS if name == op]
    allowed = {name: rf"C:\data\{name}" for name in paths}
    if op == "ref/library":
        allowed["install"] = "hg38"
    if op == "ref/library-delete":
        allowed["genome"] = "hg38"
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _PET_ANALYZE_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _PET_ANALYZE_PATHS],
)
@pytest.mark.parametrize(
    "value", ["D:evil.fq", r"D:\evil.fq", r"\\host\share\evil.fq", "evil.fq"]
)
def test_path_guard_covers_every_pet_and_analyze_path(op, field, value):
    # #346: pet-align and analyze became server operations; each file they
    # read or write must be inside an allowed folder, drive-relative included.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {**_PET_ANALYZE_REQUESTS[op], "outdir": r"C:\data"}
    if "outdir" not in {f.name for f in find(op).fields()}:
        del allowed["outdir"]
    guard.check(params_from_json(find(op), allowed))
    item = next(f for f in find(op).fields() if f.name == field)
    bad = [value] if typing.get_origin(item.base_type) is tuple else value
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: bad}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_pet_and_analyze_paths_cover_every_file():
    assert {(op, item.name) for op, item in _PET_ANALYZE_PATHS} == {
        ("pet-align", "r1"), ("pet-align", "r2"), ("pet-align", "ref"),
        ("pet-align", "outdir"), ("analyze/comprehensive", "vcf_inputs"),
        ("analyze/comprehensive", "input"), ("analyze/comprehensive", "ref"),
        ("analyze/comprehensive", "outdir"), ("analyze/batch-gen", "directory"),
        ("analyze/batch-gen", "output"),
    }  # fmt: skip


def test_vcf_paths_cover_every_resource():
    # Every annotation data file, the trio's members and outside calls are
    # guarded; PharmCAT's pipeline folder runs code, so requests can't set it.
    guarded = {item.name for _, item in _VCF_PATHS}
    assert {"clinvar_file", "cadd_indel_file", "am_file", "mother", "father",
            "proband", "mask", "pharmcat_json", "outside_call",
            "ann_vcf"} <= guarded  # fmt: skip
    # PR #452 review: <ref>/pharmcat is the pipeline's default folder.
    for field in ("pharmcat_dir", "ref"):
        with pytest.raises(RequestError, match="can't be set by a server request"):
            params_from_json(find("vcf/pharmcat"), {field: r"C:\data\pc"})


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _CALLER_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _CALLER_PATHS],
)
@pytest.mark.parametrize(
    "value", ["D:evil.bed", r"D:\evil.bed", r"\\host\share\evil.bed", r"sub\evil.bed"]
)
def test_path_guard_covers_every_caller_path(op, field, value):
    # #346: the BAM/CRAM variant callers became server operations; each file
    # they read or write (a DeepVariant region BED, a Clair3 model folder
    # included) must be inside an allowed folder, drive-relative included.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _REPAIR_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _REPAIR_PATHS],
)
@pytest.mark.parametrize(
    "value", ["D:evil.bam", r"D:\evil.bam", r"\\host\share\evil.bam", r"sub\evil.bam"]
)
def test_path_guard_covers_every_repair_path(op, field, value):
    # #478: the FTDNA repairs became server operations; the file each reads
    # or writes (an --output path included) must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_repair_paths_cover_every_file():
    assert {(op, item.name) for op, item in _REPAIR_PATHS} == {
        ("repair/ftdna-bam", "input"), ("repair/ftdna-bam", "output"),
        ("repair/ftdna-bam", "ref"), ("repair/ftdna-bam", "outdir"),
        ("repair/ftdna-vcf", "input"), ("repair/ftdna-vcf", "output"),
        ("repair/ftdna-vcf", "outdir"),
    }  # fmt: skip


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _SIGN_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _SIGN_PATHS],
)
@pytest.mark.parametrize(
    "value",
    [
        "D:evil.pub",
        r"D:\evil.pub",
        r"\\host\share\evil.pub",
        r"sub\evil.pub",
        "~/evil.pub",
    ],
)
def test_path_guard_covers_every_sign_path(op, field, value):
    # #478: sign create/verify became server operations; every file they hash,
    # read a key or signature from, or write a manifest to must be inside an
    # allowed folder, and the signing key is never taken from a request.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = _SIGN_REQUESTS[op]
    guard.check(params_from_json(find(op), allowed))
    if field == "paths":
        value = [r"C:\data\a.bam", value]
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_sign_paths_cover_every_file():
    assert {(op, item.name) for op, item in _SIGN_PATHS} == {
        ("sign/create", "paths"), ("sign/create", "output"),
        ("sign/create", "outdir"),
        ("sign/verify", "manifest"), ("sign/verify", "signature"),
        ("sign/verify", "public_key"), ("sign/verify", "allowed_signers"),
    }  # fmt: skip
    # The server never signs with a key a request names.
    with pytest.raises(RequestError) as refused:
        params_from_json(
            find("sign/create"), {**_SIGN_REQUESTS["sign/create"], "key": r"C:\data\id"}
        )
    assert refused.value.code == "usage"


def test_sign_requests_are_checked_before_a_job_is_queued():
    # PR #495 review: threads is an integer as elsewhere; conflicting trust
    # options and an empty path list are usage errors, not a dropped option
    # or a server error from a queued job.
    request = {"manifest": r"C:\data\SHA256SUMS", "threads": 4}
    assert params_from_json(find("sign/verify"), request).threads == 4
    for op, bad in (
        ("sign/verify", {**request, "public_key": "/k.pub", "allowed_signers": "/a"}),
        ("sign/create", {"paths": [], "outdir": r"C:\data"}),
    ):
        with pytest.raises(RequestError) as refused:
            params_from_json(find(op), bad)
        assert refused.value.code == "usage"


@pytest.mark.parametrize(
    "tool", ["D:evil", r"D:\evil", r"\\host\share\evil", r"sub\evil", "~/evil"]
)
def test_path_guard_covers_the_deps_check_tool(tool):
    # PR #495 review: shutil.which() probes a path-shaped tool name, which
    # could reach outside the allowed folders or a network share.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    guard.check(params_from_json(find("deps/check"), {"tool": "samtools"}))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find("deps/check"), {"tool": tool}))
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _EXAMPLE_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _EXAMPLE_PATHS],
)
@pytest.mark.parametrize(
    "value", ["D:evil", r"D:\evil", r"\\host\share\evil", r"sub\evil", "~/evil"]
)
def test_path_guard_covers_every_example_genome_path(op, field, value):
    # #478: example-genome became server operations; the library a download
    # writes into and the Aspera key it reads must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"target_root": r"C:\data"}
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_example_genome_paths_cover_every_file():
    assert {(op, item.name) for op, item in _EXAMPLE_PATHS} == {
        ("example-genome/list", "target_root"),
        ("example-genome/download", "target_root"),
        ("example-genome/download", "aspera_key"),
    }
    assert find("example-genome/list").kind == "query"
    assert find("example-genome/download").kind == "job"


@pytest.mark.parametrize(
    ("op", "field"),
    [(op, item.name) for op, item in _TYPING_PATHS],
    ids=[f"{op}-{item.name}" for op, item in _TYPING_PATHS],
)
@pytest.mark.parametrize(
    "value",
    ["D:evil.fa", r"D:\evil.fa", r"\\host\share\evil.fa", r"sub\evil.fa", "~/evil.fa"],
)
def test_path_guard_covers_every_typing_path(op, field, value):
    # #478: the typing commands became server operations; each file they read
    # (an HLA index or STR catalog included) must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    if op == "typing/target-cnv":
        allowed["bed"] = r"C:\data\targets.bed"
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_typing_paths_cover_every_file():
    assert {(op, item.name) for op, item in _TYPING_PATHS} == {
        *((f"typing/{name}", f) for name in ("cyp2d6", "hla", "smn", "str", "target-cnv")
          for f in ("input", "ref", "outdir")),
        ("typing/hla", "hla_index"), ("typing/str", "catalog"),
        ("typing/target-cnv", "bed"),
    }  # fmt: skip


@pytest.mark.parametrize("field", [item.name for item in _VEP_PATHS])
@pytest.mark.parametrize(
    "value", ["D:evil.vcf", r"D:\evil.vcf", r"\\host\share\evil.vcf", r"sub\evil.vcf"]
)
def test_path_guard_covers_every_vep_path(field, value):
    # #478: `vep run` became a server operation; every file or folder it
    # reads or writes must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"input": r"C:\data\a.vcf.gz", "outdir": r"C:\data"}
    guard.check(params_from_json(find("vep/run"), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find("vep/run"), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_vep_paths_cover_every_file():
    assert {item.name for item in _VEP_PATHS} == {
        "input", "ref", "outdir", "vep_cache", "ploidy_file"
    }  # fmt: skip


def test_caller_paths_cover_every_file():
    guarded = {(op, item.name) for op, item in _CALLER_PATHS}
    assert {("vcf/deepvariant", "checkpoint"), ("vcf/deepvariant", "region"),
            ("vcf/clair3", "model"), ("vcf/sv", "tandem_repeats"),
            *((f"vcf/{name}", f) for name in _CALLER_OPS
              for f in ("input", "ref", "outdir"))} <= guarded  # fmt: skip
    # Region text and a bundled model's name aren't files, so they pass.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    guard.check(
        params_from_json(find("vcf/deepvariant"), {**allowed, "region": "chr20:1-9"})
    )
    guard.check(
        params_from_json(
            find("vcf/clair3"), {**allowed, "model": "r1041_e82_400bps_sup_v500"}
        )
    )


@pytest.mark.parametrize("op", ["vep/download", "vep/verify"])
@pytest.mark.parametrize("field", ["vep_cache", "ref"])
@pytest.mark.parametrize(
    "value", ["D:evil", r"D:\evil", r"\\host\share\evil", r"sub\evil"]
)
def test_path_guard_covers_every_vep_cache_path(op, field, value):
    # #478: `vep download` and `vep verify` became server operations; the
    # cache folder and the library naming it must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"vep_cache": r"C:\data\vep", "ref": r"C:\data\lib"}
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize("op", ["vep/download", "vep/verify"])
def test_vep_cache_paths_cover_every_file(op):
    guarded = {f.name for f in find(op).fields() if f.spec.path or f.spec.may_name_file}
    assert guarded == {"vep_cache", "ref"}


@pytest.mark.parametrize("field", [item.name for item in _METHYLATION_PATHS])
@pytest.mark.parametrize(
    "value", ["D:evil.bam", r"D:\evil.bam", r"\\host\share\evil.bam", r"sub\evil.bam"]
)
def test_path_guard_covers_every_methylation_path(field, value):
    # #478: `methylation pileup` became a server operation; every file or
    # folder it reads or writes must be inside an allowed folder.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    op = find("methylation/pileup")
    guard.check(params_from_json(op, {**allowed, "region": "chr20:1-9,chr21"}))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(op, {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_methylation_paths_cover_every_file():
    assert {item.name for item in _METHYLATION_PATHS} == {"input", "ref", "outdir"}


@pytest.mark.parametrize(("op", "field"), _EVERY_PATH, ids=map("-".join, _EVERY_PATH))
def test_every_path_of_every_operation_must_be_inside_an_allowed_folder(op, field):
    # #351 phase 2: the typed API brought 88 operations to the server with no
    # server code each. Whatever an operation is, a file it names outside the
    # allowed folders is refused before it runs.
    guard = PathGuard([_DATA], allow_destructive=True, pathmod=_RecordingFlavour())
    body = {**_base_request(op), **_COMPANIONS.get((op, field), {})}
    guard.check(params_from_json(find(op), body))
    item = next(f for f in find(op).fields() if f.name == field)
    outside = {**body, field: _path_value(item, r"E:\elsewhere\x.vcf.gz")}
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), outside))
    assert (refused.value.status, refused.value.code) == (403, "path_not_allowed")
