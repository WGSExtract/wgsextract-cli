"""Load local run records and discover names that must be redacted."""

from __future__ import annotations

import csv
import itertools
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wgsextract_cli.core.utils import UsageError

from .redaction import _DATA_EXTENSIONS, _PROGRAM_EXTENSIONS, _SAFE_PROGRAMS, file_stems


@dataclass
class RunRecord:
    """The run a report is about, as read from the local files."""

    source: str  # "last run", "last failed run", or the kind of --run file
    manifest: dict[str, Any]
    log_tail: list[str] = field(default_factory=list)

    @property
    def command(self) -> str:
        return str(self.manifest.get("command") or "wgsextract")

    @property
    def failed(self) -> bool:
        return bool(self.manifest.get("exit_code"))


def load_run(path: Path | None, *, last_failed: bool = False) -> RunRecord | None:
    """The run to report on: ``path`` (a result manifest, a run log or its
    ``.provenance.json``) or else the last (failed) run this computer recorded."""
    from wgsextract_cli.core.last_run import load_last_run

    if path is None:
        document = load_last_run(failed=last_failed)
        if document is None:
            return None
        source = "last failed run" if last_failed else "last run"
        return RunRecord(source, document["result"], _log_tail_of(document))
    path = Path(path).expanduser()
    if not path.is_file():
        raise UsageError(f"--run: no such file: {path}")
    name = path.name
    if name.endswith(".log"):
        provenance = Path(str(path) + ".provenance.json")
        manifest = (
            _from_provenance(_read_json(provenance)) if provenance.is_file() else {}
        )
        return RunRecord("run log (--run)", manifest, _tail_lines(path))
    document = _read_json(path)
    if name.endswith(".provenance.json"):
        log = Path(str(path)[: -len(".provenance.json")])
        tail = _tail_lines(log) if log.is_file() else []
        return RunRecord("run log provenance (--run)", _from_provenance(document), tail)
    schema = document.get("schema")
    if schema == "wgsextract.last-run/1":
        return RunRecord(
            "recorded run (--run)", document["result"], _log_tail_of(document)
        )
    if schema == "wgsextract.result/1":
        return RunRecord("result manifest (--run)", document)
    raise UsageError(
        f"--run: {name} is not a result manifest (.manifest.json), run log (.log) "
        "or run provenance file (.provenance.json)."
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UsageError(f"--run: cannot read {path.name}: {e}") from e
    if not isinstance(document, dict):
        raise UsageError(f"--run: {path.name} is not a JSON object.")
    return document


def _log_tail_of(document: Mapping[str, Any]) -> list[str]:
    log_file = document.get("log_file")
    if log_file and Path(log_file).is_file():
        return _tail_lines(Path(log_file))
    return [str(line) for line in document.get("log_tail") or []]


def _tail_lines(path: Path, limit: int = 400) -> list[str]:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 256 * 1024))
            data = stream.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-limit:]


# Options naming files or samples, in run-log argv, by params key.
_INPUT_OPTIONS = {
    "--input": "input",
    "-i": "input",
    "--vcf-input": "vcf_input",
    "--r1": "r1",
    "--r2": "r2",
    "--genome": "genome",
    "--mother-vcf": "mother_vcf",
    "--father-vcf": "father_vcf",
}


def _from_provenance(document: Mapping[str, Any]) -> dict[str, Any]:
    """A run log's provenance as a (partial) result manifest."""
    from wgsextract_cli.commands.registry import COMMAND_TIERS

    argv = [str(a) for a in document.get("argv") or []]
    commands = {tier.name for tier in COMMAND_TIERS}
    words: list[str] = []
    params: dict[str, str] = {}
    for i, arg in enumerate(argv):
        option = arg.split("=", 1)[0]
        value = arg.split("=", 1)[1] if "=" in arg else (argv[i + 1 : i + 2] or [""])[0]
        if option in _INPUT_OPTIONS:
            params[_INPUT_OPTIONS[option]] = value
        if not words and arg in commands:
            words.append(arg)
        elif len(words) == 1 and re.fullmatch(r"[a-z][a-z0-9-]*", arg):
            words.append(arg)
        elif words:
            words.append("")
    status = document.get("status")
    return {
        "command": " ".join(w for w in words[:2] if w) or "wgsextract",
        "status": "failed" if status == "failed" else status or "unknown",
        "exit_code": 1 if status == "failed" else 0,
        "error": None,
        "inputs": [],
        "outputs": [],
        "metrics": {},
        "warnings": [],
        "provenance": {
            "version": document.get("wgsextract_version"),
            "argv": argv,
            "params": params,
            "tools": document.get("tool_versions") or {},
            "reference": document.get("reference"),
            "started": document.get("started_at"),
            "finished": document.get("finished_at"),
        },
    }


def catalog_reference_names() -> set[str]:
    """Stems of the reference genomes in the catalog (``hs38DH``, ``chm13v2.0``):
    file names that are safe to show. A user's own reference names are not."""
    from importlib import resources

    names: set[str] = set()
    try:
        text = (
            resources.files("wgsextract_cli")
            .joinpath("assets/reference/seed_genomes.csv")
            .read_text(encoding="utf-8")
        )
    except OSError:
        return names
    for row in csv.DictReader(text.splitlines()):
        for column in ("Final File Name", "Initial File Name"):
            names |= file_stems(row.get(column) or "")
    return names


def header_samples(path: str) -> set[str]:
    """Sample names in a local BAM/CRAM's read groups (SM) or a VCF's columns,
    which messages may quote even when the file name is neutral."""
    from wgsextract_cli.core.dependencies import get_tool_path
    from wgsextract_cli.core.process_registry import terminate_trees
    from wgsextract_cli.core.ultima import read_groups
    from wgsextract_cli.core.utils import WGSExtractError, popen

    lower = path.lower()
    if lower.endswith((".bam", ".cram", ".sam")):
        tool, args = "samtools", ["view", "-H"]
    elif lower.endswith((".vcf", ".vcf.gz", ".vcf.bgz", ".bcf")):
        tool, args = "bcftools", ["query", "-l"]
    else:
        return set()
    if not Path(path).is_file() or get_tool_path(tool) is None:
        return set()
    # The shared launcher runs a tool found only in its Pixi environment.
    try:
        process = popen(
            [tool, *args, path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, WGSExtractError):
        return set()
    try:
        output, _ = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        terminate_trees([process])
        return set()
    if tool == "bcftools":
        return set(output.split())
    return {group["SM"] for group in read_groups(output) if group.get("SM")}


# Folder names too common to name a person; replacing them everywhere would
# garble messages.
_GENERIC_FOLDERS = frozenset(
    """
    home users user mnt media volumes tmp temp var opt usr srv data desktop
    documents downloads genomes genome reference references ref refs out output
    outputs results result work scratch src library applications private share
    local roaming appdata program files program files (x86) .config .local .cache
    cache logs log state reports bam bams cram crams vcf vcfs fastq raw analysis
    wgse wgsextract wgsextract-cli
    """.split()
    + ["program files", "program files (x86)"]
)


def _path_strings(value: object) -> Iterable[str]:
    """Strings in a manifest that are local paths (also ``--opt=PATH``)."""
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _path_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _path_strings(item)
    elif isinstance(value, str) and "\n" not in value and "://" not in value:
        text = value.partition("=")[2] if value.startswith("-") else value
        if re.match(r"(?:[A-Za-z]:[\\/]|\\\\|~|\.{0,2}[\\/])", text):
            yield text


def _not_personal_folders() -> set[str]:
    """Folder names of this installation and the home folder's parents."""
    from wgsextract_cli.core.runtime import repo_root

    names = set(_GENERIC_FOLDERS)
    for path in (repo_root(), Path(sys.prefix), Path.home().parent):
        names |= {part.lower() for part in path.parts}
    return names


def path_components(paths: Iterable[str]) -> set[str]:
    """Folder (and data file stem) names in ``paths`` that may name a person,
    whole, so a name with spaces ("Robert Doe") is replaced in one piece."""
    common = _not_personal_folders()
    names: set[str] = set()
    for path in paths:
        for part in re.split(r"[\\/]", path.strip()):
            part = part.strip()
            lower = part.lower()
            if (
                len(part) < 3
                or lower in common
                or lower in _SAFE_PROGRAMS
                or lower.endswith(_DATA_EXTENSIONS)
                or lower.endswith(_PROGRAM_EXTENSIONS)
                or re.fullmatch(r"[\d\W_]+|[A-Za-z]:|~", part)
            ):
                continue
            names.add(part)
        if path.lower().endswith(_DATA_EXTENSIONS):
            names |= file_stems(path)
    return names


def batch_names(path: str) -> set[str]:
    """Sample names and input files listed in an ``analyze --batch`` file;
    a batch's errors name its samples."""
    try:
        with open(Path(path).expanduser(), newline="", errors="replace") as f:
            head = f.read(1024)
            f.seek(0)
            try:
                dialect: Any = csv.Sniffer().sniff(head)
            except csv.Error:
                dialect = csv.excel
            rows = list(itertools.islice(csv.reader(f, dialect=dialect), 100_000))
    except OSError:
        return set()
    if not rows:
        return set()
    header = [c.strip().lower() for c in rows[0]]
    names: set[str] = set()
    for row in rows[1:]:
        for column, cell in zip(header, row, strict=False):
            values = [v.strip() for v in re.split(r"[,;]", cell) if v.strip()]
            if column in ("name", "sample", "id"):
                names |= set(values)
            elif column in ("input", "bam", "cram") or "vcf" in column:
                names |= path_components(values)
    return names


def _sensitive_names(manifest: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Names that may identify a person (input and output file stems, every
    folder of a path the run used, --genome, a batch file's samples), and
    catalog reference stems that are safe to show."""
    from wgsextract_cli.core.config import configured_paths

    sensitive: set[str] = set()
    for record in [*(manifest.get("inputs") or []), *(manifest.get("outputs") or [])]:
        if isinstance(record, Mapping) and record.get("kind") != "directory":
            sensitive |= file_stems(str(record.get("path") or ""))
    configured = configured_paths()
    sensitive |= path_components(_path_strings([manifest, configured]))
    provenance = manifest.get("provenance") or {}
    params = provenance.get("params") or {}
    if isinstance(params.get("batch"), str):
        sensitive |= batch_names(params["batch"])
    for key in ("input", "vcf_input", "r1", "r2", "mother_vcf", "father_vcf"):
        if isinstance(params.get(key), str):
            sensitive |= file_stems(params[key])
    for key in ("genome", "sample", "sample_name"):
        if isinstance(params.get(key), str):
            sensitive.add(params[key])
    for key in ("input", "vcf_input", "mother_vcf", "father_vcf"):
        if isinstance(params.get(key), str):
            sensitive |= header_samples(params[key])
    safe = catalog_reference_names()
    # A file named after what it is (the app's result.json, a tool's gatk.log)
    # names nobody; replacing such words everywhere garbles the report.
    generic = _not_personal_folders() | _SAFE_PROGRAMS
    sensitive = {name for name in sensitive if name.lower() not in generic}
    return sensitive - safe, safe
