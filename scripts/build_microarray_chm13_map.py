"""Reproduce the marker-only CHM13 site map and pinned mitochondrial bridge."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict

from pyliftover import LiftOver

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.chm13_mito_bridge import (  # noqa: E402
    MitochondrialBridge,
    align_mitochondria,
    file_sha256,
    mitochondrial_artifact,
)
from wgsextract_cli.core.microarray.aadr import (
    _MARKERS,
    _chrom_name,
    aadr_markers,
)
from wgsextract_cli.core.microarray.chm13_markers import non_snv_alleles
from wgsextract_cli.core.microarray.site_map import (
    _CHROMS,
    _MAGIC,
    _REF_STATUSES,
    _STATUSES,
    SiteProjection,
    load_site_map,
)
from wgsextract_cli.core.microarray.targets import _CHAINS_TO_GRCH37, _template_paths
from wgsextract_cli.core.microarray.targets import CHM13_V2_FORMATS as _FORMATS
from wgsextract_cli.core.microarray.templates import (
    _chrom_for_liftover,
    _resolve_templates_roots,
    lift_to_grch37,
    template_rows,
)

_COMPLEMENT = str.maketrans("ACGT", "TGCA")


class PARInterval(TypedDict):
    chrom: str
    start0: int
    end0: int


def collect_markers(
    roots: Sequence[str],
    chains: Mapping[str, Path],
    source_lengths: Mapping[str, int],
) -> tuple[dict[tuple[str, int], str], dict[str, object]]:
    """The GRCh37 union of the templates' and AADR's markers; chains lifts a
    template on another build (NCBI36, GRCh38) to GRCh37."""
    paths = list(_template_paths(_resolve_templates_roots(list(roots)), _FORMATS))
    if missing := set(_FORMATS) - {name for name, _build, _parts in paths}:
        raise ValueError(f"Missing template layouts: {sorted(missing)}")
    lifters = {build: LiftOver(str(chain)) for build, chain in chains.items()}
    markers: dict[tuple[str, int], str] = {}
    source_ids: set[str] = set()
    formats: dict[str, object] = {}
    for name, build, parts in paths:
        counts: Counter[str] = Counter()
        for path in parts:
            for rsid, chrom, pos in template_rows(name, path):
                counts["rows"] += 1
                source_ids.add(rsid)
                if not pos.isdigit() or int(pos) < 1:
                    counts["unplaced"] += 1
                    continue
                if build:
                    lifted = lift_to_grch37(lifters[build], chrom, pos)
                    if lifted is None:
                        counts[f"{build.lower()}_no_hit"] += 1
                        continue
                    chrom, pos, _inverted = lifted
                canonical = _chrom_for_liftover(chrom)
                if canonical not in source_lengths:
                    counts["nonprimary_source"] += 1
                    continue
                if not 1 <= int(pos) <= source_lengths[canonical]:
                    counts["source_out_of_range"] += 1
                    continue
                counts["placed_rows"] += 1
                markers.setdefault((canonical, int(pos)), rsid)
        formats[name] = {
            "counts": dict(counts),
            "parts": [
                {"part": index + 1, "sha256": file_sha256(Path(path))}
                for index, path in enumerate(parts)
            ],
        }
    legacy_sites = len(markers)
    aadr_counts: Counter[str] = Counter()
    non_snv_sites: set[tuple[str, int]] = set()
    non_snv_ids: set[str] = set()
    for marker in aadr_markers("1240k"):
        aadr_counts["1240k_rows"] += 1
        if marker.ho:
            aadr_counts["ho_rows"] += 1
        source_ids.add(marker.snp_id)
        unsupported = non_snv_alleles(marker.ref, marker.alt)
        if unsupported:
            aadr_counts["non_snv_marker_rows"] += 1
            non_snv_ids.add(marker.snp_id)
        chrom = _chrom_for_liftover(_chrom_name(marker.chrom))
        if chrom not in source_lengths or not (
            1 <= marker.position <= source_lengths[chrom]
        ):
            aadr_counts["unplaced"] += 1
            continue
        if unsupported:
            non_snv_sites.add((chrom, marker.position))
        markers.setdefault((chrom, marker.position), marker.snp_id)
    return markers, {
        "layouts": formats,
        "aadr": {
            "release": "v66.p1",
            "sha256": file_sha256(_MARKERS),
            "counts": dict(aadr_counts),
        },
        "legacy_sites": legacy_sites,
        "complete_sites": len(markers),
        "distinct_source_ids": len(source_ids),
        "source_alleles": {
            "source": "AADR v66.p1; vendor source alleles are not packaged",
            "known_marker_rows": aadr_counts["1240k_rows"],
            "non_snv_marker_rows": aadr_counts["non_snv_marker_rows"],
            "non_snv_site_count": len(non_snv_sites),
            "non_snv_id_count": len(non_snv_ids),
            "non_snv_sites": sorted(non_snv_sites),
            "non_snv_ids": sorted(non_snv_ids),
            "bundled_manifest_defined_indels": 0,
            "manifest_policy": "User-provided known I/D/non-SNV markers are always no-call on CHM13",
        },
    }


class ReferenceSequence(Protocol):
    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> str: ...


@dataclass(frozen=True)
class _IndexedSequence:
    reference: ReferenceSequences
    chromosome: str

    def __len__(self) -> int:
        return self.reference.lengths[self.chromosome]

    def __getitem__(self, index: int) -> str:
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.reference.base(self.chromosome, index + 1)


class ReferenceSequences:
    def __init__(self, fasta: Path, dictionary: Path) -> None:
        self.fasta = fasta
        self.names: dict[str, str] = {}
        self.lengths: dict[str, int] = {}
        self.md5s: dict[str, str] = {}
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._windows: OrderedDict[tuple[str, int], str] = OrderedDict()
        self._verified: set[str] = set()
        for line in dictionary.read_text().splitlines():
            if not line.startswith("@SQ\t"):
                continue
            fields = dict(field.split(":", 1) for field in line.split("\t")[1:])
            actual = fields["SN"]
            suffix = actual.removeprefix("chr")
            chrom = "chrM" if suffix in {"M", "MT"} else f"chr{suffix}"
            if chrom not in _CHROMS:
                continue
            if chrom in self.names:
                raise ValueError(f"Duplicate primary reference chromosome: {chrom}")
            md5 = fields.get("M5", "")
            if re.fullmatch(r"[a-f0-9]{32}", md5) is None:
                raise ValueError(f"Reference dictionary has no valid MD5: {actual}")
            self.names[chrom] = actual
            self.lengths[chrom] = int(fields["LN"])
            self.md5s[chrom] = md5
        if set(self.names) != set(_CHROMS):
            raise ValueError("Reference must contain all 25 primary chromosomes")

    def sequence(self, chromosome: str) -> str:
        if chromosome in self._cache:
            self._cache.move_to_end(chromosome)
            return self._cache[chromosome]
        actual = self.names[chromosome]
        command = ["samtools", "faidx", str(self.fasta), actual]
        bases = bytearray()
        with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
            assert process.stdout is not None
            header = process.stdout.readline().strip()
            for line in process.stdout:
                bases.extend(line.strip())
            returncode = process.wait()
            if returncode:
                raise subprocess.CalledProcessError(returncode, command)
        if header != f">{actual}".encode():
            raise ValueError(f"Unexpected reference FASTA header for {actual}")
        sequence = bases.decode("ascii").upper()
        if (
            len(sequence) != self.lengths[chromosome]
            or hashlib.md5(sequence.encode()).hexdigest() != self.md5s[chromosome]
        ):
            raise ValueError(f"Reference sequence/dictionary mismatch: {actual}")
        self._cache[chromosome] = sequence
        self._verified.add(chromosome)
        while len(self._cache) > 2:
            self._cache.popitem(last=False)
        return sequence

    def _verify(self, chromosome: str) -> None:
        if chromosome in self._verified:
            return
        actual = self.names[chromosome]
        command = ["samtools", "faidx", str(self.fasta), actual]
        digest = hashlib.md5()
        length = 0
        with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
            assert process.stdout is not None
            header = process.stdout.readline().strip()
            for line in process.stdout:
                bases = line.strip().upper()
                length += len(bases)
                digest.update(bases)
            returncode = process.wait()
            if returncode:
                raise subprocess.CalledProcessError(returncode, command)
        if (
            header != f">{actual}".encode()
            or length != self.lengths[chromosome]
            or digest.hexdigest() != self.md5s[chromosome]
        ):
            raise ValueError(f"Reference sequence/dictionary mismatch: {actual}")
        self._verified.add(chromosome)

    def indexed_sequence(self, chromosome: str) -> ReferenceSequence:
        if chromosome == "chrM":
            return self.sequence(chromosome)
        self._verify(chromosome)
        return _IndexedSequence(self, chromosome)

    def base(self, chromosome: str, position: int) -> str:
        if not 1 <= position <= self.lengths[chromosome]:
            raise IndexError(position)
        self._verify(chromosome)
        start = (position - 1) // 1_000_000 * 1_000_000
        key = (chromosome, start)
        if key not in self._windows:
            end = min(start + 1_000_000, self.lengths[chromosome])
            region = f"{self.names[chromosome]}:{start + 1}-{end}"
            command = ["samtools", "faidx", str(self.fasta), region]
            data = subprocess.check_output(command).splitlines()
            if not data or data[0] != f">{region}".encode():
                raise ValueError(f"Unexpected reference FASTA header for {region}")
            sequence = b"".join(data[1:]).decode("ascii").upper()
            if len(sequence) != end - start:
                raise ValueError(f"Truncated reference window: {region}")
            self._windows[key] = sequence
            while len(self._windows) > 4:
                self._windows.popitem(last=False)
        self._windows.move_to_end(key)
        return self._windows[key][position - start - 1]

    def signature(self) -> list[dict[str, object]]:
        return [
            {
                "name": chrom,
                "length": self.lengths[chrom],
                "md5": self.md5s[chrom],
            }
            for chrom in _CHROMS
        ]


class CoordinateLifter(Protocol):
    def convert_coordinate(
        self, chromosome: str, position: int
    ) -> list[tuple[str, int, str, int]] | None: ...


def _hit(
    lifter: CoordinateLifter, chromosome: str, position: int
) -> tuple[tuple[str, int, bool] | None, str]:
    hits = lifter.convert_coordinate(chromosome, position - 1) or []
    return _unique_hit(hits)


def _unique_hit(
    hits: list[tuple[str, int, str, int]],
) -> tuple[tuple[str, int, bool] | None, str]:
    if not hits:
        return None, "unmapped"
    if len(hits) != 1:
        return None, "multiple"
    chrom, pos, strand, _score = hits[0]
    if chrom not in _CHROMS or chrom == "chrM":
        return None, "nonprimary"
    if strand not in {"+", "-"}:
        raise ValueError("Invalid chain orientation")
    return (chrom, pos + 1, strand == "-"), "mapped"


def project_sites(
    sites: Sequence[tuple[str, int]],
    forward: CoordinateLifter,
    reverse: CoordinateLifter,
    source_sequence: Callable[[str], ReferenceSequence],
    target_sequence: Callable[[str], ReferenceSequence],
    mt: MitochondrialBridge,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[SiteProjection], dict[str, int]]:
    """Audit the whole static union before any sample-dependent selection."""
    rows: list[SiteProjection] = []
    destinations: dict[tuple[str, int], int] = {}
    source_chrom = ""
    source_bases: ReferenceSequence = ""
    for chrom, pos in sites:
        if chrom != source_chrom:
            source_chrom, source_bases = chrom, source_sequence(chrom)
            if progress is not None:
                progress(chrom)
            if chrom == "chrM" and (
                mt.aligned_source.replace("-", "") != source_bases
                or mt.original_target() != target_sequence("chrM")
            ):
                raise ValueError("Mitochondrial bridge does not match the references")
        if not 1 <= pos <= len(source_bases):
            raise ValueError(f"Source site is outside its reference: {chrom}:{pos}")
        if chrom == "chrM":
            native_pos = mt.source_to_target[pos]
            hit = ("chrM", native_pos, False) if native_pos else None
            candidates = [("chrM", native_pos - 1, "+", 0)] if native_pos else []
            status = "mapped" if hit else "mt_gap"
        else:
            candidates = forward.convert_coordinate(chrom, pos - 1) or []
            hit, status = _unique_hit(candidates)
        # A filtered/nonreciprocal/multihit partner must not repair a collision.
        collision = False
        current_index = len(rows)
        for candidate_chrom, candidate_pos, _strand, _score in candidates:
            if candidate_chrom not in _CHROMS:
                continue
            key = (candidate_chrom, candidate_pos + 1)
            owner = destinations.setdefault(key, current_index)
            if owner != current_index:
                rows[owner] = rows[owner]._replace(
                    target_chrom=None,
                    target_pos=None,
                    inverted=False,
                    status="collision",
                    ref_status="ref_unknown",
                )
                collision = True
        if collision:
            hit, status = None, "collision"
        elif hit and chrom != "chrM":
            returned, _ = _hit(reverse, hit[0], hit[1])
            if returned != (chrom, pos, hit[2]):
                hit, status = None, "nonreciprocal"
        if hit is None:
            rows.append(
                SiteProjection(chrom, pos, None, None, False, status, "ref_unknown")
            )
            continue
        native_chrom, native_pos, inverted = hit
        native_bases = target_sequence(native_chrom)
        if not 1 <= native_pos <= len(native_bases):
            raise ValueError(
                f"Destination is outside its reference: {native_chrom}:{native_pos}"
            )
        source_base, native_base = source_bases[pos - 1], native_bases[native_pos - 1]
        if inverted:
            native_base = native_base.translate(_COMPLEMENT)
        ref_status = (
            ("ref_equal" if source_base == native_base else "ref_different")
            if source_base in "ACGT" and native_base in "ACGT"
            else "ref_unknown"
        )
        rows.append(
            SiteProjection(
                chrom,
                pos,
                native_chrom,
                native_pos,
                inverted,
                "mapped",
                ref_status,
            )
        )
    counts = Counter(site.status for site in rows)
    counts.update(site.ref_status for site in rows if site.status == "mapped")
    return rows, dict(counts)


def write_site_map(
    sites: Sequence[SiteProjection],
    source_lengths: Mapping[str, int],
    target_lengths: Mapping[str, int],
    outdir: Path,
    provenance: Mapping[str, object],
) -> Path:
    """Write deterministic arrays, verify them independently, then publish."""
    outdir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    part = outdir / f"sites-{token}.part.gz"
    manifest_part = outdir / f"manifest-{token}.part.json"
    checksum_part = outdir / f"manifest-checksum-{token}.part"
    source_names = [name for name in _CHROMS if name in source_lengths]
    target_names = [name for name in _CHROMS if name in target_lengths]
    target_codes = {name: index + 1 for index, name in enumerate(target_names)}
    counts = dict.fromkeys(source_names, 0)
    for site in sites:
        if site.source_chrom not in counts:
            raise ValueError(f"Unknown source chromosome: {site.source_chrom}")
        counts[site.source_chrom] += 1
    digest = hashlib.sha256()
    uncompressed_bytes = 0
    buffer = bytearray(_MAGIC)

    def integer(value: int) -> None:
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("Site-map integer exceeds uint32")
        while value >= 128:
            buffer.append((value & 127) | 128)
            value >>= 7
        buffer.append(value)

    def signed(value: int) -> None:
        integer(value * 2 if value >= 0 else -2 * value - 1)

    def native_key(index: int) -> int:
        site = sites[index]
        if site.target_chrom not in target_codes or site.target_pos is None:
            raise ValueError("Mapped site has no valid destination")
        return (target_codes[site.target_chrom] << 32) | site.target_pos

    native_order = [i for i, site in enumerate(sites) if site.status == "mapped"]
    native_order.sort(key=native_key)
    try:
        with (
            part.open("xb") as raw,
            gzip.GzipFile(
                fileobj=raw, filename="", mode="wb", mtime=0, compresslevel=9
            ) as output,
        ):

            def flush() -> None:
                nonlocal uncompressed_bytes
                digest.update(buffer)
                uncompressed_bytes += len(buffer)
                output.write(buffer)
                buffer.clear()

            integer(len(source_names))
            index = 0
            for name in source_names:
                integer(counts[name])
                previous = offset = 0
                while index < len(sites) and sites[index].source_chrom == name:
                    site = sites[index]
                    integer(site.source_pos - previous)
                    flag = _STATUSES.index(site.status)
                    flag |= 0x10 if site.inverted else 0
                    flag |= _REF_STATUSES.index(site.ref_status) << 5
                    buffer.append(flag)
                    if site.status == "mapped":
                        if (
                            site.target_chrom not in target_codes
                            or site.target_pos is None
                        ):
                            raise ValueError("Mapped site has no valid destination")
                        integer(target_codes[site.target_chrom])
                        new_offset = site.target_pos - site.source_pos
                        signed(new_offset - offset)
                        offset = new_offset
                    previous = site.source_pos
                    index += 1
                    if len(buffer) >= 65536:
                        flush()
            if index != len(sites):
                raise ValueError("Source sites are not in canonical chromosome order")
            integer(len(native_order))
            previous_index = 0
            for index in native_order:
                signed(index - previous_index)
                previous_index = index
                if len(buffer) >= 65536:
                    flush()
            flush()
        data = part.read_bytes()
        compressed_sha256 = hashlib.sha256(data).hexdigest()
        manifest: dict[str, object] = {
            "format": "wgsextract-chm13-map",
            "version": 1,
            "source_build": "GRCh37-rCRS",
            "target_build": "CHM13v2.0",
            "file": part.name,
            "compressed_bytes": len(data),
            "sha256": compressed_sha256,
            "uncompressed_bytes": uncompressed_bytes,
            "uncompressed_sha256": digest.hexdigest(),
            "source_contigs": [
                {"name": name, "length": source_lengths[name], "rows": counts[name]}
                for name in source_names
            ],
            "target_contigs": [
                {"name": name, "length": target_lengths[name]} for name in target_names
            ],
            "provenance": dict(provenance),
        }
        # LF on every platform: the runtime checks these bytes against the
        # sidecar, and git stores them as they are (.gitattributes -text).
        manifest_part.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        load_site_map(manifest_part)
        final = outdir / f"chm13-sites-v1-{compressed_sha256[:16]}.bin.gz"
        os.replace(part, final)
        manifest["file"] = final.name
        manifest_part.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        checksum_part.write_text(
            file_sha256(manifest_part) + "\n", encoding="ascii", newline="\n"
        )
        os.replace(manifest_part, outdir / "manifest.json")
        os.replace(checksum_part, outdir / "manifest.json.sha256")
        return outdir / "manifest.json"
    finally:
        part.unlink(missing_ok=True)
        manifest_part.unlink(missing_ok=True)
        checksum_part.unlink(missing_ok=True)


def audit_grch38(
    sites: Sequence[SiteProjection], chains: Path, outdir: Path
) -> dict[str, object]:
    forward = LiftOver(str(chains / "hg19ToHg38.over.chain.gz"))
    reverse = LiftOver(str(chains / "hg38ToHg19.over.chain.gz"))
    native_forward = LiftOver(str(chains / "grch38-chm13v2.chain"))
    native_reverse = LiftOver(str(chains / "chm13v2-grch38.chain"))
    destinations: dict[tuple[str, int], int] = {}
    collisions: set[int] = set()
    candidates: list[tuple[str, int, bool] | None] = []
    counts: Counter[str] = Counter()
    for index, site in enumerate(sites):
        candidates.append(None)
        if site.source_chrom == "chrM":
            counts["MT_validated_separately"] += 1
            continue
        counts["nuclear_sites"] += 1
        hits = forward.convert_coordinate(site.source_chrom, site.source_pos - 1) or []
        for chrom, pos0, _strand, _score in hits:
            if chrom in _CHROMS:
                owner = destinations.setdefault((chrom, pos0 + 1), index)
                if owner != index:
                    collisions.update((owner, index))
        hit, status = _unique_hit(hits)
        if hit is None:
            counts[f"GRCh38_{status}"] += 1
            continue
        back, _ = _hit(reverse, hit[0], hit[1])
        if back != (site.source_chrom, site.source_pos, hit[2]):
            counts["GRCh38_nonreciprocal"] += 1
            continue
        native_hit, native_status = _hit(native_forward, hit[0], hit[1])
        if native_hit is None:
            counts[f"via38_{native_status}"] += 1
            continue
        native_back, _ = _hit(native_reverse, native_hit[0], native_hit[1])
        if native_back != (hit[0], hit[1], native_hit[2]):
            counts["via38_nonreciprocal"] += 1
            continue
        if site.status != "mapped":
            counts["via38_reachable_but_direct_rejected"] += 1
            continue
        if (native_hit[0], native_hit[1]) != (site.target_chrom, site.target_pos):
            counts["bridge_coordinate_disagreement"] += 1
            continue
        if (hit[2] != native_hit[2]) != site.inverted:
            counts["bridge_strand_disagreement"] += 1
            continue
        candidates[index] = hit
    counts["GRCh38_static_collision_sites"] = len(collisions)
    path = outdir / "validation-grch38-reciprocal.tsv.gz"
    with (
        path.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed,
    ):
        compressed.write(
            b"#chrom37\tpos37\tchrom38\tpos38\tinverted38\tchrom13\tpos13\tinverted13\n"
        )
        for index, (site, hit) in enumerate(zip(sites, candidates, strict=True)):
            if hit is None or index in collisions:
                continue
            counts["eligible_sites"] += 1
            compressed.write(
                (
                    f"{site.source_chrom}\t{site.source_pos}\t{hit[0]}\t{hit[1]}\t"
                    f"{int(hit[2])}\t{site.target_chrom}\t{site.target_pos}\t"
                    f"{int(site.inverted)}\n"
                ).encode()
            )
    return {
        "counts": dict(counts),
        "eligibility_file": path.name,
        "eligibility_sha256": file_sha256(path),
        "shipped": False,
        "policy": "direct CHM13 authoritative; via38 is validation cross-check only",
        "chains": {
            filename: file_sha256(chains / filename)
            for filename in (
                "hg19ToHg38.over.chain.gz",
                "hg38ToHg19.over.chain.gz",
                "grch38-chm13v2.chain",
                "chm13v2-grch38.chain",
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", type=Path, action="append", required=True)
    parser.add_argument("--chains", type=Path, required=True)
    parser.add_argument("--source-reference", type=Path, required=True)
    parser.add_argument("--source-dictionary", type=Path, required=True)
    parser.add_argument("--native-reference", type=Path, required=True)
    parser.add_argument("--native-dictionary", type=Path, required=True)
    parser.add_argument("--par-bed", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args()
    source = ReferenceSequences(args.source_reference, args.source_dictionary)
    native = ReferenceSequences(args.native_reference, args.native_dictionary)
    if source.lengths["chr1"] != 249250621 or native.lengths["chr1"] != 248387328:
        raise ValueError("Expected GRCh37 source and CHM13v2 native references")
    if (
        source.md5s["chrM"] != "c68f52674c9fb33aef52dcf399755519"
        or native.md5s["chrM"] != "ec493a132ac4823aa696e37109f64972"
    ):
        raise ValueError("This asset requires the pinned rCRS/original CHM13 MT pair")
    args.outdir.mkdir(parents=True, exist_ok=True)
    rcrs = source.sequence("chrM")
    native_mt = native.sequence("chrM")
    anchor = rcrs[:40]
    circular = native_mt + native_mt[: len(anchor) - 1]
    origins = [
        position
        for position in range(len(native_mt))
        if circular.startswith(anchor, position)
    ]
    if len(origins) != 1 or set(anchor) - set("ACGT"):
        raise ValueError("Cannot establish a unique pinned mitochondrial origin")
    mt = align_mitochondria(rcrs, native_mt, target_rotation=origins[0])
    mt_provenance = mitochondrial_artifact(mt, rcrs, native_mt, args.outdir)
    par: list[PARInterval] = []
    y = native.sequence("chrY")
    for line in args.par_bed.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        chrom, start, end = line.split()[:3]
        start0, end0 = int(start), int(end)
        if chrom not in {"chrX", "chrY"} or not (
            0 <= start0 < end0 <= native.lengths[chrom]
        ):
            raise ValueError("Invalid CHM13 PAR definition")
        par.append({"chrom": chrom, "start0": start0, "end0": end0})
    y_intervals = [
        y[row["start0"] : row["end0"]] for row in par if row["chrom"] == "chrY"
    ]
    if (
        len(par) != 4
        or len(y_intervals) != 2
        or any(set(interval) != {"N"} for interval in y_intervals)
    ):
        raise ValueError(
            "Phase-A asset requires the original-MT, masked-Y analysis set"
        )
    lift_chains = {
        build: args.chains / source.name for build, source in _CHAINS_TO_GRCH37.items()
    }
    for build, chain in lift_chains.items():
        if file_sha256(chain) != _CHAINS_TO_GRCH37[build].sha256:
            raise ValueError(f"{chain.name} is not the pinned UCSC chain")
    markers, marker_provenance = collect_markers(
        [str(path) for path in args.templates],
        lift_chains,
        source.lengths,
    )
    rank = {chrom: index for index, chrom in enumerate(_CHROMS)}
    ordered = sorted(markers, key=lambda site: (rank[site[0]], site[1]))
    print(f"Projecting {len(ordered):,} complete-union sites", flush=True)
    forward_path = args.chains / "hg19-chm13v2.chain"
    reverse_path = args.chains / "chm13v2-hg19.chain"
    rows, counts = project_sites(
        ordered,
        LiftOver(str(forward_path)),
        LiftOver(str(reverse_path)),
        source.indexed_sequence,
        native.indexed_sequence,
        mt,
        progress=lambda chrom: print(f"Projecting {chrom}", flush=True),
    )
    print("Auditing the independent GRCh38 coordinate route", flush=True)
    validation = audit_grch38(rows, args.chains, args.outdir)
    provenance: dict[str, object] = {
        "generator": "scripts/build_microarray_chm13_map.py",
        "generator_sha256": file_sha256(Path(__file__)),
        "bridge_sha256": file_sha256(Path(__file__).with_name("chm13_mito_bridge.py")),
        "markers": marker_provenance,
        "mapping_counts": counts,
        "chains": {
            path.name: {
                "sha256": file_sha256(path),
                "url": (
                    "https://s3-us-west-2.amazonaws.com/human-pangenomics/"
                    "T2T/CHM13/assemblies/chain/v1_nflo/" + path.name
                ),
            }
            for path in (forward_path, reverse_path)
        },
        "ncbi36_chain_sha256": file_sha256(lift_chains["NCBI36"]),
        "grch38_chain_sha256": file_sha256(lift_chains["GRCh38"]),
        "source_reference": {
            "upstream_url": (
                "https://1000genomes.s3.amazonaws.com/technical/reference/"
                "phase2_reference_assembly_sequence/hs37d5.fa.gz"
            ),
            "local_bgzip_sha256": file_sha256(args.source_reference),
            "contigs": source.signature(),
        },
        "native_reference": {
            "upstream_url": (
                "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/"
                "CHM13/assemblies/analysis_set/chm13v2.0_maskedY.fa.gz"
            ),
            "sha256": file_sha256(args.native_reference),
            "fai_sha256": file_sha256(Path(str(args.native_reference) + ".fai")),
            "gzi_sha256": file_sha256(Path(str(args.native_reference) + ".gzi")),
            "contigs": native.signature(),
            "chrY_PAR_masking": "masked",
            "par_intervals": par,
            "par_bed_sha256": file_sha256(args.par_bed),
        },
        "mitochondria": mt_provenance,
        "validation": validation,
    }
    manifest = write_site_map(
        rows, source.lengths, native.lengths, args.outdir, provenance
    )
    print(manifest.read_text(), flush=True)


if __name__ == "__main__":
    main()
