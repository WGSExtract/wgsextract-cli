"""The medication context ``report --pgx-context`` reviews: a local TOML file
naming the medications a person takes or is considering, never sent anywhere.

    # Current or planned medications: CPIC's generic names (or ids), plus
    # CPIC's patient group or indication where its guidance depends on it.
    medications = ["codeine", "paroxetine", { name = "clopidogrel", population = "NVI" }]
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.utils import UsageError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_KEYS = {"name", "population"}


@dataclass(frozen=True)
class Medication:
    name: str
    population: str | None = None


@dataclass(frozen=True)
class PgxContext:
    medications: tuple[Medication, ...]

    def effective(self) -> list[dict[str, str | None]]:
        """The context as used: names lower-cased, duplicates removed."""
        return [{"name": m.name, "population": m.population} for m in self.medications]

    @property
    def sha256(self) -> str:
        text = json.dumps(self.effective(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def names(self) -> set[str]:
        return {m.name for m in self.medications}


def load(path: str | Path) -> PgxContext:
    file = Path(path).expanduser()
    try:
        document = tomllib.loads(file.read_text(encoding="utf-8"))
    except OSError as error:
        raise UsageError(f"Cannot read the PGx context {file}: {error}") from error
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise UsageError(f"PGx context {file} is not valid TOML: {error}") from error
    unknown = set(document) - {"medications"}
    if unknown:
        raise UsageError(
            f"PGx context {file}: unknown key(s) {', '.join(sorted(unknown))}; "
            "only 'medications' is read."
        )
    entries = document.get("medications")
    if not isinstance(entries, list) or not entries:
        raise UsageError(f"PGx context {file} lists no medications.")
    seen: dict[str, Medication] = {}
    for number, entry in enumerate(entries, start=1):
        medication = _medication(entry, file, number)
        if medication.name in seen and seen[medication.name] != medication:
            raise UsageError(
                f"PGx context {file}: {medication.name} is listed twice with "
                "different populations."
            )
        seen[medication.name] = medication
    return PgxContext(tuple(seen.values()))


def _medication(entry: object, file: Path, number: int) -> Medication:
    if isinstance(entry, str):
        entry = {"name": entry}
    if not isinstance(entry, dict) or set(entry) - _KEYS:
        raise UsageError(
            f"PGx context {file}, medication {number}: give a name, or a table "
            "with name and optional population."
        )
    name, population = entry.get("name"), entry.get("population")
    if not isinstance(name, str) or not name.strip():
        raise UsageError(f"PGx context {file}, medication {number} has no name.")
    if population is not None and (
        not isinstance(population, str) or not population.strip()
    ):
        raise UsageError(
            f"PGx context {file}, {name}: population must be non-empty text."
        )
    return Medication(
        " ".join(name.split()).lower(),
        " ".join(population.split()) if population else None,
    )
