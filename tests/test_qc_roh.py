"""`qc roh` (#1174): runs of homozygosity with bcftools roh and population
allele frequencies, and the isodisomy review flag over imprinted regions.

The fixtures are a synthetic GRCh38 chr20 (markers every 10 kb, hom-ref calls
included) with runs of homozygosity placed inside and outside GNAS
(20q13.32, 58.84-58.91 Mb), and a sites VCF of population frequencies."""

import json
import random
from collections.abc import Collection
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.htslib_helpers import bgzip_index, needs_htslib
from wgsextract_cli import api
from wgsextract_cli.api.qc import QcRohParams
from wgsextract_cli.cli.dispatch import dispatch_command
from wgsextract_cli.cli.parser import build_parser, parse_args
from wgsextract_cli.core import results, roh_regions
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.panel import load_annotations
from wgsextract_cli.server.operations import (
    PathGuard,
    RequestError,
    find,
    params_from_json,
)

pytestmark = needs_htslib

SCHEMA = Path(__file__).parents[1] / (
    "src/wgsextract_cli/assets/schemas/wgsextract.roh.1.schema.json"
)
STEP = 10_000
FIRST, LAST = 100_000, 64_400_000
INSIDE_GNAS = (50_000_000, 62_000_000)
OUTSIDE = (20_000_000, 32_000_000)
TO_THE_END = (58_000_000, LAST)


def _header(
    build: str,
    chr_prefix: bool,
    fmt: bool,
    lengthless: Collection[str] = (),
    gq: bool = True,
    extra: Collection[str] = (),
) -> str:
    """A header of the build's autosomes, after the ``extra`` contigs; those
    in ``lengthless`` (or all, with "*") are declared without a length."""
    lines = ["##fileformat=VCFv4.2", *(f"##contig=<ID={c}>" for c in extra)]
    for name, length in load_annotations(build).contigs.items():
        if name.removeprefix("chr").isdigit():
            contig = name if chr_prefix else name.removeprefix("chr")
            if "*" in lengthless or name.removeprefix("chr") in lengthless:
                lines.append(f"##contig=<ID={contig}>")
            else:
                lines.append(f"##contig=<ID={contig},length={length}>")
    lines.append('##INFO=<ID=AF,Number=A,Type=Float,Description="Population AF">')
    if fmt:
        lines.append('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">')
        if gq:
            lines.append('##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="GQ">')
    columns = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
    return "\n".join(lines) + "\n" + columns + ("\tFORMAT\tS1" if fmt else "") + "\n"


def _af(pos: int) -> float:
    return 0.2 + 0.4 * ((pos // STEP) % 3) / 2  # 0.2, 0.4 or 0.6


OTHERS = range(200_000, 300_000, STEP)  # markers on the other autosomes


def _elsewhere(contigs: range, row: str) -> list[str]:
    return [row.format(n, pos) for n in contigs for pos in OTHERS]


def _sample(
    tmp: Path,
    runs: list[tuple[int, int]],
    interruption: str | None = None,
    others: bool = False,
    last: int = LAST,
    lengthless: Collection[str] = (),
    with_gq: bool = True,
    chr_prefix: bool = True,
    extra: Collection[str] = (),
) -> str:
    """chr20 calls: hom inside ``runs``, Hardy-Weinberg draws elsewhere. An
    ``interruption`` puts 30 heterozygous calls at 56.0-56.3 Mb: real (het),
    GQ 5 (weak), without a GQ (no_gq) or without a genotype (missing), or
    leaves 55-58 Mb without calls (gap). ``others`` adds ten heterozygous
    calls on every other autosome; ``last`` is the last marker; without
    ``with_gq``, calls are GT only. Autosomes are named 20 without
    ``chr_prefix``; ``extra`` contigs are declared first."""
    rng = random.Random(1174)
    other = "chr{}\t{}\t.\tA\tG\t50\tPASS\t.\tGT:GQ\t0/1:60"
    rows = _elsewhere(range(1, 20), other) if others else []
    for pos in range(FIRST, last + 1, STEP):
        p = _af(pos)
        if interruption == "gap" and 55_000_000 <= pos < 58_000_000:
            continue
        if 56_000_000 <= pos < 56_300_000 and interruption:
            gt, gq = {"het": ("0/1", 60), "weak": ("0/1", 5), "missing": ("./.", 0),
                      "no_gq": ("0/1", ".")}[interruption]  # fmt: skip
        elif any(start <= pos <= end for start, end in runs):
            gt, gq = ("1/1" if rng.random() < p else "0/0"), 60
        else:
            alleles = sorted(int(rng.random() < p) for _ in range(2))
            gt, gq = f"{alleles[0]}/{alleles[1]}", 60
        rows.append(f"chr20\t{pos}\t.\tA\tG\t50\tPASS\t.\tGT:GQ\t{gt}:{gq}")
    rows += _elsewhere(range(21, 23), other) if others else []
    plain = tmp / "sample.vcf"
    if not with_gq:
        rows = [row.replace("\tGT:GQ\t", "\tGT\t").rsplit(":", 1)[0] for row in rows]
    if not chr_prefix:
        rows = [row.removeprefix("chr") for row in rows]
    header = _header("hg38", chr_prefix, True, lengthless, with_gq, extra)
    plain.write_text(header + "\n".join(rows) + "\n")
    return bgzip_index(plain)


def _frequencies(
    tmp: Path,
    build: str = "hg38",
    shift: float = 0.0,
    chr_prefix: bool = False,
    lengthless: Collection[str] = (),
) -> str:
    """Population AFs at every marker, contigs named 20 (the sample says chr20)
    unless ``chr_prefix``."""
    chr_ = "chr" if chr_prefix else ""
    other = chr_ + "{}\t{}\t.\tA\tG\t.\tPASS\tAF=0.5"
    rows = (
        _elsewhere(range(1, 20), other)
        + [
            f"{chr_}20\t{pos}\t.\tA\tG\t.\tPASS\tAF={_af(pos) + shift:.3f}"
            for pos in range(FIRST, LAST + 1, STEP)
        ]
        + _elsewhere(range(21, 23), other)
    )
    plain = tmp / f"pop_{build}.vcf"
    header = _header(build, chr_prefix, False, lengthless)
    plain.write_text(header + "\n".join(rows) + "\n")
    return bgzip_index(plain)


def _roh(tmp: Path, vcf: str, af: str, **options: object) -> results.CommandResult:
    params = QcRohParams(
        input=Path(vcf), af_file=Path(af), outdir=tmp / "out", **options
    )
    return api.qc.roh(params, check=False)


def _document(result: results.CommandResult) -> dict:
    assert result.status == "ok", result.error
    report = next(o.path for o in result.outputs if o.role == "roh_report")
    document = json.loads(Path(report).read_text())
    schema = json.loads(SCHEMA.read_text())
    assert not list(Draft202012Validator(schema).iter_errors(document))
    return document


def _covering(document: dict, pos: int) -> dict | None:
    return next(
        (s for s in document["segments"] if s["start"] <= pos <= s["end"]), None
    )


def test_issue_1174_flag_only_for_runs_over_an_imprinted_region(tmp_path):
    """OwnGenome named GNAS syndromes for a run far from GNAS (#821); a run
    outside an imprinted region must not acquire its label."""
    vcf = _sample(tmp_path, [OUTSIDE, INSIDE_GNAS])
    result = _roh(tmp_path, vcf, _frequencies(tmp_path))
    document = _document(result)

    outside = _covering(document, 26_000_000)
    inside = _covering(document, 58_850_000)
    assert outside and outside["length_bp"] > 10_000_000
    assert outside["imprinted_regions"] == [] and not outside["flagged"]
    assert inside and inside["imprinted_regions"] == ["20q13-GNAS"]
    assert inside["flagged"] and inside["position"] == "interstitial"

    [flag] = document["flags"]
    assert flag["code"] == "investigate_possible_isodisomy"
    assert [r["id"] for r in flag["regions"]] == ["20q13-GNAS"]
    assert "Pseudohypoparathyroidism" in flag["message"]
    assert "not a diagnosis" in flag["message"]
    assert [w.code for w in result.warnings].count(flag["code"]) == 1
    rows = (tmp_path / "out" / "sample.roh.tsv").read_text().splitlines()
    assert sum(row.endswith("\tyes") for row in rows) == 1


@pytest.mark.parametrize(
    ("interruption", "split", "count"),
    [
        ("het", True, None),
        ("weak", False, "weak_genotype"),
        ("missing", False, "missing_genotype"),
        # PR #1349 review: a call without its own GQ passed the GQ floor.
        ("no_gq", False, "weak_genotype"),
    ],  # fmt: skip
)
def test_issue_1174_heterozygous_calls_split_runs_but_weak_evidence_does_not(
    tmp_path, interruption, split, count
):
    vcf = _sample(tmp_path, [INSIDE_GNAS], interruption)
    document = _document(_roh(tmp_path, vcf, _frequencies(tmp_path)))

    assert (_covering(document, 56_150_000) is None) == split
    assert bool(document["flags"]) != split  # the halves are under 10 Mb
    if count:
        assert document["markers"][count] == 30


def test_issue_1174_terminal_only_when_the_whole_chromosome_is_assessed(tmp_path):
    vcf = _sample(tmp_path, [TO_THE_END])
    frequencies = _frequencies(tmp_path)

    whole = _document(_roh(tmp_path, vcf, frequencies, regions="chr20"))
    assert _covering(whole, LAST)["position"] == "terminal"
    assert [f["position"] for f in whole["flags"]] == ["terminal"]

    part = _document(
        _roh(tmp_path, vcf, frequencies, regions="chr20:30000000-64444167")
    )
    assert _covering(part, LAST)["position"] == "undetermined"
    assert part["flags"] == []


def test_pr_1349_review_runs_stop_where_coverage_stops_short_of_the_end(tmp_path):
    """PR #1349 review: markers ending at 59 Mb made a 9 Mb run over GNAS
    terminal, flagged at 5 Mb, though chr20 runs on to 64.4 Mb."""
    vcf = _sample(tmp_path, [(50_000_000, 59_000_000)], last=59_000_000)
    document = _document(_roh(tmp_path, vcf, _frequencies(tmp_path)))

    assert _covering(document, 58_850_000)["position"] == "undetermined"
    assert document["flags"] == []


def test_pr_1349_review_runs_end_at_the_edges_of_scope_ranges(tmp_path):
    """PR #1349 review: two ranges leaving out GNAS were joined into one
    12 Mb run that was flagged over the excluded interval."""
    vcf = _sample(tmp_path, [INSIDE_GNAS])
    ranges = "chr20:50000000-58500000,chr20:59200000-62000000"
    document = _document(_roh(tmp_path, vcf, _frequencies(tmp_path), regions=ranges))

    assert all(
        s["end"] <= 58_500_000 or s["start"] >= 59_200_000 for s in document["segments"]
    )
    assert document["flags"] == []
    # Markers every 10 kb from each range's start to its end; the hole between
    # the ranges isn't counted.
    covered = (58_500_000 - 50_000_000 + 1) + (62_000_000 - 59_200_000 + 1)
    assert document["scope"]["denominator_bp"] == covered


@pytest.mark.parametrize("side", ["sample", "frequencies"])
def test_pr_1349_review_contigs_declared_without_a_length_are_used(tmp_path, side):
    """PR #1349 review: a chr20 declared without a length was left out of the
    default scope, and AF contigs named chr20 were looked up as 20."""
    if side == "sample":
        vcf = _sample(tmp_path, [INSIDE_GNAS], lengthless={"20"})
        frequencies = _frequencies(tmp_path)
        options: dict[str, object] = {}
    else:
        vcf = _sample(tmp_path, [INSIDE_GNAS])
        frequencies = _frequencies(tmp_path, chr_prefix=True, lengthless="*")
        options = {"af_build": "hg38"}
    document = _document(_roh(tmp_path, vcf, frequencies, **options))

    assert "chr20" in document["scope"]["contigs"]
    assert document["markers"]["used"] > 6000
    assert len(document["flags"]) == 1


def test_pr_1349_review_a_vcf_without_gq_needs_an_explicit_min_gq_0(tmp_path):
    """PR #1349 review: without FORMAT/GQ, --min-gq was silently ignored and
    unfiltered calls could still be flagged."""
    vcf = _sample(tmp_path, [INSIDE_GNAS], with_gq=False)
    frequencies = _frequencies(tmp_path)

    refused = _roh(tmp_path, vcf, frequencies)
    assert refused.error.code == "roh_no_gq"
    assert "--min-gq 0" in refused.error.message

    document = _document(_roh(tmp_path, vcf, frequencies, min_gq=0))
    assert document["markers"]["min_gq"] is None
    assert len(document["flags"]) == 1


def test_pr_1349_review_naming_follows_the_autosomes_not_chrM(tmp_path):
    """PR #1349 review: a header with autosomes 1-22 and chrM made qc roh look
    for chr20, so no calls were found."""
    vcf = _sample(tmp_path, [INSIDE_GNAS], chr_prefix=False, extra=["chrM"])
    document = _document(_roh(tmp_path, vcf, _frequencies(tmp_path)))

    assert "20" in document["scope"]["contigs"]
    assert [f["chrom"] for f in document["flags"]] == ["20"]


def test_pr_1349_review_af_cache_is_a_path_the_server_checks(tmp_path, monkeypatch):
    """PR #1349 review: the prepared-AF cache in the reference library was
    written without the server's path guard seeing it."""
    library = tmp_path / "library"
    monkeypatch.setitem(settings, "reference_library", str(library))
    data = tmp_path / "data"
    request = {"input": str(data / "s.vcf.gz"), "af_file": str(data / "af.vcf.gz")}
    params = params_from_json(find("qc/roh"), request)
    with pytest.raises(RequestError) as refused:
        PathGuard([data]).check(params)
    assert refused.value.code == "path_not_allowed"
    PathGuard([data, library]).check(params)


def test_issue_1174_runs_end_at_gaps_without_markers(tmp_path):
    """On HG002 (GIAB v4.2.1), runs spanning the chr9 centromere's 18 Mb
    without markers were reported as one 21.8 Mb run."""
    vcf = _sample(tmp_path, [INSIDE_GNAS], "gap")
    document = _document(_roh(tmp_path, vcf, _frequencies(tmp_path)))

    left, right = _covering(document, 54_990_000), _covering(document, 58_850_000)
    assert left and right and left != right
    assert left["end"] < 55_000_000 <= 58_000_000 <= right["start"]
    assert document["flags"] == []  # neither part is 10 Mb
    assert document["scope"]["denominator_bp"] < LAST - FIRST - 2_900_000


def test_issue_1174_partial_scope_froh_uses_the_assessed_denominator(tmp_path):
    """OwnGenome divided partial data by the whole genome (#821)."""
    vcf = _sample(tmp_path, [OUTSIDE, INSIDE_GNAS])
    frequencies = _frequencies(tmp_path)
    document = _document(_roh(tmp_path, vcf, frequencies, regions="chr20"))

    scope, summary = document["scope"], document["summary"]
    assert scope["regions"] == ["chr20"] and not scope["whole_genome"]
    assert scope["denominator_bp"] == LAST - FIRST + 1
    runs = sum(s["length_bp"] for s in document["segments"] if s["length_bp"] >= 1.5e6)
    assert summary["froh"] == pytest.approx(runs / scope["denominator_bp"], abs=1e-6)
    assert any("not the whole genome" in note for note in document["notes"])

    # By default every autosome in the header is in scope; those without
    # markers are reported, and still don't count towards the denominator.
    default = _roh(tmp_path, vcf, frequencies)
    everything = _document(default)["scope"]
    assert len(everything["contigs_without_markers"]) == 21
    assert everything["denominator_bp"] == scope["denominator_bp"]
    assert "roh_contigs_without_markers" in {w.code for w in default.warnings}

    # Every autosome with markers is the whole genome only when no range
    # leaves part of one out.
    everywhere = _sample(tmp_path, [OUTSIDE, INSIDE_GNAS], others=True)
    whole = _document(_roh(tmp_path, everywhere, frequencies))["scope"]
    assert whole["whole_genome"] and whole["contigs_without_markers"] == []
    ranges = ",".join(f"chr{n}:1-1000000" for n in (*range(1, 20), 21, 22))
    sliced = _roh(tmp_path, everywhere, frequencies, regions=ranges + ",chr20")
    assert not _document(sliced)["scope"]["whole_genome"]


def test_issue_1174_mismatched_builds_and_missing_resources_are_refused(tmp_path):
    vcf = _sample(tmp_path, [INSIDE_GNAS])

    missing = _roh(tmp_path, vcf, str(tmp_path / "absent.vcf.gz"))
    assert missing.error.code == "roh_af_missing"
    assert "Nothing is downloaded" in missing.error.message

    hg19 = _roh(tmp_path, vcf, _frequencies(tmp_path, "hg19"))
    assert hg19.error.code == "roh_af_build_mismatch"

    table = tmp_path / "af.tab"
    table.write_text("chr20\t100000\tA,G\t0.2\n")
    assert _roh(tmp_path, vcf, str(table)).error.code == "roh_af_build_unknown"

    bed = tmp_path / "imprinted.bed"
    bed.write_text("track name=imprinted db=hg19\nchr20\t57414802\t57486247\tGNAS\n")
    wrong = _roh(tmp_path, vcf, _frequencies(tmp_path), imprinted_regions=bed)
    assert wrong.error.code == "roh_regions_build_mismatch"

    # PR #1349 review: a reversed interval would match runs across it.
    bed.write_text("track db=hg38\nchr20\t60000000\t58000000\tGNAS\n")
    reversed_ = _roh(tmp_path, vcf, _frequencies(tmp_path), imprinted_regions=bed)
    assert reversed_.error.code == "roh_regions_unreadable"
    assert not (tmp_path / "out" / "sample.roh.tsv").exists()


def test_issue_1174_prepared_frequencies_reused_until_the_release_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(settings, "reference_library", str(tmp_path / "library"))
    vcf = _sample(tmp_path, [INSIDE_GNAS])
    frequencies = _frequencies(tmp_path)

    first = _document(_roh(tmp_path, vcf, frequencies))["population_frequencies"]
    again = _document(_roh(tmp_path, vcf, frequencies))["population_frequencies"]
    assert (first["prepared"], again["prepared"]) == ("built", "reused")
    assert again["digest"] == first["digest"]

    _frequencies(tmp_path, shift=0.05)  # a new release at the same path
    changed = _document(_roh(tmp_path, vcf, frequencies))["population_frequencies"]
    assert changed["prepared"] == "built"
    assert "changed" in changed["prepared_reason"]
    assert changed["digest"] != first["digest"]


def test_issue_1174_cli_writes_segments_markers_and_manifest(tmp_path):
    vcf = _sample(tmp_path, [INSIDE_GNAS])
    out = tmp_path / "cli"
    argv = ["qc", "roh", "--input", vcf, "--af-file", _frequencies(tmp_path),
            "--af-release", "test panel", "--outdir", str(out)]  # fmt: skip
    args, _ = parse_args(*build_parser(), argv)
    dispatch_command(args, set())

    manifest = json.loads((out / "sample.roh.manifest.json").read_text())
    assert not list(Draft202012Validator(results.load_schema()).iter_errors(manifest))
    assert {o["role"] for o in manifest["outputs"]} == {
        "roh_segments", "roh_report", "roh_markers"
    }  # fmt: skip
    assert manifest["metrics"]["roh_flags"] == 1
    assert manifest["details"]["population_frequencies"]["release"] == "test panel"
    markers = (out / "sample.roh.markers.tsv.gz").read_bytes()
    assert markers[:2] == b"\x1f\x8b"


@pytest.mark.parametrize("build", ["hg19", "hg38", "t2t"])
def test_issue_1174_imprinted_regions_resolve_on_every_build(build):
    """Each bundled region lies on its cytoband's chromosome in the build's
    own gene table (T2T's lacks KCNQ1OT1, so 11p15 spans its other genes)."""
    regions = roh_regions.bundled(build).regions
    assert len(regions) == 7
    for region in regions:
        assert region.chrom == "chr" + region.band.split("p")[0].split("q")[0]
        assert 0 < region.end - region.start < 3_000_000
