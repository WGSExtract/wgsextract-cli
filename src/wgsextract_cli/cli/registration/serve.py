from __future__ import annotations

import argparse

from wgsextract_cli.cli._lazy import command_handler

run = command_handler("wgsextract_cli.commands.serve", "run")
MCP_TRANSPORTS = ("stdio", "http")


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    base_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "serve",
        parents=[base_parser],
        help="Serve the WGS Extract API to local apps and AI agents, over HTTP/JSON or MCP.",
        description="Run the same operations as the CLI (info, vcf snp, ...) for other programs on this computer: as an HTTP/JSON API on 127.0.0.1 with a per-launch token, or as an MCP server (--mcp) on stdio or on 127.0.0.1 over streamable HTTP. Operations run as background jobs with progress, cancelling and result manifests. Requests may only name files inside the folders you allow: --allow, config server_allowed_paths, dataset_library, reference_library, reference_genome_library and output_directory.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port on 127.0.0.1 (default: config server_port, or server_mcp_port for MCP over HTTP, else any free port).",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="DIR",
        help="Also let requests read and write inside DIR (repeatable).",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        help="Long jobs that run at once; others wait in a queue (default: config server_max_jobs, else 1). Quick queries never wait.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Speak the Model Context Protocol instead of the HTTP/JSON API: on stdin/stdout, or over HTTP with --mcp-transport http.",
    )
    parser.add_argument(
        "--mcp-transport",
        choices=MCP_TRANSPORTS,
        help="How --mcp is spoken (implies --mcp): stdio, or http for streamable HTTP on 127.0.0.1 with a per-launch token, which needs the optional 'mcp' package (default: config server_mcp_transport, else stdio).",
    )
    parser.add_argument(
        "--allow-jobs",
        action="store_true",
        help="With --mcp, also offer the operations that write files or change a sample's settings, not just the read-only queries.",
    )
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="Also run requests that delete data a command didn't make: bam unindex, sample unset, vep delete and the ref installers' --delete.",
    )
    parser.add_argument(
        "--ready-json",
        action="store_true",
        help='Once listening, print the address and token as one JSON line on stdout ({"url": ..., "token": ..., "pid": ...}), for a program that starts the server itself. Not for MCP over stdio.',
    )
    parser.add_argument(
        "--openapi",
        action="store_true",
        help="Print the HTTP API's OpenAPI document and exit.",
    )
    parser.set_defaults(func=run)
