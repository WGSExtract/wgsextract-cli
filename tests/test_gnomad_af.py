"""Exercise gnomAD v4.1 setup and annotation against local VCFs (#1419)."""

import gzip
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import bgzip_index, needs_tools
from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.core import gnomad_af
from wgsextract_cli.core.utils import run_command

pytestmark = needs_tools("bcftools", "bgzip", "tabix")

AF_INFO = '##INFO=<ID=AF_joint,Number=A,Type=Float,Description="f">\n'
JOINT_INFO = (
    '##FILTER=<ID=AC0,Description="a">\n'
    f"{AF_INFO}"
    '##INFO=<ID=AF_grpmax_joint,Number=A,Type=Float,Description="m">\n'
    '##INFO=<ID=nhomalt_joint,Number=A,Type=Integer,Description="h">\n'
)


def _vcf(path: Path, contig: str, body: str, info: str = "") -> Path:
    path.write_text(
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID={contig},length=1000>\n{info}"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" + body.replace("{c}", contig),
        encoding="utf-8",
        newline="\n",
    )
    return Path(bgzip_index(path))


def _query(path: Path, fmt: str) -> list[str]:
    return run_command(
        ["bcftools", "query", "-f", fmt + "\n", str(path)], capture_output=True
    ).stdout.splitlines()


def test_1419_gnomad_v4_rebuild_keeps_allele_specific_annotations(
    tmp_path, monkeypatch
):
    joint = _vcf(
        tmp_path / "chr1.vcf",
        "chr1",
        "{c}\t100\t.\tC\tT,G,A\t.\tPASS\t"
        "AF_joint=0.25,0.5,0;AF_grpmax_joint=0.4,0.75,0;nhomalt_joint=0,3,0\n"
        "{c}\t200\t.\tG\tA\t.\tAC0\tAF_joint=0.1\n"
        "{c}\t300\t.\tTA\tT\t.\tPASS\tAF_joint=0\n",
        JOINT_INFO,
    )
    _vcf(
        tmp_path / "chr2.vcf",
        "chr2",
        "{c}\t50\t.\tA\tG\t.\tPASS\tAF_joint=0.01\n",
        AF_INFO,
    )
    _vcf(tmp_path / "chr3.vcf", "chr3", "", JOINT_INFO)
    files = {path.name: path.read_bytes() for path in tmp_path.glob("chr*.vcf.gz")}
    sample = _vcf(
        tmp_path / "sample.vcf",
        "1",
        "{c}\t100\tm\tC\tG,T,A\t.\tPASS\t.\n"
        "{c}\t200\tf\tG\tA\t.\tPASS\t.\n"
        "{c}\t300\tn\tTA\tT\t.\tPASS\t.\n",
    )
    library = tmp_path / "lib"
    downloads = library / "downloads"
    downloads.mkdir(parents=True)
    table = downloads / "gnomad_hg38.tsv.gz"
    index = downloads / "gnomad_hg38.tsv.gz.tbi"
    check = api.RefGnomadParams(ref=library, release="4.1")
    rebuild = api.RefGnomadParams(ref=library, release="4.1", download=True, threads=2)

    def annotate(path: Path, folder: str):
        return api.vcf.gnomad(
            api.VcfGnomadParams(
                vcf_input=sample, gnomad_file=path, outdir=tmp_path / folder, max_af=0.2
            )
        )

    with serving(files=files) as server:
        monkeypatch.setattr(
            gnomad_af, "GNOMAD_V4_URL", server.url_for("{contig}.vcf.gz")
        )
        monkeypatch.setattr(gnomad_af, "GNOMAD_V4_CONTIGS", ["chr1", "chr2", "chr3"])
        with pytest.raises(api.CommandFailed, match="not installed.*--download"):
            api.ref.gnomad(check)
        old = downloads / "old.tsv"
        old.write_text(
            "## gnomAD v4.1 joint exomes+genomes, PASS sites with AF_joint > 0\n"
            "#CHROM\tPOS\tREF\tALT\tAF\nchr1\t100\tC\tT\t0.25\n"
        )
        with table.open("wb") as out:
            run_command(["bgzip", "-c", str(old)], stdout=out)
        run_command(["tabix", "-s", "1", "-b", "2", "-e", "2", str(table)])
        original = table.read_bytes(), index.read_bytes()
        old_parts = downloads / ".gnomad_hg38_parts"
        old_parts.mkdir()
        (old_parts / "chr1.tsv.gz").write_bytes(
            gzip.compress(b"chr1\t101\tC\tT\t0.1\n")
        )
        unsplit_parts = downloads / ".gnomad_hg38_parts_v2"
        unsplit_parts.mkdir()
        (unsplit_parts / "chr1.tsv.gz").write_bytes(
            gzip.compress(b"chr1\t100\tC\tT,G,A\t0.25,0.5,0\t0.4,0.75,0\t0,3,0\n")
        )

        with pytest.raises(api.CommandFailed, match="needs rebuilding.*--download"):
            api.ref.gnomad(check)
        renamed = downloads / "renamed-archive.tsv.gz"
        renamed.write_bytes(original[0])
        legacy = annotate(renamed, "legacy")
        assert "gnomad_table_stale" in {warning.code for warning in legacy.warnings}
        result = tmp_path / "legacy" / "gnomad_annotated.vcf.gz"
        assert _query(result, "%ALT:%GNOMAD_AF")[:3] == ["G:.", "T:0.25", "A:."]
        assert not server.requests

        server.get_errors["chr3.vcf.gz"] = [503] * 3
        with pytest.raises(api.CommandFailed, match="gnomAD v4.1 setup failed"):
            api.ref.gnomad(rebuild)
        assert (table.read_bytes(), index.read_bytes()) == original

        original_replace = Path.replace

        def lock_table(path, target):
            if path == downloads / f"{table.name}.partial" and target == table:
                raise PermissionError("simulated sharing violation")
            return original_replace(path, target)

        server.get_paths.clear()
        with monkeypatch.context() as locked:
            locked.setattr(Path, "replace", lock_table)
            with pytest.raises(api.CommandFailed, match="gnomAD v4.1 setup failed"):
                api.ref.gnomad(rebuild)
        assert server.get_paths == ["chr3.vcf.gz"]
        assert (table.read_bytes(), index.read_bytes()) == original

        server.get_paths.clear()
        api.ref.gnomad(rebuild)
        assert not server.get_paths
        server.requests.clear()
        index.unlink()
        api.ref.gnomad(check)
        assert not server.requests

    assert index.is_file()
    with gzip.open(table, "rt", encoding="utf-8") as f:
        assert f.read().splitlines()[1:] == [
            "#CHROM\tPOS\tREF\tALT\tAF\tAF_grpmax_joint\tnhomalt_joint",
            "chr1\t100\tC\tT\t0.25\t0.4\t0",
            "chr1\t100\tC\tG\t0.5\t0.75\t3",
            "chr2\t50\tA\tG\t0.01\t.\t.",
        ]

    annotate(table, "table")
    fmt = "%CHROM:%ID:%ALT:%GNOMAD_AF:%GNOMAD_POPMAX_AF:%GNOMAD_NHOMALT"
    annotated = ["1:m:G:0.5:0.75:3", "1:m:T:0.25:0.4:0"]
    assert _query(tmp_path / "table" / "gnomad_annotated.vcf.gz", fmt) == [
        *annotated,
        "1:m:A:.:.:.",
        "1:f:A:.:.:.",
        "1:n:T:.:.:.",
    ]
    assert _query(tmp_path / "table" / "gnomad_af_lt_0.2.vcf.gz", "%ID") == [
        "m",
        "f",
        "n",
    ]
    # The released joint sites VCF is already split; exercise header mapping on it.
    split_joint = tmp_path / "joint-sites.vcf.gz"
    run_command(
        ["bcftools", "norm", "-m", "-any", "-Oz", "-o", str(split_joint), str(joint)],
        capture_output=True,
    )
    annotate(split_joint, "joint")
    assert _query(tmp_path / "joint" / "gnomad_annotated.vcf.gz", fmt)[:2] == annotated


def test_1419_gnomad_query_launch_failure_reaps_normalizer(tmp_path, monkeypatch):
    launched = []
    original_init = subprocess.Popen.__init__

    def fail_query(process, argv, *args, **kwargs):
        if isinstance(argv, list) and Path(argv[0]).stem == "bcftools":
            if argv[1:2] == ["query"]:
                raise FileNotFoundError("simulated OS launch failure")
            if argv[1:2] == ["norm"]:
                launched.append(process)
        original_init(process, argv, *args, **kwargs)

    with serving(files={"chr1.vcf.gz": b""}) as server:
        monkeypatch.setattr(
            gnomad_af, "GNOMAD_V4_URL", server.url_for("{contig}.vcf.gz")
        )
        monkeypatch.setattr(gnomad_af, "GNOMAD_V4_CONTIGS", ["chr1"])
        monkeypatch.setattr(subprocess.Popen, "__init__", fail_query)
        with pytest.raises(api.CommandFailed, match="gnomAD v4.1 setup failed"):
            api.ref.gnomad(
                api.RefGnomadParams(ref=tmp_path, release="4.1", download=True)
            )

        assert not server.get_paths
    assert launched and all(process.poll() is not None for process in launched)
    assert not list(tmp_path.rglob("*.partial"))
