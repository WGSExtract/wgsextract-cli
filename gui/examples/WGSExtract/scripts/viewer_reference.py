"""Resolve the viewer's reference locally using the CLI's sequence dictionary."""

import json
import sys
from pathlib import Path
from typing import Any


def resolve_reference(input_path: str, config_path: str) -> dict[str, Any]:
    from wgsextract_cli.core.alignment.metadata import get_bam_header
    from wgsextract_cli.core.config import reload_settings, set_config_path
    from wgsextract_cli.core.dependencies import get_tool_path
    from wgsextract_cli.core.ref_library.assets import (
        header_sequences,
        read_fai_lengths,
        reference_dict_path,
        reference_fasta_candidates,
        reference_genomes_dir,
        sequence_signature,
    )
    from wgsextract_cli.core.ref_library.models import sequence_lengths
    from wgsextract_cli.core.ref_library.resolver import ReferenceLibrary, library_roots
    from wgsextract_cli.core.utils import run_command

    set_config_path(config_path)
    reload_settings()
    is_alignment = input_path.lower().endswith((".bam", ".cram"))
    tool = get_tool_path("samtools" if is_alignment else "bcftools")
    if not tool:
        return {"problem": "headerToolMissing"}
    header = (
        get_bam_header(input_path)
        if is_alignment
        else run_command([tool, "view", "-h", input_path], capture_output=True).stdout
    )
    lengths = sequence_lengths(header)
    if not lengths:
        return {"problem": "referenceMissing"}
    library = ReferenceLibrary(
        None,
        md5_sig=sequence_signature(lengths),
        input_path=input_path,
        skip_full_search=True,
    )
    root = library_roots(None).root
    if not root:
        return {"problem": "referenceMissing"}
    directories = [Path(root), Path(reference_genomes_dir(root))]
    # The CLI can also search beside the input. The viewer serves only installed
    # libraries, and must not let an adjacent reference hide an installed match.
    candidates = list(
        dict.fromkeys(
            [library.fasta]
            + [
                file
                for directory in directories
                for file in reference_fasta_candidates(directory)
            ]
        )
    )
    unverified = False
    sequences = header_sequences(header)
    for fasta in candidates:
        if not fasta or not any(
            Path(fasta).resolve().is_relative_to(directory.resolve())
            for directory in directories
        ):
            continue
        installed = read_fai_lengths(fasta)
        if not all(installed.get(name) == length for name, length in lengths.items()):
            continue
        # Never compute a whole-genome dictionary or follow CRAM URs.
        if any(row.get("M5") for row in sequences):
            dictionary = Path(reference_dict_path(fasta))
            if not dictionary.is_file():
                unverified = True
                continue
            checksums = {
                row.get("SN"): row.get("M5", "").lower()
                for row in header_sequences(dictionary.read_text(encoding="utf-8"))
            }
            if any(
                row.get("M5") and row["M5"].lower() != checksums.get(row.get("SN"))
                for row in sequences
            ):
                continue
        elif input_path.lower().endswith(".cram"):
            unverified = True
            continue
        first = next(iter(lengths))
        return {
            "fasta": fasta,
            "locus": f"{first}:1-{min(1000, lengths[first])}",
        }
    return {"problem": "referenceUnverified" if unverified else "referenceMissing"}


if __name__ == "__main__":
    print(json.dumps(resolve_reference(sys.argv[1], sys.argv[2])))
