import csv
import gzip
import io
import json
import math
import shutil
import subprocess
from dataclasses import replace
from http.client import IncompleteRead
from pathlib import Path
from urllib.response import addinfourl

import numpy as np
import pytest

from wgsextract_cli import api
from wgsextract_cli.core import ancestry_reference, pgs_reference
from wgsextract_cli.core.pgs import CURATED
from wgsextract_cli.main import main

FIXTURES = Path(__file__).parent / "fixtures" / "pgs"
pytestmark = pytest.mark.skipif(
    not shutil.which("bcftools") or not shutil.which("samtools"),
    reason="PGS tests need real bcftools and samtools",
)


def _params(outdir: Path, **options: object) -> api.ScorePgsParams:
    return api.ScorePgsParams(
        input=FIXTURES / "variants.vcf",
        build="GRCh38",
        scoring_file=FIXTURES / "additive.txt",
        outdir=outdir,
        **options,
    )


def _reference(folder: Path) -> Path:
    ref = folder / "reference.fa"
    sequence = list("A" * 100)
    sequence[19] = "C"
    sequence[39] = "G"
    ref.write_text(">chr1\n" + "".join(sequence) + "\n")
    subprocess.run(["samtools", "faidx", str(ref)], check=True)
    return ref


def test_617_missing_site_is_unmatched_never_hom_ref(tmp_path: Path) -> None:
    result = api.score.pgs(_params(tmp_path))
    assert result.metrics["score_sum"] == pytest.approx(1.0)
    assert result.metrics["variants_matched"] == 2
    assert result.metrics["variants_total"] == 6
    assert result.details["match_counts"] == {"called": 2, "missing": 3, "no_call": 1}
    assert result.details["percentile"] is None
    assert result.details["percentile_status"] == "below_match_threshold"
    assert {"pgs_low_match", "pgs_uncalibrated", "pgs_research_only"} <= {
        w.code for w in result.warnings
    }
    output = next(Path(f.path) for f in result.outputs if f.role == "pgs")
    assert json.loads(output.read_text())["score_sum"] == pytest.approx(1.0)
    manifest = json.loads(Path(str(output) + ".manifest.json").read_text())
    assert manifest["metrics"]["variants_matched"] == 2
    table = next(Path(f.path) for f in result.outputs if f.role == "pgs_table")
    with table.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(rows) == 1
    assert float(rows[0]["match_rate"]) == pytest.approx(2 / 6)
    assert rows[0]["license"] == "CC0-1.0"
    assert rows[0]["citation"] == "Synthetic test fixture"


def test_617_confident_bed_does_not_override_explicit_no_call(tmp_path: Path) -> None:
    bed = tmp_path / "confident.bed"
    bed.write_text("chr1\t0\t80\n")
    result = api.score.pgs(
        _params(tmp_path, ref=_reference(tmp_path), confident_bed=bed)
    )
    assert result.metrics["score_sum"] == pytest.approx(2.7)
    assert result.details["match_counts"] == {
        "called": 2,
        "confident_reference": 2,
        "no_call": 1,
        "outside_confident": 1,
    }


def test_617_reference_block_only_covers_its_interval(tmp_path: Path) -> None:
    gvcf = FIXTURES / "sample.vcf"
    params = replace(_params(tmp_path), input=gvcf, ref=_reference(tmp_path))
    result = api.score.pgs(params)
    assert result.metrics["score_sum"] == pytest.approx(1.2)
    assert result.details["match_counts"]["callable_reference"] == 1
    # A gVCF that doesn't list a base read nothing there (#863).
    assert result.details["match_counts"]["uncovered"] == 2


def test_863_gvcf_blocks_need_quality_and_reads_to_be_hom_ref(tmp_path: Path) -> None:
    """Issue #863: a `<NON_REF>` block with no GQ or depth, or a DeepVariant
    `<*>` block that no read covered (MIN_DP 0), was counted as homozygous
    reference; neither says the bases were read well."""
    lines = (FIXTURES / "sample.vcf").read_text().splitlines()
    lines[-1] = "chr1\t60\t.\tA\t<NON_REF>\t.\tPASS\tEND=75\tGT\t0/0"
    lines.insert(4, '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="m">')
    lines.append("chr1\t76\t.\tA\t<*>\t.\tPASS\tEND=100\tGT:GQ:MIN_DP\t0/0:0:0")
    gvcf = tmp_path / "blocks.g.vcf"
    gvcf.write_text("\n".join(lines) + "\n")
    scoring = tmp_path / "score.txt"
    scoring.write_text(
        "#genome_build=GRCh38\n"
        "chr_name\tchr_position\teffect_allele\tother_allele\teffect_weight\n"
        "1\t70\tA\tC\t1\n1\t90\tA\tC\t1\n"
    )
    result = api.score.pgs(
        replace(_params(tmp_path), input=gvcf, scoring_file=scoring,
                ref=_reference(tmp_path))
    )  # fmt: skip
    assert result.metrics["variants_matched"] == 0
    assert result.details["match_counts"] == {
        "low_quality_reference": 1,
        "uncovered": 1,
    }


@pytest.mark.parametrize("compressed", [False, True])
def test_617_unsorted_scores_seek_across_fasta_blocks(
    tmp_path: Path, compressed: bool
) -> None:
    sequence = "ACGT" * 35000
    ref = tmp_path / "large.fa"
    ref.write_text(
        ">chr1\n"
        + "\n".join(sequence[n : n + 61] for n in range(0, len(sequence), 61))
        + "\n"
    )
    if compressed:
        if not shutil.which("bgzip"):
            pytest.skip("bgzip not installed")
        dest = tmp_path / "large.fa.gz"
        with dest.open("wb") as target:
            subprocess.run(["bgzip", "-c", str(ref)], stdout=target, check=True)
        ref = dest
    subprocess.run(["samtools", "faidx", str(ref)], check=True)
    scoring = tmp_path / "score.txt"
    text = "#genome_build=GRCh38\nchr_name\tchr_position\teffect_allele\tother_allele\teffect_weight\n"
    for pos in (135000, 65536, 65537, 1, 70000):
        base = sequence[pos - 1]
        other = {"A": "C", "C": "A", "G": "T", "T": "G"}[base]
        text += f"1\t{pos}\t{base}\t{other}\t0.1\n"
    scoring.write_text(text)
    bed = tmp_path / "confident.bed"
    bed.write_text(f"chr1\t0\t{len(sequence)}\n")
    result = api.score.pgs(
        replace(_params(tmp_path), scoring_file=scoring, confident_bed=bed, ref=ref)
    )
    assert result.metrics["score_sum"] == pytest.approx(1.0)
    assert result.metrics["variants_matched"] == 5


def test_617_confident_bed_cannot_rescue_spanning_deletion(tmp_path: Path) -> None:
    vcf = tmp_path / "deletion.vcf"
    lines = (FIXTURES / "sample.vcf").read_text().splitlines()
    lines.insert(-2, "chr1\t28\t.\tAAA\tA\t50\tPASS\t.\tGT\t0/1")
    vcf.write_text("\n".join(lines) + "\n")
    bed = tmp_path / "confident.bed"
    bed.write_text("chr1\t0\t80\n")
    result = api.score.pgs(
        replace(
            _params(tmp_path), input=vcf, confident_bed=bed, ref=_reference(tmp_path)
        )
    )
    assert result.metrics["score_sum"] == pytest.approx(1.2)
    assert result.details["match_counts"]["overlapping_nonreference"] == 1


def test_617_palindromic_strand_is_not_inferred_from_matching_alleles(
    tmp_path: Path,
) -> None:
    scoring = tmp_path / "ambiguous.txt"
    scoring.write_text(
        "#genome_build=GRCh38\n"
        "chr_name\tchr_position\teffect_allele\tother_allele\teffect_weight\n"
        "1\t10\tA\tT\t1\n1\t20\tC\tT\t0.25\n"
    )
    vcf = tmp_path / "palindromic.vcf"
    vcf.write_text(
        (FIXTURES / "sample.vcf").read_text().replace("\tA\tG\t", "\tA\tT\t")
    )
    params = replace(_params(tmp_path), input=vcf, scoring_file=scoring)
    result = api.score.pgs(params)
    assert result.metrics["score_sum"] == pytest.approx(0.5)
    assert result.details["match_counts"]["strand_ambiguous"] == 1
    kept = api.score.pgs(replace(params, keep_ambiguous=True))
    assert kept.metrics["score_sum"] == pytest.approx(1.5)
    assert "pgs_ambiguous_strand_assumed" in {w.code for w in kept.warnings}


def test_617_sample_filters_exclude_genotypes_and_reference_blocks(
    tmp_path: Path,
) -> None:
    source = (FIXTURES / "sample.vcf").read_text().splitlines()
    lines = []
    for line in source:
        if line.startswith("#CHROM"):
            lines.append(
                '##FORMAT=<ID=FT,Number=1,Type=String,Description="Sample filter">'
            )
        elif not line.startswith("#"):
            fields = line.split("\t")
            fields[8] += ":FT"
            fields[9] += ":LowGQ" if fields[1] in ("10", "60") else ":PASS"
            line = "\t".join(fields)
        lines.append(line)
    vcf = tmp_path / "filtered.vcf"
    vcf.write_text("\n".join(lines) + "\n")
    bed = tmp_path / "confident.bed"
    bed.write_text("chr1\t0\t80\n")
    result = api.score.pgs(
        replace(
            _params(tmp_path),
            input=vcf,
            ref=_reference(tmp_path),
            confident_bed=bed,
        )
    )
    # The BED can't make the bases this gVCF never read reference (#863).
    assert result.metrics["score_sum"] == pytest.approx(0.5)
    assert result.details["match_counts"]["filtered"] == 2
    assert result.details["match_counts"]["uncovered"] == 1


def test_617_confident_bed_uses_configured_reference(tmp_path: Path) -> None:
    reference = _reference(tmp_path)
    bed = tmp_path / "confident.bed"
    bed.write_text("chr1\t0\t80\n")
    config = tmp_path / "config.toml"
    config.write_text(f"reference_fasta = {json.dumps(str(reference))}\n")
    api.use_config(config)
    result = api.score.pgs(_params(tmp_path, confident_bed=bed))
    assert result.metrics["score_sum"] == pytest.approx(2.7)
    assert any(
        f.role == "reference" and Path(f.path) == reference for f in result.inputs
    )


@pytest.mark.skipif(not shutil.which("plink2"), reason="optional plink2 not installed")
def test_617_optional_plink_checks_matched_calls(tmp_path: Path) -> None:
    result = api.score.pgs(_params(tmp_path, plink2_check=True))
    assert result.details["plink2_crosscheck"]["score_sum"] == pytest.approx(1.0)
    assert result.details["plink2_crosscheck"]["absolute_difference"] <= 1e-6


def test_617_download_retries_and_records_catalog_licence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error
    import urllib.request

    pgs_id = "PGS999999"
    url = f"https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/{pgs_id}/ScoringFiles/Harmonized/test.txt.gz"
    metadata = {
        "ftp_harmonized_scoring_files": {"GRCh38": {"positions": url}},
        "license": "Test licence",
    }
    data = gzip.compress(
        (f"#pgs_id={pgs_id}\n" + (FIXTURES / "additive.txt").read_text()).encode()
    )
    failures = 0

    def response(address: str, **kwargs: object) -> addinfourl:
        nonlocal failures
        if address == url:
            if failures == 0:
                failures += 1
                raise urllib.error.URLError("temporary outage")
            payload = data
        else:
            payload = json.dumps(metadata).encode()
        return addinfourl(
            io.BytesIO(payload), {"Content-Length": str(len(payload))}, address
        )

    monkeypatch.setattr(urllib.request, "urlopen", response)
    monkeypatch.setattr("time.sleep", lambda _: None)
    result = api.score.pgs(
        api.ScorePgsParams(
            input=FIXTURES / "sample.vcf",
            build="GRCh38",
            pgs_id=pgs_id,
            outdir=tmp_path,
        )
    )
    assert result.details["license"] == "Test licence"
    assert result.details["pgs_id"] == pgs_id
    assert result.metrics["score_sum"] == pytest.approx(1.0)
    assert "pgs_uncurated_score" in {w.code for w in result.warnings}


@pytest.mark.parametrize(
    ("license", "restricted"),
    [
        (
            "Freely available to the academic community for research use. Parties "
            "interested in using the scores for commercial purposes should contact "
            "the Broad Office of Strategic Alliances and Partnering.",
            True,
        ),
        (
            "PGS obtained from the Catalog should be cited appropriately, and used "
            "in accordance with any licensing restrictions set by the authors.",
            False,
        ),
    ],
)
def test_864_bmi_alias_warns_of_its_scores_author_licence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, license: str, restricted: bool
) -> None:
    """Issue #864: --trait bmi selected PGS000027, which only academic research
    may use, without saying so outside the licence field."""
    import urllib.request

    url = "https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/PGS000027/h.txt.gz"
    payloads = {
        "https://www.pgscatalog.org/rest/score/PGS000027/": json.dumps(
            {
                "ftp_harmonized_scoring_files": {"GRCh38": {"positions": url}},
                "license": license,
            }
        ).encode(),
        url: gzip.compress(
            ("#pgs_id=PGS000027\n" + (FIXTURES / "additive.txt").read_text()).encode()
        ),
    }

    def response(address: str, **kwargs: object) -> addinfourl:
        payload = payloads[address]
        return addinfourl(
            io.BytesIO(payload), {"Content-Length": str(len(payload))}, address
        )

    monkeypatch.setattr(urllib.request, "urlopen", response)
    result = api.score.pgs(
        api.ScorePgsParams(
            input=FIXTURES / "sample.vcf", build="GRCh38", trait="bmi", outdir=tmp_path
        )
    )
    warned = [w for w in result.warnings if w.code == "pgs_license_restricted"]
    assert bool(warned) is restricted
    assert all("academic community" in w.message for w in warned)


@pytest.fixture
def catalog_payloads() -> tuple[str, str, dict[str, bytes]]:
    pgs_id = "PGS999999"
    url = f"https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/{pgs_id}/ScoringFiles/Harmonized/test.txt.gz"
    metadata = {
        "ftp_harmonized_scoring_files": {"GRCh38": {"positions": url}},
        "license": "Retry test licence",
    }
    return (
        pgs_id,
        url,
        {
            f"https://www.pgscatalog.org/rest/score/{pgs_id}/": json.dumps(
                metadata
            ).encode(),
            url: gzip.compress(
                (
                    f"#pgs_id={pgs_id}\n" + (FIXTURES / "additive.txt").read_text()
                ).encode()
            ),
        },
    )


@pytest.mark.parametrize(
    ("target", "failure"),
    [
        ("weights", "short"),
        ("metadata", "short"),
        ("metadata", "length-mismatch"),
        ("weights", "truncated-gzip"),
        ("weights", "empty"),
        ("metadata", "invalid-json"),
        ("metadata", "invalid-encoding"),
        ("weights", "incomplete-read"),
        ("weights", "corrupt-deflate"),
    ],
)
def test_617_truncated_catalog_download_retries_before_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_payloads: tuple[str, str, dict[str, bytes]],
    target: str,
    failure: str,
) -> None:
    import urllib.request

    pgs_id, score_url, payloads = catalog_payloads
    bad_url = score_url if target == "weights" else next(iter(payloads))
    requests: dict[str, int] = {}

    class InterruptedResponse(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise IncompleteRead(b"partial body", len(payloads[bad_url]))

    def response(address: str, **kwargs: object) -> addinfourl:
        requests[address] = requests.get(address, 0) + 1
        body = payloads[address]
        headers = {"Content-Length": str(len(body))}
        if address == bad_url and requests[address] == 1:
            if failure == "short":
                body = body[: len(body) // 2]
            elif failure == "length-mismatch":
                headers["Content-Length"] = str(len(body) + 100)
            elif failure == "incomplete-read":
                return addinfourl(InterruptedResponse(), headers, address)
            else:
                headers = {}
                if failure == "truncated-gzip":
                    body = body[:-8]
                elif failure == "empty":
                    body = b""
                elif failure == "corrupt-deflate":
                    body = b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\x07" + b"\x00" * 8
                elif failure == "invalid-encoding":
                    body = body.decode("utf-8").encode("utf-16")
                else:
                    body = body[: len(body) // 2]
        return addinfourl(io.BytesIO(body), headers, address)

    monkeypatch.setattr(urllib.request, "urlopen", response)
    monkeypatch.setattr("time.sleep", lambda _: None)
    result = api.score.pgs(
        api.ScorePgsParams(
            input=FIXTURES / "sample.vcf",
            build="GRCh38",
            pgs_id=pgs_id,
            outdir=tmp_path,
        )
    )
    assert requests[bad_url] == 2
    assert result.metrics["score_sum"] == pytest.approx(1.0)
    assert result.details["license"] == "Retry test licence"
    scoring = next(Path(f.path) for f in result.inputs if f.role == "pgs_scoring_file")
    assert scoring.read_bytes() == payloads[score_url]


@pytest.mark.parametrize("target", ["metadata", "weights"])
def test_617_bad_catalog_download_never_replaces_a_good_cached_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_payloads: tuple[str, str, dict[str, bytes]],
    target: str,
) -> None:
    import urllib.request

    pgs_id, score_url, payloads = catalog_payloads
    folder = tmp_path / "pgs_catalog"
    folder.mkdir()
    metadata = folder / f"{pgs_id}.json"
    weights = folder / f"{pgs_id}_GRCh38.txt.gz"
    metadata.write_bytes(payloads[next(iter(payloads))])
    weights.write_bytes(payloads[score_url])
    bad_url = score_url if target == "weights" else next(iter(payloads))
    requests: dict[str, int] = {}

    def response(address: str, **kwargs: object) -> addinfourl:
        requests[address] = requests.get(address, 0) + 1
        body = payloads[address]
        headers = {"Content-Length": str(len(body))}
        if address == bad_url:
            body = body[: len(body) // 2]
        return addinfourl(io.BytesIO(body), headers, address)

    monkeypatch.setattr(urllib.request, "urlopen", response)
    monkeypatch.setattr("time.sleep", lambda _: None)
    result = api.score.pgs(
        api.ScorePgsParams(
            input=FIXTURES / "sample.vcf",
            build="GRCh38",
            pgs_id=pgs_id,
            outdir=tmp_path,
        ),
        check=False,
    )
    assert result.status == "failed"
    assert requests[bad_url] == 3
    assert result.error is not None
    assert "PGS download failed" in result.error.message
    assert metadata.read_bytes() == payloads[next(iter(payloads))]
    assert weights.read_bytes() == payloads[score_url]


def test_617_custom_file_overrides_catalog_and_configures_gate(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f"pgs_scoring_file = {json.dumps(str((FIXTURES / 'additive.txt').resolve()))}\n"
        "pgs_min_match = 0.3\n"
    )
    api.use_config(config)
    result = api.score.pgs(
        api.ScorePgsParams(
            input=FIXTURES / "sample.vcf",
            build="GRCh38",
            pgs_id="PGS000018",
            outdir=tmp_path,
        )
    )
    assert result.metrics["score_sum"] == pytest.approx(1.0)
    assert result.details["minimum_match_rate"] == 0.3
    assert result.details["percentile_status"] == "reference_calibration_unavailable"
    assert "pgs_low_match" not in {w.code for w in result.warnings}


def test_617_wrong_scoring_build_is_refused(tmp_path: Path) -> None:
    with pytest.raises(api.CommandFailed, match="scoring build"):
        api.score.pgs(
            api.ScorePgsParams(
                input=FIXTURES / "sample.vcf",
                build="GRCh37",
                scoring_file=FIXTURES / "additive.txt",
                outdir=tmp_path,
            )
        )


@pytest.mark.parametrize("suffix", [".vcf", ".vcf.gz", ".vcf.bgz", ".bcf"])
def test_617_compression_does_not_change_the_score_output_stem(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / f"person.run{suffix}"
    if suffix == ".vcf":
        shutil.copyfile(FIXTURES / "sample.vcf", source)
    else:
        subprocess.run(
            [
                "bcftools",
                "view",
                "-Ob" if suffix == ".bcf" else "-Oz",
                "-o",
                str(source),
                str(FIXTURES / "sample.vcf"),
            ],
            check=True,
        )
    result = api.score.pgs(replace(_params(tmp_path), input=source))
    assert {
        Path(output.path).name
        for output in result.outputs
        if output.role in ("pgs", "pgs_table")
    } == {"person.run_custom_pgs.json", "person.run_custom_pgs.tsv"}
    assert result.metrics["score_sum"] == pytest.approx(1.0)


def test_617_missing_column_error_does_not_list_present_columns(tmp_path: Path) -> None:
    scoring = tmp_path / "incomplete.txt"
    scoring.write_text(
        "#genome_build=GRCh38\nchr_name\tchr_position\teffect_allele\n1\t10\tG\n"
    )
    result = api.score.pgs(
        replace(_params(tmp_path), scoring_file=scoring), check=False
    )
    assert result.status == "failed"
    assert result.error is not None
    assert "effect_weight" in result.error.message
    for present in ("chr_name", "chr_position", "effect_allele"):
        assert present not in result.error.message


def test_617_cli_json_reports_sum_and_structured_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "score",
            "pgs",
            "--input",
            str(FIXTURES / "sample.vcf"),
            "--build",
            "GRCh38",
            "--scoring-file",
            str(FIXTURES / "additive.txt"),
            "--outdir",
            str(tmp_path),
            "--json",
        ]
    )
    document = json.loads(capsys.readouterr().out)
    assert document["metrics"]["score_sum"] == pytest.approx(1.0)
    assert document["details"]["percentile"] is None


@pytest.mark.remote
def test_617_curated_ids_match_catalog_traits() -> None:
    """Prevent accidentally offering an Alzheimer's score as height (#617)."""
    import urllib.request

    labels = {
        "cad": "Coronary artery disease",
        "breast-cancer": "Breast cancer",
        "height": "Height",
        "t2d": "Type 2 diabetes (T2D)",
        "bmi": "Body mass index (BMI)",
        "stroke": "Stroke",
        "triglycerides": "Triglycerides",
        "ldl-cholesterol": "LDL cholesterol",
        "prostate-cancer": "Prostate cancer",
        "lung-cancer": "Lung cancer",
        "thyroid-cancer": "Thyroid cancer",
    }
    for label, pgs_id in CURATED.items():
        with urllib.request.urlopen(
            f"https://www.pgscatalog.org/rest/score/{pgs_id}/", timeout=60
        ) as response:
            data = json.load(response)
        assert data["trait_reported"].casefold() == labels[label].casefold(), (
            label,
            pgs_id,
        )


# --- Ancestry-adjusted percentiles (#617 Phase 2) ------------------------------

TRAINING = 40  # reference training samples: 20 "EUR" around PC 0, 20 "AFR" at PC1 = 10


def _calibrated_world(
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    offset_pc: float = 0.0,
    calibrate_rows: int = 40,
    whole_genome: bool = False,
    development: frozenset[str] | None = frozenset({"EUR"}),
) -> dict:
    """A fake genome with the ancestry reference beside it (5 PCs; the
    sample's projection is ``offset_pc`` on PC1), a 40-row scoring file at its
    sites, and a PGS reference for that file pinned in place of the published
    one, developed in ``development``. Its model: Z_norm2 = (calibrated score
    - 1) / 2 at any PCs. ``whole_genome`` spreads the sites over 22 small
    autosomes, so a variants-only VCF of them passes as a whole genome."""
    from tests.ancestry_helpers import build, calls_vcf

    shape = (
        {
            "contigs": tuple(f"chr{c}" for c in range(1, 23)),
            "length": 40_000,
            "sites_per_contig": 100,
        }
        if whole_genome
        else {}
    )
    world = build(folder, [1, 0, 0, 0, 0, 0, 0], **shape)
    npz = folder / "ancestry" / "wgsextract-ancestry-hgdp1kg-v1.npz"
    with np.load(npz) as data:
        arrays = {k: data[k] for k in data.files}
    n = len(world["sites"])
    freq = np.full(n, 0.5, dtype=np.float32)
    # Every site's standardised genotype adds the same to PC1, so the
    # sample's PC1 is offset_pc whatever its genotypes: z sums to the offset.
    z = (world["genotypes"] - 1.0) / np.sqrt(0.5)
    loadings = np.zeros((n, 5), dtype=np.float32)
    if offset_pc:
        loadings[:, 0] = offset_pc / z.sum()
    rng = np.random.default_rng(3)
    scores = rng.normal(size=(TRAINING, 5)).astype(np.float32)
    scores[TRAINING // 2 :, 0] += 10
    arrays.update(
        pca_frequency=freq,
        pca_loadings=loadings,
        pca_scores=scores,
        pca_population=np.array(["CEU"] * 20 + ["YRI"] * 20),
        pca_region=np.array(["EUR"] * 20 + ["AFR"] * 20),
    )
    np.savez_compressed(npz, **arrays)  # type: ignore[arg-type]
    monkeypatch.setattr(ancestry_reference, "SHA256", _sha(npz))

    rows = [
        (site, g)
        for site, g in zip(world["sites"], world["genotypes"], strict=True)
        if site[2] + site[3] not in ("AT", "TA", "CG", "GC")
    ][:40]
    scoring = folder / "score.txt"
    with scoring.open("w") as f:
        f.write("#genome_build=GRCh38\n#trait_reported=Synthetic\n#license=CC0-1.0\n")
        f.write("chr_name\tchr_position\teffect_allele\tother_allele\teffect_weight\n")
        f.writelines(f"{c}\t{p}\t{alt}\t{ref}\t0.1\n" for (c, p, ref, alt), _ in rows)
    mask = np.zeros(40, dtype=bool)
    mask[:calibrate_rows] = True
    populations = {}
    for name, rows_ in (
        ("ALL", slice(None)),
        ("EUR", slice(0, 20)),
        ("AFR", slice(20, 40)),
    ):
        x = scores[rows_].astype(np.float64)
        centred = x - x.mean(axis=0)
        populations[name] = {
            "n": len(x),
            "location": x.mean(axis=0).tolist(),
            "precision": np.linalg.pinv(centred.T @ centred / len(x)).tolist(),
        }
    training_scores = np.linspace(0.0, 3.9, TRAINING)
    info = {
        "version": 1,
        "build": "GRCh38",
        "method": "test",
        "ancestry_reference_sha256": _sha(npz),
        "n_popcomp": 5,
        "n_normalization": 4,
        "frequency_regions": ["AFR", "EUR"],
        "populations": populations,
        "scores": {
            "PGS999999": {
                "scoring_file_sha256": _sha(scoring),
                "rows": 40,
                "calibration_rows": calibrate_rows,
                "model": {
                    "regions": {
                        r: {
                            "n": 20,
                            "mean": float(training_scores[s].mean()),
                            "std": float(training_scores[s].std()),
                        }
                        for r, s in (("EUR", slice(0, 20)), ("AFR", slice(20, 40)))
                    },
                    "pc_mean": [0.0] * 4,
                    "pc_std": [1.0] * 4,
                    "pgs_offset": 0.0,
                    "norm1_coef": [1.0, 0, 0, 0, 0],
                    "norm1_resid_std": 2.0,
                    "norm2_mu_coef": [1.0, 0, 0, 0, 0],
                    "norm2_var_coef": [float(np.log(4.0)), 0, 0, 0, 0],
                },
            }
        },
    }
    (folder / "pgs").mkdir()
    reference = folder / "pgs" / pgs_reference.NAME
    np.savez_compressed(
        reference,
        PGS999999_rows=np.packbits(mask),
        PGS999999_frequency=np.full((calibrate_rows, 2), 32768, dtype=np.uint16),
        PGS999999_scores=training_scores,
        training_rows=np.arange(TRAINING, dtype=np.int32),
        info=np.array(json.dumps(info)),
    )
    monkeypatch.setattr(pgs_reference, "SHA256", _sha(reference))
    monkeypatch.setattr(pgs_reference, "SCORES", {_sha(scoring): "PGS999999"})
    monkeypatch.setattr(
        pgs_reference,
        "DEVELOPMENT_ANCESTRY",
        {} if development is None else {"PGS999999": development},
    )
    vcf = calls_vcf(world, folder / "sample.vcf.gz", variants_only=False)
    expected = sum(0.1 * g for _, g in rows)
    return {
        "world": world,
        "fasta": world["fasta"],
        "vcf": vcf,
        "scoring": scoring,
        "expected": expected,
        "scored": [(f"chr{c}", str(p)) for (c, p, _, _), _ in rows],
    }


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _calibrated(world: dict, outdir: Path, **options: object) -> object:
    return api.score.pgs(
        api.ScorePgsParams(
            input=world["vcf"],
            build="GRCh38",
            scoring_file=world["scoring"],
            ref=world["fasta"],
            outdir=outdir,
            **options,
        ),
        check=False,
    )


def _drop_scored(world: dict, count: int, plain: Path) -> None:
    """Drop the first ``count`` scored sites from the sample's VCF."""
    lines = subprocess.run(
        ["bcftools", "view", str(world["vcf"])],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    dropped = set(world["scored"][:count])
    plain.write_text(
        "\n".join(
            line
            for line in lines
            if line.startswith("#") or tuple(line.split("\t")[:2]) not in dropped
        )
        + "\n"
    )
    world["vcf"] = plain


def test_617_percentile_is_adjusted_for_the_samples_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    z = (world["expected"] - 1.0) / 2.0
    assert result.details["percentile_status"] == "calibrated"
    assert result.details["z_adjusted"] == pytest.approx(z)
    assert result.metrics["percentile"] == pytest.approx(
        50 * (1 + math.erf(z / math.sqrt(2))), abs=0.05
    )
    calibration = result.details["ancestry_calibration"]
    assert calibration["most_similar_population"] == "EUR"
    assert calibration["calibration_variants_imputed"] == 0
    assert calibration["empirical_reference_samples"] == 20
    assert "pgs_uncalibrated" not in {w.code for w in result.warnings}
    assert "pgs_research_only" in {w.code for w in result.warnings}
    table = next(Path(f.path) for f in result.outputs if f.role == "pgs_table")
    row = next(csv.DictReader(table.open(), delimiter="\t"))
    assert row["percentile_status"] == "calibrated"
    assert row["disclaimer"].startswith("Research use only; not a diagnosis.")


def test_617_percentile_withheld_when_ancestry_is_outside_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _calibrated_world(tmp_path / "genome", monkeypatch, offset_pc=400.0)
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    assert result.details["percentile"] is None
    assert result.details["percentile_status"] == "ancestry_outside_reference"
    assert result.details["ancestry_calibration"]["pcs"][0] == pytest.approx(
        400.0, rel=1e-3
    )
    assert "pgs_uncalibrated" in {w.code for w in result.warnings}


def test_617_missing_reference_scored_variants_withhold_the_percentile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration rows the sample lacks are imputed; too many withhold it."""
    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    # Drop 4 of the 40 calibration sites from the sample's VCF: 10% of the
    # calibrated score's variance is then imputed (the bound is 5%).
    _drop_scored(world, 4, tmp_path / "partial.vcf")
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    assert result.details["percentile"] is None
    assert result.details["percentile_status"] == "calibration_coverage_too_low"
    calibration = result.details["ancestry_calibration"]
    assert calibration["calibration_variants_imputed"] == 4
    assert calibration["imputed_variance_share"] == pytest.approx(0.1)


def test_617_uncalibrated_scoring_file_never_downloads_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> bool:
        raise AssertionError("download attempted")

    monkeypatch.setattr("wgsextract_cli.core.pgs_reference.download_file", no_network)
    result = api.score.pgs(_params(tmp_path, min_match=0.1))
    assert result.details["percentile_status"] == "reference_calibration_unavailable"
    assert result.details["percentile"] is None


def _codes(result: object) -> set[str]:
    return {w.code for w in result.warnings}  # type: ignore[attr-defined]


def test_617_pr925_confident_bed_limits_hom_ref_ancestry_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #925 review: a variants-only VCF's absent ancestry sites outside
    --confident-bed were filled as hom-ref, passing the 90% coverage gate."""
    from tests.ancestry_helpers import calls_vcf

    world = _calibrated_world(tmp_path / "genome", monkeypatch, whole_genome=True)
    world["vcf"] = calls_vcf(
        world["world"], tmp_path / "variants.vcf.gz", variants_only=True
    )
    everywhere = tmp_path / "all.bed"
    everywhere.write_text("".join(f"chr{c}\t0\t40000\n" for c in range(1, 23)))
    half = tmp_path / "half.bed"
    half.write_text("".join(f"chr{c}\t0\t40000\n" for c in range(1, 12)))

    whole = _calibrated(world, tmp_path / "all", confident_bed=everywhere)
    assert whole.exit_code == 0, whole.error
    assert whole.details["ancestry_calibration"]["pca_sites_fraction"] == 1.0
    assert whole.details["percentile_status"] == "calibrated"

    result = _calibrated(world, tmp_path / "half", confident_bed=half)
    assert result.exit_code == 0, result.error
    calibration = result.details["ancestry_calibration"]
    genotypes = world["world"]["genotypes"]
    outside = slice(len(genotypes) // 2, None)
    # Inside the BED every site counts; outside it only the listed variants.
    assert calibration["pca_sites_fraction"] == pytest.approx(
        (len(genotypes) // 2 + int((genotypes[outside] > 0).sum())) / len(genotypes)
    )
    assert result.details["percentile_status"] == "ancestry_projection_incomplete"
    assert result.details["percentile"] is None


def test_617_pr925_sample_filtered_calls_dont_count_toward_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #925 review: calls failing the sample's FORMAT/FT counted toward the
    ancestry PCs and the 90% coverage gate."""
    from tests.ancestry_helpers import calls_vcf

    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    sites = len(world["world"]["sites"])
    # The scored sites are among the first 100; fail a third of the others.
    world["vcf"] = calls_vcf(
        world["world"],
        tmp_path / "ft.vcf.gz",
        variants_only=False,
        failed=frozenset(range(sites - sites // 3, sites)),
    )
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    calibration = result.details["ancestry_calibration"]
    assert calibration["pca_sites_fraction"] == pytest.approx(
        (sites - sites // 3) / sites
    )
    assert result.details["percentile_status"] == "ancestry_projection_incomplete"


@pytest.mark.parametrize(
    ("development", "confidence", "warning"),
    [
        (frozenset({"EUR"}), "medium", None),
        (frozenset({"AFR"}), "low", "pgs_ancestry_mismatch"),
        (None, "low", "pgs_ancestry_unknown"),
    ],
)
def test_617_pr925_development_ancestry_holds_without_catalog_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    development: frozenset[str] | None,
    confidence: str,
    warning: str | None,
) -> None:
    """PR #925 review: a pinned scoring file given by --scoring-file, without
    the Catalog's metadata JSON beside it, stayed `medium` and lost
    pgs_ancestry_mismatch."""
    world = _calibrated_world(tmp_path / "genome", monkeypatch, development=development)
    assert not list(world["scoring"].parent.glob("*.json"))
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    assert result.details["percentile_status"] == "calibrated"
    assert result.details["confidence"] == confidence
    mismatch = {"pgs_ancestry_mismatch", "pgs_ancestry_unknown"}
    assert _codes(result) & mismatch == ({warning} if warning else set())


def test_617_pr925_low_match_skips_the_ancestry_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #925 review: a score already below --min-match still downloaded the
    ancestry reference and reread the VCF for a projection it discarded."""

    def no_network(*args: object, **kwargs: object) -> bool:
        raise AssertionError("ancestry reference download attempted")

    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    _drop_scored(world, 4, tmp_path / "partial.vcf")
    (tmp_path / "genome" / "ancestry" / "wgsextract-ancestry-hgdp1kg-v1.npz").unlink()
    monkeypatch.setattr(
        "wgsextract_cli.core.ancestry_reference.download_file", no_network
    )
    result = _calibrated(world, tmp_path / "out", min_match=0.95)
    assert result.exit_code == 0, result.error
    assert result.details["percentile_status"] == "below_match_threshold"
    assert "pgs_low_match" in _codes(result)


def test_617_pr925_reference_download_failure_is_its_own_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #925 review: a curated score whose reference couldn't be downloaded
    was reported as having no reference distribution at all."""
    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    (tmp_path / "genome" / "pgs" / pgs_reference.NAME).unlink()
    monkeypatch.setattr(
        "wgsextract_cli.core.pgs_reference.download_file", lambda *a, **k: False
    )
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    assert result.details["percentile"] is None
    assert result.details["percentile_status"] == "reference_unavailable"
    assert "Could not download" in result.details["percentile_unavailable_reason"]
    assert "pgs_uncalibrated" in _codes(result)


def test_617_pr925_interval_scales_imputed_variance_by_the_fitted_sd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #925 review: the 95% interval took the imputed share of the summed
    per-site variance as Z_norm2 units, not the imputed rows' variance (with
    the score's LD) over Z_norm2's fitted SD (2 here)."""
    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    _drop_scored(world, 1, tmp_path / "partial.vcf")
    result = _calibrated(world, tmp_path / "out")
    assert result.exit_code == 0, result.error
    assert result.details["percentile_status"] == "calibrated"
    z = result.details["z_adjusted"]
    # Weight 0.1 at frequency 0.5: per-row variance 0.1^2 * 2 * 0.25; the
    # EUR reference scores' variance over the 40 rows' sum is the LD inflation.
    row = 0.1**2 * 0.5
    inflation = float(np.linspace(0.0, 3.9, TRAINING)[:20].var()) / (40 * row)
    error = math.sqrt(row * inflation) / 2
    calibration = result.details["ancestry_calibration"]
    assert calibration["imputation_error_sd"] == pytest.approx(error, rel=1e-3)
    low, high = calibration["percentile_interval"]
    assert low == pytest.approx(
        pgs_reference.normal_percentile(z - 1.96 * error), abs=0.06
    )
    assert high == pytest.approx(
        pgs_reference.normal_percentile(z + 1.96 * error), abs=0.06
    )


def test_617_pr925_score_declares_the_library_folders_it_downloads_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #925 review: first-use reference downloads into the shared library
    weren't declared, so the server could run two such jobs at once."""
    world = _calibrated_world(tmp_path / "genome", monkeypatch)
    params = api.ScorePgsParams(
        input=world["vcf"],
        build="GRCh38",
        scoring_file=world["scoring"],
        ref=world["fasta"],
        outdir=tmp_path / "out",
    )
    library = tmp_path / "genome"
    assert {library / "pgs", library / "ancestry"} <= {
        Path(f).resolve() for f in params.write_folders()
    }
