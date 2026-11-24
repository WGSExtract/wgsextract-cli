"""GRCh37 yBrowse sites, lifted locally from the installed GRCh38 snapshot.

YBrowse's own ``snps_hg19.vcf.gz`` (2017) is not used: its positions are the
GRCh38 ones under a GRCh37 header (#1186). Instead each GRCh38 allele is placed
with Ensembl's GRCh38->GRCh37 chain (or a chain the user supplies) and checked
against a GRCh37 FASTA. A site is kept only when the chain places it once, on
Y, no other GRCh38 site lands on the same position, and the GRCh37 base is its
REF (or its ALT: the alleles are swapped, AA kept). Every other site is
dropped and counted; nothing is guessed, and nothing derived is redistributed.
"""

import gzip
import hashlib
import json
import logging
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO, cast

from wgsextract_cli.core.network_policy import DownloadRequired
from wgsextract_cli.core.paths import append_suffix
from wgsextract_cli.core.ref_library.assets import (
    read_fai_lengths,
    reference_fasta_candidates,
    reference_genomes_dir,
)
from wgsextract_cli.core.utils import WGSExtractError, run_command, run_pipeline
from wgsextract_cli.core.y_liftover import YLiftOver, YPlacement, complement
from wgsextract_cli.core.ybrowse import (
    COPYRIGHT,
    RESOURCE_FILES,
    SOURCE_URL,
    Group,
    compress_resource,
    current_version,
    file_sha256,
    installed_snapshot,
    publish,
    require_allowed_paths,
    resource_hashes,
    resource_paths,
    sites_header,
    write_group,
)
from wgsextract_cli.core.ybrowse import Y_LENGTH as GRCH38_Y_LENGTH

Y_LENGTH = 59_373_566
CHR1_LENGTH = 249_250_621
CHAIN_NAME = "GRCh38_to_GRCh37.chain.gz"
CHAIN_STAGE = f".{CHAIN_NAME}.download"  # its download's staging file
CHAIN_URL = "https://ftp.ensembl.org/pub/assembly_mapping/homo_sapiens/GRCh38_to_GRCh37.chain.gz"
CHAIN_SHA256 = "6bee5bfdd6defc0fb2bd388ef51819c32bbfae1d3e7a3345aa5ebbdc3dd492b1"
CHAIN_TERMS = (
    "Ensembl imposes no restrictions on access to, or use of, its data "
    "(https://www.ensembl.org/info/about/legal/disclaimer.html)"
)
SCOPE = "GRCh37_lifted_from_GRCh38_not_YBrowse_published_GRCh37"
_BASES = frozenset("ACGT")
# The lift's outcome for each GRCh38 allele, as the manifest and VCF header
# report it.
COUNT_KEYS = (
    "grch38_alleles",
    "unlifted",
    "multi_hit_or_off_Y",
    "reverse_strand",
    "ref_alt_inverted",
    "neither_allele_matches",
    "grch37_reference_N",
    "many_to_one",
    "conflicting_ref",
    "grch37_alleles",
)


_PINNED: dict[str, object] = {
    "name": CHAIN_NAME,
    "sha256": CHAIN_SHA256,
    "kind": "pinned-ensembl",
    "url": CHAIN_URL,
    "terms": CHAIN_TERMS,
}


def library_chain(library: Path) -> Path:
    return library / "ybrowse" / CHAIN_NAME


def chain_staging_paths(library: Path) -> Iterator[Path]:
    stage = library_chain(library).with_name(CHAIN_STAGE)
    yield stage
    for suffix in (".partial", ".partial.source", ".partial.segments"):
        yield append_suffix(stage, suffix)


def lift_chain(
    library: Path, chain: Path | None, download: bool
) -> tuple[Path, dict[str, object]]:
    """The chain to lift with and its provenance: ``chain`` when given, else
    Ensembl's pinned chain in the library, fetched only when ``download``."""
    if chain is not None:
        require_allowed_paths((chain,))
        if not chain.is_file():
            raise WGSExtractError(f"Chain not found: {chain}")
        sha = file_sha256(chain)
        if sha == CHAIN_SHA256:
            return chain, {**_PINNED, "name": chain.name}
        return chain, {"name": chain.name, "sha256": sha, "kind": "user-supplied"}
    path = library_chain(library)
    require_allowed_paths((path,))
    if not path.is_file():
        if not download:
            raise DownloadRequired(
                f"Missing public reference data: Ensembl's GRCh38->GRCh37 chain "
                f"({CHAIN_NAME}, 730 KB from ftp.ensembl.org). Rerun 'wgsextract "
                "ref ybrowse --install --grch37 --download' to confirm the "
                "download (only the public file is requested, never sample data), "
                "or give a chain with --chain PATH."
            )
        from wgsextract_cli.core.ref_library.downloads import download_file

        require_allowed_paths(chain_staging_paths(library))
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(CHAIN_STAGE)
        if not download_file(CHAIN_URL, str(staged), expected_sha256=CHAIN_SHA256):
            raise WGSExtractError(
                f"Could not download {CHAIN_NAME} from {CHAIN_URL} with its "
                f"pinned SHA-256 {CHAIN_SHA256}."
            )
        staged.replace(path)
    elif file_sha256(path) != CHAIN_SHA256:
        raise WGSExtractError(
            f"{path} is not Ensembl's pinned {CHAIN_NAME} (SHA-256 {CHAIN_SHA256}). "
            "Remove it and rerun with --download, or give a chain with --chain PATH."
        )
    return path, dict(_PINNED)


def _y_contig(fasta: Path) -> str | None:
    lengths = read_fai_lengths(fasta)
    names = [name for name in ("chrY", "Y") if lengths.get(name) == Y_LENGTH]
    chr1 = [lengths[name] for name in ("1", "chr1") if name in lengths]
    if len(names) != 1 or any(length != CHR1_LENGTH for length in chr1):
        return None
    return names[0]


def is_grch37(fasta: Path) -> bool:
    """Whether ``fasta`` is indexed with GRCh37's chrY length, and chr1's when it
    has chr1 (a Y-only GRCh37 FASTA qualifies)."""
    return _y_contig(fasta) is not None


def fasta_paths(fasta: Path) -> Iterator[Path]:
    yield fasta
    yield append_suffix(fasta, ".fai")
    yield append_suffix(fasta, ".gzi")


def _fasta_candidates(library: Path) -> Iterator[Path]:
    for directory in dict.fromkeys((library, Path(reference_genomes_dir(library)))):
        require_allowed_paths((directory,))
        yield from map(Path, reference_fasta_candidates(directory))


def guarded_fasta_paths(library: Path, fasta: Path | None) -> Iterator[Path]:
    if fasta is not None:
        yield from fasta_paths(fasta)
        if is_grch37(fasta):
            return
    for directory in dict.fromkeys((library, Path(reference_genomes_dir(library)))):
        yield directory
        for candidate in reference_fasta_candidates(directory):
            yield from fasta_paths(Path(candidate))


def grch37_fasta(library: Path, fasta: Path | None) -> Path:
    """``fasta`` when it is an indexed GRCh37 FASTA, else the first one in the
    library or its genomes folder (warning that ``fasta`` was passed over)."""
    if fasta is not None:
        require_allowed_paths(fasta_paths(fasta))
        if is_grch37(fasta):
            return fasta
    found = []
    for candidate in _fasta_candidates(library):
        require_allowed_paths(fasta_paths(candidate))
        if is_grch37(candidate):
            found.append(candidate)
    wrong = (
        f"{fasta} is not an indexed GRCh37 FASTA (chrY/Y length {Y_LENGTH:,}, "
        f"chr1 {CHR1_LENGTH:,})"
        if fasta is not None
        else ""
    )
    if found:
        if wrong:
            logging.warning(f"{wrong}; checking the lifted alleles against {found[0]}.")
        return found[0]
    raise WGSExtractError(
        (f"{wrong}, and the library has no other. " if wrong else "")
        + "--grch37 checks the lifted alleles against an indexed GRCh37 FASTA "
        f"(chrY/Y length {Y_LENGTH:,}): give one with --ref FASTA, indexed with "
        "wgsextract ref index."
    )


def _y_sequence(fasta: Path, contig: str, work: Path) -> str:
    extracted = work / "Y.fa"
    run_command(["samtools", "faidx", "-o", str(extracted), str(fasta), contig])
    with extracted.open(encoding="ascii") as stream:
        stream.readline()
        sequence = "".join(line.strip() for line in stream).upper()
    extracted.unlink()
    if len(sequence) != Y_LENGTH:
        raise WGSExtractError(f"{fasta} {contig} is not {Y_LENGTH:,} bases long.")
    return sequence


def _complement_info(info: str) -> str:
    return ";".join(
        "AA=" + ",".join(complement(a) for a in item[3:].split(","))
        if item.startswith("AA=")
        else item
        for item in info.split(";")
    )


def _lift(sites38: Path, lifter: YLiftOver, sequence: str, out: TextIO) -> Counter[str]:
    """Write each placed allele to ``out`` (unsorted, GRCh37 coordinates), its
    GRCh38 position as INFO/SRC38 and its reference check as INFO/LIFT, so a
    position another GRCh38 site lands on is dropped whatever its check."""
    counts: Counter[str] = Counter()
    with gzip.open(sites38, "rt", encoding="utf-8") as source:
        for line in source:
            if line.startswith("#"):
                continue
            _, pos, names, ref, alt, _, _, info = line.rstrip("\n").split("\t")
            counts["grch38_alleles"] += 1
            placed = lifter.place(int(pos))
            if not isinstance(placed, YPlacement):
                counts[placed] += 1
                # Still claim each Y position it might land on, so a site
                # placed there uniquely is dropped as ambiguous too.
                for candidate in lifter.candidates(int(pos)):
                    out.write(
                        f"chrY\t{candidate}\t.\t{ref}\t{alt}\t.\t.\t"
                        f"SRC38={pos};LIFT=multi_hit_candidate\n"
                    )
                continue
            if placed.reverse:
                counts["reverse_strand"] += 1
                ref, alt = complement(ref), complement(alt)
                info = _complement_info(info)
            base = sequence[placed.position - 1] if placed.position <= Y_LENGTH else "N"
            if base not in _BASES:
                check = "grch37_reference_N"
            elif base == alt:
                check, ref, alt = "ref_alt_inverted", alt, ref
            else:
                check = "ok" if base == ref else "neither_allele_matches"
            info = f"SRC38={pos};LIFT={check}" + ("" if info == "." else f";{info}")
            out.write(f"chrY\t{placed.position}\t{names}\t{ref}\t{alt}\t.\t.\t{info}\n")
    return counts


def _info(info: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in info.split(";") if "=" in item)


def _group(records: list[list[str]]) -> Group:
    group: Group = {}
    for _, _, names, ref, alt, _, _, info in records:
        fields = _info(info)
        values = group.setdefault((ref, alt), (set(), set(), set(), set()))
        values[0].update(n for n in names.split(";") if n != ".")
        for index, tag in enumerate(("HG", "ISOGG", "AA"), start=1):
            values[index].update(
                v for v in fields.get(tag, "").split(",") if v not in ("", ".")
            )
    return group


def _write_sites(sorted_vcf: Path, directory: Path, counts: Counter[str]) -> None:
    """Group the sorted lifted alleles by GRCh37 position into the resource,
    dropping positions two GRCh38 sites land on or whose REFs disagree."""
    with (
        sorted_vcf.open(encoding="utf-8") as source,
        (directory / "sites.vcf").open("w", encoding="utf-8", newline="\n") as sites,
        (directory / "targets.tsv").open(
            "w", encoding="utf-8", newline="\n"
        ) as targets,
    ):
        sites.write(sites_header(Y_LENGTH, f"##ybrowse_scope={SCOPE}\n"))

        def flush(pos: int, records: list[list[str]]) -> None:
            fields = [_info(r[7]) for r in records]
            if len({f["SRC38"] for f in fields}) > 1:
                counts["many_to_one"] += sum(
                    f["LIFT"] != "multi_hit_candidate" for f in fields
                )
                return
            kept = []
            for record, info in zip(records, fields, strict=True):
                check = info["LIFT"]
                if check == "multi_hit_candidate":
                    continue  # counted as multi_hit_or_off_Y when lifted
                if check not in ("ok", "ref_alt_inverted"):
                    counts[check] += 1
                    continue
                if check != "ok":
                    counts[check] += 1
                kept.append(record)
            if len({r[3] for r in kept}) > 1:
                counts["conflicting_ref"] += len(kept)
            elif kept:
                group = _group(kept)
                counts["grch37_alleles"] += write_group(pos, group, sites, targets)

        position = 0
        records: list[list[str]] = []
        for line in source:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if int(cols[1]) != position:
                if records:
                    flush(position, records)
                position, records = int(cols[1]), []
            records.append(cols)
        if records:
            flush(position, records)


def derive(
    library: Path, fasta: Path, chain: Path, chain_provenance: dict[str, object]
) -> Path:
    """Lift the installed GRCh38 resource into ``ybrowse/hg19/<snapshot>/``."""
    source_dir, source = installed_snapshot(library)
    contig = _y_contig(fasta)
    if contig is None:
        raise WGSExtractError(f"{fasta} is not an indexed GRCh37 FASTA.")
    lifter = YLiftOver(chain, (GRCH38_Y_LENGTH, Y_LENGTH))
    root = library / "ybrowse" / "hg19"
    require_allowed_paths((root, root / "current.json"))
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install_", dir=root) as temporary:
        stage = Path(temporary)
        sequence = _y_sequence(fasta, contig, stage)
        sequence_sha256 = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        lifted = stage / "lifted.vcf"
        with lifted.open("w", encoding="utf-8", newline="\n") as out:
            out.write(
                sites_header(
                    Y_LENGTH,
                    '##INFO=<ID=SRC38,Number=1,Type=Integer,Description="GRCh38'
                    ' position">\n'
                    '##INFO=<ID=LIFT,Number=1,Type=String,Description="GRCh37'
                    ' reference check">\n',
                )
            )
            counts = _lift(source_dir / "sites.vcf.gz", lifter, sequence, out)
        del sequence
        ordered = stage / "sorted.vcf"
        run_pipeline(
            [
                [
                    "bcftools",
                    "sort",
                    "-T",
                    str(stage / "sort"),
                    "-Ov",
                    "-o",
                    str(ordered),
                    str(lifted),
                ],
            ],  # fmt: skip
            description="Sort the lifted yBrowse sites",
            stderr_dir=stage,
        )
        lifted.unlink()
        resource = stage / "resource"
        resource.mkdir()
        _write_sites(ordered, resource, counts)
        ordered.unlink()
        if not counts["grch37_alleles"]:
            raise WGSExtractError(
                "No yBrowse allele placed on GRCh37: check the chain and FASTA."
            )
        compress_resource(resource)
        # Each snapshot, chain and reference sequence derives its own folder, so
        # re-deriving never replaces the files an extraction may be reading.
        version = hashlib.sha256(
            f"{source['source_sha256']}:{chain_provenance['sha256']}:"
            f"{sequence_sha256}".encode()
        ).hexdigest()
        manifest = {
            "schema": 1,
            "assembly": "GRCh37",
            "chrY_length": Y_LENGTH,
            "copyright": COPYRIGHT,
            "attribution": str(source["attribution"]),
            "source_url": SOURCE_URL,
            "source_sha256": source["source_sha256"],
            "scope": SCOPE,
            "liftover": chain_provenance,
            "reference": {
                "fasta": fasta.name,
                "contig": contig,
                "sequence_sha256": sequence_sha256,
            },
            "counts": {key: counts[key] for key in COUNT_KEYS},
            "snp_alleles": counts["grch37_alleles"],
            "sites_only": True,
            "files": resource_hashes(resource),
        }
        (resource / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        return publish(root, stage, resource, version, keep=_valid)


def _validate(directory: Path) -> dict[str, object]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["schema"] != 1
        or manifest["assembly"] != "GRCh37"
        or manifest["chrY_length"] != Y_LENGTH
        or manifest["copyright"] != COPYRIGHT
        or manifest["sites_only"] is not True
        or not isinstance(manifest["source_sha256"], str)
        or not isinstance(manifest["liftover"]["sha256"], str)
    ):
        raise ValueError("unsupported manifest")
    if any(
        file_sha256(directory / name) != manifest["files"][name]
        for name in RESOURCE_FILES
    ):
        raise ValueError("resource checksum mismatch")
    return cast(dict[str, object], manifest)


def _valid(directory: Path) -> bool:
    try:
        _validate(directory)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def installed_grch37(library: Path) -> tuple[Path, dict[str, object]]:
    """The derived GRCh37 resource, verified and still matching the installed
    GRCh38 snapshot it was lifted from."""
    install = (
        "Derive it with wgsextract ref ybrowse --install --grch37 --download "
        f"(fetches Ensembl's pinned GRCh38->GRCh37 chain, {CHAIN_NAME}), or give "
        "a chain with --chain PATH."
    )
    version = current_version(library / "ybrowse" / "hg19")
    if version is None:
        raise WGSExtractError(f"No GRCh37 yBrowse resource. {install}")
    directory = library / "ybrowse" / "hg19" / version
    require_allowed_paths(resource_paths(directory))
    try:
        manifest = _validate(directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WGSExtractError(
            f"Invalid GRCh37 yBrowse resource in {directory}: {exc}. {install}"
        ) from exc
    if current_version(library / "ybrowse" / "hg38") != manifest["source_sha256"]:
        raise WGSExtractError(
            "The GRCh37 yBrowse resource was lifted from a GRCh38 snapshot that is "
            f"no longer installed. {install}"
        )
    return directory, manifest
