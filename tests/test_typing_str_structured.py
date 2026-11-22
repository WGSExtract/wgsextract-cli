"""The #1286 repeat table, catalog selection and personal-report contract."""

import csv
import json
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_tools, run_wgsextract, write_fasta
from tests.test_genotyping_commands import EH_RESULT, SEQ, bam, fake_tools, pair
from wgsextract_cli.cli import dispatch
from wgsextract_cli.core.utils import UsageError
from wgsextract_cli.main import main

pytestmark = needs_tools("samtools")


@pytest.fixture(autouse=True)
def fixture_callers_on_windows(monkeypatch):
    if sys.platform != "win32":
        return
    verify_platform = dispatch.verify_platform_support

    def verify(args):
        if args.command != "typing" or args.typing_cmd != "str":
            verify_platform(args)

    # The real STR callers are unavailable; these use fixture executables.
    monkeypatch.setattr(dispatch, "verify_platform_support", verify)


@pytest.fixture
def sample(tmp_path):
    path = bam(tmp_path / "s.bam", pair("p", "chr1", 1000))
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": "A" * 3000})
    return ["-i", path, "--ref", str(ref), "--sex", "male", "--threads", "1"]


def _manifest(out):
    [path] = out.glob("*.manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _output(result, role):
    [file] = [row["path"] for row in result["outputs"] if row["role"] == role]
    return Path(file)


def _assert_parity(tsv_path, file, manifest):
    pq = pytest.importorskip("pyarrow.parquet")
    pa = pytest.importorskip("pyarrow")
    result = manifest["details"]["repeat_calls"]
    arrow = pq.read_table(file)
    assert arrow.to_pylist() == result
    for name in ("allele_1", "allele_2", "ci_2_high", "allele_2_reads"):
        assert pa.types.is_int64(arrow.schema.field(name).type)
    assert pa.types.is_float64(arrow.schema.field("coverage").type)
    with tsv_path.open(encoding="utf-8") as stream:
        tsv = list(csv.DictReader(stream, delimiter="\t"))
    assert len(tsv) == len(result)
    for text, typed in zip(tsv, result, strict=True):
        assert text.keys() == typed.keys()
        for key, value in typed.items():
            if value is None:
                assert text[key] == (
                    "-" if key in {"normal_max", "pathogenic_min"} else ""
                )
            elif isinstance(value, (int, float)):
                assert float(text[key]) == value
            else:
                assert text[key] == value
    return arrow.schema.metadata


def test_1286_expansionhunter_uses_selected_catalog_and_preserves_alleles(
    tmp_path, sample, monkeypatch, capsys
):
    template = tmp_path / "caller.json"
    template.write_text(json.dumps(EH_RESULT))
    catalog = tmp_path / "another_workspace" / "catalog.json"
    catalog.parent.mkdir()
    rows = [
        {"LocusId": "HTT", "VariantId": ["HTT", "HTT_CCG"],
         "ReferenceRegion": ["chr4:3074876-3074933", "chr4:3074939-3074966"],
         "LocusStructure": "(CAG)*CAACAG(CCG)*"},
        {"LocusId": "DIP2B"},
        {"LocusId": "FMR1"},
        {"LocusId": "DMPK", "LocusStructure": "(CTG)*"},
        {"LocusId": "RFC1", "LocusStructure": "(AAAAG)*"},
    ]  # fmt: skip
    catalog.write_text(json.dumps(rows))
    fake_tools(
        monkeypatch,
        tmp_path,
        {
            "ExpansionHunter": f"""
selected = json.loads(pathlib.Path(opt("--variant-catalog")).read_text())
original = json.loads(pathlib.Path({str(template)!r}).read_text())
calls = original["LocusResults"]
result = {{row["LocusId"]: calls[row["LocusId"]] for row in selected if row["LocusId"] in calls}}
prefix = opt("--output-prefix")
pathlib.Path(prefix + ".json").write_text(json.dumps({{"LocusResults": result, "Catalog": selected}}))
pathlib.Path(prefix + ".vcf").write_text("##fileformat=VCFv4.2\\n")
"""
        },
    )
    out = tmp_path / "results"
    base = [
        "typing", "str", *sample, "-o", str(out), "--catalog", str(catalog),
    ]  # fmt: skip
    main(base)
    original_manifest = _manifest(out)
    uncalled = next(
        row
        for row in original_manifest["details"]["repeat_calls"]
        if row["locus"] == "RFC1"
    )
    assert (uncalled["status"], uncalled["allele_1"], uncalled["coverage"]) == (
        "no call",
        None,
        3.0,
    )
    original_tsv = _output(original_manifest, "repeat_summary")
    original_contents = original_tsv.read_text()
    opts = [*base, "--loci", "FMR1, HTT,FMR1,DMPK,RFC1"]
    capsys.readouterr()
    main([*opts, "--json"])
    tsv_manifest = json.loads(capsys.readouterr().out)
    native = json.loads(_output(tsv_manifest, "expansionhunter_report").read_text())
    tsv = _output(tsv_manifest, "repeat_summary")
    assert tsv != original_tsv
    assert original_tsv.read_text() == original_contents
    assert Path(original_manifest["manifest"]).is_file()
    assert native["Catalog"] == [rows[0], rows[2], rows[3], rows[4]]
    alleles = {row["locus"]: row for row in tsv_manifest["details"]["repeat_calls"]}
    assert alleles["HTT"]["allele_1"] == 19 and alleles["HTT"]["allele_2"] == 42
    assert (alleles["HTT"]["ci_2_low"], alleles["HTT"]["ci_2_high"]) == (39, 45)
    assert alleles["FMR1"]["allele_1"] == 30 and alleles["FMR1"]["allele_2"] is None
    assert alleles["FMR1"]["status"] == "normal"
    assert alleles["HTT_CCG"]["allele_2"] == 10
    assert alleles["HTT_CCG"]["pathogenic_min"] is None
    assert alleles["DMPK"]["status"] == "no call"
    assert alleles["DMPK"]["allele_1"] is None
    assert alleles["RFC1"]["status"] == "no call"
    assert alleles["RFC1"]["coverage"] == 3.0
    assert set(alleles) == {"HTT", "HTT_CCG", "FMR1", "DMPK", "RFC1"}

    report_dir = tmp_path / "report"
    main(["report", "-i", sample[1], "--outdir", str(report_dir)])
    report = json.loads(
        (report_dir / "s_personal_report.json").read_text(encoding="utf-8")
    )
    section = report["sections"]["repeat_expansions"]
    assert "1 flagged, 0 unconfirmed, 1 normal" in section["summary"]
    [finding] = section["findings"]
    assert finding["label"] == "HTT (flagged)"
    assert "19/42 repeat units" in finding["value"]

    other = tmp_path / "other_report"
    main(["report", "--workspace", str(catalog.parent), "--results", str(out),
          "--outdir", str(other)])  # fmt: skip
    [other_report] = other.glob("*_personal_report.json")
    assert (
        json.loads(other_report.read_text())["sections"]["repeat_expansions"]["status"]
        == "not_run"
    )

    pytest.importorskip("pyarrow")
    capsys.readouterr()
    parquet_out = tmp_path / "parquet"
    parquet_opts = [str(parquet_out) if item == str(out) else item for item in opts]
    main([*parquet_opts, "--format", "parquet", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert (
        result["details"]
        == tsv_manifest["details"]
        == _manifest(parquet_out)["details"]
    )
    metadata = _assert_parity(tsv, _output(result, "repeat_summary"), result)
    assert metadata[b"command"] == b"typing str"
    assert metadata[b"reference_build"] == b"hg38"
    assert metadata[b"input_fingerprint"]
    assert metadata[b"input_2_fingerprint"]
    assert {"repeat_summary", "expansionhunter_report", "repeat_calls"} <= {
        row["role"] for row in result["outputs"]
    }


@pytest.mark.parametrize("caller", ["expansionhunter", "longtr", "strkit"])
def test_1286_unknown_locus_is_rejected_before_running_caller(tmp_path, sample, caller):
    catalog = tmp_path / (
        "catalog.json" if caller == "expansionhunter" else "catalog.bed"
    )
    catalog.write_text(
        {
            "expansionhunter": '[{"LocusId":"HTT"}]',
            "longtr": "chr1\t1001\t1060\tCAG\tHTT\n",
            "strkit": "chr1\t1000\t1060\tMOTIF=CAG;ID=HTT\n",
        }[caller]
    )
    out = tmp_path / "results"
    with pytest.raises(UsageError, match="Unknown.*MISSING"):
        run_wgsextract(
            "typing", "str", *sample, "-o", str(out), "--catalog",
            str(catalog), "--caller", caller, "--loci", "HTT,MISSING",
        )  # fmt: skip
    assert not list(out.glob("s.str*"))


@pytest.mark.parametrize("caller", ["longtr", "strkit"])
@needs_tools("bcftools")
def test_1286_bed_subset_and_unconfirmed_report(tmp_path, sample, monkeypatch, caller):
    catalog = tmp_path / "loci.bed"
    loci = [("FXN", 1000), ("DMPK", 1200), ("HTT_CCG", 1400), ("ATXN2", 1600)]
    vcf_header = (
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=3000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts\n"
    )
    if caller == "longtr":
        catalog.write_text(
            "".join(f"chr1\t{n + 1}\t{n + 60}\tGAA\t{name}\n" for name, n in loci)
        )
        longtr_header = vcf_header.splitlines(keepends=True)
        longtr_header[2:2] = [
            f'##{kind}=<ID={field},Number=1,Type={type_},Description="{field}">\n'
            for kind, field, type_ in (
                ("INFO", "START", "Integer"), ("INFO", "END", "Integer"),
                ("INFO", "MOTIF", "String"), ("INFO", "PERIOD", "Integer"),
                ("FORMAT", "GB", "String"), ("FORMAT", "DP", "Integer"),
                ("FORMAT", "MALLREADS", "String"),
            )
        ]  # fmt: skip
        script = f"""
vcf = {"".join(longtr_header)!r}
for row in pathlib.Path(opt("--regions")).read_text().splitlines():
    chrom, start, end, motif, name = row.split("\\t")
    vcf += f"{{chrom}}\\t{{start}}\\t{{name}}\\tA\\tC\\t.\\tPASS\\tSTART={{start}};END={{end}};MOTIF={{motif}};PERIOD=3\\tGB:DP:MALLREADS\\t-33|120:28:-33|27;120|1\\n"
subprocess.run(["bcftools", "view", "-Oz", "-o", opt("--tr-vcf")], input=vcf, text=True, check=True)
"""
    else:
        catalog.write_text(
            "".join(f"chr1\t{n}\t{n + 60}\tMOTIF=GAA;ID={name}\n" for name, n in loci)
        )
        script = """
calls = []
for row in pathlib.Path(opt("--loci")).read_text().splitlines():
    chrom, start, end, fields = row.split("\\t")
    name = fields.split("ID=")[1]
    call = [9] if name == "FXN" else [9, 60]
    counts = [27] if name == "FXN" else [27, 1]
    calls.append({"locus_id": name, "contig": chrom, "start": int(start), "end": int(end),
                  "motif": "GAA", "call": call, "peaks": {"n_reads": counts}, "reads": [0]*28})
pathlib.Path(opt("--json")).write_text(json.dumps({"results": calls}))
pathlib.Path(opt("--vcf")).write_text("##fileformat=VCFv4.2\\n")
"""
    fake_tools(
        monkeypatch, tmp_path, {"LongTR" if caller == "longtr" else caller: script}
    )
    out = tmp_path / "results"
    opts = [
        "typing", "str", *sample, "-o", str(out), "--catalog", str(catalog),
        "--caller", caller, "--loci", "HTT_CCG,FXN",
    ]  # fmt: skip
    main(opts)
    result = _manifest(out)
    tsv = _output(result, "repeat_summary")
    if caller == "strkit":
        native = json.loads(_output(result, "strkit_report").read_text())
        assert [row["locus_id"] for row in native["results"]] == ["FXN", "HTT_CCG"]
    else:
        import gzip

        with gzip.open(_output(result, "repeat_calls"), "rt") as stream:
            assert [
                row.split("\t")[2] for row in stream if not row.startswith("#")
            ] == [
                "FXN",
                "HTT_CCG",
            ]
    fxn, flank = result["details"]["repeat_calls"]
    assert fxn["allele_1"] == 9 and fxn["allele_1_reads"] == 27
    assert fxn["confidence"] == "low"
    assert flank["allele_2"] == 60 and flank["allele_2_reads"] == 1
    assert fxn["allele_2"] == (None if caller == "strkit" else 60)
    assert fxn["status"].startswith("unresolved" if caller == "strkit" else "normal")

    report_dir = tmp_path / "report"
    main(["report", "-i", sample[1], "--outdir", str(report_dir)])
    report = json.loads(
        (report_dir / "s_personal_report.json").read_text(encoding="utf-8")
    )
    section = report["sections"]["repeat_expansions"]
    assert section["status"] == "ok"
    assert "0 flagged, 1 unconfirmed, 0 normal" in section["summary"]
    [finding] = section["findings"]
    assert finding["label"] == "FXN (unconfirmed)" and finding["confidence"] == "low"
    assert "28x" in finding["detail"]
    assert "not diagnosis" in finding["disclaimer"].lower()
    assert "Repeat Expansions" in (report_dir / "s_personal_report.md").read_text(
        encoding="utf-8"
    )
    assert "Repeat Expansions" in (report_dir / "s_personal_report.html").read_text(
        encoding="utf-8"
    )

    pytest.importorskip("pyarrow")
    main([*opts, "--format", "parquet"])
    manifest = _manifest(out)
    _assert_parity(tsv, _output(manifest, "repeat_summary"), manifest)


def test_1286_clipped_custom_flank_does_not_add_a_disease_flag(
    tmp_path, sample, monkeypatch
):
    fake_tools(
        monkeypatch,
        tmp_path,
        {
            "strkit": """
pathlib.Path(opt("--json")).write_text('{"results":[]}')
pathlib.Path(opt("--vcf")).write_text("##fileformat=VCFv4.2\\n")
"""
        },
    )
    clipped = bam(
        tmp_path / "clips.bam",
        [
            f"clip{i}\t0\tchr1\t1001\t60\t100S100M\t*\t0\t0\t{SEQ * 2}\t{'I' * 200}"
            for i in range(3)
        ],
    )
    catalog = tmp_path / "clips.bed"
    catalog.write_text(
        "chr1\t1000\t1060\tID=HTT;MOTIF=CAG\nchr1\t1040\t1100\tID=HTT_CCG;MOTIF=CCG\n"
    )
    out = tmp_path / "results"
    main([
        "typing", "str", sample[0], clipped, *sample[2:],
        "--caller", "strkit", "--catalog", str(catalog), "--outdir", str(out),
    ])  # fmt: skip
    result = _manifest(out)
    assert result["metrics"]["flagged_repeats"] == 1
    assert {(w["code"], w["details"]["locus"]) for w in result["warnings"]} == {
        ("repeat_outside_normal_range", "HTT"),
        ("repeat_clipped_reads", "HTT_CCG"),
    }
    assert {
        row["locus"]: row["clipped_reads"] for row in result["details"]["repeat_calls"]
    } == {"HTT": 3, "HTT_CCG": 3}
