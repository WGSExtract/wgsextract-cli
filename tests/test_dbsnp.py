"""Tests for NCBI dbSNP common rsIDs download, SQLite lookup index, status and delete."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from tests.htslib_helpers import needs_htslib, run_wgsextract
from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.api.ref import RefDbsnpParams, RefStatusParams
from wgsextract_cli.commands.ref.status import ref_status_values
from wgsextract_cli.core import dbsnp

_SAMPLE_VCF = (
    "##fileformat=VCFv4.0\n"
    "##source=dbSNP\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "1\t10177\trs367896724\tA\tAC\t.\t.\tCOMMON=1\n"
    "1\t13116\trs62635286;rs999999999\tT\tG\t.\t.\tCOMMON=1\n"
    "19\t44908684\trs429358\tT\tC\t.\t.\tCOMMON=1\n"
    "X\t154032200\trs123456\tG\tA\t.\t.\tCOMMON=1\n"
)


def _make_bgzf_vcf(tmp_path: Path, name: str = "common_all.vcf") -> tuple[bytes, bytes]:
    raw = tmp_path / name
    raw.write_text(_SAMPLE_VCF, encoding="utf-8")
    gz = tmp_path / f"{name}.gz"
    subprocess.run(["bgzip", "-c", str(raw)], stdout=gz.open("wb"), check=True)
    subprocess.run(["tabix", "-f", "-p", "vcf", str(gz)], check=True)
    return gz.read_bytes(), Path(f"{gz}.tbi").read_bytes()


@needs_htslib
def test_dbsnp_install_lookup_status_and_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vcf_bytes, tbi_bytes = _make_bgzf_vcf(tmp_path)
    md5 = hashlib.md5(vcf_bytes, usedforsecurity=False).hexdigest()
    reflib = tmp_path / "reference"

    with serving(data=None) as server:
        server.files["common_all_hg38.vcf.gz"] = vcf_bytes
        server.files["common_all_hg38.vcf.gz.tbi"] = tbi_bytes
        monkeypatch.setitem(
            dbsnp.DBSNP_SOURCES,
            "hg38",
            (server.url_for("common_all_hg38.vcf.gz"), md5),
        )

        result = api.ref.dbsnp(RefDbsnpParams(ref=reflib, build="hg38"))
        vcf_path = reflib / "downloads" / "dbsnp_common_hg38.vcf.gz"
        tbi_path = reflib / "downloads" / "dbsnp_common_hg38.vcf.gz.tbi"
        sqlite_path = reflib / "downloads" / "dbsnp_common_hg38.rsid.sqlite"
        assert vcf_path.is_file()
        assert tbi_path.is_file()
        assert sqlite_path.is_file()
        assert {o.path for o in result.outputs} >= {
            str(vcf_path),
            str(tbi_path),
            str(sqlite_path),
        }

        # Offline rsID lookup
        assert dbsnp.validate_rsid_sqlite(sqlite_path, "hg38")
        assert not dbsnp.validate_rsid_sqlite(sqlite_path, "hg19")
        assert dbsnp.lookup_rsid(sqlite_path, "rs429358") == ("19", 44908684)
        assert dbsnp.lookup_rsid(sqlite_path, "RS367896724") == ("1", 10177)
        assert dbsnp.lookup_rsid(sqlite_path, "rs999999999") == ("1", 13116)
        assert dbsnp.lookup_rsid(sqlite_path, "rs123456") == ("X", 154032200)
        assert dbsnp.lookup_rsid(sqlite_path, "rs404") is None
        assert dbsnp.lookup_rsid(sqlite_path, "BRCA1") is None

        # Status values reflect installation
        status = api.ref.status(RefStatusParams(ref=reflib)).details
        values = ref_status_values(status)
        assert values["library.dbsnpInstalled"] == "true"
        assert values["library.dbsnpHg38Installed"] == "true"
        assert values["library.dbsnpHg19Installed"] == "false"

        # Re-run when already complete touches zero network
        server.requests.clear()
        api.ref.dbsnp(RefDbsnpParams(ref=reflib, build="hg38"))
        assert server.requests == []

        # Deleting only SQLite index rebuilds it locally without re-downloading VCF
        sqlite_path.unlink()
        assert (
            ref_status_values(api.ref.status(RefStatusParams(ref=reflib)).details)[
                "library.dbsnpInstalled"
            ]
            == "false"
        )
        api.ref.dbsnp(RefDbsnpParams(ref=reflib, build="hg38"))
        assert server.requests == []
        assert sqlite_path.is_file()
        assert dbsnp.lookup_rsid(sqlite_path, "rs429358") == ("19", 44908684)

        # CLI delete removes VCF, index, and SQLite DB
        run_wgsextract(
            "ref", "dbsnp", "--delete", "--build", "hg38", "--ref", str(reflib)
        )
        assert not vcf_path.exists()
        assert not tbi_path.exists()
        assert not sqlite_path.exists()
        after = ref_status_values(api.ref.status(RefStatusParams(ref=reflib)).details)
        assert after["library.dbsnpInstalled"] == "false"
        assert after["library.dbsnpHg38Installed"] == "false"


@needs_htslib
def test_dbsnp_md5_mismatch_rejects_corrupt_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vcf_bytes, tbi_bytes = _make_bgzf_vcf(tmp_path)
    reflib = tmp_path / "reference"

    with serving(data=None) as server:
        server.files["bad.vcf.gz"] = vcf_bytes
        server.files["bad.vcf.gz.tbi"] = tbi_bytes
        monkeypatch.setitem(
            dbsnp.DBSNP_SOURCES,
            "hg38",
            (server.url_for("bad.vcf.gz"), "0" * 32),
        )
        with pytest.raises(api.CommandFailed, match="MD5 mismatch"):
            api.ref.dbsnp(RefDbsnpParams(ref=reflib, build="hg38"))
        assert not (reflib / "downloads" / "dbsnp_common_hg38.vcf.gz").exists()
        assert not (reflib / "downloads" / "dbsnp_common_hg38.rsid.sqlite").exists()
