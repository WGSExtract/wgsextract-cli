"""#1662: pinned GTEx installation and offline exact-ALT associations."""

import csv
import hashlib
import io
import json
import os
import sqlite3
import stat
import tarfile
from contextlib import closing
from pathlib import Path

import pytest
from smoke_utils import run_cli

from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.core import resource_releases
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.utils import run_command


@pytest.fixture
def gtex_bundle(monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    schema = pa.schema(
        [
            ("phenotype_id", pa.string()), ("gene_name", pa.string()),
            ("biotype", pa.string()), ("variant_id", pa.string()),
            ("pip", pa.float32()), ("af", pa.float32()), ("cs_id", pa.int32()),
            ("cs_size", pa.int64()), ("afc", pa.float32()), ("afc_se", pa.float32()),
        ]
    )  # fmt: skip
    records = {
        "Whole_Blood": [
            ("ENSG00000000001.2", "GENE1", "chr1_10_A_C_b38", 0.75, 1, -0.5),
            ("ENSG00000000002.1", "GENE2", "chr1_10_A_C_b38", 0.25, 2, None),
            ("ENSG00000000001.2", "GENE1", "chr1_10_A_G_b38", 0.5, 2, 0.25),
            ("ENSG00000000001.2", "GENE1", "chr1_20_A_AT_b38", 1.0, 1, 0.75),
            ("ENSG00000000001.2", "GENE1", "chr1_30_G_C_b38", 0.5, 1, 1.5),
        ],
        "Brain_Spinal_cord_cervical_c-1": [
            ("ENSG00000000001.2", "GENE1", "chr1_10_A_C_b38", 0.5, 1, 0.5)
        ],
    }
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for tissue, rows in records.items():
            sink = io.BytesIO()
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "phenotype_id": gene, "gene_name": symbol, "biotype": "protein_coding",
                            "variant_id": variant, "pip": pip, "af": 0.25, "cs_id": cs,
                            "cs_size": 3, "afc": afc, "afc_se": None,
                        }
                        for gene, symbol, variant, pip, cs, afc in rows
                    ],
                    schema=schema,
                ),
                sink,
            )  # fmt: skip
            member = tarfile.TarInfo(f"{tissue}.v10.eQTLs.SuSiE_summary.parquet")
            member.size = sink.tell()
            sink.seek(0)
            tar.addfile(member, sink)
    payload = archive.getvalue()
    name = "GTEx_v10_SuSiE_eQTL.tar"
    with serving(data=None, files={name: payload}) as server:
        entry = resource_releases.pinned_resource("gtex", "hg38")
        monkeypatch.setitem(entry, "upstream_url", server.url_for(name))
        monkeypatch.setitem(entry, "sha256", hashlib.sha256(payload).hexdigest())
        yield server, name


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    input_vcf = tmp_path / "input.vcf"
    input_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t10\t.\tA\tG,*,C,T\t.\tPASS\t.\n"
        "1\t10\t.\tC\tA\t.\tPASS\t.\n"
        "1\t19\t.\tCA\tCAT\t.\tPASS\t.\n"
        "1\t20\t.\tA\tAT\t.\tPASS\t.\n"
        "1\t30\t.\tG\t<NON_REF>\t.\tPASS\t.\n",
        encoding="utf-8",
        newline="\n",
    )
    return input_vcf


def test_1662_ref_gtex_needs_explicit_download(
    tmp_path, sample, gtex_bundle, monkeypatch
):
    server, name = gtex_bundle
    library = tmp_path / "library"
    params = api.RefGtexParams(ref=library)
    with pytest.raises(api.CommandFailed, match=r"ref gtex.*--download"):
        api.ref.gtex(params)
    with pytest.raises(api.CommandFailed, match=r"ref gtex.*--download"):
        api.vcf.gtex(api.VcfGtexParams(input=sample, ref=library))
    for operation in (["gtex"], ["chain-annotate", "--annotations", "gtex"]):
        rc, stdout, err = run_cli(
            [
                "--json", "ref", "needs", "--", "vcf", *operation,
                "--input", str(sample), "--ref", str(library),
            ]
        )  # fmt: skip
        assert rc == 0, err
        (need,) = json.loads(stdout)["details"]["missing"]
        assert need["id"] == "gtex" and "--download" in need["installArguments"]
    assert not server.requests
    install = api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    assert name in server.get_paths
    assert install.details["gtex_source"]["source_url"] == server.url_for(name)
    server.requests.clear()
    api.ref.gtex(api.RefGtexParams(ref=library, network="off"))
    status = api.ref.status(api.RefStatusParams(ref=library, network="off"))
    assert status.details["gtex"]["installed"]
    fasta = tmp_path / "external" / "GRCh38.fa"
    fasta.parent.mkdir()
    fasta.write_text(">chr1\nA\n", encoding="utf-8", newline="\n")
    monkeypatch.setitem(settings, "reference_library", str(library))
    status = api.ref.status(api.RefStatusParams(ref=fasta, network="off"))
    assert status.details["gtex"]["installed"]
    library_fasta = library / "GRCh38.fa"
    fasta.rename(library_fasta)
    monkeypatch.setitem(settings, "reference_library", None)
    monkeypatch.setitem(settings, "reference_fasta", str(library_fasta))
    status = api.ref.status(api.RefStatusParams(network="off"))
    assert status.details["gtex"]["installed"]
    assert not server.requests


@pytest.mark.parametrize("mode", ["standalone", "chain", "scoped-screen-chain"])
def test_1662_exact_alt_retains_tissue_gene_set_and_effect(
    tmp_path, sample, gtex_bundle, mode
):
    server, _ = gtex_bundle
    library = tmp_path / "library"
    api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    server.requests.clear()
    output = tmp_path / "annotation"
    original = sample.read_bytes()
    common = {"input": sample, "ref": library, "outdir": output, "network": "off"}
    if mode == "standalone":
        result = api.vcf.gtex(api.VcfGtexParams(**common))
    else:
        annotations, region = "gtex", None
        if mode == "scoped-screen-chain":
            bed = tmp_path / "screen.bed"
            bed.write_text(
                "chr1\t9\t10\tEH38D1\tEH38E1\tPLS\n", encoding="utf-8", newline="\n"
            )
            api.ref.regulatory(api.RefRegulatoryParams(ref=library, bed=bed))
            annotations, region = "gtex,regulatory", "1:10-10"
        result = api.vcf.chain_annotate(
            api.VcfChainAnnotateParams(**common, annotations=annotations, region=region)
        )
        variants = run_command(
            [
                "bcftools",
                "query",
                "-f",
                r"%POS\t%REF\t%ALT\n",
                output / "chain_annotated.vcf.gz",
            ],
            capture_output=True,
        )
        variants_seen = variants.stdout.splitlines()
        assert variants_seen[:2] == ["10\tA\tG,*,C,T", "10\tC\tA"]
        if mode == "scoped-screen-chain":
            assert len(variants_seen) == 2
        else:
            assert "20\tA\tAT" in variants_seen
    assert sample.read_bytes() == original
    filename = "gtex_eqtl.tsv" if mode == "standalone" else "chain_gtex_eqtl.tsv"
    sidecar = output / filename
    with sidecar.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    hits = [
        (
            row["pos"], row["ref"], row["alt"], row["alt_index"], row["tissue"],
            row["gene_id"], row["credible_set"], row["pip"], row["afc"],
        )
        for row in rows
    ]  # fmt: skip
    expected = [
        ("10", "A", "G", "1", "Whole_Blood", "ENSG00000000001.2", "2", "0.5", "0.25"),
        ("10", "A", "C", "3", "Whole_Blood", "ENSG00000000001.2", "1", "0.75", "-0.5"),
        ("10", "A", "C", "3", "Whole_Blood", "ENSG00000000002.1", "2", "0.25", ""),
        (
            "10", "A", "C", "3", "Brain_Spinal_cord_cervical_c-1",
            "ENSG00000000001.2", "1", "0.5", "0.5",
        ),
    ]  # fmt: skip
    if mode != "scoped-screen-chain":
        expected.append(
            (
                "20",
                "A",
                "AT",
                "1",
                "Whole_Blood",
                "ENSG00000000001.2",
                "1",
                "1.0",
                "0.75",
            )
        )
    assert hits == expected
    assert result.metrics["gtex_associations"] == len(expected)
    assert any(
        item.id == "gtex" and item.release_basis == "pinned_sha256"
        for item in result.resources
    )
    if mode == "chain":
        journal = json.loads((output / "chain_annotate.steps.json").read_text())
        assert journal["steps"]["gtex"]["status"] == "complete"
        assert [(step["step"], step["action"]) for step in result.details["steps"]] == [
            ("gtex", "run")
        ]
        rerun = api.vcf.chain_annotate(
            api.VcfChainAnnotateParams(**common, annotations="gtex")
        )
        assert rerun.details["steps"][0]["action"] == "skip"
        assert rerun.metrics["gtex_associations"] == len(expected)
    if mode == "scoped-screen-chain":
        previous_sidecar = sidecar.read_bytes()
        api.vcf.gtex(api.VcfGtexParams(**common))
        api.vcf.chain_annotate(
            api.VcfChainAnnotateParams(
                **common, annotations="regulatory", region="1:20-20"
            )
        )
        assert sidecar.read_bytes() == previous_sidecar
        assert (output / "gtex_eqtl.tsv").is_file()
        sidecar.write_bytes(b"User data, not a GTEx output\n")
        api.vcf.chain_annotate(
            api.VcfChainAnnotateParams(**common, annotations="regulatory")
        )
        assert sidecar.read_bytes() == b"User data, not a GTEx output\n"
    assert not server.requests


@pytest.mark.parametrize("linked", [False, True], ids=["direct", "hardlink"])
def test_1662_chain_refuses_to_delete_a_configured_phylop_input(
    tmp_path, sample, gtex_bundle, monkeypatch, linked
):
    library = tmp_path / "library"
    api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    common = {"input": sample, "ref": library, "outdir": tmp_path, "network": "off"}
    api.vcf.chain_annotate(api.VcfChainAnnotateParams(**common, annotations="gtex"))
    sidecar = tmp_path / "chain_gtex_eqtl.tsv"
    phylop = b"#Chr\tStart\tEnd\tScore\n1\t10\t10\t3.0\n"
    sidecar.unlink()
    sidecar.write_bytes(phylop)
    source = sidecar
    if linked:
        source = tmp_path / "configured-phylop.tsv"
        try:
            os.link(sidecar, source)
        except OSError:
            pytest.skip("hardlinks are unavailable")
    monkeypatch.setitem(settings, "phylop_tsv_path", str(source))
    with pytest.raises(
        api.CommandFailed, match=r"chain_gtex_eqtl\.tsv.*overwrite an input"
    ):
        api.vcf.chain_annotate(
            api.VcfChainAnnotateParams(**common, annotations="phylop")
        )
    assert sidecar.read_bytes() == source.read_bytes() == phylop


def test_1662_failed_gtex_chain_rebuild_keeps_sidecar_and_can_retry(
    tmp_path, sample, gtex_bundle
):
    library = tmp_path / "library"
    install = api.RefGtexParams(ref=library, download=True)
    api.ref.gtex(install)
    out = tmp_path / "chain"
    common = {
        "input": sample, "ref": library, "outdir": out, "annotations": "gtex",
        "network": "off",
    }  # fmt: skip
    api.vcf.chain_annotate(api.VcfChainAnnotateParams(**common))
    sidecar = out / "chain_gtex_eqtl.tsv"
    final = out / "chain_annotated.vcf.gz"
    previous_sidecar, previous_vcf = sidecar.read_bytes(), final.read_bytes()
    status = api.ref.status(api.RefStatusParams(ref=library))
    with closing(sqlite3.connect(status.details["gtex"]["file"])) as db, db:
        db.execute("UPDATE association SET pip=0.25 WHERE pos=20")

    with pytest.raises(api.CommandFailed, match="missing or damaged"):
        api.vcf.chain_annotate(
            api.VcfChainAnnotateParams(
                **common, region="1:10-10", force_steps=("gtex",)
            )
        )
    assert sidecar.read_bytes() == previous_sidecar
    assert final.read_bytes() == previous_vcf

    api.ref.gtex(install)
    retry = api.vcf.chain_annotate(
        api.VcfChainAnnotateParams(**common, region="1:10-10")
    )
    assert retry.metrics["gtex_associations"] == 4
    assert sidecar.read_bytes() != previous_sidecar


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX file modes")
@pytest.mark.parametrize("chain", [False, True], ids=["standalone", "chain"])
def test_1662_gtex_rebuild_keeps_private_sidecar_mode(
    tmp_path, sample, gtex_bundle, chain
):
    library = tmp_path / "library"
    api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    common = {"input": sample, "ref": library, "outdir": tmp_path, "network": "off"}
    sidecar = tmp_path / ("chain_gtex_eqtl.tsv" if chain else "gtex_eqtl.tsv")
    previous_umask = os.umask(0o022)
    try:
        if chain:
            api.vcf.chain_annotate(
                api.VcfChainAnnotateParams(**common, annotations="gtex")
            )
        else:
            api.vcf.gtex(api.VcfGtexParams(**common))
        assert stat.S_IMODE(sidecar.stat().st_mode) == 0o644
        sidecar.chmod(0o600)
        if chain:
            api.vcf.chain_annotate(
                api.VcfChainAnnotateParams(
                    **common, annotations="gtex", force_steps=("gtex",)
                )
            )
        else:
            api.vcf.gtex(api.VcfGtexParams(**common))
        assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    finally:
        os.umask(previous_umask)


def test_1662_failed_sidecar_rename_rolls_back_the_chain_vcf(
    tmp_path, sample, gtex_bundle, monkeypatch
):
    library = tmp_path / "library"
    api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    out = tmp_path / "chain"
    common = {
        "input": sample, "ref": library, "outdir": out, "annotations": "gtex",
        "network": "off",
    }  # fmt: skip
    api.vcf.chain_annotate(api.VcfChainAnnotateParams(**common))
    final = out / "chain_annotated.vcf.gz"
    index = final.with_suffix(".gz.tbi")
    sidecar = out / "chain_gtex_eqtl.tsv"
    previous = {p: p.read_bytes() for p in (final, index, sidecar)}
    replace = os.replace

    def deny_sidecar_replace(src, dest, *args, **kwargs):
        if Path(dest) == sidecar:
            raise PermissionError("The sidecar is locked.")
        return replace(src, dest, *args, **kwargs)

    with monkeypatch.context() as fs:
        fs.setattr(os, "replace", deny_sidecar_replace)
        with pytest.raises(api.CommandFailed, match="The sidecar is locked"):
            api.vcf.chain_annotate(
                api.VcfChainAnnotateParams(**common, region="1:10-10")
            )
    assert {p: p.read_bytes() for p in previous} == previous
    retry = api.vcf.chain_annotate(
        api.VcfChainAnnotateParams(**common, region="1:10-10")
    )
    assert retry.metrics["gtex_associations"] == 4


def test_1662_gtex_bad_checksum_does_not_install(tmp_path, gtex_bundle):
    server, name = gtex_bundle
    library = tmp_path / "library"
    server.files[name] += b"corrupt"
    with pytest.raises(api.CommandFailed, match="SHA-256"):
        api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    status = api.ref.status(api.RefStatusParams(ref=library))
    assert not status.details["gtex"]["installed"]
    assert not Path(status.details["gtex"]["file"]).exists()


@pytest.mark.parametrize(
    "corruption", ["missing-association", "changed-pip", "missing-checksum"]
)
def test_1662_ref_gtex_rejects_and_repairs_corruption(
    tmp_path, sample, gtex_bundle, corruption
):
    library = tmp_path / "library"
    install = api.RefGtexParams(ref=library, download=True)
    api.ref.gtex(install)
    status = api.ref.status(api.RefStatusParams(ref=library))
    track = Path(status.details["gtex"]["file"])
    if corruption == "missing-checksum":
        track.with_suffix(".sqlite.json").unlink()
    else:
        with closing(sqlite3.connect(track)) as db, db:
            if corruption == "missing-association":
                db.execute("DELETE FROM association WHERE pos=20")
            else:
                db.execute("UPDATE association SET pip=0.25 WHERE pos=20")
    with pytest.raises(api.CommandFailed, match=r"missing or damaged"):
        api.ref.gtex(api.RefGtexParams(ref=library, network="off"))
    annotate = api.VcfGtexParams(
        input=sample, ref=library, outdir=tmp_path, network="off"
    )
    with pytest.raises(api.CommandFailed, match=r"missing or damaged"):
        api.vcf.gtex(annotate)
    assert not (tmp_path / "gtex_eqtl.tsv").exists()
    api.ref.gtex(install)
    result = api.vcf.gtex(annotate)
    assert result.metrics["gtex_associations"] == 5
    with (tmp_path / "gtex_eqtl.tsv").open(encoding="utf-8", newline="") as stream:
        (repaired,) = (
            row for row in csv.DictReader(stream, delimiter="\t") if row["pos"] == "20"
        )
    assert repaired["pip"] == "1.0"


def test_1662_gtex_refuses_grch37_without_replacing_sidecar(tmp_path, sample):
    sample.write_text(
        sample.read_text(encoding="utf-8").replace("248956422", "249250621"),
        encoding="utf-8",
        newline="\n",
    )
    old = tmp_path / "gtex_eqtl.tsv"
    old.write_text("previous successful output\n", encoding="utf-8", newline="\n")
    with pytest.raises(
        api.CommandFailed, match="GTEx annotation requires a GRCh38 VCF"
    ):
        api.vcf.gtex(api.VcfGtexParams(input=sample, ref=tmp_path, network="off"))
    assert old.read_text(encoding="utf-8") == "previous successful output\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires named pipes")
def test_1662_ref_status_refuses_a_non_file_receipt(tmp_path, gtex_bundle):
    library = tmp_path / "library"
    api.ref.gtex(api.RefGtexParams(ref=library, download=True))
    status = api.ref.status(api.RefStatusParams(ref=library))
    receipt = Path(status.details["gtex"]["file"]).with_suffix(".sqlite.json")
    receipt.unlink()
    os.mkfifo(receipt)
    rc, stdout, err = run_cli(
        ["--json", "ref", "status", "--ref", str(library)], timeout=15
    )
    assert rc == 0, err
    assert not json.loads(stdout)["details"]["gtex"]["installed"]
