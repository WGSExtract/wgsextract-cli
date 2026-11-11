"""gnomAD structural-variant sites tables for `vcf sv-freq`, installed into the
reference library by ``ref gnomad-sv``.

Each catalogue's sites VCFs are downloaded one at a time, checked against a
pinned SHA-256, reduced to a small tab-separated table (CHROM, POS, END,
SVTYPE, SVLEN, AF, POPMAX_AF, ID; bgzip + tabix) and deleted, so only the
table stays. A marker beside the table records its own digest, checked
before every use.

- gnomAD-SV v4.1 (GRCh38; 63,046 genomes, short reads) and gnomAD-SV v2.1
  (GRCh37; 10,847 genomes) — gnomAD data are released without use
  restriction (CC0 1.0, https://gnomad.broadinstitute.org/policies).
- gnomAD-LR 0.1 beta (GRCh38; All of Us Phase I, 1,027 long-read genomes
  of participants self-identifying as Black or African American), "publicly
  released without use restriction" (gnomAD blog, 2026-10-22). Its SVs (50
  bp or longer; not tandem-repeat alleles) are streamed out of the 53 GB
  of per-chromosome sites VCFs.

Only PASS sites with an allele frequency are kept (and gnomAD-SV's
multi-allelic CNVs, by their non-reference carrier frequency).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.reference_assets import resolve_reference_library
from wgsextract_cli.core.sv_frequency import TABLE_COLUMNS, sv_type
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command

FOLDER = "gnomad-sv"
SOURCES = ("sr", "lr")
_BUCKET = "https://storage.googleapis.com/gcp-public-data--gnomad"
_MARKER = ".installed.json"
_TABLE_ROOM = 500 * 10**6


@dataclass(frozen=True)
class SourceFile:
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Catalogue:
    source: str  # sr or lr
    build: str
    label: str  # SV_AF_SOURCE value
    table: str
    files: tuple[SourceFile, ...]
    include: str  # bcftools -i
    fields: str  # bcftools query -f: CHROM POS END TYPE LEN AF POPMAX ID [CNFREQ]
    licence: str

    @property
    def download_bytes(self) -> int:
        return sum(f.size for f in self.files)


_SR_V4 = Catalogue(
    "sr",
    "hg38",
    "gnomAD-SV_v4.1",
    "gnomad_sv_v4.1_hg38.tsv.gz",
    (
        SourceFile(
            f"{_BUCKET}/release/4.1/genome_sv/gnomad.v4.1.sv.sites.vcf.gz",
            "26a70c309a853c77c043c8d710016f97a326d4a55cc89a08bc45384e00de2b04",
            1743544245,
        ),
    ),
    '(FILTER="PASS" || (FILTER="MULTIALLELIC" && SVTYPE="CNV")) && '
    '(SVTYPE="DEL" || SVTYPE="DUP" || SVTYPE="INS" || SVTYPE="INV" || '
    'SVTYPE="CNV")',
    "%CHROM\t%POS\t%INFO/END\t%INFO/SVTYPE\t%INFO/SVLEN\t%INFO/AF\t"
    "%INFO/GRPMAX_AF\t%ID\t%INFO/CN_NONREF_FREQ\n",
    "gnomAD: no use restriction (CC0 1.0)",
)
_SR_V2 = Catalogue(
    "sr",
    "hg19",
    "gnomAD-SV_v2.1",
    "gnomad_sv_v2.1_hg19.tsv.gz",
    (
        SourceFile(
            f"{_BUCKET}/papers/2019-sv/gnomad_v2.1_sv.sites.vcf.gz",
            "9324488dfb1dac86427cf95a9717c6030f3e1dc17e7a20ab01b958949575fda1",
            140885330,
        ),
    ),
    'FILTER="PASS" && (SVTYPE="DEL" || SVTYPE="DUP" || SVTYPE="INS" || SVTYPE="INV")',
    "%CHROM\t%POS\t%INFO/END\t%INFO/SVTYPE\t%INFO/SVLEN\t%INFO/AF\t"
    "%INFO/POPMAX_AF\t%ID\t.\n",
    "gnomAD: no use restriction (CC0 1.0)",
)
_LR_PINS: dict[str, tuple[str, int]] = {
    "chr1": (
        "a7b6bd275a7c4080c23227b3f465036882b362f58c93849afca3f8e6c2e12d2c",
        4131693839,
    ),
    "chr2": (
        "56dc66f36ccdffdcb6292f29d0c3383555e9e35003d01af97363506402facbdf",
        4267592928,
    ),
    "chr3": (
        "7fd925473d85df273bcdb8e19e10d36a6c5033b5ebc5271d6b665f9a29c91b27",
        3622805180,
    ),
    "chr4": (
        "12f57e5c14e85adc375ae38dff98d28686dfe2eb8a24eaa9152b32e89b06e8a1",
        3334955687,
    ),
    "chr5": (
        "47b97370e25a7a7c2a161310eba89fd751c8e15da77edd670a60c916a1e0d4ca",
        3028077346,
    ),
    "chr6": (
        "f1124534c671d52496bacca791644b5835179079f8df68bd94928be90e7f5837",
        3016821658,
    ),
    "chr7": (
        "62aa05373e9b4b8392a561518aa88cecb3345acb4d3fafd7a30dd533bf40fd45",
        3010339181,
    ),
    "chr8": (
        "5cfe108d81c1a901daf132326db1ca8dafa7fbfdd31c646a3291805b1fa8fc42",
        2730376921,
    ),
    "chr9": (
        "99cafe9fdf52cc78e365f4727311fa1f43d80b1c1b28317652d39572ad3a39bc",
        2268896542,
    ),
    "chr10": (
        "6da63555baed1963ce5c851c13f4ac4721696fb10bc9c243df3f4bca564c2714",
        2607605375,
    ),
    "chr11": (
        "7f190d5ed147d5e714bf4b375bf47fc45bbaeec5957dc427c7a94454af5bc196",
        2468075988,
    ),
    "chr12": (
        "14a36efde8c5fedc7e8278562379a46555b15e61e442a263ab700d1178b03134",
        2481468307,
    ),
    "chr13": (
        "b85147e84bd6ce59372797aabba4bc0b34e8426b0909e5ee0a40337a53fd0a19",
        1610507336,
    ),
    "chr14": (
        "c536c443955762aaefe34678dbb8689b7248ec7b004bd1db95e3f92b9fc9e8ea",
        1670106509,
    ),
    "chr15": (
        "efcc8b588ee9b292c576205bf090ce4de410d17e124677a55a434452fecdd9c4",
        1599050614,
    ),
    "chr16": (
        "68d3852099d566bb3669f91db969ff41c3d35ee14f3503509c83497518e22afb",
        1821129360,
    ),
    "chr17": (
        "e18b9e6d731e5de7339c3972baaa0bc6c1e57b8478e50f5e87e882a6e62d963a",
        1744833641,
    ),
    "chr18": (
        "27f6fbdc3cd7278eb99fb270aaa6f3f338b52c3d71dd899cc0748f9835645ed8",
        1399394493,
    ),
    "chr19": (
        "a0464f6bb4c4f6697dff45f1c1d9f69d1f1cfa5b424fdf8ac0a7c0845bc165da",
        1440321732,
    ),
    "chr20": (
        "6ccba4b5891d0bd1125ba34e90e79a0824b21ba441c63d9c99248e27c860f939",
        1201612282,
    ),
    "chr21": (
        "a21ad2269a263f26f09b7e1397d7343bde741f0603b1b8c6ab4e8aabdee43af0",
        757796519,
    ),
    "chr22": (
        "e59cd21f82294199ecceab15d6b5f3fe265c935c770d6b22de667ebdf6c56b88",
        859352650,
    ),
    "chrX": (
        "89bd23b5216921adec95b66e5943d21881934a7fe11255ec4d2bfd820d665583",
        1826249218,
    ),
    "chrY": (
        "facde7a68b52dcb2031b0441b6d1620abd3defad03bde338a3497cde1eaa066e",
        51265468,
    ),
}
_LR = Catalogue(
    "lr",
    "hg38",
    "gnomAD-LR_0.1",
    "gnomad_lr_0.1_hg38.tsv.gz",
    tuple(
        SourceFile(
            f"{_BUCKET}/release/lr/0.1/vcf/aou/"
            f"gnomad.lr.genomes.0.1.aou.sites.{contig}.vcf.gz",
            *_LR_PINS[contig],
        )
        for contig in [f"chr{c}" for c in (*range(1, 23), "X", "Y")]
    ),
    'FILTER="PASS" && INFO/SOURCE="SV" && (allele_type="del" || '
    'allele_type="ins" || allele_type="dup") && abs(allele_length)>=50',
    "%CHROM\t%POS\t%END\t%INFO/allele_type\t%INFO/allele_length\t%INFO/AF\t"
    "%INFO/AF_grpmax\t%ID\t.\n",
    "gnomAD-LR beta: publicly released without use restriction",
)
CATALOGUES: dict[tuple[str, str], Catalogue] = {
    (c.source, c.build): c for c in (_SR_V4, _SR_V2, _LR)
}


def folder(reflib: str | None = None) -> Path:
    return Path(resolve_reference_library(reflib), FOLDER)


def catalogue(source: str, build: str) -> Catalogue | None:
    return CATALOGUES.get((source, build))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _marker(table: Path) -> Path:
    return table.with_name(f".{table.name}{_MARKER}")


def installed_table(reflib: str | None, cat: Catalogue) -> Path | None:
    """The catalogue's table if it is installed and unchanged since."""
    table = folder(reflib) / cat.table
    try:
        marker = json.loads(_marker(table).read_text())
        if marker["sources"] != [f.sha256 for f in cat.files]:
            return None
        if not Path(f"{table}.tbi").is_file() or _sha256(table) != marker["sha256"]:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return table


def installed(reflib: str | None, source: str, build: str) -> bool:
    cat = catalogue(source, build)
    return bool(cat and (folder(reflib) / cat.table).is_file())


def _normalise(fields: Sequence[str], source: str) -> str | None:
    """One table row from a bcftools query line, or None to drop it."""
    chrom, pos, end, raw, svlen, af, popmax, ident, cn_freq = fields
    svtype = sv_type(raw)
    start = int(pos)
    length = abs(int(svlen)) if svlen not in (".", "") else 0
    if af in (".", ""):
        af = cn_freq  # multi-allelic CNV: non-reference carrier frequency
    if af in (".", "") or "," in af or float(af) == 0:
        return None
    if source == "lr":
        # Sequence-resolved: an insertion sits at POS; a tandem duplication is
        # an inserted copy of the sequence after POS, so it spans that copy.
        end = str(start if svtype == "INS" else start + length)
    elif svtype == "INS":
        end = pos
    popmax = popmax or "."
    return "\t".join((chrom, pos, end, svtype, str(length or "."), af, popmax, ident))


def convert_vcf(
    vcf: str | Path,
    out: Path,
    cat: Catalogue,
    cancel: CancelEvent | None = None,
) -> int:
    """Reduce one gnomAD sites VCF to table rows (BGZF, no header) in out."""
    rows = 0
    with tempfile.TemporaryFile() as err, out.open("wb") as sink:
        query = popen(
            ["bcftools", "query", "-i", cat.include, "-f", cat.fields, str(vcf)],
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
        )
        compress = popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=sink)
        assert query.stdout is not None and compress.stdin is not None
        try:
            for line in query.stdout:
                if cancel and cancel.is_set():
                    query.kill()
                    break
                row = _normalise(line.rstrip("\n").split("\t"), cat.source)
                if row is not None:
                    compress.stdin.write(row.encode() + b"\n")
                    rows += 1
        finally:
            compress.stdin.close()
        failed = query.wait() != 0
        if compress.wait() != 0 or failed:
            err.seek(0)
            detail = err.read().decode(errors="replace").strip()[-500:]
            raise WGSExtractError(f"Reading {Path(vcf).name} failed: {detail}")
    return rows


def _write_table(parts: Sequence[Path], dest: Path, cat: Catalogue) -> None:
    partial = dest.with_name(f".{dest.name}.partial")
    with partial.open("wb") as out:
        header = popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=out)
        header.communicate(
            (
                f"##source={cat.label}\n##build={cat.build}\n"
                f"##licence={cat.licence}\n" + "\t".join(TABLE_COLUMNS) + "\n"
            ).encode()
        )
        if header.returncode != 0:
            raise WGSExtractError("bgzip failed to write the table header.")
        out.flush()
        for part in parts:
            with part.open("rb") as source:
                shutil.copyfileobj(source, out, 8 << 20)
    run_command(["tabix", "-f", "-s", "1", "-b", "2", "-e", "3", str(partial)])
    Path(f"{partial}.tbi").replace(f"{dest}.tbi")
    partial.replace(dest)


def install(
    reflib: str | None,
    cat: Catalogue,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
) -> Path:
    """Download, verify and reduce the catalogue; return its table."""
    done = installed_table(reflib, cat)
    if done is not None:
        logging.info(f"{cat.label} ({cat.build}) is already installed: {done}")
        return done
    base = folder(reflib)
    parts_dir = base / f".{cat.table}.parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    # One source VCF is on disk at a time, beside the growing table.
    need = max(f.size for f in cat.files) + _TABLE_ROOM
    free = shutil.disk_usage(parts_dir).free
    if free < need:
        raise WGSExtractError(
            f"Installing {cat.label} needs {need / 1e9:.1f} GB free in {base} "
            f"(the largest source VCF, downloaded one at a time, and the table); "
            f"{free / 1e9:.1f} GB is free."
        )
    parts = []
    for index, source in enumerate(cat.files, 1):
        part = parts_dir / f"{index:02d}.tsv.gz"
        parts.append(part)
        stamp = part.with_name(f"{part.name}.sha256")
        if part.is_file() and stamp.is_file() and stamp.read_text() == source.sha256:
            continue  # done by an earlier, interrupted run
        name = source.url.rsplit("/", 1)[1]
        download = parts_dir / name
        logging.info(
            f"{cat.label}: downloading {name} ({index} of {len(cat.files)}, "
            f"{source.size / 1e9:.1f} GB)..."
        )
        if not download_file(
            source.url,
            str(download),
            progress,
            cancel,
            expected_sha256=source.sha256,
            resolve_release_digest=False,
        ):
            raise WGSExtractError(
                f"Could not download the pinned {name}; rerun to resume."
            )
        rows = convert_vcf(download, part, cat, cancel)
        if cancel and cancel.is_set():
            raise WGSExtractError("Cancelled.")
        download.unlink()
        stamp.write_text(source.sha256)
        logging.info(f"{cat.label}: {rows:,} SVs from {name}")
    dest = base / cat.table
    _write_table(parts, dest, cat)
    _marker(dest).write_text(
        json.dumps(
            {
                "label": cat.label,
                "build": cat.build,
                "sources": [f.sha256 for f in cat.files],
                "urls": [f.url for f in cat.files],
                "licence": cat.licence,
                "sha256": _sha256(dest),
            },
            indent=1,
        ),
        newline="\n",
    )
    shutil.rmtree(parts_dir)
    return dest


def delete(reflib: str | None, cat: Catalogue) -> list[Path]:
    table = folder(reflib) / cat.table
    stem = cat.table.removesuffix(".tsv.gz")
    paths = [
        table,
        Path(f"{table}.tbi"),
        _marker(table),
        table.parent / f".{cat.table}.parts",
        table.parent / f"{stem}.tsv.manifest.json",
    ]
    return [p for p in paths if p.exists()]


def tables_for(reflib: str | None, build: str | None) -> list[tuple[Path, Catalogue]]:
    """Installed, verified tables for the build."""
    found = []
    for cat in CATALOGUES.values():
        if cat.build != build:
            continue
        table = installed_table(reflib, cat)
        if table is not None:
            found.append((table, cat))
    return found
