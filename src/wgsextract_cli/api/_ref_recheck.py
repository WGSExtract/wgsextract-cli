"""``ref recheck``'s parameters, kept apart from the large ``api.ref``."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.utils import UsageError

from ._params import Params, param


@dataclass(frozen=True, kw_only=True)
class RefRecheckParams(Params):
    """Compare the resource releases that result manifests recorded (ClinVar,
    gene lists, ...) with the newest ones: from an answers file, or only with
    ``online`` from NCBI's public ClinVar release listing. Nothing is
    downloaded and no analysis is rerun; ``details`` is the
    ``wgsextract.recheck/1`` document."""

    COMMAND = "ref recheck"

    manifests: tuple[Path, ...] = param(
        help="Result manifests (.manifest.json) whose recorded releases to check; "
        "a report's manifest covers the runs it shows.",
        metavar="MANIFEST",
        path="file",
        positional=True,
    )
    answers: Path | None = param(
        None,
        help="Newest releases as a wgsextract.release_answers/1 JSON file "
        "(offline; e.g. saved by an earlier --online --save-answers run).",
        path="file",
        metavar="FILE",
    )
    online: bool = param(
        False,
        help="Ask the public release listing (ClinVar: ftp.ncbi.nlm.nih.gov) for "
        "the newest releases. Sends a plain request for the listing: no sample "
        "data, paths or positions. Off unless given.",
        action="store_true",
        writes=True,
        safe_value=False,
    )
    save_answers: Path | None = param(
        None,
        help="With --online, also save the answers it got to FILE, to repeat "
        "the check offline with --answers.",
        path="file",
        metavar="FILE",
        writes=True,
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.manifests:
            raise UsageError("Name at least one result manifest to recheck.")
        if self.answers is not None and self.online:
            raise UsageError("Give either --answers or --online, not both.")
        if self.save_answers is not None and not self.online:
            raise UsageError("--save-answers needs --online.")

    def write_folders(self) -> Iterator[Path]:
        if self.save_answers is not None:
            yield self.save_answers.expanduser().absolute().parent
