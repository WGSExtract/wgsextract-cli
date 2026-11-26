"""Group-owned evidence and the one notice shared by CLI and API runs."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, cast

from wgsextract_cli.commands.registry import COMMAND_MODULES
from wgsextract_cli.core.certification import (
    LEVEL_CRITERIA,
    CertificationEvidence,
    CertificationLevel,
)

if TYPE_CHECKING:
    from wgsextract_cli.core.results import CommandResult

REPO = "https://github.com/WGSExtract/wgsextract-cli"
_GROUPS = {registration.name: registration for registration in COMMAND_MODULES}


@dataclass(frozen=True)
class Certification:
    level: CertificationLevel
    summary: str
    reviewed: str
    links: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()

    def evidence(self) -> CertificationEvidence:
        return {
            "summary": self.summary,
            "links": list(self.links),
            "platforms": list(self.platforms),
            "reviewed": self.reviewed,
        }

    def describe(self) -> str:
        return f"Certification: {self.level.value}. {self.summary}"


def tested(
    reviewed: str, test: str, summary: str = "Covered by fixture tests."
) -> Certification:
    return Certification(
        CertificationLevel.TESTED,
        summary,
        reviewed,
        (f"{REPO}/blob/main/tests/{test}",),
    )


def experimental(reviewed: str, summary: str, *links: str) -> Certification:
    return Certification(CertificationLevel.EXPERIMENTAL, summary, reviewed, links)


def validated(
    reviewed: str, summary: str, platforms: tuple[str, ...], *links: str
) -> Certification:
    return Certification(
        CertificationLevel.VALIDATED, summary, reviewed, links, platforms
    )


@cache
def _group_levels(group: str) -> Mapping[str, Certification]:
    if group in _GROUPS:
        return cast("Mapping[str, Certification]", _GROUPS[group].load().CERTIFICATION)
    from wgsextract_cli.cli.registration.builtins import CERTIFICATION

    return CERTIFICATION


def command_certification(path: str) -> Certification:
    return _group_levels(path.partition(" ")[0])[path]


def find_certification(path: str) -> Certification | None:
    return _group_levels(path.partition(" ")[0]).get(path)


def certification_paths() -> Iterator[str]:
    yield from _group_levels("help")
    for group in _GROUPS:
        yield from _group_levels(group)


def record_certification(result: CommandResult, path: str) -> None:
    from wgsextract_cli.core.config import settings
    from wgsextract_cli.core.settings_schema import SETTINGS

    # Internal/non-command result producers do not have registration metadata.
    if (certification := find_certification(path)) is None:
        return
    result.certification = certification.level.value
    if certification.level in {
        CertificationLevel.TESTED,
        CertificationLevel.EXPERIMENTAL,
    } and settings.get(
        "certification_notices", SETTINGS["certification_notices"].default
    ):
        logging.info(
            "%s: %s certification. %s",
            path,
            certification.level.value,
            LEVEL_CRITERIA[certification.level],
        )
