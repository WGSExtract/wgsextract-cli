"""somalier's sites files and the labelled 1000 Genomes sketches, installed
explicitly into the reference library (``ref somalier --install``) and
checked against pinned SHA-256 digests before every use.

- Sites files (MIT, brentp/somalier release attachments): the ~17,800 common
  SNPs somalier genotypes, one file per build and contig naming.
- ``ancestry-labels-1kg.tsv`` (MIT, brentp/somalier scripts/): the 1000
  Genomes superpopulation of each of the 2,504 labelled samples.
- ``1kg.somalier.tar.gz`` (CC BY 4.0, Zenodo record 3479773): somalier
  sketches of those samples from the NYGC high-coverage data.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.reference_assets import resolve_reference_library
from wgsextract_cli.core.utils import WGSExtractError

FOLDER = "somalier"
_ATTACHMENTS = "https://github.com/brentp/somalier/files"


@dataclass(frozen=True)
class Resource:
    name: str
    url: str
    sha256: str


# (canonical build, contigs named chr1...) -> its sites file.
SITES = {
    ("hg38", True): Resource(
        "sites.hg38.vcf.gz",
        f"{_ATTACHMENTS}/3412456/sites.hg38.vcf.gz",
        "d1a853b8bb2e5f1a520bc67c8303be699543d225d785bce51e8335a2420489b5",
    ),
    ("hg38", False): Resource(
        "sites.hg38.nochr.vcf.gz",
        f"{_ATTACHMENTS}/3412454/sites.hg38.nochr.vcf.gz",
        "297d938350eb3b41c3024b657bd8e96a5d85364251ffa5740f64c5d6baeb1343",
    ),
    ("hg19", True): Resource(
        "sites.hg19.vcf.gz",
        f"{_ATTACHMENTS}/3412453/sites.hg19.vcf.gz",
        "66655f4b1a93d9f474a3dd3d251c11f3d897981fb45f1355538dd807a63ba49a",
    ),
    ("hg19", False): Resource(
        "sites.GRCh37.vcf.gz",
        f"{_ATTACHMENTS}/3412455/sites.GRCh37.vcf.gz",
        "4ca35db90deb4b0e99838c9ef4d48657384b5e0e962476a90e70e039dd38c64d",
    ),
    ("t2t", True): Resource(
        "sites.chm13v2.T2T.vcf.gz",
        f"{_ATTACHMENTS}/9954286/sites.chm13v2.T2T.vcf.gz",
        "b9d93b878966c9affce67a69274b2ba7965a6328bee7532cd4c9dd8ece32bebc",
    ),
}
LABELS = Resource(
    "ancestry-labels-1kg.tsv",
    "https://raw.githubusercontent.com/brentp/somalier/"
    "d9dfab190bdb20a6d35be0a77395efd9fb372a77/scripts/ancestry-labels-1kg.tsv",
    "ef198b72cde1258a2df484e8fb9234b1a0727ad85d09413ed0775ccd078b4fd8",
)
SKETCHES = Resource(
    "1kg.somalier.tar.gz",
    "https://zenodo.org/records/3479773/files/1kg.somalier.tar.gz?download=1",
    "b2b7959cfd6d37d1ac7a96e03aeb13707cfe84e5c3a208329220712a935c5a1e",
)
SKETCH_DIR = "1kg-somalier"
SKETCH_COUNT = 2504
# Written once the sketches are unpacked from the verified archive: its digest
# and each sketch's, checked before every use (~2.5 s for the 519 MB).
_SKETCH_MARKER = ".installed.json"
_SKETCH_NAME = re.compile(r"[A-Za-z0-9_.-]+\.somalier")


def folder(reflib: str | None = None) -> Path:
    return Path(resolve_reference_library(reflib), FOLDER)


def _hint(reflib: Path) -> str:
    return f"Run 'wgsextract ref somalier --install --ref {reflib}'."


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, resource: Resource, what: str) -> Path:
    hint = _hint(path.parent.parent)
    if not path.is_file():
        raise WGSExtractError(f"{what} is not installed: {path}. {hint}")
    try:
        digest = _sha256(path)
    except OSError as e:
        raise WGSExtractError(f"Cannot read {what}: {path}.") from e
    if digest != resource.sha256:
        raise WGSExtractError(f"{what} SHA256 mismatch: {path}. {hint}")
    return path


def sites_resource(build: str, chr_prefix: bool) -> Resource:
    resource = SITES.get((build, chr_prefix))
    if resource is None:
        raise WGSExtractError(
            f"somalier has no sites file for {build} with contigs named "
            f"{'chr1' if chr_prefix else '1'}...; pass --sites with your own."
        )
    return resource


def require(reflib: str | None, resource: Resource) -> Path:
    """An installed resource, checked against its pinned digest."""
    return _verified(folder(reflib) / resource.name, resource, resource.name)


def require_ancestry(reflib: str | None = None) -> tuple[Path, list[Path]]:
    """The labels file and the 2,504 labelled sketches."""
    base = folder(reflib)
    labels = _verified(base / LABELS.name, LABELS, "1000 Genomes ancestry labels")
    sketches = base / SKETCH_DIR
    problem = _sketch_problem(sketches)
    if problem:
        raise WGSExtractError(
            f"The 1000 Genomes somalier sketches in {sketches} {problem}. "
            f"{_hint(base.parent)}"
        )
    return labels, sorted(sketches.glob("*.somalier"))


def _sketch_problem(sketches: Path) -> str | None:
    """Why the unpacked sketches can't be used, if they can't."""
    try:
        marker = json.loads((sketches / _SKETCH_MARKER).read_text())
        digests: dict[str, str] = marker["files"]
        if marker["archive_sha256"] != SKETCHES.sha256:
            return "are from another archive"
    except (OSError, ValueError, KeyError, TypeError):
        return "are missing or incomplete"
    found = {p.name for p in sketches.glob("*.somalier")}
    if found != set(digests) or len(found) != SKETCH_COUNT:
        return "are incomplete or have extra files"
    for name in sorted(found):
        if _sha256(sketches / name) != digests[name]:
            return f"have a changed file, {name}"
    return None


def installed(reflib: str | None = None) -> bool:
    base = folder(reflib)
    return (
        all((base / r.name).is_file() for r in (*SITES.values(), LABELS))
        and (base / SKETCH_DIR / _SKETCH_MARKER).is_file()
    )


def _fetch(
    resource: Resource,
    dest: Path,
    progress: ProgressCallback | None,
    cancel: CancelEvent | None,
) -> None:
    if dest.is_file() and _sha256(dest) == resource.sha256:
        return
    if not download_file(
        resource.url,
        str(dest),
        progress,
        cancel,
        expected_sha256=resource.sha256,
        resolve_release_digest=False,
    ):
        raise WGSExtractError(f"Could not fetch the pinned {resource.name}.")


def _unpack_sketches(archive: Path, target: Path) -> None:
    """Only flat 1kg-somalier/<sample>.somalier files are taken."""
    staging = target.with_name(f".{target.name}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    digests: dict[str, str] = {}
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                parent, _, name = member.name.rpartition("/")
                if not member.isfile():
                    continue
                if parent != SKETCH_DIR or not _SKETCH_NAME.fullmatch(name):
                    raise WGSExtractError(
                        f"Unexpected entry in {archive.name}: {member.name}"
                    )
                source = tar.extractfile(member)
                assert source is not None
                with source, (staging / name).open("wb") as out:
                    shutil.copyfileobj(source, out)
                digests[name] = _sha256(staging / name)
    except tarfile.TarError as e:
        raise WGSExtractError(f"Could not unpack {archive}: {e}") from e
    if len(digests) != SKETCH_COUNT:
        raise WGSExtractError(
            f"{archive.name} held {len(digests)} sketches, not {SKETCH_COUNT}."
        )
    marker = {"archive_sha256": SKETCHES.sha256, "files": digests}
    (staging / _SKETCH_MARKER).write_text(json.dumps(marker, indent=1), newline="\n")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


def install(
    reflib: str | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
) -> list[Path]:
    """Download and verify every resource; returns what is installed."""
    base = folder(reflib)
    base.mkdir(parents=True, exist_ok=True)
    paths = []
    for resource in (*SITES.values(), LABELS):
        _fetch(resource, base / resource.name, progress, cancel)
        paths.append(base / resource.name)
    sketches = base / SKETCH_DIR
    try:
        require_ancestry(str(base.parent))
    except WGSExtractError:
        archive = base / SKETCHES.name
        _fetch(SKETCHES, archive, progress, cancel)
        _unpack_sketches(archive, sketches)
        archive.unlink()
    paths.append(sketches)
    return paths
