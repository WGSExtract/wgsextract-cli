"""Where outputs go when config.toml sets no output_directory.

A lab delivers primary files: a BAM or CRAM, FASTQs, VCFs or a raw microarray
file, maybe with their indexes. The folder holding them is the lab's results
and is treated as immutable: outputs go to a ``wgse/`` folder inside it, so the
original folder and its ``wgse/`` together are the person's genome file set.
An input already in a ``wgse/`` folder was made by this tool, and further
outputs go beside it.

Only an index that must sit beside its file (.bai, .crai, .tbi, ...) is ever
written in the lab's folder, unless the user points output_directory there."""

from collections.abc import Iterable
from os import PathLike, fspath
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from wgsextract_cli.core.paths import absolute_path, expand_path

OUTPUT_FOLDER = "wgse"
# Commands whose outputs belong beside the files they describe, as an index
# does: a checksum manifest (and its signature) lists the files around it.
BESIDE_INPUT_COMMANDS = frozenset({"sign create"})

_COMPRESSED = (".gz", ".bgz", ".zip", ".bz2", ".xz", ".zst")
# The primary file kinds, in the order that picks the output folder when a
# command reads more than one: alignments, then reads, then variants, then
# microarray results.
_KINDS: tuple[tuple[str, ...], ...] = (
    (".bam", ".cram", ".sam"),
    (".fastq", ".fq", ".ora"),
    (".vcf", ".bcf", ".gvcf"),
    (".txt", ".csv", ".tsv"),
)


def primary_rank(path: str | PathLike[str]) -> int | None:
    """The primary file kind's rank (0 = alignment ... 3 = microarray), or
    None for any other file (a reference FASTA, an index, a folder)."""
    name = Path(fspath(path)).name.lower()
    if name.endswith(".zip"):
        try:
            with ZipFile(path) as archive:
                members = [
                    item
                    for item in archive.infolist()
                    if not item.is_dir()
                    and not Path(item.filename).name.startswith(".")
                    and "__MACOSX" not in item.filename
                ]
                if (
                    len(members) == 1
                    and Path(members[0].filename).suffix.lower() in _KINDS[3]
                ):
                    return 3
        except (OSError, BadZipFile):
            pass
        return None
    for suffix in _COMPRESSED:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    for rank, suffixes in enumerate(_KINDS):
        if name.endswith(suffixes):
            return rank
    return None


def _is_local(value: str) -> bool:
    return "://" not in value


def primary_input(values: Iterable[object]) -> Path | None:
    """The input that decides the output folder: the first of the highest
    ranked primary files among ``values`` (paths, or tuples of them)."""
    best: tuple[int, Path] | None = None
    for value in _flatten(values):
        text = expand_path(fspath(value))
        if not text or not _is_local(text):
            continue
        rank = primary_rank(text)
        if rank is not None and (best is None or rank < best[0]):
            best = (rank, absolute_path(text))
    return best[1] if best else None


def default_output_directory(values: Iterable[object]) -> Path | None:
    """``wgse/`` beside the primary input, or the input's own folder when that
    already is a ``wgse/`` folder; None when the command reads no primary file
    (it then keeps its own default)."""
    source = primary_input(values)
    if source is None:
        return None
    folder = source.parent
    return folder if folder.name.lower() == OUTPUT_FOLDER else folder / OUTPUT_FOLDER


def primary_overwrite_warning(
    output: str | PathLike[str], inputs: Iterable[object], outdir: object
) -> str | None:
    """Why ``output``, a primary file kind, may replace a file from the lab:
    it lands in the folder of an input (a lab results folder) that isn't the
    output folder the user chose. None when it doesn't."""
    target = absolute_path(expand_path(fspath(output)))
    if primary_rank(target) is None:
        return None
    if outdir and target.parent == absolute_path(expand_path(fspath(str(outdir)))):
        return None
    lab_folders = {
        absolute_path(expand_path(fspath(value))).parent
        for value in _flatten(inputs)
        if _is_local(fspath(value)) and primary_rank(value) is not None
    }
    if target.parent not in lab_folders:
        return None
    return (
        f"Wrote {target} into the folder of the lab's original files, where it "
        "could replace one of them. Choose an output folder elsewhere to keep "
        "them untouched."
    )


def make_output_folder(folder: str | PathLike[str]) -> Path | None:
    """Create ``folder`` (and its parents); returns it when this created it."""
    path = Path(expand_path(fspath(folder)))
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    return None if existed else path


def remove_if_empty(folder: Path | None) -> None:
    """Remove an output folder a run created and left empty (it failed, or
    wrote nothing), so no empty wgse/ is left beside the lab's files."""
    if folder is None:
        return
    try:
        folder.rmdir()
    except OSError:
        pass  # it holds the run's files, or is gone already


def _flatten(values: Iterable[object]) -> Iterable[str | PathLike[str]]:
    for value in values:
        if isinstance(value, (list, tuple, set, frozenset)):
            yield from _flatten(value)
        elif isinstance(value, (str, PathLike)) and fspath(value):
            yield value
