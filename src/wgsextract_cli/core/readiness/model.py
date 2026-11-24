"""What ``deps ready`` reports: one item per prerequisite of a command line,
and whether the command can run on this computer without the network.

An item's ``status`` says how far it was checked:

- ``runnable``: present, and it ran or matched what it must be (a tool
  reported its version, a model or weights matched their pinned SHA-256, a
  Docker image is in the local engine that answered);
- ``installed``: present, but nothing more could be checked offline;
- ``unverified``: present, but its integrity or fit can't be confirmed (a
  model without a pinned digest, a reference whose fit can't be read);
- ``unsupported``: this platform, backend or an offline run can't have it;
- ``missing``: not there; ``stale``: an index older than its file;
- ``incompatible``: there but wrong (a digest mismatch, a reference the
  input wasn't aligned to, a broken tool).

A ``required`` item whose status is ``missing``, ``stale``, ``unsupported``
or ``incompatible`` blocks the run; the others are advisory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from wgsextract_cli.core.download_progress import redact_urls
from wgsextract_cli.core.utils import MissingDependencyError, UsageError

RUNNABLE = "runnable"
INSTALLED = "installed"
UNVERIFIED = "unverified"
UNSUPPORTED = "unsupported"
MISSING = "missing"
STALE = "stale"
INCOMPATIBLE = "incompatible"

BLOCKING = frozenset({UNSUPPORTED, MISSING, STALE, INCOMPATIBLE})

READY = "ready"
READY_UNVERIFIED = "ready_unverified"
NOT_READY = "not_ready"

# Terms are read, never inferred: "recorded" when the installed component's
# own metadata states them (``source`` says which), "unknown" when nothing
# local does. Neither is a grant to redistribute it.
TERMS_RECORDED = "recorded"
TERMS_UNKNOWN = "unknown"
TERMS_NOTICE = (
    "Licences are as each installed component's own metadata records them; "
    "unknown means nothing installed here records them. Installing, verifying "
    "or signing a component doesn't grant a right to redistribute it."
)


def status_of(error: Exception) -> str:
    """The status of an item whose check raised ``error``: a usage error (an
    option or platform the command refuses) is unsupported, a missing
    dependency missing, anything else there but wrong."""
    if isinstance(error, UsageError):
        return UNSUPPORTED
    if isinstance(error, MissingDependencyError):
        return MISSING
    return INCOMPATIBLE


def terms(licence: str | None = None, source: str | None = None) -> dict[str, Any]:
    """Terms evidence: ``licence`` as ``source`` states it, else unknown."""
    status = TERMS_RECORDED if licence else TERMS_UNKNOWN
    return {"status": status, "licence": licence, "source": source if licence else None}


@dataclass
class Item:
    """One prerequisite and what was found."""

    # tool, platform, network, backend, gpu, image, model, input, reference,
    # index or annotation
    kind: str
    name: str
    status: str
    required: bool = True
    detail: str = ""
    path: str | None = None
    version: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    # The `wgsextract` arguments of the explicit step that provides it, and
    # what to do where no command does.
    fix_arguments: list[str] | None = None
    fix_text: str | None = None

    @property
    def blocking(self) -> bool:
        return self.required and self.status in BLOCKING

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": _redacted(self.name),
            "status": self.status,
            "required": self.required,
            "blocking": self.blocking,
            "detail": _redacted(self.detail),
            "path": _redacted(self.path),
            "version": self.version,
            "evidence": _redacted(self.evidence),
            "fix_arguments": _redacted(self.fix_arguments),
            "fix_text": _redacted(self.fix_text),
        }


def _redacted(value: Any) -> Any:
    """URL credentials and signed queries hidden, as run records hide them."""
    if isinstance(value, str):
        return redact_urls(value) if "://" in value else value
    if isinstance(value, Mapping):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(item) for item in value]
    return value


@dataclass
class Report:
    """The readiness of one command line."""

    command: str
    command_line: list[str]
    host: dict[str, str]
    items: list[Item] = field(default_factory=list)

    def add(self, *items: Item | None) -> None:
        self.items.extend(item for item in items if item is not None)

    @property
    def status(self) -> str:
        if any(item.blocking for item in self.items):
            return NOT_READY
        if any(item.status == UNVERIFIED for item in self.items):
            return READY_UNVERIFIED
        return READY

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "command_line": self.command_line,
            "host": self.host,
            "status": self.status,
            "ready": self.status != NOT_READY,
            "blocking": [_redacted(item.name) for item in self.items if item.blocking],
            "items": [item.as_dict() for item in self.items],
            "notices": [TERMS_NOTICE],
        }
