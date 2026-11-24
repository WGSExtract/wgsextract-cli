"""`sample show|set|unset`: a sample's workspace, wgsextract.sample.toml."""

from wgsextract_cli.api.sample import (
    SampleSetParams,
    SampleShowParams,
    SampleUnsetParams,
)
from wgsextract_cli.cli.registration.sample import register as register
from wgsextract_cli.core.builds import display_build
from wgsextract_cli.core.results import current_result, human_output
from wgsextract_cli.core.sample_types import OverrideChanges
from wgsextract_cli.core.sample_workspace import (
    FACTS,
    SampleWorkspace,
)

_LABEL_WIDTH = 15


def _value(value: object) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items())
    return str(value)


def _report(params: SampleShowParams | SampleSetParams | SampleUnsetParams) -> None:
    workspace = SampleWorkspace(params.folder)
    data = workspace.describe(params.input)
    current_result().set_details(data)
    human_output(f"Sample workspace: {data['path']}")
    if data["problem"]:
        human_output(f"  Ignored: {data['problem']}")
    elif not data["exists"]:
        human_output("  Nothing recorded yet.")
    overrides = data["overrides"]
    if overrides:
        human_output(
            "Overrides for every file: "
            + ", ".join(f"{k} = {v}" for k, v in overrides.items())
        )
    elif not any("overrides" in entry for entry in data["files"].values()):
        human_output("Overrides: none (set with 'wgsextract sample set --sex Female')")
    if "effective" in data:
        human_output(f"\nIn effect for {data['file']}:")
        effective = data["effective"]
        for name in FACTS:
            fact = effective.get(name)
            if fact is None:
                continue
            source = (
                "your override"
                if fact["source"] == "override"
                else ", ".join(
                    x for x in (fact.get("method"), fact.get("confidence")) if x
                )
                + (f"; {fact['at'][:10]}" if fact.get("at") else "")
            )
            shown = (
                display_build(str(fact["value"])) or str(fact["value"])
                if name == "build"
                else _value(fact["value"])
            )
            human_output(
                f"  {FACTS[name].name.replace('_', ' ').capitalize():<{_LABEL_WIDTH}}"
                f"{shown}  ({source})"
            )
        if not effective:
            human_output("  nothing recorded; run 'wgsextract info' to detect it.")
    if data["files"]:
        human_output("\nRecorded files:")
        for key, entry in data["files"].items():
            note = {
                "current": "",
                "changed": " (file changed since; re-detected on next use)",
                "missing": " (file not found)",
            }[entry["status"]]
            line = ", ".join(entry["facts"]) or "no detected facts"
            own = entry.get("overrides")
            if own:
                values = ", ".join(f"{k} = {v}" for k, v in own["values"].items())
                stale = (
                    " (file changed since; ignored)"
                    if own["status"] != "current"
                    else ""
                )
                line += f"; overrides: {values}{stale}"
            human_output(f"  {key}: {line}{note}")
    if data["outputs"]:
        human_output("\nResults:")
        for key, output in data["outputs"].items():
            human_output(
                f"  {key}: {output.get('command', '?')}, {output.get('status', '?')}"
                f", {str(output.get('at', ''))[:10]}"
            )


def run_show(params: SampleShowParams) -> None:
    _report(params)


def run_set(params: SampleSetParams) -> None:
    changes: OverrideChanges = {}
    if params.sex is not None:
        changes["sex"] = params.sex
    if params.platform is not None:
        changes["platform"] = params.platform
    SampleWorkspace(params.folder).set_overrides(changes, params.input)
    _report(params)


def run_unset(params: SampleUnsetParams) -> None:
    changes: OverrideChanges = {}
    if params.sex:
        changes["sex"] = None
    if params.platform:
        changes["platform"] = None
    SampleWorkspace(params.folder).set_overrides(changes, params.input)
    _report(params)


__all__ = ["register", "run_set", "run_show", "run_unset"]
