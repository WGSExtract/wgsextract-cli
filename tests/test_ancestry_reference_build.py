"""The maintainer-only reference builder uses explicitly configured assemblies."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parents[1] / "scripts/ancestry_reference/build.py"


@pytest.mark.parametrize(
    ("input_state", "error"),
    [
        ("no-config", "package requires --config"),
        ("no-fasta", "GRCh38 FASTA not found"),
        ("no-index", "index the FASTA with samtools faidx"),
    ],
)
def test_package_reports_missing_local_reference_inputs(
    tmp_path: Path, input_state: str, error: str
) -> None:
    args = [sys.executable, str(BUILD), "package", "--work", str(tmp_path)]
    if input_state != "no-config":
        config = tmp_path / "config.toml"
        config.write_text('[ancestry_reference.fasta]\nGRCh38 = "assembly.fa"\n')
        args.extend(["--config", str(config)])
    if input_state == "no-index":
        (tmp_path / "assembly.fa").write_text(">chr1\nA\n")
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert error in result.stderr


@pytest.mark.skipif(
    shutil.which("samtools") is None, reason="Builder needs samtools on PATH"
)
@pytest.mark.skipif(sys.platform == "win32", reason="Builder uses /dev/stdin")
def test_package_accepts_configured_numeric_grch38_contigs(tmp_path: Path) -> None:
    work = tmp_path / "work"
    for subdir in ("data", "sets", "chains", "adamixture/train"):
        (work / subdir).mkdir(parents=True)

    config = tmp_path / "config.toml"
    entries = ["[ancestry_reference.fasta]"]
    for build, contig in (("GRCh38", "1"), ("GRCh37", "chr1"), ("CHM13", "1")):
        fasta = tmp_path / f"{build}.fa"
        fasta.write_text(f">{contig}\n{'A' * 12}\n")
        subprocess.run(["samtools", "faidx", str(fasta)], check=True)
        entries.append(f'{build} = "{fasta.name}"')
    config.write_text("\n".join(entries))
    for chain in ("hg38ToHg19.over.chain.gz", "hg38ToHs1.over.chain.gz"):
        with gzip.open(work / "chains" / chain, "wt") as stream:
            stream.write("chain 1 chr1 12 + 0 12 chr1 12 + 0 12 1\n12\n")

    regions = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")
    (work / "data/gnomad_meta_v1.tsv").write_text(
        "s\thgdp_tgp_meta.Population\thgdp_tgp_meta.Genetic.region\n"
        + "".join(f"s{i}\tpop{i % 7}\t{regions[i % 7]}\n" for i in range(12))
    )
    (work / "sets/train.fam").write_text(
        "".join(f"0 s{i} 0 0 0 0\n" for i in range(12))
    )
    (work / "sets/train.bim").write_text(
        "".join(f"1 snp{i} 0 {i + 1} G A\n" for i in range(12))
    )
    # SNP-major PLINK, one hom-alt sample per site and hom-ref elsewhere.
    (work / "sets/train.bed").write_bytes(
        b"\x6c\x1b\x01"
        + bytes(
            255 ^ (3 << (2 * (site % 4))) if byte == site // 4 else 255
            for site in range(12)
            for byte in range(3)
        )
    )
    (work / "adamixture/train/train.7.P").write_text(
        (" ".join(["0.1"] * 7) + "\n") * 12
    )
    (work / "adamixture/train/train.7.Q").write_text(
        "".join(
            " ".join(str(int(component == sample % 7)) for component in range(7)) + "\n"
            for sample in range(12)
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            str(BUILD),
            "package",
            "--work",
            str(work),
            "--config",
            str(config),
            "--k",
            "7",
        ],  # fmt: skip
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert json.JSONDecoder().raw_decode(result.stdout)[0]["lifted"] == {
        "GRCh37": 12,
        "CHM13": 12,
    }
