"""Help entries for ``serve``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "serve": _h(
        "Prints the server's address and token, also saved to "
        "state/server/server.json (setting state_directory) for you only; "
        "jobs and their logs go in state/server/jobs. Each operation writes "
        "its usual outputs and result manifest.",
        "wgsextract serve --allow ~/wgs",
        "wgsextract serve --port 8765 --max-jobs 2",
        "wgsextract serve --mcp --allow ~/wgs",
        "wgsextract serve --openapi",
    ),
}
