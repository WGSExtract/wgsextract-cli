"""Tests for rendered Markdown documentation HTML and wgse.bio doc links."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.build_docs_html import build_docs_html, github_slug
from wgsextract_cli.core.docs_links import doc_url, linkify_doc_refs
from wgsextract_cli.core.execution_context import bound
from wgsextract_cli.core.network_policy import DownloadRequired, require_download
from wgsextract_cli.core.results import CommandResult

ROOT = Path(__file__).resolve().parents[1]


def test_doc_url_maps_cli_and_app_markdown_paths() -> None:
    assert doc_url("privacy.md") == "https://wgse.bio/wgsextract-cli/docs/privacy.html"
    assert (
        doc_url("docs/hardware.md#nvidia-gpus-parabricks")
        == "https://wgse.bio/wgsextract-cli/docs/hardware.html#nvidia-gpus-parabricks"
    )
    assert (
        doc_url("docs/README.md") == "https://wgse.bio/wgsextract-cli/docs/index.html"
    )
    assert (
        doc_url("gui/docs/privacy.md")
        == "https://wgse.bio/wgsextract-cli/app/docs/privacy.html"
    )
    assert (
        doc_url("gui/devdocs/architecture.md", "privacy-and-network-boundary")
        == "https://wgse.bio/wgsextract-cli/app/devdocs/architecture.html#privacy-and-network-boundary"
    )


def test_linkify_doc_refs_rewrites_inline_markdown_references() -> None:
    text = "See docs/privacy.md and gui/docs/genome-viewer.md#open-a-local-file."
    assert linkify_doc_refs(text) == (
        "See https://wgse.bio/wgsextract-cli/docs/privacy.html and "
        "https://wgse.bio/wgsextract-cli/app/docs/genome-viewer.html#open-a-local-file."
    )


def test_download_required_error_includes_rendered_wgse_bio_privacy_url() -> None:
    with bound(result=CommandResult(command="qc ancestry"), allow_downloads=False):
        try:
            require_download("HGDP+1KG ancestry reference")
        except DownloadRequired as error:
            message = str(error)
        else:
            raise AssertionError("Expected DownloadRequired")
    assert "https://wgse.bio/wgsextract-cli/docs/privacy.html" in message


def test_github_slug_matches_markdown_heading_anchors() -> None:
    counts: dict[str, int] = {}
    assert (
        github_slug("Privacy and network access", counts)
        == "privacy-and-network-access"
    )
    assert github_slug("What's new in `v0.8`?", counts) == "whats-new-in-v08"
    assert (
        github_slug("Privacy and network access", counts)
        == "privacy-and-network-access-1"
    )


def test_build_docs_html_renders_cli_and_gui_docs_with_rewritten_links(
    tmp_path: Path,
) -> None:
    written = build_docs_html(ROOT, tmp_path)
    assert written

    cli_index = tmp_path / "docs/index.html"
    cli_privacy = tmp_path / "docs/privacy.html"
    app_index = tmp_path / "app/docs/index.html"
    app_privacy = tmp_path / "app/docs/privacy.html"
    app_arch = tmp_path / "app/devdocs/architecture.html"

    for path in (cli_index, cli_privacy, app_index, app_privacy, app_arch):
        assert path.is_file(), f"Missing rendered HTML: {path}"

    cli_index_html = cli_index.read_text(encoding="utf-8")
    assert 'href="privacy.html"' in cli_index_html
    assert 'href="../app/docs/index.html"' in cli_index_html
    assert 'href="../readme.html#quick-start"' in cli_index_html
    assert (
        'rel="canonical" href="https://wgse.bio/wgsextract-cli/docs/index.html"'
        in cli_index_html
    )

    app_index_html = app_index.read_text(encoding="utf-8")
    assert 'href="modes.html"' in app_index_html
    assert 'href="reference-data.html"' in app_index_html
    assert 'href="reference-genomes.html"' in app_index_html
    assert 'href="tools.html"' in app_index_html
    assert 'href="settings.html"' in app_index_html
    assert 'href="privacy.html"' in app_index_html
    assert 'src="readme-screenshot.png"' in app_index_html
    assert (tmp_path / "app/docs/readme-screenshot.png").is_file()

    app_arch_html = app_arch.read_text(encoding="utf-8")
    assert 'id="privacy-and-network-boundary"' in app_arch_html
    assert 'href="../docs/privacy.html"' in app_arch_html
    assert 'href="../../docs/privacy.html"' in app_arch_html

    # Every relative link to a rendered doc or doc image must resolve to a generated file.
    missing_targets: list[str] = []
    for html_file in written:
        content = html_file.read_text(encoding="utf-8")
        for attr in re.findall(r'(?:href|src)="([^"]+)"', content):
            if attr.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_part = attr.split("#", 1)[0]
            if not target_part or target_part.endswith("assets/site.css"):
                continue
            # Top-level landing pages (index.html, install.html, etc.) are built by build_site.py.
            resolved = (html_file.parent / target_part).resolve()
            rel = resolved.relative_to(tmp_path.resolve()).as_posix()
            if "/" not in rel or rel in {
                "app/index.html",
                "app/architecture.html",
                "app/wgsextract.html",
                "app/docs.html",
                "app/whats-different.html",
            }:
                continue
            if not resolved.is_file():
                missing_targets.append(f"{html_file.relative_to(tmp_path)} -> {attr}")
    assert missing_targets == []
