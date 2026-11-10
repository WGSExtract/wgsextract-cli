"""The sample's DNA contamination, as `qc contamination` recorded it.

`qc contamination` records VerifyBamID2's FREEMIX estimate as the
``contamination`` fact of the alignment in its sample workspace. Commands
that turn reads or calls into genotypes look it up here, warn when it is above
``qc_max_contamination``, and confidence labels cap their findings at
moderate. In a genome folder, which holds one person's files, the newest
estimate from any of its alignments applies to all its files (a VCF called
from them, say); elsewhere only to the file it was measured on.
"""

from __future__ import annotations

from pathlib import Path

from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.config import KNOWN_SETTINGS, settings
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.results import current_result, warn

FACT = "contamination"
GUIDE = "docs/contamination.md"
WARNING = "sample_contamination"


def max_contamination() -> float:
    """``qc_max_contamination``; 0 disables the warning and the cap."""
    value = settings.get("qc_max_contamination")
    if not isinstance(value, int | float) or isinstance(value, bool):
        value = KNOWN_SETTINGS["qc_max_contamination"][0]
    return float(value) if isinstance(value, int | float) else 0.0


def sample_contamination(file: PathInput | None) -> sample_workspace.Fact | None:
    """The contamination estimate that applies to ``file``, if one is recorded."""
    if not file:
        return None
    fact = sample_workspace.known(FACT, file)
    if fact is not None:
        return fact
    workspace = sample_workspace.workspace_for(file)
    if workspace is None or not workspace.is_genome_folder:
        return None
    try:
        facts = workspace.detected_facts(FACT)
    except (OSError, ValueError):
        return None
    return facts[0] if facts else None


def contaminated(file: PathInput | None) -> float | None:
    """The recorded FREEMIX that applies to ``file`` when it is at or above
    ``qc_max_contamination``, else None."""
    threshold = max_contamination()
    fact = sample_contamination(file)
    if fact is None or not threshold:
        return None
    try:
        value = float(fact.value)
    except (TypeError, ValueError):
        return None
    return value if value >= threshold else None


def warn_if_contaminated(file: PathInput | None, what: str = "genotypes") -> None:
    """Warn, once per run, that ``what`` from ``file`` may be wrong because
    its sample is contaminated."""
    if any(w.code == WARNING for w in current_result().warnings):
        return
    value = contaminated(file)
    if value is None or file is None:
        return
    threshold = max_contamination()
    warn(
        WARNING,
        f"About {value:.1%} of the DNA behind {Path(file).name} is from another "
        f"person (VerifyBamID2 FREEMIX, above qc_max_contamination "
        f"{threshold:.1%}), so {what} from it may show the other person's "
        f"alleles as heterozygous calls. See {GUIDE}.",
        freemix=round(value, 4),
        threshold=threshold,
    )
