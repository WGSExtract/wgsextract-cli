"""Space-free symlinks for tools that split their path arguments at spaces.

Some tools (Clair3's shell steps, LongPhase's option parser) break a path
with a space into pieces even when it is passed as one argv entry. They are
given symlinks, in a temporary folder without spaces, to the real files and
folders; the data stays where it is. Only paths with a space are linked,
and no links folder is made when none has one, so such runs are unchanged.
Headers the tool writes are given the real paths back before publishing.
"""

import re
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from wgsextract_cli.core.paths import PathInput, absolute_path
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import WGSExtractError, run_command

READS_INDEXES = (".bai", ".csi", ".crai")
REFERENCE_INDEXES = (".fai", ".gzi")
VCF_INDEXES = (".tbi", ".csi")


def has_space(path: PathInput) -> bool:
    return bool(re.search(r"\s", str(path)))


@contextmanager
def space_free_folder(tool: str) -> Iterator[Path]:
    """A temporary folder, without spaces in its path, for ``tool``'s links,
    in the run's temporary directory (--tmp-dir or ``temp_directory``)."""
    with tempfile.TemporaryDirectory(
        prefix=f"wgse_{tool.lower()}_", dir=configured_temp_directory()
    ) as folder:
        if has_space(folder):
            raise WGSExtractError(
                f"{tool} can't use paths with spaces, and the temporary folder "
                f"{folder} has one. Choose a temporary folder without spaces "
                "with --tmp-dir or the temp_directory setting in config.toml."
            )
        yield Path(folder)


def space_free_link(target: PathInput, link: Path, indexes: Sequence[str] = ()) -> str:
    """``target`` itself when its path has no space; else ``link``, linked to
    ``target``, with ``link`` + ext linked to each of ``target``'s ``indexes``
    found (``x.bam.bai``, or ``x.bai`` for reads)."""
    path = Path(target)
    if not has_space(path.absolute()):
        return str(target)
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(path.absolute(), target_is_directory=path.is_dir())
    for ext in indexes:
        candidates = [Path(f"{path}{ext}")]
        if ext in READS_INDEXES and path.suffix.lower() in (".bam", ".cram"):
            candidates.append(path.with_suffix(ext))
        for index in candidates:
            if index.is_file():
                Path(f"{link}{ext}").symlink_to(index.absolute())
                break
    return str(link)


@contextmanager
def space_free_paths(
    tool: str, paths: Sequence[tuple[PathInput, str, Sequence[str]]]
) -> Iterator[list[str]]:
    """For each ``(target, link name, indexes)``, the path to give ``tool``:
    the target, or a space-free link to it (see ``space_free_link``) in a
    temporary links folder, made only when some target has a space."""
    if not any(has_space(Path(target).absolute()) for target, _n, _i in paths):
        yield [str(target) for target, _n, _i in paths]
        return
    with space_free_folder(tool) as links:
        yield [
            space_free_link(target, links / name, indexes)
            for target, name, indexes in paths
        ]


def restore_paths(text: str, real_paths: Mapping[str, str], prefix: str = "") -> str:
    """``text`` with each link in ``real_paths`` replaced by the absolute path
    it stands for, on the lines starting with ``prefix``. Paths given to the
    tool as they are (link equal to its path) are left alone."""
    real = {
        link: str(absolute_path(path))
        for link, path in real_paths.items()
        if link not in (path, str(absolute_path(path)))
    }
    if not real:
        return text
    links = sorted(real, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(link) for link in links))
    return "".join(
        pattern.sub(lambda m: real[m.group()], line)
        if line.startswith(prefix)
        else line
        for line in text.splitlines(keepends=True)
    )


def restore_vcf_paths(vcf: str, real_paths: Mapping[str, str], work: str) -> str:
    """``vcf``, or a copy in ``work`` whose ``##`` header lines name the real
    paths instead of the links in ``real_paths``."""
    header = run_command(["bcftools", "view", "-h", vcf], capture_output=True).stdout
    restored = restore_paths(header, real_paths, "##")
    if restored == header:
        return vcf
    header_file = Path(work, "restored_header.vcf")
    header_file.write_text(restored, newline="\n")
    named = str(Path(work, f"restored{''.join(Path(vcf).suffixes[-2:])}"))
    run_command(["bcftools", "reheader", "-h", str(header_file), "-o", named, vcf])
    return named


def restore_cram_paths(cram: str, real_paths: Mapping[str, str], work: str) -> None:
    """Put the real paths in ``cram``'s header in place of the links in
    ``real_paths``: @SQ UR, else readers without -T fetch the reference by M5
    from EBI, and @PG CL. In place when the new header fits the CRAM's header
    container; else rewritten (reheader -i fails, leaving it unchanged)."""
    header = run_command(["samtools", "view", "-H", cram], capture_output=True).stdout
    restored = restore_paths(header, real_paths)
    if restored == header:
        return
    header_file = Path(work, "restored_header.sam")
    header_file.write_text(restored, newline="\n")
    fixed = str(header_file)
    if run_command(["samtools", "reheader", "-i", fixed, cram], check=False).returncode:
        copy = str(Path(work, "restored.cram"))
        with open(copy, "wb") as out:
            run_command(["samtools", "reheader", fixed, cram], stdout=out)
        Path(copy).replace(cram)
