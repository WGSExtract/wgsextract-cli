import csv
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_htslib
from wgsextract_cli.api.cohort import CohortSummarizeParams, summarize
from wgsextract_cli.core.results import fingerprint
from wgsextract_cli.core.utils import run_command

pytestmark = needs_htslib


def _binding(path: Path) -> dict[str, str]:
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _vcf(path: Path, name: str, records: str, *, scope: bool = False) -> Path:
    provenance = (
        '##wgseReferenceEvidence=<Schema="wgsextract.reference_evidence/1",'
        'Engine="deepvariant",Version="1",QualityModel="gq",Region="chr1:20-50">\n'
        if scope
        else ""
    )
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##reference=GRCh38\n"
        "##contig=<ID=chr1,length=50>\n"
        "##DeepVariant_version=1.9\n"
        '##ALT=<ID=NON_REF,Description="Unknown alternate">\n'
        '##FILTER=<ID=LowQual,Description="Low quality">\n'
        '##FORMAT=<ID=FT,Number=1,Type=String,Description="Sample filter">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
        '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n'
        + provenance
        + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{name}\n"
        + records,
        encoding="utf-8",
        newline="\n",
    )
    return path


@pytest.fixture()
def cohort(tmp_path):
    ref = tmp_path / "secret-reference.fa"
    ref.write_text(">chr1\n" + "A" * 50 + "\n", encoding="utf-8", newline="\n")
    run_command(["samtools", "faidx", ref])
    manifest = tmp_path / "secret-cohort.json"
    specs = [
        (
            "chr1\t10\tsecret-id\tA\tC,G\t.\tPASS\t.\tGT:DP:GQ\t1|2:20:60\n"
            "chr1\t20\t.\tAA\tCA,A\t.\tPASS\t.\tGT:DP:GQ\t0/1:20:60\n"
            "chr1\t30\t.\tA\tG\t.\tLowQual\t.\tGT:DP:GQ\t0/1:20:50\n"
            "chr1\t40\t.\tA\tG\t.\tPASS\t.\tGT:DP:GQ\t1/.:20:60\n",
            "chr1\t1\t.\tA\t<NON_REF>\t.\tPASS\tEND=9\tGT:MIN_DP:GQ\t0/0:15:50\n"
            "chr1\t16\t.\tA\t<NON_REF>\t.\tPASS\tEND=19\tGT:MIN_DP:GQ\t0/0:15:50\n",
        ),
        (
            "chr1\t10\t.\tA\tT\t.\tPASS\t.\tGT:DP:GQ\t1:15:50\n"
            "chr1\t19\t.\tA\tG\t.\tPASS\t.\tGT:DP:GQ\t0/1:20:50\n"
            "chr1\t20\t.\tA\tC\t.\tPASS\t.\tGT:DP:GQ:FT\t0/0:20:50:LowQual\n",
            "chr1\t15\t.\tA\t<NON_REF>\t.\tPASS\tEND=25\tGT:MIN_DP:GQ\t0/0:12:60\n",
        ),
        (
            "",
            "chr1\t6\t.\tA\t<NON_REF>\t.\tPASS\tEND=15\tGT:MIN_DP:GQ\t0/0:20:60\n",
        ),
        (
            "",
            "chr1\t20\t.\tA\t<NON_REF>\t.\tPASS\tEND=25\tGT:MIN_DP:GQ\t0/0:20:5\n",
        ),
    ]
    rows = []
    for i, (variants, evidence) in enumerate(specs):
        name = f"secret-sample-{i}"
        vcf = _vcf(tmp_path / f"secret-variants-{i}.vcf", name, variants)
        if i == 1:
            vcf.write_text(
                vcf.read_text(encoding="utf-8").replace("chr1", "1"),
                encoding="utf-8",
                newline="\n",
            )
        gvcf = _vcf(
            tmp_path / f"secret-evidence-{i}.g.vcf", name, evidence, scope=i == 3
        )
        compressed = bgzip_index(gvcf)
        rows.append(
            {
                "id": f"secret-person-{i}",
                "sample": name,
                "variants": _binding(vcf),
                "evidence": _binding(Path(compressed)),
            }
        )
    document = {
        "schema": "wgsextract.cohort/1",
        "assembly": "GRCh38",
        "caller": "deepvariant",
        "reference": _binding(ref),
        "reference_index": _binding(ref.with_suffix(".fa.fai")),
        "participants": rows,
    }
    manifest.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    return manifest, document


def test_1288_cohort_counts_reference_missing_other_alt_and_partial(cohort, tmp_path):
    manifest, _ = cohort
    dest = tmp_path / "result"
    log = tmp_path / "run.log"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "wgsextract_cli",
            "cohort",
            "summarize",
            str(manifest),
            "--outdir",
            str(dest),
            "--log-file",
            str(log),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    table = dest / "cohort.variants.tsv"
    with table.open(encoding="utf-8", newline="") as stream:
        rows = {
            (row["pos"], row["alt"]): row
            for row in csv.DictReader(stream, delimiter="\t")
        }
    assert list(rows) == [
        ("10", "C"),
        ("10", "G"),
        ("10", "T"),
        ("19", "G"),
        ("20", "C"),
        ("30", "G"),
        ("40", "G"),
    ]
    for alt in ("C", "G", "T"):
        row = rows["10", alt]
        assert (row["ac"], row["an"], float(row["af"])) == ("1", "5", 0.2)
        assert (
            row["callable_samples"],
            row["reference_called_samples"],
            row["not_assessed_samples"],
        ) == ("3", "1", "1")
        assert (row["n_het"], row["n_hemi"]) == (
            ("0", "1") if alt == "T" else ("1", "0")
        )
    assert rows["20", "C"]["an"] == "2"
    assert rows["19", "G"]["an"] == "4"
    assert rows["19", "G"]["reference_called_samples"] == "1"
    assert rows["20", "C"]["uncallable_samples"] == "2"
    assert rows["20", "C"]["not_assessed_samples"] == "1"
    assert rows["30", "G"]["an"] == "0"
    assert rows["30", "G"]["af"] == ""
    assert rows["40", "G"]["ac"] == "0"
    result = json.loads(proc.stdout)
    assert result["metrics"]["rows"] == len(rows)
    assert result["metrics"]["samples"] == 4
    assert any(
        warning["code"] == "cohort_sensitive_summary" for warning in result["warnings"]
    )
    recorded = Path(result["manifest"]).read_text(encoding="utf-8")
    assert "secret-" not in proc.stdout + proc.stderr + recorded + table.read_text(
        encoding="utf-8"
    )
    assert "secret-" not in log.read_text(encoding="utf-8") + log.with_suffix(
        ".log.provenance.json"
    ).read_text(encoding="utf-8")
    assert not (dest / "cohort.participants.private.json").exists()


def test_1288_cohort_api_bed_parquet_and_opt_in(cohort, tmp_path):
    parquet = pytest.importorskip("pyarrow.parquet")
    manifest, document = cohort
    person = document["participants"][0]
    source = tmp_path / person["variants"]["path"]
    ancestry = tmp_path / "secret-ancestry.json"
    ancestry.write_text(
        json.dumps(
            {
                "command": "qc ancestry",
                "status": "ok",
                "inputs": [
                    {"role": "input", "fingerprint": fingerprint(source)},
                    {
                        "role": "ancestry_reference",
                        "fingerprint": "wgse-fp1:" + "a" * 64,
                    },
                ],
                "metrics": {
                    "ancestry_build": "hg38",
                    "ancestry_reference_version": "v-test",
                    "ancestry_pca_sites_fraction": 0.8,
                    **{f"ancestry_pc{i}": i / 10 for i in range(1, 11)},
                },
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    person["ancestry"] = _binding(ancestry)
    manifest.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    bed = tmp_path / "secret-scope.bed"
    bed.write_text("1\t9\t10\nchr1\t19\t20\n", encoding="utf-8", newline="\n")
    dest = tmp_path / "scoped"
    result = summarize(
        CohortSummarizeParams(
            cohort_manifest=manifest,
            bed=bed,
            outdir=dest,
            table_format="parquet",
            include_identifiers=True,
        )
    )
    data = parquet.read_table(dest / "cohort.variants.parquet")
    assert data["pos"].to_pylist() == [10, 10, 10, 20]
    assert data["an"].to_pylist() == [5, 5, 5, 2]
    assert b"secret-" not in repr(data.schema.metadata).encode()
    opted_in = next(
        item for item in result.outputs if item.role == "private_cohort_participants"
    )
    participants = json.loads(Path(opted_in.path).read_text(encoding="utf-8"))[
        "participants"
    ]
    assert participants[0]["sample"] == "secret-sample-0"
    assert participants[0]["ancestry"]["ancestry_pc10"] == 1.0
    assert participants[1]["ancestry"] is None
    assert result.metrics["ancestry_available"] == 1


@pytest.mark.parametrize(
    ("problem", "expected"),
    [
        ("build", "reference build"),
        ("fingerprint", "sha-256 mismatch"),
        ("duplicate", "duplicate"),
        ("ancestry", "ancestry result"),
        ("ref", "reference/build verification"),
        ("conflict", "conflicting normalized genotype"),
        ("caller", "mixed or mismatched callers"),
        ("remote", "must be local"),
        ("scope", "invalid assessed"),
    ],
)
def test_1288_cohort_refuses_unverifiable_bindings(cohort, tmp_path, problem, expected):
    manifest, document = cohort
    rows = document["participants"]
    if problem == "duplicate":
        rows[1]["id"] = rows[0]["id"]
    elif problem == "remote":
        rows[0]["variants"]["path"] = "https://example.invalid/secret.vcf"
    elif problem == "scope":
        path = _vcf(
            tmp_path / "secret-invalid-interval.vcf",
            rows[3]["sample"],
            "chr1\t1\t.\tA\t<NON_REF>\t.\tPASS\tEND=50\tGT:MIN_DP:GQ\t0/0:20:60\n",
            scope=True,
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace("chr1:20-50", "chr1:20-19"),
            encoding="utf-8",
            newline="\n",
        )
        rows[3]["evidence"] = _binding(path)
    elif problem == "ancestry":
        path = tmp_path / "secret-ancestry.json"
        path.write_text(
            json.dumps(
                {
                    "command": "qc ancestry",
                    "status": "ok",
                    "inputs": [
                        {
                            "role": "input",
                            "fingerprint": fingerprint(
                                tmp_path / rows[1]["variants"]["path"]
                            ),
                        }
                    ],
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        rows[0]["ancestry"] = _binding(path)
    else:
        path = tmp_path / rows[0]["variants"]["path"]
        raw = path.read_text(encoding="utf-8")
        if problem == "conflict":
            raw += raw.splitlines()[-1].replace("1/.:", "1/1:") + "\n"
        elif problem == "ref":
            raw = raw.replace("\tA\tC,G", "\tT\tC,G")
        elif problem == "caller":
            raw = raw.replace("##DeepVariant_version=1.9", "##source=Clair3")
        else:
            raw = raw.replace("GRCh38", "GRCh37")
        path.write_text(
            raw,
            encoding="utf-8",
            newline="\n",
        )
        if problem != "fingerprint":
            rows[0]["variants"] = _binding(path)
    manifest.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    dest = tmp_path / "fail"
    result = summarize(
        CohortSummarizeParams(cohort_manifest=manifest, outdir=dest), check=False
    )
    assert result.exit_code != 0
    assert result.error is not None
    assert expected in result.error.message.lower()
    assert "secret-" not in result.to_json()
    assert not (dest / "cohort.variants.tsv").exists()


def test_1288_cohort_will_not_overwrite_or_retire_a_bound_file(cohort, tmp_path):
    manifest, _ = cohort
    dest = tmp_path / "result"
    dest.mkdir()
    collision = dest / "cohort.variants.card.html"
    try:
        collision.hardlink_to(manifest)
    except OSError:
        pytest.skip("Hard links aren't available on this filesystem.")
    original = manifest.read_bytes()
    result = summarize(
        CohortSummarizeParams(cohort_manifest=manifest, outdir=dest), check=False
    )
    assert result.exit_code == 2
    assert result.error is not None and "must not overwrite" in result.error.message
    assert collision.read_bytes() == manifest.read_bytes() == original
    assert not (dest / "cohort.variants.tsv").exists()


def test_1288_cohort_checks_relatedness_flags_for_renamed_vcf_samples(cohort, tmp_path):
    manifest, document = cohort
    a, b = document["participants"][:2]
    old, b["sample"] = b["sample"], a["sample"]
    vcf_b = tmp_path / b["variants"]["path"]
    vcf_b.write_text(
        vcf_b.read_text(encoding="utf-8").replace(old, b["sample"]),
        encoding="utf-8",
        newline="\n",
    )
    b["variants"] = _binding(vcf_b)
    evidence_b = tmp_path / "secret-renamed.g.vcf"
    with gzip.open(
        tmp_path / b["evidence"]["path"], "rt", encoding="utf-8", newline=""
    ) as source:
        evidence_b.write_text(
            source.read().replace(old, b["sample"]), encoding="utf-8", newline="\n"
        )
    b["evidence"] = _binding(evidence_b)
    vcf_a = tmp_path / a["variants"]["path"]
    old_path = tmp_path / "secret-old-alias.vcf"
    label_a, label_b = f"{old_path.stem}_{a['sample']}", f"{vcf_b.stem}_{b['sample']}"
    samples = tmp_path / "secret-relate.samples.tsv"
    samples.write_text(
        "input\tsample\tsex\tsex_confidence\tcalled_sites_only\n"
        f"{old_path}\t{label_a}\tUnknown\tlow\tfalse\n"
        f"{vcf_b}\t{label_b}\tUnknown\tlow\tfalse\n",
        encoding="utf-8",
        newline="\n",
    )
    pairs = tmp_path / "secret-relate.pairs.tsv"
    pairs.write_text(
        "sample_a\tsample_b\trelationship\tflagged\n"
        f"{label_a}\t{label_b}\tsame person\ttrue\n",
        encoding="utf-8",
        newline="\n",
    )
    relate = tmp_path / "secret-relate.manifest.json"
    relate.write_text(
        json.dumps(
            {
                "command": "qc relate",
                "status": "ok",
                "inputs": [
                    {"role": "input", "path": str(alias), "fingerprint": fingerprint(p)}
                    for alias, p in ((old_path, vcf_a), (vcf_b, vcf_b))
                ],
                "outputs": [
                    {"role": "relate_samples", "fingerprint": fingerprint(samples)},
                    {"role": "relate_pairs", "fingerprint": fingerprint(pairs)},
                ],
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    document["relatedness"] = {
        "result": _binding(relate),
        "samples": _binding(samples),
        "pairs": _binding(pairs),
    }
    manifest.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    result = summarize(
        CohortSummarizeParams(cohort_manifest=manifest, outdir=tmp_path / "result"),
        check=False,
    )
    assert result.error is not None and "duplicate participants" in result.error.message
    assert "secret-" not in result.to_json()
