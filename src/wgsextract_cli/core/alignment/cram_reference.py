"""Supply reference bases to readers which don't use their FASTA for CRAM."""

import gzip
import hashlib
import re
import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.utils import WGSExtractError


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
    with TemporaryDirectory(prefix=".cram-reference-", dir=work) as directory:
        root = Path(directory)
        pending = root / "sequence"
        digest = hashlib.md5()
        opener = gzip.open if reference.endswith(".gz") else open
        with ExitStack() as stack:
            source = stack.enter_context(opener(reference, "rb"))
            target = stack.enter_context(pending.open("wb"))
            has_sequence = False
            while chunk := source.readline(1024**2):
                if chunk.startswith(b">"):
                    if has_sequence:
                        target.close()
                        pending.replace(root / digest.hexdigest())
                        target = stack.enter_context(pending.open("wb"))
                        digest = hashlib.md5()
                    has_sequence = True
                    continue
                bases = b"".join(chunk.split()).upper()
                target.write(bases)
                digest.update(bases)
            target.close()
            if has_sequence:
                pending.replace(root / digest.hexdigest())
        for line in get_bam_header(alignment, reference).splitlines():
            if not line.startswith("@SQ\t"):
                continue
            tags = dict(field.split(":", 1) for field in line.split("\t")[1:])
            if tags.get("SN") not in lengths or "M5" not in tags:
                continue
            md5 = tags["M5"].lower()
            if not re.fullmatch(r"[0-9a-f]{32}", md5) or not (root / md5).is_file():
                raise WGSExtractError(
                    f"The supplied FASTA cannot decode CRAM reference {tags['SN']}: "
                    "its M5 checksum does not match. Supply the encoding --ref."
                )
        yield root.resolve()
