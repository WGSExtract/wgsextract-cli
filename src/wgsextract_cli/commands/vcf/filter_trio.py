import logging
import os
import subprocess
import tempfile
from pathlib import Path

from wgsextract_cli.api.vcf import VcfFilterParams, VcfTrioParams
from wgsextract_cli.core.alignment_health import warn_about_alignment
from wgsextract_cli.core.atomic_output import atomic_outputs_enabled, safe_output
from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.reference_assets import n_count_paths, read_fai_lengths
from wgsextract_cli.core.reference_resolver import ReferenceLibrary
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    popen,
    run_command,
)
from wgsextract_cli.core.variant_files import (
    calculate_bam_md5,
    ensure_vcf_indexed,
    ensure_vcf_prepared,
    get_vcf_samples,
    normalize_vcf_chromosomes,
    vcf_chromosomes,
    verify_paths_exist,
)

from .._extract_helpers import cleanup_files
from .basic import record_variants, require_vcf_input, vcf_input_handler
from .trio_alignments import (
    CLUSTER_DISTANCE,
    DEFAULT_MIN_DEPTH,
    is_alignment,
    joint_filter,
    joint_trio_vcf,
    remove_index,
    split_multiallelic,
    trio_contig_lengths,
    write_isolated_denovo,
)


def _text(path: Path | None) -> str | None:
    return os.fspath(path) if path else None


def write_gaps_bed(ref_path: str, bed_path: str) -> bool:
    """
    Convert the reference's Count-Ns ``_nbin.csv`` sidecar to a BED of N runs.

    The sidecar is tab-separated with ``#`` comment/header lines and columns
    SN, BinID, Start (0-based), Size; numbers carry thousands separators.
    Returns False when the sidecar is missing.
    """
    nbin_file = n_count_paths(ref_path)[1]
    if not Path(nbin_file).exists():
        return False
    with (
        open(nbin_file, encoding="utf-8", newline="\n") as f_in,
        open(bed_path, "w", newline="\n") as f_out,
    ):
        for line in f_in:
            parts = line.rstrip("\n").split("\t")
            if line.startswith("#") or len(parts) < 4:
                continue
            try:
                start = int(parts[2].replace(",", ""))
                size = int(parts[3].replace(",", ""))
            except ValueError:
                continue
            f_out.write(f"{parts[0]}\t{start}\t{start + size}\n")
    return True


def run_filter(params: VcfFilterParams) -> None:
    input_file = require_vcf_input(params)
    verify_paths_exist({"--input": input_file})

    verify_dependencies(["bcftools", "tabix"])
    log_dependency_info(["bcftools", "tabix"])

    logging.debug(f"Input file: {str(absolute_path(input_file))}")

    outdir = os.fspath(params.outdir or absolute_path(input_file).parent)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    logging.debug(f"Output directory: {str(absolute_path(outdir))}")
    out_vcf = str(Path(outdir, "filtered.vcf.gz"))

    # Resolve reference if needed for gap filtering or gene resolution
    md5_sig = calculate_bam_md5(input_file, None)
    lib = ReferenceLibrary(params.ref, md5_sig, input_path=input_file)
    logging.debug(f"Resolved reference: {lib.fasta}")

    # Gene-based region resolution
    region = params.region
    if params.gene:
        from wgsextract_cli.core.gene_map import gene_coords

        maps = [
            str(Path(d, f"genes_{canonical_build(lib.build or 'hg38')}.tsv"))
            for d in ("ref", "microarray")
        ]
        resolved_region = gene_coords(
            lib.resource_root(*maps) or str(absolute_path(input_file).parent),
            params.gene,
            lib.build or "hg38",
        )
        logging.info(f"Resolved gene {params.gene} to {resolved_region}")
        region = resolved_region

    region_args = ["-r", region] if region else []
    expr_args = ["-i", params.expr] if params.expr else []

    input_vcf = ensure_vcf_prepared(input_file)
    if Path(out_vcf).exists() and any(
        Path(p).exists() and os.path.samefile(out_vcf, p)
        for p in (input_file, input_vcf)
    ):
        raise UsageError(
            f"vcf filter would overwrite its input {input_file}; "
            "choose another --outdir."
        )
    logging.info(LOG_MESSAGES["vcf_filtering"].format(input=input_vcf, output=out_vcf))
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_filter_") as work:
        exclude_args = []
        if params.exclude_near_gaps:
            gaps_bed = str(Path(work, "gaps.bed"))
            if not lib.fasta:
                warn(
                    "gaps_not_excluded",
                    "Gap exclusion requested but reference genome not resolved.",
                )
            elif write_gaps_bed(lib.fasta, gaps_bed):
                logging.info(f"Excluding variants in reference N runs ({lib.fasta})")
                exclude_args = ["-T", f"^{gaps_bed}"]
            else:
                warn(
                    "gaps_not_excluded",
                    "Gap exclusion requested but Count Ns output (_nbin.csv) not found.",
                )
        partial = [out_vcf, out_vcf + ".tbi", out_vcf + ".csi"]
        try:
            with cleanup_files(
                on_failure=partial, preserve_outputs=atomic_outputs_enabled()
            ):
                with safe_output(out_vcf) as staged:
                    run_command(
                        ["bcftools", "view"]
                        + region_args
                        + expr_args
                        + exclude_args
                        + ["-Oz", "-o", staged, input_vcf]
                    )
                ensure_vcf_indexed(out_vcf)
        except (OSError, subprocess.SubprocessError, RuntimeError) as e:
            raise WGSExtractError(f"VCF filtering failed: {e}") from e
    record_variants(out_vcf, "filtered_vcf", vcf_record_count(out_vcf))


cmd_filter = vcf_input_handler(VcfFilterParams, run_filter)


# bcftools genotype classes: "mis" is a missing genotype (a parent with no record
# here is ./. after the merge; GT="." never matches it), "AA" is hom-alt ("hom"
# would also match 0/0).
_TRIO_FILTERS = {
    # Child het, both parents assessed reference.
    "denovo": '{p}="het" && {m}="ref" && {f}="ref"',
    # Child hom-alt, both parents het.
    "recessive": '{p}="AA" && {m}="het" && {f}="het"',
    # Simplified: child het, exactly one parent het, the other reference.
    "comphet": ('{p}="het" && (({m}="het" && {f}="ref") || ({m}="ref" && {f}="het"))'),
}


def _vcf_trio_expressions(
    mode: str, merged_vcf: str, p_idx: int, m_idx: int, f_idx: int
) -> tuple[str, str | None]:
    p, m, f = (f"GT[{i}]" for i in (p_idx, m_idx, f_idx))
    pattern = _TRIO_FILTERS[mode].format(p=p, m=m, f=f)
    if mode == "recessive":
        return pattern, None
    header = run_command(
        ["bcftools", "view", "-h", merged_vcf], capture_output=True
    ).stdout

    def confidence(idx: int) -> tuple[str, str]:
        if "##FORMAT=<ID=GQ," not in header:
            # Valid expressions even without a GQ field.
            return (
                f'(GT[{idx}]="ref" && GT[{idx}]="het")',
                f'(GT[{idx}]="ref" || GT[{idx}]!="ref")',
            )
        assessed = f'(GT[{idx}]!~"\\." && GQ[{idx}]!="." && GQ[{idx}]>=20)'
        uncertain = f'(GT[{idx}]~"\\." || GQ[{idx}]="." || GQ[{idx}]<20)'
        if "##FORMAT=<ID=FT," in header:
            passed = f'(FT[{idx}]="PASS" || FT[{idx}]=".")'
            failed = f'(FT[{idx}]!="PASS" && FT[{idx}]!=".")'
            assessed = f"({assessed} && {passed})"
            uncertain = f"({uncertain} || {failed})"
        return assessed, uncertain

    (ma, mu), (fa, fu) = confidence(m_idx), confidence(f_idx)
    confident = f"({ma} && {fa})"
    uncertain = f"({mu} || {fu})"
    mr, fr = f'({m}="ref" || {mu})', f'({f}="ref" || {fu})'
    if mode == "denovo":
        possible = f'{p}="het" && {mr} && {fr}'
    else:
        mh, fh = f'({m}="het" || {mu})', f'({f}="het" || {fu})'
        possible = f'{p}="het" && (({mh} && {fr}) || ({mr} && {fh}))'
    return f"({pattern}) && {confident}", f"({possible}) && {uncertain}"


def trio_sample_indices(p_vcf: str, m_vcf: str, f_vcf: str) -> tuple[int, int, int]:
    """
    Column of each role's (first) sample in `bcftools merge p m f` output.

    merge concatenates samples in input-file order, and --force-samples renames
    duplicates (e.g. "2:sample"), so positions are reliable while names are not.
    """
    counts = []
    for path in (p_vcf, m_vcf, f_vcf):
        n = len(get_vcf_samples(path))
        if n == 0:
            raise WGSExtractError(f"Trio VCF has no samples: {path}")
        counts.append(n)
    return 0, counts[0], counts[0] + counts[1]


def _summarize_trio_output(mode: str, out_vcf: str) -> None:
    """Log the mode's variant (and HIGH impact) counts and record its output."""
    metrics: dict[str, int | None] = {"variants": None}
    try:
        metrics["variants"] = vcf_record_count(out_vcf)
        summary = f"{mode.upper()} results: {metrics['variants']} total variants"
        header = run_command(["bcftools", "view", "-h", out_vcf], capture_output=True)
        if "ID=CSQ," in header.stdout:
            command = ["bcftools", "query", "-i", 'CSQ~"HIGH"', "-f", "%POS\n", out_vcf]
            with tempfile.TemporaryFile(dir=Path(out_vcf).parent) as err:
                with popen(
                    command, stdout=subprocess.PIPE, stderr=err, text=True
                ) as proc:
                    assert proc.stdout is not None
                    metrics["high_impact"] = sum(1 for _ in proc.stdout)
                    if proc.wait():
                        raise subprocess.CalledProcessError(proc.returncode, command)
            summary += f", {metrics['high_impact']} HIGH impact"
        logging.info(summary)
    except (OSError, subprocess.SubprocessError) as e:
        logging.debug("Could not summarize trio filter output %s: %s", out_vcf, e)
    current_result().add_output(out_vcf, f"trio_{mode}", metrics=metrics)
    current_result().add_metrics(**{f"{mode}_variants": metrics["variants"]})


def _is_gvcf(vcf: str) -> bool:
    header = run_command(["bcftools", "view", "-h", vcf], capture_output=True).stdout
    return (
        "##GVCFBlock" in header
        or "##ALT=<ID=NON_REF" in header
        or ("##ALT=<ID=*," in header)
    )


def _gvcf_merge_args(params: VcfTrioParams, vcfs: list[str]) -> list[str]:
    """`--gvcf <ref>` when an input is a gVCF: bcftools then fills each sample's
    reference block in at the other samples' sites. Without it, a parent's
    block that doesn't start at the child's site reads as missing, and the de
    novo list fills with variants the parent was checked for."""
    if not any(_is_gvcf(v) for v in vcfs):
        return []
    ref = ReferenceLibrary(params.ref, None, input_path=vcfs[0]).fasta
    if not ref:
        warn(
            "gvcf_reference_missing",
            "The trio includes gVCFs but no reference was found (--ref); their "
            "reference blocks only count where they start at a variant.",
        )
        return []
    return ["--gvcf", ref]


def run_trio(params: VcfTrioParams) -> None:
    verify_dependencies(["bcftools", "tabix"])

    proband = _text(params.proband or params.vcf_input or params.input)
    mother = _text(params.mother)
    father = _text(params.father)
    if not proband or not mother or not father:
        raise WGSExtractError(
            "Proband (--proband, --vcf-input, or --input), --mother, and --father "
            "(VCFs, or BAM/CRAMs) are all required."
        )
    verify_paths_exist({"--proband": proband, "--mother": mother, "--father": father})

    outdir = os.fspath(params.outdir or absolute_path(proband).parent)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    result = current_result()
    result.set_manifest_stem(Path(outdir, "trio"))
    for path, role in ((proband, "proband"), (mother, "mother"), (father, "father")):
        result.add_input(path, role)
    modes = [params.mode] if params.mode != "all" else list(_TRIO_FILTERS)

    alignments = [is_alignment(p) for p in (proband, mother, father)]
    if all(alignments):
        _trio_from_alignments(params, [proband, mother, father], outdir, modes)
        return
    if any(alignments):
        raise WGSExtractError(
            "Give all three as VCFs or all three as BAM/CRAMs; a trio can't mix them."
        )

    p_vcf = ensure_vcf_prepared(proband)
    m_vcf = ensure_vcf_prepared(mother)
    f_vcf = ensure_vcf_prepared(father)
    for mode in modes:
        names = [f"trio_{mode}.vcf.gz"]
        if mode in ("denovo", "comphet"):
            names.append(f"trio_{mode}_inconclusive.vcf.gz")
        for name in names:
            output = Path(outdir, name)
            if output.exists() and any(
                output.samefile(path)
                for path in (proband, mother, father, p_vcf, m_vcf, f_vcf)
            ):
                raise UsageError(
                    f"vcf trio would overwrite its input {output}; "
                    "choose another --outdir."
                )
    p_idx, m_idx, f_idx = trio_sample_indices(p_vcf, m_vcf, f_vcf)
    region_args = ["-r", params.region] if params.region else []
    gvcf_args = _gvcf_merge_args(params, [p_vcf, m_vcf, f_vcf])

    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_trio_") as work:
        # gVCF merging fetches reference bases even on parent-only contigs.
        target_chroms = (
            list(read_fai_lengths(gvcf_args[1])) if gvcf_args else []
        ) or vcf_chromosomes(p_vcf)
        p_norm = normalize_vcf_chromosomes(p_vcf, target_chroms, work)
        m_norm = normalize_vcf_chromosomes(m_vcf, target_chroms, work)
        f_norm = normalize_vcf_chromosomes(f_vcf, target_chroms, work)

        merged_vcf = str(Path(work, "merged_trio.vcf.gz"))
        try:
            run_command(
                ["bcftools", "merge", "--force-samples", *gvcf_args, "-Oz", "-o"]
                + [merged_vcf]
                + region_args
                + [p_norm, m_norm, f_norm],
                capture_output=True,
            )
            ensure_vcf_indexed(merged_vcf)
            # One record per ALT: merging a child's A>T with a mother's A>G
            # makes ALT T,G, where "het" can't tell the mother's G from the T.
            merged_vcf = split_multiallelic(
                merged_vcf, str(Path(work, "merged_split.vcf.gz"))
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise WGSExtractError(f"VCF trio merge failed: {e}") from e

        for mode in modes:
            out_vcf = str(Path(outdir, f"trio_{mode}.vcf.gz"))
            logging.info(
                LOG_MESSAGES["vcf_trio_analysis"].format(mode=mode, output=out_vcf)
            )
            filter_expr, inconclusive_expr = _vcf_trio_expressions(
                mode, merged_vcf, p_idx, m_idx, f_idx
            )
            try:
                remove_index(out_vcf)
                run_command(
                    ["bcftools", "view", "-i", filter_expr, "-Oz", "-o", out_vcf]
                    + [merged_vcf],
                    capture_output=True,
                )
                ensure_vcf_indexed(out_vcf)
            except (OSError, subprocess.SubprocessError) as e:
                raise WGSExtractError(
                    f"VCF trio filtering failed for {mode}: {e}"
                ) from e
            logging.info(LOG_MESSAGES["vcf_trio_complete"].format(output=out_vcf))
            _summarize_trio_output(mode, out_vcf)
            if inconclusive_expr:
                inconclusive = str(Path(outdir, f"trio_{mode}_inconclusive.vcf.gz"))
                remove_index(inconclusive)
                run_command(
                    ["bcftools", "view", "-i", inconclusive_expr, "-Oz", "-o",
                     inconclusive, merged_vcf],
                    capture_output=True,
                )  # fmt: skip
                ensure_vcf_indexed(inconclusive)
                _summarize_trio_output(f"{mode}_inconclusive", inconclusive)
                count = vcf_record_count(inconclusive)
                if count:
                    warn(
                        "inconclusive_parent_genotypes",
                        f"{count:,} {mode} candidates have inconclusive inheritance: "
                        "a parental genotype is missing, partial, filtered, "
                        "or lacks GQ >=20. "
                        f"They are not classified as {mode}; see {inconclusive}.",
                        count=count,
                        mode=mode,
                    )


cmd_trio = vcf_input_handler(VcfTrioParams, run_trio)


def _trio_from_alignments(
    params: VcfTrioParams, alignments: list[str], outdir: str, modes: list[str]
) -> None:
    from wgsextract_cli.core.builds import (
        is_hg37_build,
        is_hg38_build,
        ploidy_for_build,
    )
    from wgsextract_cli.core.utils import get_resource_defaults

    from ..info.metrics import sample_sex

    verify_dependencies(["bcftools", "tabix", "samtools"])
    proband = alignments[0]
    # Joint calling compares reads at the same positions: the chromosomes being
    # called must have the same names and lengths in all three (extra decoy or
    # alt contigs don't matter).
    region = params.region
    called = [trio_contig_lengths(a, region) for a in alignments]
    if not called[0]:
        raise WGSExtractError(
            f"{proband} has none of the chromosomes to call "
            f"({region or 'chr1-22, chrX'})."
        )
    for role, alignment, contigs in zip(
        ("mother", "father"), alignments[1:], called[1:], strict=True
    ):
        if contigs != called[0]:
            differing = sorted(
                name
                for name in set(called[0]) | set(contigs)
                if called[0].get(name) != contigs.get(name)
            )
            raise WGSExtractError(
                f"The {role}'s alignment {Path(alignment).name} isn't on the same "
                f"reference as the proband's (chromosomes differ: "
                f"{', '.join(differing[:5])}); realign them to one reference."
            )
    lib = ReferenceLibrary(
        params.ref, calculate_bam_md5(proband, None), input_path=proband
    )
    if not lib.fasta:
        raise WGSExtractError(
            "Reference genome required to genotype the trio's alignments; pass --ref."
        )
    if lib.ploidy_file:
        ploidy_args = ["--ploidy-file", lib.ploidy_file]
    elif lib.build and (is_hg37_build(lib.build) or is_hg38_build(lib.build)):
        ploidy_args = ["--ploidy", ploidy_for_build(lib.build)]
    else:
        # T2T's sex-chromosome coordinates differ: guessed ploidy would be wrong.
        raise WGSExtractError(
            f"No ploidy for reference build {lib.build or 'unknown'}: add a ploidy "
            "file to the reference library (see 'wgsextract ref bootstrap')."
        )
    from wgsextract_cli.core.sample_ploidy import sex_dependent_ploidy

    sex_dependent = sex_dependent_ploidy(ploidy_args, contigs=set(called[0]))
    # An arbitrary sex code is harmless only when all called ploidies agree.
    proband_sex = params.proband_sex
    if not proband_sex and sex_dependent:
        inferred = sample_sex(proband, outdir, reference=lib.fasta)
        if inferred not in ("Male", "Female"):
            raise WGSExtractError(
                f"Couldn't tell the proband's sex from {proband}'s chrX/chrY coverage; "
                "pass --proband-sex male or female."
            )
        proband_sex = inferred.lower()
    proband_sex = "M" if proband_sex == "male" else "F"
    for alignment in alignments:
        warn_about_alignment(alignment, lib.fasta)
    threads = int(get_resource_defaults(params.threads, None)[0])
    joint = joint_trio_vcf(
        alignments,
        [proband_sex, "F", "M"],
        lib.fasta,
        ploidy_args,
        str(Path(outdir, "trio_joint.vcf.gz")),
        params.region,
        threads,
    )
    logging.info(f"Joint trio genotypes: {joint}")
    current_result().set_reference(lib.fasta, lib.build)
    current_result().add_output(joint, "trio_joint")
    min_depth = params.min_depth or DEFAULT_MIN_DEPTH
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_trio_") as work:
        split = split_multiallelic(joint, str(Path(work, "split.vcf.gz")))
        for mode in modes:
            _filter_joint_trio(mode, split, outdir, min_depth)


def _filter_joint_trio(mode: str, split: str, outdir: str, min_depth: int) -> None:
    out_vcf = str(Path(outdir, f"trio_{mode}.vcf.gz"))
    logging.info(LOG_MESSAGES["vcf_trio_analysis"].format(mode=mode, output=out_vcf))
    try:
        remove_index(out_vcf)
        run_command(
            ["bcftools", "view", "-i", joint_filter(mode, min_depth)]
            + ["-Oz", "-o", out_vcf, split],
            capture_output=True,
        )
        ensure_vcf_indexed(out_vcf)
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"VCF trio filtering failed for {mode}: {e}") from e
    logging.info(LOG_MESSAGES["vcf_trio_complete"].format(output=out_vcf))
    _summarize_trio_output(mode, out_vcf)
    if mode == "denovo":
        isolated_vcf = str(Path(outdir, "trio_denovo_isolated.vcf.gz"))
        isolated, total = write_isolated_denovo(out_vcf, isolated_vcf)
        current_result().add_output(
            isolated_vcf, "trio_denovo_isolated", metrics={"variants": isolated}
        )
        current_result().add_metrics(denovo_isolated_variants=isolated)
        logging.info(
            f"{isolated:,} of the {total:,} de novo candidates have no other within "
            f"{CLUSTER_DISTANCE:,} bp (clusters are typically misaligned reads in "
            f"repeats): {isolated_vcf}"
        )
