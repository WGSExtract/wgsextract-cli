"""Focused deepvariant gvcf regressions."""

from pathlib import Path

import pytest

from tests.disk_volumes import GIB
from tests.htslib_helpers import (
    vcf_calls,
)
from tests.test_deepvariant import (
    STAGES,
    call,
    call_with_evidence,
    evidence_state,
    rerun,
    visible,
    woman_sample,
)
from tests.test_deepvariant import (
    apple as apple,
)
from tests.test_deepvariant import (
    arm64 as arm64,
)
from tests.test_deepvariant import (
    big_cram as big_cram,
)
from tests.test_deepvariant import (
    dv as dv,
)
from tests.test_deepvariant import (
    genome_fai as genome_fai,
)
from tests.test_deepvariant import (
    linux_native as linux_native,
)
from tests.test_deepvariant import (
    linux_x86 as linux_x86,
)
from tests.test_deepvariant import (
    mac_native as mac_native,
)
from tests.test_deepvariant import (
    rosetta as rosetta,
)
from tests.test_deepvariant import (
    ultima_gpu as ultima_gpu,
)
from wgsextract_cli.commands.vcf import deepvariant
from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.reference_evidence import (
    INDEX_ROLE,
    ROLE,
    State,
)
from wgsextract_cli.core.utils import (
    WGSExtractError,
)


@pytest.mark.parametrize("engine", ["image", "wgse-deepvariant"])
def test_gvcf_tells_confident_reference_from_bases_without_reads_863(
    dv, request, engine
):
    """Issue #863: DeepVariant writes bases without reads as 0/0 blocks with
    GQ 0 and MIN_DP 0. --reference-evidence gvcf publishes its gVCF beside
    the unchanged VCF, and those bases read as uncovered, not reference."""
    if engine == "wgse-deepvariant":
        dv = request.getfixturevalue("linux_native")
    outputs = call_with_evidence(dv)

    assert vcf_calls(dv.out / "deepvariant.vcf.gz") == dv.sample.calls
    gvcf = outputs[ROLE]
    assert Path(gvcf.path).name == "deepvariant.g.vcf.gz"
    assert outputs[INDEX_ROLE].path == gvcf.path + ".tbi"
    assert gvcf.metrics["engine"] == "deepvariant"
    assert gvcf.metrics["quality_model"] == "gq"
    # snp_sample's reads cover 500-2499 at 20x.
    assert evidence_state(gvcf.path, "chr1", 100) is State.UNCOVERED
    assert evidence_state(gvcf.path, "chr1", 1500) is State.CALLABLE_REFERENCE
    assert evidence_state(gvcf.path, "chr1", 1000) is State.VARIANT
    assert not (dv.out / deepvariant.WORK_DIRECTORY).exists()


def test_a_missing_gvcf_is_written_again_by_a_rerun_863(dv):
    """Issue #863: the gVCF and its index are outputs of the final stage. An
    interrupted postprocess_variants reruns alone; a deleted gVCF reruns
    DeepVariant, whose stages' work is gone; nothing missing reruns nothing."""
    dv.deepvariant.fail("postprocess_variants", write=False)
    with pytest.raises(WGSExtractError, match="resume"):
        call(dv, "--reference-evidence", "gvcf")
    assert visible(dv.out) == []
    rerun(dv, "--reference-evidence", "gvcf")
    assert dv.deepvariant.stages() == ["postprocess_variants"]
    gvcf = dv.out / "deepvariant.g.vcf.gz"
    assert evidence_state(gvcf, "chr1", 1500) is State.CALLABLE_REFERENCE

    gvcf.unlink()
    rerun(dv, "--reference-evidence", "gvcf")
    assert dv.deepvariant.stages() == STAGES
    assert evidence_state(gvcf, "chr1", 1500) is State.CALLABLE_REFERENCE

    dv.deepvariant.clear()
    assert ROLE in call_with_evidence(dv)
    assert dv.deepvariant.stages() == []


@pytest.mark.parametrize("engine", ["image", "native-0.4.0"])
def test_a_womans_chry_is_excluded_by_policy_not_uncovered_863(dv, request, engine):
    """Issue #863: ploidy-0 intervals (a woman's chrY) are left out of
    calling, so the gVCF has no records there; its provenance must say they
    were excluded, or they would read as bases nobody could read."""
    if engine == "native-0.4.0":
        dv = request.getfixturevalue("linux_native")
        dv.deepvariant.set_native_version("0.4.0")
    dv.sample = woman_sample(dv)
    gvcf = call_with_evidence(dv)[ROLE]

    assert gvcf.metrics["sex"] == "F"
    assert gvcf.metrics["ploidy_zero"] == "chrY:1-3000"
    assert evidence_state(gvcf.path, "chrY", 1500) is State.EXCLUDED_BY_POLICY
    assert evidence_state(gvcf.path, "chr1", 1500) is State.CALLABLE_REFERENCE


def test_a_rerun_over_more_excluded_bases_rewrites_the_gvcf_provenance_863(dv):
    """#863 review: widening a woman's chrY selection leaves every stage
    command alike (chrY isn't called), but the gVCF's provenance must grow
    with it, or the new chrY bases read as not assessed."""
    dv.sample = woman_sample(dv)
    call_with_evidence(dv, "chr1:1-3000 chrY:1-100")
    gvcf = call_with_evidence(dv, "chr1:1-3000 chrY:1-200")[ROLE]

    assert gvcf.metrics["region"] == "chr1:1-3000,chrY:1-200"
    assert gvcf.metrics["ploidy_zero"] == "chrY:1-200"
    assert evidence_state(gvcf.path, "chrY", 150) is State.EXCLUDED_BY_POLICY


@pytest.mark.parametrize("selection", ["bed", "thousands"])
def test_the_gvcf_records_the_bases_it_assessed_not_the_selection_863(dv, selection):
    """#863 review: a BED --region was recorded by name, so moving or
    editing it changed what a published gVCF assessed, and "1,001" split
    the recorded region apart; either read callable bases as not assessed."""
    if selection == "bed":
        bed = dv.tmp / "target.bed"
        bed.write_text("chr1\t1000\t2000\n", encoding="utf-8")
        region = str(bed)
    else:
        region = "chr1:1,001-2,000"
    gvcf = call_with_evidence(dv, region)[ROLE]
    if selection == "bed":
        bed.unlink()

    assert gvcf.metrics["region"] == "chr1:1001-2000"
    assert evidence_state(gvcf.path, "chr1", 1500) is State.CALLABLE_REFERENCE
    assert evidence_state(gvcf.path, "chr1", 2500) is State.NOT_ASSESSED


def test_turning_on_the_gvcf_in_a_finished_outdir_keeps_the_ram_check_863(
    dv, monkeypatch, ram
):
    """#863 review: --reference-evidence gvcf reruns every stage of a
    finished VCF-only run, but its journal records made the RAM shortfall a
    warning; with nothing kept, the run is refused as a fresh one is."""
    call(dv)
    ram(4 * GIB)
    monkeypatch.setattr(alignment_memory, "available_memory", lambda: 4 * GIB)
    dv.deepvariant.clear()
    with pytest.raises(WGSExtractError, match=r"this computer has 4\.0 GiB"):
        call(dv, "--reference-evidence", "gvcf")
    assert dv.deepvariant.stages() == []
