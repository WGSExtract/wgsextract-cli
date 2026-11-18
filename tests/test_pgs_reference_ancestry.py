"""Ancestry calibration must not fill gVCF coverage gaps as reference."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.ancestry_helpers import calls_vcf
from tests.htslib_helpers import needs_htslib
from tests.test_pgs import _calibrated, _calibrated_world
from wgsextract_cli.core.results import fingerprint


@needs_htslib
@pytest.mark.parametrize("header_lengths", [True, False])
@pytest.mark.parametrize("direct_gvcf", [True, False])
def test_863_ancestry_projection_takes_only_callable_reference_from_the_gvcf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    header_lengths: bool,
    direct_gvcf: bool,
) -> None:
    """Issue #863: missing ancestry sites became hom-ref even where the
    caller's gVCF says no read covered them."""
    world = _calibrated_world(tmp_path / "genome", monkeypatch, whole_genome=True)
    calls = tmp_path / "calls"
    calls.mkdir()
    world["vcf"] = calls_vcf(world["world"], calls / "v.vcf.gz", variants_only=True)
    genotypes = world["world"]["genotypes"]
    half = len(genotypes) // 2
    uncovered = {i for i, g in enumerate(genotypes[:half]) if g == 0}
    uncovered = set(sorted(uncovered)[-4:])
    # Reference evidence covers chr1-chr11; chr12-chr22's absent sites have
    # no reads, not a hom-ref genotype.
    lines = [
        "##fileformat=VCFv4.2",
        *(
            f"##contig=<ID=chr{c}{',length=40000' if header_lengths else ''}>"
            for c in range(1, 23)
        ),
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End">',
        '##ALT=<ID=*,Description="Any other allele">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
        '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">',
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS",
    ]
    for i, ((c, pos, ref, alt), g) in enumerate(
        zip(world["world"]["sites"], genotypes, strict=True)
    ):
        if g:
            gt = ("0/1", "1/1")[g - 1]
            lines.append(f"chr{c}\t{pos}\t.\t{ref}\t{alt},<*>\t50\tPASS\t.\tGT:DP:GQ\t{gt}:30:50")  # fmt: skip
        elif i in uncovered:
            lines.append(f"chr{c}\t{pos}\t.\t{ref}\t{alt},<*>\t.\tPASS\t.\tGT:DP:GQ\t0/0:0:0")  # fmt: skip
        elif i < half:
            lines.append(f"chr{c}\t{pos}\t.\t{ref}\t<*>\t.\tPASS\tEND={pos}\tGT:DP:MIN_DP:GQ\t0/0:30:30:50")  # fmt: skip
    plain = calls / "v.g.vcf"
    plain.write_text("\n".join(lines) + "\n")
    subprocess.run(["bgzip", "-f", str(plain)], check=True)
    subprocess.run(["tabix", "-p", "vcf", str(calls / "v.g.vcf.gz")], check=True)
    if direct_gvcf:
        world["vcf"] = calls / "v.g.vcf.gz"
    (calls / "v.manifest.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "outputs": [
                    {
                        "path": str(path),
                        "role": role,
                        "fingerprint": fingerprint(path),
                    }
                    for path, role in (
                        (world["vcf"], "variants"),
                        (calls / "v.g.vcf.gz", "reference_evidence"),
                    )
                ],
            }
        )
    )

    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    calibration = result.details["ancestry_calibration"]
    second = genotypes[half:]
    assert calibration["pca_sites_fraction"] == pytest.approx(
        (half - len(uncovered) + int((second > 0).sum())) / len(genotypes)
    )
    assert calibration["pca_missing_site_states"] == {
        "callable_reference": int((genotypes[:half] == 0).sum()) - len(uncovered),
        "uncovered": int((second == 0).sum()) + len(uncovered),
    }
    assert result.details["percentile_status"] == "ancestry_projection_incomplete"
