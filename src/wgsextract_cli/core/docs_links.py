"""Canonical rendered-HTML documentation URLs (https://wgse.bio/wgsextract-cli/...)."""

from __future__ import annotations

import re

SITE_BASE_URL = "https://wgse.bio/wgsextract-cli"
CLI_DOCS_BASE_URL = f"{SITE_BASE_URL}/docs"
APP_DOCS_BASE_URL = f"{SITE_BASE_URL}/app/docs"
APP_DEVDOCS_BASE_URL = f"{SITE_BASE_URL}/app/devdocs"

_DOC_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_./:-])(?P<prefix>(?:gui/(?:docs|devdocs)|docs)/)"
    r"(?P<rel>[A-Za-z0-9_./-]+\.md)(?:#(?P<frag>[A-Za-z0-9_-]+))?"
)


def doc_url(doc_path: str, fragment: str | None = None) -> str:
    """Return the rendered HTML URL for a repo doc path (e.g. 'privacy.md' or 'docs/privacy.md')."""
    raw, hash_sep, inline_frag = doc_path.partition("#")
    frag = fragment if fragment is not None else (inline_frag if hash_sep else None)
    clean = raw.strip().lstrip("./")
    if clean.startswith("gui/devdocs/"):
        base = APP_DEVDOCS_BASE_URL
        rel = clean[len("gui/devdocs/") :]
    elif clean.startswith("gui/docs/"):
        base = APP_DOCS_BASE_URL
        rel = clean[len("gui/docs/") :]
    elif clean.startswith("docs/"):
        base = CLI_DOCS_BASE_URL
        rel = clean[len("docs/") :]
    else:
        base = CLI_DOCS_BASE_URL
        rel = clean

    if rel == "README.md":
        html_rel = "index.html"
    elif rel.endswith(".md"):
        html_rel = f"{rel[:-3]}.html"
    elif rel.endswith(".html"):
        html_rel = rel
    else:
        html_rel = f"{rel}.html"

    url = f"{base}/{html_rel}"
    return f"{url}#{frag}" if frag else url


def linkify_doc_refs(text: str) -> str:
    """Replace bare repo doc paths (docs/*.md, gui/docs/*.md) with rendered HTML URLs."""
    return _DOC_REF_RE.sub(
        lambda m: doc_url(f"{m.group('prefix')}{m.group('rel')}", m.group("frag")),
        text,
    )
