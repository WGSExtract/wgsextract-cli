"""Pinned previous-release output-contract baselines: pin, load and verify.

A baseline records a release's classified output surface with the tag, its
commit, the git blobs it was built from and a digest. Verification reads only
local git objects; nothing here reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from scripts.output_contract_surface import Finding, classify, flatten

REGISTRY = "contracts/output-contracts.json"
BASELINES = "contracts/baselines"
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
Reader = Callable[[str], bytes | None]


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(baseline: Mapping[str, Any]) -> str:
    body = {key: value for key, value in baseline.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def git(root: Path, *args: str) -> bytes | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=False
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def version(release: str) -> tuple[int, ...]:
    match = RELEASE.match(release)
    if not match:
        raise ValueError(f"Not a release tag: {release!r} (expected vX.Y.Z)")
    return tuple(int(part) for part in match.groups())


def worktree(root: Path) -> Reader:
    def read(path: str) -> bytes | None:
        file = root / path
        return file.read_bytes() if file.is_file() else None

    return read


def at_revision(root: Path, revision: str) -> Reader:
    return lambda path: git(root, "show", f"{revision}:{path}")


def surfaces(
    registry: Mapping[str, Any], read: Reader, *, keep_internal: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Classified surfaces of every registered document that exists."""
    documents: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, str]] = {}
    for name, entry in registry["documents"].items():
        data = read(entry["schema"])
        if data is None:
            continue
        sources[name] = {"path": entry["schema"], "git_blob": git_blob_id(data)}
        if entry["tier"] == "internal" and not keep_internal:
            documents[name] = {"tier": "internal", "paths": {}}
            continue
        paths = classify(
            flatten(json.loads(data)), entry["tier"], entry.get("fields", {})
        )
        documents[name] = {
            "tier": entry["tier"],
            "paths": {
                path: node
                for path, node in paths.items()
                if keep_internal or node["tier"] != "internal"
            },
        }
    return documents, sources


def build_baseline(
    release: str, commit: str, registry: Mapping[str, Any], read: Reader, tiers: str
) -> dict[str, Any]:
    documents, sources = surfaces(registry, read, keep_internal=False)
    baseline: dict[str, Any] = {
        "format": 1,
        "release": release,
        "commit": commit,
        "tiers_source": tiers,
        "sources": sources,
        "registry": registry,
        "documents": documents,
        "roles": registry.get("roles", {}),
    }
    baseline["digest"] = digest(baseline)
    return baseline


def load_baselines(texts: Mapping[str, bytes]) -> dict[str, dict[str, Any]]:
    return {Path(name).stem: json.loads(text) for name, text in texts.items()}


def worktree_baselines(root: Path) -> dict[str, bytes]:
    folder = root / BASELINES
    return {file.name: file.read_bytes() for file in sorted(folder.glob("*.json"))}


def revision_baselines(root: Path, revision: str) -> dict[str, bytes]:
    listing = git(root, "ls-tree", "--name-only", f"{revision}:{BASELINES}") or b""
    names = [name for name in listing.decode().splitlines() if name.endswith(".json")]
    return {
        name: git(root, "show", f"{revision}:{BASELINES}/{name}") or b""
        for name in names
    }


def provenance(root: Path, baseline: Mapping[str, Any]) -> tuple[bool, str] | None:
    """None if the baseline regenerates from its tag's files; else whether the
    tag is merely absent here (a shallow clone) and why it was not verified."""
    release, commit = str(baseline.get("release")), str(baseline.get("commit"))
    tagged = git(
        root, "rev-parse", "--verify", "--quiet", f"refs/tags/{release}^{{commit}}"
    )
    if tagged is None or git(root, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        return True, f"tag {release} or commit {commit[:12]} is not in this repository"
    try:
        problem = _regenerates(root, baseline, tagged)
    except (KeyError, TypeError, ValueError) as error:
        problem = f"it is malformed ({error})"
    return None if problem is None else (False, problem)


def _regenerates(root: Path, baseline: Mapping[str, Any], tagged: bytes) -> str | None:
    release, commit = baseline["release"], baseline["commit"]
    if tagged.decode().strip() != commit:
        return f"tag {release} is {tagged.decode().strip()[:12]}, not the recorded {commit[:12]}"
    read = at_revision(root, commit)
    tagged_registry = read(REGISTRY)
    if baseline["tiers_source"] == "bootstrap":
        # Only a release from before the registry existed may bring its tiers.
        if tagged_registry is not None:
            return f"{release} has {REGISTRY}, so its tiers must come from it, not bootstrap"
    elif baseline["tiers_source"] != "release":
        return f"tiers_source {baseline['tiers_source']!r} is neither release nor bootstrap"
    elif tagged_registry is None or json.loads(tagged_registry) != baseline["registry"]:
        return f"its registry is not {release}'s {REGISTRY}"
    regenerated = build_baseline(
        release, commit, baseline["registry"], read, baseline["tiers_source"]
    )
    if regenerated != baseline:
        differing = sorted(
            key for key in regenerated if regenerated[key] != baseline.get(key)
        )
        return f"it does not regenerate from {release}'s files (differs in {', '.join(differing)})"
    return None


def effective_baselines(
    root: Path, base: str | None
) -> tuple[dict[str, dict[str, Any]], list[Finding]]:
    """Baselines to compare against, preferring the base's untouched copies."""
    found: list[Finding] = []
    head = worktree_baselines(root)
    chosen = dict(head)
    trusted: set[str] = set()
    if base is not None:
        if git(root, "cat-file", "-e", f"{base}^{{commit}}") is None:
            raise SystemExit(f"Base revision {base} is not in the local repository.")
        for name, text in revision_baselines(root, base).items():
            trusted.add(name)
            chosen[name] = text
            if name not in head:
                found.append(
                    Finding(
                        "error",
                        "baseline_removed",
                        "baselines",
                        name,
                        Path(name).stem,
                        f"{BASELINES}/{name} was deleted; pinned baselines are immutable.",
                    )
                )
            elif json.loads(head[name]) != json.loads(text):
                found.append(
                    Finding(
                        "error",
                        "baseline_modified",
                        "baselines",
                        name,
                        Path(name).stem,
                        f"{BASELINES}/{name} was edited; pinned baselines are immutable, "
                        "so outputs are still compared with the base's copy.",
                    )
                )
    baselines = load_baselines(chosen)
    for name, baseline in baselines.items():
        file = f"{name}.json"
        release = baseline.get("release", "")
        if release != name or not RELEASE.match(name):
            found.append(
                Finding(
                    "error",
                    "baseline_invalid",
                    "baselines",
                    file,
                    name,
                    f"{BASELINES}/{file} must be named after its vX.Y.Z release.",
                )
            )
        if baseline.get("digest") != digest(baseline):
            found.append(
                Finding(
                    "error",
                    "baseline_tampered",
                    "baselines",
                    file,
                    name,
                    f"{BASELINES}/{file} does not match its recorded digest.",
                )
            )
        if file in trusted:
            continue
        outcome = provenance(root, baseline)
        if outcome is not None:
            absent, problem = outcome
            strict = base is not None or not absent
            found.append(
                Finding(
                    "error" if strict else "info",
                    "baseline_unverified" if strict else "baseline_provenance_skipped",
                    "baselines",
                    file,
                    name,
                    f"{BASELINES}/{file} was not verified: {problem}. "
                    + (
                        "Record baselines only with `scripts/output_contracts.py pin`."
                        if strict
                        else ""
                    ),
                )
            )
    valid = {name: b for name, b in baselines.items() if RELEASE.match(name)}
    bootstraps = sorted(
        n for n, b in valid.items() if b.get("tiers_source") == "bootstrap"
    )
    oldest = min(valid, key=version, default=None)
    for name in bootstraps:
        if name != oldest or len(bootstraps) > 1:
            found.append(
                Finding(
                    "error",
                    "baseline_invalid",
                    "baselines",
                    f"{name}.json",
                    name,
                    f"{BASELINES}/{name}.json bootstraps its tiers, which only the "
                    "first (oldest) baseline may do.",
                )
            )
    return valid, found


def newest(baselines: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The previous release's baseline: the one every check compares with."""
    return max(baselines.values(), key=lambda b: version(b["release"]), default=None)


def pin(root: Path, release: str) -> Path:
    """Record the release's contract as an immutable baseline (maintainer step)."""
    version(release)
    target = root / BASELINES / f"{release}.json"
    if target.exists():
        raise SystemExit(
            f"{target.relative_to(root)} exists; pinned baselines are immutable."
        )
    tagged = git(
        root, "rev-parse", "--verify", "--quiet", f"refs/tags/{release}^{{commit}}"
    )
    if tagged is None:
        raise SystemExit(
            f"Tag {release} is not in the local repository; fetch it first "
            f"(git fetch origin tag {release})."
        )
    commit = tagged.decode().strip()
    read = at_revision(root, commit)
    recorded = read(REGISTRY)
    if recorded is not None:
        registry, tiers = json.loads(recorded), "release"
    elif worktree_baselines(root):
        raise SystemExit(
            f"{release} predates {REGISTRY}; only the first baseline may bootstrap tiers."
        )
    else:
        registry, tiers = (
            json.loads((root / REGISTRY).read_text(encoding="utf-8")),
            "bootstrap",
        )
    baseline = build_baseline(release, commit, registry, read, tiers)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(baseline), encoding="utf-8", newline="\n")
    return target


def render(baseline: Mapping[str, Any]) -> str:
    """Pretty JSON with one line per path, so a baseline stays reviewable."""
    nodes: list[str] = []

    def placeholder(node: object) -> str:
        nodes.append(canonical(node))
        return f"\0{len(nodes) - 1}\0"

    documents = {
        name: {
            **document,
            "paths": {
                path: placeholder(node) for path, node in document["paths"].items()
            },
        }
        for name, document in baseline["documents"].items()
    }
    text = json.dumps({**baseline, "documents": documents}, indent=1, sort_keys=True)
    text = re.sub(r'"\\u0000(\d+)\\u0000"', lambda match: nodes[int(match[1])], text)
    return text + "\n"
