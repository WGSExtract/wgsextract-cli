"""Supply reference bases to readers which don't use their FASTA for CRAM."""

import gzip
import hashlib
import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.utils import WGSExtractError

_CACHE_PREFIX = ".cram-reference-"


def clear_cram_reference_caches(work: Path) -> None:
    """Reclaim interrupted caches after acquiring the journal lock/orphan check."""
    for path in work.glob(f"{_CACHE_PREFIX}*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


@contextmanager
def cram_reference_cache(alignment: str, reference: str, work: Path) -> Iterator[Path]:
    """A local htslib MD5 cache made from the supplied indexed FASTA.

    Stream unwrapped, upper-case bases to disk, not a genome-sized Python
    object. Keep the cache only while the reader and its shards are alive.
    """
    lengths = read_fai_lengths(reference)
    if not lengths:
        raise WGSExtractError(f"Missing FASTA index for CRAM decoding: {reference}.fai")
    required = sum(lengths.values()) + 1024**2
    if shutil.disk_usage(work).free < required:
        raise WGSExtractError(
            "Insufficient free disk space for the CRAM decoding reference "
            f"at {work} (needs ~{required / 1024**3:.2f} GiB)."
        )
    with TemporaryDirectory(prefix=_CACHE_PREFIX, dir=work) as directory:
        root = Path(directory)
        pending = root / "sequence"
        digest = hashlib.md5(usedforsecurity=False)
        digests: dict[str, str] = {}
        name = ""
        with open(reference, "rb") as signature:
            compressed = signature.read(2) == b"\x1f\x8b"
        opener = gzip.open if compressed else open
        with ExitStack() as stack:
            source = stack.enter_context(opener(reference, "rb"))
            target = stack.enter_context(pending.open("wb"))
            has_sequence = False
            while chunk := source.readline(1024**2):
                if chunk.startswith(b">"):
                    if has_sequence:
                        target.close()
                        pending.replace(root / digest.hexdigest())
                        digests[name] = digest.hexdigest()
                        target = stack.enter_context(pending.open("wb"))
                        digest = hashlib.md5(usedforsecurity=False)
                    name = chunk[1:].split(maxsplit=1)[0].decode()
                    has_sequence = True
                    continue
                bases = b"".join(chunk.split()).upper()
                target.write(bases)
                digest.update(bases)
            target.close()
            if has_sequence:
                pending.replace(root / digest.hexdigest())
                digests[name] = digest.hexdigest()
        for line in get_bam_header(alignment, reference).splitlines():
            if not line.startswith("@SQ\t"):
                continue
            tags = dict(field.split(":", 1) for field in line.split("\t")[1:])
            if tags.get("SN") not in lengths or "M5" not in tags:
                continue
            if digests.get(tags["SN"]) != tags["M5"].lower():
                raise WGSExtractError(
                    f"The supplied FASTA cannot decode CRAM reference {tags['SN']}: "
                    "its M5 checksum does not match. Supply the encoding --ref."
                )
        yield root.resolve()
