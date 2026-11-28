"""Resolve local comparison inputs, verify their builds and normalize the calls."""

from __future__ import annotations

import gzip
import io
import re
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
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
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    popen,
    run_command,
    run_pipeline,
)
from wgsextract_cli.core.variant_confidence import SPLIT_TAG, SYMBOLIC_REFERENCE_ALTS
from wgsextract_cli.core.variant_files import chromosome_aliases, vcf_header_chromosomes

_CONTIG = re.compile(r"^##contig=<.*?\bID=([^,>]+)")
_BASES = re.compile("[ACGTN]+", re.IGNORECASE | re.ASCII)
_SEPARATOR = re.compile(r"[/|]")
_STALE_INFO = {"AC", "AN", "AF", "NS"}
PARTIAL = "WGSE_COMPARE_PARTIAL"
ALIGN_ORIGIN = "WGSE_COMPARE_ALIGN_ORIGIN"
SOURCE_RECORD = "WGSE_COMPARE_SOURCE_RECORD"


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
        with popen(command, stdout=subprocess.PIPE, stderr=errors) as process:
            try:
                assert process.stdout is not None
                with io.TextIOWrapper(process.stdout, encoding="utf-8") as stream:
                    yield stream
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


def _sequence_record(fields: list[str], alts: list[str], record_id: int) -> None:
    remap = {"0": "0"}
    small = []
    for index, alt in enumerate(alts, 1):
        if _BASES.fullmatch(alt):
            small.append(alt)
            remap[str(index)] = str(len(small))
    fields[4] = ",".join(small) or "."
    sample = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
    gt = re.split(r"([/|])", sample.get("GT", "."))
    gt[::2] = [remap.get(allele, ".") for allele in gt[::2]]
    fields[2] = fields[5] = "."
    flags = [PARTIAL] if "." in gt[::2] else []
    if len(small) > 1:
        flags.append(f"{SOURCE_RECORD}={record_id}")
    fields[7] = ";".join(flags) or "."
    fields[8:10] = ["GT", "".join(gt)]
    if "FT" in sample:
        fields[8] += ":FT"
        fields[9] += ":" + sample["FT"]


def _normalize_calls(
    raw: Path, normalized: Path, ref: Path | None, *, atomize: bool
) -> None:
    norm = ["bcftools", "norm", "--no-version"]
    split = ["-m", "-any", "--multi-overlaps", "."]
    stages: list[Sequence[object]]
    if atomize:
        assert ref is not None
        align = ["-f", ref, "-c", "e"]
        stages = [
            # Atomizing several ALTs together can assign a primitive to the
            # wrong GT slot. Split and check the full REF before atomizing.
            [*norm, *split, *align, "-Ou", raw],
            [*norm, "--atomize", "--atom-overlaps", ".", "-Ou"],
            [*norm, *align, "-Ou"],
        ]
    else:
        stages = [[*norm, *split, "--old-rec-tag", SPLIT_TAG, "-Ou", raw]]
        if ref is not None:
            stages.append(
                [
                    *norm,
                    "-f",
                    ref,
                    "-c",
                    "e",
                    "--old-rec-tag",
                    ALIGN_ORIGIN,
                    "-Ou",
                ]
            )
    work = raw.parent
    stages.append(
        [
            "bcftools",
            "sort",
            "-m",
            "128M",
            "-T",
            work / "sort",
            "-Ob",
            "-o",
            normalized,
        ]
    )
    run_pipeline(stages, stderr_dir=work, description="Normalize comparison callset")


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
    blockers_only: bool = False,
    atomize: bool = False,
) -> tuple[Path, ReferenceEvidence | None, int]:
    work.mkdir()
    mapped = dictionary.mapping(source.contigs)
    raw_calls = work / "calls.vcf.gz"
    raw_evidence = work / "confidence.vcf.gz"
    from_bcftools = bcftools_gvcf_header(source.header)
    found_evidence = False
    skipped = 0
    with (
        vcf_stream(source.path, work, "source") as stream,
        (
            gzip.open(raw_calls, "wt", compresslevel=1, encoding="utf-8", newline="\n")
            if calls
            else nullcontext()
        ) as call_file,
        (
            gzip.open(
                raw_evidence, "wt", compresslevel=1, encoding="utf-8", newline="\n"
            )
            if evidence
            else nullcontext()
        ) as evidence_file,
    ):
        header = _headers(source, mapped, dictionary.names)
        reserved = (
            (PARTIAL, ALIGN_ORIGIN, SOURCE_RECORD)
            if atomize
            else (PARTIAL, ALIGN_ORIGIN)
        )
        if any(f"##INFO=<ID={tag}," in header for tag in reserved):
            raise UsageError(
                f"{source.path}: header contains reserved comparison fields."
            )
        marker = f'##INFO=<ID={PARTIAL},Number=0,Type=Flag,Description="Original genotype incomplete">\n'
        if atomize:
            marker += f'##INFO=<ID={SOURCE_RECORD},Number=1,Type=String,Description="Source multiallelic record identifier">\n'
        if call_file is not None:
            call_file.write(header.replace("\n#CHROM\t", f"\n{marker}#CHROM\t"))
        if evidence_file is not None:
            identity = {name: name for name in source.contigs}
            evidence_file.write(_headers(source, identity, identity))
        for record_id, fields in enumerate(_selected_lines(source, stream), 1):
            name = fields[0]
            if name not in mapped:
                raise UsageError(
                    f"{source.path}: contig {name!r} is not declared in the header."
                )
            if evidence_file is not None:
                confident, keep = _evidence_record(fields, from_bcftools)
                found_evidence |= confident
                if keep or blockers_only:
                    evidence_file.write("\t".join(fields) + "\n")
            if call_file is not None:
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
                if atomize:
                    _sequence_record(fields, alts, record_id)
                    call_file.write("\t".join(fields) + "\n")
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
    normalized = work / "normalized.bcf"
    if calls:
        _normalize_calls(raw_calls, normalized, ref, atomize=atomize)
        raw_calls.unlink()
    evidence_source = None
    if found_evidence or blockers_only:
        sorted_evidence = work / "sorted.g.vcf.gz"
        run_command(
            [
                "bcftools",
                "sort",
                "-m",
                "128M",
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
    raw_evidence.unlink(missing_ok=True)
    return normalized, evidence_source, skipped
