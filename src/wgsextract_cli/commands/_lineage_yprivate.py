"""``lineage y-private``: candidate private (novel) Y SNVs.

A candidate is a called chrY SNV inside the bundled Poz callable mask whose
alleles no installed marker set lists (the YBrowse snapshot on GRCh38; the
YFull YTree positions on GRCh38 and T2T), and which passes strict gates. It is
a candidate to confirm, not a proven private SNP: sequencing and mapping
errors pass such filters too.
"""

import csv
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.lineage import YPrivateParams
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.genotype_qc import CHR_Y, warn_low_depth
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.ref_library.assets import (
    input_stem,
    read_fai_lengths,
    resolve_reference_library,
)
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.temp_directory import scratch_parent
from wgsextract_cli.core.utils import WGSExtractError, run_command, run_pipeline
from wgsextract_cli.core.variant_files import calculate_bam_md5, verify_paths_exist

from ._extract_poz import poz_intervals, y_build

VCF_SUFFIXES = (".vcf", ".vcf.gz", ".bcf")
EXCESS_CANDIDATES = 50
"""More candidates than this in the ~11.6 Mb mask suggests a problem: a male
line usually has a few to a few dozen SNPs below its youngest named branch."""
BUILD_NAMES = {"hg38": "GRCh38", "t2t": "T2T-CHM13v2.0"}
DEFINITION = (
    "Candidate private (novel) Y SNVs: called SNVs whose alleles are in no "
    "installed marker set, inside the callable mask, passing every filter. "
    "Candidates to confirm (for example by Sanger sequencing or a second "
    "test), not proven private SNPs."
)
MASK = {
    "name": "CombBED/McDonald/Poznik union (bundled Poz regions)",
    "scope": "chrY regions used for phylogenetic and dating analyses: mostly "
    "unique X-degenerate sequence. It leaves out both pseudoautosomal regions "
    "(PAR1, PAR2) and the Yq12 heterochromatin, and most ampliconic, "
    "palindromic and other low-mappability sequence; calls outside it are not "
    "made. Per-site structural classes are not labelled.",
}
_BASES = frozenset("ACGT")
_COLUMNS = ("chrom", "pos", "ref", "alt", "qual", "depth", "alt_reads", "af", "note")
OTHER_ALLELE = "known site, other allele"


@dataclass(frozen=True)
class _Call:
    chrom: str
    pos: int
    ref: str
    alt: str
    qual: float | None
    filter: str
    ref_reads: int | None
    alt_reads: int | None
    line: str

    @property
    def depth(self) -> int | None:
        if self.alt_reads is None:
            return None
        return (self.ref_reads or 0) + self.alt_reads

    @property
    def af(self) -> float:
        depth = self.depth
        return (self.alt_reads or 0) / depth if depth else 0.0


def _y_contig(contigs: dict[str, int], names: list[str]) -> str:
    found = [name for name in ("chrY", "Y") if name in contigs or name in names]
    if len(found) != 1:
        raise WGSExtractError(
            "The input has no single chrY/Y contig, so there are no Y calls."
        )
    return found[0]


def _vcf_header(path: str) -> str:
    return run_command(["bcftools", "view", "-h", path], capture_output=True).stdout


def _vcf_y_name(path: str) -> list[str]:
    """chrY or Y, from the records of a VCF whose header lists no contigs."""
    first = run_command(
        ["bcftools", "query", "-t", "chrY,Y", "-f", "%CHROM\n", path],
        capture_output=True,
    ).stdout.split("\n", 1)[0]
    return [first] if first else []


def _resolve_build(contigs: dict[str, int], chr_y: str, override: str | None) -> str:
    known = y_build(contigs, chr_y) if chr_y in contigs else None
    if chr_y in contigs and known is None:
        raise WGSExtractError(
            f"Unknown or inconsistent Y assembly ({chr_y} length "
            f"{contigs[chr_y]:,}); lineage y-private supports GRCh38 and "
            "T2T-CHM13v2.0."
        )
    if known and override and known != override:
        raise WGSExtractError(
            f"--build {override} contradicts the input's {chr_y} length "
            f"({contigs[chr_y]:,}, {known})."
        )
    build = known or override
    if build is None:
        raise WGSExtractError(
            f"The VCF header gives no {chr_y} length; pass --build hg38 or t2t."
        )
    if build not in BUILD_NAMES:
        raise WGSExtractError(
            "lineage y-private supports GRCh38 (primary) and T2T-CHM13v2.0; "
            "GRCh37/hg19 has no installed marker set or callable mask here. "
            "Realign to GRCh38 or T2T first (wgsextract realign)."
        )
    return build


def _ybrowse_set(library: Path) -> tuple[Path, dict[str, Any]]:
    from wgsextract_cli.core.ybrowse import installed_snapshot

    try:
        directory, manifest = installed_snapshot(library)
    except WGSExtractError as error:
        raise WGSExtractError(
            "lineage y-private on GRCh38 needs the YBrowse SNP snapshot as its "
            f"known-marker set. {error}"
        ) from error
    record = {
        "name": "YBrowse SNP snapshot",
        "assembly": "GRCh38",
        "snapshot_sha256": manifest["source_sha256"],
        "snp_alleles": manifest["snp_alleles"],
        "attribution": manifest["attribution"],
    }
    return directory / "sites.vcf.gz", record


def _yfull_set(build: str, library: Path) -> tuple[Path, dict[str, Any]]:
    from wgsextract_cli.ydna.yleaf_native.trees import ensure_module, tree_metadata

    meta = tree_metadata("yfull")
    root = ensure_module("yfull", "positions", library)
    record = {
        "name": f"{meta['name']} positions",
        "version": meta["version"],
        "assembly": BUILD_NAMES[build],
        "license": meta["modules"]["positions"]["license"],
    }
    return root / meta["positions"].format(build=build), record


def _call_alignment(
    params: YPrivateParams, alignment: str, ref: str, bed: Path, out: Path, work: Path
) -> None:
    """Haploid SNV calls inside the mask; MAPQ and base quality gate the reads."""
    run_pipeline(
        [
            [
                "bcftools",
                "mpileup",
                "-Ou",
                "-f",
                ref,
                "-R",
                str(bed),
                "-q",
                str(params.min_mapq),
                "-Q",
                str(params.base_quality),
                "-a",
                "FORMAT/AD,FORMAT/DP",
                alignment,
            ],
            ["bcftools", "call", "-m", "-v", "-V", "indels", "--ploidy", "1", "-Ou"],
            ["bcftools", "norm", "-m", "-any", "-f", ref, "-Ov", "-o", str(out)],
        ],  # fmt: skip
        description="Haploid chrY SNV calling in the callable mask",
        stderr_dir=work,
    )


def _select_vcf(vcf: str, bed: Path, out: Path, work: Path) -> None:
    indexed = any(Path(vcf + ext).exists() for ext in (".tbi", ".csi"))
    run_pipeline(
        [
            [
                "bcftools",
                "view",
                "-R" if indexed else "-T",
                str(bed),
                "-v",
                "snps",
                "-Ou",
                vcf,
            ],
            ["bcftools", "norm", "-m", "-any", "-Ov", "-o", str(out)],
        ],  # fmt: skip
        description="Select chrY SNVs in the callable mask",
        stderr_dir=work,
    )


def _number(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _read_calls(path: Path) -> tuple[list[str], list[_Call]]:
    """Header lines and the sample's ALT SNV calls (GT carrying the ALT)."""
    header: list[str] = []
    calls: list[_Call] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("#"):
                header.append(line)
                continue
            cols = line.rstrip("\n").split("\t")
            ref, alt = cols[3].upper(), cols[4].upper()
            if ref not in _BASES or alt not in _BASES or len(cols) < 10:
                continue
            fields = dict(zip(cols[8].split(":"), cols[9].split(":"), strict=False))
            if "1" not in re.split(r"[/|]", fields.get("GT", ".")):
                continue
            depths = fields.get("AD", ".").split(",")
            qual = None if cols[5] == "." else float(cols[5])
            calls.append(
                _Call(
                    chrom=cols[0],
                    pos=int(cols[1]),
                    ref=ref,
                    alt=alt,
                    qual=qual,
                    filter=cols[6],
                    ref_reads=_number(depths[0]),
                    alt_reads=_number(depths[1]) if len(depths) > 1 else None,
                    line=line,
                )
            )
    return header, calls


def _ybrowse_alleles(
    sites: Path, positions: set[int], work: Path
) -> dict[int, set[frozenset[str]]]:
    regions = work / "positions.tsv"
    regions.write_text(
        "".join(f"chrY\t{pos}\t{pos}\n" for pos in sorted(positions)),
        encoding="utf-8",
        newline="\n",
    )
    rows = run_command(
        ["bcftools", "query", "-R", str(regions), "-f", "%POS\t%REF\t%ALT\n", sites],
        capture_output=True,
    ).stdout.splitlines()
    known: dict[int, set[frozenset[str]]] = {}
    for row in rows:
        pos, ref, alts = row.split("\t")
        for alt in alts.split(","):
            known.setdefault(int(pos), set()).add(frozenset((ref, alt)))
    return known


def _yfull_alleles(
    table: Path, positions: set[int], known: dict[int, set[frozenset[str]]]
) -> None:
    """Add the YFull marker alleles at ``positions`` (chr, name, hg, pos, mutation,
    ancestral, derived per row)."""
    with table.open(encoding="utf-8") as stream:
        for row in csv.reader(stream, delimiter="\t"):
            if len(row) >= 7 and row[3].isdigit() and int(row[3]) in positions:
                alleles = frozenset((row[5].upper(), row[6].upper()))
                known.setdefault(int(row[3]), set()).add(alleles)


def _failed_gate(call: _Call, params: YPrivateParams) -> str | None:
    """The first filter a call fails, in the order the report lists them."""
    if call.filter not in (".", "PASS"):
        return "filter"
    if call.qual is None or call.qual < params.min_qual:
        return "qual"
    if call.alt_reads is None or call.alt_reads < params.min_alt_reads:
        return "alt_reads"
    if call.af < params.min_af:
        return "af"
    return None


def _write_report(
    path: Path, header: list[str], candidates: list[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.writelines(f"# {line}\n" for line in header)
        stream.write("\t".join(_COLUMNS) + "\n")
        for row in candidates:
            stream.write(
                "\t".join("" if row[c] is None else str(row[c]) for c in _COLUMNS)
                + "\n"
            )


def _write_vcf(path: Path, header: list[str], lines: list[str], work: Path) -> None:
    plain = work / "candidates.vcf"
    with plain.open("w", encoding="utf-8", newline="\n") as stream:
        stream.writelines(header[:-1])
        stream.write("##wgsextract_y_private=Candidate private Y SNVs; not confirmed\n")
        stream.write(header[-1])
        stream.writelines(lines)
    for index in (Path(f"{path}.tbi"), Path(f"{path}.csi")):
        index.unlink(missing_ok=True)
    with path.open("wb") as out:
        run_command(["bgzip", "-c", str(plain)], stdout=out)
    run_command(["tabix", "-f", "-p", "vcf", str(path)])


def run_y_private(params: YPrivateParams) -> None:
    """``lineage y-private``."""
    input_file = str(params.input)
    verify_paths_exist({"--input": input_file})
    lower = input_file.lower()
    is_vcf = lower.endswith(VCF_SUFFIXES)
    if not is_vcf and not lower.endswith((".bam", ".cram")):
        raise WGSExtractError(
            "lineage y-private reads an indexed BAM or CRAM, or a single-sample "
            "VCF/BCF with FORMAT/AD. Align FASTQ with `wgsextract align` first."
        )
    if not 0 < params.min_af <= 1:
        raise WGSExtractError("--min-af must be above 0 and at most 1.")
    tools = ["bcftools", "bgzip", "tabix", *([] if is_vcf else ["samtools"])]
    verify_dependencies(tools)
    log_dependency_info(tools)

    outdir_arg = str(params.outdir) if params.outdir else None
    outdir = absolute_path(outdir_arg or str(Path(input_file).absolute().parent))
    result = current_result()
    ref: str | None = None
    if is_vcf:
        header_text = _vcf_header(input_file)
        samples = run_command(
            ["bcftools", "query", "-l", input_file], capture_output=True
        ).stdout.split()
        if len(samples) != 1:
            raise WGSExtractError(
                f"lineage y-private needs a single-sample VCF; this one has "
                f"{len(samples)} samples."
            )
        if not re.search(r"^##FORMAT=<ID=AD,", header_text, re.MULTILINE):
            raise WGSExtractError(
                "The VCF has no FORMAT/AD, so the ALT read count and allele "
                "fraction filters can't be applied."
            )
        contigs = sequence_lengths(header_text)
        names = [] if {"chrY", "Y"} & set(contigs) else _vcf_y_name(input_file)
        chr_y = _y_contig(contigs, names)
    else:
        contigs = sequence_lengths(get_bam_header(input_file))
        chr_y = _y_contig(contigs, [])
    build = _resolve_build(contigs, chr_y, params.build)

    library = Path(resolve_reference_library())
    marker_sets: list[dict[str, Any]] = []
    ybrowse_sites: Path | None = None
    if build == "hg38":
        ybrowse_sites, record = _ybrowse_set(library)
        marker_sets.append(record)
    yfull_table, record = _yfull_set(build, library)
    marker_sets.append(record)
    if ybrowse_sites is None:
        warn(
            "known_markers_partial",
            "On T2T only the YFull YTree positions are known markers (the "
            "YBrowse snapshot is GRCh38-only), so SNPs that YBrowse names but "
            "YFull's tree doesn't place can be reported as candidates.",
            build=build,
        )

    if not is_vcf:
        from wgsextract_cli.commands.extract import require_reference
        from wgsextract_cli.commands.info.metrics import sample_sex

        lib = ReferenceLibrary(
            str(params.ref) if params.ref else None,
            calculate_bam_md5(input_file, None),
            input_path=input_file,
        )
        ref = require_reference(lib.fasta, "Y private-SNP calling", input_file)
        if read_fai_lengths(ref).get(chr_y) != contigs[chr_y]:
            raise WGSExtractError(
                f"The reference FASTA's {chr_y} doesn't match the input's "
                f"({contigs[chr_y]:,} bp); pass the FASTA it was aligned to."
            )
        if sample_sex(input_file, outdir_arg, reference=ref, allow_scan=False) == (
            "Female"
        ):
            raise WGSExtractError(
                f"{input_file} has almost no chrY reads (the sample appears "
                "female), so there are no Y SNPs to report."
            )
        warn_low_depth(
            input_file,
            {CHR_Y},
            outdir=outdir_arg,
            ref=ref,
            min_depth=params.qc_min_depth,
        )
        result.set_reference(ref, build)

    intervals = poz_intervals(build)
    mask_bases = sum(end - start for start, end in intervals)
    filters: dict[str, Any] = {
        "variant_types": "SNVs only (indels and MNPs are not reported)",
        "order": ["filter", "qual", "alt_reads", "af"],
        "filter": "FILTER is PASS or missing",
        "min_qual": params.min_qual,
        "min_alt_reads": params.min_alt_reads,
        "min_af": params.min_af,
        "min_mapq": None if is_vcf else params.min_mapq,
        "base_quality": None if is_vcf else params.base_quality,
        "mapping": "as applied by the VCF's caller (not re-checked)"
        if is_vcf
        else "reads below min_mapq and bases below base_quality are not counted",
    }

    result_dir = Path(outdir, "y_private")
    result_dir.mkdir(parents=True, exist_ok=True)
    stem = input_stem(input_file)
    report = result_dir / f"{stem}.y_private.tsv"
    candidate_vcf = result_dir / f"{stem}.y_private.vcf.gz"
    with tempfile.TemporaryDirectory(
        prefix=".y_private_", dir=scratch_parent(outdir)
    ) as temporary:
        work = Path(temporary)
        bed = work / "mask.bed"
        bed.write_text(
            "".join(f"{chr_y}\t{start}\t{end}\n" for start, end in intervals),
            encoding="utf-8",
            newline="\n",
        )
        calls_path = work / "calls.vcf"
        if is_vcf:
            _select_vcf(input_file, bed, calls_path, work)
        else:
            assert ref is not None
            _call_alignment(params, input_file, ref, bed, calls_path, work)
        header, calls = _read_calls(calls_path)

        positions = {call.pos for call in calls}
        known = (
            _ybrowse_alleles(ybrowse_sites, positions, work)
            if ybrowse_sites and positions
            else {}
        )
        if positions:
            _yfull_alleles(yfull_table, positions, known)

        counts = {"called": len(calls), "known": 0}
        counts.update({f"failed_{gate}": 0 for gate in filters["order"]})
        candidates: list[dict[str, Any]] = []
        lines: list[str] = []
        for call in calls:
            alleles = known.get(call.pos, set())
            if frozenset((call.ref, call.alt)) in alleles:
                counts["known"] += 1
                continue
            gate = _failed_gate(call, params)
            if gate:
                counts[f"failed_{gate}"] += 1
                continue
            candidates.append(
                {
                    "chrom": call.chrom,
                    "pos": call.pos,
                    "ref": call.ref,
                    "alt": call.alt,
                    "qual": call.qual,
                    "depth": call.depth,
                    "alt_reads": call.alt_reads,
                    "af": round(call.af, 4),
                    "note": OTHER_ALLELE if alleles else "",
                }
            )
            lines.append(call.line)
        counts["candidates"] = len(candidates)

        sets_text = "; ".join(
            f"{s['name']} {s.get('version') or s['snapshot_sha256'][:12]}"
            for s in marker_sets
        )
        report_header = [
            "WGS Extract lineage y-private: CANDIDATE private (novel) Y SNVs, "
            "not confirmed private SNPs.",
            f"Novel = alleles in no installed marker set: {sets_text}.",
            f"Callable mask: {MASK['name']}, {mask_bases:,} bp of "
            f"{BUILD_NAMES[build]} chrY. {MASK['scope']}",
            "Filters, in order: FILTER PASS or missing; QUAL >= "
            f"{params.min_qual:g}; ALT reads >= {params.min_alt_reads}; "
            f"ALT fraction >= {params.min_af:g}; SNVs only."
            + (
                ""
                if is_vcf
                else f" Reads with MAPQ < {params.min_mapq} and bases with "
                f"quality < {params.base_quality} are not counted."
            ),
            f"Called {counts['called']}, known {counts['known']}, "
            f"candidates {counts['candidates']}.",
        ]
        _write_report(report, report_header, candidates)
        _write_vcf(candidate_vcf, header, lines, work)

    result.add_output(report, "y_private_candidates")
    result.add_output(candidate_vcf, "y_private_vcf")
    result.set_details(
        {
            "build": build,
            "definition": DEFINITION,
            "marker_sets": marker_sets,
            "callable_mask": {**MASK, "bases": mask_bases},
            "filters": filters,
            "counts": counts,
            "candidates": candidates,
        }
    )
    result.add_metrics(
        y_private_candidates=counts["candidates"],
        y_called_snvs=counts["called"],
        y_known_snvs=counts["known"],
    )
    if len(candidates) > EXCESS_CANDIDATES:
        warn(
            "y_private_excess",
            f"{len(candidates)} candidate private Y SNVs is more than the "
            f"{EXCESS_CANDIDATES} expected: check for contamination, a wrong "
            "or mismatched reference, low-quality data, or a marker set that "
            "misses this lineage before trusting them.",
            count=len(candidates),
            threshold=EXCESS_CANDIDATES,
        )
    human_output(f"\n🧬 Candidate private Y SNVs ({stem}, {BUILD_NAMES[build]}):")
    human_output("-" * 30)
    human_output(*report_header, sep="\n")
    for row in candidates:
        note = f" ({row['note']})" if row["note"] else ""
        human_output(
            f"{row['chrom']}:{row['pos']} {row['ref']}>{row['alt']} "
            f"reads {row['alt_reads']}/{row['depth']}{note}"
        )
    human_output("-" * 30 + "\n")


cmd_y_private = cli_handler(YPrivateParams, run_y_private)
