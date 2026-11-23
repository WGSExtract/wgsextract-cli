"""Prepare and validate input files for fastVEP databases."""

import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

from wgsextract_cli.core.builds import CHR1_LENGTH_BUILDS
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.utils import (
    WGSExtractError,
    run_command,
)


def split_revel(
    zipped: Path,
    chromosomes: list[str],
    out_dir: Path,
    before_each: Callable[[], None] = lambda: None,
    cancel: CancelEvent | None = None,
) -> Iterator[tuple[str, Path]]:
    """Write the REVEL v1.3 rows of each of ``chromosomes`` to its own CSV,
    with the header, in ``out_dir``, yielding each (chromosome, CSV) as it is
    complete. REVEL's rows come a chromosome at a time, so one CSV is written
    at a time; ``before_each`` runs before each is started (and may wait)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = {c.removeprefix("chr"): c for c in chromosomes}
    finished: set[str] = set()
    current: tuple[str, Path, IO[str]] | None = None
    try:
        with zipfile.ZipFile(zipped) as archive:
            members = [m for m in archive.namelist() if not m.endswith("/")]
            if len(members) != 1:
                raise WGSExtractError(
                    f"{zipped.name} should hold one REVEL CSV; it holds {members}."
                )
            with archive.open(members[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                header = text.readline()
                if not header.startswith("chr,hg19_pos,grch38_pos,"):
                    raise WGSExtractError(
                        f"{zipped.name} isn't REVEL v1.3's CSV (its header is "
                        f"{header.strip()[:80]!r})."
                    )
                for number, line in enumerate(text):
                    if number & 0xFFFFF == 0 and cancel and cancel.is_set():
                        raise WGSExtractError("Cancelled.")
                    chrom = keys.get(line.split(",", 1)[0])
                    if current and chrom != current[0]:
                        name, path, out = current
                        current = None
                        out.close()
                        finished.add(name)
                        yield name, path
                    if chrom is None:
                        continue
                    if current is None:
                        if chrom in finished:
                            raise WGSExtractError(
                                f"{zipped.name} has {chrom} rows apart from the "
                                "others; REVEL v1.3 lists them together."
                            )
                        before_each()
                        path = out_dir / f"revel_{chrom}.csv"
                        current = (chrom, path, path.open("w", newline=""))
                        current[2].write(header)
                    current[2].write(line)
        if current:
            name, path, out = current
            current = None
            out.close()
            yield name, path
    except zipfile.BadZipFile as e:
        zipped.unlink(missing_ok=True)
        raise WGSExtractError(
            f"{zipped.name} is not a complete zip ({e}); it was removed, rerun "
            "to fetch it again."
        ) from e
    finally:
        if current:
            current[2].close()
            current[1].unlink(missing_ok=True)


def tabix_contigs(path: Path) -> set[str]:
    listed = run_command(["tabix", "-l", str(path)], capture_output=True).stdout
    return set(listed.split())


def require_grch38_vcf(path: Path) -> list[str]:
    """Refuse a VCF whose header says another assembly (see
    ``require_grch38_header``). Returns the header."""
    header = run_command(["bcftools", "view", "-h", str(path)], capture_output=True)
    lines = header.stdout.splitlines()
    require_grch38_header(lines, str(path))
    return lines


def require_grch38_header(lines: list[str], name: str) -> None:
    """Refuse a SpliceAI VCF header that says another assembly: its chromosome
    1 length, else a ``##reference`` line naming GRCh38 or hg38."""
    for line in lines:
        match = re.match(r"##contig=<ID=(?:chr)?1,.*length=(\d+)", line)
        if match:
            build = CHR1_LENGTH_BUILDS.get(int(match.group(1)))
            if build == "hg38":
                return
            raise WGSExtractError(
                f"{name} is for {build or 'an unknown assembly'}; the databases "
                "are GRCh38, so give a GRCh38 SpliceAI VCF."
            )
    if any(
        line.startswith("##reference") and re.search(r"GRCh38|hg38", line)
        for line in lines
    ):
        return
    raise WGSExtractError(
        f"{name}'s header doesn't say it is GRCh38 (no chromosome 1 length or "
        "##reference line); give a GRCh38 SpliceAI VCF."
    )


# ClinGen gene-disease validity as the genemap2-style table fastVEP's `omim`
# source reads, after fastVEP's analysis/acmg_benchmark/scripts/sa_sources/
# clingen_gdv_to_oga.py (Apache-2.0). Every classification is kept, the weak
# and negative ones too: the classifier tells "curated and found wanting"
# apart from "not curated".
_CLINGEN_COLUMNS = (
    "GENE SYMBOL",
    "DISEASE LABEL",
    "DISEASE ID (MONDO)",
    "MOI",
    "CLASSIFICATION",
)
_CLINGEN_MOI = {"AD", "AR", "XL", "SD", "MT", "UD"}


def clingen_to_genemap(src: Path, dst: Path) -> int:
    """Convert ClinGen's gene-validity CSV export; returns the gene count."""
    by_gene: dict[str, list[str]] = defaultdict(list)
    columns: dict[str, int] | None = None
    unknown_moi: dict[str, int] = defaultdict(int)
    with src.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row:
                continue
            if row[0] == "GENE SYMBOL":
                missing = [c for c in _CLINGEN_COLUMNS if c not in row]
                if missing:
                    raise WGSExtractError(
                        f"{src.name}'s header has no column {missing}; ClinGen's "
                        "export may have changed."
                    )
                columns = {c: row.index(c) for c in _CLINGEN_COLUMNS}
                continue
            gene = row[0].strip('"')
            if not gene or gene.startswith(("+", "FILE", "WEBPAGE", "CLINGEN")):
                continue
            if columns is None or len(row) <= max(columns.values()):
                continue
            classification = row[columns["CLASSIFICATION"]].strip('"')
            if not classification:
                continue
            moi = row[columns["MOI"]].strip('"')
            if moi not in _CLINGEN_MOI:
                unknown_moi[moi] += 1
            disease = row[columns["DISEASE LABEL"]].strip('"')
            mondo = row[columns["DISEASE ID (MONDO)"]].strip('"') or "."
            by_gene[gene].append(f"{disease} (ClinGen {classification}/{moi}, {mondo})")
    if columns is None:
        raise WGSExtractError(
            f"{src.name} has no GENE SYMBOL header row, so it isn't a ClinGen "
            "gene-disease validity export."
        )
    if unknown_moi:
        logging.info(
            "ClinGen curations with a mode of inheritance fastVEP doesn't know "
            "are read as having none: %s",
            dict(sorted(unknown_moi.items())),
        )
    with dst.open("w", newline="\n") as out:
        out.write(
            "# Synthetic genemap2 from ClinGen Gene-Disease Validity\n"
            "# col5=mim_proxy (always 0), col8=gene_symbol, col12=phenotypes\n"
        )
        for gene, phenotypes in sorted(by_gene.items()):
            cols = [""] * 13
            cols[5], cols[8], cols[12] = "0", gene, "; ".join(phenotypes)
            out.write("\t".join(cols) + "\n")
    return len(by_gene)
