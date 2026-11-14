"""Reference-normalized allele matching and phase-supported ClinVar MNVs."""

import gzip
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from wgsextract_cli.core import clinical_evidence as ce
from wgsextract_cli.core import variant_confidence as vc
from wgsextract_cli.core.imputed_vcf import IMPUTED_TAG
from wgsextract_cli.core.reference_resolver import header_contig_lengths
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_confidence import SPLIT_TAG
from wgsextract_cli.core.variant_files import (
    ensure_vcf_indexed,
    match_chromosome_name,
    normalize_vcf_chromosomes,
    vcf_chromosomes,
)


def table_as_vcf(
    source: str, columns: str, headers: Sequence[str], reference: str, work: str
) -> tuple[str, str]:
    fields = columns.split(",")
    if "REF" not in fields or "ALT" not in fields:
        return source, columns
    info_fields = [
        (i, c.removeprefix("INFO/"))
        for i, c in enumerate(fields)
        if c.startswith("INFO/")
    ]
    output = Path(work, "allele-table.vcf")
    contigs = []
    with output.open("w") as handle:
        handle.write("##fileformat=VCFv4.2\n")
        for line in Path(reference + ".fai").read_text().splitlines():
            name, length, *_ = line.split("\t")
            contigs.append(name)
            handle.write(f"##contig=<ID={name},length={length}>\n")
        handle.writelines(h + "\n" for h in headers)
        handle.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        with gzip.open(source, "rt") as records:
            for line in records:
                if line.startswith("#"):
                    continue
                row = line.rstrip("\n").split("\t")
                info = ";".join(f"{tag}={row[i]}" for i, tag in info_fields)
                handle.write(
                    "\t".join(
                        [
                            match_chromosome_name(row[fields.index("CHROM")], contigs)
                            or row[fields.index("CHROM")],
                            row[fields.index("POS")],
                            ".",
                            row[fields.index("REF")],
                            row[fields.index("ALT")],
                            ".",
                            ".",
                            info or ".",
                        ]
                    )
                    + "\n"
                )
    return str(output), ",".join("INFO/" + tag for _, tag in info_fields)


def _text_copy(
    source: str, output: Path, clinical: bool, allowed: set[str] | None = None
) -> None:
    text = run_command(["bcftools", "view", "-h", source], capture_output=True).stdout
    definitions = (
        {}
        if allowed is not None
        else {ce.ORIGIN: "Original record and sample fields (URL-encoded JSON)"}
    )
    if clinical:
        definitions.update(
            {
                ce.IDENTITY: "ClinVar resource record ID",
                ce.STARS: "ClinVar review stars; not sequencing confidence",
                ce.TIER: "ClinVar review tier; not sequencing confidence",
            }
        )
    with output.open("w") as handle:
        for line in text.splitlines():
            if line.startswith("#CHROM"):
                for tag, description in definitions.items():
                    if f"##INFO=<ID={tag}," not in text:
                        kind = "Integer" if tag == ce.STARS else "String"
                        handle.write(
                            f"##INFO=<ID={tag},Number=1,Type={kind},"
                            f'Description="{description}">\n'
                        )
            handle.write(line + "\n")
        # Streaming a prepared BGZF VCF avoids capturing a whole genome.
        opener = gzip.open if source.endswith((".gz", ".bgz")) else open
        skipped = set()
        skipped_count = 0
        with opener(source, "rt") as records:
            for line in records:
                if line.startswith("#"):
                    continue
                fields = line.rstrip().split("\t")
                if allowed is not None and fields[0] not in allowed:
                    skipped.add(fields[0])
                    skipped_count += 1
                    continue
                info = dict(item.partition("=")[::2] for item in fields[7].split(";"))
                if clinical:
                    evidence = ce.review_evidence(info)
                    info[ce.IDENTITY] = fields[2]
                    if evidence["stars"] is not None:
                        info[ce.STARS] = str(evidence["stars"])
                    info[ce.TIER] = (
                        str(evidence["review_tier"]).replace(" ", "_").replace(",", "")
                    )
                elif allowed is None:
                    info.setdefault(
                        ce.ORIGIN,
                        quote(
                            json.dumps(
                                {
                                    "chrom": fields[0],
                                    "pos": int(fields[1]),
                                    "ref": fields[3],
                                    "alt": fields[4],
                                    "format": fields[8] if len(fields) > 8 else None,
                                    "samples": fields[9:],
                                },
                                separators=(",", ":"),
                            ),
                            safe="",
                        ),
                    )
                info.pop(".", None)
                fields[7] = (
                    ";".join(f"{k}={v}" if v else k for k, v in info.items()) or "."
                )
                handle.write("\t".join(fields) + "\n")
    if skipped:
        warn(
            "annotation_contigs_not_in_reference",
            f"Excluded {skipped_count} resource records on contigs absent from the reference FASTA; these cannot be matched. No sample records were excluded.",
            count=skipped_count,
            contigs=",".join(sorted(skipped)),
        )


def _normalize(
    source: str,
    reference: str,
    output: str,
    work: str,
    clinical: bool,
    is_resource: bool = False,
) -> str:
    fai = Path(reference + ".fai")
    if not fai.is_file():
        raise WGSExtractError(
            f"Reference FASTA index missing: {fai}; run samtools faidx."
        )
    lengths = {
        f[0]: int(f[1])
        for line in fai.read_text().splitlines()
        if (f := line.split("\t"))
    }
    header = run_command(["bcftools", "view", "-h", source], capture_output=True).stdout
    canonical = {
        c.removeprefix("chr").replace("MT", "M"): n for c, n in lengths.items()
    }
    for contig, length in header_contig_lengths(header).items():
        expected = canonical.get(contig.removeprefix("chr").replace("MT", "M"))
        if expected is not None and expected != length:
            raise WGSExtractError(
                f"Reference/build mismatch: {contig} has length {length}, FASTA has {expected}."
            )
    staged = Path(work, "resource.vcf" if is_resource else "query.vcf")
    allowed = (
        {c for c in vcf_chromosomes(source) if match_chromosome_name(c, lengths)}
        if is_resource
        else None
    )
    _text_copy(source, staged, clinical, allowed)
    renamed = normalize_vcf_chromosomes(str(staged), list(lengths), work)
    split = output + ".split.gz"
    run_command(
        [
            "bcftools",
            "norm",
            "-m",
            "-any",
            "--old-rec-tag",
            SPLIT_TAG,
            "-Oz",
            "-o",
            split,
            renamed,
        ],
        capture_output=True,
    )
    norm = output + ".norm.gz"
    run_command(
        ["bcftools", "norm", "-f", reference, "-c", "e", "-Oz", "-o", norm, split],
        capture_output=True,
    )
    run_command(
        ["bcftools", "sort", "-T", str(Path(work, "sort")), "-Oz", "-o", output, norm],
        capture_output=True,
    )
    ensure_vcf_indexed(output)
    return output


def _compose(query: str, resource: str, work: str, thresholds: vc.Thresholds) -> str:
    """Look up MNV components in a disk-backed SNV index; never infer cis."""
    output = Path(work, "composed.vcf")
    with sqlite3.connect(Path(work, "snvs.sqlite")) as db:
        db.execute("CREATE TABLE snvs (chrom TEXT, pos INTEGER, record TEXT)")
        db.execute("CREATE TABLE alleles (chrom TEXT, pos TEXT, ref TEXT, alt TEXT)")
        with gzip.open(query, "rt") as source, output.open("w") as handle:
            header = []
            for line in source:
                if line.startswith("#"):
                    header.append(line.rstrip())
                if line.startswith("#CHROM"):
                    handle.write(
                        f"##INFO=<ID={ce.COMPONENTS},Number=1,Type=String,"
                        'Description="Phase-supported MNV components (URL-encoded JSON)">\n'
                    )
                    for tag in (ce.COMPONENT_CONF, ce.COMPONENT_REASONS):
                        handle.write(
                            f'##INFO=<ID={tag},Number=1,Type=String,Description="Worst technical support of MNV components">\n'
                        )
                handle.write(line)
                if line.startswith("#"):
                    continue
                f = line.rstrip().split("\t")
                db.execute(
                    "INSERT INTO alleles VALUES (?, ?, ?, ?)", (f[0], f[1], f[3], f[4])
                )
                if len(f) == 10 and len(f[3]) == len(f[4]) == 1:
                    db.execute(
                        "INSERT INTO snvs VALUES (?, ?, ?)",
                        (f[0], int(f[1]), line.rstrip()),
                    )
            db.execute("CREATE INDEX locus ON snvs (chrom, pos)")
            db.execute("CREATE INDEX allele ON alleles (chrom, pos, ref, alt)")
            with gzip.open(resource, "rt") as targets:
                emitted: set[tuple[str, str, str, str]] = set()
                for line in targets:
                    if line.startswith("#"):
                        continue
                    target = line.rstrip().split("\t")
                    ref, alt = target[3:5]
                    if (
                        len(ref) < 2
                        or len(ref) != len(alt)
                        or not set(ref + alt) <= set("ACGT")
                    ):
                        continue
                    key = (target[0], target[1], ref, alt)
                    if key in emitted:
                        continue
                    if db.execute(
                        "SELECT 1 FROM alleles WHERE chrom=? AND pos=? AND ref=? AND alt=?",
                        key,
                    ).fetchone():
                        continue
                    parts = []
                    for offset, (r, a) in enumerate(zip(ref, alt, strict=True)):
                        if r == a:
                            continue
                        rows = db.execute(
                            "SELECT record FROM snvs WHERE chrom=? AND pos=?",
                            (target[0], int(target[1]) + offset),
                        ).fetchall()
                        if len(rows) != 1:
                            break
                        part = str(rows[0][0]).split("\t")
                        if part[3:5] != [r, a]:
                            break
                        parts.append(part)
                    else:
                        combined = _combined(target, parts, header, thresholds)
                        if combined:
                            handle.write("\t".join(combined) + "\n")
                            emitted.add(key)
    result = str(Path(work, "composed.vcf.gz"))
    run_command(
        [
            "bcftools",
            "sort",
            "-T",
            str(Path(work, "compose-sort")),
            "-Oz",
            "-o",
            result,
            str(output),
        ],
        capture_output=True,
    )
    ensure_vcf_indexed(result)
    return result


def _combined(
    target: list[str],
    parts: list[list[str]],
    header: list[str],
    thresholds: vc.Thresholds,
) -> list[str] | None:
    if len(parts) < 2:
        return None
    fmts = [dict(zip(p[8].split(":"), p[9].split(":"), strict=False)) for p in parts]
    gts = [f.get("GT", ".").split("|") for f in fmts]
    if any(len(g) != 2 or not set(g) <= {"0", "1"} for g in gts):
        return None
    ps = [f.get("PS", ".") for f in fmts]
    if len(set(ps)) != 1 or ps[0] == ".":
        return None
    carried = [all(g[h] == "1" for g in gts) for h in (0, 1)]
    if not any(carried):
        return None
    if any(
        IMPUTED_TAG in dict(item.partition("=")[::2] for item in p[7].split(";"))
        for p in parts
    ):
        return None
    # No AD/likelihood inference: component reads do not measure MNV support.
    values = ["|".join("1" if c else "0" for c in carried), ps[0]]
    for tag in ("DP", "GQ"):
        nums = [f.get(tag, ".") for f in fmts]
        values.append(
            str(min(int(n) for n in nums)) if all(n.isdigit() for n in nums) else "."
        )
    filters = sorted(
        {v for p in parts for v in p[6].split(";") if v not in {"PASS", "."}}
    )
    provenance = quote(
        json.dumps(
            [
                {
                    "chrom": p[0],
                    "pos": int(p[1]),
                    "ref": p[3],
                    "alt": p[4],
                    "format": p[8],
                    "sample": p[9],
                    "info": p[7],
                }
                for p in parts
            ],
            separators=(",", ":"),
        ),
        safe="",
    )
    summary = next(
        s
        for s in vc.label_records(
            [*header, *("\t".join(p) for p in parts)],
            thresholds,
            suppress_rare_imputed=False,
        )
        if isinstance(s, vc.ConfidenceSummary)
    )
    component_conf = max(
        (f.confidence.label for f in summary.findings), key=vc.LABELS.index
    )
    reasons = (
        "|".join(sorted({r for f in summary.findings for r in f.confidence.reasons}))
        or "."
    )
    return [
        target[0],
        target[1],
        ".",
        target[3],
        target[4],
        ".",
        ";".join(filters) or "PASS",
        f"{ce.COMPONENTS}={provenance};{ce.COMPONENT_CONF}={component_conf};{ce.COMPONENT_REASONS}={reasons}",
        "GT:PS:DP:GQ",
        ":".join(values),
    ]


def normalized_inputs(
    query: str,
    resource: str,
    reference: str,
    work: str,
    clinical: bool,
    thresholds: vc.Thresholds | None = None,
) -> tuple[str, str]:
    normalized_query = _normalize(
        query, reference, str(Path(work, "query.vcf.gz")), work, False
    )
    if resource.endswith((".vcf", ".vcf.gz", ".vcf.bgz")):
        resource = _normalize(
            resource,
            reference,
            str(Path(work, "resource.vcf.gz")),
            work,
            clinical,
            True,
        )
        if clinical:
            normalized_query = _compose(
                normalized_query, resource, work, thresholds or vc.thresholds_from()
            )
    return normalized_query, resource
