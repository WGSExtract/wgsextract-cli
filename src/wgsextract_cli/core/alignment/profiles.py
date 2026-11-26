"""Lossless alignment output profiles; the default keeps samtools' defaults."""

import subprocess

from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.tool_commands import _tool_command_parts
from wgsextract_cli.core.utils import UsageError, WGSExtractError

OUTPUT_PROFILES = ("default", "archive", "calling")


def output_profile_options(profile: str) -> list[str]:
    if profile not in OUTPUT_PROFILES:
        raise UsageError(f"Unknown output profile: {profile!r}.")
    options = {
        "default": (),
        "archive": ("archive", "version=3.1"),
        "calling": ("version=3.0",),
    }[profile]
    return [arg for option in options for arg in ("--output-fmt-option", option)]


def validate_output_profile(profile: str) -> None:
    """Check the installed encoder, including the version it actually writes."""
    options = output_profile_options(profile)
    if not options:
        return
    try:
        version = probe_cram_output_version(options)
    except (OSError, subprocess.SubprocessError) as error:
        raise WGSExtractError(
            f"Installed samtools cannot write the {profile} output profile. "
            "Use --output-profile default or install a samtools supporting "
            "the requested CRAM options."
        ) from error
    expected = (3, 1) if profile == "archive" else (3, 0)
    if version != expected:
        raise WGSExtractError(
            f"Installed samtools did not write the CRAM version required by "
            f"the {profile} output profile."
        )


def probe_cram_output_version(options: list[str]) -> tuple[int, int] | None:
    """Return the CRAM version actually emitted by the configured samtools."""
    tool = get_tool_path("samtools")
    if not tool:
        raise WGSExtractError("samtools is required for CRAM output.")
    output = subprocess.run(
        [*_tool_command_parts(tool), "view", "-C", *options, "-o", "-", "-"],
        input=b"@HD\tVN:1.6\n",
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    if len(output) >= 6 and output[:4] == b"CRAM":
        return output[4], output[5]
    return None
