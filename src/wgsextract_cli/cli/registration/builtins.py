"""Evidence for commands registered directly in cli.parser."""

from wgsextract_cli.commands._certification import tested

CERTIFICATION = {
    "help": tested("2026-11-25", "test_cli_help.py", "CLI discovery and help tests."),
    "config": tested(
        "2026-11-25", "test_core_cli.py", "Local CLI configuration tests."
    ),
}
