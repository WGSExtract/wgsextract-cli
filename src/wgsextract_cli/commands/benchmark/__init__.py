"""``benchmark``: the CLI command; ``api.benchmark`` documents the operation."""

import argparse


def register(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    base_parser: argparse.ArgumentParser,
) -> None:
    from wgsextract_cli.cli.registration.benchmark import register

    register(subparsers, base_parser)
