"""Bounded reference expansion and definition-span evidence for PharmCAT."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.core import reference_evidence as evidence
from wgsextract_cli.core import variant_confidence as vc
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import chromosome_rename_mapping, vcf_chromosomes


@dataclass
class GvcfEvidence:
    """What the gVCF a caller published beside a variant-only input says about
    PharmCAT's definition bases (in PharmCAT's contig names): each base's
    state (core/reference_evidence) and, where a reference record covers it,
    that record's ploidy and depth and genotype quality."""

    path: str
    states: dict[tuple[str, int], evidence.State] = field(default_factory=dict)
    ploidy: dict[tuple[str, int], int] = field(default_factory=dict)
    depth: dict[tuple[str, int], float] = field(default_factory=dict)
    quality: dict[tuple[str, int], float] = field(default_factory=dict)


def gvcf_evidence(
    gvcf: str,
    loci: Iterable[tuple[str, int]],
    to_gvcf: Mapping[str, str],
    thresholds: vc.Thresholds,
    work: str,
) -> GvcfEvidence:
    """The gVCF's evidence at ``loci`` (PharmCAT names; ``to_gvcf`` renames
    them to the gVCF's)."""
    source = evidence.ReferenceEvidence(gvcf)
    back = {name: pharmcat for pharmcat, name in to_gvcf.items()}
    wanted = sorted(
        {(to_gvcf.get(c, c), p) for c, p in loci if to_gvcf.get(c, c) in source.lengths}
    )
    found = GvcfEvidence(gvcf)
    if not wanted:
        return found
    for span in source.spans(thresholds, ((c, p, p) for c, p in wanted)):
        for pos in range(span.start, span.end + 1):
            found.states[(back.get(span.contig, span.contig), pos)] = span.state
    regions = Path(work, "gvcf_loci.tsv")
    regions.write_text(
        "".join(f"{c}\t{p}\t{p}\n" for c, p in wanted), encoding="utf-8", newline="\n"
    )
    records = run_command(
        ["bcftools", "query", "--allow-undef-tags", "-R", str(regions), "-f",
         "%CHROM\t%POS\t%INFO/END\t%REF\t[%GT\t%DP\t%MIN_DP\t%GQ]\n", gvcf],
        capture_output=True,
    ).stdout  # fmt: skip
    wanted_set = set(wanted)
    for line in records.splitlines():
        chrom, start, end, ref, gt, dp, min_dp, gq = line.split("\t")
        alleles = re.split(r"[/|]", gt)
        if not all(a == "0" for a in alleles):
            continue
        first = int(start)
        last = int(end) if end.isdigit() else first + len(ref) - 1
        depths = [float(v) for v in (dp, min_dp) if _is_number(v)]
        for p in range(first, last + 1):
            if (chrom, p) not in wanted_set:
                continue
            key = (back.get(chrom, chrom), p)
            found.ploidy[key] = len(alleles)
            if depths:
                found.depth[key] = min([*depths, found.depth.get(key, min(depths))])
            if _is_number(gq):
                found.quality[key] = min(float(gq), found.quality.get(key, float(gq)))
    return found


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _info(text: str) -> dict[str, str]:
    return dict(item.partition("=")[::2] for item in text.split(";") if item != ".")


def _format(fields: list[str]) -> dict[str, str]:
    return dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))


def _reference_call(
    fields: list[str],
    thresholds: vc.Thresholds,
    mq_kind: str,
) -> vc.CallConfidence:
    result = vc.classify(
        alt=1,
        alleles=re.split(r"[/|]", _format(fields).get("GT", ".")),
        ref=fields[3],
        alt_allele=fields[4],
        filters=fields[6],
        info=_info(fields[7]),
        fmt=_format(fields),
        n_alts=len(fields[4].split(",")),
        thresholds=thresholds,
        reference=True,
        mq_kind=mq_kind,
    )
    assert result is not None
    return result


def _usable(call: vc.CallConfidence) -> bool:
    return (
        call.label != vc.LOW
        and call.depth is not None
        and call.genotype_quality is not None
    )


def _worst_metrics(supporting: list[vc.CallConfidence]) -> list[str]:
    metrics = []
    for attr in ("depth", "genotype_quality"):
        values = [getattr(call, attr) for call in supporting]
        known = [v for v in values if v is not None]
        metrics.append(
            f"{min(known):g}" if known and len(known) == len(values) else "."
        )
    return metrics


def _support_reasons(supporting: list[vc.CallConfidence]) -> str:
    return "|".join(sorted({reason for call in supporting for reason in call.reasons}))


def _from_gvcf(
    target: list[str],
    loci: list[tuple[str, int]],
    sequence: Mapping[tuple[str, int], str],
    found: GvcfEvidence,
) -> list[str]:
    """The record of a definition the input doesn't list, from the gVCF: its
    reference where the classifier calls every base callable reference (with
    one ploidy), else a no-call whose PGX_STATE is the worst base's state."""
    states = [found.states.get(locus, evidence.State.NOT_ASSESSED) for locus in loci]
    # Any state but callable reference outweighs it, not_assessed included.
    failing = [s for s in states if s is not evidence.State.CALLABLE_REFERENCE]
    worst = (
        min(failing, key=list(evidence.State).index)
        if failing
        else evidence.State.CALLABLE_REFERENCE
    )
    ploidies = {found.ploidy.get(locus) for locus in loci}
    matches = "".join(sequence[locus] for locus in loci).upper() == target[3].upper()
    callable_reference = (
        worst is evidence.State.CALLABLE_REFERENCE
        and matches
        and len(ploidies) == 1
        and None not in ploidies
    )
    state = "reference" if callable_reference else worst.value
    if worst is evidence.State.CALLABLE_REFERENCE and not callable_reference:
        state = "overlap_conflict"
    depths = [found.depth.get(locus) for locus in loci]
    qualities = [found.quality.get(locus) for locus in loci]
    metrics = [
        f"{min(v for v in values if v is not None):g}"
        if values and None not in values
        else "."
        for values in (depths, qualities)
    ]
    ploidy = next(iter(ploidies)) if callable_reference else 2
    gt = "/".join(("0" if callable_reference else ".") for _ in range(ploidy or 2))
    return [
        target[0], target[1], ".", target[3], target[4], ".", "PASS",
        f"PGX_STATE={state}",
        "GT:DP:GQ", f"{gt}:{metrics[0]}:{metrics[1]}",
    ]  # fmt: skip


def expand(
    source: str,
    directory: str,
    reference: str,
    work: str,
    regions: list[tuple[str, int, int]],
    thresholds: vc.Thresholds,
    gvcf: str | None = None,
) -> str:
    """Keep original support in evidence.vcf; mask unsupported reference in expanded.vcf.

    Expand only intersections with gene regions, not whole input blocks. Definition
    REF spans need exactly one reference observation at every base, a consistent
    ploidy, and known depth/GQ meeting the minimum across the entire span.
    With ``gvcf`` (the caller's gVCF beside a variant-only input), a definition the input has no record at takes the gVCF's states:
    reference only where every base of its span is callable reference.
    """
    definitions = next(
        (
            p
            for suffix in (".vcf.bgz", ".vcf.gz", ".vcf")
            if (p := Path(directory, "pharmcat_positions" + suffix)).is_file()
        ),
        None,
    )
    if definitions is None:
        raise WGSExtractError(
            f"PharmCAT definitions missing from {directory}: pharmcat_positions.vcf.bgz"
        )
    text = run_command(["bcftools", "view", source], capture_output=True).stdout
    header = [
        line
        for line in text.splitlines()
        if line.startswith("#") and not line.startswith("##GVCFBlock")
    ]
    columns = header[-1].split("\t")
    if len(columns) != 10:
        raise WGSExtractError("PharmCAT requires a single-sample VCF.")
    records = [
        line.split("\t") for line in text.splitlines() if not line.startswith("#")
    ]
    targets = [
        line.split("\t")
        for line in run_command(
            ["bcftools", "view", "-H", str(definitions)], capture_output=True
        ).stdout.splitlines()
    ]
    definition_loci = {
        (target[0], pos)
        for target in targets
        for pos in range(int(target[1]), int(target[1]) + len(target[3]))
    }
    sequence: dict[tuple[str, int], str] = {}
    for chrom, start, end in regions:
        fasta = run_command(
            ["samtools", "faidx", reference, f"{chrom}:{start + 1}-{end}"],
            capture_output=True,
        ).stdout
        bases = "".join(fasta.splitlines()[1:])
        sequence.update(((chrom, start + i + 1), base) for i, base in enumerate(bases))
    reference_evidence = None
    if gvcf is not None:
        pharmcat_chroms = sorted({target[0] for target in targets})
        reference_evidence = gvcf_evidence(
            gvcf,
            (
                (target[0], pos)
                for target in targets
                for pos in range(int(target[1]), int(target[1]) + len(target[3]))
            ),
            dict(chromosome_rename_mapping(pharmcat_chroms, vcf_chromosomes(gvcf))),
            thresholds,
            work,
        )
    observations: dict[tuple[str, int], list[int]] = {}
    calls: dict[int, vc.CallConfidence] = {}
    reference_records: set[int] = set()
    output: list[list[str]] = []
    for index, fields in enumerate(records):
        if "GT" not in fields[8].split(":"):
            fields[8], fields[9] = (
                ("GT", "./.")
                if fields[8] == "."
                else ("GT:" + fields[8], "./.:" + fields[9])
            )
        chrom, start = fields[0], int(fields[1])
        alleles = re.split(r"[/|]", _format(fields).get("GT", "."))
        structural = any(
            a.startswith("<") and a not in vc.SYMBOLIC_REFERENCE_ALTS
            for a in fields[4].split(",")
        )
        is_ref = not structural and all(a in {"0", "."} for a in alleles)
        symbolic = all(
            a in vc.SYMBOLIC_REFERENCE_ALTS or a == "." for a in fields[4].split(",")
        )
        end = int(_info(fields[7]).get("END", str(start + len(fields[3]) - 1)))
        if not symbolic and (not structural or all(a == "0" for a in alleles)):
            end = start + len(fields[3]) - 1
        calls[index] = _reference_call(
            fields, thresholds, vc.mapping_quality_kind(header)
        )
        for reg_chrom, reg_start, reg_end in regions:
            if chrom == reg_chrom:
                for pos in range(max(start, reg_start + 1), min(end, reg_end) + 1):
                    indices = observations.setdefault((chrom, pos), [])
                    if index not in indices:
                        indices.append(index)
        if is_ref or symbolic:
            reference_records.add(index)
        else:
            output.append(fields)
    for locus, indices in observations.items():
        refs = [index for index in indices if index in reference_records]
        if not refs:
            continue
        fields = records[refs[0]].copy()
        fields[1], fields[3], fields[4] = str(locus[1]), sequence[locus], "."
        info = _info(fields[7])
        info.pop("END", None)
        call = calls[refs[0]]
        state = "reference" if _usable(call) else "low_quality_reference"
        if "no_call" in call.reasons:
            state = "explicit_no_call"
        if len(indices) != 1:
            state = "overlap_conflict"
            info["reference_overlap"] = ""
            supporting = [calls[index] for index in indices]
            metrics = _worst_metrics(supporting)
            ploidies = {
                len(re.split(r"[/|]", _format(records[index]).get("GT", ".")))
                for index in indices
            }
            genotype = (
                _format(fields).get("GT", "./.")
                if len(ploidies) == 1
                and all("no_call" not in c.reasons for c in supporting)
                else "./."
            )
            fields[8], fields[9] = "GT:DP:GQ", f"{genotype}:{metrics[0]}:{metrics[1]}"
            info["PGX_REASONS"] = _support_reasons(supporting)
        info["PGX_STATE"] = state
        fields[7] = ";".join(k + ("=" + v if v else "") for k, v in info.items())
        output.append(fields)
    existing = {(f[0], int(f[1]), f[3]) for f in output}
    for target in targets:
        chrom, start, ref = target[0], int(target[1]), target[3]
        if (chrom, start, ref) in existing:
            continue
        loci = [(chrom, pos) for pos in range(start, start + len(ref))]
        if not all(locus in sequence for locus in loci):
            continue
        span_indices = {
            index for locus in loci for index in observations.get(locus, [])
        }
        if not span_indices and reference_evidence is not None:
            output.append(_from_gvcf(target, loci, sequence, reference_evidence))
            existing.add((chrom, start, ref))
            continue
        supporting = [calls[index] for index in span_indices if index in calls]
        gap = any(not observations.get(locus) for locus in loci)
        conflict = any(len(observations.get(locus, [])) > 1 for locus in loci) or any(
            index not in reference_records for index in span_indices
        )
        ploidies = {
            len(re.split(r"[/|]", _format(records[index]).get("GT", ".")))
            for index in span_indices
        }
        matches = "".join(sequence[locus] for locus in loci).upper() == ref.upper()
        usable = (
            not gap
            and not conflict
            and matches
            and len(ploidies) == 1
            and all(_usable(call) for call in supporting)
        )
        state = (
            "reference"
            if usable
            else (
                "absent"
                if not span_indices
                else "reference_gap"
                if gap
                else "overlap_conflict"
                if conflict or not matches or len(ploidies) != 1
                else "explicit_no_call"
                if any("no_call" in c.reasons for c in supporting)
                else "low_quality_reference"
            )
        )
        # Never hide one unknown metric behind another block's known value.
        metrics = _worst_metrics(supporting)
        observed_reference = (
            not gap
            and not conflict
            and matches
            and len(ploidies) == 1
            and all("no_call" not in call.reasons for call in supporting)
        )
        gt = (
            "/".join("0" for _ in range(next(iter(ploidies))))
            if observed_reference
            else "./."
        )
        span_info = f"PGX_STATE={state}"
        supporting_reasons = _support_reasons(supporting)
        if supporting_reasons:
            span_info += ";PGX_REASONS=" + supporting_reasons
        if gap:
            span_info += ";reference_gap"
        if conflict or not matches or len(ploidies) > 1:
            span_info += ";reference_overlap"
        output.append(
            [
                chrom,
                str(start),
                ".",
                ref,
                target[4],
                ".",
                "PASS",
                span_info,
                "GT:DP:GQ",
                f"{gt}:{metrics[0]}:{metrics[1]}",
            ]
        )
        existing.add((chrom, start, ref))
    extra = [
        '##INFO=<ID=PGX_STATE,Number=1,Type=String,Description="Reference evidence state">',
        '##INFO=<ID=PGX_REASONS,Number=1,Type=String,Description="Worst supporting reference reasons">',
        '##INFO=<ID=reference_gap,Number=0,Type=Flag,Description="Incomplete reference span">',
        '##INFO=<ID=reference_overlap,Number=0,Type=Flag,Description="Conflicting reference span">',
    ]
    if not any(line.startswith("##FORMAT=<ID=GT,") for line in header):
        extra.append('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">')
    for metric in ("DP", "GQ"):
        if not any(line.startswith(f"##FORMAT=<ID={metric},") for line in header):
            extra.append(
                f'##FORMAT=<ID={metric},Number=1,Type=Float,Description="Worst supporting quality">'
            )
    order = {
        line.split("ID=", 1)[1].split(",", 1)[0].split(">", 1)[0]: i
        for i, line in enumerate(header)
        if line.startswith("##contig=")
    }
    output.sort(key=lambda f: (order.get(f[0], len(order)), int(f[1]), len(f[3])))
    for name, mask in (("evidence.vcf", False), ("expanded.vcf", True)):
        with Path(work, name).open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join([*header[:-1], *extra, header[-1]]) + "\n")
            for original in output:
                fields = original.copy()
                record_state = _info(fields[7]).get("PGX_STATE")
                if (
                    not mask
                    and record_state is not None
                    and (fields[0], int(fields[1])) not in definition_loci
                ):
                    continue
                if mask and record_state == "absent":
                    continue
                if mask and record_state and record_state != "reference":
                    fmt = fields[9].split(":")
                    gt_index = fields[8].split(":").index("GT")
                    ploidy = len(re.split(r"[/|]", fmt[gt_index]))
                    fmt[gt_index] = "/".join("." for _ in range(ploidy))
                    fields[9] = ":".join(fmt)
                stream.write("\t".join(fields) + "\n")
    return str(Path(work, "expanded.vcf"))
