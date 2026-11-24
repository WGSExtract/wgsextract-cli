"""Resolve the viewer's reference locally using the CLI's sequence dictionary."""

import json
import sys
from pathlib import Path
from typing import Any


def mitochondrial_origin(fasta: str, lengths: dict[str, int]) -> str:
    """ "rCRS" when the FASTA's mitochondrion is numbered as the rCRS, else what
    it is instead, or "" when it has none. Length can't tell: CHM13v2.0's chrM
    is as long as the rCRS but starts elsewhere round the circle."""
    from wgsextract_cli.core.ref_library.mitochondrial import fasta_mito, mito_origin

    name = next((n for n in lengths if n.upper() in {"CHRM", "CHRMT", "MT", "M"}), "")
    sequence = fasta_mito(fasta, name) if name else None
    return (mito_origin(sequence) or "unrecognised") if sequence else ""


def _vcf_fallback_fasta(
    input_path: str,
    header: str,
    lengths: dict[str, int],
    candidates: list[str | None],
    directories: list[Path],
    root: str,
) -> tuple[str, dict[str, int]] | None:
    """Match an installed FASTA for a VCF using Basic Info's workspace build or
    layered VCF reference detection when exact ##contig equality doesn't match."""
    from wgsextract_cli.core import sample_workspace
    from wgsextract_cli.core.builds import (
        CHR1_LENGTH_BUILDS,
        build_from_path,
        canonical_build,
    )
    from wgsextract_cli.core.dependencies import get_tool_path
    from wgsextract_cli.core.ref_library.assets import read_fai_lengths
    from wgsextract_cli.core.utils import run_command
    from wgsextract_cli.core.vcf_reference import identify_vcf_reference

    recorded_build: str | None = None
    ws = sample_workspace.workspace_for(input_path)
    if ws is not None:
        fact = ws.detected("build", input_path)
        if fact and isinstance(fact.value, str):
            recorded_build = canonical_build(fact.value)

    detection = identify_vcf_reference(
        input_path,
        ref_lib_dir=root,
        header=header,
        allow_body_sampling=recorded_build is None,
    )
    build = detection.build or recorded_build
    if not build:
        return None

    present_contigs: list[str] = []
    tabix = get_tool_path("tabix")
    if tabix and any(Path(input_path + ext).is_file() for ext in (".tbi", ".csi")):
        res = run_command([tabix, "-l", input_path], capture_output=True, check=False)
        if res.returncode == 0:
            present_contigs = [
                line.strip() for line in res.stdout.splitlines() if line.strip()
            ]
    naming = detection.naming
    if not naming and present_contigs:
        naming = (
            "Chr"
            if any(c.startswith(("chr", "CHR")) for c in present_contigs)
            else "Num"
        )

    for fasta in candidates:
        if not fasta or not any(
            Path(fasta).resolve().is_relative_to(directory.resolve())
            for directory in directories
        ):
            continue
        installed = read_fai_lengths(fasta)
        if not installed:
            continue
        chr1 = next(
            (installed[n] for n in ("chr1", "1", "CHR1") if n in installed), None
        )
        fasta_build = (
            CHR1_LENGTH_BUILDS.get(chr1)
            if chr1 is not None
            else build_from_path(Path(fasta).name)
        )
        if fasta_build != build:
            continue
        if present_contigs:
            if not all(
                name in installed
                and (name not in lengths or installed[name] == lengths[name])
                for name in present_contigs
            ):
                continue
        elif naming == "Chr" and not any(k.startswith("chr") for k in installed):
            continue
        elif naming == "Num" and "1" not in installed:
            continue
        return fasta, installed
    return None


def resolve_reference(
    input_path: str, config_path: str, mito: bool = False
) -> dict[str, Any]:
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
    if not lengths and is_alignment:
        return {"problem": "referenceMissing"}
    library = ReferenceLibrary(
        None,
        md5_sig=sequence_signature(lengths) if lengths else None,
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
    if lengths:
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
            from viewer_region import choose_initial_locus

            return {
                "fasta": fasta,
                "locus": choose_initial_locus(
                    input_path,
                    fasta,
                    lengths,
                    tool,
                    is_alignment,
                    reflib_root=root,
                ),
                # Only asked for rCRS-numbered positions, such as haplogroup variants.
                **({"mitoOrigin": mitochondrial_origin(fasta, lengths)} if mito else {}),
            }
    if not is_alignment:
        matched = _vcf_fallback_fasta(
            input_path, header, lengths, candidates, directories, root
        )
        if matched is not None:
            fasta, installed = matched
            effective_lengths = lengths or installed
            from viewer_region import choose_initial_locus

            return {
                "fasta": fasta,
                "locus": choose_initial_locus(
                    input_path,
                    fasta,
                    effective_lengths,
                    tool,
                    False,
                    reflib_root=root,
                ),
                **(
                    {"mitoOrigin": mitochondrial_origin(fasta, effective_lengths)}
                    if mito
                    else {}
                ),
            }
    return {"problem": "referenceUnverified" if unverified else "referenceMissing"}


if __name__ == "__main__":
    print(
        json.dumps(
            resolve_reference(
                sys.argv[1], sys.argv[2], mito="--mito-origin" in sys.argv[3:]
            )
        )
    )
