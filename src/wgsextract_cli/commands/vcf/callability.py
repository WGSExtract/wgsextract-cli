"""``vcf callability``: which coding exons of a gene list a gVCF could read."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from wgsextract_cli import __version__
from wgsextract_cli.api.vcf import VcfCallabilityParams
from wgsextract_cli.commands.qc._relate_vcf import is_gvcf
from wgsextract_cli.core import exon_callability as ec
from wgsextract_cli.core import gene_lists
from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.builds import build_from_chr1_length, canonical_build
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.paths import absolute_path, expand_path
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.table_writer import provenance, write_table
from wgsextract_cli.core.temp_directory import scratch_parent
from wgsextract_cli.core.utils import UsageError, run_command
from wgsextract_cli.core.variant_confidence import thresholds_from

from .basic import require_vcf_input, vcf_input_handler

JSON_NAME = "exon_callability.json"
TABLE_NAME = "exon_callability.tsv"
ROLE, TABLE_ROLE = "exon_callability", "exon_callability_table"


def _header(vcf: str) -> str:
    return run_command(["bcftools", "view", "-h", vcf], capture_output=True).stdout


def _first_sample(header: str) -> str | None:
    columns = header.rstrip("\n").rsplit("\n", 1)[-1].split("\t")
    return columns[9] if len(columns) > 9 else None


def _reference_file(header: str) -> str | None:
    for line in header.splitlines():
        if line.startswith("##reference="):
            # Only the name: a full path would reveal the user's folders.
            return Path(line.split("=", 1)[1].removeprefix("file://")).name or None
    return None


def _relative(path: str, folder: Path) -> str:
    """``path`` relative to ``folder`` (where the JSON is), so the document
    names no private folders; absolute only on another drive (Windows)."""
    try:
        return Path(
            os.path.relpath(Path(path).absolute(), folder.absolute())
        ).as_posix()
    except ValueError:
        return str(Path(path).absolute())


def _genes(spec: str | None) -> tuple[dict[str, Any], list[str]]:
    """The gene list's description and symbols; a built-in list's symbols
    come from the bundled manifest, so nothing is downloaded."""
    described = dict(gene_lists.describe(spec))
    list_id = str(described.get("id") or spec or gene_lists.default_id())
    if list_id in gene_lists.builtin_ids():
        return described, list(gene_lists.builtin_symbols(list_id))
    return described, [gene.symbol for gene in gene_lists.load(spec).genes]


def _find_gvcf(vcf: str, header: str) -> str:
    gvcf = vcf if is_gvcf(vcf, header) else evidence.companion(vcf)
    if gvcf is None:
        raise UsageError(
            f"{vcf} has no reference evidence: exon callability reads a gVCF. "
            "Give the gVCF, or rerun the caller with --reference-evidence gvcf "
            "(for example 'wgsextract vcf snp --reference-evidence gvcf'); see "
            "docs/reference-evidence.md."
        )
    return gvcf


def run_callability(params: VcfCallabilityParams) -> None:
    verify_dependencies(["bcftools"])
    vcf = require_vcf_input(params)
    outdir = Path(expand_path(params.outdir or absolute_path(vcf).parent))
    outdir.mkdir(parents=True, exist_ok=True)
    result = current_result()
    result.expect_output(outdir / JSON_NAME)
    vcf_header = _header(vcf)
    gvcf = _find_gvcf(vcf, vcf_header)
    sample = _first_sample(vcf_header)
    described, symbols = _genes(params.gene_list)
    thresholds = thresholds_from(params)
    with tempfile.TemporaryDirectory(
        dir=scratch_parent(outdir), prefix=".wgse_callability_"
    ) as work:
        read = evidence.readable(gvcf, sample, Path(work))
        if read is None:
            raise UsageError(
                f"The gVCF {gvcf} holds another sample than {vcf} ({sample})."
            )
        header = _header(read)
        source = evidence.ReferenceEvidence(
            read, header_text=header, check_variant_quality=True
        )
        model, names = ec.choose_model(source.lengths, params.exons)
        exons = [e for s in symbols for e in model.genes.get(s.upper(), ())]
        logging.info(
            f"Classifying {len(exons):,} exons of {len(symbols)} genes from {gvcf}"
        )
        results = ec.assess(source, exons, names, thresholds, params.flank)
    if gvcf != vcf:
        result.add_input(gvcf, evidence.ROLE)
    genes = [g.document() for g in ec.genes(symbols, model, results)]
    build = model.build or canonical_build(build_from_chr1_length(source.lengths))
    states = {
        state: sum(g["state"] == state for g in genes) for state in ec.GENE_STATES
    }
    exon_counts = {
        status: sum(r.status == status for r in results) for status in ec.EXON_STATUSES
    }
    provenance_ = source.provenance
    document = {
        "schema": ec.SCHEMA,
        "wgsextract_version": __version__,
        "sample": sample,
        "build": build,
        "gene_list": described,
        "exon_model": model.describe(),
        "reference": {
            "build": build,
            "contigs_checked": sorted(names.values()),
            "fasta": _reference_file(header),
        },
        "evidence": {
            "file": _relative(gvcf, outdir),
            "engine": provenance_.engine if provenance_ else None,
            "engine_version": provenance_.version if provenance_ else None,
            "quality_model": source.quality_model,
            "min_mapping_quality": provenance_.min_mapping_quality
            if provenance_
            else None,
            "region": provenance_.region if provenance_ else None,
        },
        "thresholds": {
            "min_depth": thresholds.min_depth,
            "min_gq": thresholds.min_gq,
            "flank": params.flank,
            "scope": f"coding exons ±{params.flank} bp",
        },
        "counts": {"genes": len(genes), **states, "exons": exon_counts},
        "genes": genes,
        "exon_statuses": ec.EXON_STATUSES,
        "gene_states": ec.GENE_STATES,
        "limitations": ec.LIMITATIONS,
        "unassessed_variant_classes": ec.UNASSESSED_CLASSES,
        "note": ec.NOTE,
        "guide": ec.GUIDE,
    }
    table = outdir / TABLE_NAME
    rows = write_table(
        table,
        ec.EXON_COLUMNS,
        (r.row() for r in results),
        metadata=provenance([vcf, gvcf], reference_build=build),
    )
    json_path = outdir / JSON_NAME
    with (
        safe_output(json_path) as staged,
        open(staged, "w", encoding="utf-8", newline="\n") as stream,
    ):
        stream.write(json.dumps(document, indent=2) + "\n")
    result.add_output(json_path, ROLE, metrics={"genes": len(genes)})
    result.add_output(table, TABLE_ROLE, metrics={"exons": rows})
    result.add_metrics(
        build=build,
        genes=len(genes),
        **{f"genes_{state}": n for state, n in states.items()},
        **{f"exons_{status}": n for status, n in exon_counts.items()},
    )
    result.set_details(
        {k: document[k] for k in ("schema", "build", "gene_list", "exon_model",
                                  "reference", "evidence", "thresholds", "counts")}
    )  # fmt: skip
    missing = [g["gene"] for g in genes if g["reasons"] == ["no_exon_model"]]
    if missing:
        warn(
            "genes_without_exon_model",
            f"{len(missing)} gene{'s' * (len(missing) != 1)} "
            f"{'have' if len(missing) != 1 else 'has'} no exon model "
            f"({', '.join(missing[:10])}"
            f"{', ...' if len(missing) > 10 else ''}): not run. Give --exons "
            "with a model for them.",
            count=len(missing),
        )
    unread = [g for g in genes if g["state"] in (ec.PARTIAL, ec.NOT_READABLE)]
    if unread:
        warn(
            "exons_not_callable",
            f"{len(unread)} of {len(genes)} genes have coding exons this gVCF "
            "could not read: a variant there would not have been called.",
            genes=len(unread),
        )
    human_output(
        f"Exon callability of {len(genes)} {described['name']} genes "
        f"({build or 'build unknown'}, {model.describe()['transcripts']} coding "
        f"exons ±{params.flank} bp): "
        + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in states.items())
        + "."
    )
    for gene in genes:
        if gene["state"] in (ec.PARTIAL, ec.NOT_READABLE):
            human_output(
                f"  {gene['gene']}: "
                + "; ".join(
                    f"exon{'s' * (len(n) > 1)} {ec.ranges(n)} {s.replace('_', ' ')}"
                    for s, n in gene["not_callable"].items()
                )
            )
    human_output(f"{ec.NOTE}\nOutputs: {json_path}, {table.name}")


cmd_callability = vcf_input_handler(VcfCallabilityParams, run_callability)
