"""Run selected cheap optional-tool integrations without accepting skips."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Keep the synthetic fixtures; these suites require no downloaded sample data.
SUITES = {
    "tests/test_contamination.py": ("verifybamid2", ""),
    "tests/test_vcf_callers.py": ("freebayes", "freebayes"),
    "tests/test_typing_str_long_reads.py": ("LongTR", ""),
    "tests/test_methylation.py": (
        "modkit",
        "test_real_modkit_gives_the_same_calls_from_cram_as_from_bam or "
        "test_cram_without_region_matches_bam_and_skips_empty_contigs",
    ),
    "tests/test_genotyping_commands.py": (
        "ExpansionHunter",
        "test_str_on_hg19_says_notch2nlc_is_unavailable",
    ),
}


def selected_suites(selection: str) -> list[str]:
    if selection == "all" or not selection:
        return list(SUITES)
    targets = json.loads(selection)
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        raise ValueError("Selection must be a JSON list of test files or all")
    return [test for test in SUITES if test in targets]


def routed_suites(paths: list[str], selection: list[str] | str) -> list[str]:
    """Workflow/runner edits exercise their own fixtures, not the OS matrix."""
    if any(
        path
        in {
            ".github/workflows/optional_tools.yml",
            ".github/workflows/ubuntu_ci.yml",
            ".github/actions/setup-pixi/action.yml",
            "scripts/ci_optional_tools.py",
        }
        for path in paths
    ):
        return list(SUITES)
    return selected_suites(
        selection if isinstance(selection, str) else json.dumps(selection)
    )


def require_executed(report: Path) -> None:
    cases = list(ET.parse(report).iter("testcase"))
    if not cases or any(case.find("skipped") is not None for case in cases):
        raise ValueError(f"Optional-tool tests did not all run: {report}")
    if any(
        case.find("failure") is not None or case.find("error") is not None
        for case in cases
    ):
        raise ValueError(f"Optional-tool tests failed: {report}")
    print(f"{report}: {len(cases)} tests ran, zero skipped")


def main() -> None:
    from wgsextract_cli.core.dependencies import get_tool_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", default="all")
    args = parser.parse_args()
    out = Path("out/optional-tools")
    out.mkdir(parents=True, exist_ok=True)
    for test in selected_suites(args.selection):
        tool, expression = SUITES[test]
        command = get_tool_path(tool)
        if not command:
            raise ValueError(f"Required optional CI tool missing: {tool}")
        # VerifyBamID2's --version exits 255. Actual execution below validates
        # the binary and its shared libraries instead of a generic version flag.
        print(f"Required {tool}: {shlex.split(command)[0]}")
        report = out / f"{Path(test).stem}.xml"
        invocation = [
            "pixi",
            "run",
            "--no-install",
            "-e",
            "dev",
            "pytest",
            test,
            "-p",
            "no:randomly",
            "-rs",
            "-o",
            "addopts=",
            f"--basetemp={out / Path(test).stem}",
            f"--junitxml={report}",
        ]
        if expression:
            invocation += ["-k", expression]
        subprocess.run(invocation, check=True)
        require_executed(report)


if __name__ == "__main__":
    main()
