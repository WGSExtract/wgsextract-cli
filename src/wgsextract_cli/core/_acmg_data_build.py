"""Download, verify, stage and build the databases in an ACMG data plan."""

import base64
import hashlib
import logging
import re
import shutil
import subprocess
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import Future, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.acmg_manifest import (
    MANIFEST,
    load_manifest,
    save_manifest,
)
from wgsextract_cli.core.acmg_regions import (
    REGION_FLAGS,
    region_bed,
    region_name,
    region_tracks,
    repeatmasker_bed,
)
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.download_progress import CancelEvent, format_bytes
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.ref_library import download_file
from wgsextract_cli.core.utils import (
    WGSExtractError,
    popen,
    wait_with_cancel,
)

from . import _acmg_data_plan
from ._acmg_data_plan import (
    _MB,
    ASSEMBLY,
    BY_KEY,
    CHROMOSOMES,
    GFF3_TERMS,
    PHYLOP_FILE,
    Plan,
    Step,
    _head,
    _text,
    library_spliceai,
    library_spliceai_path,
    source_url,
)
from ._acmg_data_sources import (
    clingen_to_genemap,
    require_grch38_vcf,
    split_revel,
    tabix_contigs,
)
from ._acmg_data_tools import add_region_flags, run_tool, stream_reduced


def _gcs_md5(headers: Any) -> str | None:
    """The MD5 Google Cloud Storage gives in x-goog-hash, as hex."""
    for value in headers.get_all("x-goog-hash") or []:
        for part in value.split(","):
            kind, _, digest = part.strip().partition("=")
            if kind == "md5":
                return base64.b64decode(digest).hex()
    return None


def _http_date(headers: Any) -> str:
    stamp = headers.get("Last-Modified")
    try:
        return parsedate_to_datetime(stamp).date().isoformat() if stamp else ""
    except (TypeError, ValueError):
        return ""


def bsd_sum(path: Path) -> tuple[int, int]:
    """``sum``'s default (BSD) checksum and 1 KB block count, which Ensembl's
    CHECKSUMS files give."""
    checksum = size = 0
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            size += len(chunk)
            for byte in chunk:
                checksum = (((checksum >> 1) | ((checksum & 1) << 15)) + byte) & 0xFFFF
    return checksum, -(-size // 1024)


def _digests(path: Path) -> tuple[str, str]:
    """(SHA-256, MD5) of a file, read once."""
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _sha256(path: Path, cancel: CancelEvent | None = None) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            if cancel and cancel.is_set():
                raise WGSExtractError("Cancelled.")
            sha.update(chunk)
    return sha.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stop:
    """Set when the run stops (Ctrl-C, SIGTERM) or its caller cancels it: the
    builds still running stop at their next check and their tools are killed."""

    def __init__(self, outer: CancelEvent | None) -> None:
        self.outer = outer
        self.event = threading.Event()

    def set(self) -> None:
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set() or bool(self.outer and self.outer.is_set())


def sa_build(
    fastvep: str,
    key: str,
    inputs: Path,
    work: Path,
    name: str,
    cancel: CancelEvent | None,
) -> tuple[list[Path], int]:
    """``fastvep sa-build`` of source ``key`` from ``inputs`` into a fresh
    ``work`` folder as ``name``: the files it wrote, and its record count."""
    source = BY_KEY[key]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    command = [
        fastvep, "sa-build", "--source", source.key, "-i", str(inputs),
        "-o", str(work / name), "--assembly", ASSEMBLY, "--no-progress",
    ]  # fmt: skip
    if source.chunk_bits:
        command += ["--chunk-bits", str(source.chunk_bits)]
    if key == "repeatmasker":
        command[3] = "custom_bed"
        command += ["--name", key]
    logging.info("Building %s...", name)
    output = run_tool(command, cancel)
    pattern = (
        r"Parsed ([\d,]+) interval records"
        if key == "repeatmasker"
        else r"Wrote: .*\(([\d,]+) (?:records|genes)"
    )
    records = re.findall(pattern, output)
    built = sorted(work.iterdir())
    if not records or not built:
        raise WGSExtractError(
            f"fastvep sa-build wrote no {name} database: {output[-500:]}"
        )
    return built, int(records[-1].replace(",", ""))


class _Build:
    """A run of a plan: the manifest is saved as each step completes."""

    def __init__(
        self,
        plan: Plan,
        cancel: CancelEvent | None,
        on_output: Callable[[Path], None],
        workers: int = 1,
    ) -> None:
        self.plan = plan
        self.fastvep = get_tool_path("fastvep") or "fastvep"
        self.cancel = Stop(cancel)
        self.on_output = on_output
        self.lock = threading.Lock()
        self.manifest = load_manifest(plan.sa_dir)
        self.manifest.update(format=1, assembly=ASSEMBLY, site_data=plan.site_data)
        if plan.spliceai is not None:
            self.manifest["spliceai_file"] = str(plan.spliceai.resolve())
        self.manifest.setdefault("sources", {})
        self.manifest.setdefault("databases", {})
        self.staging = plan.sa_dir / ".building"
        self.phylop_md5: dict[str, str] | None = None
        # REVEL CSVs cut and not yet built: at most one per worker.
        self.revel_slots = threading.Semaphore(max(1, workers))
        self.spliceai: Path | None = plan.spliceai

    def _save(self) -> None:
        self.manifest["updated"] = _now()
        path = self.plan.sa_dir / MANIFEST
        save_manifest(path, self.manifest)

    def record_source(
        self,
        key: str,
        file: str,
        entry: dict[str, Any],
        *,
        label: str | None = None,
        url: str | None = None,
    ) -> None:
        """Record a source file; ``label`` and ``url`` say where a file that
        isn't the publisher download came from."""
        source = BY_KEY[key]
        with self.lock:
            info = self.manifest["sources"].setdefault(key, {})
            info.update(
                label=source.label if label is None else label,
                url=source_url(key) if url is None else url,
                terms=source.terms,
            )
            if "release" in entry:
                info["release"] = entry.pop("release")
            info.setdefault("files", {})[file] = entry
            self._save()

    def record_database(self, step: Step, entry: dict[str, Any]) -> None:
        with self.lock:
            self.manifest["databases"][step.name] = {
                "source": step.source,
                "chromosome": step.chromosome,
                **entry,
            }
            self._save()

    def fetch(
        self,
        url: str,
        dest: Path,
        *,
        md5: str | None = None,
        check: Callable[[Path], str] | None = None,
    ) -> dict[str, Any]:
        """Download ``url`` to ``dest`` (resuming a ``.partial``), check it
        against ``md5`` or with ``check`` (which says what it verified), and
        return its manifest entry."""
        if not read_scope.may_follow(dest):
            raise WGSExtractError(f"{dest} leads outside the folders this run may use.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            logging.info("Downloading %s...", url)
            # Not the download cache: it is keyed by the URL, and most of these
            # URLs name the latest release, checked only after the download.
            if not download_file(
                url,
                str(dest),
                None,
                self.cancel,
                resolve_release_digest=False,
                use_cache=False,
            ):
                raise WGSExtractError(
                    f"Downloading {url} failed; rerun the command to resume it."
                )
        sha256, actual_md5 = _digests(dest)
        verified = "none published"
        if md5 is not None:
            if actual_md5 != md5.lower():
                dest.unlink()
                raise WGSExtractError(
                    f"{dest.name} doesn't match the MD5 its publisher gives "
                    f"({actual_md5}, expected {md5}), so it was removed: it may "
                    "have changed during the download. Rerun to fetch it again."
                )
            verified = f"MD5 {md5.lower()}"
        elif check is not None:
            verified = check(dest)
        return {
            "file": str(dest),
            "bytes": dest.stat().st_size,
            "sha256": sha256,
            "verified": verified,
            "downloaded": _now(),
        }

    def database(
        self,
        step: Step,
        inputs: Path,
        *,
        keep_input: bool = True,
        regions: dict[str, str] | None = None,
    ) -> None:
        """``fastvep sa-build`` into the staging folder, then moved in."""
        work = self.staging / step.name
        built, records = sa_build(
            self.fastvep, step.source, inputs, work, step.name, self.cancel
        )
        for path in built:
            path.replace(self.plan.sa_dir / path.name)
            self.on_output(self.plan.sa_dir / path.name)
        shutil.rmtree(work, ignore_errors=True)
        if not keep_input:
            inputs.unlink(missing_ok=True)
        main = self.plan.sa_dir / built[0].name
        self.record_database(
            step,
            {
                "file": main.name,
                "files": [p.name for p in built],
                "records": records,
                "bytes": main.stat().st_size,
                "sha256": _digests(main)[0],
                "built": _now(),
                **({"region_tracks": regions} if regions else {}),
            },
        )

    def absent(self, step: Step, reason: str) -> None:
        logging.warning("No %s database: %s.", step.name, reason)
        self.record_database(step, {"absent": True, "reason": reason})

    def run(self, step: Step) -> None:
        if self.cancel and self.cancel.is_set():
            raise WGSExtractError("Cancelled.")
        getattr(self, f"step_{step.source}")(step)

    def step_gff3(self, step: Step) -> None:
        base = _acmg_data_plan.ENSEMBL.format(release=self.plan.release)
        name = self.plan.gff3.name
        listed = {
            parts[2]: (int(parts[0]), int(parts[1]))
            for line in _text(base + "CHECKSUMS").splitlines()
            if len(parts := line.split()) == 3 and parts[0].isdigit()
        }
        if name not in listed:
            raise WGSExtractError(
                f"Ensembl release {self.plan.release} has no {name}; check "
                "--ensembl-release (it should match your vep-rs cache)."
            )

        def check(path: Path) -> str:
            found = bsd_sum(path)
            if found != listed[name]:
                path.unlink()
                raise WGSExtractError(
                    f"{name} doesn't match Ensembl's CHECKSUMS {listed[name]} "
                    f"(got {found}), so it was removed; rerun to fetch it again."
                )
            return f"Ensembl CHECKSUMS sum {found[0]} {found[1]}"

        entry = self.fetch(base + name, self.plan.gff3, check=check)
        with self.lock:
            self.manifest["gff3"] = {
                **entry,
                "url": base + name,
                "release": self.plan.release,
                "terms": GFF3_TERMS,
            }
            self._save()
        self.on_output(self.plan.gff3)

    def step_clinvar(self, step: Step) -> None:
        digest, _, remote = (
            _text(_acmg_data_plan.CLINVAR_URL + ".md5").strip().partition(" ")
        )
        release = re.search(r"(\d{8})", remote)
        dest = self.plan.sources_dir / Path(remote.strip()).name
        entry = self.fetch(_acmg_data_plan.CLINVAR_URL, dest, md5=digest)
        entry["release"] = release.group(1) if release else ""
        self.record_source("clinvar", dest.name, entry)
        self.database(step, dest)

    def step_clinvar_protein(self, step: Step) -> None:
        digest = _text(_acmg_data_plan.VARIANT_SUMMARY_URL + ".md5").split()[0]
        release = _http_date(_head(_acmg_data_plan.VARIANT_SUMMARY_URL))
        dest = self.plan.sources_dir / Path(_acmg_data_plan.VARIANT_SUMMARY_URL).name
        entry = self.fetch(_acmg_data_plan.VARIANT_SUMMARY_URL, dest, md5=digest)
        entry["release"] = release
        self.record_source("clinvar_protein", dest.name, entry)
        self.database(step, dest)

    def step_gnomad_genes(self, step: Step) -> None:
        dest = self.plan.sources_dir / Path(_acmg_data_plan.CONSTRAINT_URL).name
        entry = self.fetch(
            _acmg_data_plan.CONSTRAINT_URL,
            dest,
            md5=_gcs_md5(_head(_acmg_data_plan.CONSTRAINT_URL)),
        )
        entry["release"] = "4.1"
        self.record_source("gnomad_genes", dest.name, entry)
        self.database(step, dest)

    def step_omim(self, step: Step) -> None:
        dest = self.plan.sources_dir / "clingen_gene_validity.csv"
        entry = self.fetch(_acmg_data_plan.CLINGEN_URL, dest)
        entry["release"] = entry["downloaded"][:10]
        self.record_source("omim", dest.name, entry)
        converted = self.staging / "clingen_gdv.tsv"
        converted.parent.mkdir(parents=True, exist_ok=True)
        genes = clingen_to_genemap(dest, converted)
        logging.info("ClinGen gene-disease validity: %d genes.", genes)
        self.database(step, converted, keep_input=False)

    def step_gnomad(self, step: Step) -> None:
        assert step.chromosome
        regions = region_tracks(self.plan.sa_dir, self.manifest)
        if len(regions) != len(REGION_FLAGS):
            raise WGSExtractError(
                "Build the gnomAD low-complexity and segdup tracks first."
            )
        previous = self.manifest["databases"].get(step.name, {})
        current = self.plan.sa_dir / str(previous.get("file") or "")
        if (
            previous.get("region_tracks") == regions
            and current.is_file()
            and current.stat().st_size == previous.get("bytes")
        ):
            logging.info("%s already has the restored gnomAD tracks.", step.name)
            return
        url = source_url("gnomad", step.chromosome)
        headers = _head(url)
        size = int(headers.get("Content-Length") or 0)
        md5 = _gcs_md5(headers)
        reduced = self.staging / f"{step.name}.vcf.gz"
        reduced.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Streaming gnomAD %s (%s)...", step.chromosome, format_bytes(size))
        streamed = stream_reduced(url, size, reduced, self.cancel)
        if md5 and streamed != md5:
            reduced.unlink(missing_ok=True)
            raise WGSExtractError(
                f"gnomAD {step.chromosome} didn't match the MD5 Google Cloud "
                f"Storage gives ({streamed}, expected {md5}); rerun to stream it "
                "again."
            )
        self.record_source(
            "gnomad",
            Path(url).name,
            {
                "bytes": size,
                "md5": streamed,
                "verified": f"MD5 {md5}" if md5 else "none published",
                "streamed": _now(),
                "release": "4.1",
            },
        )
        flagged = add_region_flags(
            reduced, self.plan.sa_dir, regions, step.chromosome, self.cancel
        )
        self.database(step, flagged, keep_input=False, regions=regions)

    def step_repeatmasker(self, step: Step) -> None:
        source = self.fetch_track(step)
        bed = self.staging / "repeatmasker.bed"
        bed.parent.mkdir(parents=True, exist_ok=True)
        repeatmasker_bed(source, bed, CHROMOSOMES)
        self.database(step, bed, keep_input=False)

    def step_lcr(self, step: Step) -> None:
        self.build_region_track(step)

    def step_segdup(self, step: Step) -> None:
        self.build_region_track(step)

    def fetch_track(self, step: Step) -> Path:
        url = source_url(step.source)
        headers = _head(url)
        dest = self.plan.sources_dir / Path(url).name
        entry = self.fetch(url, dest, md5=_gcs_md5(headers))
        entry["release"] = _http_date(headers)
        self.record_source(step.source, dest.name, entry)
        return dest

    def build_region_track(self, step: Step) -> None:
        source = self.fetch_track(step)
        packed = self.staging / region_name(step.source)
        bed = packed.with_suffix("")
        bed.parent.mkdir(parents=True, exist_ok=True)
        region_bed(source, bed, step.source, CHROMOSOMES)
        for command in (
            ["bgzip", "-f", str(bed)],
            ["tabix", "-f", "-p", "bed", str(packed)],
        ):
            exe = get_tool_path(command[0])
            run_tool([exe or command[0], *command[1:]], self.cancel)
        index = packed.with_name(packed.name + ".tbi")
        main, tbi = self.plan.sa_dir / packed.name, self.plan.sa_dir / index.name
        packed.replace(main)
        index.replace(tbi)
        self.on_output(main)
        self.on_output(tbi)
        self.record_database(
            step,
            {
                "file": main.name,
                "files": [main.name, tbi.name],
                "bytes": main.stat().st_size,
                "index_bytes": tbi.stat().st_size,
                "sha256": _sha256(main, self.cancel),
                "built": _now(),
            },
        )

    def step_phylop(self, step: Step) -> None:
        assert step.chromosome
        url = source_url("phylop", step.chromosome)
        name = Path(url).name
        with self.lock:
            if self.phylop_md5 is None:
                self.phylop_md5 = {
                    parts[1]: parts[0]
                    for line in _text(
                        _acmg_data_plan.PHYLOP_DIR + "md5sum.txt"
                    ).splitlines()
                    if len(parts := line.split()) == 2
                }
            md5 = self.phylop_md5.get(name)
        dest = self.staging / name
        entry = self.fetch(url, dest, md5=md5)
        del entry["file"]  # removed once built
        entry["release"] = "hg38.phyloP100way"
        self.record_source("phylop", name, entry)
        self.database(step, dest, keep_input=False)

    def produce_revel(
        self, steps: list[Step], submit: Callable[[Step, Path], None]
    ) -> None:
        """Cut REVEL into one CSV per chromosome being built, which bounds
        sa-build's memory, handing each to ``submit`` once complete. A CSV is
        started only when one of the ``workers`` slots is free, so at most that
        many wait on disk; the build of each frees its slot."""
        dest = self.plan.sources_dir / Path(_acmg_data_plan.REVEL_URL).name
        recorded = self.manifest["sources"].get("revel", {}).get("files", {})
        if dest.name not in recorded or not dest.is_file():
            entry = self.fetch(_acmg_data_plan.REVEL_URL, dest)
            entry["release"] = "1.3"
            self.record_source("revel", dest.name, entry)

        def slot() -> None:
            while not self.revel_slots.acquire(timeout=1):
                if self.cancel.is_set():
                    raise WGSExtractError("Cancelled.")

        by_chromosome = {s.chromosome: s for s in steps}
        logging.info("Splitting REVEL by chromosome...")
        for chromosome, part in split_revel(
            dest, [c for c in by_chromosome if c], self.staging, slot, self.cancel
        ):
            submit(by_chromosome.pop(chromosome), part)
        for step in by_chromosome.values():
            self.absent(step, f"REVEL has no rows on {step.chromosome}")

    def build_revel(self, step: Step, part: Path) -> None:
        try:
            self.database(step, part, keep_input=False)
        finally:
            part.unlink(missing_ok=True)
            self.revel_slots.release()

    def prepare_spliceai(self) -> None:
        """The SpliceAI VCF to cut: the one given, else the library's (as
        ``ref spliceai`` installs it), downloaded from Ensembl when missing.
        Its SHA-256 and where it came from go into the manifest."""
        downloaded = False
        if self.spliceai is None:
            from wgsextract_cli.core.annotation_resources import download_spliceai

            reflib = self.plan.reflib
            if not library_spliceai(reflib):
                # An unindexed file already there is indexed, not downloaded.
                downloaded = not library_spliceai_path(reflib).is_file()
                if not download_spliceai(str(reflib), self.cancel, builds=("hg38",)):
                    raise WGSExtractError(
                        "Downloading the SpliceAI scores failed; rerun to resume."
                    )
            self.spliceai = library_spliceai(reflib)
            if self.spliceai is None:
                raise WGSExtractError("The SpliceAI download left no indexed file.")
        path = self.spliceai
        if not read_scope.may_follow(path):
            raise WGSExtractError(f"{path} leads outside the folders this run may use.")
        header = require_grch38_vcf(path)
        date = next(
            (h.split("=", 1)[1] for h in header if h.startswith("##fileDate=")), ""
        )
        if not any(Path(f"{path}{ext}").is_file() for ext in (".tbi", ".csi")):
            raise WGSExtractError(
                f"{path} has no tabix index; index it with 'tabix -p vcf {path}'."
            )
        stat = path.stat()
        info = self.manifest["sources"].get("spliceai", {})
        before = info.get("files", {}).get(path.name, {})
        unchanged = bool(before.get("sha256")) and (
            before.get("file"),
            before.get("bytes"),
            before.get("mtime_ns"),
        ) == (str(path), stat.st_size, stat.st_mtime_ns)
        # Where the file came from is recorded with it: the source's label is
        # that of the file used last, which may have been another.
        if self.plan.spliceai:
            origin = {
                "label": "SpliceAI scores (given with --spliceai-file)",
                "url": "",
            }
            verified = "given with --spliceai-file"
        elif downloaded:
            origin = {
                "label": BY_KEY["spliceai"].label,
                "url": _acmg_data_plan.SPLICEAI_URL,
            }
            verified = "BGZF end-of-file block and tabix index (ref spliceai)"
        elif unchanged and "label" in before:
            # The file an earlier run recorded, untouched since.
            origin = {"label": before["label"], "url": before.get("url", "")}
            verified = str(before.get("verified", ""))
        else:
            origin = {
                "label": f"SpliceAI scores (the library's {path.name}, installed "
                "before this run; origin not recorded)",
                "url": "",
            }
            verified = "found in the library"
        if unchanged:
            sha256 = str(before["sha256"])
        else:
            logging.info("Hashing %s...", path.name)
            sha256 = _sha256(path, self.cancel)
        self.record_source(
            "spliceai",
            path.name,
            {
                "file": str(path),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256,
                "verified": verified,
                "release": date,
                **origin,
            },
            **origin,
        )

    def step_spliceai(self, step: Step) -> None:
        assert step.chromosome and self.spliceai is not None
        contigs = tabix_contigs(self.spliceai)
        contig = next(
            (c for c in (step.chromosome, step.chromosome[3:]) if c in contigs), None
        )
        if contig is None:
            self.absent(step, f"{self.spliceai.name} has no {step.chromosome}")
            return
        cut = self.staging / f"{step.name}.vcf.gz"
        cut.parent.mkdir(parents=True, exist_ok=True)
        with cut.open("wb") as out:
            tabix = popen(
                ["tabix", "-h", str(self.spliceai), contig], stdout=subprocess.PIPE
            )
            bgzip = popen(["bgzip", "-c"], stdin=tabix.stdout, stdout=out)
            assert tabix.stdout is not None
            tabix.stdout.close()
            ok = wait_with_cancel(bgzip, self.cancel) and tabix.wait() == 0
        if not ok:
            cut.unlink(missing_ok=True)
            raise WGSExtractError(f"Cutting {contig} out of {self.spliceai} failed.")
        self.database(step, cut, keep_input=False)


def build(
    plan: Plan,
    workers: int,
    cancel: CancelEvent | None,
    on_output: Callable[[Path], None],
) -> None:
    """Carry out ``plan``: the GFF3, ClinVar and the gene-level sources first,
    then the per-chromosome databases ``workers`` at a time. A failed step
    leaves the others to finish; the run then fails, naming it. Stopped
    (Ctrl-C, SIGTERM), it starts nothing more and stops what is running."""
    plan.sa_dir.mkdir(parents=True, exist_ok=True)
    run = _Build(plan, cancel, on_output, workers)
    failed: dict[str, str] = {}

    def attempt(name: str, func: Callable[..., None], *args: Any) -> bool:
        try:
            func(*args)
            return True
        except (OSError, HTTPException, WGSExtractError) as e:
            logging.error("%s failed: %s", name, e)
            failed[name] = str(e)
            return False

    by_source: dict[str, list[Step]] = defaultdict(list)
    for step in plan.steps:
        by_source[step.source if step.chromosome else ""].append(step)
    for steps in by_source.values():
        steps.sort(key=lambda s: -_MB[s.chromosome or "chr1"])
    # SpliceAI's download and REVEL's split run beside the other builds, which
    # go first, the largest first so the last to finish is a short one; each
    # hands its builds over as their input is ready.
    revel = by_source.pop("revel", [])
    revel.sort(key=lambda s: CHROMOSOMES.index(s.chromosome or "chr1"))
    spliceai = by_source.pop("spliceai", [])
    pool = ContextThreadPoolExecutor(max_workers=max(1, workers))
    preparing = ContextThreadPoolExecutor(max_workers=2)
    futures: list[Future[Any]] = []
    submitting = threading.Lock()
    succeeded = False

    def submit(func: Callable[..., Any], *args: Any) -> None:
        with submitting:
            futures.append(pool.submit(func, *args))

    def prepare_spliceai() -> None:
        if attempt("spliceai", run.prepare_spliceai):
            for step in spliceai:
                submit(attempt, step.name, run.run, step)

    def prepare_revel() -> None:
        attempt(
            "revel",
            run.produce_revel,
            revel,
            lambda step, part: submit(attempt, step.name, run.build_revel, step, part),
        )

    def finish(future: Future[Any]) -> None:
        # An unbounded Future.result() cannot deliver pending Python signals
        # while its lock is waiting on Windows.
        while not future.done():
            wait((future,), timeout=0.1)
        future.result()

    try:
        for step in by_source.pop("", []):
            attempt(step.name, run.run, step)
        per_chromosome = sorted(
            (s for steps in by_source.values() for s in steps),
            key=lambda s: -_MB[s.chromosome or "chr1"],
        )
        for step in per_chromosome:
            submit(attempt, step.name, run.run, step)
        prepared = [
            preparing.submit(prepare)
            for prepare, steps in ((prepare_spliceai, spliceai), (prepare_revel, revel))
            if steps
        ]
        for future in prepared:
            finish(future)
        # Every build is submitted once the preparations are done.
        for future in list(futures):
            finish(future)
        succeeded = not failed
    except BaseException:
        run.cancel.set()
        raise
    finally:
        preparing.shutdown(cancel_futures=True)
        pool.shutdown(cancel_futures=True)
        _tidy_staging(run.staging, keep_downloads=not succeeded)
    if failed:
        raise WGSExtractError(
            "ref acmg-data didn't finish: "
            + "; ".join(f"{name}: {why.rstrip('.')}" for name, why in failed.items())
            + ". Rerun the command to resume; what was built and downloaded is kept."
        )


def _tidy_staging(staging: Path, *, keep_downloads: bool) -> None:
    """Remove the staging folder, or after a failure all of it but the phyloP
    downloads (and their resumable ``.partial`` files) a rerun picks up."""
    if not keep_downloads:
        shutil.rmtree(staging, ignore_errors=True)
        return
    for path in staging.iterdir() if staging.is_dir() else ():
        if PHYLOP_FILE.format(chrom="") in path.name:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
