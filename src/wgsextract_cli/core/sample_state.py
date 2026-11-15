"""Tolerant workspace reads and typed writes, retaining unrecognised TOML data.

Loaded TOML is not assumed to satisfy the public schema. Only table structure is
narrowed here; fresh records enter through the output models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .sample_types import FactName

if TYPE_CHECKING:
    from .sample_document import FileOverride, Overrides, RecordedFact, WorkspaceOutput


def table(value: object) -> dict[str, object]:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return cast(dict[str, object], value)
    return {}


@dataclass
class WorkspaceState:
    raw: dict[str, object]
    exists: bool = False
    writable: bool = True
    problem: str | None = None

    def section(self, name: str) -> dict[str, object]:
        return table(self.raw.get(name))

    def record(
        self, name: FactName, key: str, fingerprint: str, fact: RecordedFact
    ) -> bool:
        detected = self.section("detected")
        entry = table(detected.get(key))
        if entry.get("fingerprint") != fingerprint:
            entry = {"fingerprint": fingerprint}
        old = table(entry.get(name))
        if old.get("value") == fact["value"] and old.get("version") == fact["version"]:
            return False
        entry[name] = fact
        detected[key] = entry
        self.raw["detected"] = detected
        return True

    def override(
        self,
        key: str | None,
        shared: Overrides,
        own: FileOverride | None,
        removed: set[str],
    ) -> bool:
        values = self.section("override")
        before = {k: dict(v) if isinstance(v, dict) else v for k, v in values.items()}
        entry = table(values.get(key)) if key is not None else {}
        if own is not None:
            if entry.get("fingerprint") != own["fingerprint"]:
                entry = {}
            entry.update(own)
        values.update(shared)
        for name in removed:
            values.pop(name, None)
            entry.pop(name, None)
        if key is not None:
            if set(entry) <= {"fingerprint"}:
                values.pop(key, None)
            else:
                values[key] = entry
        if values:
            self.raw["override"] = values
        else:
            self.raw.pop("override", None)
        return values != before or not self.exists

    def output(self, key: str, entry: WorkspaceOutput, retained: set[str]) -> None:
        outputs = self.section("outputs")
        for old in set(outputs) - retained:
            del outputs[old]
        outputs[key] = entry
        self.raw["outputs"] = outputs
