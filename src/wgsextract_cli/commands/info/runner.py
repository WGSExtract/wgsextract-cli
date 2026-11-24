import argparse
import csv
import io
import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.info import (
    CalculateCoverageParams,
    CoverageSampleParams,
    InfoParams,
)
from wgsextract_cli.core.alignment.health import alignment_health
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.alignment.samtools_commands import alignment_index
from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    build_from_path,
)
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import is_fasta_path
from wgsextract_cli.core.ref_library.models import identify_reference, sequence_lengths
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.table_writer import (
    check_format,
    provenance,
    table_path,
    write_table,
)
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import UsageError, WGSExtractError
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    is_sorted,
    verify_paths_exist,
)

from . import coverage as reports
from ._reporting import _print_detailed, _print_fast
from ._reporting import reference_model_name as reference_model_name
from ._tables import CHROMOSOME_TABLE as CHROMOSOME_TABLE
from ._tables import QC_TABLE as QC_TABLE
from ._tables import _export_tables
from .metrics import (
    DEPTH_COUNTED,
    DEPTH_SAMPLED,
    LONG_READ_MIN_LEN,
    SEX_INFERENCE_VERSION,
    SEX_MEASUREMENT_UNAVAILABLE,
    IdxStat,
    contig_key,
    determine_sequencer,
    generate_chrom_table,
    get_file_stats,
    infer_sex_call,
    input_signature,
    is_long_read_library,
    long_read_lengths,
    long_read_mapped_pct,
    parse_idxstats,
    run_body_sample,
)
from .ncounts import n_lengths_digest, reference_n_lengths
from .render import (
    MAPPING_UNAVAILABLE,
    exact_mapped_read_pct,
    region_file_tag,
    sampled_mapped_bases,
    sampled_primary_share,
)

COVERAGE_COMMANDS = ("calculate-coverage", "coverage-sample")


def _load_cache(path: str, signature: dict[str, int | str] | None) -> dict[str, Any]:
    """Load the metrics cache, discarding it unless it was built from this exact input.

    Caches written before input signatures were recorded are treated as stale.
    """
    if signature is None:
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        logging.debug(f"No cache file at {path}")
        return {}
    except (OSError, ValueError) as e:
        logging.debug(f"Ignoring unreadable cache {path}: {e}")
        return {}
    if not isinstance(data, dict) or data.get("input_signature") != signature:
        logging.debug(f"Ignoring stale cache {path} (input changed or old format)")
        return {}
    if (
        data.get("gender")
        and data.get("sex_inference_version") != SEX_INFERENCE_VERSION
    ):
        return {}
    logging.debug(f"Cache file found: {path}")
    return data


def _save_cache(path: str, data: dict[str, Any]) -> bool:
    try:
        # Atomic: concurrent info calls may read the cache while one writes it.
        # Readable like the plain write it replaced: other users and tools
        # (Install Lab's oracle) read this cache.
        atomic_write(path, json.dumps(data, indent=2).encode("utf-8"), new_mode=0o666)
    except (OSError, TypeError) as exc:
        logging.warning(f"Failed to write metrics cache {path}: {exc}")
        return False
    logging.debug(f"Metrics cached to {path}")
    return True


def _guess_ref_model(
    md5_sig: str | None, header: str, user_fasta: str | None
) -> tuple[str, str]:
    """Return (reference model description, suggested reference filename).

    A FASTA name counts as evidence only when the user named that FASTA; one a
    library search picked says nothing about the input.
    """
    model = identify_reference(header, md5_sig, user_fasta)
    if model is None:
        num_sns = len(sequence_lengths(header))
        return f"Unknown, {num_sns} SNs", ""
    logging.debug(f"Reference identified as {model.name} by {model.evidence}")
    return model.describe(), model.fasta


def _fast_metrics(
    input_p: str,
    header: str,
    md5_sig: str | None,
    resolved_ref: str | None,
    ref_model: tuple[str, str],
) -> dict[str, Any]:
    from wgsextract_cli.core.variant_files import get_file_version

    cram_opt = (
        ["-T", resolved_ref] if resolved_ref and Path(resolved_ref).is_file() else []
    )

    t0 = time.time()
    sorted_status = is_sorted(input_p, cram_opt, header=header)
    logging.debug(f"Sort check: {sorted_status} (took {time.time() - t0:.3f}s)")

    size_gb, indexed = get_file_stats(input_p)
    ref_model_str, ref_fname = ref_model

    t0 = time.time()
    sample = run_body_sample(input_p, cram_opt)
    logging.debug(f"Body sampling took {time.time() - t0:.3f}s")
    avg_len, first_qname = sample.avg_len, sample.first_qname
    # No reads could be read (a CRAM without its reference, say): what they
    # would have told us is unknown, not zero or single-end.
    unread = sample.reads == 0
    sequencer = determine_sequencer(first_qname, header)

    return {
        "filename": Path(input_p).name,
        "md5_signature": md5_sig,
        "file_stats": {
            "sorted": sorted_status,
            "indexed": indexed,
            "size_gb": size_gb,
            "version": get_file_version(input_p),
        },
        "ref_model_str": ref_model_str,
        "suggested_ref_file": ref_fname,
        "avg_read_len": avg_len,
        "std_read_len": None if unread else sample.std_len,
        "is_paired": None if unread else sample.is_paired,
        "avg_insert_size": None if unread else sample.avg_tlen,
        "std_insert_size": None if unread else sample.std_tlen,
        "read_lengths": long_read_lengths(sample)
        if not unread and is_long_read_library(avg_len, header, first_qname)
        else None,
        "sequencer": None if unread and sequencer == "Unknown" else sequencer,
        "read_sample_error": sample.error if unread else None,
        "first_qname": first_qname,
        "alignment_health": alignment_health(
            input_p, cram_opt[1] if cram_opt else None
        ),
    }


def _detailed_metrics(
    data: dict[str, Any],
    idx_stats: list[Any],
    total_unmapped: int,
    coverage_map: dict[str, str],
    n_lengths: dict[str, int],
    mapped_bases: dict[str, float] | None = None,
    input_file: str | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    total_reads = sum(s["mapped"] + s["unmapped"] for s in idx_stats)
    avg_len = data["avg_read_len"]
    ref_model_str = data["ref_model_str"]
    ref_model_name = reference_model_name(ref_model_str)

    mapped_by_key: dict[str, int] = {}
    for s in idx_stats:
        if s["name"] != "*":
            key = contig_key(s["name"])
            mapped_by_key[key] = mapped_by_key.get(key, 0) + s["mapped"]
    call = infer_sex_call(idx_stats, alignment=input_file, reference=reference)
    gender = call.sex

    chrom_table = generate_chrom_table(
        idx_stats,
        avg_len,
        gender,
        ref_model_name,
        coverage_map,
        n_counts=n_lengths,
        mapped_bases=mapped_bases,
    )
    total_row = next(r for r in chrom_table if r[1] == "Total")
    other_row = next((r for r in chrom_table if r[1] == "Other"), None)
    mapped_segs = total_row[4] + (other_row[4] if other_row else 0)
    total_bases = total_row[8] + (other_row[8] if other_row else 0)
    eff_len = total_row[2] - total_row[3] + 0.0001
    # Measured mapped bases exclude soft clips, so the unmapped reads are added
    # to them, rather than every record counted at the mean read length.
    raw_bases = (
        total_bases + total_unmapped * avg_len
        if mapped_bases is not None
        else total_reads * avg_len
    )

    present = {k for k, v in mapped_by_key.items() if v > 0}
    content = {
        "Auto": any(k.isdigit() for k in present),
        "X": "X" in present,
        "Y": "Y" in present,
        "Mito": "M" in present,
        "Other": any(not k.isdigit() and k not in {"X", "Y", "M"} for k in present),
        "Unmap": total_unmapped > 0,
    }

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(
        [
            "Seq Name",
            "Model Len",
            "Model N Len",
            "# Segs Map",
            "Map Gbases",
            "Map ARD",
            "Breadth Coverage",
        ]
    )
    for row in chrom_table:
        cw.writerow(
            [row[1], row[2], row[3], row[4], f"{row[5]:.2f}", f"{row[6]:.0f}", row[7]]
        )

    return {
        "gender": gender,
        "sex_call": list(call),
        "sex_inference_version": SEX_INFERENCE_VERSION,
        "refined_ns": bool(n_lengths),
        # The N counts depth and breadth left out; new ones recompute the table.
        "n_lengths_digest": n_lengths_digest(n_lengths),
        "file_content": ", ".join(k for k, v in content.items() if v),
        "depth_method": DEPTH_SAMPLED if mapped_bases is not None else DEPTH_COUNTED,
        # Marks the depths current; left out when long reads couldn't be
        # sampled, so the next run (or genotype QC) tries again.
        "contig_bases": {row[1]: round(row[8]) for row in chrom_table}
        if mapped_bases is not None or avg_len <= LONG_READ_MIN_LEN
        else None,
        "metrics": {
            "ard_mapped": total_row[8] / eff_len,
            "ard_raw": raw_bases / eff_len,
            "gbases_mapped": total_bases / (10**9),
            "gbases_raw": raw_bases / (10**9),
            "reads_mapped_m": mapped_segs / 1_000_000,
            "reads_raw_m": total_reads / 1_000_000,
            "reads_mapped_pct": (mapped_segs / total_reads * 100)
            if total_reads > 0
            else 0,
            "reads_raw_pct": 100.0,
        },
        "chrom_table_csv": si.getvalue(),
    }


def _warn_depth_needs_index(input_file: str) -> None:
    logging.warning(
        f"{Path(input_file).name} has no index, so long reads' depth is "
        "estimated from their records and overstated; index it "
        "(samtools index) to measure it."
    )


InfoLikeParams = InfoParams | CalculateCoverageParams | CoverageSampleParams


# Up to this many records, the mapped percentage is counted exactly: a
# whole-file flagstat that takes seconds and needs no sampled read starts.
EXACT_MAPPING_MAX_RECORDS = 200_000


def _mapped_reads(
    input_file: str,
    ref: str | None,
    idx_stats: Sequence[IdxStat],
    temp_parent: str,
) -> dict[str, Any] | None:
    """Long reads' mapped percentage (#338): counted when the file is small,
    else from idxstats and the primary share of records sampled through the
    index. None when it failed, so the next run tries again."""
    records = sum(s["mapped"] + s["unmapped"] for s in idx_stats)
    try:
        if records <= EXACT_MAPPING_MAX_RECORDS:
            return {
                "mapped_read_pct": exact_mapped_read_pct(input_file, ref, temp_parent),
                "mapped_read_method": f"exact, from all {records:,} records",
            }
        sampled = sampled_primary_share(input_file, ref, idx_stats, temp_parent)
    except (OSError, WGSExtractError) as e:
        logging.warning(f"Could not measure the mapped reads of {input_file}: {e}")
        return None
    if sampled is None:
        return {"mapped_read_pct": None, "mapped_read_method": MAPPING_UNAVAILABLE}
    share, starts = sampled
    return {
        "mapped_read_pct": long_read_mapped_pct(idx_stats, share),
        "mapped_read_method": f"estimate from {starts:,} sampled records",
    }


def run_info(params: InfoLikeParams, *, as_csv: bool = False) -> None:
    """``info`` and its coverage subcommands; ``as_csv`` prints the detailed
    table as CSV (CLI only)."""
    from wgsextract_cli.core.variant_files import resolve_reference
    from wgsextract_cli.core.warnings import print_warning

    if isinstance(params, CalculateCoverageParams) and params.status:
        reports.coverage_status(params)
        return
    start_time = time.time()
    verify_dependencies(["samtools"])
    log_dependency_info(["samtools"])
    input_file = str(params.input)
    ref = str(params.ref) if params.ref else None
    outdir_arg = str(params.outdir) if params.outdir else None

    verify_paths_exist({"--input": input_file})
    logging.debug(f"Input file: {str(absolute_path(input_file))}")

    table_format = params.table_format
    check_format(table_format)
    info_cmd = None if isinstance(params, InfoParams) else params.COMMAND.split()[-1]
    region = None if isinstance(params, InfoParams) else params.region
    detailed = (
        not isinstance(params, InfoParams) or params.detailed or bool(table_format)
    )
    bins = isinstance(params, CalculateCoverageParams) and params.bins
    # Only `info` can be told not to save its cache (as a read-only server does).
    cache = not isinstance(params, InfoParams) or params.cache

    # If it's a CRAM and we have a ref, resolve it early so samtools can read the header
    initial_ref = None
    if input_file.lower().endswith(".cram") and ref:
        resolved = resolve_reference(ref, None)
        if resolved and Path(resolved).is_file():
            initial_ref = resolved
        else:
            logging.debug("CRAM detected, but reference is not installed or invalid.")

    t0 = time.time()
    header = get_bam_header(input_file, cram_opt=initial_ref)
    if not header:
        raise WGSExtractError(
            f"Could not read header from {input_file}. Samtools might be missing, "
            "the file is corrupt, or it requires a reference (--ref)."
        )
    logging.debug(f"Header fetch took {time.time() - t0:.3f}s")

    md5_sig = calculate_bam_md5(input_file, header=header)
    user_fasta = ref if ref and is_fasta_path(ref) else None
    resolved_ref = resolve_reference(ref, md5_sig)
    # resolve_reference hands back a --ref folder as is when no FASTA in it
    # matches; only a file is a resolved reference.
    if resolved_ref and Path(resolved_ref).is_file():
        ref_note = resolved_ref
    elif resolved_ref:
        ref_note = f"none (no matching FASTA in {resolved_ref})"
    else:
        ref_note = "none"
    logging.debug(f"MD5 signature: {md5_sig}; resolved reference: {ref_note}")
    build = build_from_chr1_length(sequence_lengths(header))
    if resolved_ref and Path(resolved_ref).is_file():
        build = build or build_from_path(Path(resolved_ref).name)
        current_result().set_reference(resolved_ref, build)
    ref_model = _guess_ref_model(md5_sig, header, user_fasta)
    # Only a FASTA the user chose names the N counts; one found to decode the
    # input says nothing the header's signature doesn't.
    reference_ns = reference_n_lengths(sequence_lengths(header), md5_sig, user_fasta)
    n_lengths = reference_ns.n_lengths
    by_targets = isinstance(params, CalculateCoverageParams) and params.by is not None
    # A partial span keeps every coordinate, so its N counts don't matter.
    partial = bool(region) and ":" in str(region)
    if (detailed or bins) and not by_targets and not partial and reference_ns.problem:
        warn(*reference_ns.problem)
    metadata = provenance(
        [input_file],
        reference_build=build,
        sorted_by="the reference's contig order",
        region=region or "",
    )

    if bins:
        from .bins import run_depth_bins

        if input_file.lower().endswith(".cram") and (
            not resolved_ref or not Path(resolved_ref).is_file()
        ):
            raise UsageError("--ref required for detailed mode with CRAM.")
        bins_tsv = run_depth_bins(
            input_file,
            resolved_ref,
            sequence_lengths(header),
            outdir_arg or str(absolute_path(input_file).parent),
            region,
            table_format or "tsv",
            metadata,
            n_lengths=n_lengths,
            display="csv" if as_csv else "tsv",
        )
        current_result().add_output(bins_tsv, "coverage_bins")
        return

    if isinstance(params, CalculateCoverageParams) and params.by is not None:
        from .targets import run_target_coverage

        if input_file.lower().endswith(".cram") and (
            not resolved_ref or not Path(resolved_ref).is_file()
        ):
            raise UsageError("--ref required for --by with CRAM.")
        by = Path(params.by).expanduser()
        verify_paths_exist({"--by": str(by)})
        run_target_coverage(
            input_file,
            resolved_ref,
            sequence_lengths(header),
            by,
            outdir_arg or str(absolute_path(input_file).parent),
            params.min_depth,
            table_format or "tsv",
            metadata,
            region=region,
        )
        return

    if detailed and input_file.lower().endswith(".cram") and not resolved_ref:
        raise UsageError("--ref required for detailed mode with CRAM.")

    outdir = outdir_arg or str(absolute_path(input_file).parent)
    if outdir_arg and (cache or detailed):
        Path(outdir).mkdir(parents=True, exist_ok=True)
    logging.debug(f"Output directory: {str(absolute_path(outdir))}")

    if region:
        # Header lengths are enough to choose sampled windows. Regional work
        # must not gather global read counts, sex, or first-record QC.
        stats = [
            IdxStat(name=name, length=length, mapped=0, unmapped=0)
            for name, length in sequence_lengths(header).items()
        ]
        coverage = reports.coverage_map(
            info_cmd,
            input_file,
            resolved_ref,
            stats,
            outdir,
            region,
            n_lengths,
        )
        current_result().set_details({"region": region, "breadth": coverage})
        with_bins = isinstance(params, CalculateCoverageParams) and params.with_bins
        if as_csv and not with_bins:  # with bins, their CSV is the one stream
            stream = io.StringIO(newline="")
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["Seq Name", "Breadth Coverage"])
            writer.writerows(coverage.items())
            human_output(stream.getvalue(), end="")
        elif not as_csv:
            human_output(
                f"Region: {region}\nSeq Name\tBreadth Coverage\n"
                + "".join(f"{name}\t{breadth}\n" for name, breadth in coverage.items())
            )
        if table_format:
            path = table_path(
                Path(
                    outdir, Path(input_file).name + region_file_tag(region) + "_breadth"
                ),
                table_format,
            )
            write_table(
                path,
                [CHROMOSOME_TABLE[0], CHROMOSOME_TABLE[-1]],
                [
                    (name, float(value.lstrip("~").rstrip("% ").strip()))
                    for name, value in coverage.items()
                ],
                table_format,
                metadata={
                    **metadata,
                    "breadth": "sampled estimate (coverage-sample)"
                    if info_cmd == "coverage-sample"
                    else "exact (calculate-coverage)",
                },
            )
            current_result().add_output(path, "coverage_table")
        if with_bins:
            from .bins import run_depth_bins

            bins_path = run_depth_bins(
                input_file,
                resolved_ref,
                sequence_lengths(header),
                outdir,
                region,
                "tsv",
                metadata,
                reuse=True,
                n_lengths=n_lengths,
                display="csv" if as_csv else "table",
            )
            current_result().add_output(bins_path, "coverage_bins")
        return

    json_cache = str(Path(outdir, f"{Path(input_file).name}.wgse_info.json"))
    table_stem = Path(outdir, Path(input_file).name + region_file_tag(region))
    signature = input_signature(input_file)
    cache = cache and signature is not None
    data = _load_cache(json_cache, signature)
    call = data.get("sex_call")
    sex_unavailable = (
        isinstance(call, list)
        and len(call) == 3
        and call[1] == SEX_MEASUREMENT_UNAVAILABLE
    )
    # Classifying the header is cheap, so it is redone every run. A cache made
    # when it said otherwise (older detection, or a different --ref) is stale,
    # including its chromosome table, which depends on the build.
    if data and data.get("ref_model_str") != ref_model[0]:
        logging.debug(
            f"Ignoring cache {json_cache}: it was made for reference "
            f"{data.get('ref_model_str')!r}, not {ref_model[0]!r}"
        )
        data = {}
    # Long reads' length QC is part of the fast metrics since #338.
    if (
        data.get("avg_read_len")
        and is_long_read_library(data["avg_read_len"], header, data.get("first_qname"))
        and not data.get("read_lengths")
    ):
        logging.debug(f"Ignoring cache {json_cache}: it has no read-length QC")
        data = {}
    # Likewise the sequencer, from the cached first read name and the header.
    if data.get("avg_read_len"):
        data["sequencer"] = determine_sequencer(data.get("first_qname"), header)

    if data.get("avg_read_len") and "alignment_health" not in data:
        # A cache from before these checks existed.
        cram_ref = (
            resolved_ref if resolved_ref and Path(resolved_ref).is_file() else None
        )
        data["alignment_health"] = alignment_health(input_file, cram_ref)
        if cache:
            _save_cache(json_cache, data)

    if info_cmd is None:
        if not detailed and data.get("avg_read_len"):
            logging.debug("Cache hit for fast metrics.")
            _print_fast(data, (input_file, header))
            return
        # The chromosome table hides chrY for a Female, so it is redone along
        # with the sex when the cached call came from an older infer_sex.
        # Likewise a table from before long reads' depth was measured.
        # Long reads' depth that couldn't be sampled for want of an index is
        # redone only once the index exists: without one, idxstats reads the
        # whole file again for the same answer. A mapped percentage that failed
        # to be measured is retried.
        if (
            detailed
            and data.get("chrom_table_csv")
            and (
                isinstance(data.get("contig_bases"), dict)
                or (
                    data.get("depth_needs_index")
                    and alignment_index(input_file) is None
                )
            )
            and not data.get("mapping_failed")
            and data.get("n_lengths_digest") == n_lengths_digest(n_lengths)
            and data.get("sex_inference_version") == SEX_INFERENCE_VERSION
            and (not sex_unavailable or alignment_index(input_file) is None)
        ):
            logging.debug("Cache hit for detailed metrics.")
            if data.get("depth_needs_index"):
                _warn_depth_needs_index(input_file)
            _print_detailed(data, as_csv, (input_file, header))
            if table_format:
                _export_tables(data, table_stem, table_format, metadata)
            return

    if not data.get("avg_read_len"):
        logging.debug("Generating fast metrics...")
        data = _fast_metrics(input_file, header, md5_sig, resolved_ref, ref_model)
    data["input_signature"] = signature

    if not detailed:
        _print_fast(data, (input_file, header))
        if cache:
            _save_cache(json_cache, data)
        logging.debug(f"Total info time: {time.time() - start_time:.3f}s")
        return

    t0 = time.time()
    idx_stats, _genome_len, _total_mapped, total_unmapped = parse_idxstats(input_file)
    logging.debug(f"Idxstats took {time.time() - t0:.3f}s")

    coverage_map = reports.coverage_map(
        info_cmd,
        input_file,
        resolved_ref,
        idx_stats,
        outdir,
        region,
        n_lengths,
    )
    mapped_bases = None
    long_reads = data.get("avg_read_len", 0) > LONG_READ_MIN_LEN
    temp_parent = configured_temp_directory() or outdir
    # Sampling fetches windows through the index. Unaligned reads need none:
    # no contig has any, so their depth is measured as zero.
    data["depth_needs_index"] = (
        long_reads
        and alignment_index(input_file) is None
        and any(s["mapped"] for s in idx_stats)
    )
    if data["depth_needs_index"]:
        _warn_depth_needs_index(input_file)
    elif long_reads:
        try:
            mapped_bases = sampled_mapped_bases(
                input_file, resolved_ref, idx_stats, temp_parent
            )
        except (OSError, WGSExtractError) as e:
            logging.warning(
                f"Could not sample the depth of {Path(input_file).name}, so long "
                f"reads' depth is estimated from their records and overstated: {e}"
            )
    # Unaligned or unindexed reads have no mapped percentage to measure.
    data.pop("mapping_failed", None)
    if (
        data.get("read_lengths")
        and any(s["mapped"] for s in idx_stats)
        and alignment_index(input_file) is not None
    ):
        mapping = _mapped_reads(input_file, resolved_ref, idx_stats, temp_parent)
        if mapping is None:
            data["mapping_failed"] = True
        else:
            data["read_lengths"].update(mapping)
    data.update(
        _detailed_metrics(
            data,
            idx_stats,
            total_unmapped,
            coverage_map,
            n_lengths,
            mapped_bases,
            input_file,
            resolved_ref,
        )
    )

    if data.get("avg_read_len", 0) > LONG_READ_MIN_LEN:
        print_warning("LongReadSequenceWarning")
    if data["metrics"]["ard_mapped"] < 10:
        print_warning("LowCoverageWarning")

    with_bins = isinstance(params, CalculateCoverageParams) and params.with_bins
    # With bins, their table is the report; the chromosome table, breadth and
    # all, is cached for `info --detailed`.
    _print_detailed(data, as_csv, (input_file, header), show=not with_bins)
    if table_format:
        _export_tables(data, table_stem, table_format, metadata)
    if with_bins:
        from .bins import run_depth_bins

        bins_tsv = run_depth_bins(
            input_file,
            resolved_ref,
            sequence_lengths(header),
            outdir,
            region,
            "tsv",
            metadata,
            reuse=True,
            n_lengths=n_lengths,
            display="csv" if as_csv else "table",
        )
        current_result().add_output(bins_tsv, "coverage_bins")
    # A region-limited coverage table describes only part of the file, so it
    # must not replace the whole-file cache.
    if cache and not region and _save_cache(json_cache, data):
        logging.info(LOG_MESSAGES["info_metrics_cached"].format(path=json_cache))
    logging.debug(f"Total info time: {time.time() - start_time:.3f}s")


INFO_PARAMS: dict[str | None, type[InfoLikeParams]] = {
    None: InfoParams,
    "calculate-coverage": CalculateCoverageParams,
    "coverage-sample": CoverageSampleParams,
}


def run(args: argparse.Namespace) -> None:
    """CLI handler of `info` and its coverage subcommands."""
    cls = INFO_PARAMS[getattr(args, "info_cmd", None)]
    cli_handler(cls, run_info, as_csv="csv")(args)
