"""Check output contracts against the pinned previous-release baseline.

``check`` compares the committed schemas and tier registry with the newest
baseline in contracts/baselines/. ``check --base REV`` (CI) also compares
against the baselines as they are at REV, so a change that edits a baseline
cannot also erase the comparison. ``pin --release vX.Y.Z`` is the maintainer
release step that records a new baseline from the tag's own files. ``instance``
checks real output documents. Nothing here reaches the network: provenance
comes from local git objects only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.output_contract_baselines import (  # noqa: E402
    BASELINES,
    REGISTRY,
    effective_baselines,
    newest,
    pin,
    surfaces,
    worktree,
)
from scripts.output_contract_surface import (  # noqa: E402
    ACTIONS,
    TIERS,
    Finding,
    ancestors,
    check_instance,
    compare,
    covers,
)

ROOT = Path(__file__).resolve().parents[1]
APPROVAL = re.compile(r"#\d+|https://github\.com/\S+/(issues|pull)/\d+")


def validate_registry(
    root: Path, registry: Mapping[str, Any], current: Mapping[str, Mapping[str, Any]]
) -> list[Finding]:
    """The registry must describe real paths and keep its own promises."""
    found: list[Finding] = []

    def error(code: str, document: str, path: str, message: str) -> None:
        found.append(Finding("error", code, document, path, "", message))

    for name, entry in registry["documents"].items():
        if entry.get("tier") not in TIERS:
            error(
                "invalid_tier",
                name,
                "",
                f"{name} has tier {entry.get('tier')!r}; use one of {TIERS}.",
            )
            continue
        if name not in current:
            error(
                "contract_schema_missing",
                name,
                "",
                f"{name}'s schema {entry['schema']} does not exist.",
            )
            continue
        if entry["tier"] == "internal":
            consumers = entry.get("consumers") or []
            missing = [path for path in consumers if not (root / path).is_file()]
            if not consumers or missing:
                error(
                    "internal_consumers_undeclared",
                    name,
                    "",
                    f"Internal {name} must list the in-repository modules that read it "
                    f"('consumers'); missing: {missing or 'none listed'}.",
                )
            continue
        paths = current[name]["paths"]
        fields = entry.get("fields", {})
        for path, field in fields.items():
            if path not in paths:
                error(
                    "unknown_contract_path",
                    name,
                    path,
                    f"`{name}:{path}` is classified but not in the schema; remove or rename the entry.",
                )
            elif field.get("tier", entry["tier"]) not in TIERS:
                error(
                    "invalid_tier",
                    name,
                    path,
                    f"`{name}:{path}` has tier {field.get('tier')!r}.",
                )
        for path, node in paths.items():
            if node["tier"] != "stable":
                continue
            weaker = [
                a
                for a in ancestors(path)
                if a in paths and paths[a]["tier"] != "stable"
            ]
            if weaker:
                error(
                    "stable_under_unstable",
                    name,
                    path,
                    f"Stable `{name}:{path}` sits under {paths[weaker[0]]['tier']} `{weaker[0]}`; "
                    "a stable field needs stable parents.",
                )
            if node.get("scientific"):
                owners = [
                    key
                    for key in fields
                    if covers(key, path) and fields[key].get("validation")
                ]
                evidence = fields[owners[0]]["validation"] if owners else None
                if not evidence or not (root / evidence).exists():
                    error(
                        "unvalidated_scientific_result",
                        name,
                        path,
                        f"`{name}:{path}` is a scientific result marked stable without validation "
                        "evidence; a stable JSON shape does not make its meaning stable. Keep it "
                        "experimental, or add 'validation' naming the validating doc or test.",
                    )
    for role, entry in registry.get("roles", {}).items():
        if entry.get("tier") not in TIERS:
            error(
                "invalid_tier",
                "roles",
                role,
                f"Role {role!r} has tier {entry.get('tier')!r}.",
            )
        elif entry["tier"] == "stable" and not entry.get("meaning"):
            error(
                "stable_role_without_meaning",
                "roles",
                role,
                f"Stable role {role!r} needs a 'meaning'.",
            )
    for entry in registry.get("approved_breaks", []):
        if (
            not {"document", "path", "code", "approval", "reason"} <= set(entry)
            or not APPROVAL.search(str(entry.get("approval", "")))
            or not approvable(str(entry.get("code", "")))
        ):
            error(
                "approved_break_invalid",
                str(entry.get("document", "")),
                str(entry.get("path", "")),
                "An approved break needs document, path, a stable_* or experimental_* "
                "contract finding code, reason and the owner's approval (an issue or "
                "PR reference); baseline integrity findings cannot be approved.",
            )
    return found


def produced_roles(root: Path) -> str:
    return "\n".join(
        file.read_text(encoding="utf-8", errors="replace")
        for file in sorted((root / "src/wgsextract_cli").rglob("*.py"))
    )


def compare_roles(
    current: Mapping[str, Mapping[str, Any]], baseline: Mapping[str, Any], sources: str
) -> Iterable[Finding]:
    release = baseline["release"]
    for role, before in baseline["roles"].items():
        tier = before["tier"]
        if tier == "internal":
            continue
        after = current.get(role)
        level = "error" if tier == "stable" else "notice"
        key = f"role:{role}"
        action = (
            "Restore it or follow the breaking-change procedure in docs/output-contracts.md."
            if tier == "stable"
            else f"Announce it: name `{key}` in docs/release-notes/unreleased.md."
        )
        if after is None:
            yield Finding(
                level,
                f"{tier}_role_removed",
                "roles",
                role,
                release,
                f"{tier.capitalize()} output role `{key}` was removed since {release}. {action}",
            )
        elif tier == "stable" and after.get("tier") != "stable":
            yield Finding(
                level,
                "stable_role_downgraded",
                "roles",
                role,
                release,
                f"Stable output role `{key}` was demoted to {after.get('tier')}. {action}",
            )
        elif tier == "stable" and " ".join(
            str(after.get("meaning")).split()
        ) != " ".join(str(before.get("meaning")).split()):
            yield Finding(
                level,
                "stable_role_meaning_changed",
                "roles",
                role,
                release,
                f"Stable output role `{key}` changed meaning since {release}. {action}",
            )
    for role, entry in current.items():
        if entry.get("tier") == "stable" and not re.search(
            rf"[\"']{re.escape(role)}[\"']", sources
        ):
            yield Finding(
                "error",
                "stable_role_unproduced",
                "roles",
                role,
                release,
                f"No source under src/wgsextract_cli writes stable output role `role:{role}`.",
            )


def release_notes(root: Path) -> str:
    return "\n".join(
        file.read_text(encoding="utf-8")
        for file in sorted((root / "docs/release-notes").rglob("*.md"))
    )


def check(root: Path = ROOT, base: str | None = None) -> list[Finding]:
    registry = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    current, _ = surfaces(registry, worktree(root), keep_internal=True)
    found = validate_registry(root, registry, current)
    baselines, problems = effective_baselines(root, base)
    found += problems
    if not baselines:
        found.append(
            Finding(
                "error",
                "baseline_missing",
                "baselines",
                "",
                "",
                f"No pinned baseline in {BASELINES}; pin the previous release "
                "with `scripts/output_contracts.py pin --release vX.Y.Z`.",
            )
        )
        return found
    latest = newest(baselines)
    assert latest is not None
    release = latest["release"]
    for name in sorted(set(latest["documents"]) | set(current)):
        before = latest["documents"].get(name)
        if before is None:
            found.append(
                Finding(
                    "info",
                    "document_added",
                    name,
                    "",
                    release,
                    f"{name} is new since {release}.",
                )
            )
        elif name in current:
            found += compare(name, current[name]["paths"], before["paths"], release)
        elif before["tier"] != "internal":
            tier = before["tier"]
            found.append(
                Finding(
                    "error" if tier == "stable" else "notice",
                    f"{tier}_document_removed",
                    name,
                    "",
                    release,
                    f"{tier.capitalize()} {name} was removed since {release}. "
                    + ACTIONS[tier].format(key=f"{name}:"),
                )
            )
    found += compare_roles(registry.get("roles", {}), latest, produced_roles(root))
    return settle(found, registry.get("approved_breaks", []), release_notes(root))


def approvable(code: str) -> bool:
    """Contract changes can be approved; baseline integrity never can."""
    return code.startswith(("stable_", "experimental_")) and not code.endswith(
        ("_in_output", "_unproduced")
    )


def settle(
    found: list[Finding], approved: list[Mapping[str, Any]], notes: str
) -> list[Finding]:
    """Apply owner-approved breaks and the experimental notice rule."""
    used: set[int] = set()
    settled = []
    for item in found:
        match = next(
            (
                index
                for index, entry in enumerate(approved)
                if (entry.get("document"), entry.get("path"), entry.get("code"))
                == (item.document, item.path, item.code)
            ),
            None,
        )
        if (
            match is not None
            and item.level in {"error", "notice"}
            and approvable(item.code)
        ):
            used.add(match)
            item = Finding(
                "approved",
                item.code,
                item.document,
                item.path,
                item.release,
                f"{item.message} Approved: {approved[match]['approval']}.",
            )
        elif item.level == "notice":
            key = f"role:{item.path}" if item.document == "roles" else item.key()
            if f"`{key}`" not in notes:
                item = Finding(
                    "error",
                    item.code,
                    item.document,
                    item.path,
                    item.release,
                    item.message,
                )
        settled.append(item)
    for index, entry in enumerate(approved):
        if index not in used:
            settled.append(
                Finding(
                    "error",
                    "stale_approved_break",
                    str(entry.get("document")),
                    str(entry.get("path")),
                    "",
                    "This approved break matches no current finding; remove it.",
                )
            )
    return settled


def check_output(root: Path, document: Mapping[str, Any]) -> list[Finding]:
    """Check one real output document (a manifest, --json or API result)."""
    name = str(document.get("schema", ""))
    baselines, _ = effective_baselines(root, None)
    baseline = newest(baselines)
    if baseline is None or name not in baseline["documents"]:
        return [
            Finding(
                "error",
                "unknown_document",
                name,
                "",
                "",
                f"No baseline describes {name!r}.",
            )
        ]
    release = baseline["release"]
    found = check_instance(name, document, baseline, release)
    if name == "wgsextract.result/1" and document.get("status") == "ok":
        seen = {
            record.get("role")
            for key in ("inputs", "outputs")
            for record in document.get(key, [])
        }
        for role, entry in baseline["roles"].items():
            if (
                entry["tier"] == "stable"
                and document.get("command") in entry.get("commands", [])
                and role not in seen
            ):
                found.append(
                    Finding(
                        "error",
                        "stable_role_missing_in_output",
                        name,
                        role,
                        release,
                        f"`{document.get('command')}` no longer records stable role `role:{role}`.",
                    )
                )
    return found


def report(found: list[Finding], *, as_json: bool, verbose: bool) -> int:
    errors = [item for item in found if item.level == "error"]
    if as_json:
        print(json.dumps([item.as_dict() for item in found], indent=1))
    else:
        for item in found:
            if verbose or item.level in {"error", "notice", "approved"}:
                print(f"{item.level.upper()} {item.code}: {item.message}")
        counts = {
            level: sum(item.level == level for item in found)
            for level in ("error", "notice", "approved", "info")
        }
        print(
            "Output contracts: "
            + ", ".join(f"{n} {level}" for level, n in counts.items())
        )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser(
        "check", help="Compare contracts with the pinned baseline."
    )
    checking.add_argument(
        "--base", help="Also compare with the baselines at this revision (CI)."
    )
    checking.add_argument("--json", action="store_true")
    checking.add_argument(
        "--verbose", action="store_true", help="Also list additive changes."
    )
    pinning = commands.add_parser(
        "pin", help="Record a release's contract (maintainer step)."
    )
    pinning.add_argument("--release", required=True)
    outputs = commands.add_parser("instance", help="Check real output documents.")
    outputs.add_argument("files", nargs="+", type=Path)
    outputs.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "pin":
        target = pin(root, args.release)
        baseline = json.loads(target.read_text(encoding="utf-8"))
        print(
            f"Pinned {target.relative_to(root)}: {baseline['release']} {baseline['commit']} {baseline['digest']}"
        )
        return 0
    if args.command == "instance":
        found = [
            item
            for file in args.files
            for item in check_output(root, json.loads(file.read_text(encoding="utf-8")))
        ]
        return report(found, as_json=args.json, verbose=True)
    return report(check(root, args.base), as_json=args.json, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
