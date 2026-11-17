"""Focused server paths regressions."""

import json
import os
import typing
from pathlib import Path

import pytest

from tests.test_server import (
    _ALIGN_REQUESTS,
    _CALLER_OPS,
    _CALLER_PATHS,
    _COMPANIONS,
    _DATA,
    _EVERY_PATH,
    _EXAMPLE_PATHS,
    _IN,
    _METHYLATION_PATHS,
    _PET_ANALYZE_PATHS,
    _PET_ANALYZE_REQUESTS,
    _REF_PATHS,
    _REGION_LISTS,
    _REPAIR_PATHS,
    _SIGN_PATHS,
    _SIGN_REQUESTS,
    _TYPING_PATHS,
    _VCF_PATHS,
    _VEP_PATHS,
    _base_request,
    _path_value,
    _RecordingFlavour,
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
from wgsextract_cli.api._params import json_schema
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    operations,
    params_from_json,
)


@pytest.mark.parametrize(
    ("op", "params", "status", "code"),
    [
        ("info", {"input": "{outside}/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "{work}/../outside/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "{work}/escape/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "a.bam"}, 400, "usage"),
        # #431: realign reads URLs on the command line; a server would fetch
        # whatever a request names.
        (
            "realign",
            {"input": "http://127.0.0.1:9/x.cram", "outdir": "{work}"},
            400,
            "usage",
        ),
        # Security review of PR #422: Windows opens UNC paths over SMB.
        ("info", {"input": "//attacker/share/a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "\\\\attacker\\share\\a.bam"}, 403, "path_not_allowed"),
        ("info", {"input": "{work}/a.bam", "bogus": 1}, 400, "usage"),
        ("vcf/snp", {"input": "{work}/a.bam", "threads": "four"}, 400, "usage"),
        (
            "lineage/mt-haplogroup",
            {"input": "{work}/a.bam", "haplogrep_path": "/bin/sh"},
            400,
            "usage",
        ),
        # PR #426 review: a region may name a BED file the guard didn't see.
        (
            "extract/custom",
            {"input": "{work}/a.bam", "region": "{outside}/t.bed"},
            403,
            "path_not_allowed",
        ),
        (
            "extract/bam-subset",
            {"input": "{work}/a.bam", "fraction": 0.5, "region": "beds/t.bed"},
            400,
            "usage",
        ),
        # PR #442 review: with no input or outdir it saved into the server's
        # working folder.
        ("qc/fake-data", {}, 400, "usage"),
        # #346: comprehensive saves into the working folder without outdir.
        ("analyze/comprehensive", {"input": "{work}/a.bam"}, 400, "usage"),
        (
            "analyze/comprehensive",
            {"batch": "{work}/b.csv", "outdir": "{work}"},
            400,
            "usage",
        ),
        ("analyze/batch-gen", {"directory": "{work}"}, 400, "usage"),
        # PR #463 review: "" became None, which the path guard skips.
        ("analyze/batch-gen", {"directory": "{work}", "output": ""}, 400, "usage"),
        (
            "pet-align",
            {"r1": "{outside}/r1.fq", "species": "dog", "outdir": "{work}"},
            403,
            "path_not_allowed",
        ),
        # #351 security review: an unknown format name became part of the
        # template and output paths, so "../" read and wrote outside them.
        (
            "microarray",
            {"input": "{work}/a.bam", "outdir": "{work}", "formats": "../../x"},
            400,
            "usage",
        ),
        # PR #631 review: --svd-prefix names panel files the guard must see.
        (
            "qc/contamination",
            {"input": "{work}/a.bam", "svd_prefix": "{outside}/panel"},
            403,
            "path_not_allowed",
        ),
        (
            "qc/contamination",
            {"input": "{work}/a.bam", "svd_prefix": "{work}/escape/panel"},
            403,
            "path_not_allowed",
        ),
        ("nope", {}, 404, "unknown_op"),
        # PR #422 review: these were 500 "internal" errors, or jobs with
        # values the schema forbids.
        ("vcf/snp", {}, 400, "usage"),
        (
            "lineage/y-haplogroup",
            {"input": "{work}/a.bam", "min_reads": None},
            400,
            "usage",
        ),
    ],
)
def test_requests_are_validated_and_confined_to_allowed_folders(
    client, tmp_path, work, op, params, status, code
):
    outside = tmp_path / "outside"
    outside.mkdir()
    if hasattr(os, "symlink"):
        try:
            (work / "escape").symlink_to(outside, target_is_directory=True)
        except OSError:
            pass
    if "escape" in str(params) and not (work / "escape").is_symlink():
        pytest.skip("can't create symlinks here")
    body = {
        k: v.format(work=work, outside=outside) if isinstance(v, str) else v
        for k, v in params.items()
    }
    got, response = client.request("POST", f"/ops/{op}", body)
    assert (got, response["error"]["code"]) == (status, code)
    assert client.request("GET", "/jobs")[1]["jobs"] == []


@pytest.mark.parametrize(
    "path",
    [
        r"\\attacker\share\a.bam",
        "//attacker/share/a.bam",
        r"\/attacker/share/a.bam",
        r"\\?\UNC\attacker\share\a.bam",
        r"\\?\C:\data\a.bam",
        r"\\.\pipe\a.bam",
        r"\??\UNC\attacker\share\a.bam",
        r"C:\data\..\other\a.bam",
    ],
)
def test_path_guard_refuses_network_paths_without_opening_them(path):
    # Security review of PR #422: on Windows, checking `\\attacker\share\a.bam`
    # resolved it first, which connects to the host and sends the user's NTLM
    # credentials, before the 403.
    flavour = _RecordingFlavour()
    guard = PathGuard([r"C:\data"], pathmod=flavour)
    assert flavour.opened == [r"C:\data"]
    with pytest.raises(RequestError) as refused:
        guard.check_path("input", path)
    assert (refused.value.status, refused.value.code) == (403, "path_not_allowed")
    assert flavour.opened == [r"C:\data"]
    guard.check_path("input", "c:/DATA/sub/a.bam")
    assert flavour.opened[-1] == "c:/DATA/sub/a.bam"


@pytest.mark.parametrize(
    ("op", "body"),
    [
        ("bam/merge", {"inputs": [_IN, _IN], "output": "D:evil.bam"}),
        ("bam/unalign", {"input": _IN, "r1": "D:evil.fq", "r2": "b.fq"}),
        ("bam/unalign", {"input": _IN, "r1": "a.fq", "r2": "D:evil.fq"}),
        ("bam/unalign", {"input": _IN, "r1": "a.fq", "r2": "b.fq", "se": "D:e.fq"}),
    ],
    ids=["merge-output", "unalign-r1", "unalign-r2", "unalign-se"],
)
def test_path_guard_refuses_drive_relative_output_names(op, body):
    # Review of PR #442: on Windows `D:evil.bam` names a file in drive D's
    # working folder, outside every allowed root, yet passed as a bare name.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    request = params_from_json(find(op), {**body, "outdir": r"C:\data"})
    with pytest.raises(RequestError) as refused:
        guard.check(request)
    assert (refused.value.status, refused.value.code) == (400, "usage")
    assert "absolute path" in str(refused.value)
    # The same names without a drive are bare file names, saved in outdir.
    bare = {
        k: v.removeprefix("D:") if isinstance(v, str) else v for k, v in body.items()
    }
    guard.check(params_from_json(find(op), {**bare, "outdir": r"C:\data"}))


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


def test_server_offers_no_ref_command_that_fetches_a_url_or_saves_config():
    # #346: ref download fetches any URL and ref bootstrap saves config.toml.
    ref_ops = {name for name in operations() if name.startswith("ref/")}
    assert {"ref/index", "ref/status", "ref/clinvar"} <= ref_ops
    assert not ref_ops & {"ref/download", "ref/bootstrap"}


def test_server_does_not_offer_benchmark():
    # #478: benchmark downloads any --dataset-url, so it stays local only.
    assert "benchmark" not in operations()


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
    "op", ["ref/gene-map", "ref/clinvar", "ref/status", "ref/library-list"]
)
def test_path_guard_checks_the_library_above_a_genomes_fasta(tmp_path, op):
    # PR #458 review: a FASTA in <library>/genomes names that library, which
    # these commands write into or read, so the library must be allowed too.
    genomes = tmp_path / "library" / "genomes"
    genomes.mkdir(parents=True)
    fasta = genomes / "hg38.fa"
    fasta.write_text(">chr1\nA\n")
    params = params_from_json(find(op), {"ref": str(fasta)})
    with pytest.raises(RequestError) as refused:
        PathGuard([genomes]).check(params)
    assert refused.value.code == "path_not_allowed"
    # status also reads the dataset library beside it.
    if op == "ref/status":
        with pytest.raises(RequestError, match="outside the folders"):
            PathGuard([tmp_path / "library"]).check(params)
    PathGuard([tmp_path / "library", tmp_path / "datasets"]).check(params)


@pytest.mark.parametrize("op", ["ref/clinvar", "ref/status", "ref/library-list"])
def test_path_guard_checks_the_library_of_a_home_relative_fasta(
    tmp_path, monkeypatch, op
):
    # PR #458 review: ~/lib/genomes/x.fa wasn't expanded before the library
    # check, so a server allowing only ~/lib/genomes wrote into ~/lib.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    genomes = tmp_path / "lib" / "genomes"
    genomes.mkdir(parents=True)
    (genomes / "x.fa").write_text(">chr1\nA\n")
    with _serve(tmp_path, [genomes]) as client:
        status, body = client.request(
            "POST", f"/ops/{op}", {"ref": "~/lib/genomes/x.fa"}
        )
    assert (status, body["error"]["code"]) == (403, "path_not_allowed")
    # A Python caller's Params are checked the same way.
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([genomes]).check(find(op).params(ref="~/lib/genomes/x.fa"))


@pytest.mark.parametrize("op", ["vcf/freebayes", "vcf/clair3", "vcf/gatk"])
@pytest.mark.parametrize("filename", ["outside.bed", "outside with spaces, commas.bed"])
def test_caller_region_beds_cannot_escape_serve_allow(tmp_path, op, filename):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / filename
    outside.write_text("chr20\t0\t100\n")
    with pytest.raises(RequestError) as refused:
        PathGuard([allowed]).check(
            params_from_json(
                find(op), {"input": str(allowed / "s.bam"), "region": str(outside)}
            )
        )
    assert refused.value.code == "path_not_allowed"
    inside = allowed / filename
    inside.write_text("chr20\t0\t100\n")
    PathGuard([allowed]).check(
        params_from_json(
            find(op), {"input": str(allowed / "s.bam"), "region": str(inside)}
        )
    )


@pytest.mark.parametrize("op", ["vcf/impute", "ref/imputation"])
def test_732_imputation_checks_the_library_above_an_allowed_fasta(tmp_path, op):
    genomes = tmp_path / "library" / "genomes"
    genomes.mkdir(parents=True)
    fasta = genomes / "hg38.fa"
    fasta.write_text(">chr20\nA\n")
    request = {"ref": str(fasta), "chromosomes": "chr20"}
    if op == "vcf/impute":
        request["vcf_input"] = str(genomes / "chip.vcf.gz")
    params = params_from_json(find(op), request)
    with pytest.raises(RequestError) as error:
        PathGuard([genomes]).check(params)
    assert error.value.code == "path_not_allowed"
    PathGuard([tmp_path / "library"]).check(params)


@pytest.mark.parametrize(
    ("op", "delete"),
    [("vcf/impute", False), ("ref/imputation", False), ("ref/imputation", True)],
)
@pytest.mark.parametrize("configured", ["reference_library", "reference_fasta"])
def test_732_omitted_imputation_ref_cannot_escape_allowed_roots(
    tmp_path, monkeypatch, op, delete, configured
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    genomes = outside / "genomes"
    genomes.mkdir(parents=True)
    fasta = genomes / "hg38.fa"
    fasta.write_text(">chr20\nA\n")
    monkeypatch.delitem(settings, "reference_fasta", raising=False)
    monkeypatch.setitem(settings, "reference_library", str(outside))
    if configured == "reference_fasta":
        monkeypatch.setitem(settings, "reference_library", str(allowed))
        monkeypatch.setitem(settings, "reference_fasta", str(fasta))
    request = {"chromosomes": "chr20"}
    if op == "vcf/impute":
        request["vcf_input"] = str(allowed / "chip.vcf.gz")
    else:
        request["delete"] = delete
    with pytest.raises(RequestError) as error:
        PathGuard([allowed], allow_destructive=True).check(
            params_from_json(find(op), request)
        )
    assert error.value.code == "path_not_allowed"


@pytest.mark.parametrize("op", ["vcf/impute", "ref/imputation"])
@pytest.mark.parametrize("escape", ["panel-folder", "binary", "marker-path"])
def test_732_imputation_rejects_escaping_panel_paths(tmp_path, op, escape):
    from wgsextract_cli.core import imputation_panel

    library, outside = tmp_path / "library", tmp_path / "outside"
    library.mkdir()
    outside.mkdir()
    panel = imputation_panel.folder(str(library))
    if escape == "panel-folder":
        panel.parent.mkdir(parents=True)
        try:
            panel.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are unavailable")
    else:
        chrom = panel / "chr20"
        (chrom / "bins").mkdir(parents=True)
        if escape == "binary":
            target = outside / "ref.bin"
            target.write_bytes(b"reference")
            try:
                (chrom / "bins" / "ref.bin").symlink_to(target)
            except OSError:
                pytest.skip("symlinks are unavailable")
        else:
            (chrom / ".installed.json").write_text(
                json.dumps({"chunks": [["input", "output", str(outside / "ref.bin")]]})
            )
    request = {"ref": str(library), "chromosomes": "chr20"}
    if op == "vcf/impute":
        request["vcf_input"] = str(library / "chip.vcf.gz")
    with pytest.raises(RequestError) as error:
        PathGuard([library]).check(params_from_json(find(op), request))
    assert error.value.code == "path_not_allowed"


@pytest.mark.parametrize(
    ("op", "params"),
    [
        ("bam/merge", {"inputs": ["{home}/a.bam"] * 2, "output": "~/m.bam"}),
        ("bam/unalign", {"input": "{home}/a.bam", "r1": "~/r1.fq", "r2": "r2.fq"}),
        ("extract/custom", {"input": "{home}/a.bam", "region": "~/t.bed"}),
        ("vcf/deepvariant", {"input": "{home}/a.bam", "region": "chr1 ~/t.bed"}),
    ],
)
def test_path_guard_refuses_home_relative_file_names_in_text_fields(
    tmp_path, monkeypatch, op, params
):
    # PR #458 re-review: the guard approved ~/m.bam as the home folder's, but
    # the command used it literally: a `~` folder in the working folder.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    request = {
        k: [v.format(home=tmp_path) for v in value]
        if isinstance(value, list)
        else value.format(home=tmp_path)
        for k, value in params.items()
    }
    with pytest.raises(RequestError, match="not ~") as refused:
        PathGuard([tmp_path]).check(params_from_json(find(op), request))
    assert refused.value.code == "usage"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("reference_library", "outside"),
        ("reference_fasta", "outside/genomes/hg38.fa"),
    ],
)
def test_a_library_list_request_checks_the_library_it_falls_back_to(
    tmp_path, work, monkeypatch, key, value
):
    # #478: with no ref, the configured FASTA's library (else the configured
    # library) is the one read, so it must be an allowed folder too.
    from wgsextract_cli.core.config import settings

    fasta = tmp_path / "outside" / "genomes" / "hg38.fa"
    fasta.parent.mkdir(parents=True)
    fasta.write_text(">chr1\nA\n")
    # The FASTA's library comes first, even when reference_library is allowed.
    monkeypatch.setitem(settings, "reference_library", str(work))
    monkeypatch.setitem(settings, key, str(tmp_path / value))
    with _serve(tmp_path, [work]) as client:
        status, body = client.request("POST", "/ops/ref/library-list", {})
        assert (status, body["error"]["code"]) == (403, "path_not_allowed"), body
        status, body = client.request(
            "POST", "/ops/ref/library-list", {"ref": str(work)}
        )
        assert status == 202, body


@pytest.mark.parametrize("op", ["ref/clinvar", "ref/status", "ref/library-list"])
def test_path_guard_checks_the_library_of_a_fasta_named_through_dotdot(tmp_path, op):
    # PR #499 review: genomes/absent/../x.fa isn't a file as written, so the
    # guard checked it as a folder inside genomes, while the command
    # collapsed the `..` and read the library above.
    genomes = tmp_path / "lib" / "genomes"
    genomes.mkdir(parents=True)
    (genomes / "x.fa").write_text(">chr1\nA\n")
    params = params_from_json(find(op), {"ref": f"{genomes}/absent/../x.fa"})
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([genomes]).check(params)


def test_a_library_list_request_checks_a_configured_fasta_link(
    tmp_path, work, monkeypatch
):
    # PR #499 review: with no ref, the configured FASTA is read too; a link
    # in an allowed library to a FASTA elsewhere must be refused.
    from wgsextract_cli.core.config import settings

    outside = tmp_path / "outside.fa"
    outside.write_text(">chr1\nA\n")
    (work / "genomes").mkdir()
    link = work / "genomes" / "hg38.fa"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links need privileges here")
    monkeypatch.setitem(settings, "reference_fasta", str(link))
    with pytest.raises(RequestError, match="outside the folders"):
        PathGuard([work]).check(find("ref/library-list").params())
    PathGuard([work, outside]).check(find("ref/library-list").params())


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


def test_path_guard_refuses_file_operands_in_expressions():
    # PR #452 review: bcftools reads `@FILE` in an expression, past the guard.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    allowed = {"vcf_input": r"C:\data\in.vcf.gz", "expr": "QUAL>30"}
    guard.check(params_from_json(find("vcf/filter"), allowed))
    for expr in ("ID=@/outside/ids.txt", r"ID!=@\\host\share\ids"):
        with pytest.raises(RequestError, match="@FILE") as refused:
            guard.check(params_from_json(find("vcf/filter"), {**allowed, "expr": expr}))
        assert refused.value.code == "path_not_allowed"


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
    guard.check(params_from_json(find(op), allowed))
    with pytest.raises(RequestError) as refused:
        guard.check(params_from_json(find(op), {**allowed, field: value}))
    assert refused.value.code in ("usage", "path_not_allowed")


def test_typing_paths_cover_every_file():
    assert {(op, item.name) for op, item in _TYPING_PATHS} == {
        *((f"typing/{name}", f) for name in ("cyp2d6", "hla", "smn", "str")
          for f in ("input", "ref", "outdir")),
        ("typing/hla", "hla_index"), ("typing/str", "catalog"),
    }  # fmt: skip


def test_path_guard_checks_the_library_hla_typing_builds_its_index_in(tmp_path):
    # #478: without --hla-index, `typing hla` builds its T1K index in the
    # reference library above a <library>/genomes FASTA, so that library must
    # be allowed; an explicit index needs no library.
    genomes = tmp_path / "library" / "genomes"
    genomes.mkdir(parents=True)
    fasta = genomes / "hg38.fa"
    fasta.write_text(">chr1\nA\n")
    request = {"input": str(genomes / "a.bam"), "ref": str(fasta)}
    with pytest.raises(RequestError) as refused:
        PathGuard([genomes]).check(params_from_json(find("typing/hla"), request))
    assert refused.value.code == "path_not_allowed"
    PathGuard([genomes]).check(
        params_from_json(
            find("typing/hla"), {**request, "hla_index": str(genomes / "i.fa")}
        )
    )
    PathGuard([tmp_path / "library"]).check(
        params_from_json(find("typing/hla"), request)
    )


def test_path_guard_checks_where_hla_typing_would_build_its_index(
    tmp_path, monkeypatch
):
    # PR #492 review: the guard checked the library, not the index folder
    # hla_index() picks, so a symlinked <library>/ref sent the build outside;
    # and a ref filled from config was not checked at all.
    library = tmp_path / "library"
    (library / "genomes").mkdir(parents=True)
    fasta = library / "genomes" / "hg38.fa"
    fasta.write_text(">chr1\nA\n")
    request = {"input": str(library / "a.bam"), "ref": str(fasta)}
    PathGuard([library]).check(params_from_json(find("typing/hla"), request))

    monkeypatch.setitem(settings, "reference_fasta", str(fasta))
    without_ref = {"input": str(library / "a.bam")}
    with pytest.raises(RequestError) as refused:
        PathGuard([library / "genomes"]).check(
            params_from_json(find("typing/hla"), without_ref)
        )
    assert refused.value.code == "path_not_allowed"

    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (library / "ref").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")
    with pytest.raises(RequestError) as refused:
        PathGuard([library]).check(params_from_json(find("typing/hla"), request))
    assert refused.value.code == "path_not_allowed"


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


@pytest.mark.parametrize(("op", "extra", "sep"), _REGION_LISTS)
def test_path_guard_checks_every_file_in_a_region_list(
    op, extra, sep, tmp_path, monkeypatch
):
    # Review of PR #454: DeepVariant reads every whitespace-separated BED file
    # in its region, and Parabricks every comma-separated one, but the guard
    # checked the region as one path: an allowed BED hid an outside file, and
    # a bare name after a region read the server's working folder.
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    for folder in (allowed, outside):
        folder.mkdir()
        (folder / "a.bed").write_text("chr1\t0\t9\n")
    monkeypatch.chdir(outside)
    guard = PathGuard([allowed])
    body = {"input": str(allowed / "a.bam"), "outdir": str(allowed), **extra}
    inside = str(allowed / "a.bed")
    for region in (f"{inside}{sep}chr1", f"chr1:1-9{sep}chr2"):
        guard.check(params_from_json(find(op), {**body, "region": region}))
    for region, code in (
        (f"{inside}{sep}{outside / 'a.bed'}", "path_not_allowed"),
        (f"chr1{sep}a.bed", "usage"),
    ):
        with pytest.raises(RequestError) as refused:
            guard.check(params_from_json(find(op), {**body, "region": region}))
        assert refused.value.code == code, region


@pytest.mark.parametrize(
    ("extra", "name"), [({}, "panel,2026"), ({"gpu": True}, "panel files")]
)
def test_path_guard_splits_a_region_list_as_its_backend_does(extra, name, tmp_path):
    # Review of PR #454: a folder name with the other backend's delimiter is
    # one allowed path, not a refused fragment.
    bed = tmp_path / name / "targets.bed"
    bed.parent.mkdir()
    bed.write_text("chr1\t0\t9\n")
    body = {"input": str(tmp_path / "a.bam"), "outdir": str(tmp_path), **extra}
    PathGuard([tmp_path]).check(
        params_from_json(find("vcf/deepvariant"), {**body, "region": str(bed)})
    )


@pytest.mark.parametrize(
    ("extra", "region"),
    [
        ({}, r"C:\data\a.bed D:\evil.bed"),
        ({"gpu": True}, r"C:\data\a.bed,D:evil.bed"),
        ({}, "chr1 D:evil.bed"),
        ({}, "D:evil.bed"),
        ({}, r"chr1 \\host\share\evil.bed"),
        ({}, r"C:\data\a.bed sub\evil.bed"),
    ],
)
def test_path_guard_checks_every_windows_file_in_a_region_list(extra, region):
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    body = {"input": r"C:\data\a.bam", "outdir": r"C:\data", **extra}
    with pytest.raises(RequestError) as refused:
        guard.check(
            params_from_json(find("vcf/deepvariant"), {**body, "region": region})
        )
    assert refused.value.code in ("usage", "path_not_allowed")


@pytest.mark.parametrize(
    "region", [r"C:\data\a.bed chr1", "X:1-100", "1:1-100 Y:5,000-", "X"]
)
def test_path_guard_reads_a_windows_interval_as_a_region(region):
    # Review of PR #454: on Windows "X:1-100" has a drive, so it was refused
    # as a drive-relative path.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    body = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    guard.check(params_from_json(find("vcf/deepvariant"), {**body, "region": region}))


@pytest.mark.parametrize("depth", [-1, float("nan")])
def test_a_negative_or_nan_depth_is_refused(depth):
    # Review of PR #454: --qc-min-depth refused them, the API didn't.
    with pytest.raises(UsageError, match="at least 0"):
        find("vcf/deepvariant").params(input=Path("a.bam"), qc_min_depth=depth)
    if depth == -1:
        with pytest.raises(RequestError):
            params_from_json(
                find("vcf/deepvariant"), {"input": "/a.bam", "qc_min_depth": depth}
            )
    schema = json_schema(find("vcf/deepvariant").params)
    assert schema["properties"]["qc_min_depth"]["minimum"] == 0


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


@pytest.mark.parametrize(
    "region", ["chr1,-U/x/leak.bam", "-o/x/leak.bam", "chr1 --output=/x/leak.bam"]
)
def test_a_region_item_that_looks_like_an_option_is_refused(client, work, region):
    # #351 phase 2 security review: vcf phase --haplotag passed each region
    # item to `samtools view`, so `-U/any/file` wrote outside the allowed
    # folders, on the CLI too.
    with pytest.raises(UsageError, match="isn't a region"):
        find("vcf/phase").params(input=work / "a.bam", region=region, haplotag=True)
    body = {"input": str(work / "a.bam"), "region": region, "haplotag": True}
    status, response = client.request("POST", "/ops/vcf/phase", body)
    assert (status, response["error"]["code"]) == (400, "usage")
    assert client.request("GET", "/jobs")[1]["jobs"] == []


@pytest.mark.parametrize(
    ("gpu", "accepted", "refused"),
    [
        (False, "/d/panel,-v2/t.bed", "/d/panel -old/t.bed"),
        (True, "/d/panel -old/t.bed", "/d/panel,-v2/t.bed"),
    ],
)
def test_region_items_are_split_as_the_backend_splits_them(
    work, gpu, accepted, refused
):
    # Review of #486: every region was split on commas and spaces, refusing
    # BED paths DeepVariant (spaces) or Parabricks (commas) reads as one item.
    op = find("vcf/deepvariant")
    assert op.params(input=work / "a.bam", region=accepted, gpu=gpu).region == accepted
    with pytest.raises(UsageError, match="isn't a region"):
        op.params(input=work / "a.bam", region=refused, gpu=gpu)
