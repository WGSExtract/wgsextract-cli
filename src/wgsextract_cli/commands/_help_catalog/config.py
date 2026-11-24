"""Help entries for ``config``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "config": _h(
        "Prints the settings; in a terminal, offers to create a default config.toml.",
        "wgsextract config",
    ),
}
