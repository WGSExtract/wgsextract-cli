import bisect
import contextlib
import gzip
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from wgsextract_cli.core import ybrowse_grch37
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.ref_library.assets import (
    read_fai_lengths,
    resolve_reference_library,
)
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.temp_directory import scratch_parent
from wgsextract_cli.core.utils import (
    WGSExtractError,
    popen,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_files import ensure_vcf_indexed, get_chr_name
from wgsextract_cli.core.vcf_output import uppercase_vcf
from wgsextract_cli.core.ybrowse import (
    COPYRIGHT,
    SOURCE_URL,
    Y_LENGTH,
    installed_snapshot,
    require_allowed_paths,
)

from ._extract_helpers import BaseArgs, cleanup_files, derive_output_path

_N_RUN = re.compile(rb"[Nn]+")


def _n_runs(ref: str, contig: str, work: Path) -> list[tuple[int, int]]:
    """0-based, half-open runs of N in ``contig`` (hs38's masked PARs, gaps)."""
    errors = work / "faidx.err"
    with errors.open("wb") as stderr:
        query = popen(
            ["samtools", "faidx", ref, contig], stdout=subprocess.PIPE, stderr=stderr
        )
        assert query.stdout is not None
        query.stdout.readline()  # the FASTA header
        runs: list[tuple[int, int]] = []
        offset = 0
        while chunk := query.stdout.read(1 << 20):
            bases = chunk.replace(b"\n", b"").replace(b"\r", b"")
            for match in _N_RUN.finditer(bases):
                start, end = offset + match.start(), offset + match.end()
                if runs and runs[-1][1] == start:
                    start = runs.pop()[0]  # a run split across reads
                runs.append((start, end))
            offset += len(bases)
    if query.wait() != 0:
        detail = errors.read_text(errors="replace").strip()
        raise WGSExtractError(f"samtools faidx could not read {contig}: {detail}")
    return runs


# chrY and chr1 lengths of each build --ybrowse calls.
_BUILDS = {
    "GRCh38": (Y_LENGTH, 248_956_422),
    "GRCh37": (ybrowse_grch37.Y_LENGTH, ybrowse_grch37.CHR1_LENGTH),
}


def _build(contigs: dict[str, int], lengths: dict[str, int], chr_y: str) -> str:
    """The build both the alignment and the FASTA are, by their chrY and chr1."""
    if sum(name in contigs for name in ("Y", "chrY")) == 1:
        for build, (y_length, chr1_length) in _BUILDS.items():
            if (
                contigs.get(chr_y) == y_length
                and lengths.get(chr_y) == y_length
                and all(
                    sizes.get(name, chr1_length) == chr1_length
                    for sizes in (contigs, lengths)
                    for name in ("1", "chr1")
                )
            ):
                return build
    raise WGSExtractError(
        "--ybrowse requires an indexed reference matching the alignment's build: "
        f"GRCh38 (chrY/Y length {Y_LENGTH:,}) or GRCh37 (chrY/Y length "
        f"{ybrowse_grch37.Y_LENGTH:,}). T2T is unsupported."
    )


def _provenance(build: str, manifest: dict[str, object], excluded: int) -> str:
    lines = [
        f'##copyright="{COPYRIGHT}"',
        f"##ybrowse_source={json.dumps(SOURCE_URL)}",
        f"##ybrowse_snapshot_sha256={manifest['source_sha256']}",
        f"##ybrowse_attribution={json.dumps(str(manifest['attribution']))}",
        f"##ybrowse_excluded_reference_N_sites={excluded}",
    ]
    if build == "GRCh37":
        lift = cast(dict[str, object], manifest["liftover"])
        lines += [
            f"##ybrowse_liftover_chain={json.dumps(str(lift['name']))}",
            f"##ybrowse_liftover_chain_sha256={lift['sha256']}",
            "##ybrowse_liftover_counts="
            + json.dumps(manifest["counts"], separators=(",", ":")),
            f"##ybrowse_scope={ybrowse_grch37.SCOPE};Y_SNPs_only;"
            "HG_is_marker_annotation_not_sample_prediction",
        ]
    else:
        lines.append(
            "##ybrowse_scope=GRCh38_Y_SNPs_only;"
            "HG_is_marker_annotation_not_sample_prediction"
        )
    return "\n".join(lines) + "\n"


def call_ybrowse(input_path: str, base: BaseArgs) -> None:
    from .extract import _extract_regions, _warn_if_female, require_reference

    ref = require_reference(base.ref, "yBrowse Y calling", input_path)
    require_allowed_paths(ybrowse_grch37.fasta_paths(Path(ref)))
    contigs = sequence_lengths(get_bam_header(input_path, base.cram_opt))
    chr_y = get_chr_name(input_path, "Y", base.cram_opt)
    build = _build(contigs, read_fai_lengths(ref), chr_y)
    library = Path(resolve_reference_library())
    directory, manifest = (
        ybrowse_grch37.installed_grch37(library)
        if build == "GRCh37"
        else installed_snapshot(library)
    )
    _warn_if_female(input_path, base.outdir, ref)
    output = derive_output_path(input_path, base.outdir, "_Y_ybrowse.vcf.gz")
    with tempfile.TemporaryDirectory(
        prefix=".ybrowse_", dir=scratch_parent(base.outdir)
    ) as temporary:
        work = Path(temporary)
        normalized = work / "sites.vcf.gz"
        rename = work / "rename.tsv"
        rename.write_text(f"chrY\t{chr_y}\n", encoding="utf-8", newline="\n")
        # A reference N (hs38/no-alt mask the PARs) makes a site unavailable,
        # not a REF mismatch: drop it before checking and calling.
        masked = _n_runs(ref, chr_y, work)
        masked_starts = [start for start, _ in masked]
        masked_bed = work / "reference_n.bed"
        masked_bed.write_text(
            "".join(f"{chr_y}\t{start}\t{end}\n" for start, end in masked),
            encoding="utf-8",
            newline="\n",
        )
        bed = work / "sites.bed"
        targets = work / "targets.tsv"
        excluded = kept = 0
        with (
            gzip.open(directory / "targets.tsv.gz", "rt", encoding="utf-8") as source,
            targets.open("w", encoding="utf-8", newline="\n") as target_out,
            bed.open("w", encoding="utf-8", newline="\n") as bed_out,
        ):
            for line in source:
                _, pos, alleles = line.rstrip("\n").split("\t")
                start = int(pos) - 1
                run = bisect.bisect_right(masked_starts, start) - 1
                if run >= 0 and start < masked[run][1]:
                    excluded += 1
                    continue
                kept += 1
                target_out.write(f"{chr_y}\t{pos}\t{alleles}\n")
                bed_out.write(f"{chr_y}\t{start}\t{pos}\n")
        current_result().add_metrics(ybrowse_excluded_reference_n_sites=excluded)
        if excluded:
            warn(
                "ybrowse_reference_n_sites_excluded",
                f"Skipped {excluded:,} yBrowse sites where the reference {chr_y} "
                "base is N (for example its masked PARs); they are not called.",
                sites=excluded,
                contig=chr_y,
            )
        if not kept:
            raise WGSExtractError(
                f"The reference {chr_y} is N at every installed yBrowse site; "
                f"use a {build} FASTA whose chrY is not masked."
            )
        # Fail on REF disagreement before decoding/calling the user's alignment.
        run_pipeline(
            [
                [
                    "bcftools",
                    "annotate",
                    "--rename-chrs",
                    str(rename),
                    "-Ou",
                    str(directory / "sites.vcf.gz"),
                ],
                *(
                    [["bcftools", "view", "-T", f"^{masked_bed}", "-Ou"]]
                    if masked
                    else []
                ),
                [
                    "bcftools",
                    "norm",
                    "-c",
                    "e",
                    "-f",
                    ref,
                    "-Oz",
                    "-o",
                    str(normalized),
                ],
            ],
            description=f"Verify yBrowse REF alleles against {build}",
            stderr_dir=work,
        )
        ensure_vcf_indexed(str(normalized))
        compressed_targets = work / "targets.tsv.gz"
        with compressed_targets.open("wb") as stream:
            run_command(["bgzip", "-c", str(targets)], stdout=stream)
        run_command(["tabix", "-s", "1", "-b", "2", "-e", "2", str(compressed_targets)])
        provenance = work / "provenance.txt"
        provenance.write_text(
            _provenance(build, manifest, excluded), encoding="utf-8", newline="\n"
        )
        bam = str(work / "Y.bam")
        called = str(work / "called.vcf.gz")
        _extract_regions(base, input_path, bam, [chr_y], bed=str(bed))
        with cleanup_files(on_failure=[output, output + ".tbi", output + ".csi"]):
            for index in (output + ".tbi", output + ".csi"):
                with contextlib.suppress(FileNotFoundError):
                    Path(index).unlink()
            run_pipeline(
                [
                    [
                        "bcftools",
                        "mpileup",
                        "-Ou",
                        "-f",
                        ref,
                        "-a",
                        "FORMAT/DP,FORMAT/AD",
                        "-T",
                        str(bed),
                        bam,
                    ],
                    [
                        "bcftools",
                        "call",
                        "-m",
                        "-A",
                        "-i",
                        "--ploidy",
                        "1",
                        "-C",
                        "alleles",
                        "-T",
                        str(compressed_targets),
                        "-Ou",
                    ],
                    *uppercase_vcf(
                        ["bcftools", "norm", "-m", "-any", "--multi-overlaps", "."],
                        called,
                    ),
                ],
                description="Targeted haploid yBrowse calling",
                stderr_dir=work,
            )
            ensure_vcf_indexed(called)
            run_command(
                [
                    "bcftools",
                    "annotate",
                    "--pair-logic",
                    "exact",
                    "-a",
                    str(normalized),
                    "-c",
                    "ID,INFO/HG,INFO/ISOGG,INFO/AA",
                    "-h",
                    str(provenance),
                    "-Oz",
                    "-o",
                    output,
                    called,
                ]
            )
            ensure_vcf_indexed(output)
    current_result().add_output(output, "y_vcf")
