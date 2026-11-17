"""Content identities for small mutable scientific inputs, not genome files."""

import hashlib
from pathlib import Path


def resource_identity(
    path: str | Path | None, *, include_path: bool = True
) -> dict[str, str] | None:
    if path is None:
        return None
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except FileNotFoundError:
        return {"path": str(resolved)} if include_path else {}
    return ({"path": str(resolved)} if include_path else {}) | {
        "sha256": digest.hexdigest()
    }
