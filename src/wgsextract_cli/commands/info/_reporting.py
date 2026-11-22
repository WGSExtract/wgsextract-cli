"""Render info results and record their metrics and sample facts."""

import csv
import io
from typing import Any

from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.builds import build_from_chr1_length, canonical_build
from wgsextract_cli.core.messages import SYSTEM_MESSAGES
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.sample_types import is_platform
from wgsextract_cli.core.sequencing import (
    mixed_platforms_message,
    platform_from_metadata,
    read_group_platforms,
)
from wgsextract_cli.core.ultima import read_groups

from .metrics import (
    DEPTH_COUNTED,
    LONG_READ_MIN_LEN,
    MIN_LONG_READ_MAPPED_PCT,
    MIN_LONG_READ_N50,
    SEX_INFERENCE_VERSION,
    SexCall,
    is_missing_cram_reference,
    record_sex,
)
from .render import MAPPING_UNAVAILABLE, render_info


def _print_detailed(
    data: dict[str, Any], as_csv: bool, sample: tuple[str, str], show: bool = True
) -> None:
    _note_mixed_platforms(data, *sample)
    _record_info(data, sample[0])
    _record_facts(data, *sample)
    if not show:
        return
    if as_csv:
        human_output(data["chrom_table_csv"], end="")
    else:
        human_output(render_info(data, detailed=True))


def _print_fast(data: dict[str, Any], sample: tuple[str, str]) -> None:
    _note_mixed_platforms(data, *sample)
    _record_info(data, sample[0])
    _record_facts(data, *sample)
    human_output(render_info(data, detailed=False))


# Cache bookkeeping that isn't a result.
_PRIVATE_KEYS = {
    "chrom_table_csv",
    "contig_bases",
    "depth_needs_index",
    "input_signature",
    "first_qname",
    # samtools' raw error names local paths; the warning explains it instead.
    "read_sample_error",
}


def _note_mixed_platforms(data: dict[str, Any], input_file: str, header: str) -> None:
    """Report the platforms of an input whose read groups mix them; platform-aware
    commands refuse it or warn unless told which platform to use."""
    platforms = read_group_platforms(header)
    if len(platforms) < 2:
        return
    data["platforms"] = platforms
    warn(
        "mixed_platforms",
        f"{mixed_platforms_message(input_file, platforms)}; platform-aware commands "
        "refuse it or warn unless --platform, --model-type or --caller chooses.",
        platforms=",".join(platforms),
    )


def _record_facts(data: dict[str, Any], input_file: str, header: str) -> None:
    """Record what `info` found in the input's sample workspace."""
    build = canonical_build(build_from_chr1_length(sequence_lengths(header)))
    if build:
        sample_workspace.record(
            "build",
            input_file,
            build,
            method="alignment header contigs",
            confidence="high",
        )
    platform = platform_from_metadata(header, data.get("first_qname"))
    if is_platform(platform):
        has_pl = any(group.get("PL") for group in read_groups(header))
        sample_workspace.record(
            "platform",
            input_file,
            platform,
            method="read-group PL tag" if has_pl else "first read name pattern",
            confidence="high" if has_pl else "medium",
        )
    if data.get("avg_read_len"):
        sample_workspace.record(
            "read_length",
            input_file,
            round(float(data["avg_read_len"]), 1),
            method="mean length of sampled reads",
            confidence="high",
        )
    if (
        data.get("gender")
        and data.get("sex_inference_version") == SEX_INFERENCE_VERSION
    ):
        call = data.get("sex_call") or (
            "Unknown",
            "cache lacks observed confidence",
            "low",
        )
        record_sex(input_file, SexCall(*call))
    ard = (data.get("metrics") or {}).get("ard_mapped")
    # Only depths from current bookkeeping: not a cache from before #427, nor
    # long reads whose depth couldn't be sampled.
    if ard and isinstance(data.get("contig_bases"), dict):
        sample_workspace.record(
            "mean_coverage",
            input_file,
            round(float(ard), 2),
            method=data.get("depth_method") or DEPTH_COUNTED,
            confidence="high",
        )


def _record_info(data: dict[str, Any], input_file: str) -> None:
    """Put the metrics `info` reports on the result manifest."""
    result = current_result()
    details = {k: v for k, v in data.items() if k not in _PRIVATE_KEYS}
    if "avg_read_len" in details:
        details["avg_read_len"] = details["avg_read_len"] or None
    if data.get("chrom_table_csv"):
        details["chromosomes"] = list(
            csv.DictReader(io.StringIO(data["chrom_table_csv"]))
        )
    result.set_details(details)
    stats = data.get("file_stats") or {}
    health = data.get("alignment_health") or {}
    result.add_metrics(
        reference_model=data.get("ref_model_str"),
        sequencer=data.get("sequencer"),
        # 0 means no reads were sampled, so the length is unknown.
        avg_read_len=data.get("avg_read_len") or None,
        is_paired=data.get("is_paired"),
        avg_insert_size=data.get("avg_insert_size"),
        size_gb=stats.get("size_gb"),
        sorted=stats.get("sorted"),
        indexed=stats.get("indexed"),
        duplicates_marked=health.get("duplicates_marked"),
        duplicate_pct=health.get("duplicate_pct"),
        alt_aware=health.get("alt_aware"),
    )
    if data.get("platforms"):
        result.add_metrics(platforms=",".join(data["platforms"]))
    _record_long_read_qc(data.get("read_lengths"))
    _record_unread_reads(data)
    if "gender" in data:
        result.add_metrics(sex=data["gender"], **data.get("metrics", {}))
        call = data.get("sex_call")
        if call:
            result.add_metrics(sex_method=call[1], sex_confidence=call[2])
        workspace = sample_workspace.workspace_for(input_file)
        if workspace:
            result.add_metrics(declared_sex=workspace.overrides(input_file).get("sex"))
        if data["gender"] == "Unknown":
            result.warn(
                "sex_unknown",
                "The sample's sex could not be inferred from its chrX/chrY reads.",
            )
        if data.get("avg_read_len", 0) > LONG_READ_MIN_LEN:
            result.warn(
                "long_reads",
                SYSTEM_MESSAGES["LongReadSequenceWarning"],
                avg_read_len=data["avg_read_len"],
            )
        ard = data.get("metrics", {}).get("ard_mapped")
        if ard is not None and ard < 10:
            result.warn(
                "low_coverage",
                SYSTEM_MESSAGES["LowCoverageWarning"],
                target="genome",
                depth=round(ard, 3),
                threshold=10,
            )


def _catalog_code(md5_sig: str | None, fasta_name: str | None) -> str | None:
    """The reference catalog's install code for the input's genome, if listed."""
    from wgsextract_cli.core.ref_library.catalog import get_available_genomes

    genomes = get_available_genomes()
    for genome in genomes:
        if md5_sig and genome.get("md5") == md5_sig:
            return genome.get("code") or None
    for genome in genomes:
        if fasta_name and genome.get("final") == fasta_name:
            return genome.get("code") or None
    return None


def _record_unread_reads(data: dict[str, Any]) -> None:
    """Say why the read length, pairing, insert size, sequencer and duplicates
    are missing when no reads could be sampled."""
    if data.get("avg_read_len"):
        return
    error = data.get("read_sample_error")
    name = data.get("filename") or "the input"
    if is_missing_cram_reference(error):
        model = reference_model_name(data.get("ref_model_str") or "")
        known = bool(model) and model != "Unknown"
        needed = f"the {model} reference" if known else "its reference genome"
        code = _catalog_code(data.get("md5_signature"), data.get("suggested_ref_file"))
        install = f"ref library --install {code}" if code else "ref library"
        warn(
            "cram_reference_missing",
            f"Could not read the reads in {name}: {needed} is not installed. "
            f"Install it from Reference Data (wgsextract {install}) or pass "
            "--ref with its FASTA. Read length, pairing, insert size, "
            "sequencer and duplicates are unknown until then.",
            reference_model=model if known else None,
        )
        return
    detail = f": {error.splitlines()[-1]}" if error else ""
    warn(
        "reads_unreadable",
        f"Could not read the reads in {name}{detail}. Read length, pairing, "
        "insert size, sequencer and duplicates are unknown.",
    )


def _record_long_read_qc(lengths: dict[str, Any] | None) -> None:
    """#338: library failures show up as short reads and poor mapping."""
    if not lengths:
        return
    result = current_result()
    n50 = lengths.get("read_n50")
    mapped = lengths.get("mapped_read_pct")
    result.add_metrics(read_n50=n50)
    if mapped is not None:
        result.add_metrics(mapped_read_pct=mapped)
    if n50 and n50 < MIN_LONG_READ_N50:
        warn(
            "short_read_n50",
            f"Read N50 is {n50:,} bp ({lengths['method']}), under "
            f"{MIN_LONG_READ_N50:,} bp: reads this short usually mean degraded "
            "DNA or a failed library.",
            read_n50=n50,
            exact=bool(lengths.get("exact")),
            threshold=MIN_LONG_READ_N50,
        )
    if mapped is not None and mapped < MIN_LONG_READ_MAPPED_PCT:
        warn(
            "low_mapped_read_fraction",
            f"Only {mapped:.1f}% of reads map, under "
            f"{MIN_LONG_READ_MAPPED_PCT:.0f}%: check for contamination, a failed "
            "library or the wrong reference.",
            mapped_read_pct=mapped,
            threshold=MIN_LONG_READ_MAPPED_PCT,
        )
    if lengths.get("mapped_read_method") == MAPPING_UNAVAILABLE:
        warn(
            "mapped_read_fraction_unavailable",
            "The mapped reads could not be estimated: no read starts in the "
            "sampled windows. samtools flagstat counts them exactly.",
        )


def reference_model_name(described: str) -> str:
    """The model in a `ReferenceModel.describe()` line: "hg38 (Chr), rCRS, 195
    SNs" and "Cat_Fca126, , 71 SNs" (no chr1, so no naming) alike."""
    return described.split(",", 1)[0].split(" (", 1)[0]
