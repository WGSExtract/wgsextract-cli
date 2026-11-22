#!/usr/bin/env python3
"""Run the WGS Extract v44.2 differential corpus and record its evidence.

The corpus (tests/fixtures/v4_differential/corpus.json) records, for each case,
what released v44.2 did, what this CLI does, and whether that is parity, a
correction, a policy difference or a known gap. See docs/migrating-from-v4.md.

  synthetic  Run every case's evidence test (real samtools/bcftools on small
             synthetic genomes; offline) and write tool versions, test
             outcomes and the hashes of every input and output file.
  fetch      Download the pinned public inputs into CACHE: the HG00096 chr20
             slice (streamed from the EBI CRAM), GRCh38 chr20 and the released
             v8 reference-library assets. Network: needs --download.
  public     Rerun this CLI offline on the cached public slice and compare it
             with the recorded v44.2 outputs and expected current results.

Usage:
  pixi run -e dev python scripts/v4_differential_corpus.py synthetic --outdir out/v4-differential
  pixi run python scripts/v4_differential_corpus.py fetch --cache CACHE --download [--reference GRCh38.fa]
  pixi run python scripts/v4_differential_corpus.py public --cache CACHE --outdir out/v4-differential
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "fixtures" / "v4_differential" / "corpus.json"
REFERENCE_ZIP = "WGSExtract-v8_30Jan2023_reflib.zip"
ZIP_PREFIX = "WGSExtractv4/reference/"


class CorpusError(RuntimeError):
    pass


def load_corpus() -> dict[str, Any]:
    corpus: dict[str, Any] = json.loads(CORPUS.read_text())
    return corpus


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tool(name: str) -> str:
    from wgsextract_cli.core.dependencies import get_tool_path

    path = get_tool_path(name)
    if not path:
        raise CorpusError(f"{name} is not installed; run `pixi install` first.")
    return str(path)


def run(argv: Sequence[object], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run([str(a) for a in argv], check=True, **kwargs)


def output(argv: Sequence[object], **kwargs: Any) -> str:
    return str(run(argv, capture_output=True, text=True, **kwargs).stdout)


def environment() -> dict[str, Any]:
    """The versions that decide this run's results."""
    from importlib.metadata import version

    head = output(["git", "-C", ROOT, "rev-parse", "HEAD"]).strip()
    dirty = bool(output(["git", "-C", ROOT, "status", "--porcelain"]).strip())
    samtools = output([tool("samtools"), "--version"]).splitlines()
    return {
        "cli_commit": head,
        "cli_worktree_dirty": dirty,
        "wgsextract_cli": version("wgsextract-cli"),
        "samtools": samtools[0],
        "htslib": samtools[1],
        "bcftools": output([tool("bcftools"), "--version"]).splitlines()[0],
        "pyliftover": version("pyliftover"),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def file_hashes(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(path.relative_to(root)): {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def write_evidence(outdir: Path, name: str, evidence: dict[str, Any]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / name
    path.write_text(json.dumps(evidence, indent=2) + "\n")
    return path


# --- synthetic ----------------------------------------------------------------


def synthetic(outdir: Path) -> int:
    corpus = load_corpus()
    nodes = sorted({c["evidence"] for c in corpus["cases"] if c["evidence"]})
    nodes += sorted({f["evidence"] for f in corpus["completed_fixes"]} - set(nodes))
    work = outdir / "synthetic"
    if work.exists():
        shutil.rmtree(work)
    junit = outdir / "synthetic-junit.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0"]
        + ["-o", "tmp_path_retention_policy=all", "--basetemp", str(work)]
        + ["--junitxml", str(junit), *nodes],
        cwd=ROOT,
    )
    outcomes: dict[str, str] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        name = case.get("name", "").split("[")[0]
        failed = any(case.find(tag) is not None for tag in ("failure", "error"))
        skipped = case.find("skipped") is not None
        state = "failed" if failed else "skipped" if skipped else "passed"
        if outcomes.get(name) not in ("failed", "skipped"):
            outcomes[name] = state
    cases = [
        {
            "id": case["id"],
            "relation": case["relation"],
            "tracking": case["tracking"],
            "evidence": case["evidence"],
            "outcome": (
                outcomes.get(case["evidence"].split("::")[1], "not run")
                if case["evidence"]
                else "documented"
            ),
        }
        for case in corpus["cases"]
    ]
    evidence = {
        "environment": environment(),
        "pins": corpus["pins"],
        "cases": cases,
        "completed_fixes": [
            {**fix, "outcome": outcomes.get(fix["evidence"].split("::")[1], "not run")}
            for fix in corpus["completed_fixes"]
        ],
        "files": file_hashes(work) if work.exists() else {},
    }
    path = write_evidence(outdir, "synthetic-evidence.json", evidence)
    print(f"Wrote {path}")
    states = collections.Counter(c["outcome"] for c in cases)
    print(", ".join(f"{n} {s}" for s, n in sorted(states.items())))
    if result.returncode or {"failed", "not run", "skipped"} & set(states):
        return 1
    return 0


# --- fetch --------------------------------------------------------------------


def download(url: str, destination: Path, expected: str | None = None) -> None:
    if destination.exists() and (expected is None or sha256(destination) == expected):
        return
    partial = destination.with_name(destination.name + ".part")
    print(f"Downloading {url}")
    with urllib.request.urlopen(url) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    if expected and sha256(partial) != expected:
        partial.unlink()
        raise CorpusError(f"{url} does not match its pinned SHA-256 {expected}.")
    partial.replace(destination)


def contig_md5(fasta: Path, contig: str) -> str | None:
    for line in output([tool("samtools"), "dict", fasta]).splitlines():
        fields = dict(f.split(":", 1) for f in line.split("\t")[1:] if ":" in f)
        if fields.get("SN") == contig:
            return fields.get("M5")
    return None


def fetch_reference(cache: Path, public: dict[str, Any], source: Path | None) -> Path:
    spec = public["reference"]
    fasta = cache / "reflib" / f"{spec['contig']}.fa"
    if not (fasta.exists() and contig_md5(fasta, spec["contig"]) == spec["md5"]):
        if source is None:
            packed = cache / f"{spec['contig']}.fa.gz"
            download(spec["download"], packed)
            with gzip.open(packed, "rb") as src, fasta.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            packed.unlink()
        else:
            with fasta.open("w") as handle:
                run([tool("samtools"), "faidx", source, spec["contig"]], stdout=handle)
        run([tool("samtools"), "faidx", fasta])
    if contig_md5(fasta, spec["contig"]) != spec["md5"]:
        raise CorpusError(
            f"{fasta} is not GRCh38 {spec['contig']} (MD5 {spec['md5']})."
        )
    return fasta


def records_sha256(bam: Path) -> str:
    view = subprocess.Popen(
        [tool("samtools"), "view", str(bam)], stdout=subprocess.PIPE
    )
    stream = view.stdout
    assert stream is not None
    digest = hashlib.sha256()
    while chunk := stream.read(1 << 20):
        digest.update(chunk)
    if view.wait():
        raise CorpusError(f"samtools view failed on {bam}")
    return digest.hexdigest()


def fetch(cache: Path, confirmed: bool, reference: Path | None) -> int:
    corpus = load_corpus()
    public = corpus["public"]
    if not confirmed:
        raise CorpusError(
            "The public corpus needs the HG00096 chr20 slice (streamed from "
            f"{public['cram']}), GRCh38 chr20 and the released WGS Extract v8 "
            f"reference library ({corpus['pins']['reference_zip']['url']}, 254 MB). "
            "Rerun with --download to fetch them."
        )
    reflib = cache / "reflib"
    reflib.mkdir(parents=True, exist_ok=True)
    fasta = fetch_reference(cache, public, reference)

    archive = cache / REFERENCE_ZIP
    pin = corpus["pins"]["reference_zip"]
    download(pin["url"], archive, pin["sha256"])
    with zipfile.ZipFile(archive) as packed:
        for member, expected in public["reference_zip_members"].items():
            target = reflib / member.removeprefix(ZIP_PREFIX)
            if target.exists() and sha256(target) == expected:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with packed.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            if sha256(target) != expected:
                raise CorpusError(f"{member} does not match its pinned SHA-256.")

    targets = cache / "targets.tab"
    all_snps = reflib / "microarray" / "All_SNPs_hg38_ref.tab.gz"
    with targets.open("w") as handle:
        run([tool("tabix"), all_snps, public["region"]], stdout=handle)
    if sha256(targets) != public["targets_sha256"]:
        raise CorpusError("The v8 target subset does not match its pinned SHA-256.")
    run([tool("bgzip"), "-f", "-k", targets])
    run([tool("tabix"), "-f", "-s", "1", "-b", "2", "-e", "2", f"{targets}.gz"])

    bam = cache / "HG00096.slice.bam"
    if not (bam.exists() and records_sha256(bam) == public["records_sha256"]):
        print(f"Streaming {public['region']} from {public['cram']}")
        # htslib saves the remote index into the working directory.
        run(
            [tool("samtools"), "view", "-b", "-T", fasta, "-o", bam.name]
            + [public["cram"], public["region"]],
            cwd=cache,
        )
        (cache / (Path(public["cram"]).name + ".crai")).unlink(missing_ok=True)
        run([tool("samtools"), "index", bam])
    if records_sha256(bam) != public["records_sha256"]:
        raise CorpusError(f"{bam} records do not match the pinned digest.")
    print(f"Public corpus inputs verified in {cache}")
    return 0


# --- public -------------------------------------------------------------------


def kit_rows(path: Path) -> dict[str, tuple[str, ...]]:
    rows = {}
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        rsid, *fields = line.split("\t")
        rows[rsid] = tuple(fields)
    return rows


def v44_2_outputs(path: Path) -> dict[str, dict[str, str]]:
    lines = [row for row in path.read_text().splitlines() if not row.startswith("#")]
    header = lines[0].split("\t")
    return {
        f[0]: dict(zip(header, f, strict=True))
        for f in (row.split("\t") for row in lines[1:])
    }


def verified_cache(cache: Path, public: dict[str, Any]) -> dict[str, Path]:
    paths = {
        "bam": cache / "HG00096.slice.bam",
        "fasta": cache / "reflib" / f"{public['reference']['contig']}.fa",
        "targets": cache / "targets.tab.gz",
        "ploidy": cache / "reflib" / "microarray" / "ploidy.txt",
    }
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise CorpusError(
            f"Missing public corpus inputs ({', '.join(missing)}). Run "
            f"`python scripts/v4_differential_corpus.py fetch --cache {cache} --download`."
        )
    if records_sha256(paths["bam"]) != public["records_sha256"]:
        raise CorpusError(f"{paths['bam']} records do not match the pinned digest.")
    return paths


def cli(config: Path, *argv: str | Path) -> None:
    run([sys.executable, "-m", "wgsextract_cli", "--config", config, *argv], cwd=ROOT)


Kit = dict[str, tuple[str, ...]]


def microarray(
    config: Path, inputs: dict[str, Path], bam: Path, out: Path, region: str
) -> tuple[Kit, Kit, Path]:
    cli(
        config, "microarray", "--input", bam, "--ref", inputs["fasta"],
        "--outdir", out, "--threads", "1", "--formats", "23andme_v3",
        "--ref-vcf-tab", inputs["targets"], "--ploidy-file", inputs["ploidy"],
        "--region", region, "--call-reasons",
    )  # fmt: skip
    stem = bam.name.removesuffix(".bam")
    return (
        kit_rows(out / f"{stem}_CombinedKit_hg19.txt"),
        kit_rows(out / f"{stem}_CombinedKit_reasons.tsv"),
        out / f"{stem}_23andMe_V3.txt",
    )


def public_run(cache: Path, outdir: Path) -> int:
    corpus = load_corpus()
    public = corpus["public"]
    expected = public["expected"]
    inputs = verified_cache(cache, public)
    region = public["region"]
    work = outdir / "public"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    config = work / "config.toml"
    config.write_text(
        f'reference_library = "{(cache / "reflib").resolve().as_posix()}"\n'
    )
    v44_2 = v44_2_outputs(ROOT / public["v44_2_outputs"])
    checks: dict[str, dict[str, Any]] = {}

    def check(name: str, observed: dict[str, Any], wanted: dict[str, Any]) -> None:
        checks[name] = {
            "observed": observed,
            "expected": wanted,
            "ok": observed == wanted,
        }

    kit, _, v3_path = microarray(config, inputs, inputs["bam"], work / "high", region)
    disagreements = sum(
        kit.get(r, ())[:3] != (v["CHROM"], v["POS"], v["GENOTYPE"])
        for r, v in v44_2.items()
    )
    want = expected["combined_kit_hg19"]
    check(
        "combined_kit_hg19",
        {"current_rows": len(kit), "disagreements": disagreements},
        {"current_rows": want["current_rows"], "disagreements": want["disagreements"]},
    )
    v3 = kit_rows(v3_path)
    called = {r: f[2] for r, f in v3.items() if f[2] != "--"}
    v3_expected = {r: v["V3"] for r, v in v44_2.items() if v["V3"]}
    want = expected["23andme_v3"]
    check(
        "23andme_v3",
        {
            "current_rows": len(v3),
            "called": len(called),
            "disagreements": sum(called.get(r) != g for r, g in v3_expected.items())
            + len(set(called) - set(v3_expected)),
        },
        {k: want[k] for k in ("current_rows", "called", "disagreements")},
    )

    low = work / "HG00096.low.bam"
    sample = public["downsample"]
    names = ROOT / sample["qnames"]
    run([tool("samtools"), "view", "-b", "-N", names, "-o", low, inputs["bam"]])
    if records_sha256(low) != sample["records_sha256"]:
        raise CorpusError(f"{low} records do not match the pinned low-depth digest.")
    run([tool("samtools"), "index", low])
    low_kit, reasons, _ = microarray(config, inputs, low, work / "low", region)
    want = expected["low_depth"]
    check(
        "low_depth",
        {
            "current_rows": len(low_kit),
            "current_called": sum(f[2] != "--" for f in low_kit.values()),
            "current_reasons": dict(
                collections.Counter(f[3] for f in reasons.values())
            ),
            # Where both programs call a genotype, the genotypes agree.
            "disagreements_where_both_called": sum(
                f[2] not in ("--", v44_2[r]["LOW_DEPTH"])
                for r, f in low_kit.items()
                if v44_2.get(r, {}).get("LOW_DEPTH") not in (None, "", "--")
            ),
        },
        {
            k: want[k]
            for k in (
                "current_rows",
                "current_called",
                "current_reasons",
                "disagreements_where_both_called",
            )
        },
    )

    info = json.loads(
        output(
            [sys.executable, "-m", "wgsextract_cli", "--config", config, "info"]
            + [
                "--input",
                inputs["bam"],
                "--ref",
                inputs["fasta"],
                "--outdir",
                work / "info",
            ]
            + ["--threads", "1", "--detailed", "--json"],
            cwd=ROOT,
        )
    )["metrics"]
    want = expected["stats"]
    check(
        "stats",
        {
            "current_mapped": round(info["reads_mapped_m"] * 1e6),
            "current_raw": round(info["reads_raw_m"] * 1e6),
        },
        {k: want[k] for k in ("current_mapped", "current_raw")},
    )

    # vcf indel requires the BAM's sequence dictionary to match the chr20-only
    # reference: drop the unused @SQ lines through SAM text, which re-numbers
    # the records' contig references (records are otherwise unchanged).
    contig = public["reference"]["contig"]
    sam = output([tool("samtools"), "view", "-h", inputs["bam"]]).splitlines()
    kept = [
        line
        for line in sam
        if not line.startswith("@SQ") or f"\tSN:{contig}\t" in f"{line}\t"
    ]
    sam_file = work / "chr20-header.sam"
    sam_file.write_text("\n".join(kept) + "\n")
    indel_bam = work / "HG00096.chr20-header.bam"
    run([tool("samtools"), "view", "-b", "-o", indel_bam, sam_file])
    sam_file.unlink()
    run([tool("samtools"), "index", indel_bam])
    cli(
        config, "vcf", "indel", "--input", indel_bam, "--ref", inputs["fasta"],
        "--outdir", work / "indel", "--threads", "1", "--region", region,
        "--ploidy-file", inputs["ploidy"],
    )  # fmt: skip
    records = output([tool("bcftools"), "view", "-H", work / "indel" / "indels.vcf.gz"])
    check(
        "indels",
        {"current_records": len(records.splitlines())},
        {"current_records": expected["indels"]["current_records"]},
    )

    evidence = {
        "environment": environment(),
        "pins": corpus["pins"],
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in inputs.items()
        }
        | {"bam_records_sha256": public["records_sha256"]},
        "checks": checks,
        "outputs": file_hashes(work),
    }
    path = write_evidence(outdir, "public-evidence.json", evidence)
    print(f"Wrote {path}")
    for name, result in checks.items():
        print(f"{'ok  ' if result['ok'] else 'DIFF'} {name}: {result['observed']}")
    return 0 if all(c["ok"] for c in checks.values()) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    parsed = sub.add_parser("synthetic", help="Run the synthetic cases (offline).")
    parsed.add_argument("--outdir", type=Path, default=Path("out/v4-differential"))
    parsed = sub.add_parser("fetch", help="Download the public inputs (network).")
    parsed.add_argument("--cache", type=Path, required=True)
    parsed.add_argument(
        "--download", action="store_true", help="Confirm the downloads."
    )
    parsed.add_argument(
        "--reference",
        type=Path,
        help="Local GRCh38 FASTA (faidx-indexed) to take chr20 from instead of UCSC.",
    )
    parsed = sub.add_parser(
        "public", help="Compare on the cached public slice (offline)."
    )
    parsed.add_argument("--cache", type=Path, required=True)
    parsed.add_argument("--outdir", type=Path, default=Path("out/v4-differential"))
    args = parser.parse_args(argv)
    try:
        if args.command == "synthetic":
            return synthetic(args.outdir.resolve())
        if args.command == "fetch":
            return fetch(args.cache.resolve(), args.download, args.reference)
        return public_run(args.cache.resolve(), args.outdir.resolve())
    except (CorpusError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
