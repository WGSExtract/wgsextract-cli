"""Reference-normalized allele matching and phase-supported ClinVar MNVs."""

import gzip
import json
import sqlite3
from bisect import bisect_right
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from wgsextract_cli.core import clinical_evidence as ce
from wgsextract_cli.core import variant_confidence as vc
from wgsextract_cli.core.imputed_vcf import IMPUTED_TAG
from wgsextract_cli.core.reference_resolver import header_contig_lengths
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_confidence import SPLIT_TAG
from wgsextract_cli.core.variant_files import (
    ensure_vcf_indexed,
    match_chromosome_name,
    normalize_vcf_chromosomes,
    vcf_chromosomes,
)
from wgsextract_cli.core.variant_table import parse_header


def table_as_vcf(
    source: str,
    columns: str,
    headers: Sequence[str],
    reference: str,
    work: str,
    snv_query: str | None = None,
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
    subset = None
    if snv_query:
        regions = Path(work, "snv-regions.tsv")
        available = run_command(
            ["tabix", "-l", source], capture_output=True
        ).stdout.splitlines()
        query_names = {
            c: match_chromosome_name(c, available) for c in vcf_chromosomes(snv_query)
        }
        with (
            gzip.open(snv_query, "rt") as query,
            regions.open("w", newline="\n") as loci,
        ):
            for line in query:
                if line.startswith("#"):
                    continue
                row = line.split("\t")
                if len(row[3]) == len(row[4]) == 1 and set(row[3] + row[4]) <= set(
                    "ACGT"
                ):
                    chrom = query_names.get(row[0])
                    if chrom:
                        loci.write(f"{chrom}\t{row[1]}\t{row[1]}\n")
        subset = Path(work, "snv-scores.tsv")
        with subset.open("w", newline="\n") as selected:
            if regions.stat().st_size:
                run_command(["tabix", "-R", str(regions), source], stdout=selected)
    contigs = []
    with output.open("w", newline="\n") as handle:
        handle.write("##fileformat=VCFv4.2\n")
        for line in Path(reference + ".fai").read_text().splitlines():
            name, length, *_ = line.split("\t")
            contigs.append(name)
            handle.write(f"##contig=<ID={name},length={length}>\n")
        handle.writelines(h + "\n" for h in headers)
        handle.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        mapped_names: dict[str, str] = {}
        with subset.open() if subset else gzip.open(source, "rt") as records:
            for line in records:
                if line.startswith("#"):
                    continue
                row = line.rstrip("\n").split("\t")
                chrom = row[fields.index("CHROM")]
                if chrom not in mapped_names:
                    mapped_names[chrom] = match_chromosome_name(chrom, contigs) or chrom
                info = ";".join(f"{tag}={row[i]}" for i, tag in info_fields)
                handle.write(
                    "\t".join(
                        [
                            mapped_names[chrom],
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
    source: str,
    output: Path,
    clinical: bool,
    allowed: set[str] | None = None,
    incompatible_mito: set[str] | None = None,
) -> None:
    text = run_command(["bcftools", "view", "-h", source], capture_output=True).stdout
    if clinical:
        text = "\n".join(
            line
            for line in text.splitlines()
            if not line.startswith(
                tuple(f"##INFO=<ID={tag}," for tag in (ce.IDENTITY, ce.STARS, ce.TIER))
            )
        )
    info_numbers = parse_header(text.splitlines()).info_number
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
    with output.open("w", newline="\n") as handle:
        for line in text.splitlines():
            if line.startswith("#CHROM"):
                for contig in vcf_chromosomes(source):
                    if (
                        f"##contig=<ID={contig}," not in text
                        and f"##contig=<ID={contig}>" not in text
                    ):
                        handle.write(f"##contig=<ID={contig}>\n")
                for tag, description in definitions.items():
                    if f"##INFO=<ID={tag}," not in text:
                        kind = "Integer" if tag == ce.STARS else "String"
                        number = "A" if tag in {ce.STARS, ce.TIER} else "1"
                        handle.write(
                            f"##INFO=<ID={tag},Number={number},Type={kind},"
                            f'Description="{description}">\n'
                        )
            handle.write(line + "\n")
        # Streaming a prepared BGZF VCF avoids capturing a whole genome.
        opener = gzip.open if source.endswith((".gz", ".bgz")) else open
        skipped = set()
        skipped_count = 0
        mito_count = 0
        with opener(source, "rt") as records:
            for line in records:
                if line.startswith("#"):
                    continue
                fields = line.rstrip().split("\t")
                if allowed is not None and fields[0] not in allowed:
                    if fields[0] in (incompatible_mito or set()):
                        mito_count += 1
                        continue
                    skipped.add(fields[0])
                    skipped_count += 1
                    continue
                info = dict(item.partition("=")[::2] for item in fields[7].split(";"))
                if clinical:
                    for tag in (ce.IDENTITY, ce.STARS, ce.TIER):
                        info.pop(tag, None)
                    evidence_rows = []
                    for allele in range(len(fields[4].split(","))):
                        per_allele = dict(info)
                        for key in ("CLNREVSTAT", "CLNSIG", "CLNSIGCONF"):
                            field_number = info_numbers.get(key)
                            if field_number in {"A", "R"} and key in info:
                                values = info[key].split(",")
                                index = allele + (field_number == "R")
                                per_allele[key] = (
                                    values[index] if index < len(values) else "."
                                )
                        evidence_rows.append(ce.review_evidence(per_allele))
                    info[ce.IDENTITY] = fields[2]
                    if any(e["stars"] is not None for e in evidence_rows):
                        info[ce.STARS] = ",".join(
                            str(e["stars"]) if e["stars"] is not None else "."
                            for e in evidence_rows
                        )
                    info[ce.TIER] = ",".join(
                        str(e["review_tier"]).replace(" ", "_").replace(",", "")
                        for e in evidence_rows
                    )
                elif allowed is None and ce.ORIGIN not in info:
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
    if mito_count:
        warn(
            "annotation_mito_reference_incompatible",
            "Excluded resource mitochondrial records: UCSC hg19 Yoruba chrM and GRCh37 rCRS MT are incompatible. Autosomal records remain validated.",
            count=mito_count,
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
    incompatible_mito = set()
    for contig, length in header_contig_lengths(header).items():
        alias = match_chromosome_name(contig, lengths)
        expected = lengths.get(alias or "")
        if expected is not None and expected != length:
            if (
                is_resource
                and alias in {"M", "MT", "chrM", "chrMT"}
                and {length, expected} == {16569, 16571}
            ):
                incompatible_mito.add(contig)
                continue
            raise WGSExtractError(
                f"Reference/build mismatch: {contig} has length {length}, FASTA has {expected}."
            )
    staged = Path(work, "resource.vcf" if is_resource else "query.vcf")
    allowed = (
        {c for c in vcf_chromosomes(source) if match_chromosome_name(c, lengths)}
        - incompatible_mito
        if is_resource
        else None
    )
    _text_copy(source, staged, clinical, allowed, incompatible_mito)
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
    source_header = run_command(
        ["bcftools", "view", "-h", query], capture_output=True
    ).stdout
    current_result().add_metrics(clinvar_mnv_indexed_records=0)
    column_header = next(
        s for s in source_header.splitlines() if s.startswith("#CHROM")
    )
    if "##FORMAT=<ID=PS," not in source_header or len(column_header.split("\t")) != 10:
        return query
    targets = []
    intervals: dict[str, list[tuple[int, int]]] = {}
    with gzip.open(resource, "rt") as source:
        for line in source:
            if line.startswith("#"):
                continue
            target = line.rstrip().split("\t")
            ref, alt = target[3:5]
            if (
                len(ref) < 2
                or len(ref) != len(alt)
                or not set(ref + alt) <= set("ACGT")
                or any(r == a for r, a in zip(ref, alt, strict=True))
            ):
                continue
            targets.append(target)
            intervals.setdefault(target[0], []).append(
                (int(target[1]), int(target[1]) + len(ref) - 1)
            )
    if not targets:
        return query
    merged = {}
    for chrom, spans in intervals.items():
        compact: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if compact and start <= compact[-1][1] + 1:
                compact[-1] = (compact[-1][0], max(end, compact[-1][1]))
            else:
                compact.append((start, end))
        merged[chrom] = ([s for s, _ in compact], [e for _, e in compact])
    output = Path(work, "composed.vcf")
    with closing(sqlite3.connect(Path(work, "snvs.sqlite"))) as db, db:
        db.execute(
            "CREATE TABLE alleles (chrom TEXT, pos INTEGER, ref TEXT, alt TEXT, end INTEGER, record TEXT)"
        )
        db.execute("CREATE VIRTUAL TABLE spans USING rtree(record_id, start, end)")
        with gzip.open(query, "rt") as source, output.open("w", newline="\n") as handle:
            header = []
            for line in source:
                if line.startswith("#"):
                    header.append(line.rstrip())
                if line.startswith("#CHROM"):
                    for tag in ("DP", "GQ"):
                        if f"##FORMAT=<ID={tag}," not in source_header:
                            handle.write(
                                f'##FORMAT=<ID={tag},Number=1,Type=Integer,Description="Minimum component {tag}; missing if unavailable">\n'
                            )
                    handle.write(
                        f"##INFO=<ID={ce.COMPONENTS},Number=1,Type=String,"
                        'Description="Phase-supported MNV components (URL-encoded JSON)">\n'
                    )
                    for tag in (ce.COMPONENT_CONF, ce.COMPONENT_REASONS):
                        handle.write(
                            f'##INFO=<ID={tag},Number=1,Type=String,Description="Worst technical support of MNV components">\n'
                        )
                if line.startswith("#"):
                    handle.write(line)
                    continue
                f = line.rstrip().split("\t")
                info = dict(item.partition("=")[::2] for item in f[7].split(";"))
                pos = int(f[1])
                end = max(
                    pos + len(f[3]) - 1,
                    int(info["END"]) if info.get("END", "").isdigit() else pos,
                )
                starts, ends = merged.get(f[0], ([], []))
                index = bisect_right(starts, end) - 1
                if index < 0 or ends[index] < pos:
                    continue
                inserted = db.execute(
                    "INSERT INTO alleles VALUES (?, ?, ?, ?, ?, ?)",
                    (f[0], int(f[1]), f[3], f[4], end, line.rstrip()),
                )
                db.execute(
                    "INSERT INTO spans VALUES (?, ?, ?)",
                    (inserted.lastrowid, int(f[1]), end),
                )
            db.execute("CREATE INDEX allele ON alleles (chrom, pos, ref, alt)")
            current_result().add_metrics(
                clinvar_mnv_indexed_records=db.execute(
                    "SELECT count(*) FROM alleles"
                ).fetchone()[0]
            )
            if targets:
                emitted: set[tuple[str, str, str, str]] = set()
                for target in targets:
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
                    existing = db.execute(
                        "SELECT record FROM alleles WHERE chrom=? AND pos=? AND ref=? AND alt=?",
                        key,
                    ).fetchall()
                    if any(
                        len(fields := str(row[0]).split("\t")) == 10
                        and "1"
                        in dict(
                            zip(
                                fields[8].split(":"), fields[9].split(":"), strict=False
                            )
                        )
                        .get("GT", ".")
                        .replace("|", "/")
                        .split("/")
                        for row in existing
                    ):
                        continue
                    parts = []
                    for offset, (r, a) in enumerate(zip(ref, alt, strict=True)):
                        if r == a:
                            continue
                        rows = db.execute(
                            "SELECT record FROM alleles WHERE chrom=? AND pos=? AND ref=? AND alt=?",
                            (target[0], int(target[1]) + offset, r, a),
                        ).fetchall()
                        matches = [
                            str(row[0]).split("\t")
                            for row in rows
                            if str(row[0]).split("\t")[3:5] == [r, a]
                        ]
                        if len(matches) != 1:
                            break
                        part = matches[0]
                        parts.append(part)
                    else:
                        part_keys = {(int(p[1]), p[3], p[4]) for p in parts}
                        start, end = int(target[1]), int(target[1]) + len(ref) - 1
                        overlaps = db.execute(
                            "SELECT a.pos, a.ref, a.alt, a.record FROM spans s JOIN alleles a ON a.rowid=s.record_id "
                            "WHERE s.start<=? AND s.end>=? AND a.chrom=? AND a.pos<=? AND a.end>=?",
                            (end, start, target[0], end, start),
                        )
                        extras = [
                            str(row[3]).split("\t")
                            for row in overlaps
                            if (row[0], row[1], row[2]) not in part_keys
                        ]
                        combined = _combined(target, parts, header, thresholds, extras)
                        if combined:
                            handle.write("\t".join(combined) + "\n")
                            emitted.add(key)
    candidates = str(Path(work, "mnv-candidates.vcf.gz"))
    run_command(
        [
            "bcftools",
            "sort",
            "-T",
            str(Path(work, "compose-sort")),
            "-Oz",
            "-o",
            candidates,
            str(output),
        ],
        capture_output=True,
    )
    ensure_vcf_indexed(candidates)
    union_header = Path(work, "mnv-header.txt")
    union_header.write_text(
        run_command(["bcftools", "view", "-h", candidates], capture_output=True).stdout,
        newline="\n",
    )
    aligned_query = str(Path(work, "query-with-mnv-header.vcf.gz"))
    run_command(
        ["bcftools", "reheader", "-h", str(union_header), "-o", aligned_query, query],
        capture_output=True,
    )
    ensure_vcf_indexed(aligned_query)
    unsorted = str(Path(work, "with-mnvs.vcf.gz"))
    run_command(
        ["bcftools", "concat", "-a", "-Oz", "-o", unsorted, aligned_query, candidates],
        capture_output=True,
    )
    result = str(Path(work, "composed.vcf.gz"))
    run_command(
        [
            "bcftools",
            "sort",
            "-T",
            str(Path(work, "compose-sort-final")),
            "-Oz",
            "-o",
            result,
            unsorted,
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
    extras: list[list[str]],
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
    for extra in extras:
        if len(extra) != 10:
            return None
        fmt = dict(zip(extra[8].split(":"), extra[9].split(":"), strict=False))
        gt = fmt.get("GT", ".")
        if gt in {"0", "0/0", "0|0"}:
            continue
        extra_gt = gt.split("|")
        if (
            len(extra_gt) != 2
            or not set(extra_gt) <= {"0", "1"}
            or fmt.get("PS", ".") != ps[0]
        ):
            return None
        carried = [c and a == "0" for c, a in zip(carried, extra_gt, strict=True)]
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
    columns: str = "",
    headers: Sequence[str] = (),
    snv_table: bool = False,
) -> tuple[str, str, str]:
    normalized_query = _normalize(
        query, reference, str(Path(work, "query.vcf.gz")), work, False
    )
    from .resource_cache import normalized_resource

    is_vcf = resource.lower().endswith((".vcf", ".vcf.gz", ".vcf.bgz"))
    if not is_vcf and (
        "REF" not in columns.split(",") or "ALT" not in columns.split(",")
    ):
        return normalized_query, resource, columns
    source = resource
    converted_columns = (
        columns
        if is_vcf
        else ",".join(c for c in columns.split(",") if c.startswith("INFO/"))
    )

    def build() -> str:
        selected = source
        if not is_vcf:
            selected, _ = table_as_vcf(
                source,
                columns,
                headers,
                reference,
                work,
                normalized_query if snv_table else None,
            )
        return _normalize(
            selected,
            reference,
            str(Path(work, "resource.vcf.gz")),
            work,
            clinical,
            True,
        )

    resource = (
        build()
        if snv_table and not is_vcf
        else normalized_resource(
            source, reference, work, [columns, list(headers), clinical], build
        )
    )
    if clinical:
        normalized_query = _compose(
            normalized_query, resource, work, thresholds or vc.thresholds_from()
        )
    return normalized_query, resource, converted_columns
