"""Which contigs a Y extraction reads: the primary Y chromosome alone, or with
the reference's Y-associated contigs (#1184).

Of the supported assemblies only GRCh38 assigns another sequence to Y: the
unlocalized scaffold HSCHRY_RANDOM_CTG1, 37,240 bp, GenBank KI270740.1, RefSeq
NT_187395.1, UCSC chrY_KI270740v1_random (NCBI assembly report
GCA_000001405.15). GRCh37 (GCA_000001405.14) and T2T-CHM13v2.0
(GCA_009914755.4) assign none. WGS Extract v4 added the UCSC name to the Y and
Y+MT BAMs of chr-named build-38 inputs. Here a header contig qualifies by any
of its names only with that length, its M5 when the header records one, and a
GRCh38-length primary Y.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from wgsextract_cli.core.ref_library.assets import (
    header_sequences,
    read_fai_lengths,
    reference_dict_path,
)
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import match_chromosome_name

Y_SCOPES = ("primary", "associated")
Role = Literal["y", "y_associated", "mt"]


@dataclass(frozen=True)
class _Associated:
    names: frozenset[str]
    length: int
    md5: str
    y_length: int
    label: str


_ASSOCIATED = (
    _Associated(
        frozenset(
            {
                "chrY_KI270740v1_random",
                "KI270740.1",
                "NT_187395.1",
                "HSCHRY_RANDOM_CTG1",
            }
        ),
        37_240,
        "69e42252aead509bf56f1ea6fda91405",
        57_227_415,
        "GRCh38 KI270740.1",
    ),
)
# The primary Y by its RefSeq or GenBank accession (GRCh37, GRCh38, CHM13v2.0).
_Y_ACCESSION = re.compile(r"(?:NC_000024|CM000686|NC_060948|CP086569)\.\d+")
# Names that look Y-specific but aren't a recognised Y-associated contig.
_Y_LIKE = re.compile(r"(?:chr)?Y[_.-]", re.IGNORECASE)


@dataclass(frozen=True)
class ContigChoice:
    name: str
    length: int | None
    role: Role
    included: bool
    reason: str


@dataclass(frozen=True)
class ContigSelection:
    scope: str
    choices: tuple[ContigChoice, ...]

    @property
    def contigs(self) -> list[str]:
        """The contigs to read, in header order."""
        return [c.name for c in self.choices if c.included]

    def record(self, bam: str | None = None) -> dict[str, object]:
        """The selection as a result's ``details``: each considered contig,
        with the records ``bam`` holds on it (from its index) when given."""
        counts = _index_counts(bam) if bam else {}
        rows = []
        for choice in self.choices:
            row: dict[str, object] = asdict(choice)
            if choice.included and choice.name in counts:
                mapped, placed_unmapped = counts[choice.name]
                row["mapped_records"] = mapped
                row["placed_unmapped_records"] = placed_unmapped
            rows.append(row)
        return {"y_scope": self.scope, "contigs": rows}

    def check_reference(self, ref: str) -> None:
        """Fail when ``ref`` lacks an included Y-associated contig, or its
        .fai and .dict can't show whether it has them: samtools can't decode
        a CRAM's reads there, and bcftools silently calls nothing there."""
        wanted = [
            c.name for c in self.choices if c.included and c.role == "y_associated"
        ]
        if not wanted:
            return
        names = set(read_fai_lengths(ref)) or _dict_names(ref)
        if not names:
            raise WGSExtractError(
                f"The reference {ref} has no readable .fai or .dict to confirm "
                f"it has {', '.join(wanted)}: index it with samtools faidx, or "
                "extract with --y-scope primary."
            )
        missing = [name for name in wanted if name not in names]
        if missing:
            raise WGSExtractError(
                f"The reference {ref} has no {', '.join(missing)}, which the "
                "input's header has: pass the reference it was aligned to with "
                "--ref, or extract with --y-scope primary."
            )


def _dict_names(ref: str) -> set[str]:
    try:
        text = Path(reference_dict_path(ref)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return {s["SN"] for s in header_sequences(text) if s.get("SN")}


def primary_y(names: list[str]) -> str | None:
    """The header's primary Y chromosome: chrY, Y or its accession."""
    return match_chromosome_name("Y", names) or next(
        (n for n in names if _Y_ACCESSION.fullmatch(n)), None
    )


def select_contigs(
    header: str, scope: str, *, mt: bool = False, source: str = "The input"
) -> ContigSelection:
    """The Y (and with ``mt`` the mitochondrial) contigs of an alignment's
    header to extract under ``scope``, with each Y-associated or Y-like
    contig's reason for being included or not."""
    if scope not in Y_SCOPES:
        raise WGSExtractError(f"Unknown Y scope {scope!r}.")
    sequences = [s for s in header_sequences(header) if s.get("SN")]
    names = [s["SN"] for s in sequences]
    lengths = {s["SN"]: int(s["LN"]) for s in sequences if s.get("LN", "").isdigit()}
    y = primary_y(names)
    if scope == "associated":
        if not sequences:
            raise WGSExtractError(
                f"{source} has no reference dictionary (@SQ lines), so its "
                "Y-associated contigs can't be identified."
            )
        if y is None:
            raise WGSExtractError(
                f"{source} names no Y chromosome (chrY, Y or its RefSeq/GenBank "
                "accession), so it has no Y-associated contigs to add."
            )
    # Without a Y in the header, primary extraction reads "Y" as it always
    # has: an empty BAM, which warns.
    choices = [
        ContigChoice(
            y or "Y",
            lengths.get(y or "Y"),
            "y",
            True,
            "primary Y chromosome" if y else "not in the header",
        )
    ]
    if mt:
        chr_m = match_chromosome_name("MT", names) or "MT"
        choices.append(
            ContigChoice(chr_m, lengths.get(chr_m), "mt", True, "mitochondrial genome")
        )
    y_length = lengths.get(y) if y else None
    for sequence in sequences:
        name = sequence["SN"]
        if name == y:
            continue
        known = next((k for k in _ASSOCIATED if name in k.names), None)
        if known is None:
            if _Y_LIKE.match(name):
                choices.append(
                    ContigChoice(
                        name,
                        lengths.get(name),
                        "y_associated",
                        False,
                        "not a recognised Y-associated contig of GRCh37, GRCh38 "
                        "or T2T-CHM13v2.0",
                    )
                )
            continue
        failure = _disqualified(known, sequence, y_length)
        included = scope == "associated" and failure is None
        if included:
            reason = f"{known.label}, Y-associated"
        else:
            reason = failure or "Y-associated; --y-scope associated adds it"
        choices.append(
            ContigChoice(name, lengths.get(name), "y_associated", included, reason)
        )
    order = {name: i for i, name in enumerate(names)}
    choices.sort(key=lambda c: order.get(c.name, len(order)))
    return ContigSelection(scope, tuple(choices))


def _disqualified(
    known: _Associated, sequence: dict[str, str], y_length: int | None
) -> str | None:
    length = int(sequence["LN"]) if sequence.get("LN", "").isdigit() else None
    if length != known.length:
        return f"length {length}, not {known.label}'s {known.length:,} bp"
    md5 = sequence.get("M5", "").lower()
    if md5 and md5 != known.md5:
        return f"M5 {md5} is not {known.label}'s {known.md5}"
    if y_length != known.y_length:
        return f"the primary Y is {y_length} bp, not GRCh38's {known.y_length:,}"
    return None


def _index_counts(bam: str) -> dict[str, tuple[int, int]]:
    """Each contig's (mapped, placed unmapped) records from a BAM's index."""
    stats = run_command(["samtools", "idxstats", bam], capture_output=True).stdout
    return {
        row[0]: (int(row[2]), int(row[3]))
        for row in (line.split("\t") for line in stats.splitlines())
        if len(row) >= 4 and row[0] != "*"
    }
