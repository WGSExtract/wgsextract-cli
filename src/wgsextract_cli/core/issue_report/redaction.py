"""Redact identifying names, paths, genetic data and secrets."""

from __future__ import annotations

import getpass
import os
import platform
import re
import urllib.parse
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from wgsextract_cli.core.tool_catalog import MANDATORY_TOOLS, OPTIONAL_TOOLS

# Extensions of files whose names are usually a sample or person's name.
_DATA_EXTENSIONS = tuple(
    sorted(
        {
            base + gz
            for base in (
                ".bam .cram .sam .ubam .bai .crai .csi .vcf .gvcf .g.vcf .bcf .fastq "
                ".fq .fa .fasta .fna .bed .txt .csv .tsv .json .zip .log .tbi .bw "
                ".bigwig .html .pdf .md .parquet .sqlite"
            ).split()
            for gz in ("", ".gz", ".bgz", ".zst", ".bz2", ".xz")
        },
        key=len,
        reverse=True,
    )
)
# Other file and folder names are shown only when they are programs.
_PROGRAM_EXTENSIONS = (".py", ".sh", ".exe", ".bat", ".dll", ".so", ".dylib", ".jar")
_SAFE_PROGRAMS = frozenset(
    name.lower()
    for name in (
        *MANDATORY_TOOLS,
        *OPTIONAL_TOOLS,
        "python",
        "pixi",
        "docker",
        "podman",
        "wsl",
        "conda",
        "perl",
        "bash",
        "wgsextract",
        "bin",
    )
)
_PATH_CHARS = r"(?:[^\s'\"`<>|;,()\[\]{}]|<sample>)"
_PATH_START = r"(?<![\w.~:/\\>-])(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/(?=[\w.~<-]))"
_PATH = re.compile(rf"{_PATH_START}{_PATH_CHARS}*")
# More words of an unquoted file name with spaces ("C:\Data\Jane Doe WGS.bam"):
# up to four, without separators, the last ending in a data extension.
# More words of an unquoted folder name with spaces, up to the next separator:
# "D:\Genomes\Robert Doe\out" is one path.
_SPACED_DIR_TAIL = re.compile(
    r"(?: (?:[^\s'\"`<>|;,()\[\]{}/\\]|<sample>)+){1,4}[\\/]" + _PATH_CHARS + "*"
)
_SPACED_NAME_TAIL = re.compile(
    r"(?: (?:[^\s'\"`<>|;,()\[\]{}/\\]|<sample>)+?){1,4}?(?:"
    + "|".join(re.escape(e) for e in _DATA_EXTENSIONS)
    + r")(?![\w.-])",
    re.IGNORECASE,
)
_QUOTED_PATH = re.compile(r"""(['"])((?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/)[^'"\r\n]*)\1""")
_BARE_FILE = re.compile(
    r"(?<![\w<>/\\.~-])(?:\w|<sample>)[\w.-]*?(?:"
    + "|".join(re.escape(e) for e in _DATA_EXTENSIONS)
    + r")(?![\w.-])",
    re.IGNORECASE,
)
# Relative paths: reduced when they have 2+ separators or end in a data file.
_RELATIVE_PATH = re.compile(
    r"(?<![\w<>/\\.~:@-])(?:[\w.-]|<sample>)+(?:[\\/](?:[\w.-]|<sample>)+)+[\\/]?"
)
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>'\"`]+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_SECRET_WORD = (
    r"[\w-]*(?:token|secret|password|passwd|credential|api[_-]?key|authorization)"
    r"[\w-]*"
)
# --token VALUE, token=VALUE, "api_key": "VALUE"; quoted values may hold spaces
# and escaped quotes, and an auth scheme (Basic, Bearer) goes with its value.
_SECRET_ASSIGNMENT = re.compile(
    rf"""(?ix)
    ( --?{_SECRET_WORD}(?:=|\s+)
    | (?<![\w-])(?P<key_quote>["']?){_SECRET_WORD}(?P=key_quote)\s*[=:]\s*
    )
    (?:(?:basic|bearer|digest|token)\s+)?(?!<[a-z])
    (?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s'",;}}\]]+)
    """
)
_KNOWN_TOKENS = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|hf_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}|xox[abprs]-[\w-]{10,})\b|(?i:\bbearer\s+)[\w.~+/-]{8,}=*"
)
_READ_NAMES = re.compile(
    # Illumina, SRA/ENA reads, MGI/BGI, PacBio and Nanopore (UUID) read names.
    r"\b[\w-]+:\d+:[\w-]+:\d+:\d+:\d+:\d+\b"
    r"|\b[SDE]RR\d{5,}\.\d+(?:\.\d+)?\b"
    r"|\b[A-Z]\d{8,10}L\dC\d{3}R\d{3}\d+\b"
    r"|\bm\d{5,}[eUs]?_\d{6}_\d{6}/\d+(?:/\w+)?"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_VARIANT_LINE = re.compile(
    r"^\s*(?:chr)?(?:\d{1,2}|[XYM]|MT)[\s:]+\d+\s+\S+\s+[ACGTN]+\s+[ACGTN.,*<>]"
    r"|^\s*(?:rs|i)\d+\s+\S+\s+\d+\s+[ACGTDI-]{1,2}\s*$"
    r"|^[ACGTN]{30,}$",
    re.IGNORECASE,
)
# What a log handler puts before a message: "INFO ", "2026-01-02 03:04:05,678
# INFO ", "[03:04:05] ERROR: " or a level symbol.
_LOG_PREFIX = re.compile(
    r"^\s*(?:\[?\d{4}-\d\d-\d\d[ T][\d:.,]+\]?\s+|\[\d\d:\d\d:\d\d\]\s+)?"
    r"(?:(?:DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b:?|[^\s<]{1,3}:)\s+"
)
# Lines that state a genetic result: haplogroups, sex, genotypes, repeat
# calls and their clinical class. Commands log these, even as warnings.
_RESULT_LINE = re.compile(
    r"haplogroup|pathogenic|benign|premutation|zygous|genotype|\bcarrier\b"
    r"|\b(?:fe)?male\b|\b[ACGTN]{2,}\s+\d+/\d+\b"
    r"|(?<![\w/.|])[01.][/|][01.](?![\w/.|])",
    re.IGNORECASE,
)
# Sample names inside messages: "SM:Jane", "samples (Jane, John)", "sample
# name: Jane", "sample(s): Jane, John", "naming the sample Jane", "sample Jane".
_NOT_A_SAMPLE = (
    "of|is|was|are|were|be|been|the|a|an|and|or|in|on|at|to|from|with|for|has|had"
    "|have|not|no|per|by|as|that|this|which|sheet|files?|folders?|csv|tsv|sex"
    "|counts?|size|reads?|data|names?|ids?|columns?|rows?|level|mode|rate"
)
_SAMPLE_MENTION = re.compile(
    r"(?i)(\bSM:)(?!<sample>(?:[\s,;)]|$))[^\s,;)]+"
    r"|(\bsamples?\s*\()(?!s\))[^)]*(?=\))"
    r"|(\bsamples?(?:\(s\))?(?:[ _](?:names?|ids?))?\s*[:=]\s*)"
    r"(?!(?:<sample>(?:,\s*|(?=[\s;)]|$)))+(?:[\s;)]|$))[^\s,;)]+(?:,\s*[^\s,;)]+)*"
    r"|(\b(?:naming|named|calling) the sample\s+)[^\s,;.)]+"
    rf"|(\bsamples?(?:[ _](?:names?|ids?))?\s+)(?!(?:{_NOT_A_SAMPLE})\b|<|\d+\b)"
    r"['\"]?[^\s,;:)'\"]*[^\s,;:)'\".]['\"]?"
)
# Public data servers whose URL paths name public files; other URLs keep only
# a non-identifying file name, and other servers become <server>.
_PUBLIC_HOSTS = frozenset(
    """
    ftp.ebi.ac.uk ftp.sra.ebi.ac.uk ftp.1000genomes.ebi.ac.uk ftp.ncbi.nlm.nih.gov
    ftp-trace.ncbi.nlm.nih.gov hgdownload.soe.ucsc.edu hgdownload.cse.ucsc.edu
    ftp.ensembl.org ftp.ccb.jhu.edu genomes.yseq.net ngi-igenomes.s3.amazonaws.com
    github.com api.github.com raw.githubusercontent.com objects.githubusercontent.com
    release-assets.githubusercontent.com dataverse.harvard.edu zenodo.org
    krishna.gs.washington.edu www.bio8.cs.hku.hk www.openbioinformatics.org
    strchive.org api.clinpgx.org pharmcat.org ybrowse.org www.yfull.com
    prefix.dev conda.anaconda.org pypi.org files.pythonhosted.org
    """.split()
)
# Shared storage hosts: only these public buckets.
_PUBLIC_BUCKETS = (
    "storage.googleapis.com/dm_alphamissense/",
    "storage.googleapis.com/gcp-public-data--gnomad/",
    "storage.googleapis.com/gcp-public-data--broad-references/",
    "s3-us-west-2.amazonaws.com/human-pangenomics/",
)
_PLACEHOLDER = re.compile(r"<[a-z][a-z -]*>")


def _word(token: str) -> re.Pattern[str]:
    """``token`` as a whole word, case-insensitively, outside ``<placeholders>``."""
    return re.compile(rf"(?<![A-Za-z0-9<]){re.escape(token)}(?![A-Za-z0-9>])", re.I)


def file_stems(path: str) -> set[str]:
    """Name parts of a file that may identify a person: ``John_Doe.hg38.cram``
    gives ``John_Doe.hg38`` and ``John_Doe``."""
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    stems = {name.split(".", 1)[0]}
    for extension in _DATA_EXTENSIONS:
        if name.lower().endswith(extension):
            stems.add(name[: -len(extension)])
            break
    return {s for s in stems if len(s) >= 3 and not s.isdigit()}


@dataclass
class Redactor:
    """Removes what could identify a person or leak a secret from text.

    ``sensitive`` strings (sample and file stems) and ``users``/``hosts``
    names are replaced wherever they appear; paths keep only a non-identifying
    file name (``<path>/samtools``, ``<path>/<file>.cram``); ``safe_names``
    (reference genome stems) may stay in file names. ``counts`` says what was
    removed, by kind."""

    home: str | None = None
    users: Sequence[str] = ()
    hosts: Sequence[str] = ()
    sensitive: Iterable[str] = ()
    safe_names: Iterable[str] = ()
    counts: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        # Longest first, so "John_Doe.hg38" goes before "John_Doe".
        def words(values: Iterable[str], minimum: int) -> list[re.Pattern[str]]:
            unique = {v for v in values if v and len(v) >= minimum}
            return [_word(v) for v in sorted(unique, key=len, reverse=True)]

        self._sensitive = words(self.sensitive, 3)
        self._users = words(self.users, 3)
        self._hosts = words(self.hosts, 3)
        self._all_users = {u.lower() for u in self.users if u}
        self._safe = {s.lower() for s in self.safe_names if s}

    @classmethod
    def for_this_computer(
        cls, *, sensitive: Iterable[str] = (), safe_names: Iterable[str] = ()
    ) -> Redactor:
        home = str(Path.home())
        users = {Path(home).name}
        try:
            users.add(getpass.getuser())
        except (KeyError, OSError):  # no user database entry (some containers)
            pass
        node = platform.node()
        hosts = {node, node.split(".", 1)[0]} if node else set()
        return cls(
            home=home,
            users=sorted(users),
            hosts=sorted(hosts),
            sensitive=sensitive,
            safe_names=safe_names,
        )

    def _sub(self, pattern: re.Pattern[str], repl: str, text: str, kind: str) -> str:
        text, n = pattern.subn(repl, text)
        self.counts[kind] += n
        return text

    def _sensitive_names(self, text: str) -> str:
        for pattern in self._sensitive:
            text = self._sub(pattern, "<sample>", text, "sample or file name")
        return text

    def _names(self, text: str) -> str:
        text = self._sensitive_names(text)
        for pattern in self._users:
            text = self._sub(pattern, "<user>", text, "user name")
        for pattern in self._hosts:
            text = self._sub(pattern, "<host>", text, "computer name")
        return text

    def _file_name(self, name: str) -> str:
        if not name or _PLACEHOLDER.fullmatch(name):
            return name
        if name.lower() in self._all_users:
            self.counts["user name"] += 1
            return "<user>"
        lower = name.lower()
        for extension in _DATA_EXTENSIONS:
            if lower.endswith(extension) and len(name) > len(extension):
                if name[: -len(extension)].lower() in self._safe:
                    return name
                self.counts["file name"] += 1
                return "<file>" + name[-len(extension) :]
        if lower in _SAFE_PROGRAMS or lower.endswith(_PROGRAM_EXTENSIONS):
            return self._names(name)
        self.counts["file name"] += 1
        return "<name>"

    def path(self, value: str) -> str:
        """One path, reduced to ``<path>/`` and a non-identifying file name.

        Paths inside this package or a Python environment keep their place
        there (``wgsextract_cli/core/x.py``), which is what a traceback needs."""
        trimmed = value.rstrip(".:")
        tail = value[len(trimmed) :]
        normal = trimmed.replace("\\", "/")
        for anchor in ("site-packages/", "wgsextract_cli/"):
            index = normal.find(anchor)
            if index >= 0:
                self.counts["folder"] += 1
                return f"<{anchor.rstrip('/')}>/{normal[index + len(anchor) :]}{tail}"
        name = normal.rstrip("/").rsplit("/", 1)[-1]
        if name in ("", "~") or re.fullmatch(r"[A-Za-z]:", name):
            name = ""
        self.counts["folder"] += 1
        reduced = "<path>/" + self._file_name(name) if name else "<path>"
        return reduced + tail

    def _paths(self, text: str) -> str:
        text = _QUOTED_PATH.sub(lambda m: m[1] + self.path(m[2]) + m[1], text)
        text = self._unquoted_paths(text)
        text = _RELATIVE_PATH.sub(self._relative, text)
        return _BARE_FILE.sub(lambda m: self._file_name(m[0]), text)

    def _unquoted_paths(self, text: str) -> str:
        """Absolute paths; one whose name has spaces is taken whole when the
        words after it end in a data extension and it is not a program."""
        pieces: list[str] = []
        last = 0
        for match in _PATH.finditer(text):
            if match.start() < last:
                continue
            end = match.end()
            name = match[0].rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
            lower = name.lower()
            if not (
                match[0].endswith(("/", "\\"))
                or lower.endswith(_DATA_EXTENSIONS)
                or lower in _SAFE_PROGRAMS
                or lower.endswith(_PROGRAM_EXTENSIONS)
            ):
                while folder := _SPACED_DIR_TAIL.match(text, end):
                    end = folder.end()
                tail = _SPACED_NAME_TAIL.match(text, end)
                end = tail.end() if tail else end
            pieces += [text[last : match.start()], self.path(text[match.start() : end])]
            last = end
        return "".join(pieces) + text[last:]

    def _relative(self, match: re.Match[str]) -> str:
        value = match[0]
        separators = len(re.findall(r"[\\/]", value.rstrip("\\/")))
        if separators >= 2 or value.lower().endswith(_DATA_EXTENSIONS):
            return self.path(value)
        return value

    def _line(self, line: str, *, results: bool = True) -> str:
        # Data checks look at the message, after any log prefix ("INFO ").
        prefix = _LOG_PREFIX.match(line)
        lead = prefix[0] if prefix else ""
        message = line[len(lead) :]
        if message.count("\t") >= 3 or _VARIANT_LINE.search(message):
            self.counts["data line"] += 1
            return lead + "<data line removed>"
        if results and _RESULT_LINE.search(message):
            self.counts["genetic result"] += 1
            return lead + "<line with a genetic result removed>"
        line = self._sub(_KNOWN_TOKENS, "<secret>", line, "secret")
        line = _SECRET_ASSIGNMENT.sub(self._secret, line)
        # Known names first, while a name with spaces is still whole.
        line = self._sensitive_names(line)
        line = _SAMPLE_MENTION.sub(self._sample_mention, line)
        pieces: list[str] = []
        last = 0
        for match in _URL.finditer(line):
            pieces += [self._local(line[last : match.start()]), self._url(match[0])]
            last = match.end()
        line = "".join(pieces) + self._local(line[last:])
        line = self._sub(_READ_NAMES, "<read>", line, "read name")
        return self._names(line)

    def _local(self, text: str) -> str:
        """Text outside URLs: emails, the home folder and paths."""
        text = self._sub(_EMAIL, "<email>", text, "email address")
        if self.home:
            home = re.compile(re.escape(self.home), re.I if os.name == "nt" else 0)
            text = self._sub(home, "~", text, "home folder")
        return self._paths(text)

    def _secret(self, match: re.Match[str]) -> str:
        self.counts["secret"] += 1
        value = match["value"]
        quote = value[0] if value[0] in "\"'" else ""
        return match[0][: match.start("value") - match.start()] + (
            f"{quote}<redacted>{quote}"
        )

    def _sample_mention(self, match: re.Match[str]) -> str:
        self.counts["sample or file name"] += 1
        return next(g for g in match.groups() if g is not None) + "<sample>"

    def _url(self, url: str) -> str:
        """A URL without credentials or query; its path only on public servers."""
        from wgsextract_cli.core.download_progress import redact_urls

        trimmed = url.rstrip(".,:;)!?")
        tail = url[len(trimmed) :]
        clean = redact_urls(trimmed)
        if clean != trimmed:
            self.counts["URL credentials or query"] += 1
        try:
            parts = urllib.parse.urlsplit(clean)
            host = (parts.hostname or "").lower()
        except ValueError:
            self.counts["URL"] += 1
            return "<url>" + tail
        path = urllib.parse.unquote(parts.path)
        public = host in _PUBLIC_HOSTS or f"{host}{path}".startswith(_PUBLIC_BUCKETS)
        named = any(p.search(path) for p in (*self._sensitive, *self._users))
        if public and not named:
            return clean + tail
        self.counts["URL path"] += 1
        server = parts.netloc if public else "<server>"
        name = path.rstrip("/").rsplit("/", 1)[-1]
        shown = "/<path>/" + self._file_name(name) if name else ""
        return f"{parts.scheme}://{server}{shown}{tail}"

    def text(self, value: object, *, results: bool = True) -> str:
        """Any text: a message, a log line, a setting. ``results=False`` keeps
        lines that mention genetic results (for what the user typed)."""
        return "\n".join(
            self._line(line, results=results) for line in str(value).splitlines()
        )

    def argv(self, argv: Sequence[str]) -> list[str]:
        """A command line; each value naming a file is reduced as a whole path,
        so names with spaces are never split."""
        from wgsextract_cli.core.results import redact_argv

        cleaned = redact_argv(list(argv))
        self.counts["secret"] += sum(arg.endswith("<redacted>") for arg in cleaned)
        return [self._arg(arg) for arg in cleaned]

    def _arg(self, arg: str) -> str:
        prefix, value = "", arg
        if arg.startswith("-") and "=" in arg:
            prefix, value = arg.split("=", 1)
            prefix += "="
        names_file = re.search(r"[\\/]", value) or value.lower().endswith(
            _DATA_EXTENSIONS
        )
        if not names_file or value.endswith("<redacted>") or _URL.match(value):
            return self.text(arg)
        value = self._sensitive_names(value)
        if not re.search(r"[\\/]", value):
            return self.text(prefix) + self._file_name(value)
        return self.text(prefix) + self.path(value)

    def summary(self) -> dict[str, int]:
        return {kind: n for kind, n in sorted(self.counts.items()) if n}
