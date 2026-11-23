import contextlib
import hashlib
import itertools
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from os import fspath
from pathlib import Path

from wgsextract_cli.core.alignment.metadata import get_bam_header as get_bam_header
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.paths import PathInput, append_suffix, expand_path
from wgsextract_cli.core.ref_library.resolver import (
    ReferenceLibrary,
    header_contig_lengths,
)
from wgsextract_cli.core.temp_directory import input_scratch_directory

from .utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
    run_command,
)

VCF_SUFFIXES = (".vcf", ".vcf.gz", ".vcf.bgz")


def is_vcf_path(path: PathInput, *, bcf: bool = True) -> bool:
    """Whether ``path`` is named as a plain or bgzipped VCF, or (unless
    ``bcf`` is False) a BCF, ignoring case."""
    suffixes = (*VCF_SUFFIXES, ".bcf") if bcf else VCF_SUFFIXES
    return fspath(path).lower().endswith(suffixes)


def resolve_reference(
    ref_path: PathInput | None,
    md5_sig: str | None,
    input_path: PathInput | None = None,
) -> str | None:
    """Find specific .fa.gz from directory or direct path."""
    lib = ReferenceLibrary(
        ref_path, md5_sig, skip_full_search=True, input_path=input_path
    )
    return lib.fasta or (fspath(ref_path) if ref_path is not None else None)


def verify_paths_exist(paths_dict: Mapping[str, PathInput | None]) -> None:
    """Raise WGSExtractError unless every labelled path exists as a file."""
    problems: list[str] = []
    for label, path in paths_dict.items():
        if not path:
            problems.append(
                LOG_MESSAGES["file_not_found"].format(label=label, path=path)
            )
            continue

        normalized_path = expand_path(path)
        if not Path(normalized_path).exists():
            problems.append(
                LOG_MESSAGES["file_not_found"].format(label=label, path=normalized_path)
            )
        elif Path(normalized_path).is_dir():
            problems.append(
                f"{label} path is a directory, but a file is expected: {normalized_path}"
            )
    if problems:
        raise WGSExtractError("\n".join(problems))


def _short_index_sibling(vcf: Path) -> Path | None:
    """Another bgzipped VCF (``x.vcf.gz`` beside ``x.vcf.bgz``, in any case)
    that HTSlib would pair with ``vcf``'s short index name ``x.vcf.csi``."""
    for ext in ("gz", "bgz"):
        for letters in itertools.product(*((c, c.upper()) for c in ext)):
            candidate = vcf.with_suffix("." + "".join(letters))
            if candidate.is_file() and not candidate.samefile(vcf):
                return candidate
    return None


def ensure_vcf_indexed(vcf_path: PathInput) -> None:
    """Create an index when missing, or refresh all sidecars if any look stale."""
    vcf = Path(vcf_path)
    if vcf.suffix.lower() not in {".gz", ".bgz"}:
        # We can't index plain VCF easily with tabix
        return

    vcf_mtime = vcf.stat().st_mtime_ns
    indexes: list[tuple[int, Path]] = []
    for ext in (".tbi", ".csi"):
        # HTSlib searches for both sample.vcf.gz.csi and sample.vcf.csi (and
        # likewise for TBI). Inspect all names readers can select.
        for index in (append_suffix(vcf, ext), vcf.with_suffix(ext)):
            try:
                mtime = index.stat().st_mtime_ns
            except FileNotFoundError:
                continue
            if index == vcf.with_suffix(ext) and (sibling := _short_index_sibling(vcf)):
                raise WGSExtractError(
                    f"{vcf} and {sibling} share the index name {index}. "
                    "Copy the intended VCF without its indexes to a separate "
                    "writable directory and retry."
                )
            indexes.append((mtime, index))
    # Age can trigger a refresh, but doesn't establish whether an index matches.
    if indexes and all(mtime >= vcf_mtime for mtime, _ in indexes):
        return

    # Publish the oldest index last so a partial publication doesn't clear the
    # refresh trigger. Build every required format before publishing any.
    destinations = [index for _, index in sorted(indexes, reverse=True)] or [
        append_suffix(vcf, ".tbi")
    ]
    from wgsextract_cli.core.dependencies import get_tool_path

    tabix = get_tool_path("tabix")
    if tabix is None:
        raise MissingDependencyError("tabix dependency is required to index VCF files.")
    logging.info(LOG_MESSAGES["vcf_indexing"].format(path=vcf_path))
    try:
        with tempfile.TemporaryDirectory(prefix=".wgse-", dir=vcf.parent) as stage:
            staged_vcf = Path(stage, "input.vcf.gz")
            generation = _stage_vcf_for_indexing(vcf.resolve(strict=True), staged_vcf)
            tbi = append_suffix(staged_vcf, ".tbi")
            csi = append_suffix(staged_vcf, ".csi")
            if any(index.suffix == ".tbi" for index in destinations):
                try:
                    run_command(
                        [tabix, "-f", "-p", "vcf", staged_vcf], capture_output=True
                    )
                except subprocess.CalledProcessError as exc:
                    if "cannot be stored in a tbi index" not in (exc.stderr or ""):
                        raise
                    tbi.unlink(missing_ok=True)
            need_csi = not tbi.exists() or any(
                index.suffix == ".csi" for index in destinations
            )
            if need_csi:
                run_command([tabix, "-f", "-C", "-p", "vcf", staged_vcf])
            if _vcf_index_generation(vcf) != generation:
                raise WGSExtractError(f"VCF changed while indexing {vcf}; retry.")
            published: dict[Path, os.stat_result] = {}
            if need_csi and all(index.suffix != ".csi" for index in destinations):
                destinations.insert(0, append_suffix(vcf, ".csi"))
            try:
                for index in destinations:
                    if index.suffix == ".tbi" and not tbi.exists():
                        index.unlink(missing_ok=True)
                    else:
                        published[index] = _publish_vcf_index(
                            csi if index.suffix == ".csi" else tbi, index, vcf
                        )
            finally:
                _check_published_indexes(vcf, generation, published)
    except MissingDependencyError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise WGSExtractError(
            f"Could not index VCF {vcf}: {exc}. "
            "Check that the VCF is coordinate-sorted and BGZF-compressed; "
            "if the index cannot be written, copy the VCF without its indexes "
            "to a writable directory and retry."
        ) from exc


def _vcf_index_generation(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _stage_vcf_for_indexing(
    source: Path, staged: Path
) -> tuple[int, int, int, int, int]:
    # tabix writes next to its input. Avoid copying whole genomes when links
    # are available; a Windows account may not be allowed to create symlinks.
    try:
        staged.symlink_to(source)
    except OSError:
        try:
            os.link(source, staged)
        except OSError:
            generation = _vcf_index_generation(source)
            shutil.copyfile(source, staged)
            return generation
    return _vcf_index_generation(staged)


def _publish_vcf_index(source: Path, destination: Path, vcf: Path) -> os.stat_result:
    # Keep the staged index available for the other name of the same format.
    duplicate = source.with_name("publish")
    shutil.copy2(source, duplicate)
    try:
        previous_mode = destination.stat().st_mode
    except FileNotFoundError:
        # A new index shouldn't be readable by users who cannot read the VCF.
        duplicate.chmod(stat.S_IMODE(duplicate.stat().st_mode) & vcf.stat().st_mode)
    else:
        duplicate.chmod(stat.S_IMODE(previous_mode))
    identity = duplicate.stat()
    duplicate.replace(destination)
    return identity


def _check_published_indexes(
    vcf: Path,
    generation: tuple[int, int, int, int, int],
    published: Mapping[Path, os.stat_result],
) -> None:
    with contextlib.suppress(FileNotFoundError):
        if _vcf_index_generation(vcf) == generation:
            return
    # A replacement can retain an older mtime, so the next run must not
    # treat any index we just published as current.
    for index, identity in published.items():
        with contextlib.suppress(FileNotFoundError):
            if os.path.samestat(index.stat(), identity):
                index.unlink()
    raise WGSExtractError(f"VCF changed while indexing {vcf}; retry.")


def publish_indexed_vcf(
    staged: Path, final: Path, verify: Callable[[Path], None] | None = None
) -> Path:
    """Index ``staged``, run ``verify`` on it, and publish it with its index as
    ``final``, retiring indexes a reader could pick up instead; returns the
    published index."""
    from wgsextract_cli.api._runtime import checkpoint
    from wgsextract_cli.core.atomic_output import publish_output_set

    # Let the indexer see restrictions the publisher will preserve.
    if final.is_file():
        staged.chmod(staged.stat().st_mode & final.stat().st_mode & 0o777)
    ensure_vcf_indexed(staged)
    if verify is not None:
        verify(staged)
    outputs = {}
    obsolete = []
    sibling = final.with_suffix(".gz" if final.suffix == ".bgz" else ".bgz")
    for ext in (".tbi", ".csi"):
        index = Path(f"{staged}{ext}")
        destination = Path(f"{final}{ext}")
        if index.is_file():
            outputs[index] = destination
        else:
            obsolete.append(destination)
        alternate = final.with_suffix(ext)
        if not sibling.is_file():
            obsolete.append(alternate)
        elif alternate.exists() or alternate.is_symlink():
            raise WGSExtractError(
                f"Cannot replace {final}: {sibling.name} shares {alternate}."
            )
    if not outputs:
        raise WGSExtractError(f"Indexing did not produce an index for {final}.")
    published = next(iter(outputs.values()))
    outputs[staged] = final
    publish_output_set(outputs, retire=obsolete, checkpoint=checkpoint)
    return published


def ensure_vcf_prepared(
    vcf_path: PathInput | None, workdir: PathInput | None = None
) -> str:
    """Ensure VCF is bgzipped and indexed, returns path to compressed file.
    A bgzipped VCF needs indexes beside it; a plain one is
    bgzipped into the run's temporary work (see input_scratch_directory)."""
    if not vcf_path:
        raise UsageError("VCF input path is required.")
    vcf_path = fspath(vcf_path)

    # If it's a BAM or CRAM, don't try to prepare it as a VCF
    if vcf_path.lower().endswith((".bam", ".cram")):
        return vcf_path

    if vcf_path.lower().endswith((".gz", ".bgz")):
        ensure_vcf_indexed(vcf_path)
        return vcf_path

    # Need to bgzip: a working copy, never written beside the user's input.

    source = Path(vcf_path).resolve()
    key = hashlib.sha256(str(source).encode("utf-8", "surrogateescape")).hexdigest()
    copies = Path(input_scratch_directory(workdir), f"vcf-{key[:16]}")
    copies.mkdir(exist_ok=True)
    gz_path = copies / f"{Path(vcf_path).name}.gz"
    if gz_path.exists() and gz_path.stat().st_mtime > Path(vcf_path).stat().st_mtime:
        ensure_vcf_indexed(gz_path)
        return str(gz_path)

    from wgsextract_cli.core.dependencies import get_tool_path

    bgzip = get_tool_path("bgzip")
    if bgzip is None:
        raise MissingDependencyError(
            "bgzip dependency is required to compress VCF files."
        )
    logging.info(f"Compressing VCF: {vcf_path}")
    # Write to a temp name so an interrupted run never leaves a truncated .gz
    # that looks newer than the source and gets reused next time.
    tmp_path = append_suffix(gz_path, ".tmp")
    try:
        with tmp_path.open("wb") as f_out:
            run_command([bgzip, "-c", vcf_path], stdout=f_out)
        tmp_path.replace(gz_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    ensure_vcf_indexed(gz_path)
    return str(gz_path)


def get_vcf_samples(vcf_path: PathInput) -> list[str]:
    """Retrieve sample names from VCF header."""
    try:
        res = run_command(["bcftools", "query", "-l", vcf_path], capture_output=True)
        stdout = str(res.stdout)
        return stdout.strip().split("\n") if stdout.strip() else []
    except (OSError, subprocess.SubprocessError, WGSExtractError):
        return []


def chromosome_aliases(chrom: str) -> tuple[str, ...]:
    """Alias chr prefixes for every contig, including random, unplaced and ALT."""
    if not chrom:
        return ()

    if chrom.startswith("chr"):
        bare = chrom[3:]
    else:
        bare = chrom

    aliases: tuple[str, ...]
    if bare.upper() in {"M", "MT"}:
        aliases = ("chrM", "chrMT", "MT", "M")
    elif chrom.startswith("chr"):
        aliases = (chrom, bare)
    else:
        aliases = (chrom, f"chr{chrom}")

    return tuple(dict.fromkeys(aliases))


def match_chromosome_name(target: str, available: Iterable[str]) -> str | None:
    """Return the alias of ``target`` (e.g. MT -> chrM) present in ``available``."""
    names = set(available)
    return next((alias for alias in chromosome_aliases(target) if alias in names), None)


def check_vcf_build(
    vcf_path: PathInput, lengths: Mapping[str, int], against: str
) -> None:
    """Fail when the VCF's ##contig lengths show a build other than ``against``'s."""
    check_header_build(get_bam_header(vcf_path), lengths, against)


def check_header_build(header: str, lengths: Mapping[str, int], against: str) -> None:
    """Fail when a VCF header's ##contig lengths show a build other than
    ``against``'s."""
    contigs = list(lengths)
    mismatched = [
        f"{name} ({length:,} vs {lengths[other]:,})"
        for name, length in header_contig_lengths(header).items()
        if (other := match_chromosome_name(name, contigs)) and lengths[other] != length
    ]
    if mismatched:
        raise WGSExtractError(
            f"The VCF was called against a different reference than {against}; "
            f"contig lengths differ (VCF vs {against}): "
            + ", ".join(mismatched[:5])
            + (f" and {len(mismatched) - 5} more" if len(mismatched) > 5 else "")
        )


def chromosome_rename_mapping(
    source_chroms: Sequence[str], target_chroms: Sequence[str]
) -> list[tuple[str, str]]:
    """Build bcftools --rename-chrs mappings from source names to target names."""
    targets = {chrom for chrom in target_chroms if chrom}
    target_by_alias: dict[str, str] = {}
    for target in target_chroms:
        if not target:
            continue
        for alias in chromosome_aliases(target):
            target_by_alias.setdefault(alias, target)

    mapping: list[tuple[str, str]] = []
    for source in source_chroms:
        if not source or source in targets:
            continue
        matched_target = target_by_alias.get(source)
        if matched_target and matched_target != source:
            mapping.append((source, matched_target))
    return mapping


def vcf_index_chromosomes(vcf_path: PathInput) -> list[str]:
    """Return contigs listed in a bgzipped VCF index."""
    try:
        res = run_command(["bcftools", "index", "-s", vcf_path], capture_output=True)
    except (OSError, subprocess.SubprocessError) as e:
        logging.debug(f"Could not inspect VCF chromosome index for {vcf_path}: {e}")
        return []
    return [
        line.split("\t", 1)[0]
        for line in (res.stdout or "").splitlines()
        if line.strip()
    ]


def header_attributes(body: str) -> dict[str, str]:
    """``key=value`` pairs of a structured header line's ``<...>`` body, in
    any order; commas and ``=`` inside quoted values don't split it."""
    pairs: dict[str, str] = {}
    key, value, quoted, escaped, in_value = "", "", False, False, False
    for char in body + ",":
        if escaped:
            value += char
            escaped = False
        elif quoted and char == "\\":
            value += char
            escaped = True
        elif char == '"':
            quoted = not quoted
            value += char
        elif char == "," and not quoted:
            pairs.setdefault(key.strip(), value.strip())
            key, value, in_value = "", "", False
        elif char == "=" and not in_value:
            in_value = True
        elif in_value:
            value += char
        else:
            key += char
    return pairs


def vcf_header_chromosomes(header: str) -> list[str]:
    """Declared contig IDs, whether or not their lengths are known."""
    return [
        match.group(1)
        for match in re.finditer(
            r"^##contig=<.*?\bID=([^,>]+)",
            header,
            re.MULTILINE,
        )
    ]


def vcf_chromosomes(vcf_path: PathInput) -> list[str]:
    """All declared and indexed contigs, including those without records."""
    declared = vcf_header_chromosomes(get_bam_header(vcf_path))
    return list(dict.fromkeys([*declared, *vcf_index_chromosomes(vcf_path)]))


def normalize_vcf_chromosomes(
    vcf_path: PathInput | None,
    target_chroms: Sequence[str],
    workdir: PathInput | None = None,
) -> str:
    """
    Ensure VCF chromosome naming (chr1 vs 1) matches the targets.
    Returns path to a temporary normalized VCF (in ``workdir``, default the
    run's temporary work, never the input's folder) if changes were needed.
    """
    if not vcf_path:
        raise WGSExtractError(
            "VCF input path is required for chromosome normalization."
        )
    vcf_path = fspath(vcf_path)

    v_chroms = vcf_chromosomes(vcf_path)
    if not v_chroms:
        logging.warning(f"Chromosome normalization skipped for {vcf_path}.")
        return vcf_path

    mapping = chromosome_rename_mapping(v_chroms, target_chroms)
    if not mapping:
        return vcf_path

    fd, map_file = tempfile.mkstemp(
        suffix=".txt", dir=workdir or input_scratch_directory()
    )
    with os.fdopen(fd, "w", newline="\n") as f:
        f.write("\n".join(f"{source} {target}" for source, target in mapping))

    # Use a unique temporary file for the normalized VCF to avoid collisions.
    # Free the name again so bcftools creates the file with the usual umask
    # permissions rather than mkstemp's 0600, which a final output would keep.
    fd_out, norm_vcf = tempfile.mkstemp(
        suffix=".vcf.gz", dir=workdir or input_scratch_directory()
    )
    os.close(fd_out)
    Path(norm_vcf).unlink()

    logging.info(f"Normalizing chromosomes in {vcf_path}...")
    try:
        run_command(
            [
                "bcftools",
                "annotate",
                "--rename-chrs",
                map_file,
                "-Oz",
                "-o",
                norm_vcf,
                vcf_path,
            ]
        )
        ensure_vcf_indexed(norm_vcf)
        return norm_vcf
    except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
        if Path(norm_vcf).exists():
            Path(norm_vcf).unlink()
        raise WGSExtractError(
            f"Chromosome normalization failed for {vcf_path}: {e}"
        ) from e
    finally:
        Path(map_file).unlink()


def get_chr_name(
    bam_path: PathInput,
    target_chr: str,
    cram_opt: Sequence[str] | PathInput | None = None,
) -> str:
    """
    Dynamically map a target chromosome (like MT or Y) to the specific
    naming convention used in the BAM header (e.g., chrM, MT, M, chrY).
    """
    header = get_bam_header(bam_path, cram_opt)
    sn_names = [
        field[3:]
        for line in header.splitlines()
        if line.startswith("@SQ")
        for field in line.split("\t")
        if field.startswith("SN:")
    ]
    return match_chromosome_name(target_chr, sn_names) or target_chr


def get_vcf_chr_name(vcf_path: PathInput, target_chr: str) -> str:
    """Map a target chromosome (like MT or Y) to the naming used in a VCF index."""
    return (
        match_chromosome_name(target_chr, vcf_index_chromosomes(vcf_path)) or target_chr
    )


def calculate_bam_md5(
    bam_path: PathInput,
    cram_opt: Sequence[str] | PathInput | None = None,
    header: str | None = None,
) -> str:
    """
    Calculates MD5 signature from BAM header @SQ lines.
    It takes SN and LN, upcases SN, sorts ASCII-wise, and hashes.
    If a @CO line with MD5: exists, it returns that instead.
    """
    if header is None:
        header = get_bam_header(bam_path, cram_opt)
    if not header:
        return "00000000000000000000000000000000"

    # Check for embedded MD5 signature in comments or read groups first
    for line in header.splitlines():
        if (line.startswith("@CO") or line.startswith("@RG")) and "MD5:" in line:
            parts = line.split("MD5:")
            if len(parts) > 1:
                sig = parts[1].strip().split()[0]
                if len(sig) == 32:
                    return sig

    sq_lines = [line for line in header.splitlines() if line.startswith("@SQ")]

    bamsq_header: dict[str, str] = {}
    for line in sq_lines:
        sn = None
        ln = None
        for part in line.split("\t"):
            if part.startswith("SN:"):
                sn = part[3:].upper()
            elif part.startswith("LN:"):
                ln = part[3:]
        if sn and ln:
            bamsq_header[sn] = ln

    pseudo_header = ""
    for key in sorted(bamsq_header.keys()):
        pseudo_header += f"SN:{key}\tLN:{bamsq_header[key]}\n"

    md5hash = hashlib.md5(pseudo_header.encode())
    return md5hash.hexdigest()


def is_sorted(
    bam_path: PathInput,
    cram_opt: Sequence[str] | PathInput | None = None,
    header: str | None = None,
) -> bool:
    """Verifies if BAM is coordinate sorted."""
    if header is None:
        header = get_bam_header(bam_path, cram_opt)
    if not header:
        return False
    for line in header.splitlines():
        if line.startswith("@HD"):
            if "SO:coordinate" in line:
                return True
    return False


def get_file_version(filepath: PathInput) -> str:
    """Retrieve the format and version of a genomic file using htsfile."""
    try:
        res = run_command(["htsfile", filepath], capture_output=True, check=False)
        if res.returncode == 0:
            # Output format: "path:\tFormat version X sequence data", e.g.
            # "file.cram:\tCRAM version 3.1 sequence data". Split at the tab: a
            # Windows path has a colon of its own (C:\...).
            out = str(res.stdout).strip()
            if ":\t" in out:
                version_info = out.rsplit(":\t", 1)[1].strip()
                # Clean up "sequence data" suffix if present
                version_info = version_info.replace(" sequence data", "")
                return version_info
    except (OSError, subprocess.SubprocessError):
        logging.debug("Could not determine file version with htsfile for %s", filepath)
    return "Unknown"
