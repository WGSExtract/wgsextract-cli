"""Result cards: one run's result as a self-contained page to view, print or share.

``--card`` (or ``result_card = true`` in config.toml) writes ``<name>.card.html``
beside the run's manifest. The card shows what the CLI printed, the metrics,
warnings and outputs, and the provenance a reader needs to trust it: the
WGS Extract and tool versions, the input file and its fingerprint, the
reference build and when it ran. It names files without their folders, so it
can be shared without revealing the user's directory layout; the manifest keeps
the full paths. The page has no scripts and loads nothing, so it opens offline
in any browser and in a sandboxed frame.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
from collections.abc import Iterable
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.builds import display_build
from wgsextract_cli.core.constants import MODEL_TAGS_BY_CODE, REF_GENOME_FILENAMES
from wgsextract_cli.core.download_progress import format_bytes
from wgsextract_cli.core.file_lock import exclusive_lock
from wgsextract_cli.core.read_scope import may_follow
from wgsextract_cli.core.results import (
    FINGERPRINT_PREFIX,
    MANIFEST_SUFFIX,
    CommandResult,
    FileRecord,
)
from wgsextract_cli.core.utils import WGSExtractError

CARD_SUFFIX: Final = ".card.html"
FAILED_MANIFEST: Final = ".failed" + MANIFEST_SUFFIX
CARD_ROLE: Final = "result_card"
NO_CARD: Final = "No result card: this run reads no sample and has no outputs."

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Where an absolute POSIX or Windows path may start.
_PATH_START = re.compile(r"(?<![\w:/.~])(?:[A-Za-z]:)?[\\/](?=[^\s/\\])")
# Characters a path in printed text can end before (spaces may be inside one).
_PATH_ENDS = frozenset(" \t'\"<>|,;:)")
_URL = re.compile(r"[A-Za-z][\w+.-]*://[^\s'\"<>]+")
_STATUS_LABEL = {"ok": "Completed", "failed": "Failed", "cancelled": "Cancelled"}


def card_path(
    result: CommandResult, outdir: str | os.PathLike[str] | None
) -> Path | None:
    """Where this run's card goes: beside its manifest, with the same stem.

    A report-only run that reads a sample (``info``) writes no manifest of its
    own; it gets one named after its input and command, so the card always
    has its full-path record beside it, in ``outdir`` or, when the command has
    none, where outputs go by default: config.toml's output_directory, else
    ``wgse/`` beside the input. A failed run whose input was missing names it
    after the input it was given. A report-only run without an input (``ref
    status``) is about no sample and gets no card. Neither is a server job's
    card outside the folders the server may use. None when there is none."""
    manifest = result.manifest_path()
    if manifest is None:
        from wgsextract_cli.core.config import output_directory

        local = [r.path for r in result.inputs if "://" not in r.path]
        given = result.provenance.params.get("input")
        if not local and result.status != "ok" and isinstance(given, str) and given:
            given = os.path.abspath(os.path.expanduser(given))
            # Not beside an input whose folder doesn't exist either: no folders from a typo.
            if outdir or os.path.isdir(os.path.dirname(given)):
                local = [given]
        folder = (outdir or output_directory(local)) if local else None
        if not folder:
            return None
        name = f"{_input_name(local[0])}_{'_'.join(result.command.split()) or 'wgsextract'}"
        manifest = Path(folder).expanduser().absolute() / (name + MANIFEST_SUFFIX)
    stem = manifest.with_name(manifest.name.removesuffix(MANIFEST_SUFFIX))
    card = stem.with_name(stem.name + CARD_SUFFIX)
    if not (may_follow(card) and may_follow(manifest)):
        return None
    if _is_result_data(result, card) or _is_result_data(result, manifest):
        logging.warning("Skipping result card: its path or manifest names a data file.")
        return None
    # Pinned: once the card is an output, it mustn't become the manifest's namesake.
    result.set_manifest_stem(stem)
    return card


def publish(result: CommandResult, card: Path | None) -> Path | None:
    """Write the run's manifest, after its card when it has one. The pair is
    written under one lock, kept in this user's WGS Extract state folder and
    named after the card's resolved path, so concurrent runs of a report on
    one sample (``info``) can't leave a card beside another run's manifest,
    whatever their temp folder or a symlink on the way. Without the lock the
    manifest is still written, without a card. A manifest written under the
    output's own name retires a failed attempt's card, as it does the failed
    attempt's manifest."""
    with contextlib.ExitStack() as held:
        if card is not None:
            try:
                from wgsextract_cli.core.last_run import state_directory

                card.parent.mkdir(parents=True, exist_ok=True)
                key = hashlib.sha256(os.fsencode(card.resolve())).hexdigest()[:32]
                locks = state_directory() / "card-locks"
                locks.mkdir(parents=True, exist_ok=True)
                held.enter_context(exclusive_lock(locks / key))
            except (OSError, WGSExtractError) as e:
                logging.warning(f"Could not write the result card: {e}")
                card = None
        if card is not None:
            try:
                write_card(result, card)
            except OSError as e:
                logging.warning(f"Could not write the result card: {e}")
        manifest = result.write_manifest()
    if manifest is not None and not manifest.name.endswith(FAILED_MANIFEST):
        stem = manifest.name.removesuffix(MANIFEST_SUFFIX)
        old_card = manifest.with_name(f"{stem}.failed{CARD_SUFFIX}")
        if not _is_result_data(result, old_card):
            old_card.unlink(missing_ok=True)
    return manifest


def _is_result_data(result: CommandResult, path: Path) -> bool:
    try:
        if not path.exists() and not path.is_symlink():
            return False
        resolved = path.resolve()
        for record in (*result.inputs, *result.outputs):
            if "://" in record.path:
                continue
            candidate = Path(record.path)
            if candidate.resolve() == resolved:
                return True
            with contextlib.suppress(OSError):
                if candidate.samefile(path):
                    return True
    except (OSError, RuntimeError):
        # If identity cannot be established, leave the potentially aliased file alone.
        return True
    return False


def write_card(result: CommandResult, path: Path) -> None:
    """Write the card for a finalised ``result`` and list it among its outputs."""
    from wgsextract_cli.core.run_log import is_run_log_path

    if is_run_log_path(path):
        raise FileExistsError(f"{path} is this run's log file; not replacing it.")
    manifest = result.manifest_path()
    html = render_card(result, manifest_name=manifest.name if manifest else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(path, retire_indexes=False) as staged:
        Path(staged).write_text(html, encoding="utf-8", newline="\n")
    result.add_output(path, CARD_ROLE, kind="html")
    for record in result.outputs:
        if record.role == CARD_ROLE:
            record.refresh()


def render_card(result: CommandResult, *, manifest_name: str | None = None) -> str:
    """The card's HTML. File paths appear as names only."""
    provenance = result.provenance
    status = result.status
    command = result.command or "wgsextract"

    facts: list[tuple[str, str]] = []
    for record in result.inputs:
        facts.append((_role_label(record.role), _file_fact(record)))
    if (reference := provenance.reference) is not None:
        name = Path(reference.path).name
        badge = _reference_badge(name, reference.build)
        facts.append(
            (
                "Reference",
                f"{escape(name)}{_muted(badge)}" if badge else escape(name),
            )
        )
    facts.append(("WGS Extract", escape(provenance.version)))
    if provenance.tools:
        tools = ", ".join(
            f"{name} {version}" for name, version in provenance.tools.items()
        )
        facts.append(("Tools", escape(tools)))
    facts.append(("Run", escape(_when(provenance.started, provenance.elapsed_s))))

    body: list[str] = []
    if result.error is not None:
        body.append(
            f'<section class="error"><h2>Error</h2><p>{escape(_private(result.error.message, result))}</p></section>'
        )
    if result.warnings:
        items = "".join(
            f"<li><code>{escape(w.code)}</code> {escape(_private(w.message, result))}</li>"
            for w in result.warnings
        )
        body.append(
            f'<section class="warnings"><h2>Warnings</h2><ul>{items}</ul></section>'
        )
    text = _private(_ANSI.sub("", result.text), result).strip("\n")
    if text:
        body.append(f"<section><h2>Result</h2><pre>{escape(text)}</pre></section>")
    if result.metrics:
        rows = _rows(
            (_label(k), _private(_scalar(v), result)) for k, v in result.metrics.items()
        )
        table = f'<table class="facts">{rows}</table>'
        # Printed results usually show the headline numbers already.
        body.append(
            f"<details><summary>Metrics</summary>{table}</details>"
            if text
            else f"<section><h2>Metrics</h2>{table}</section>"
        )
    outputs = [r for r in result.outputs if r.role != CARD_ROLE]
    if outputs:
        rows = "".join(
            f"<tr><td>{escape(Path(r.path).name)}</td><td>{escape(_label(r.role))}</td>"
            f'<td class="num">{escape(_size(r))}</td></tr>'
            for r in outputs
        )
        body.append(
            f'<section><h2>Output files</h2><table class="files">{rows}</table></section>'
        )

    footer = f"Generated by WGS Extract {escape(provenance.version)}."
    if manifest_name:
        footer += (
            f" Full paths and options are in <code>{escape(manifest_name)}</code>."
        )
    title = f"{command} · WGS Extract"
    return _PAGE.format(
        title=escape(title),
        version=escape(provenance.version),
        command=escape(command),
        status=escape(status),
        status_label=escape(_STATUS_LABEL.get(status, status)),
        facts=_rows(facts, escaped=True),
        body="\n".join(body),
        footer=footer,
    )


def _reference_badge(filename: str, build: str | None) -> str | None:
    """Model code + tag when the reference matches a known catalog FASTA, else Major Build."""
    code = next((c for c, f in REF_GENOME_FILENAMES.items() if f == filename), None)
    if code:
        tag = MODEL_TAGS_BY_CODE.get(code, "")
        return f"{code} [{tag}]" if tag else code
    return display_build(build)


def _input_name(path: str) -> str:
    """The input's name without a compression suffix, keeping its type, so
    ``s.bam`` and ``s.cram`` get cards of their own."""
    name = Path(path).name
    for suffix in (".gz", ".bgz"):
        name = name.removesuffix(suffix)
    return name


def _private(text: str, result: CommandResult) -> str:
    """``text`` without the user's folders: URLs, the run's files, its path
    options as given (missing or relative too) and any other existing path in
    it, reduced to their names; the home folder to ``~``."""
    text = _URL.sub(lambda m: _url_name(m.group(0)), text)
    paths = {r.path for r in (*result.inputs, *result.outputs)}
    if result.provenance.reference is not None:
        paths.add(result.provenance.reference.path)
    for value in result.provenance.params.values():
        if (
            isinstance(value, str)
            and "://" not in value
            and ("/" in value or "\\" in value)
        ):
            paths.update(
                {
                    value,
                    os.path.expanduser(value),
                    os.path.abspath(os.path.expanduser(value)),
                }
            )
    # Existing paths first, each as long as it goes, so a folder that prefixes
    # one doesn't leave its rest; then the given ones that don't exist.
    text = _existing_paths_named(text)
    for path in sorted(paths, key=len, reverse=True):
        if "://" not in path and len(Path(path).parts) > 1 and Path(path).name:
            text = text.replace(path, Path(path).name)
    home = str(Path.home())
    if len(home) > 1:
        text = re.sub(re.escape(home) + r"(?=[\\/\s'\"),;:]|$)", "~", text)
    return text


def _url_name(url: str) -> str:
    """A URL as the name of what it points to: no host, folders or query."""
    trailing = url[len(url.rstrip(".,;:)")) :]
    name = Path(urlsplit(url.rstrip(".,;:)")).path).name
    return (name or "URL") + trailing


def _existing_paths_named(text: str) -> str:
    """``text`` with each path that exists on this computer, spaces and all,
    reduced to its name: at each place a path may start, the longest stretch
    of that line, ending before a space or punctuation, that exists."""
    parts: list[str] = []
    done = 0
    for start in _PATH_START.finditer(text):
        if start.start() < done:
            continue
        line_end = text.find("\n", start.start())
        line_end = len(text) if line_end < 0 else line_end
        ends = [
            e
            for e in range(start.end(), line_end + 1)
            if e == line_end or text[e] in _PATH_ENDS
        ][:64]
        for end in reversed(ends):
            candidate = text[start.start() : end].rstrip(".")
            try:
                found = len(Path(candidate).parts) > 2 and Path(candidate).exists()
            except (OSError, ValueError):
                found = False
            if found:
                parts += [text[done : start.start()], Path(candidate).name]
                done = start.start() + len(candidate)
                break
    return "".join(parts) + text[done:]


def _file_fact(record: FileRecord) -> str:
    parts = [
        escape(Path(record.path).name)
        if "://" not in record.path
        else escape(_url_name(record.path))
    ]
    detail = [_size(record)] if record.size_bytes is not None else []
    if record.fingerprint:
        # Enough of the fingerprint to match a file by eye; the manifest has it all.
        detail.append(
            "fingerprint " + record.fingerprint.removeprefix(FINGERPRINT_PREFIX)[:12]
        )
    if detail:
        parts.append(_muted(" · ".join(detail)))
    return "".join(parts)


def _muted(text: str | None) -> str:
    return f' <span class="muted">{escape(text or "")}</span>'


def _rows(pairs: Iterable[tuple[str, str]], *, escaped: bool = False) -> str:
    return "".join(
        f"<tr><th>{escape(key)}</th><td>{value if escaped else escape(value)}</td></tr>"
        for key, value in pairs
    )


def _role_label(role: str) -> str:
    return "Input" if role == "input" else _label(role)


_ACRONYMS: Final = {
    "bam", "bed", "cram", "csv", "dna", "fastq", "html", "id", "json", "mt",
    "pgs", "r1", "r2", "sv", "tsv", "vcf",
}  # fmt: skip


def _label(key: str) -> str:
    """``mt_bam`` -> ``MT BAM``, ``snps`` -> ``SNPs``, ``avg_depth`` -> ``Avg depth``."""
    words = [
        "SNPs" if w == "snps" else w.upper() if w in _ACRONYMS else w
        for w in key.replace("_", " ").split()
    ]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _scalar(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1e6 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _size(record: FileRecord) -> str:
    if record.kind == "directory":
        return "folder"
    return format_bytes(record.size_bytes) if record.size_bytes is not None else "—"


def _when(started: str, elapsed_s: float) -> str:
    try:
        moment = datetime.fromisoformat(started.replace("Z", "+00:00"))
        stamp = moment.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        stamp = started
    if elapsed_s < 60:
        took = f"{elapsed_s:.1f} s"
    elif elapsed_s < 3600:
        took = f"{elapsed_s / 60:.1f} min"
    else:
        took = f"{elapsed_s / 3600:.1f} h"
    return f"{stamp}, took {took}"


_PAGE: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="color-scheme" content="light dark">
<meta name="generator" content="wgsextract {version}">
<title>{title}</title>
<style>
:root {{
  --bg: #f4f5f7; --card: #ffffff; --ink: #1d2330; --muted: #5d6677;
  --line: #dde1e8; --accent: #2457a6; --code-bg: #f6f7f9;
  --ok: #1f7a4a; --failed: #b42318; --cancelled: #8a6100;
  --warn-bg: #fff7e0; --warn-line: #e8c76a; --error-bg: #fdecea; --error-line: #f1a39b;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #14171c; --card: #1d2128; --ink: #e6e9ef; --muted: #9aa3b2;
    --line: #333a45; --accent: #7aa7f0; --code-bg: #171a20;
    --ok: #5cc98d; --failed: #ff8a80; --cancelled: #e5b94e;
    --warn-bg: #2e2714; --warn-line: #6b5a24; --error-bg: #34191a; --error-line: #7a3530;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 24px 16px; background: var(--bg); color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}}
main {{
  max-width: 860px; margin: 0 auto; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
}}
header {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  padding: 16px 20px; border-bottom: 3px solid var(--accent);
}}
.brand {{ font-weight: 600; color: var(--accent); letter-spacing: .02em; }}
h1 {{ margin: 2px 0 0; font-size: 20px; font-weight: 600; }}
h1 code {{ font-size: inherit; }}
.status {{ font-weight: 600; white-space: nowrap; }}
.status.ok {{ color: var(--ok); }} .status.failed {{ color: var(--failed); }}
.status.cancelled {{ color: var(--cancelled); }}
.content {{ padding: 4px 20px 16px; }}
h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 18px 0 8px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; vertical-align: top; padding: 4px 0; border-bottom: 1px solid var(--line); }}
th {{ font-weight: 500; color: var(--muted); width: 9.5em; padding-right: 12px; }}
.files td {{ padding-right: 12px; overflow-wrap: anywhere; }}
.num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.facts td {{ overflow-wrap: anywhere; }}
.muted {{ color: var(--muted); }}
code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }}
pre {{
  margin: 0; padding: 12px 14px; background: var(--code-bg); border: 1px solid var(--line);
  border-radius: 6px; white-space: pre-wrap; overflow-wrap: anywhere;
}}
details {{ margin-top: 14px; }}
summary {{ cursor: pointer; color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .06em; }}
.warnings, .error {{ border-radius: 6px; padding: 2px 14px 8px; margin-top: 14px; }}
.warnings {{ background: var(--warn-bg); border: 1px solid var(--warn-line); }}
.error {{ background: var(--error-bg); border: 1px solid var(--error-line); }}
.warnings h2, .error h2 {{ margin-top: 10px; }}
.warnings ul {{ margin: 0; padding-left: 18px; }}
.error p {{ margin: 0; white-space: pre-wrap; }}
footer {{ padding: 10px 20px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }}
@media print {{
  body {{ background: #fff; padding: 0; }}
  main {{ border: none; }}
}}
</style>
</head>
<body>
<main>
<header>
<div><div class="brand">WGS Extract</div><h1><code>{command}</code></h1></div>
<div class="status {status}">{status_label}</div>
</header>
<div class="content">
<table class="facts">{facts}</table>
{body}
</div>
<footer>{footer}</footer>
</main>
</body>
</html>
"""
