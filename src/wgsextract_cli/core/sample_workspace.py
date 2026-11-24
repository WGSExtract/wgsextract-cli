"""Sample workspaces: what is known about one sample, recorded once.

A sample workspace is a genome folder: the folder of a ``--genome`` library
entry, or any folder holding ``wgsextract.sample.toml`` or
``genome-config.toml``. Its ``wgsextract.sample.toml`` (schema
``wgsextract.sample/1``) records

- ``[override]``: facts the user set (``wgsextract sample set``), which win
  over detection. A sex override is the person's, so it is folder-wide in a
  genome folder; elsewhere, and for the platform always, it belongs to one file
  (``[override."<file>"]``, under that file's fingerprint);
- ``[detected."<file>"]``: facts commands detected from each input file, with
  the method, confidence, detector version, command and time, under the file's
  fingerprint, so a replaced file's facts are ignored and re-detected;
- ``[outputs]``: the result manifests that ``--genome`` runs wrote there.

Commands ask ``known(fact, file)`` before detecting (override, else this
file's current detected fact) and ``record(...)`` what they detect. Where an
override changes an algorithm, ``resolve`` still detects the fact cheaply and
warns when the reads disagree with the override. Recording
is best effort: a read-only folder, a corrupt file or one written by a newer
release never fails a command, and such a file is never overwritten.
docs/sample-workspace.md describes the format.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, overload

from wgsextract_cli import __version__
from wgsextract_cli.core.paths import PathInput, absolute_path
from wgsextract_cli.core.sample_context import (
    active_workspace as active_workspace,
)
from wgsextract_cli.core.sample_context import (
    check_override as check_override,
)
from wgsextract_cli.core.sample_context import (
    current_workspace as current_workspace,
)
from wgsextract_cli.core.sample_context import (
    known as known,
)
from wgsextract_cli.core.sample_context import (
    record as record,
)
from wgsextract_cli.core.sample_context import (
    recording_paused as recording_paused,
)
from wgsextract_cli.core.sample_context import (
    resolve as resolve,
)
from wgsextract_cli.core.sample_context import (
    workspace_for as workspace_for,
)
from wgsextract_cli.core.sample_facts import (
    FACTS as FACTS,
)
from wgsextract_cli.core.sample_facts import (
    OVERRIDABLE as OVERRIDABLE,
)
from wgsextract_cli.core.sample_facts import (
    PERSON_FACTS as PERSON_FACTS,
)
from wgsextract_cli.core.sample_facts import (
    PLATFORMS as PLATFORMS,
)
from wgsextract_cli.core.sample_facts import (
    SEXES as SEXES,
)
from wgsextract_cli.core.sample_facts import (
    Fact as Fact,
)
from wgsextract_cli.core.sample_facts import (
    FactSpec as FactSpec,
)
from wgsextract_cli.core.sample_facts import (
    Source as Source,
)
from wgsextract_cli.core.sample_facts import (
    detected_fact,
)
from wgsextract_cli.core.sample_state import WorkspaceState, table
from wgsextract_cli.core.sample_types import (
    SCHEMA_ID as SCHEMA_ID,
)
from wgsextract_cli.core.sample_types import (
    Confidence as Confidence,
)
from wgsextract_cli.core.sample_types import (
    FactName,
    ObservedPlatform,
    ObservedSex,
    OverrideChanges,
    Sex,
    Status,
    Superpopulation,
    is_platform,
    is_sex,
)
from wgsextract_cli.core.sample_view import (
    FactView as FactView,
)
from wgsextract_cli.core.sample_view import (
    FileStatus,
    FileView,
    WorkspaceView,
)
from wgsextract_cli.core.sample_workspace_format import (
    SCHEMA_RESOURCE as SCHEMA_RESOURCE,
)
from wgsextract_cli.core.sample_workspace_format import (
    WORKSPACE_FILE as WORKSPACE_FILE,
)
from wgsextract_cli.core.sample_workspace_format import (
    load_schema as load_schema,
)
from wgsextract_cli.core.sample_workspace_format import (
    read_workspace,
)
from wgsextract_cli.core.sample_workspace_format import (
    render as render,
)
from wgsextract_cli.core.sample_workspace_format import (
    tomllib as tomllib,
)
from wgsextract_cli.core.step_identity import file_fingerprint as file_fingerprint

if TYPE_CHECKING:
    from wgsextract_cli.core.sample_document import (
        FileOverride,
        Overrides,
        RecordedFact,
        WorkspaceOutput,
    )

_LOCK_TIMEOUT = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _by() -> str:
    from wgsextract_cli.core.results import current_result

    command = current_result().command
    return f"wgsextract {__version__}" + (f" {command}" if command else "")


# --- the workspace -------------------------------------------------------------

_warned: set[str] = set()
_unwritable: set[Path] = set()


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        logging.warning(message)


class SampleWorkspace:
    """One sample folder and its ``wgsextract.sample.toml``."""

    def __init__(self, folder: PathInput) -> None:
        self.folder = absolute_path(folder)
        self.path = self.folder / WORKSPACE_FILE

    def __repr__(self) -> str:
        return f"SampleWorkspace({str(self.folder)!r})"

    def key(self, file: PathInput) -> str:
        """How ``file`` is named in [detected]: relative to the folder, '/'
        separated, or absolute when it lies elsewhere."""
        path = absolute_path(file)
        try:
            return path.relative_to(self.folder).as_posix()
        except ValueError:
            return str(path)

    def file_path(self, key: str) -> Path:
        path = Path(key)
        return path if path.is_absolute() else self.folder / path

    # --- reading ---------------------------------------------------------------

    def _load(self) -> WorkspaceState:
        doc = read_workspace(self.path)
        if doc.problem and not doc.writable:
            _warn_once(
                f"Ignoring {self.path}: {doc.problem}. wgsextract won't change it; "
                "fix or delete it to record sample facts again."
            )
        return doc

    @property
    def is_genome_folder(self) -> bool:
        """Whether the folder holds one person's files: the --genome folder, a
        My Genomes library entry, or a folder with genome-config.toml."""
        from wgsextract_cli.core.config import mygenomes_library
        from wgsextract_cli.core.genome_library import GENOME_CONFIG_NAME

        active = current_workspace()
        if active is not None and active.folder == self.folder:
            return True
        if (self.folder / GENOME_CONFIG_NAME).is_file():
            return True
        root = absolute_path(mygenomes_library())
        return self.folder != root and self.folder.is_relative_to(root)

    def _valid(self, name: str, value: object) -> str | None:
        spec = FACTS.get(name)
        if spec is None or not spec.overridable:
            _warn_once(f"{self.path}: ignoring unknown override {name!r}.")
            return None
        try:
            return spec.normalize(value)
        except ValueError as e:
            _warn_once(f"{self.path}: ignoring override: {e}.")
            return None

    def folder_overrides(self, doc: WorkspaceState | None = None) -> dict[str, str]:
        """The folder-wide overrides in effect: person facts, in a genome
        folder. Others are reported once and ignored."""
        values = (doc or self._load()).section("override")
        found: dict[str, str] = {}
        for name, value in values.items():
            if isinstance(value, dict):
                continue
            valid = self._valid(name, value)
            if valid is None:
                continue
            if name not in PERSON_FACTS:
                _warn_once(
                    f"{self.path}: ignoring the folder-wide {name} override; a "
                    f"{name} belongs to one file: 'wgsextract sample set --input "
                    f"FILE --{name} {valid}'."
                )
            elif not self.is_genome_folder:
                _warn_once(
                    f"{self.path}: ignoring the folder-wide {name} override: "
                    f"{self.folder} isn't a genome folder, so it may hold several "
                    f"people's files. Set it per file: 'wgsextract sample set "
                    f"--input FILE --{name} {valid}'."
                )
            else:
                found[name] = valid
        return found

    def file_overrides(
        self, file: PathInput, doc: WorkspaceState | None = None
    ) -> dict[str, str]:
        """``file``'s own overrides, while it is the revision they were set on."""
        values = (doc or self._load()).section("override")
        entry = table(values.get(self.key(file)))
        if not entry:
            return {}
        if entry.get("fingerprint") != file_fingerprint(file):
            _warn_once(
                f"{self.path}: ignoring the overrides of {self.key(file)}: the file "
                "changed since they were set. Set them again if they still hold."
            )
            return {}
        return {
            name: valid
            for name, value in entry.items()
            if name != "fingerprint" and (valid := self._valid(name, value))
        }

    def overrides(
        self, file: PathInput | None = None, doc: WorkspaceState | None = None
    ) -> dict[str, str]:
        """The overrides in effect for ``file`` (folder-wide ones without it)."""
        doc = doc or self._load()
        found = self.folder_overrides(doc)
        if file is not None:
            found.update(self.file_overrides(file, doc))
        return found

    def detected(
        self,
        name: str,
        file: PathInput,
        *,
        doc: WorkspaceState | None = None,
    ) -> Fact | None:
        """The fact detected from this very file (same fingerprint) by the
        current detector version."""
        doc = doc or self._load()
        key = self.key(file)
        entry = table(doc.section("detected").get(key))
        if not entry:
            return None
        fact = table(entry.get(name))
        if "value" not in fact:
            return None
        spec = FACTS.get(name)
        if spec is None or fact.get("version") != spec.version:
            return None
        current = file_fingerprint(file)
        if current is None or entry.get("fingerprint") != current:
            return None
        if name == "platform" and fact.get("statistics") is not None:
            from wgsextract_cli.core.alignment.fastq_platform import (
                fastq_evidence_fingerprint,
            )

            try:
                evidence = fastq_evidence_fingerprint(str(absolute_path(file)))
            except OSError:
                return None
            if fact.get("evidence_fingerprint") != evidence:
                return None
        return detected_fact(name, key, entry["fingerprint"], fact)

    def detected_facts(self, name: str) -> list[Fact]:
        """``name`` as detected from each file still as it was, newest first."""
        doc = self._load()
        found = [
            fact
            for key in doc.section("detected")
            if (fact := self.detected(name, self.file_path(key), doc=doc))
        ]
        return sorted(found, key=lambda fact: fact.at or "", reverse=True)

    def fact(self, name: str, file: PathInput | None) -> Fact | None:
        """The fact in effect: the override, else ``file``'s current detection."""
        doc = self._load()
        if file is not None:
            own = self.file_overrides(file, doc).get(name)
            if own is not None:
                return Fact(name, own, "override", file=self.key(file))
        shared = self.folder_overrides(doc).get(name)
        if shared is not None:
            return Fact(name, shared, "override")
        return self.detected(name, file, doc=doc) if file else None

    # --- writing ---------------------------------------------------------------

    def _update(
        self, mutate: Callable[[WorkspaceState], bool], *, strict: bool = False
    ) -> bool:
        """Apply ``mutate`` to the document under the workspace lock; write it
        if it reports a change. Best effort unless ``strict``."""
        from wgsextract_cli.core.atomic_output import atomic_write
        from wgsextract_cli.core.file_lock import exclusive_lock
        from wgsextract_cli.core.utils import WGSExtractError

        if not strict and self.folder in _unwritable:
            return False
        try:
            with exclusive_lock(self.folder / f".{WORKSPACE_FILE}", _LOCK_TIMEOUT):
                doc = self._load()
                if not doc.writable:
                    if strict:
                        raise WGSExtractError(
                            f"Not changing {self.path}: {doc.problem}. Fix or "
                            "delete it first."
                        )
                    return False
                if not mutate(doc):
                    return False
                doc.raw["schema"] = SCHEMA_ID
                atomic_write(self.path, render(doc.raw).encode("utf-8"))
                return True
        except (OSError, WGSExtractError) as e:
            if strict:
                raise WGSExtractError(f"Couldn't update {self.path}: {e}") from e
            _unwritable.add(self.folder)
            logging.debug(f"Not recording sample facts in {self.path}: {e}")
            return False

    @overload
    def record(
        self,
        name: Literal["sex"],
        file: PathInput,
        value: ObservedSex | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    @overload
    def record(
        self,
        name: Literal["somalier_sex"],
        file: PathInput,
        value: Sex | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    @overload
    def record(
        self,
        name: Literal["platform"],
        file: PathInput,
        value: ObservedPlatform | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    @overload
    def record(
        self,
        name: Literal["superpopulation"],
        file: PathInput,
        value: Superpopulation | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    @overload
    def record(
        self,
        name: Literal["build"],
        file: PathInput,
        value: str | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    @overload
    def record(
        self,
        name: Literal["read_length", "mean_coverage", "contamination"],
        file: PathInput,
        value: float | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    @overload
    def record(
        self,
        name: Literal["target_depths", "ancestry"],
        file: PathInput,
        value: dict[str, float] | None,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool: ...

    def record(
        self,
        name: FactName,
        file: PathInput,
        value: object,
        *,
        method: str,
        confidence: Confidence,
    ) -> bool:
        """Record a detected fact for this revision of ``file``."""
        return self._record(name, file, value, method=method, confidence=confidence)

    def _record(
        self,
        name: FactName,
        file: PathInput,
        value: object,
        *,
        method: str,
        confidence: Confidence,
        statistics: dict[str, float] | None = None,
    ) -> bool:
        fingerprint = file_fingerprint(file)
        if fingerprint is None or value is None:
            return False
        key = self.key(file)
        new: RecordedFact = {
            "value": value,
            "method": method,
            "confidence": confidence,
            "version": FACTS[name].version,
            "by": _by(),
            "at": _now(),
        }
        if statistics is not None:
            new["statistics"] = statistics
            if name == "platform":
                from wgsextract_cli.core.alignment.fastq_platform import (
                    fastq_evidence_fingerprint,
                )

                try:
                    new["evidence_fingerprint"] = fastq_evidence_fingerprint(
                        str(absolute_path(file))
                    )
                except OSError:
                    return False

        def mutate(doc: WorkspaceState) -> bool:
            return doc.record(name, key, fingerprint, new)

        return self._update(mutate)

    def set_overrides(
        self, values: OverrideChanges, file: PathInput | None = None
    ) -> dict[str, str]:
        """Set (or, for None, remove) overrides: a person fact folder-wide in a
        genome folder, anything else for ``file`` alone. Returns the overrides
        now in effect for ``file``. Raises on an invalid name or value, a
        missing file, or an unwritable workspace."""
        from wgsextract_cli.core.utils import UsageError

        changes: dict[str, str | None] = {}
        for name, value in values.items():
            spec = FACTS.get(name)
            if spec is None or not spec.overridable:
                raise UsageError(
                    f"{name!r} can't be overridden; choose from "
                    f"{', '.join(OVERRIDABLE)}."
                )
            try:
                changes[name] = None if value is None else spec.normalize(value)
            except ValueError as e:
                raise UsageError(f"{e}.") from None
        shared = {
            name
            for name, value in changes.items()
            if value is not None and name in PERSON_FACTS and self.is_genome_folder
        }
        per_file = {n for n, v in changes.items() if v is not None} - shared
        fingerprint = file_fingerprint(file) if file is not None else None
        if per_file and fingerprint is None:
            name = sorted(per_file)[0]
            why = (
                f"a {name} override belongs to one file"
                if name not in PERSON_FACTS
                else f"{self.folder} isn't a genome folder, so a {name} override "
                "belongs to one file"
            )
            raise UsageError(
                f"Give the file with --input: {why}."
                if file is None
                else f"Can't read {file} to tie the override to it."
            )
        key = self.key(file) if file is not None else None

        shared_values: Overrides = {}
        own_values: FileOverride | None = None
        if per_file:
            assert fingerprint is not None
            own_values = {"fingerprint": fingerprint}
        sex = changes.get("sex")
        if is_sex(sex):
            if "sex" in shared:
                shared_values["sex"] = sex
            elif own_values is not None:
                own_values["sex"] = sex
        platform = changes.get("platform")
        if is_platform(platform) and own_values is not None:
            own_values["platform"] = platform
        removed = set(changes) - shared - per_file

        def mutate(doc: WorkspaceState) -> bool:
            return doc.override(key, shared_values, own_values, removed)

        self._update(mutate, strict=True)
        return self.overrides(file)

    def record_output(self, manifest: PathInput, command: str, status: Status) -> bool:
        """Index a result manifest written in this workspace."""
        key = self.key(manifest)
        entry: WorkspaceOutput = {"command": command, "status": status, "at": _now()}

        def mutate(doc: WorkspaceState) -> bool:
            retained = {k for k in doc.section("outputs") if self.file_path(k).exists()}
            doc.output(key, entry, retained)
            return True

        return self._update(mutate)

    def recorded_manifests(self) -> list[Path]:
        """The result manifests ``record_output`` indexed here."""
        return [self.file_path(key) for key in self._load().section("outputs")]

    # --- reporting -------------------------------------------------------------

    def _status(self, key: str, entry: Mapping[str, object]) -> FileStatus:
        """Whether the file ``entry`` describes is unchanged, changed or gone."""
        current = file_fingerprint(self.file_path(key))
        if current is None:
            return "missing"
        return "current" if current == entry.get("fingerprint") else "changed"

    def describe(self, file: PathInput | None = None) -> WorkspaceView:
        """The workspace as JSON-ready data, for ``sample show`` and the API."""
        doc = self._load()
        files: dict[str, FileView] = {}
        for key, value in doc.section("detected").items():
            if not isinstance(value, dict):
                continue
            entry = table(value)
            files[key] = {
                "status": self._status(key, entry),
                "fingerprint": entry.get("fingerprint"),
                "facts": {
                    name: detected_fact(
                        name, key, entry.get("fingerprint"), table(fact)
                    ).to_dict()
                    for name, fact in entry.items()
                    if isinstance(fact, dict) and "value" in fact
                },
            }
        for key, value in doc.section("override").items():
            if not isinstance(value, dict):
                continue
            entry = table(value)
            listed = files.setdefault(
                key, {"facts": {}, "status": self._status(key, entry)}
            )
            listed["overrides"] = {
                "status": self._status(key, entry),
                "fingerprint": entry.get("fingerprint"),
                "values": {k: v for k, v in entry.items() if k != "fingerprint"},
            }
            listed.setdefault("status", listed["overrides"]["status"])
        data: WorkspaceView = {
            "schema": SCHEMA_ID,
            "workspace": str(self.folder),
            "path": str(self.path),
            "exists": doc.exists,
            "problem": doc.problem,
            "genome_folder": self.is_genome_folder,
            "overrides": self.folder_overrides(doc),
            "files": files,
            "outputs": {
                key: table(value) for key, value in doc.section("outputs").items()
            },
        }
        if file is not None:
            data["file"] = self.key(file)
            data["effective"] = {
                name: fact.to_dict()
                for name in FACTS
                if (fact := self.fact(name, file)) is not None
            }
        return data
