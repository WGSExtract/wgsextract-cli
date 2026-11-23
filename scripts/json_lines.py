"""Write JSON compactly, but with one line per entry of chosen objects.

Large generated documents stay small, yet a change to one entry touches only
its own line, so independent changes merge cleanly in git (issue #1416).
"""

from __future__ import annotations

import json
from collections.abc import Iterable

KeyPath = tuple[str, ...]


def _compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def dump_lines(document: object, expand: Iterable[KeyPath] = ()) -> str:
    """Serialise ``document`` with each object on an ``expand`` key path, and
    every object enclosing one (the root included), written one entry per line.

    Everything else stays compact. Key order is kept, so parsing the result
    gives back the same document.
    """
    paths = tuple(expand)

    def dump(value: object, at: KeyPath) -> str:
        if not (
            isinstance(value, dict)
            and value
            and any(path[: len(at)] == at for path in paths)
        ):
            return _compact(value)
        entries = ",\n".join(
            f"{_compact(key)}:{dump(item, (*at, key))}" for key, item in value.items()
        )
        return "{\n" + entries + "\n}"

    return dump(document, ()) + "\n"
