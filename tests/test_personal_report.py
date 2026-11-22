"""``wgsextract report``: a sample's results as one local report.

Sections follow which runs exist (missing ones say how to run them), findings
keep their confidence labels and always carry evidence and the "technical
result, not diagnosis" disclaimer, the JSON follows its schema, and rendering
is offline and keeps the sample's absolute paths out of the documents."""

import gzip as gzip
import hashlib as hashlib
import io as io
import json as json
import os as os
import shutil as shutil
import socket as socket
import sys as sys
from contextlib import redirect_stdout as redirect_stdout
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from html.parser import HTMLParser as HTMLParser
from pathlib import Path as Path
from typing import Any as Any
from unittest.mock import patch as patch

import jsonschema as jsonschema
import pytest as pytest
from markdown_it import MarkdownIt as MarkdownIt

from tests.htslib_helpers import bgzip_index as bgzip_index
from tests.htslib_helpers import needs_htslib as needs_htslib
from tests.htslib_helpers import needs_tools as needs_tools
from tests.htslib_helpers import write_fasta as write_fasta
from wgsextract_cli import api as api
from wgsextract_cli.core import gene_lists as gene_lists
from wgsextract_cli.core import personal_report as pr
from wgsextract_cli.core.config import get_config_path as get_config_path
from wgsextract_cli.core.config import reload_settings as reload_settings
from wgsextract_cli.core.config import settings as settings
from wgsextract_cli.core.sample_workspace import SampleWorkspace as SampleWorkspace
from wgsextract_cli.core.utils import UsageError as UsageError
from wgsextract_cli.core.utils import run_command as run_command
from wgsextract_cli.main import main as main

TSV_HEADER = (
    "# ClinVar pathogenic/likely pathogenic calls: wgsextract confidence labels\n"
    "chrom\tpos\tref\talt\tgenotype\tzygosity\tconfidence\treasons\tdepth\t"
    "genotype_quality\talt_fraction\tCLNSIG\tCLNDN\n"
)


# Three genes in the built-in lists' format, with their NCBI RefSeq spans.
MINI_LIST = (
    "# A test gene list\n"
    "gene\tcategory\tinheritance\thg19_chrom\thg19_start\thg19_end\thg38_chrom\t"
    "hg38_start\thg38_end\tnote\n"
    "BRCA2\tcancer\tAD\tchr13\t32889645\t32974405\tchr13\t32315077\t32400268\t\n"
    "MUTYH\tcancer\tAR\tchr1\t45794914\t45806112\tchr1\t45329242\t45340440\t"
    "two P/LP variants (biallelic) are reportable\n"
    "PLN\tcardiovascular\tAD\tchr6\t118869459\t118882879\tchr6\t118548296\t"
    "118561716\t\n"
)
SPANS = {"BRCA2": ("chr13", 32315077, 32400268), "MUTYH": ("chr1", 45329242, 45340440)}


@dataclass
class ServedLists:
    library: Path
    body: bytes = MINI_LIST.encode()
    online: bool = True
    downloads: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def served_lists(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> ServedLists:
    """Every built-in gene list is MINI_LIST, served by a counted download
    into a fresh reference library."""
    served = ServedLists(tmp_path_factory.mktemp("library"))
    # In config.toml too, which the CLI reloads.
    config = get_config_path()
    config.write_text(
        "allow_reference_downloads = true\n"
        + config.read_text()
        + f"reference_library = {json.dumps(str(served.library))}\n"
    )
    reload_settings()
    pinned = {
        "genes": {"BRCA2": "cancer", "MUTYH": "cancer", "PLN": "cardiovascular"},
        "sha256": hashlib.sha256(MINI_LIST.encode()).hexdigest(),
    }
    lists = {
        list_id: {
            **meta,
            **pinned,
            "download": f"https://example.invalid/{list_id}.tsv",
        }
        for list_id, meta in gene_lists.MANIFEST["lists"].items()
    }
    monkeypatch.setitem(gene_lists.MANIFEST, "lists", lists)

    def download(url: str, dest: str, **_kwargs) -> bool:
        if not served.online:
            return False
        served.downloads.append(url)
        Path(dest).write_bytes(served.body)
        return True

    # The network is the boundary: serve the list locally.
    monkeypatch.setattr(gene_lists, "download_file", download)
    return served


def _mid(symbol: str) -> tuple[str, int]:
    chrom, start, end = SPANS[symbol]
    return chrom, (start + end) // 2


def _touch(path: Path, text: str = "x") -> Path:
    path.write_text(text)
    return path


def _manifest(
    folder: Path,
    name: str,
    command: str,
    *,
    inputs: tuple[tuple[Path, str], ...] = (),
    outputs: tuple[tuple[Path, str], ...] = (),
    metrics: dict[str, Any] | None = None,
    warnings: tuple[tuple[str, str], ...] = (),
    status: str = "ok",
    finished: str = "2025-01-01T00:00:00+00:00",
    error: str = "tool crashed",
) -> Path:
    path = folder / f"{name}.manifest.json"
    document = {
        "schema": "wgsextract.result/1",
        "command": command,
        "status": status,
        "exit_code": 0 if status == "ok" else 1,
        "error": None
        if status == "ok"
        else {"type": "WGSExtractError", "message": error},
        "inputs": [{"path": str(p), "role": r} for p, r in inputs],
        "outputs": [{"path": str(p), "role": r, "metrics": {}} for p, r in outputs],
        "metrics": metrics or {},
        "warnings": [{"code": c, "message": m, "details": {}} for c, m in warnings],
        "details": None,
        "manifest": str(path),
        "provenance": {"started": finished, "finished": finished},
    }
    path.write_text(json.dumps(document))
    return path


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    """A sample folder after info-less runs: mtDNA lineage, ClinVar and Cyrius."""
    folder = tmp_path / "HG002"
    folder.mkdir()
    (folder / "genome-config.toml").write_text("")  # a genome-library folder
    cram = _touch(folder / "HG002.cram")
    vcf = _touch(folder / "HG002.vcf.gz")
    workspace = SampleWorkspace(folder)
    workspace.record("sex", cram, "Male", method="chrY reads", confidence="high")
    workspace.record("build", vcf, "hg38", method="VCF header", confidence="high")

    haplogrep = _touch(folder / "haplogrep_results.txt")
    _manifest(
        folder,
        "haplogrep",
        "lineage mt-haplogroup",
        inputs=((cram, "input"),),
        outputs=((haplogrep, "haplogroup_report"),),
        metrics={"haplogroup": "H1a", "haplogroup_quality": 0.95},
    )

    brca2, mutyh = _mid("BRCA2"), _mid("MUTYH")
    rows = [
        (*brca2, "C", "T", "0/1", "het", "high", "", 34, 99, 0.5, "Pathogenic", "HBOC"),
        (
            *mutyh,
            "G",
            "A",
            "0/1",
            "het",
            "low",
            "low_depth|low_gq",
            6,
            12,
            0.33,
            "Likely_pathogenic",
            "MAP",
        ),  # fmt: skip
        (
            "chr1",
            1000,
            "A",
            "G",
            "0/1",
            "het",
            "moderate",
            "moderate_depth",
            15,
            40,
            0.4,
            "Pathogenic",
            "Elsewhere",
        ),  # fmt: skip
        (
            *brca2,
            "C",
            "G",
            "0/1",
            "het",
            "high",
            "",
            30,
            99,
            0.5,
            "Conflicting_classifications_of_pathogenicity",
            "x",
        ),  # fmt: skip
    ]
    table = folder / "clinvar_pathogenic.confidence.tsv"
    table.write_text(TSV_HEADER + "".join("\t".join(map(str, r)) + "\n" for r in rows))
    _manifest(
        folder,
        "clinvar_pathogenic.vcf",
        "vcf clinvar",
        inputs=((vcf, "input"),),
        outputs=(
            (_touch(folder / "clinvar_pathogenic.vcf.gz"), "clinvar_pathogenic"),
            (table, "confidence_table"),
        ),
    )

    calls = _touch(folder / "HG002_cyp2d6.tsv")
    _manifest(
        folder,
        "HG002_cyp2d6",
        "typing cyp2d6",
        inputs=((cram, "input"),),
        outputs=((calls, "cyp2d6_calls"),),
        metrics={
            "cyp2d6_genotype": "*1/*2",
            "cyp2d6_filter": "PASS",
            "cyp2d6_call_status": "resolved",
            "median_depth": 31.0,
        },
    )
    return folder


def _cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with redirect_stdout(out), patch.object(sys, "argv", ["wgsextract", *argv]):
        try:
            main()
        except SystemExit as e:
            code = int(e.code or 0)
    return code, out.getvalue()


def test_sections_follow_the_runs_and_missing_ones_say_how_to_run(sample):
    report = pr.build_report(sample)
    sections = report["sections"]
    assert {name: s["status"] for name, s in sections.items()} == {
        "overview": "ok",  # workspace facts, though info has not run
        "lineage": "ok",
        "ancestry": "not_run",
        "pharmacogenomics": "ok",
        "carrier_screening": "not_run",
        "clinvar": "ok",
        "exon_callability": "not_run",
        "structural_variants": "not_run",
        "microarray": "not_run",
    }
    hints = {n: [m["command"] for m in s["missing"]] for n, s in sections.items()}
    assert hints["overview"] == [
        "wgsextract info --detailed -i HG002.cram",
        "wgsextract qc contamination -i HG002.cram",  # #616
    ]
    assert hints["lineage"] == ["wgsextract lineage y-haplogroup -i HG002.cram"]
    assert hints["pharmacogenomics"] == [
        "wgsextract vcf pharmcat --vcf-input HG002.vcf.gz",
        "wgsextract typing hla -i HG002.cram",
    ]
    assert hints["carrier_screening"] == ["wgsextract typing smn -i HG002.cram"]
    assert hints["microarray"] == ["wgsextract microarray -i HG002.cram"]
    assert hints["clinvar"] == []
    assert hints["exon_callability"] == [  # #1177
        "wgsextract vcf snp -i HG002.cram --reference-evidence gvcf",
        "wgsextract vcf callability --vcf-input HG002.vcf.gz",
    ]
    assert hints["structural_variants"] == [  # #618
        "wgsextract vcf sv -i HG002.cram, then "
        "wgsextract vcf sv-freq --vcf-input sv.vcf.gz"
    ]
    sex = next(f for f in sections["overview"]["facts"] if f["name"] == "sex")
    assert (sex["value"], sex["method"], sex["confidence"]) == (
        "Male",
        "chrY reads",
        "high",
    )
    assert report["sample"]["name"] == "HG002"


def test_y_dna_is_not_asked_for_a_female_sample(sample):
    SampleWorkspace(sample).set_overrides({"sex": "Female"}, None)
    lineage = pr.build_report(sample)["sections"]["lineage"]
    assert lineage["missing"] == []
    assert "recorded sex Female" in lineage["summary"]


def test_clinvar_lists_gene_list_hits_with_their_call_confidence(sample):
    clinvar = pr.build_report(sample)["sections"]["clinvar"]
    hits = {f["gene"]: f for f in clinvar["findings"]}
    assert set(hits) == {"BRCA2", "MUTYH"}
    assert hits["BRCA2"]["confidence"] == "high"
    assert hits["MUTYH"]["confidence"] == "low"
    assert hits["MUTYH"]["confidence_reasons"] == ["low_depth", "low_gq"]
    assert "biallelic" in hits["MUTYH"]["note"]
    assert hits["MUTYH"]["variant"]["depth"] == 6
    chrom, pos = _mid("MUTYH")
    assert hits["MUTYH"]["evidence"] == {
        "file": "clinvar_pathogenic.vcf.gz",
        "region": f"{chrom}:{pos}",
        "manifest": "clinvar_pathogenic.vcf.manifest.json",
    }
    # Conflicting classifications are not P/LP; chr1:1000 is outside the list.
    assert clinvar["counts"] == {
        "carried": 3,
        "in_gene_list": 2,
        "outside_gene_list": 1,
        "unplaced": 0,
        "high": 1,
        "moderate": 0,
        "low": 1,
    }
    assert clinvar["all_calls"] == "clinvar_pathogenic.confidence.tsv"


# Genes per category in each ACMG SF version's Table 1 (v3.2: Miller et al.
# 2023, PMID 37347242, https://pmc.ncbi.nlm.nih.gov/articles/PMC10524344/;
# v3.3: Lee et al. 2025, PMID 40568962,
# https://pmc.ncbi.nlm.nih.gov/articles/PMC12318660/).
ACMG_SF_TABLE_1 = {
    "3.2": {"cancer": 28, "cardiovascular": 40, "metabolism": 4, "miscellaneous": 9},
    "3.3": {"cancer": 28, "cardiovascular": 41, "metabolism": 7, "miscellaneous": 8},
}


# Genotype and zygosity column of each kind of call.
GENOTYPES = {
    "het": ("0/1", "het"),
    "hom": ("1/1", "hom"),
    "haploid": ("1", "haploid"),
    "partial": ("1/.", "het"),
}


def _clinvar_table(folder: Path, *calls: tuple[Any, ...]) -> None:
    """Replace the sample's ClinVar calls with (chrom, pos, CLNSIG, GENEINFO
    [, kind of call [, confidence]]); heterozygous and high unless given."""
    table = folder / "clinvar_pathogenic.confidence.tsv"
    rows = []
    for chrom, pos, sig, info, *more in calls:
        kind = more[0] if more else "het"
        label = more[1] if len(more) > 1 else "high"
        genotype, zygosity = GENOTYPES[kind]
        rows.append(
            f"{chrom}\t{pos}\tC\tT\t{genotype}\t{zygosity}\t{label}\t\t30\t99\t0.5\t"
            f"{sig}\tx\t{info}\n"
        )
    table.write_text(TSV_HEADER.replace("CLNDN\n", "CLNDN\tGENEINFO\n") + "".join(rows))


def _bgzipped_vcf(path: Path, info: str, rows: list[str]) -> str:
    """A one-sample hg38 chr1 VCF; each row is space-separated from POS on."""
    path.with_suffix("").write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=248956422>\n"
        + info
        + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        + "".join("chr1\t" + "\t".join(r.split()) + "\n" for r in rows)
    )
    run_command(["bgzip", "-f", str(path.with_suffix(""))])
    run_command(["tabix", "-f", "-p", "vcf", str(path)])
    return str(path)


# Public MUTYH P/LP variants on GRCh38 (issue #840's reproduction).
MUTYH_CALLS = ((45329439, "T", "A"), (45330515, "C", "A"))


def _phased_mutyh_report(
    tmp_path: Path, calls: list[str], calls_from: str = "table"
) -> tuple[dict, str]:
    """Run vcf clinvar and report (CLI) on a one-sample GRCh38 VCF of MUTYH
    calls, each "POS REF ALT FORMAT VALUES" (DP30, GQ99, AD15,15 added),
    every ALT of them a ClinVar P/LP MUTYH record; MUTYH's report gene and
    the HTML page's text. With ``calls_from`` "manifest" the report reads the
    run's manifest findings (its confidence table gone), "truncated" too but
    as if the manifest listed only some."""
    header = "".join(
        f"##FORMAT=<ID={tag},Number={number},Type={kind},Description=\"{tag}\">\n"
        for tag, number, kind in (("GT", 1, "String"), ("PS", 1, "Integer"),
                                  ("DP", 1, "Integer"), ("GQ", 1, "Integer"),
                                  ("AD", "R", "Integer"))
    )  # fmt: skip
    rows = []
    for call in calls:
        pos, ref, alt, keys, values = call.split()
        ad = ",".join(["15"] * (alt.count(",") + 2))
        rows.append(
            f"{pos} . {ref} {alt} 50 PASS . {keys}:DP:GQ:AD {values}:30:99:{ad}"
        )
    vcf = _vcf_on_1(tmp_path / "HG002.vcf.gz", header, rows)
    info = "".join(
        f'##INFO=<ID={tag},Number=.,Type=String,Description="{tag}">\n'
        for tag in ("CLNSIG", "CLNDN", "GENEINFO")
    )
    known = {
        (c.split()[0], c.split()[1], a) for c in calls for a in c.split()[2].split(",")
    }
    clinvar = _vcf_on_1(
        tmp_path / "clinvar.vcf.gz",
        info + '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n',
        [f"{pos} {i} {ref} {alt} . . CLNSIG=Pathogenic;CLNDN=MAP;GENEINFO=MUTYH:4595 GT 0/1"
         for i, (pos, ref, alt) in enumerate(sorted(known))],
    )  # fmt: skip
    results, out = tmp_path / "clinvar", tmp_path / "report"
    from tests.htslib_helpers import reference_for_vcf

    reference = reference_for_vcf(tmp_path / "ref.fa.gz", vcf, clinvar)
    code, log = _cli("vcf", "clinvar", "--vcf-input", vcf, "--clinvar-file", clinvar,
                     "--outdir", str(results), "--ref", str(reference))  # fmt: skip
    assert code == 0, log
    if calls_from != "table":
        (manifest,) = results.glob("clinvar_pathogenic*.manifest.json")
        document = json.loads(manifest.read_text())
        document["outputs"] = [
            o for o in document["outputs"] if o["role"] != "confidence_table"
        ]
        if calls_from == "truncated":
            for entry in document["details"]["confidence"]:
                entry["findings_truncated"] = True
        manifest.write_text(json.dumps(document))
    code, log = _cli("report", "--input", vcf, "--results", str(results),
                     "--outdir", str(out))  # fmt: skip
    assert code == 0, log
    (document,) = out.glob("*_personal_report.json")
    report = json.loads(document.read_text())
    jsonschema.validate(report, pr.load_schema())
    (mutyh,) = report["sections"]["clinvar"]["genes"]
    page = _Page()
    page.feed(next(out.glob("*_personal_report.html")).read_text())
    return {**mutyh, "summary": report["sections"]["clinvar"]["summary"]}, " ".join(
        page.text
    )


def _vcf_on_1(path: Path, header: str, rows: list[str]) -> str:
    """A bgzipped GRCh38 VCF on contig 1; each row is space-separated from POS on."""
    path.with_suffix("").write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=1,length=248956422>\n"
        + header
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        + "".join("1\t" + "\t".join(r.split()) + "\n" for r in rows)
    )
    run_command(["bgzip", "-f", str(path.with_suffix(""))])
    run_command(["tabix", "-f", "-p", "vcf", str(path)])
    return str(path)


HOSTILE = '<img src=x onerror="alert(1)">&amp;[x](javascript:alert(1))|`*_'


class _Page(HTMLParser):
    """The tags, attributes, links and visible text of an HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attributes: set[str] = set()
        self.hrefs: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        self.attributes.update(name for name, _ in attrs)
        self.hrefs += [value or "" for name, value in attrs if name == "href"]

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _assert_shown_not_rendered(page_html: str, tags: set[str]) -> None:
    page = _Page()
    page.feed(page_html)
    assert page.tags <= tags
    assert not {a for a in page.attributes if a.startswith("on")}
    assert not [h for h in page.hrefs if h.lower().startswith("javascript:")]
    assert HOSTILE in "".join(page.text)


def _hostile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _hostile(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_hostile(v) for v in value]
    return HOSTILE if isinstance(value, str) else value


def test_report_needs_a_sample():
    with pytest.raises(api.UsageError):
        api.ReportParams()


def _two_people(folder: Path) -> Path:
    """Alice's and Bob's runs side by side in a plain folder."""
    folder.mkdir()
    for person, haplogroup in (("Alice", "H1a"), ("Bob", "U5b")):
        cram = _touch(folder / f"{person}.cram")
        _manifest(
            folder,
            f"{person}_haplogrep",
            "lineage mt-haplogroup",
            inputs=((cram, "input"),),
            outputs=((_touch(folder / f"{person}_mt.txt"), "haplogroup_report"),),
            metrics={"haplogroup": haplogroup, "haplogroup_quality": 0.95},
            finished="2025-01-02T00:00:00+00:00" if person == "Bob" else "2025-01-01",
        )
    return folder


def _overview_contamination(report: dict) -> tuple[Any, set[str]]:
    overview = report["sections"]["overview"]
    value = next(
        (f["value"] for f in overview["facts"] if f["name"] == "contamination"), None
    )
    return value, {w["code"] for w in overview["warnings"]}


def _acmg_run(
    folder: Path, *, samples: tuple[str, ...] = ("S",), mutyh_info: str = ""
) -> Path:
    """A `vcf acmg` run (issue 629) on the sample's VEP VCF: a carried LP in
    MUTYH and P elsewhere, a carried VUS, and an LP the sample doesn't carry.
    Every sample has the same genotypes; ``mutyh_info`` is added to the MUTYH
    LP's INFO. Returns the classified VCF."""
    brca2, mutyh = _mid("BRCA2"), _mid("MUTYH")
    csq = "Allele|Consequence|IMPACT|SYMBOL|HGVSc|HGVSp|CANONICAL"
    lines = [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr1,length=248956422>",
        "##contig=<ID=chr13,length=114364328>",
        f'##INFO=<ID=CSQ,Number=.,Type=String,Description="VEP. Format: {csq}">',
        '##INFO=<ID=ACMG_CLASS,Number=A,Type=String,Description="Research only">',
        '##INFO=<ID=ACMG_CRITERIA,Number=A,Type=String,Description="Criteria">',
        '##INFO=<ID=ACMG_TRANSCRIPT,Number=A,Type=String,Description="Transcript">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">',
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">',
        '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Imputed">',
        '##INFO=<ID=RAF,Number=A,Type=Float,Description="Panel ALT frequency">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples),
    ]
    records = [
        ("chr1", 1000, "A", "G", "P", "GENEX", "0/1:20,20:40:99"),
        (*mutyh, "G", "A", "LP", "MUTYH", "0/1:20,20:40:99"),
        (*mutyh[:1], mutyh[1] + 5, "C", "T", "VUS", "MUTYH", "0/1:20,20:40:99"),
        (*brca2, "C", "T", "LP", "BRCA2", "0/0:40,0:40:99"),
    ]
    for chrom, pos, ref, alt, cls, gene, sample_fields in sorted(records):
        info = (
            f"CSQ={alt}|stop_gained|HIGH|{gene}|ENST1:c.1C>T|ENSP1:p.Gln1Ter|YES;"
            f"ACMG_CLASS={cls};ACMG_CRITERIA=PVS1&PM2_Supporting;"
            "ACMG_TRANSCRIPT=ENST1"
        )
        if (chrom, pos) == mutyh and mutyh_info:
            info += f";{mutyh_info}"
        lines.append(
            f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t{info}\tGT:AD:DP:GQ\t"
            + "\t".join([sample_fields] * len(samples))
        )
    plain = folder / "acmg_annotated.vcf"
    plain.write_text("\n".join(lines) + "\n")
    vcf = Path(bgzip_index(plain))
    summary = folder / "acmg_summary.json"
    summary.write_text(
        json.dumps(
            {
                "tool": {"name": "fastVEP", "version": "0.5.0"},
                "criteria_not_evaluated": ["PS3", "BS3", "PS4", "PP1", "PP4"],
            }
        )
    )
    _manifest(
        folder,
        "acmg",
        "vcf acmg",
        inputs=((folder / "HG002.vcf.gz", "input"),),
        outputs=((vcf, "annotated_vcf"), (summary, "acmg_summary")),
    )
    return vcf


def _acmg_section(folder: Path) -> dict[str, Any]:
    report = pr.build_report(folder)
    jsonschema.validate(json.loads(json.dumps(report)), pr.load_schema())
    return report["sections"]["acmg_research"]
