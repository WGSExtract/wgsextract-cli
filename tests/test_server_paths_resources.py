"""Focused server_paths resources regressions."""

import json

import pytest

from tests.test_server import (
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
from wgsextract_cli.api._params import with_config_defaults
from wgsextract_cli.core.config import settings
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    params_from_json,
)


def test_1087_inferred_output_symlink_cannot_escape_allowed_input_folder(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    try:
        (allowed / "wgse").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    params = params_from_json(
        find("vcf/table"), {"vcf_input": str(allowed / "sample.vcf")}
    )
    with pytest.raises(RequestError) as error:
        PathGuard([allowed]).check(params)
    assert error.value.code == "path_not_allowed"


def test_1087_zip_request_is_refused_before_any_file_open(tmp_path, monkeypatch):
    guard = PathGuard([tmp_path])
    params = params_from_json(find("info/chip"), {"input": "//host/share/sample.zip"})

    def forbidden_open(*args, **kwargs):
        raise AssertionError("an unvalidated request path was opened")

    monkeypatch.setattr("io.open", forbidden_open)
    with pytest.raises(RequestError) as error:
        guard.check(params)
    assert error.value.code == "path_not_allowed"


def test_1087_host_only_defaults_are_not_subject_to_request_path_restrictions(
    tmp_path, monkeypatch
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    host_tool = tmp_path / "host-tools" / "pharmcat"
    monkeypatch.setitem(settings, "pharmcat_directory", str(host_tool))
    monkeypatch.delitem(settings, "output_directory", raising=False)
    op = find("vcf/pharmcat")

    checked = PathGuard([allowed]).check(
        params_from_json(op, {"vcf_input": str(allowed / "sample.vcf")})
    )

    assert checked.outdir == allowed / "wgse"
    assert with_config_defaults(checked).pharmcat_dir == host_tool
    assert "pharmcat_dir" not in op.request_values(checked)


def test_1087_server_output_follows_the_vcf_override(tmp_path, monkeypatch):
    monkeypatch.delitem(settings, "output_directory", raising=False)
    checked = PathGuard([tmp_path]).check(
        params_from_json(
            find("vcf/table"),
            {
                "input": str(tmp_path / "old" / "sample.vcf"),
                "vcf_input": str(tmp_path / "current" / "sample.vcf"),
            },
        )
    )
    assert checked.outdir == tmp_path / "current" / "wgse"


def test_1087_trio_proband_overrides_unused_inputs_for_output_inference(
    tmp_path, monkeypatch
):
    monkeypatch.delitem(settings, "output_directory", raising=False)
    checked = PathGuard([tmp_path]).check(
        params_from_json(
            find("vcf/trio"),
            {
                "input": str(tmp_path / "old" / "unused.bam"),
                "vcf_input": str(tmp_path / "old-vcf" / "unused.vcf"),
                "proband": str(tmp_path / "current" / "child.vcf"),
            },
        )
    )
    assert checked.outdir == tmp_path / "current" / "wgse"


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
        (library / "downloads").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")
    with pytest.raises(RequestError) as refused:
        PathGuard([library]).check(params_from_json(find("typing/hla"), request))
    assert refused.value.code == "path_not_allowed"
