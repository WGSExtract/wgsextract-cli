"""Render command certification from the live CLI registry."""

from wgsextract_cli.cli.help import command_index
from wgsextract_cli.cli.parser import build_parser
from wgsextract_cli.commands._certification import command_certification
from wgsextract_cli.core.certification import LEVEL_CRITERIA
from wgsextract_cli.core.config import settings_suspended


def render() -> str:
    with settings_suspended():
        entries = command_index(build_parser()[0])
    lines = ["# Command certification", "", "## What the levels mean", ""]
    for level, criteria in LEVEL_CRITERIA.items():
        lines.append(f"- **{level.value}:** {criteria}")
    lines.extend(
        [
            "",
            "A level applies to the evidence listed, not to every input, option or "
            "backend. Research features can remain Experimental even when their "
            "mechanics have been tested. Platform entries describe the linked "
            "qualification; a missing entry is not an unsupported-platform claim.",
            "",
            "Running a Tested or Experimental operation logs a notice; asking "
            "for help does not run the operation. Set "
            "`certification_notices = false` in `config.toml` to hide it without "
            "changing the level recorded in results. The notice is not a run warning.",
            "",
            "## Promoting a command",
            "",
            "Open a pull request with evidence that meets the new level's criteria. "
            "Link the validation reports and regression tests, identify the platforms "
            "and inputs covered, and document the limits. Update the command's "
            "`CERTIFICATION` entry in `src/wgsextract_cli/cli/registration/` with "
            "the new level, evidence links and review date.",
            "",
            "For reviewed changes to the original inventory, also update "
            "`tests/fixtures/certification_inventory_1596.tsv`. Regenerate the "
            "desktop badge data with `python scripts/gen_server_openapi.py --gui`. "
            "Lower the level if later evidence invalidates the qualification.",
            "",
            "## Evidence by command",
            "",
            "| Command | Tier | Level | Evidence | Platforms | Reviewed |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for entry in entries:
        meta = command_certification(entry.path)
        links = ", ".join(f"[{i}]({link})" for i, link in enumerate(meta.links, 1))
        evidence = f"{meta.summary} {links}".rstrip()
        platforms = ", ".join(meta.platforms) or "—"
        lines.append(
            f"| `{entry.path}` | {entry.tier} | {meta.level.value} | "
            f"{evidence} | {platforms} | {meta.reviewed} |"
        )
    return "\n".join(lines) + "\n"
