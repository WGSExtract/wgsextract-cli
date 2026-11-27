"""`vcf table`: a VCF's variants and annotations as a flat TSV and SQLite table."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import IO

from wgsextract_cli.api.vcf import VcfTableParams
from wgsextract_cli.core import _variant_table_protein as protein
from wgsextract_cli.core import acmg
from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    build_from_reference_header,
)
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.gnomad_fields import GNOMAD_FAF95, gnomad_native_faf95
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.process_registry import terminate_trees
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.results import (
    current_result,
    human_output,
    warn,
)
from wgsextract_cli.core.table_writer import (
    TableWriter,
    check_format,
    provenance,
    table_path,
)
from wgsextract_cli.core.temp_directory import scratch_directory
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    popen,
)
from wgsextract_cli.core.variant_files import get_vcf_samples
from wgsextract_cli.core.variant_table import (
    COLUMN_NAMES,
    COLUMNS,
    VEP_GNOMAD_AF,
    Row,
    VcfHeader,
    parse_header,
    variant_rows,
)

from .basic import require_vcf_input, vcf_input_handler
from .panel_slice import indexed_vcf, resolve_panel_slice
from .pharmcat import summarize

_VCF_SUFFIX = re.compile(r"\.(g?vcf(\.b?gz)?|bcf)$", re.IGNORECASE)
_BATCH = 50_000
TABLE = "variants"
# Indexes for the lookups agents make most: a gene, an rsID, a position.
INDEXES = {"gene": ("gene",), "rsid": ("rsid",), "position": ("chrom", "pos")}


def table_stem(vcf: str) -> str:
    return _VCF_SUFFIX.sub("", Path(vcf).name) + "_variants"


def _sha256(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _reference_build(header: VcfHeader) -> str | None:
    build = build_from_chr1_length(sequence_lengths("\n".join(header.lines)))
    if build:
        return build
    for line in header.lines:
        if line.startswith("##reference="):
            return build_from_reference_header(line.partition("=")[2])
    return None


def _create_sqlite(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode = OFF")
    db.execute("PRAGMA synchronous = OFF")
    columns = ", ".join(f"{c.name} {c.type}" for c in COLUMNS)
    db.execute(f"CREATE TABLE {TABLE} ({columns})")
    db.execute("CREATE TABLE columns (name TEXT, type TEXT, description TEXT)")
    db.executemany(
        "INSERT INTO columns VALUES (?, ?, ?)",
        [(c.name, c.type, c.description) for c in COLUMNS],
    )
    db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")


def _finish_sqlite(db: sqlite3.Connection, metadata: dict[str, str]) -> None:
    for name, columns in INDEXES.items():
        db.execute(f"CREATE INDEX {TABLE}_{name} ON {TABLE} ({', '.join(columns)})")
    db.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(metadata.items()))
    db.commit()
    db.execute("ANALYZE")
    db.commit()


def _batches(rows: Iterable[Row]) -> Iterator[list[Row]]:
    batch: list[Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) == _BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


# The population frequency columns; the first two name the source they hold.
_FREQUENCIES = ("gnomad_af", "kg_af", "max_af")
_SOURCES = {"gnomad_af": "gnomad", "kg_af": "1000genomes"}


def _count_frequencies(rows: Iterable[Row], counts: dict[str, int]) -> Iterator[Row]:
    """Pass ``rows`` through, counting in ``counts`` the rows that have a
    value in each population frequency column."""
    columns = {name: COLUMN_NAMES.index(name) for name in _FREQUENCIES}
    for row in rows:
        for name, i in columns.items():
            if row[i] is not None:
                counts[name] += 1
        yield row


def _warn_missing_gnomad(
    vcf: str, header: VcfHeader, rows: int, counts: dict[str, int]
) -> None:
    """Say so when no row has a gnomAD frequency, so an empty gnomad_af isn't
    read as rare (#1354). The data can't always tell a cache without gnomAD
    (a vep-rs cache from `vep download` gives VEP 1000 Genomes frequencies
    only) from variants gnomAD lacks, so where VEP declares gnomAD fields
    the warning says both are possible."""
    if not rows or counts["gnomad_af"] or "GNOMAD_AF" in header.info_number:
        return
    kg = f", though it filled kg_af (1000 Genomes) on {counts['kg_af']:,} rows"
    if set(header.csq_fields or ()) & set(VEP_GNOMAD_AF):
        held = (
            f"VEP left every gnomAD field empty{kg if counts['kg_af'] else ''}. "
            "Either VEP's cache has no gnomAD frequencies (a vep-rs cache from "
            "'vep download' has none) or no variant matched gnomAD, so an empty "
            "gnomad_af may mean not annotated rather than rare"
        )
    else:
        held = (
            "it has no gnomAD annotation, so an empty gnomad_af means not "
            "annotated, not rare"
        )
    warn(
        "gnomad_af_missing",
        f"No row of {vcf} has a gnomAD frequency: {held}. Run 'wgsextract vcf "
        "gnomad' on the VCF before filtering on rarity.",
        path=vcf,
    )


def _pharmcat_calls(path: str | None) -> dict[str, tuple[str, str]]:
    if not path:
        return {}
    try:
        calls = summarize(path)
    except (OSError, ValueError) as e:
        raise WGSExtractError(f"Could not read PharmCAT results {path}: {e}") from e
    return {call.gene: (call.diplotype, call.phenotype) for call in calls}


def _select_sample(vcf: str, requested: str | None) -> tuple[list[str], str | None]:
    samples = get_vcf_samples(vcf)
    if requested:
        if requested not in samples:
            raise UsageError(
                f"Sample '{requested}' is not in {vcf}; it has: "
                f"{', '.join(samples) or 'no samples'}."
            )
        return samples, requested
    if len(samples) > 1:
        logging.info(
            f"{vcf} has {len(samples)} samples; using the first, {samples[0]}. "
            "Pick another with --sample."
        )
    return samples, (samples[0] if samples else None)


class VcfStream:
    """``bcftools view`` of the input as text: its parsed header and data lines.

    Spool stderr separately so it can't block stdout or outlive the stream.
    """

    def __init__(self, command: list[str], errors: IO[bytes]) -> None:
        self.command = command
        self.errors = errors
        self.proc = popen(command, stdout=subprocess.PIPE, stderr=errors, text=True)
        assert self.proc.stdout is not None
        self.stdout = self.proc.stdout
        try:
            self.header = self._read_header()
        except BaseException:
            self.close()
            raise

    def _read_header(self) -> VcfHeader:
        lines = []
        for line in self.stdout:
            lines.append(line)
            if line.startswith("#CHROM"):
                return parse_header(lines)
        self.check()  # no #CHROM line: bcftools failed or this isn't a VCF
        raise WGSExtractError(f"{self.command[-1]} has no #CHROM header line.")

    def check(self) -> None:
        if self.proc.wait() != 0:
            end = self.errors.seek(0, os.SEEK_END)
            self.errors.seek(max(0, end - 8192))
            tail = self.errors.read(8192).decode(errors="replace").splitlines()
            detail = "\n".join(tail[-20:]).strip()
            raise WGSExtractError(
                f"bcftools failed reading {self.command[-1]}: {detail}"
            )

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                terminate_trees([self.proc])
            self.proc.wait()
        finally:
            self.stdout.close()


def _write_tables(
    stream: VcfStream,
    rows: Iterable[Row],
    table_path: str,
    table_format: str,
    sqlite_path: str | None,
    metadata: dict[str, str],
    frequencies: dict[str, int],
) -> int:
    """Stream ``rows`` into the table file (and SQLite database); return the
    count, and count in ``frequencies`` the rows with each population
    frequency. Each file appears under its final name only once complete."""
    with ExitStack() as stack:
        table = stack.enter_context(
            TableWriter(table_path, COLUMNS, table_format, metadata=metadata)
        )
        db = None
        if sqlite_path:
            db = sqlite3.connect(stack.enter_context(atomic_output(sqlite_path)))
            stack.callback(db.close)
            _create_sqlite(db)
        insert = f"INSERT INTO {TABLE} VALUES ({', '.join('?' * len(COLUMNS))})"
        count = 0
        for batch in _batches(_count_frequencies(rows, frequencies)):
            table.write(batch)
            if db is not None:
                db.executemany(insert, batch)
            count += len(batch)
        stream.check()
        if db is not None:
            sources = [s for k, s in _SOURCES.items() if frequencies[k]]
            _finish_sqlite(
                db,
                {
                    **metadata,
                    "rows": str(count),
                    "population_af_sources": ",".join(sources),
                },
            )
    return count


def run_table(params: VcfTableParams) -> None:
    verify_dependencies(["bcftools"])
    check_format(params.table_format)
    input_vcf = require_vcf_input(params)
    if not Path(input_vcf).is_file():
        raise UsageError(f"VCF not found: {input_vcf}")
    outdir = str(absolute_path(params.outdir or Path(input_vcf).parent))
    Path(outdir).mkdir(parents=True, exist_ok=True)
    source = input_vcf
    scope = resolve_panel_slice(params, source)
    if scope is not None:
        input_vcf = scope.prepared
    elif params.region:
        input_vcf = indexed_vcf(input_vcf)
    samples, sample = _select_sample(input_vcf, params.sample)
    pharmcat = _pharmcat_calls(
        os.fspath(params.pharmcat_json) if params.pharmcat_json else None
    )
    result = current_result()
    if params.pharmcat_json:
        result.add_input(params.pharmcat_json, "pharmcat_phenotype")

    stem = str(Path(outdir, table_stem(source)))
    out_table = table_path(stem, params.table_format)
    sqlite_path = None if params.no_sqlite else stem + ".sqlite"

    command = ["bcftools", "view", "--no-version"]
    if sample and len(samples) > 1:
        command += ["-s", sample, "-I"]  # -I: keep INFO as it is
    logging.info(f"Writing the variant table from {input_vcf}...")
    with ExitStack() as stack:
        if scope is not None:
            work = stack.enter_context(scratch_directory(outdir, ".wgse_table_panel_"))
            command += scope.args(work)
        elif params.region:
            command += ["-r", params.region]
        errors = stack.enter_context(tempfile.TemporaryFile(dir=outdir))
        stream = VcfStream([*command, input_vcf], errors)
        stack.callback(stream.close)
        header = stream.header
        build = (scope.build if scope else None) or _reference_build(header)
        annotations = ",".join(header.annotations)
        protein_annotation_fields = protein.annotation_fields(
            header.csq_fields or (), header.ann_fields or ()
        )
        research_acmg = "acmg" in header.annotations
        # A full pass over the VCF, so only when SQLite or Parquet keeps it.
        keeps_metadata = sqlite_path is not None or params.table_format == "parquet"
        metadata = provenance(
            [source],
            reference_build=build or "",
            sorted_by="the VCF's record order (chrom, pos)",
            source_sha256=(_sha256(source) or "") if keeps_metadata else None,
            sample=sample or "",
            region=params.region or "",
            annotations=annotations,
            protein_annotation_fields=protein_annotation_fields,
            carried_only=str(not params.all_alleles).lower(),
            acmg_disclaimer=acmg.DISCLAIMER if research_acmg else None,
        )
        frequencies = dict.fromkeys(_FREQUENCIES, 0)
        rows = _write_tables(
            stream,
            variant_rows(
                stream.stdout,
                header,
                sample_index=0 if sample else None,
                carried_only=not params.all_alleles,
                pharmcat=pharmcat,
            ),
            out_table,
            params.table_format,
            sqlite_path,
            metadata,
            frequencies,
        )
    result.add_output(out_table, "variant_table", metrics={"rows": rows})
    if sqlite_path:
        result.add_output(
            sqlite_path, "variant_database", kind="sqlite", metrics={"rows": rows}
        )
    result.add_metrics(
        rows=rows,
        sample=sample,
        reference_build=build,
        annotations=annotations,
        protein_annotation_fields=protein_annotation_fields,
        **{f"{name}_rows": n for name, n in frequencies.items()},
    )
    if header.annotations:
        _warn_missing_gnomad(input_vcf, header, rows, frequencies)
        if (
            frequencies["gnomad_af"]
            and "GNOMAD_AF" in header.info_number
            and GNOMAD_FAF95 not in header.info_number
            and gnomad_native_faf95(header.info_number) is None
        ):
            warn(
                "gnomad_faf95_unavailable",
                "The VCF has gnomAD allele frequencies but no ancestry-group maximum "
                "FAF95 (gnomad_faf95). To fill it, rerun 'wgsextract vcf gnomad' "
                "with an updated gnomAD v4.1 table or sites VCF that contains FAF95.",
                path=input_vcf,
            )
    else:
        warn(
            "nothing_annotated",
            f"{input_vcf} has no VEP, SnpEff, ClinVar, gnomAD or PharmGKB "
            "annotations, so those columns are empty. Annotate it first, e.g. "
            "with 'wgsextract vep run' and 'wgsextract vcf clinvar'.",
            path=input_vcf,
        )
    if research_acmg:
        warn("acmg_research_only", acmg.DISCLAIMER, path=input_vcf)
    if rows == 0:
        warn("no_variants", f"No variants to tabulate in {input_vcf}.", path=input_vcf)
    found = f" ({annotations})" if annotations else ""
    human_output(f"{rows:,} variant rows{found}:")
    for path in filter(None, (out_table, sqlite_path)):
        human_output(f"  {path}")


cmd_table = vcf_input_handler(VcfTableParams, run_table)
