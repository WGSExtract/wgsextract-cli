"""Focused server_paths regions regressions."""

import pytest

from tests.server_helpers import client as client
from tests.server_helpers import work as work
from tests.server_path_cases import _REGION_LISTS, _RecordingFlavour
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    params_from_json,
)


@pytest.mark.parametrize(
    "op", ["vcf/freebayes", "vcf/clair3", "vcf/gatk", "vcf/sv", "vcf/cnv", "vcf/call"]
)
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


def test_path_guard_refuses_file_operands_in_expressions():
    # PR #452 review: bcftools reads `@FILE` in an expression, past the guard.
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    # Keep this expression test independent of the host's output-path flavour.
    allowed = {
        "vcf_input": r"C:\data\in.vcf.gz",
        "outdir": r"C:\data\wgse",
        "expr": "QUAL>30",
    }
    guard.check(params_from_json(find("vcf/filter"), allowed))
    for expr in ("ID=@/outside/ids.txt", r"ID!=@\\host\share\ids"):
        with pytest.raises(RequestError, match="@FILE") as refused:
            guard.check(params_from_json(find("vcf/filter"), {**allowed, "expr": expr}))
        assert refused.value.code == "path_not_allowed"


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


@pytest.mark.parametrize("op", ["vcf/sv", "vcf/cnv", "vcf/call"])
def test_1250_windows_path_guard_accepts_structural_region_lists(op):
    guard = PathGuard([r"C:\data"], pathmod=_RecordingFlavour())
    body = {"input": r"C:\data\a.bam", "outdir": r"C:\data"}
    guard.check(params_from_json(find(op), {**body, "region": "1:1-1,000, X:-300"}))


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
