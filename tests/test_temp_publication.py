"""Finished reports replace links; interrupted cross-volume copies publish nothing."""

import errno
import os
import shutil
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools
from tests.test_genotyping_commands import bam, pair
from tests.test_genotyping_commands import cyrius as cyrius
from tests.test_genotyping_commands import hla_setup as hla_setup
from tests.test_genotyping_commands import mhc_bam as mhc_bam
from tests.test_temp_directory import _python_writes
from wgsextract_cli import api
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.temp_directory import move_file
from wgsextract_cli.core.utils import run_command


@needs_tools("samtools")
@pytest.mark.parametrize("command", ["cyp2d6", "hla"])
def test_1093_cross_volume_genotyping_reports_do_not_follow_destination_links(
    tmp_path, monkeypatch, request, command
):
    if command == "hla":
        setup = request.getfixturevalue("hla_setup")
        source = Path(request.getfixturevalue("mhc_bam"))
        name = "NA12878.t1k_genotype.tsv"
    else:
        request.getfixturevalue("cyrius")
        source = Path(
            bam(tmp_path / "in" / "NA12878.bam", pair("p", "chr22", 42_127_000))
        )
        name = "NA12878.cyrius.tsv"
    original = source.read_bytes()
    scratch, output = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    output.mkdir()
    destination = output / name
    try:
        destination.symlink_to(source)
    except OSError as error:
        pytest.skip(f"Symlinks unavailable: {error}")
    monkeypatch.setitem(settings, "temp_directory", str(scratch))
    real_replace = os.replace

    def cross_volume(source_path, target):
        if Path(source_path).is_relative_to(scratch) and Path(target).is_relative_to(
            output
        ):
            raise OSError(errno.EXDEV, "separate temporary volume")
        real_replace(source_path, target)

    monkeypatch.setattr(os, "replace", cross_volume)
    if command == "hla":
        api.genotyping.hla(
            api.TypingHlaParams(
                input=source, hla_index=setup.index, outdir=output, threads=1
            )
        )
    else:
        api.genotyping.cyp2d6(
            api.TypingCyp2d6Params(input=source, outdir=output, threads=1)
        )
    assert source.read_bytes() == original
    assert destination.is_file() and not destination.is_symlink()
    assert destination.read_bytes() != original
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    "failure", [OSError(errno.ENOSPC, "disk full"), KeyboardInterrupt()]
)
def test_1093_failed_cross_volume_copy_leaves_original_output_and_no_partial(
    tmp_path, monkeypatch, failure
):
    source = tmp_path / "finished"
    source.write_bytes(b"finished new output")
    output = tmp_path / "out"
    output.mkdir()
    final = output / "result"
    final.write_bytes(b"previous output")
    real_replace = os.replace

    def cross_volume(source_path, target):
        if Path(source_path).parent != Path(target).parent:
            raise OSError(errno.EXDEV, "separate temporary volume")
        real_replace(source_path, target)

    def interrupted_copy(source_path, target, *, follow_symlinks=True):
        Path(target).write_bytes(b"partial")
        raise failure

    monkeypatch.setattr(os, "replace", cross_volume)
    # Windows copy2 uses CopyFile2 directly, bypassing shutil.copyfile.
    monkeypatch.setattr(shutil, "copy2", interrupted_copy)
    with pytest.raises(type(failure)):
        move_file(source, final)
    assert source.read_bytes() == b"finished new output"
    assert final.read_bytes() == b"previous output"
    assert list(output.iterdir()) == [final]


@needs_tools("samtools")
@pytest.mark.parametrize("configured_temp", [False, True])
@pytest.mark.parametrize("aliased", [False, True], ids=["direct", "symlink"])
def test_1093_unsort_keeps_its_reheader_work_in_the_selected_scratch(
    tmp_path, monkeypatch, configured_temp, aliased
):
    source = Path(bam(tmp_path / "in" / "sample.bam", pair("p", "chr22", 42_127_000)))
    output, scratch = tmp_path / "out", tmp_path / "scratch"
    output.mkdir()
    scratch.mkdir()
    if aliased:
        for folder in (scratch, output):
            actual = folder.with_name(folder.name + "-actual")
            folder.rename(actual)
            try:
                folder.symlink_to(actual, target_is_directory=True)
            except OSError as error:
                pytest.skip(f"Symlinks unavailable: {error}")
    if configured_temp:
        monkeypatch.setitem(settings, "temp_directory", str(scratch))
    selected = scratch if configured_temp else output
    with _python_writes(monkeypatch) as written:
        result = api.bam.unsort(api.BamUnsortParams(input=source, outdir=output))
    header = run_command(
        ["samtools", "view", "-H", result.outputs[0].path], capture_output=True
    ).stdout
    assert "SO:unsorted" in header
    assert any(
        path.name == "newhead.sam" and path.is_relative_to(selected.resolve())
        for path in written
    ), sorted(map(str, written))
    assert not any(p.name.startswith(".wgse") for p in output.iterdir())
    assert list(scratch.iterdir()) == []
