#!/usr/bin/env python3
"""Render repository Markdown documentation to static HTML under site/.

Outputs mirror the wgsextract.github.io / wgse.bio layout:
- CLI docs: site/readme.html, site/docs/<path>.html (README.md -> index.html)
- App docs: site/app/readme.html, site/app/docs/<path>.html, site/app/devdocs/<path>.html
"""

from __future__ import annotations

import argparse
import html
import os
import posixpath
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "site"
BASE_URL = "https://wgse.bio/wgsextract-cli"
GITHUB_URL = "https://github.com/WGSExtract/wgsextract-cli"
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})

CLI_NAV = (
    ("index.html", "Home"),
    ("install.html", "Install CLI"),
    ("gui.html", "GUI"),
    ("cli.html", "CLI"),
    ("workflows.html", "Workflows"),
    ("wgs-guide.html", "WGS guide"),
    ("reference.html", "Reference"),
    ("docs/index.html", "Docs"),
)
APP_NAV = (
    ("index.html", "Home"),
    ("architecture.html", "GUI Architecture"),
    ("wgsextract.html", "WGSExtract"),
    ("docs.html", "Docs"),
    ("../index.html", "WGSExtract CLI"),
    ("whats-different.html", "What's Different"),
)

DOC_EXTRA_CSS = """
.doc-breadcrumb { font-size: 0.9rem; color: var(--muted, #8a97a8); margin-bottom: 1rem; }
.doc-breadcrumb a { color: inherit; text-decoration: none; }
.doc-breadcrumb a:hover { text-decoration: underline; }
.doc-article { padding: 2.2rem 0 3.5rem; }
.doc-article h1 { font-size: clamp(1.85rem, 3vw, 2.5rem); margin: 0 0 1.25rem; line-height: 1.2; }
.doc-article h2 { margin-top: 2.25rem; padding-bottom: 0.35rem; border-bottom: 1px solid var(--border, rgba(255,255,255,0.12)); }
.doc-article h3 { margin-top: 1.6rem; }
.doc-article h4, .doc-article h5, .doc-article h6 { margin-top: 1.25rem; }
.doc-article p, .doc-article li { line-height: 1.65; }
.doc-article ul, .doc-article ol { padding-left: 1.5rem; margin: 0.75rem 0 1rem; }
.doc-article li + li { margin-top: 0.35rem; }
.doc-article blockquote { margin: 1.1rem 0; padding: 0.85rem 1.15rem; border-left: 3px solid var(--accent, #38bdf8); background: rgba(127, 150, 180, 0.08); border-radius: 0 8px 8px 0; }
.doc-article blockquote > :first-child { margin-top: 0; }
.doc-article blockquote > :last-child { margin-bottom: 0; }
.doc-article pre { overflow-x: auto; padding: 1rem 1.15rem; border-radius: 10px; }
.doc-article img { max-width: 100%; height: auto; border-radius: 10px; margin: 1rem 0; }
.doc-article hr { border: 0; border-top: 1px solid var(--border, rgba(255,255,255,0.12)); margin: 2rem 0; }
.doc-article .table-wrap { overflow-x: auto; margin: 1.1rem 0; }
.doc-article .heading-anchor { opacity: 0; margin-left: 0.4rem; text-decoration: none; font-weight: 400; font-size: 0.85em; }
.doc-article h2:hover .heading-anchor, .doc-article h3:hover .heading-anchor, .doc-article h4:hover .heading-anchor { opacity: 0.65; }
""".strip()


@dataclass(frozen=True)
class DocEntry:
    source: Path
    repo_rel: PurePosixPath
    out_rel: PurePosixPath
    section: str  # "cli" or "app"


def collect_docs(root: Path) -> list[DocEntry]:
    entries: list[DocEntry] = []

    def add(source: Path, out_rel: str, section: str) -> None:
        if source.is_file():
            entries.append(
                DocEntry(
                    source=source,
                    repo_rel=PurePosixPath(source.relative_to(root).as_posix()),
                    out_rel=PurePosixPath(out_rel),
                    section=section,
                )
            )

    add(root / "README.md", "readme.html", "cli")
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*.md")):
            rel = PurePosixPath(path.relative_to(docs_dir).as_posix())
            if rel.parts[0] == "data":
                continue
            if rel.parts[:2] == ("release-notes", "unreleased"):
                continue
            out_name = (
                "docs/index.html"
                if rel == PurePosixPath("README.md")
                else f"docs/{rel.with_suffix('.html').as_posix()}"
            )
            add(path, out_name, "cli")

    add(root / "gui/README.md", "app/readme.html", "app")
    for sub, prefix in (("gui/docs", "app/docs"), ("gui/devdocs", "app/devdocs")):
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            rel = PurePosixPath(path.relative_to(base).as_posix())
            if rel.parts[:2] == ("changelog", "unreleased"):
                continue
            out_name = (
                f"{prefix}/index.html"
                if rel == PurePosixPath("README.md")
                else f"{prefix}/{rel.with_suffix('.html').as_posix()}"
            )
            add(path, out_name, "app")

    return entries


def copy_doc_assets(root: Path, output_dir: Path) -> dict[PurePosixPath, PurePosixPath]:
    copied: dict[PurePosixPath, PurePosixPath] = {}
    for src_sub, out_sub in (
        ("docs", "docs"),
        ("gui/docs", "app/docs"),
        ("gui/devdocs", "app/devdocs"),
    ):
        base = root / src_sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            rel = PurePosixPath(path.relative_to(base).as_posix())
            repo_rel = PurePosixPath(path.relative_to(root).as_posix())
            out_rel = PurePosixPath(out_sub) / rel
            dest = output_dir / out_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            copied[repo_rel] = out_rel
    return copied


def github_slug(heading_text: str, counts: dict[str, int]) -> str:
    plain = re.sub(r"`([^`]+)`", r"\1", heading_text)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    plain = re.sub(r"[*~]", "", plain)
    plain = re.sub(r"(?<!\w)_+|_+(?!\w)", "", plain)
    slug = re.sub(r"[^\w\s-]", "", plain.lower(), flags=re.UNICODE).strip()
    slug = re.sub(r"\s+", "-", slug)
    if not slug:
        slug = "section"
    seen = counts.get(slug, 0)
    counts[slug] = seen + 1
    return slug if seen == 0 else f"{slug}-{seen}"


def extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            title = re.sub(r"`([^`]+)`", r"\1", match.group(1))
            title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
            title = re.sub(r"[*_~]", "", title).strip()
            if title:
                return title
    return fallback


class DocMarkdownRenderer:
    """Dependency-free GFM-subset renderer for repository Markdown docs."""

    def __init__(
        self,
        markdown: str,
        entry: DocEntry,
        doc_map: dict[PurePosixPath, PurePosixPath],
        asset_map: dict[PurePosixPath, PurePosixPath],
        root: Path,
    ) -> None:
        self.lines = markdown.splitlines()
        self.entry = entry
        self.doc_map = doc_map
        self.asset_map = asset_map
        self.root = root
        self.index = 0
        self.slug_counts: dict[str, int] = {}

    def render(self) -> str:
        blocks = self._render_blocks(self.lines)
        return "\n".join(blocks)

    def _render_blocks(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        paragraph: list[str] = []
        idx = 0

        def flush() -> None:
            if paragraph:
                out.append(f"<p>{self.render_inline(' '.join(paragraph))}</p>")
                paragraph.clear()

        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            if not stripped:
                flush()
                idx += 1
                continue
            fence = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
            if fence:
                flush()
                marker = fence.group(1)
                lang = (
                    fence.group(2).strip().split()[0] if fence.group(2).strip() else ""
                )
                idx += 1
                code_lines: list[str] = []
                while idx < len(lines) and not lines[idx].strip().startswith(marker):
                    code_lines.append(lines[idx])
                    idx += 1
                if idx < len(lines):
                    idx += 1
                lang_attr = f' class="language-{escape_attr(lang)}"' if lang else ""
                code_html = html.escape("\n".join(code_lines))
                out.append(f"<pre><code{lang_attr}>{code_html}</code></pre>")
                continue
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
            if heading:
                flush()
                level = len(heading.group(1))
                text = heading.group(2).strip()
                slug = github_slug(text, self.slug_counts)
                anchor = (
                    f'<a class="heading-anchor" href="#{escape_attr(slug)}" '
                    f'aria-label="Link to section">#</a>'
                    if level >= 2
                    else ""
                )
                out.append(
                    f'<h{level} id="{escape_attr(slug)}">'
                    f"{self.render_inline(text)}{anchor}</h{level}>"
                )
                idx += 1
                continue
            if re.fullmatch(r"([-*_])(\s*\1){2,}", stripped):
                flush()
                out.append("<hr />")
                idx += 1
                continue
            if stripped.startswith(">"):
                flush()
                quote_lines: list[str] = []
                while idx < len(lines) and lines[idx].lstrip().startswith(">"):
                    q = lines[idx].lstrip()[1:]
                    quote_lines.append(q[1:] if q.startswith(" ") else q)
                    idx += 1
                inner = "\n".join(self._render_blocks(quote_lines))
                out.append(f"<blockquote>\n{inner}\n</blockquote>")
                continue
            if self._is_table_start(lines, idx):
                flush()
                table_html, idx = self._read_table(lines, idx)
                out.append(table_html)
                continue
            if self._list_marker(line) is not None:
                flush()
                list_html, idx = self._read_list(lines, idx)
                out.append(list_html)
                continue
            paragraph.append(stripped)
            idx += 1

        flush()
        return out

    @staticmethod
    def _is_table_start(lines: list[str], idx: int) -> bool:
        if idx + 1 >= len(lines):
            return False
        cur = lines[idx].strip()
        sep = lines[idx + 1].strip()
        return "|" in cur and bool(re.fullmatch(r"\|?[\s:|-]+\|?", sep)) and "-" in sep

    @staticmethod
    def _split_row(line: str) -> list[str]:
        raw = line.strip()
        if raw.startswith("|"):
            raw = raw[1:]
        if raw.endswith("|") and not raw.endswith(r"\|"):
            raw = raw[:-1]
        cells = re.split(r"(?<!\\)\|", raw)
        return [cell.replace(r"\|", "|").strip() for cell in cells]

    def _read_table(self, lines: list[str], idx: int) -> tuple[str, int]:
        header = self._split_row(lines[idx])
        idx += 2
        rows: list[list[str]] = []
        while idx < len(lines):
            stripped = lines[idx].strip()
            if not stripped or "|" not in stripped:
                break
            rows.append(self._split_row(stripped))
            idx += 1
        head_html = "".join(f"<th>{self.render_inline(c)}</th>" for c in header)
        body_html = "\n".join(
            "<tr>" + "".join(f"<td>{self.render_inline(c)}</td>" for c in r) + "</tr>"
            for r in rows
        )
        return (
            '<div class="table-wrap"><table>\n'
            f"<thead><tr>{head_html}</tr></thead>\n"
            f"<tbody>\n{body_html}\n</tbody>\n"
            "</table></div>",
            idx,
        )

    @staticmethod
    def _list_marker(line: str) -> tuple[int, str, str] | None:
        match = re.match(r"^(\s*)(?:([-*+])|(\d+)\.)\s+(.*)$", line)
        if not match:
            return None
        indent = len(match.group(1).expandtabs(2))
        tag = "ul" if match.group(2) else "ol"
        return indent, tag, match.group(4)

    def _read_list(
        self, lines: list[str], idx: int, base_indent: int | None = None
    ) -> tuple[str, int]:
        first = self._list_marker(lines[idx])
        assert first is not None
        indent, tag, _ = first
        if base_indent is None:
            base_indent = indent
        items: list[str] = []

        while idx < len(lines):
            marker = self._list_marker(lines[idx])
            if marker is None:
                break
            cur_indent, cur_tag, text = marker
            if cur_indent < base_indent or (
                cur_indent == base_indent and cur_tag != tag
            ):
                break
            if cur_indent > base_indent:
                nested, idx = self._read_list(lines, idx, cur_indent)
                if items:
                    items[-1] = items[-1][:-5] + "\n" + nested + "</li>"
                continue
            item_parts = [text.strip()]
            idx += 1
            nested_blocks: list[str] = []
            while idx < len(lines):
                nxt = lines[idx]
                if not nxt.strip():
                    idx += 1
                    break
                nxt_marker = self._list_marker(nxt)
                if nxt_marker is not None:
                    if nxt_marker[0] > base_indent:
                        nested, idx = self._read_list(lines, idx, nxt_marker[0])
                        nested_blocks.append(nested)
                        continue
                    break
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent > base_indent:
                    item_parts.append(nxt.strip())
                    idx += 1
                    continue
                break
            content = self.render_inline(" ".join(item_parts))
            if nested_blocks:
                content += "\n" + "\n".join(nested_blocks)
            items.append(f"<li>{content}</li>")

        return f"<{tag}>\n" + "\n".join(items) + f"\n</{tag}>", idx

    def resolve_href(self, raw_href: str) -> tuple[str, bool]:
        href = raw_href.strip()
        if not href or href.startswith(("#", "mailto:")):
            return href, False
        if re.match(r"^https?://", href, re.IGNORECASE):
            return href, True
        path_part, hash_sep, fragment = href.partition("#")
        suffix = f"#{fragment}" if hash_sep else ""
        resolved = Path(os.path.normpath(str(self.entry.source.parent / path_part)))
        try:
            repo_rel = PurePosixPath(resolved.relative_to(self.root).as_posix())
        except ValueError:
            return href, False

        if repo_rel in self.doc_map:
            target = self.doc_map[repo_rel]
            rel = posixpath.relpath(
                target.as_posix(), self.entry.out_rel.parent.as_posix()
            )
            return rel + suffix, False
        if repo_rel in self.asset_map:
            target = self.asset_map[repo_rel]
            rel = posixpath.relpath(
                target.as_posix(), self.entry.out_rel.parent.as_posix()
            )
            return rel + suffix, False
        if resolved.is_dir():
            return f"{GITHUB_URL}/tree/main/{repo_rel.as_posix()}{suffix}", True
        if resolved.exists():
            return f"{GITHUB_URL}/blob/main/{repo_rel.as_posix()}{suffix}", True
        return href, False

    def render_inline(self, text: str) -> str:
        tokens: list[str] = []

        def stash(token: str) -> str:
            tokens.append(token)
            return f"\x00{len(tokens) - 1}\x00"

        text = re.sub(
            r"`([^`]+)`",
            lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"),
            text,
        )

        def repl_image(match: re.Match[str]) -> str:
            alt = escape_attr(match.group(1))
            src, _ = self.resolve_href(match.group(2))
            return stash(f'<img src="{escape_attr(src)}" alt="{alt}" />')

        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl_image, text)

        def repl_link(match: re.Match[str]) -> str:
            label = self.render_inline_no_links(match.group(1), tokens)
            href, external = self.resolve_href(match.group(2))
            ext = ' target="_blank" rel="noopener noreferrer"' if external else ""
            return stash(f'<a href="{escape_attr(href)}"{ext}>{label}</a>')

        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_link, text)

        def repl_autolink(match: re.Match[str]) -> str:
            url = match.group(1)
            return stash(
                f'<a href="{escape_attr(url)}" target="_blank" '
                f'rel="noopener noreferrer">{html.escape(url)}</a>'
            )

        text = re.sub(r"<(https?://[^>]+)>", repl_autolink, text)
        text = self._format_emphasis(html.escape(text, quote=False))
        for idx, token in enumerate(tokens):
            text = text.replace(f"\x00{idx}\x00", token)
        return text

    def render_inline_no_links(self, text: str, tokens: list[str]) -> str:
        escaped = html.escape(text, quote=False)
        formatted = self._format_emphasis(escaped)
        for idx, token in enumerate(tokens):
            formatted = formatted.replace(f"\x00{idx}\x00", token)
        return formatted

    @staticmethod
    def _format_emphasis(text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
        text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
        return text


def rel_from(out_rel: PurePosixPath, target_rel: str) -> str:
    return posixpath.relpath(target_rel, out_rel.parent.as_posix())


def render_doc_page(
    entry: DocEntry,
    body_html: str,
    title: str,
) -> str:
    is_app = entry.section == "app"
    site_prefix = "app" if is_app else ""
    nav_items = APP_NAV if is_app else CLI_NAV
    brand_name = "WGS Extract GUI" if is_app else "WGS Extract CLI"
    brand_href = rel_from(entry.out_rel, "app/index.html" if is_app else "index.html")
    css_href = rel_from(
        entry.out_rel, "app/assets/site.css" if is_app else "assets/site.css"
    )
    canonical = f"{BASE_URL}/{entry.out_rel.as_posix()}"
    source_url = f"{GITHUB_URL}/blob/main/{entry.repo_rel.as_posix()}"

    nav_links: list[str] = []
    for href, label in nav_items:
        target = posixpath.normpath(f"{site_prefix}/{href}") if site_prefix else href
        link_href = rel_from(entry.out_rel, target)
        active = ' class="active"' if label == "Docs" else ""
        nav_links.append(
            f'<a{active} href="{escape_attr(link_href)}">{html.escape(label)}</a>'
        )
    nav_links.append(
        f'<a href="{GITHUB_URL}" target="_blank" rel="noopener noreferrer">GitHub</a>'
    )

    if is_app:
        is_dev = entry.out_rel.parts[:2] == ("app", "devdocs")
        index_target = "app/devdocs/index.html" if is_dev else "app/docs/index.html"
        index_label = "Developer Docs" if is_dev else "User Docs"
    else:
        index_target = "docs/index.html"
        index_label = "CLI Docs"
    index_href = rel_from(entry.out_rel, index_target)
    breadcrumb = (
        f'<nav class="doc-breadcrumb" aria-label="Breadcrumb">'
        f'<a href="{escape_attr(brand_href)}">{html.escape(brand_name)}</a>'
        f' / <a href="{escape_attr(index_href)}">{html.escape(index_label)}</a>'
        f" / <span>{html.escape(title)}</span></nav>"
    )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "  <head>",
            '    <meta charset="utf-8" />',
            '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"    <title>{html.escape(title)} | {html.escape(brand_name)}</title>",
            f'    <link rel="canonical" href="{escape_attr(canonical)}" />',
            f'    <link rel="stylesheet" href="{escape_attr(css_href)}" />',
            f"    <style>{DOC_EXTRA_CSS}</style>",
            "  </head>",
            "  <body>",
            '    <nav class="nav" aria-label="Primary navigation">',
            '      <div class="wrap nav-inner">',
            f'        <a class="brand" href="{escape_attr(brand_href)}">'
            '<span class="brand-mark" aria-hidden="true"></span>'
            f"<span>{html.escape(brand_name)}</span></a>",
            f'        <div class="nav-links">{"".join(nav_links)}</div>',
            "      </div>",
            "    </nav>",
            '    <main class="doc-article">',
            '      <div class="wrap">',
            f"        {breadcrumb}",
            body_html,
            "      </div>",
            "    </main>",
            '    <footer class="footer">',
            '      <div class="wrap">',
            f"        <div><strong>{html.escape(brand_name)}</strong>"
            f"<p>Rendered from <code>{html.escape(entry.repo_rel.as_posix())}</code>.</p></div>",
            f'        <p><a href="{escape_attr(source_url)}" target="_blank" '
            'rel="noopener noreferrer">View Markdown source on GitHub</a></p>',
            "      </div>",
            "    </footer>",
            "  </body>",
            "</html>",
            "",
        ]
    )


def escape_attr(value: str) -> str:
    return html.escape(value, quote=True)


def build_docs_html(
    root: Path = ROOT, output_dir: Path = DEFAULT_OUTPUT_DIR
) -> list[Path]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    entries = collect_docs(root)
    doc_map = {entry.repo_rel: entry.out_rel for entry in entries}
    asset_map = copy_doc_assets(root, output_dir)
    written: list[Path] = []
    for entry in entries:
        markdown = entry.source.read_text(encoding="utf-8")
        title = extract_title(markdown, entry.source.stem)
        renderer = DocMarkdownRenderer(markdown, entry, doc_map, asset_map, root)
        body_html = renderer.render()
        out_path = output_dir / entry.out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            render_doc_page(entry, body_html, title),
            encoding="utf-8",
            newline="\n",
        )
        written.append(out_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    written = build_docs_html(args.root.resolve(), args.out.resolve())
    print(f"Rendered {len(written)} Markdown documentation pages into {args.out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
