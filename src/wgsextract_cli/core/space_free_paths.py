"""Space-free symlinks for tools that split their path arguments at spaces.

Some tools (Clair3's shell steps, LongPhase's option parser) break a path
with a space into pieces even when it is passed as one argv entry. They are
given symlinks, in a temporary folder without spaces, to the real files and
folders; the data stays where it is.
"""

import re
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from wgsextract_cli.core.paths import PathInput, absolute_path
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import WGSExtractError

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


def restore_paths(text: str, links: Sequence[str], targets: Sequence[str]) -> str:
    """``text`` (a tool's output header) with each of ``links`` that differs
    from its target replaced by the target's absolute path."""
    for link, target in zip(links, targets, strict=True):
        if link != target:
            text = text.replace(link, str(absolute_path(target)))
    return text
