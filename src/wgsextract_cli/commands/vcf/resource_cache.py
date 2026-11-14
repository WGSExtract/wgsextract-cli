"""Reusable normalized resource copies, keyed to the resource and FASTA."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from wgsextract_cli.core.atomic_output import atomic_output
from wgsextract_cli.core.file_lock import exclusive_lock
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import run_command


def normalized_resource(
    source: str, reference: str, work: str, options: object, build: Callable[[], str]
) -> str:
    identity = []
    for name in (source, reference, reference + ".fai"):
        path = Path(name).resolve()
        stat = path.stat()
        identity.append((str(path), stat.st_size, stat.st_mtime_ns))
    version = run_command(
        ["bcftools", "--version"], capture_output=True
    ).stdout.splitlines()[0]
    key = hashlib.sha256(
        json.dumps([3, identity, version, options], sort_keys=True).encode()
    ).hexdigest()
    cache = Path(work).parent / ".wgse_normalized_resources" / key
    cache.mkdir(parents=True, exist_ok=True)
    output, metadata = cache / "resource.vcf.gz", cache / "ready.json"
    with exclusive_lock(cache / "build", timeout=3600):
        if output.is_file() and metadata.is_file():
            saved = json.loads(metadata.read_text())
            if all(
                Path(name).is_file()
                and Path(name).stat().st_size == size
                and Path(name).stat().st_mtime_ns == modified
                for name, size, modified in saved["files"]
            ):
                for warning in saved["warnings"]:
                    warn(warning["code"], warning["message"], **warning["details"])
                current_result().add_metrics(annotation_normalization_cache_hit=True)
                return str(output)
        before = len(current_result().warnings)
        built = Path(build())
        built.replace(output)
        files = [output]
        for suffix in (".tbi", ".csi"):
            index = Path(str(built) + suffix)
            if index.is_file():
                final = Path(str(output) + suffix)
                index.replace(final)
                files.append(final)
        with atomic_output(metadata, retire_indexes=False) as staged:
            Path(staged).write_text(
                json.dumps(
                    {
                        "files": [
                            (str(p), p.stat().st_size, p.stat().st_mtime_ns)
                            for p in files
                        ],
                        "warnings": [
                            asdict(w) for w in current_result().warnings[before:]
                        ],
                    }
                )
            )
        current_result().add_metrics(annotation_normalization_cache_hit=False)
        return str(output)
