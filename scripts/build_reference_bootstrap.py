"""Build the reference bootstrap tarball with CombinedKit v5 templates.

Derives ``wgsextract-reference-bootstrap-<version>.tar.gz`` from the pinned
``v0.2.0`` bootstrap (already stripped of CC-BY-SA YBrowse ``snps_*.vcf.gz``
files and macOS Finder metadata). Every existing CombinedKit v3 file stays
unchanged; the CLI-packaged microarray templates (``body/``, ``head/``,
``corrections.tsv``, ``README.md``) are added under
``reference/microarray/raw_file_templates/`` so CombinedKit v5 expands v3 in
the bootstrap archive itself, and ``reference/00README.txt`` notes the
addition. The output is byte-reproducible, and a ``SHA256SUMS`` file is
written beside it; pin the printed checksum as ``BOOTSTRAP_SHA256``.

    python scripts/build_reference_bootstrap.py \
        --source wgsextract-reference-bootstrap-0.2.0.tar.gz --output out/bootstrap
"""

import argparse
import gzip
import hashlib
import io
import re
import sys
import tarfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

SOURCE_SHA256 = "03d7f58ba813c12e1efed048466f62959dc51f996d95aace76105c49fefca8e9"
VERSION = "0.3.0"
README = "reference/00README.txt"
TEMPLATES_ARCHIVE_ROOT = PurePosixPath("reference/microarray/raw_file_templates")
DEFAULT_TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "src/wgsextract_cli/assets/microarray/raw_file_templates"
)
YBROWSE = re.compile(r"snps_[^/]*\.vcf\.gz(\.[a-z]+)*")
YBROWSE_SECTION = re.compile(
    r"Y Chromosome SNPs file \(yBrowse\.org\):\n.*?\n\n(?=Exome BED)", re.DOTALL
)
YBROWSE_NOTE = """\
Y Chromosome SNPs file (yBrowse.org):
-- Not included since reference bootstrap v{version}: YBrowse data is
   CC-BY-SA and is not redistributed with WGS Extract. Install it from
   ybrowse.org with 'wgsextract ref ybrowse --install'; it is then used by
   'wgsextract extract ydna-vcf --ybrowse'. The snps_*.vcf.gz files left by
   an older bootstrap are no longer used and can be deleted.

"""
COMBINEDKIT_V5_MARKER = "CombinedKit v5"
COMBINEDKIT_V5_NOTE = """\
-- Since reference bootstrap v{version}, raw_file_templates/ expands
   CombinedKit v3 into CombinedKit v5 with additional consumer vendor
   templates (23andMe v2/v5_2025, Ancestry v2_2021/v2_2024/v2ab/v2cd/v2e,
   FTDNA v4 and GRCh38 Y files v2y/v3y/v4y, MyHeritage GSA 2025/v3, Nucleus,
   tellmeGen, Genera, MTHFR Genetics, meuDNA v1/v2, SelfDecode) and
   corrections.tsv; all CombinedKit v3 templates remain unchanged.
"""


def dropped(name: str) -> bool:
    """Whether the bootstrap member ``name`` is left out."""
    path = PurePosixPath(name)
    return (
        bool(YBROWSE.fullmatch(path.name))
        or path.name.startswith("._")
        or path.name == ".DS_Store"
        or "__MACOSX" in path.parts
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_template_files(templates_dir: Path | None) -> list[tuple[str, Path]]:
    """Collect sorted ``(archive_name, local_path)`` pairs to add from ``templates_dir``."""
    if templates_dir is None:
        return []
    if not templates_dir.is_dir():
        raise SystemExit(f"Templates directory does not exist: {templates_dir}")
    entries: list[tuple[str, Path]] = []
    for path in sorted(templates_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = PurePosixPath(*path.relative_to(templates_dir).parts)
        if dropped(str(rel)) or any(part.startswith(".") for part in rel.parts):
            continue
        if len(rel.parts) == 1 and rel.name in {"corrections.tsv", "README.md"}:
            entries.append((str(TEMPLATES_ARCHIVE_ROOT / rel), path))
        elif len(rel.parts) == 2 and rel.parts[0] in {"body", "head"}:
            entries.append((str(TEMPLATES_ARCHIVE_ROOT / rel), path))
    if not entries:
        raise SystemExit(f"No template files found in {templates_dir}")
    return sorted(entries, key=lambda item: item[0])


def _update_readme(raw_bytes: bytes, version: str, has_templates: bool) -> bytes:
    """Update ``reference/00README.txt`` preserving CRLF line endings."""
    text = raw_bytes.decode("utf-8").replace("\r\n", "\n")
    if "Not included since reference bootstrap v" not in text:
        text, count = YBROWSE_SECTION.subn(YBROWSE_NOTE.format(version=version), text)
        if count != 1:
            raise SystemExit(f"{README}: no yBrowse section to replace")
    if has_templates and COMBINEDKIT_V5_MARKER not in text:
        if "Microarray/:\n" not in text:
            raise SystemExit(f"{README}: no Microarray section to update")
        if not text.endswith("\n"):
            text += "\n"
        text += COMBINEDKIT_V5_NOTE.format(version=version)
    return text.replace("\n", "\r\n").encode("utf-8")


def build(
    source: Path,
    output: Path,
    version: str,
    source_sha256: str,
    templates_dir: Path | None = None,
) -> Path:
    actual = sha256(source)
    if actual != source_sha256:
        raise SystemExit(f"{source}: SHA-256 {actual}, expected {source_sha256}")
    additions = _collect_template_files(templates_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"wgsextract-reference-bootstrap-{version}.tar.gz"
    partial = target.with_name(target.name + ".partial")
    try:
        removed, added = _copy(source, partial, version, additions)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    digest = sha256(target)
    (output / "SHA256SUMS").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    for name in removed:
        print(f"removed {name}")
    for name in added:
        print(f"added {name}")
    print(f"Wrote {target} ({target.stat().st_size} bytes, sha256 {digest})")
    return target


def _copy(
    source: Path,
    target: Path,
    version: str,
    additions: Sequence[tuple[str, Path]] = (),
) -> tuple[list[str], list[str]]:
    """Write the updated bootstrap to ``target``; return ``(dropped, added)`` names."""
    removed: list[str] = []
    added_map = dict(additions)
    with tarfile.open(source, "r:gz") as old:
        kept: dict[str, tarfile.TarInfo] = {}
        for member in old.getmembers():
            if dropped(member.name):
                removed.append(member.name)
                continue
            if not (member.isfile() or member.isdir()):
                raise SystemExit(f"Unexpected bootstrap member: {member.name}")
            if member.name in added_map:
                raise SystemExit(
                    f"Bootstrap template collides with existing member: {member.name}"
                )
            kept[member.name] = member

        all_names = sorted([*kept, *added_map])
        with (
            target.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as packed,
            tarfile.open(fileobj=packed, mode="w", format=tarfile.PAX_FORMAT) as new,
        ):
            for name in all_names:
                if name in kept:
                    member = kept[name]
                    info = tarfile.TarInfo(member.name)
                    info.type, info.mode, info.mtime = (
                        member.type,
                        member.mode,
                        member.mtime,
                    )
                    data = old.extractfile(member) if member.isfile() else None
                    if member.name == README and data:
                        updated = _update_readme(
                            data.read(), version, has_templates=bool(added_map)
                        )
                        data = io.BytesIO(updated)
                        info.size = len(updated)
                    else:
                        info.size = member.size
                    new.addfile(info, data)
                else:
                    local_path = added_map[name]
                    payload = local_path.read_bytes()
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.REGTYPE
                    info.mode = 0o644
                    info.mtime = 0
                    info.size = len(payload)
                    new.addfile(info, io.BytesIO(payload))
    return removed, sorted(added_map)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", default=SOURCE_SHA256)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument(
        "--templates",
        type=Path,
        default=DEFAULT_TEMPLATES,
        help="Directory of microarray raw_file_templates to include (default: packaged CLI assets).",
    )
    parser.add_argument(
        "--no-templates",
        action="store_true",
        help="Do not add extra microarray templates.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    templates_dir = None if args.no_templates else args.templates
    build(args.source, args.output, args.version, args.source_sha256, templates_dir)


if __name__ == "__main__":
    main(sys.argv[1:])
