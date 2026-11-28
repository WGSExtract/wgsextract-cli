"""What each runnable command needs, and how long it typically takes.

Each command's ``--help``, ``wgsextract help <command> --json`` and
the published command reference show this. ``tools`` are the external tools every
documented run needs; ``optional`` are those only some options or inputs use
(an alternative caller or aligner, a VCF instead of a BAM, a download, or a
faster tool used when installed). Names are those ``wgsextract deps check``
reports on (core/tool_catalog.py), plus docker and ssh-keygen.

tests/test_command_needs.py keeps the two in step with the code by following
each handler's calls: ``tools`` are those it checks for with
``verify_dependencies``, ``env_tool``, ``pixi_env_tool`` or
``require_native_tool`` on every run, plus samtools when the examples give
the BAM/CRAM ``--input`` the CLI checks first; every other tool it checks,
probes for (``get_tool_path``, ``shutil.which``) or runs is ``optional``.

``runtime`` is a class, not a promise: for a 30x whole genome (about 100 GB
of BAM or 15-20 GB of CRAM) on a typical 8-thread computer, unless ``scale``
(the suffix of ``runtime_text``) says what it depends on instead. Downloads
depend on the connection.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from typing import cast

from wgsextract_cli.commands.registry import COMMAND_MODULES

INSTANT = "instant"
SECONDS = "seconds"
MINUTES = "minutes"
HOURS = "hours"
DOWNLOAD = "download"

# Upper bounds in seconds, matched to core/warnings.py's EXPECTED_TIME.
RUNTIME_LIMITS = {INSTANT: 5, SECONDS: 60, MINUTES: 3600}
RUNTIMES = (INSTANT, SECONDS, MINUTES, HOURS, DOWNLOAD)
GENOME = " on a 30x genome"
REFERENCE = ", once per reference"


@dataclass(frozen=True)
class CommandNeeds:
    runtime: str
    tools: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    scale: str = GENOME

    @property
    def runtime_text(self) -> str:
        if self.runtime == DOWNLOAD:
            return "depends on download speed"
        if self.runtime == INSTANT:
            return INSTANT
        return self.runtime + self.scale

    @property
    def tools_text(self) -> str:
        """'bcftools, tabix; depending on options and inputs, also: delly, sniffles'."""
        text = ", ".join(self.tools) or "no external tools"
        if self.optional:
            text += "; depending on options and inputs, also: " + ", ".join(
                self.optional
            )
        return text


def _n(
    runtime: str, *tools: str, optional: str = "", scale: str = GENOME
) -> CommandNeeds:
    return CommandNeeds(runtime, tools, tuple(optional.split()), scale)


_SAM = ("samtools",)
_VCF = ("bcftools", "tabix")
_CALL = ("bcftools", "samtools", "tabix")
_SMALL = " for small inputs"

_GROUPS = {registration.name: registration for registration in COMMAND_MODULES}
_BUILTIN_NEEDS = {"help": CommandNeeds(INSTANT), "config": CommandNeeds(INSTANT)}


@cache
def _group_needs(group: str) -> Mapping[str, CommandNeeds]:
    return cast("Mapping[str, CommandNeeds]", _GROUPS[group].load().NEEDS)


class _CommandNeedsRegistry(Mapping[str, CommandNeeds]):
    def __getitem__(self, path: str) -> CommandNeeds:
        if path in _BUILTIN_NEEDS:
            return _BUILTIN_NEEDS[path]
        group = path.partition(" ")[0]
        if group in _GROUPS:
            return _group_needs(group)[path]
        raise KeyError(path)

    def __iter__(self) -> Iterator[str]:
        yield from _BUILTIN_NEEDS
        for group in _GROUPS:
            yield from _group_needs(group)

    def __len__(self) -> int:
        return sum(1 for _ in self)


COMMAND_NEEDS: Mapping[str, CommandNeeds] = _CommandNeedsRegistry()


def tool_users() -> dict[str, list[str]]:
    """Each tool's commands: those whose runs need it or may use it, so the
    desktop app can tell which tools only its Advanced mode's commands use."""
    users: dict[str, list[str]] = {}
    for path, needs in COMMAND_NEEDS.items():
        for tool in dict.fromkeys(needs.tools + needs.optional):
            users.setdefault(tool, []).append(path)
    return users
