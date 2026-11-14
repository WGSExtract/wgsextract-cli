import csv
import gzip
import io
import json
import shutil
import subprocess
from dataclasses import replace
from http.client import IncompleteRead
from pathlib import Path
from urllib.response import addinfourl

import pytest

from wgsextract_cli import api
from wgsextract_cli.core.pgs import CURATED
from wgsextract_cli.main import main

FIXTURES = Path(__file__).parent / "fixtures" / "pgs"
pytestmark = pytest.mark.skipif(
    not shutil.which("bcftools") or not shutil.which("samtools"),
    reason="PGS tests need real bcftools and samtools",
)


def _params(outdir: Path, **options: object) -> api.ScorePgsParams:
    return api.ScorePgsParams(
        input=FIXTURES / "sample.vcf",
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
    result = api.score.pgs(_params(tmp_path, ref=_reference(tmp_path)))
    assert result.metrics["score_sum"] == pytest.approx(1.2)
    assert result.details["match_counts"]["reference_block"] == 1
    assert result.details["match_counts"]["missing"] == 2


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


@pytest.mark.parametrize("with_bed", [False, True])
def test_617_sample_filters_exclude_genotypes_and_reference_blocks(
    tmp_path: Path, with_bed: bool
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
            fields[8] = "GT:FT"
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
            confident_bed=bed if with_bed else None,
        )
    )
    assert result.metrics["score_sum"] == pytest.approx(2.0 if with_bed else 0.5)
    assert result.details["match_counts"]["filtered"] == 1
    assert result.details["match_counts"]["overlapping_nonreference"] == 1


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
