"""Pre-filled GitHub bug reports, redacted on this computer before anyone sees them.

A report describes this installation (versions, OS, CPU, RAM, tools, settings)
and, when there is one, a run: its command line, exit status, error, warnings
and log tail. It never contains genotypes, reads, file contents, sample names,
user or host names, full paths or secrets: every string taken from the run or
the settings goes through ``Redactor`` first, and the result's ``details`` are
left out entirely. Nothing is sent anywhere; the report becomes a GitHub
new-issue link that the user reviews and submits in their own browser.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wgsextract_cli import __version__
from wgsextract_cli.core.utils import UsageError

from .environment import environment_lines, settings_lines, tool_versions
from .redaction import Redactor, file_stems
from .runs import (
    RunRecord,
    _sensitive_names,
    batch_names,
    catalog_reference_names,
    header_samples,
    load_run,
    path_components,
)

__all__ = [
    "DEFAULT_LOG_LINES",
    "DESCRIPTION_FIELD",
    "DIAGNOSTICS_FIELD",
    "ISSUES_URL",
    "LABELS",
    "MAX_URL_LENGTH",
    "MIN_URL_LENGTH",
    "STEPS_PLACEHOLDER",
    "TEMPLATE",
    "IssueReport",
    "Redactor",
    "RunRecord",
    "batch_names",
    "build_report",
    "catalog_reference_names",
    "environment_lines",
    "file_stems",
    "header_samples",
    "issue_url",
    "load_run",
    "open_in_browser",
    "path_components",
    "settings_lines",
    "tool_versions",
]


ISSUES_URL = "https://github.com/WGSExtract/wgsextract-cli/issues/new"
# .github/ISSUE_TEMPLATE/bug_report.yml; its field ids are the query keys below.
TEMPLATE = "bug_report.yml"
DESCRIPTION_FIELD = "what-happened"
DIAGNOSTICS_FIELD = "diagnostics"
LABELS = ("bug",)
# Browsers and GitHub accept longer links, but ~8 KB is safe everywhere.
MAX_URL_LENGTH = 8000
# Below this, even the title and the form's own fields may not fit.
MIN_URL_LENGTH = 1000
DEFAULT_LOG_LINES = 40

# Metrics safe to share: what kind of data it is, never what it says about
# the person (sex, haplogroups and calls are left out).
_SHAREABLE_METRICS = re.compile(
    r"^(reference_model|sequencer|avg_read_len|is_paired|avg_insert_size|size_gb"
    r"|sorted|indexed|duplicates_marked|duplicate_pct|alt_aware)$"
    r"|coverage|depth|read_len"
)


@dataclass(frozen=True)
class IssueReport:
    """A redacted bug report, ready to show the user and open on GitHub.

    ``diagnostics`` is the full redacted text, saved locally by
    ``save``; ``link_diagnostics`` is what fits in ``url`` (see
    ``truncated``). Edit ``title``, ``description`` or ``link_diagnostics``
    with ``dataclasses.replace`` and call ``issue_url`` for a new link."""

    title: str
    description: str
    diagnostics: str
    link_diagnostics: str
    url: str
    labels: tuple[str, ...] = LABELS
    source: str = "none"
    redactions: Mapping[str, int] = field(default_factory=dict)
    truncated: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    file_name: str = ""

    @property
    def body(self) -> str:
        """The whole issue as Markdown, as saved in the diagnostics file."""
        description = self.description or (
            "_Describe what you did, what you expected and what happened instead._"
        )
        return (
            f"# {self.title}\n\n## What happened?\n\n{description}\n\n"
            f"## Steps to reproduce\n\n{STEPS_PLACEHOLDER}\n\n"
            f"## Diagnostics\n\n{self.diagnostics}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "labels": list(self.labels),
            "source": self.source,
            "redactions": dict(self.redactions),
            "truncated": list(self.truncated),
            "notes": list(self.notes),
            "url": self.url,
            "url_length": len(self.url),
            "diagnostics": self.diagnostics,
            "link_diagnostics": self.link_diagnostics,
            "file_name": self.file_name,
        }

    def save(self, directory: Path) -> Path:
        """Write ``body`` to ``directory/file_name`` (readable only by the user)."""
        from wgsextract_cli.core.atomic_output import atomic_write

        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self.file_name
        atomic_write(path, self.body.encode("utf-8"))
        return path


STEPS_PLACEHOLDER = (
    "1. Run `wgsextract ...`\n2. ...\n\nIf you can, reproduce it on public or "
    "fake data (`wgsextract example-genome list`, `wgsextract qc fake-data`) "
    "so the steps can be shared."
)


def issue_url(report: IssueReport, *, max_length: int = MAX_URL_LENGTH) -> str:
    """The GitHub new-issue link for ``report``'s title, description and
    ``link_diagnostics``. If it would exceed ``max_length``, the diagnostics are
    cut at a line, then the description and the title are shortened."""
    lines = report.link_diagnostics.splitlines()
    title, description = report.title, report.description
    cut = False
    while True:
        diagnostics = "\n".join(lines)
        if cut:
            if diagnostics.count("```") % 2:
                diagnostics += "\n```"
            diagnostics += (
                "\n\n_(cut to fit the link; paste the rest from the saved report)_"
            )
        url = _url(title, description, diagnostics, report.labels)
        if len(url) <= max_length:
            return url
        if lines:
            lines, cut = lines[: len(lines) - max(1, len(lines) // 8)], True
        elif len(description) > 40:
            description = description[: len(description) * 3 // 4].rstrip() + " [...]"
        elif description:
            description = ""
        elif len(title) > 30:
            title = title[: len(title) * 3 // 4].rstrip() + "..."
        else:
            raise UsageError(
                f"Cannot fit the report in a {max_length}-character link; "
                "use a larger --max-url-length."
            )


def _url(title: str, description: str, diagnostics: str, labels: Sequence[str]) -> str:
    query = {"template": TEMPLATE, "labels": ",".join(labels), "title": title}
    if description:
        query[DESCRIPTION_FIELD] = description
    query[DIAGNOSTICS_FIELD] = diagnostics
    return f"{ISSUES_URL}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}"


@dataclass
class _Sections:
    run: list[str]
    metrics: list[str]
    warnings: list[str]
    log: list[str]
    environment: list[str]
    tools: list[str]
    settings: list[str]

    def render(
        self,
        *,
        log_lines: int,
        warnings: int | None = None,
        tools: bool = True,
        settings: bool = True,
        note: str = "",
    ) -> str:
        parts: list[str] = []
        if self.run:
            parts += ["### Run", "", *self.run, *self.metrics]
            shown = self.warnings if warnings is None else self.warnings[:warnings]
            if shown:
                parts += ["- Warnings:", *shown]
            if len(shown) < len(self.warnings):
                parts.append(f"  - ... {len(self.warnings) - len(shown)} more")
            parts.append("")
        if self.log and log_lines:
            tail = self.log[-log_lines:]
            parts += [f"### Log (last {len(tail)} lines, redacted)", "", "```text"]
            parts += [line.replace("```", "'''") for line in tail] + ["```", ""]
        parts += ["### Environment", "", *self.environment, ""]
        if tools:
            parts += ["### Tools", "", *self.tools, ""]
        if settings:
            parts += ["### Settings", "", *self.settings, ""]
        if note:
            parts += [note, ""]
        return "\n".join(parts).rstrip() + "\n"


def _human_size(n: object) -> str:
    if not isinstance(n, (int, float)):
        return "size unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def _run_sections(run: RunRecord, redactor: Redactor) -> tuple[list[str], ...]:
    manifest = run.manifest
    provenance = manifest.get("provenance") or {}
    argv = redactor.argv([str(a) for a in provenance.get("argv") or []])
    lines = [f"- Report on: {run.source}"]
    if argv:
        lines.append(f"- Command: `wgsextract {' '.join(argv)}`")
    else:
        lines.append(f"- Command: `wgsextract {redactor.text(run.command)}`")
    status = manifest.get("status", "unknown")
    error = manifest.get("error") or {}
    kind = f", {error['kind']}" if error.get("kind") else ""
    lines.append(f"- Result: {status} (exit code {manifest.get('exit_code')}{kind})")
    if error.get("message"):
        message = redactor.text(error["message"]).strip().replace("\n", " ")
        lines.append(f"- Error: {message}")
    if provenance.get("version") and provenance["version"] != __version__:
        lines.append(f"- Run with wgsextract {provenance['version']}")
    if provenance.get("elapsed_s") is not None:
        lines.append(f"- Elapsed: {provenance['elapsed_s']} s")
    reference = provenance.get("reference") or {}
    if reference.get("path") or reference.get("build"):
        parts = [str(reference["build"])] if reference.get("build") else []
        if reference.get("path"):
            name = redactor.path(str(reference["path"])).removeprefix("<path>/")
            parts.append(f"`{name}`")
        lines.append(f"- Reference: {', '.join(parts)}")
    for label, records in (
        ("Inputs", manifest.get("inputs")),
        ("Outputs", manifest.get("outputs")),
    ):
        described = [
            f"{r.get('role', '?')}: {r.get('kind', '?')}, {_human_size(r.get('size_bytes'))}"
            for r in records or []
            if isinstance(r, Mapping)
        ]
        if described:
            lines.append(f"- {label}: " + "; ".join(described))
    metrics = [
        f"  - {key}: {redactor.text(f'{value:.4g}' if isinstance(value, float) else value)}"
        for key, value in (manifest.get("metrics") or {}).items()
        if _SHAREABLE_METRICS.search(key) and value is not None
    ]
    if metrics:
        metrics.insert(0, "- Input metrics:")
    warnings = [
        f"  - `{w.get('code')}`: {redactor.text(w.get('message', '')).strip()}"
        for w in manifest.get("warnings") or []
        if isinstance(w, Mapping)
    ]
    return lines, metrics, warnings


def _default_title(run: RunRecord | None, redactor: Redactor) -> str:
    if run is None:
        return f"Problem with wgsextract {__version__}"
    # Only the command, exit code and kind of error: the public title is the
    # likeliest part to be copied around, and a message may quote a name the
    # redactor doesn't know. The body has the redacted message.
    if run.failed:
        kind = str((run.manifest.get("error") or {}).get("kind") or "")
        kind = kind.replace("_", " ") if re.fullmatch(r"[a-z_]{1,30}", kind) else ""
        code = f"exit code {run.manifest.get('exit_code')}"
        detail = f"{code}, {kind}" if kind not in ("", "error", "failure") else code
        title = f"{run.command} failed ({detail})"
    else:
        title = f"Problem with '{run.command}'"
    return title if len(title) <= 100 else title[:97] + "..."


def build_report(
    *,
    run: RunRecord | None,
    title: str | None = None,
    description: str | None = None,
    log_lines: int = DEFAULT_LOG_LINES,
    max_url_length: int = MAX_URL_LENGTH,
    probe_tools: Callable[[Iterable[str]], Mapping[str, str]] | None = None,
    now: datetime | None = None,
) -> IssueReport:
    """Collect, redact and fit a report; see ``api.support.build_issue_report``."""
    if log_lines < 0:
        raise UsageError("--log-lines must be 0 or more.")
    if max_url_length < MIN_URL_LENGTH:
        raise UsageError(f"--max-url-length must be at least {MIN_URL_LENGTH}.")
    now = now or datetime.now(timezone.utc)
    manifest = run.manifest if run else {}
    sensitive, safe = _sensitive_names(manifest)
    redactor = Redactor.for_this_computer(sensitive=sensitive, safe_names=safe)

    run_lines: list[str] = []
    metrics: list[str] = []
    warnings: list[str] = []
    log: list[str] = []
    if run is not None:
        run_lines, metrics, warnings = _run_sections(run, redactor)
        log = redactor.text("\n".join(run.log_tail)).splitlines()
    used = (manifest.get("provenance") or {}).get("tools") or {}
    logging.debug("Checking tool versions for the report...")
    installed = dict((probe_tools or tool_versions)(used))
    tools = [
        f"- {name}: {redactor.text(line)}"
        for name, line in _tool_lines(used, installed).items()
    ]
    sections = _Sections(
        run=run_lines,
        metrics=metrics,
        warnings=warnings,
        log=log,
        environment=[redactor.text(line) for line in environment_lines()],
        tools=tools,
        settings=settings_lines(redactor),
    )
    title = (
        redactor.text(title, results=False).strip()
        if title
        else _default_title(run, redactor)
    )
    description = (
        redactor.text(description, results=False).strip() if description else ""
    )
    file_name = f"issue-report-{now.strftime('%Y%m%dT%H%M%SZ')}.md"
    diagnostics = sections.render(log_lines=log_lines)

    notes: list[str] = []
    if run is None:
        notes.append(
            "No recorded run to report on; the report describes this installation only."
        )
    elif run.source == "last run" and not run.failed:
        notes.append(
            "The last run succeeded. To report the last failed run instead, "
            "use --last-failed."
        )

    report = IssueReport(
        title=title,
        description=description,
        diagnostics=diagnostics,
        link_diagnostics=diagnostics,
        url="",
        source=run.source if run else "none",
        notes=tuple(notes),
        file_name=file_name,
    )
    report = _fit(report, sections, log_lines, max_url_length)
    return replace(report, redactions=redactor.summary())


def _tool_lines(
    used: Mapping[str, Any], installed: Mapping[str, str]
) -> dict[str, str]:
    """Tool versions: what the run recorded first, then what is installed now."""
    lines: dict[str, str] = {}
    for name in dict.fromkeys([*used, *installed]):
        recorded = str(used.get(name) or "").strip()
        now = installed.get(name)
        if not recorded:
            lines[name] = now or "not found"
        elif now is None or now == recorded:
            lines[name] = f"{recorded} (used by the run)"
        else:
            lines[name] = f"{recorded} (used by the run; installed now: {now})"
    return lines


def _fit(
    report: IssueReport, sections: _Sections, log_lines: int, max_length: int
) -> IssueReport:
    """Shorten the link's diagnostics until the link fits, least useful first."""
    log_steps = [log_lines]
    while log_steps[-1] > 5:
        log_steps.append(log_steps[-1] // 2)
    log_steps.append(0)
    shown_log = min(log_lines, len(sections.log))
    plans: list[dict[str, Any]] = [{"log_lines": n} for n in log_steps]
    plans += [{"log_lines": 0, "warnings": n} for n in (10, 3, 0)]
    plans += [
        {"log_lines": 0, "warnings": 0, "settings": False},
        {"log_lines": 0, "warnings": 0, "settings": False, "tools": False},
    ]
    for plan in plans:
        cuts = _describe_cuts(plan, sections, shown_log)
        note = (
            "> Shortened to fit in a GitHub link: "
            + "; ".join(cuts)
            + f". The full report is in `{report.file_name}`, saved on your "
            "computer; paste from it if it helps."
            if cuts
            else ""
        )
        text = sections.render(note=note, **plan)
        candidate = replace(report, link_diagnostics=text, truncated=tuple(cuts))
        url = _url(candidate.title, candidate.description, text, candidate.labels)
        if len(url) <= max_length:
            return replace(candidate, url=url)
    candidate = replace(
        candidate, truncated=(*candidate.truncated, "diagnostics cut at a line")
    )
    return replace(candidate, url=issue_url(candidate, max_length=max_length))


def _describe_cuts(
    plan: Mapping[str, Any], sections: _Sections, shown: int
) -> list[str]:
    cuts: list[str] = []
    kept = min(plan["log_lines"], len(sections.log))
    if kept < shown:
        cuts.append(
            f"log tail cut from {shown} to {kept} lines"
            if kept
            else "log tail left out"
        )
    if plan.get("warnings") is not None and plan["warnings"] < len(sections.warnings):
        cuts.append(
            f"{len(sections.warnings) - plan['warnings']} of "
            f"{len(sections.warnings)} warnings left out"
        )
    if plan.get("settings") is False:
        cuts.append("settings left out")
    if plan.get("tools") is False:
        cuts.append("tool versions left out")
    return cuts


def open_in_browser(url: str) -> bool:
    """Open ``url`` in the user's browser; False if no browser could be started."""
    import webbrowser

    try:
        return bool(webbrowser.open(url, new=2))
    except webbrowser.Error:
        return False
