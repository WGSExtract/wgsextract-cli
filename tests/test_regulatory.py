"""#1624: SCREEN cCRE annotations are explicit, offline and allele-specific."""

import csv
import gzip
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from smoke_utils import run_cli

from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.api.ref import RefRegulatoryParams
from wgsextract_cli.api.vcf import VcfRegulatoryParams
from wgsextract_cli.core import regulatory_data
from wgsextract_cli.core.utils import run_command

BED = (
    b"chr1\t9\t11\tEH38D2\tEH38E2\tPLS\n"
    b"chr1\t9\t11\tEH38D2\tEH38E2\tPLS\n"
    b"chr1\t10\t11\tEH38D1\tEH38E1\tCA-TF\n"
    b"chr1\t13\t14\tEH38D3\tEH38E3\tdELS\n"
)


def _vcf(directory: Path, header: str = "##contig=<ID=1,length=248956422>\n") -> Path:
    path = directory / "sample.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        + header
        + '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        "1\t9\t.\tA\tG\t.\tPASS\t.\tGT\t1/1\n"
        "1\t10\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
        "1\t11\t.\tAC\tAT,TC,*\t.\tPASS\t.\tGT\t1|2\n"
        "1\t11\t.\tA\tAT\t.\tPASS\t.\tGT\t0/1\n"
        "1\t12\t.\tACG\tA\t.\tPASS\t.\tGT\t0/1\n"
        "1\t13\t.\tA\tAT\t.\tPASS\t.\tGT\t0/1\n"
        "1\t14\t.\tA\t<NON_REF>\t.\tPASS\tEND=20\tGT\t0/0\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def imported(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    bed = tmp_path / "screen.bed.gz"
    bed.write_bytes(gzip.compress(BED))
    result = api.ref.regulatory(
        RefRegulatoryParams(ref=library, bed=bed, network="off")
    )
    assert result.details
    track = result.details["regulatory_source"]
    assert isinstance(track, dict) and track["origin"] == "local-import"
    return library


@pytest.mark.parametrize("chain", [False, True], ids=["standalone", "chain"])
def test_1624_screen_tracks_only_changed_allele_bases(
    tmp_path: Path, imported: Path, chain: bool
) -> None:
    vcf = _vcf(tmp_path)
    out = tmp_path / "annotation"
    command = (
        ["chain-annotate", "--annotations", "regulatory", "--to-table"]
        if chain
        else ["regulatory"]
    )
    rc, _, err = run_cli(
        [
            "--network",
            "off",
            "vcf",
            *command,
            "--input",
            str(vcf),
            "--ref",
            str(imported),
            "--outdir",
            str(out),
        ]
    )
    assert rc == 0, err
    annotated = out / (
        "chain_annotated.vcf.gz" if chain else "regulatory_annotated.vcf.gz"
    )
    got = run_command(
        ["bcftools", "query", "-f", r"%POS\t%CCRE_CLASS\t%CCRE_ID[\t%GT]\n", annotated],
        capture_output=True,
    )
    assert got.stdout.splitlines() == [
        "9\t.\t.\t1/1",
        "10\tPLS\tEH38E2\t0/1",
        "11\t.,CA-TF|PLS,.\t.,EH38E1|EH38E2,.\t1|2",
        "11\t.\t.\t0/1",
        "12\tdELS\tEH38E3\t0/1",
        "13\tdELS\tEH38E3\t0/1",
        "14\t.\t.\t0/0",
    ]
    assert run_command(["tabix", annotated, "1:13-13"], capture_output=True).stdout
    if not chain:
        result = api.vcf.table(api.vcf.VcfTableParams(input=annotated, outdir=out))
        tables = [
            Path(item.path)
            for item in result.outputs
            if str(item.path).endswith(".tsv")
        ]
        (table,) = tables
    else:
        (table,) = out.glob("*.tsv")
    with table.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    at_11 = {(row["alt"], row["ccre_class"]) for row in rows if row["pos"] == "11"}
    assert ("AT", "") in at_11
    assert ("TC", "CA-TF|PLS") in at_11


def test_1624_missing_screen_requires_download_and_is_reused_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vcf = _vcf(tmp_path)
    library = tmp_path / "library"
    path = f"{uuid4().hex}/release.bed"
    with serving(data=None) as server:
        server.files[path] = BED
        monkeypatch.setattr(regulatory_data, "URL", server.url_for(path))
        monkeypatch.setattr(regulatory_data, "SHA256", hashlib.sha256(BED).hexdigest())
        with pytest.raises(api.CommandFailed, match=r"SCREEN.*--download"):
            api.ref.regulatory(RefRegulatoryParams(ref=library))
        params = VcfRegulatoryParams(
            input=vcf, ref=library, outdir=tmp_path / "annotation"
        )
        with pytest.raises(api.CommandFailed, match=r"SCREEN.*--download"):
            api.vcf.regulatory(params)
        rc, stdout, err = run_cli(
            [
                "--json",
                "ref",
                "needs",
                "--",
                "vcf",
                "regulatory",
                "--input",
                str(vcf),
                "--ref",
                str(library),
            ]
        )
        assert rc == 0, err
        (item,) = json.loads(stdout)["details"]["missing"]
        assert item["id"] == "regulatory"
        assert "--download" in item["installArguments"]
        assert not server.requests
        installed = api.ref.regulatory(RefRegulatoryParams(ref=library, download=True))
        assert path in server.get_paths
        assert installed.details
        track = installed.details["regulatory_source"]
        assert isinstance(track, dict) and track["source_url"] == server.url_for(path)

        server.requests.clear()
        api.ref.regulatory(RefRegulatoryParams(ref=library, network="off"))
        api.vcf.regulatory(params)
        assert not server.requests


def test_1624_bad_checksum_never_publishes_or_replaces_screen(
    tmp_path: Path, imported: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"{uuid4().hex}/bad.bed"
    with serving(data=None) as server:
        server.files[path] = BED + b"bad"
        monkeypatch.setattr(regulatory_data, "URL", server.url_for(path))
        monkeypatch.setattr(regulatory_data, "SHA256", hashlib.sha256(BED).hexdigest())
        with pytest.raises(api.CommandFailed, match="SHA-256"):
            api.ref.regulatory(RefRegulatoryParams(ref=imported, download=True))
    vcf = _vcf(tmp_path)
    result = api.vcf.regulatory(VcfRegulatoryParams(input=vcf, ref=imported))
    assert result.details
    track = result.details["regulatory_source"]
    assert isinstance(track, dict) and track["origin"] == "local-import"
    index = imported / "downloads" / "screen-v4-hg38.bed.gz.tbi"
    index.write_bytes(b"damaged")
    with pytest.raises(api.CommandFailed, match=r"SCREEN.*--download"):
        api.vcf.regulatory(
            VcfRegulatoryParams(input=vcf, ref=imported, outdir=tmp_path / "damaged")
        )


@pytest.mark.parametrize(
    ("header", "evidence"),
    [
        ("##contig=<ID=1,length=249250621>\n", "249,250,621"),
        ("##contig=<ID=1>\n", "no build"),
        ("##assembly=GRCh37\n##contig=<ID=1,length=248956422>\n", "hg19"),
        ("##assembly=GRCh37\n##reference=GRCh38.fa\n##contig=<ID=1>\n", "hg19"),
        ("##assembly=GRCh38\n##contig=<length=249250621,ID=1>\n", "249,250,621"),
    ],
)
def test_1624_refuses_other_or_unidentified_builds(
    tmp_path: Path,
    imported: Path,
    header: str,
    evidence: str,
) -> None:
    vcf = _vcf(tmp_path, header)
    with pytest.raises(api.CommandFailed, match=re.escape(evidence)):
        api.vcf.regulatory(VcfRegulatoryParams(input=vcf, ref=imported))
    assert not (tmp_path / "regulatory_annotated.vcf.gz").exists()


def test_1624_refseq_and_reannotation_preserve_unrelated_utf8(
    tmp_path: Path, imported: Path
) -> None:
    vcf = tmp_path / "accession.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=NC_000001.11,length=248956422>\n"
        '##INFO=<Number=1,Type=Integer,Description="Old, score",ID=CCRE_CLASS>\n'
        '##INFO=<ID=TEXT,Number=1,Type=String,Description="café">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "NC_000001.11\t10\t.\tA\tG\t.\tPASS\tCCRE_CLASS=8;TEXT=café\n",
        encoding="utf-8",
    )
    rc, _, err = run_cli(
        [
            "vcf",
            "regulatory",
            "--input",
            str(vcf),
            "--ref",
            str(imported),
            "--outdir",
            str(tmp_path),
        ],
        env={"LC_ALL": "C", "PYTHONUTF8": "0"},
    )
    assert rc == 0, err
    with gzip.open(
        tmp_path / "regulatory_annotated.vcf.gz", "rt", encoding="utf-8"
    ) as file:
        result = file.read()
    assert result.count("ID=CCRE_CLASS") == 1
    assert "CCRE_CLASS=PLS" in result
    assert "TEXT=café" in result
    assert 'Description="café"' in result


def test_1663_refuses_to_replace_input_through_symlinked_outdir(
    tmp_path: Path, imported: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    vcf = source / "regulatory_annotated.vcf.gz"
    original = gzip.compress(_vcf(source).read_bytes())
    vcf.write_bytes(original)

    with pytest.raises(api.CommandFailed, match=r"overwrite its input.*--outdir"):
        api.vcf.regulatory(
            VcfRegulatoryParams(input=vcf, ref=imported, outdir=alias, network="off")
        )
    assert vcf.read_bytes() == original
