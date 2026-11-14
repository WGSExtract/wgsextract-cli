"""Fail before decoding reads with a reference from another assembly."""

from pathlib import Path

from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.reference_assets import header_sequences, read_fai_lengths
from wgsextract_cli.core.utils import WGSExtractError, run_command


def validate_calling_reference(alignment: str, fasta: str) -> None:
    """Require every input SQ to exist with the same name and length.

    An alignment may contain a subset of the reference dictionary. Sequence
    content is checked only where both dictionaries provide an M5 checksum.
    """
    sequences = header_sequences(get_bam_header(alignment))
    if not Path(fasta + ".fai").is_file():
        run_command(["samtools", "faidx", fasta])
    lengths = read_fai_lengths(fasta)
    if not sequences or not lengths:
        raise WGSExtractError(
            "Cannot validate the calling reference: missing alignment SQ or FASTA index."
        )
    problems = []
    for sequence in sequences:
        name, length = sequence["SN"], int(sequence["LN"])
        if name not in lengths:
            problems.append(f"{name}: missing from reference")
        elif lengths[name] != length:
            problems.append(f"{name}: input length {length}, reference {lengths[name]}")
    # Do not scan an entire human FASTA just to compute checksums. Existing
    # dictionaries let us catch same-length replacements without that cost.
    from wgsextract_cli.core.reference_assets import reference_dict_path

    dictionary = Path(reference_dict_path(fasta))
    if dictionary.is_file():
        reference = {row["SN"]: row for row in header_sequences(dictionary.read_text())}
        for sequence in sequences:
            expected = reference.get(sequence["SN"], {}).get("M5")
            actual = sequence.get("M5")
            if expected and actual and expected.lower() != actual.lower():
                problems.append(f"{sequence['SN']}: sequence MD5 differs")
    if problems:
        raise WGSExtractError(
            f"Incompatible calling reference {fasta} for {alignment}: "
            + "; ".join(problems[:5])
            + ". Use the FASTA used to align this input (same build and contig names)."
        )
