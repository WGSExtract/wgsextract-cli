"""Render the documentation derived from the CLI, API and settings."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from scripts.build_site import OUTPUT_DIR, ROOT, SOURCE_DIR, load_page, render_page

DOCS_URL = "https://github.com/WGSExtract/wgsextract-cli/blob/main/docs/"
REFERENCE_PAGES = frozenset(
    {"api.html", "commands.html", "configuration.html", "certification.html"}
)
_REPO_DOC_LINK = re.compile(r"\]\(([\w-]+\.md(?:#[\w-]+)?)\)")
_GENERATED_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def command_cards(body: str) -> str:
    from wgsextract_cli.cli.help import render_site_commands

    marker = "<!-- command cards -->"
    if body.count(marker) != 1:
        raise ValueError(f"CLI site source must contain exactly one {marker}")
    return body.replace(marker, render_site_commands().rstrip())


def reference_html(output_name: str, root: Path) -> str:
    from markdown_it import MarkdownIt

    if output_name == "api.html":
        from scripts.gen_api_docs import render as render_api

        text = render_api()
    elif output_name == "configuration.html":
        from scripts.gen_config_docs import render as render_config

        text = render_config(root / "docs" / "data" / "configuration-guide.md")
    elif output_name == "commands.html":
        from wgsextract_cli.cli.help import render_markdown
        from wgsextract_cli.cli.parser import build_parser
        from wgsextract_cli.core.config import settings_suspended

        with settings_suspended():
            text = render_markdown(build_parser()[0])
    elif output_name == "certification.html":
        from scripts.site_certification import render

        text = render()
    else:
        raise ValueError(f"Unknown reference page: {output_name}")
    # Page metadata supplies the h1; the Markdown contains the rest of the page.
    body = _GENERATED_COMMENT.sub("", text.partition("\n")[2])
    body = _REPO_DOC_LINK.sub(rf"]({DOCS_URL}\1)", body)
    rendered = MarkdownIt("commonmark", {"html": False}).enable("table").render(body)
    if output_name == "certification.html":
        rendered = re.sub(
            r"<tr>\n<td><code>([a-z0-9 -]+)</code>",
            lambda match: (
                f'<tr id="command-{match[1].replace(" ", "-")}">\n<td><code>{match[1]}</code>'
            ),
            rendered,
        )
    rendered = rendered.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>"
    )
    return f'<section><div class="wrap">{rendered}</div></section>'


def built_pages() -> Iterator[tuple[str, str]]:
    if not SOURCE_DIR.is_dir():
        raise SystemExit(f"Missing site source directory: {SOURCE_DIR}")

    seen = set()
    for source in sorted(SOURCE_DIR.glob("*.md")):
        page = load_page(source)
        name = page.output_name
        if name in seen:
            raise ValueError(f"Duplicate site output: {name}")
        seen.add(name)
        if name == "cli.html":
            page = replace(page, body=command_cards(page.body))
        body = reference_html(name, ROOT) if name in REFERENCE_PAGES else None
        yield name, render_page(page, body_html=body)
    if missing := REFERENCE_PAGES - seen:
        raise ValueError(f"Missing sources for generated references: {sorted(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static documentation site.")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rendered in built_pages():
        (args.output / name).write_text(rendered, encoding="utf-8", newline="\n")
