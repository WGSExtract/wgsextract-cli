"""``vcf acmg``: research-only ACMG/AMP classes from fastVEP (issue 629).

vep-rs (``vep run``) stays the consequence engine. fastVEP annotates the same
records again with its own transcript models (an Ensembl GFF3), the reference
and its supplementary databases (``--sa-dir``), and classifies each allele on
the transcript its ``--pick-allele`` order chooses, the order ``vcf table``
picks in. Only the class, its criteria and that transcript are copied into the
vep-rs VCF; fastVEP's own consequences are discarded. fastVEP doesn't fail
when a source is missing (the criteria it drives just don't fire, which reads
as VUS), so a run whose databases lack one of the required sources is refused.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from collections import Counter, deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from wgsextract_cli.api.vcf import VcfAcmgParams
from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import (
    get_tool_version,
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
    get_resource_defaults,
    popen,
)
from wgsextract_cli.core.variant_confidence import SPLIT_TAG
from wgsextract_cli.core.variant_files import (
    ensure_vcf_indexed,
    ensure_vcf_prepared,
    vcf_index_chromosomes,
)
from wgsextract_cli.core.variant_table import (
    csq_entries,
    csq_for_allele,
    parse_header,
    pick_csq,
    split_origin,
)

from .annotation_helpers import annotation_context, resource
from .basic import vcf_input_handler

ACMG_ANNOTATED = "acmg_annotated.vcf.gz"
ACMG_SUMMARY = "acmg_summary.json"
DISCLAIMER = (
    "Research use only; not a diagnosis. These ACMG/AMP classes are computed "
    "automatically by fastVEP from public databases, without the clinical, "
    "family and functional evidence (PS3/BS3, PS4, PP1, PP4) a classification "
    "needs, and a VUS can mean that nothing was checked. Don't act on them "
    "without a clinical genetics professional."
)
# The order `vcf table` picks a transcript in (pick_csq), so fastVEP's one row
# per allele is the table's transcript for all but a few records.
PICK_ORDER = "mane_select,canonical,biotype,rank"
# fastVEP's reference stack by the label its warnings use: every source but
# RepeatMasker (BP3) must be loaded.
REQUIRED_SOURCES = {
    "gnomAD allele frequencies": "gnomad",
    "ClinVar": "clinvar",
    "REVEL missense scores": "revel",
    "SpliceAI splice predictions": "spliceai",
    "PhyloP conservation": "phylop",
    "gnomAD gene constraint": "gnomad_genes",
    "gene-disease validity and inheritance (ClinGen, or OMIM)": "omim",
    "ClinVar protein and splice index": "clinvar_protein",
}
OPTIONAL_SOURCES = {"RepeatMasker repeat intervals": "repeatmasker"}
NOT_EVALUATED = ("PS3", "BS3", "PS4", "PP1", "PP4", "BP3")
_SUPPORTED_VERSION = re.compile(r"\b0\.5\.\d+\b")
# Printed once fastVEP has loaded its databases and checked them, before it
# reads a record (we always pass --pick-order).
_SETUP_DONE = "Using --pick order"
_MISSING = re.compile(r"sources the classifier reads are not loaded: (.+?)\.?$")
_GNOMAD_LACKS = re.compile(
    r"the gnomAD databases that are loaded hold nothing of chromosomes (.+?), which"
)
_HEADER = (
    '##INFO=<ID=ACMG_CLASS,Number=A,Type=String,Description="Research only, '
    "not a diagnosis: automated ACMG/AMP class from fastVEP (P, LP, VUS, LB, "
    'B) on ACMG_TRANSCRIPT">',
    '##INFO=<ID=ACMG_CRITERIA,Number=A,Type=String,Description="Research only: '
    'the ACMG/AMP criteria fastVEP found met, joined by &">',
    '##INFO=<ID=ACMG_TRANSCRIPT,Number=A,Type=String,Description="The '
    'transcript fastVEP classified the allele on">',
)
_UNSAFE = re.compile(r"[,;=\s]")


def require_opt_in(research_acmg: bool) -> None:
    if not research_acmg:
        raise UsageError(
            "vcf acmg runs only with --research-acmg (research_acmg in the API), "
            f"which confirms that you read this: {DISCLAIMER}"
        )


@dataclass
class _Tally:
    classes: Counter[str] = field(default_factory=Counter)
    criteria: Counter[str] = field(default_factory=Counter)
    transcripts: Counter[str] = field(default_factory=Counter)
    records: int = 0


def _version() -> str:
    raw = get_tool_version("fastvep") or ""
    match = _SUPPORTED_VERSION.search(raw)
    if not match:
        raise WGSExtractError(
            f"vcf acmg needs fastVEP 0.5 (found: {raw.strip() or 'unknown'}); its "
            "classifier and messages are what this command was validated with."
        )
    return match.group(0)


def _contig_key(name: str) -> str:
    key = name.removeprefix("chr")
    return "MT" if key == "M" else key


def _chromosome_list(text: str) -> set[str]:
    """fastVEP's describe_chromosomes ("1-3, 5, X and Y") as names."""
    names: set[str] = set()
    for part in re.split(r",\s*|\s+and\s+", text.strip()):
        low, _, high = part.partition("-")
        if high and low.isdigit() and high.isdigit():
            names.update(str(n) for n in range(int(low), int(high) + 1))
        elif part:
            names.add(_contig_key(part))
    return names


def _check_setup(lines: list[str], contigs: Iterable[str]) -> None:
    """Refuse what fastVEP only warns about before it reads a record: a
    required source not loaded, no gnomAD for a chromosome the input has, or
    anything else (a database it skipped, one of another assembly)."""
    text = "\n".join(lines)
    problems = []
    for line in lines:
        missing = _MISSING.search(line)
        if missing:
            labels = [p.rsplit(" (", 1)[0] for p in missing.group(1).split("; ")]
            required = [label for label in labels if label not in OPTIONAL_SOURCES]
            if required:
                problems.append(
                    "the databases lack " + ", ".join(required) + "; build them "
                    "with fastvep sa-build"
                )
        lacks = _GNOMAD_LACKS.search(line)
        if lacks:
            uncovered = _chromosome_list(lacks.group(1))
            hit = sorted(c for c in contigs if _contig_key(c) in uncovered)
            if hit:
                problems.append(
                    "the gnomAD database holds nothing of "
                    + ", ".join(hit[:10])
                    + (" ..." if len(hit) > 10 else "")
                )
        if line.startswith("warning:") and not (missing or lacks):
            # A database fastVEP skipped, or one of another assembly.
            problems.append(line.removeprefix("warning:").strip())
    if problems:
        raise WGSExtractError(
            "vcf acmg refused: " + "; ".join(problems) + ". A classification "
            "without them would quietly read as VUS. fastVEP said:\n" + text
        )


class _FastVep:
    """fastVEP reading the input (minus CSQ) from bcftools; stderr is read
    until its setup is done, then drained by a thread."""

    def __init__(self, command: list[str], strip: list[str], env: dict[str, str]):
        self.strip = popen(
            strip, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.proc = popen(
            command,
            stdin=self.strip.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert self.strip.stdout is not None and self.proc.stderr is not None
        self.strip.stdout.close()  # fastVEP holds the pipe now
        self.tail: deque[str] = deque(maxlen=40)
        self.strip_tail: deque[str] = deque(maxlen=10)
        assert self.strip.stderr is not None
        strip_err = self.strip.stderr
        self._strip_drain = threading.Thread(
            target=lambda: self.strip_tail.extend(strip_err), daemon=True
        )
        self._strip_drain.start()
        self.setup: list[str] = []
        self.ready = False
        # Others the run starts and close() must stop: the reader and writer.
        self.others: list[subprocess.Popen[str]] = []
        for line in self.proc.stderr:
            line = line.rstrip("\n")
            self.setup.append(line)
            logging.debug("fastVEP: %s", line)
            if line.startswith(_SETUP_DONE):
                self.ready = True
                break
        stderr = self.proc.stderr
        self._drain = threading.Thread(target=self._read, args=(stderr,), daemon=True)
        self._drain.start()

    def _read(self, stderr: IO[str]) -> None:
        for line in stderr:
            line = line.rstrip("\n")
            self.tail.append(line)
            if line.startswith("warning:"):
                logging.warning("fastVEP %s", line)

    @property
    def stdout(self) -> IO[str]:
        assert self.proc.stdout is not None
        return self.proc.stdout

    def close(self) -> None:
        for proc in (self.proc, self.strip, *self.others):
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdin, proc.stdout):
                if stream is not None and not stream.closed:
                    with contextlib.suppress(OSError):
                        stream.close()

    def require_ready(self) -> None:
        """fastVEP stopped before its setup was done: say why."""
        if not self.ready:
            self.proc.wait()
            detail = "\n".join(self.setup).strip()
            raise WGSExtractError(f"fastVEP failed before reading the input: {detail}")

    def check(self) -> None:
        self._drain.join(timeout=30)
        self._strip_drain.join(timeout=30)
        if self.strip.wait() != 0:
            detail = "".join(self.strip_tail).strip()
            raise WGSExtractError(f"bcftools failed reading the input: {detail}")
        if self.proc.wait() != 0:
            detail = "\n".join([*self.setup[-10:], *self.tail]).strip()
            raise WGSExtractError(f"fastVEP failed: {detail}")


def _records(lines: Iterable[str]) -> Iterator[list[str]]:
    for line in lines:
        if not line.startswith("#"):
            yield line.rstrip("\n").split("\t")


def _info_dict(info: str) -> dict[str, str]:
    if info in ("", "."):
        return {}
    return dict(kv.partition("=")[::2] for kv in info.split(";"))


def _feature(entry: Mapping[str, str] | None) -> str:
    return ((entry or {}).get("Feature") or "").split(".")[0]


def _clean(value: str | None) -> str:
    return _UNSAFE.sub("_", value) if value else "."


def _classify(
    fields: list[str],
    ours: list[dict[str, str]],
    theirs: list[dict[str, str]],
    tally: _Tally,
) -> tuple[list[str], list[str], list[str]] | None:
    """Per ALT: the class, criteria and transcript of fastVEP's row for the
    transcript the variant table picks, else fastVEP's own pick."""
    ref, alts = fields[3], fields[4].split(",")
    origin = split_origin(_info_dict(fields[7]).get(SPLIT_TAG))
    classes, criteria, transcripts = [], [], []
    for index in range(1, len(alts) + 1):
        rows = csq_for_allele(theirs, ref, alts, index, origin)
        if not rows:
            classes.append(".")
            criteria.append(".")
            transcripts.append(".")
            continue
        want = _feature(pick_csq(csq_for_allele(ours, ref, alts, index, origin)))
        row = next((r for r in rows if _feature(r) == want), None)
        if row is None:
            row = rows[0]
            tally.transcripts["other_transcript"] += 1
        else:
            tally.transcripts["same_transcript" if want else "no_transcript"] += 1
        acmg = row.get("ACMG") or ""
        if acmg:
            tally.classes[acmg] += 1
        met = [c for c in (row.get("ACMG_CRITERIA") or "").split("&") if c]
        tally.criteria.update(met)
        classes.append(_clean(acmg))
        criteria.append(_clean("&".join(met)))
        transcripts.append(_clean(row.get("Feature")))
    if all(c == "." for c in classes):
        return None
    return classes, criteria, transcripts


def _merge(source: IO[str], fastvep: IO[str], sink: IO[str], meta: list[str]) -> _Tally:
    """Copy the input VCF to ``sink`` with fastVEP's classes added. fastVEP
    writes the records in input order; one it skipped stays unclassified."""
    header = []
    for line in source:
        if line.startswith("#CHROM"):
            header.append(line)
            break
        header.append(line)
    ours_names = parse_header(header).csq_fields or []
    fv_header = []
    for line in fastvep:
        fv_header.append(line)
        if line.startswith("#CHROM"):
            break
    theirs_names = parse_header(fv_header).csq_fields or []
    if "ACMG" not in theirs_names:
        raise WGSExtractError("fastVEP wrote no ACMG column in its CSQ.")
    sink.writelines(header[:-1])
    sink.writelines(line + "\n" for line in (*_HEADER, *meta))
    sink.write(header[-1])

    tally = _Tally()
    theirs_iter = _records(fastvep)
    pending = next(theirs_iter, None)
    for line in source:
        fields = line.rstrip("\n").split("\t")
        tally.records += 1
        key = fields[0:2] + fields[3:5]
        if pending is None or pending[0:2] + pending[3:5] != key:
            tally.transcripts["not_classified"] += 1
            sink.write(line)
            continue
        info = _info_dict(fields[7])
        ours = csq_entries(info.get("CSQ"), ours_names)
        theirs = csq_entries(_info_dict(pending[7]).get("CSQ"), theirs_names)
        pending = next(theirs_iter, None)
        values = _classify(fields, ours, theirs, tally)
        if values is not None:
            added = ";".join(
                f"{tag}={','.join(v)}"
                for tag, v in zip(
                    ("ACMG_CLASS", "ACMG_CRITERIA", "ACMG_TRANSCRIPT"),
                    values,
                    strict=True,
                )
            )
            fields[7] = added if fields[7] in ("", ".") else f"{fields[7]};{added}"
        sink.write("\t".join(fields) + "\n")
    if pending is not None:
        raise WGSExtractError(
            "fastVEP wrote a record the input doesn't have at that point: "
            + " ".join(pending[0:2] + pending[3:5])
        )
    return tally


def _sources(sa_dir: str) -> list[dict[str, object]]:
    found = []
    for path in sorted(Path(sa_dir).iterdir()):
        if path.suffix in {".osa", ".osa2", ".oga", ".oib"} and path.is_file():
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            found.append(
                {
                    "file": path.name,
                    "size_bytes": stat.st_size,
                    "modified": modified.isoformat(timespec="seconds"),
                }
            )
    return found


def run_acmg(params: VcfAcmgParams) -> None:
    require_opt_in(params.research_acmg)
    tools = ["bcftools", "bgzip", "tabix", "fastvep"]
    verify_dependencies(tools)
    log_dependency_info(tools)
    version = _version()
    input_file, outdir, lib = annotation_context(params, ACMG_ANNOTATED)
    if canonical_build(lib.build) != "hg38":
        raise WGSExtractError(
            f"vcf acmg supports GRCh38 only; {input_file} is "
            f"{lib.build or 'of an unknown build'}."
        )
    if not lib.fasta:
        raise WGSExtractError("vcf acmg needs the GRCh38 reference FASTA; pass --ref.")
    gff3 = resource(params, "gff3", None)
    if not gff3:
        raise UsageError(
            "vcf acmg needs an Ensembl GRCh38 GFF3 (--gff3, or acmg_gff3 in "
            "config.toml), such as Homo_sapiens.GRCh38.116.gff3.gz from "
            "https://ftp.ensembl.org/pub/release-116/gff3/homo_sapiens/."
        )
    sa_dir = expand_path(params.sa_dir) if params.sa_dir else None
    if not sa_dir or not Path(sa_dir).is_dir():
        raise UsageError(
            "vcf acmg needs a folder of fastVEP databases (--sa-dir, or acmg_sa_dir "
            "in config.toml)" + (f"; {sa_dir} is not a folder." if sa_dir else ".")
        )
    vcf = ensure_vcf_prepared(input_file)
    header = _vcf_header(vcf)
    if not parse_header(header).csq_fields:
        raise WGSExtractError(
            f"{vcf} has no VEP CSQ annotation; run `wgsextract vep run` on it first, "
            "so the classes are taken on the transcript the variant table shows."
        )
    contigs = vcf_index_chromosomes(vcf)
    result = current_result()
    result.add_input(gff3, "transcript_models")
    result.add_input(sa_dir, "acmg_databases")
    warn("acmg_research_only", DISCLAIMER)

    threads = int(get_resource_defaults(params.threads)[0])
    fastvep = get_tool_path("fastvep") or "fastvep"
    command = [
        fastvep, "annotate", "-i", "-", "-o", "-",
        "--gff3", gff3, "--fasta", lib.fasta, "--sa-dir", sa_dir,
        "--acmg", "--pick-allele", "--pick-order", PICK_ORDER, "--no-progress",
    ]  # fmt: skip
    strip = ["bcftools", "annotate", "--no-version", "-x", "INFO/CSQ", "-Ov", vcf]
    meta = [
        f'##WGSE_ACMG=<Disclaimer="{DISCLAIMER}",Tool="fastVEP {version}",'
        f'PickOrder="{PICK_ORDER}",GFF3="{Path(gff3).name}",'
        f'Databases="{Path(sa_dir).name}">'
    ]
    out_vcf = str(Path(outdir, ACMG_ANNOTATED))
    env = {**os.environ, "RAYON_NUM_THREADS": str(threads)}
    logging.info("Classifying %s with fastVEP %s (research only)...", vcf, version)
    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_acmg_") as work:
        partial = str(Path(work, ACMG_ANNOTATED))
        run = _FastVep(command, strip, env)
        try:
            run.require_ready()
            _check_setup(run.setup, contigs)
            view = popen(
                ["bcftools", "view", "--no-version", vcf],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            run.others.append(view)
            with open(partial, "wb") as raw:
                bgzip = popen(
                    ["bgzip", "-@", str(threads), "-c"],
                    stdin=subprocess.PIPE,
                    stdout=raw,
                    text=True,
                )
                run.others.append(bgzip)
                assert view.stdout is not None and bgzip.stdin is not None
                tally = _merge(view.stdout, run.stdout, bgzip.stdin, meta)
                bgzip.stdin.close()
                if bgzip.wait() != 0 or view.wait() != 0:
                    raise WGSExtractError("Writing the classified VCF failed.")
            run.check()
        finally:
            run.close()
        Path(partial).replace(out_vcf)
    for index in (out_vcf + ".tbi", out_vcf + ".csi"):
        Path(index).unlink(missing_ok=True)
    ensure_vcf_indexed(out_vcf)

    summary_path = Path(outdir, ACMG_SUMMARY)
    summary = {
        "disclaimer": DISCLAIMER,
        "tool": {"name": "fastVEP", "version": version},
        "pick_order": PICK_ORDER,
        "input": str(Path(input_file).absolute()),
        "gff3": str(Path(gff3).absolute()),
        "reference": str(Path(lib.fasta).absolute()),
        "databases": {
            "folder": str(Path(sa_dir).absolute()),
            "files": _sources(sa_dir),
        },
        "sources_required": sorted(REQUIRED_SOURCES.values()),
        "sources_not_used": sorted(OPTIONAL_SOURCES.values()),
        "criteria_not_evaluated": list(NOT_EVALUATED),
        "records": tally.records,
        "classes": dict(tally.classes.most_common()),
        "criteria": dict(tally.criteria.most_common()),
        "transcript_choice": dict(tally.transcripts.most_common()),
    }
    summary_path.write_text(json.dumps(summary, indent=1) + "\n")
    result.add_output(out_vcf, "annotated_vcf", metrics={"variants": tally.records})
    result.add_output(summary_path, "acmg_summary")
    result.add_metrics(
        **{
            f"acmg_{k.lower()}": tally.classes.get(k, 0)
            for k in ("P", "LP", "VUS", "LB", "B")
        },
        acmg_other_transcript=tally.transcripts.get("other_transcript", 0),
        acmg_not_classified=tally.transcripts.get("not_classified", 0),
    )
    logging.info(
        "Wrote %s and %s (research only, not a diagnosis).", out_vcf, summary_path
    )


def _vcf_header(vcf: str) -> list[str]:
    from wgsextract_cli.core.utils import run_command

    res = run_command(["bcftools", "view", "-h", vcf], capture_output=True)
    return (res.stdout or "").splitlines()


cmd_acmg = vcf_input_handler(VcfAcmgParams, run_acmg)
