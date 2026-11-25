"""Resolve local comparison inputs, verify their builds and normalize the calls."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from wgsextract_cli.api.vcf_specs.compare import local_path
from wgsextract_cli.core.builds import (
    build_from_chr1_length,
    build_from_path,
    build_from_reference_header,
)
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.ref_library.assets import read_fai_lengths
from wgsextract_cli.core.ref_library.mitochondrial import header_md5
from wgsextract_cli.core.ref_library.models import sequence_lengths
from wgsextract_cli.core.reference_evidence import ReferenceEvidence, readable
from wgsextract_cli.core.reference_evidence_records import bcftools_gvcf_header
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_confidence import SPLIT_TAG, SYMBOLIC_REFERENCE_ALTS
from wgsextract_cli.core.variant_files import chromosome_aliases, vcf_header_chromosomes

_CONTIG = re.compile(r"^##contig=<.*?\bID=([^,>]+)")
_BASES = re.compile("[ACGTN]+", re.IGNORECASE | re.ASCII)
_SEPARATOR = re.compile(r"[/|]")
_STALE_INFO = {"AC", "AN", "AF", "NS"}
PARTIAL = "WGSE_COMPARE_PARTIAL"
ALIGN_ORIGIN = "WGSE_COMPARE_ALIGN_ORIGIN"


def require_local(path: str | Path) -> Path:
    local = local_path(path).expanduser().absolute()
    if not local.is_file():
        raise UsageError(f"Local input file not found: {local}.")
    return local


def reference_indexes(ref: Path) -> Iterator[Path]:
    yield Path(f"{ref}.fai")
    with ref.open("rb") as stream:
        if stream.read(2) == b"\x1f\x8b":
            yield Path(f"{ref}.gzi")


@dataclass(frozen=True)
class Input:
    path: Path
    header: str
    sample: str
    sample_column: int
    contigs: list[str]
    lengths: dict[str, int]
    build: str | None
    md5s: dict[str, str]

    @classmethod
    def read(cls, path: Path, sample: str | None, label: str) -> Input:
        header = run_command(
            ["bcftools", "view", "--no-version", "-h", path], capture_output=True
        ).stdout
        samples = header.rstrip().splitlines()[-1].split("\t")[9:]
        if sample is None and len(samples) != 1:
            raise UsageError(
                f"{label}: select one sample with --sample {label}=SAMPLE."
            )
        if sample is None:
            sample = samples[0]
        if sample not in samples:
            raise UsageError(f"{label}: sample {sample!r} is not in {path}.")
        contigs, lengths = vcf_header_chromosomes(header), sequence_lengths(header)
        detected = {build_from_chr1_length(lengths)}
        for line in header.splitlines():
            key, _, value = line.partition("=")
            if key == "##reference":
                detected.add(build_from_reference_header(value))
            elif key == "##assembly":
                detected.add(build_from_path(value))
            elif key == "##contig" and (
                assembly := re.search(r"[,<]assembly=([^,>]+)", value)
            ):
                detected.add(build_from_path(assembly[1]))
        detected.discard(None)
        if len(detected) > 1:
            raise UsageError(
                f"{label}: conflicting reference build metadata in {path}."
            )
        md5s = {name: md5 for name in contigs if (md5 := header_md5(header, name))}
        return cls(
            path,
            header,
            sample,
            9 + samples.index(sample),
            contigs,
            lengths,
            next(iter(detected), None),
            md5s,
        )


def _alias_key(name: str) -> str:
    return min(chromosome_aliases(name))


@dataclass(frozen=True)
class Dictionary:
    names: dict[str, str]
    lengths: dict[str, int]
    build: str | None

    def mapping(self, names: list[str], *, allow_new: bool = False) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for name in names:
            key = _alias_key(name)
            if allow_new:
                self.names.setdefault(key, name)
            if key not in self.names:
                raise UsageError(f"Contig {name!r} is not in the comparison reference.")
            mapping[name] = self.names[key]
        if len(set(mapping.values())) != len(names):
            raise UsageError(
                "Contig alias collision: an input declares multiple names for the same contig."
            )
        return mapping


def verify_build(
    inputs: dict[str, Input],
    ref: Path | None,
    allow_unverified: bool,
) -> Dictionary:
    lengths = read_fai_lengths(ref) if ref is not None else {}
    if ref is not None and not lengths:
        raise UsageError(
            f"Missing or unreadable reference index {ref}.fai; run samtools faidx."
        )
    if ref is not None:
        for index in reference_indexes(ref):
            if not index.is_file():
                raise UsageError(
                    f"Missing reference index {index}; run samtools faidx."
                )
    ref_build = build_from_chr1_length(lengths)
    declared = {s.build for s in inputs.values() if s.build is not None}
    if ref_build is not None:
        declared.add(ref_build)
    if len(declared) > 1:
        raise UsageError(f"Reference build mismatch: {', '.join(sorted(declared))}.")
    build = next(iter(declared), None)
    dictionary = Dictionary({}, lengths, build)
    dictionary.mapping(list(lengths), allow_new=True)
    digests: dict[str, str] = {}
    reference_md5s: dict[str, str] = {}
    if ref is not None and any(s.md5s for s in inputs.values()):
        header = run_command(["samtools", "dict", ref], capture_output=True).stdout
        reference_md5s = {
            name: md5 for name in lengths if (md5 := header_md5(header, name))
        }
        digests.update(reference_md5s)
    unverified = []
    digest_sets = []
    for label, source in inputs.items():
        mapping = dictionary.mapping(source.contigs, allow_new=ref is None)
        for name, target in mapping.items():
            length = source.lengths.get(name)
            if length is not None and lengths.setdefault(target, length) != length:
                raise UsageError(
                    f"{label}: reference build mismatch at {name} (contig length)."
                )
            md5 = source.md5s.get(name)
            if md5 is not None and digests.setdefault(target, md5) != md5:
                raise UsageError(
                    f"{label}: reference sequence mismatch at {name} (MD5)."
                )
        complete = bool(mapping) and len(source.md5s) == len(mapping)
        digest_sets.append(frozenset(mapping.values()) if complete else frozenset())
        matches_ref = complete and all(
            reference_md5s.get(target) == source.md5s[name]
            for name, target in mapping.items()
        )
        if not matches_ref and (
            source.build is None or (ref is not None and ref_build is None)
        ):
            unverified.append(label)
    same_digest_dictionary = (
        ref is None
        and bool(digest_sets[0])
        and all(item == digest_sets[0] for item in digest_sets)
    )
    if unverified and not same_digest_dictionary:
        message = (
            f"Cannot verify a common reference build for: {', '.join(unverified)}."
        )
        if not allow_unverified:
            raise UsageError(
                f"{message} Supply an indexed --ref and matching build metadata, "
                "or explicitly accept the risk with --allow-unverified-build."
            )
        warn("vcf_compare_unverified_build", message)
    return dictionary


@contextmanager
def vcf_stream(path: Path, work: Path, tag: str) -> Iterator[IO[str]]:
    command = ["bcftools", "view", "--no-version", str(path)]
    with (work / f"{tag}.stderr").open("w+", encoding="utf-8", newline="\n") as errors:
        with popen(
            command, stdout=subprocess.PIPE, stderr=errors, text=True
        ) as process:
            try:
                assert process.stdout is not None
                yield process.stdout
                if process.wait():
                    errors.seek(0)
                    raise WGSExtractError(
                        f"Could not read {path}: {errors.read(4096).strip()}"
                    )
            finally:
                if process.poll() is None:
                    process.kill()


def _selected_lines(
    source: Input,
    stream: IO[str],
) -> Iterator[list[str]]:
    for line in stream:
        if not line.startswith("#"):
            fields = line.rstrip("\n").split("\t")
            yield [*fields[:9], fields[source.sample_column]]


def _headers(source: Input, mapping: dict[str, str], order: dict[str, str]) -> str:
    meta, assigned, columns = [], {}, ""
    for line in source.header.splitlines():
        if match := _CONTIG.match(line):
            chrom = mapping[match[1]]
            assigned[chrom] = line[: match.start(1)] + chrom + line[match.end(1) :]
        elif line.startswith("#CHROM"):
            columns = "\t".join(line.split("\t")[:9] + [source.sample])
        else:
            meta.append(line)
    return "\n".join(
        [*meta, *(assigned[c] for c in order.values() if c in assigned), columns, ""]
    )


def genotype(fields: list[str]) -> list[str]:
    values = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
    return _SEPARATOR.split(values.get("GT", "."))


def _evidence_record(fields: list[str], from_bcftools: bool) -> tuple[bool, bool]:
    alts = set(fields[4].split(","))
    confidence = bool(alts & SYMBOLIC_REFERENCE_ALTS) or (
        alts == {"."}
        and (
            from_bcftools or any(tag.startswith("END=") for tag in fields[7].split(";"))
        )
    )
    # Never let a plain VCF's hom-ref genotype serve as a reference-confidence record.
    return confidence, confidence or any(a != "0" for a in genotype(fields))


def prepare(
    source: Input,
    dictionary: Dictionary,
    ref: Path | None,
    work: Path,
    *,
    calls: bool,
    evidence: bool,
) -> tuple[Path, ReferenceEvidence | None, int]:
    work.mkdir()
    mapped = dictionary.mapping(source.contigs)
    raw_calls = work / "calls.vcf"
    raw_evidence = work / "confidence.vcf"
    from_bcftools = bcftools_gvcf_header(source.header)
    found_evidence = False
    skipped = 0
    with (
        vcf_stream(source.path, work, "source") as stream,
        raw_calls.open("w", encoding="utf-8", newline="\n") as call_file,
        raw_evidence.open("w", encoding="utf-8", newline="\n") as evidence_file,
    ):
        header = _headers(source, mapped, dictionary.names)
        if any(f"##INFO=<ID={tag}," in header for tag in (PARTIAL, ALIGN_ORIGIN)):
            raise UsageError(
                f"{source.path}: header contains reserved comparison fields."
            )
        marker = f'##INFO=<ID={PARTIAL},Number=0,Type=Flag,Description="Original genotype incomplete">\n'
        call_file.write(header.replace("\n#CHROM\t", f"\n{marker}#CHROM\t"))
        if evidence:
            identity = {name: name for name in source.contigs}
            evidence_file.write(_headers(source, identity, identity))
        for fields in _selected_lines(source, stream):
            name = fields[0]
            if name not in mapped:
                raise UsageError(
                    f"{source.path}: contig {name!r} is not declared in the header."
                )
            if evidence:
                confident, keep = _evidence_record(fields, from_bcftools)
                found_evidence |= confident
                if keep:
                    evidence_file.write("\t".join(fields) + "\n")
            if calls:
                fields[0] = mapped[name]
                alts = fields[4].split(",")
                skipped += sum(
                    alt != "."
                    and alt not in SYMBOLIC_REFERENCE_ALTS
                    and not _BASES.fullmatch(alt)
                    for alt in alts
                )
                if alts != ["."] and not any(_BASES.fullmatch(alt) for alt in alts):
                    continue
                tags = [
                    tag
                    for tag in fields[7].split(";")
                    if tag.split("=", 1)[0] not in _STALE_INFO
                ]
                if any(
                    not a.isdigit()
                    or int(a) > len(alts)
                    or (int(a) > 0 and alts[int(a) - 1] in SYMBOLIC_REFERENCE_ALTS)
                    for a in genotype(fields)
                ):
                    tags = [tag for tag in tags if tag != "."] + [PARTIAL]
                fields[7] = ";".join(tags) or "."
                call_file.write("\t".join(fields) + "\n")
    normalized = work / "normalized.vcf"
    if calls:
        split = work / "split.bcf"
        # Keep non-target ALTs missing in the emitted GT; the marker preserves original completeness.
        run_command(
            [
                "bcftools",
                "norm",
                "--no-version",
                "-m",
                "-any",
                "--multi-overlaps",
                ".",
                "--old-rec-tag",
                SPLIT_TAG,
                "-Ob",
                "-o",
                split,
                raw_calls,
            ],
            capture_output=True,
        )
        raw_calls.unlink()
        align = split
        if ref is not None:
            align = work / "aligned.bcf"
            run_command(
                [
                    "bcftools",
                    "norm",
                    "--no-version",
                    "-f",
                    ref,
                    "-c",
                    "e",
                    "--old-rec-tag",
                    ALIGN_ORIGIN,
                    "-Ob",
                    "-o",
                    align,
                    split,
                ],
                capture_output=True,
            )
            split.unlink()
        run_command(
            ["bcftools", "sort", "-T", work / "sort", "-Ov", "-o", normalized, align],
            capture_output=True,
        )
        align.unlink()
    evidence_source = None
    if found_evidence:
        sorted_evidence = work / "sorted.g.vcf.gz"
        run_command(
            [
                "bcftools",
                "sort",
                "-T",
                work / "evidence-sort",
                "-Oz",
                "-o",
                sorted_evidence,
                raw_evidence,
            ],
            capture_output=True,
        )
        run_command(["bcftools", "index", sorted_evidence])
        if ready := readable(str(sorted_evidence), source.sample, work):
            known = {
                c: dictionary.lengths[t]
                for c, t in mapped.items()
                if t in dictionary.lengths
            }
            evidence_source = ReferenceEvidence(ready, known, allow_region_files=False)
            if ref is not None:
                verify_dependencies(["tabix"])
                evidence_source = evidence_source.normalized(str(ref), work)
    raw_evidence.unlink()
    return normalized, evidence_source, skipped
