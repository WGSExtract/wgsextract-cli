"""Find a VCF's companion gVCF from recorded run manifests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from wgsextract_cli.core.paths import PathInput

ROLE, INDEX_ROLE = "reference_evidence", "reference_evidence_index"


def _recorded_file(
    record: Mapping[str, object], manifest: Path, document: Mapping[str, object]
) -> Path | None:
    """A file a result manifest recorded, where it is now (relative to the
    manifest when its folder has moved), if its recorded fingerprint shows
    it unchanged since; without one the file can't be trusted to be the
    run's."""
    from wgsextract_cli.core import read_scope
    from wgsextract_cli.core.results import fingerprint, manifest_base_dir

    place = Path(str(record.get("path") or ""))
    if not (read_scope.may_follow(place) and place.is_file()) and isinstance(
        original := document.get("manifest"), str
    ):
        try:
            place = manifest_base_dir(manifest) / place.relative_to(
                manifest_base_dir(original)
            )
        except ValueError:
            return None
    expected = record.get("fingerprint")
    if not (expected and read_scope.may_follow(place) and place.is_file()):
        return None
    return place if fingerprint(place) == expected else None


def companion(vcf: PathInput) -> str | None:
    """The gVCF the run that wrote ``vcf`` published beside it, if any: the
    ``reference_evidence`` output of a successful result manifest that also
    lists ``vcf`` among its outputs. The manifests read are those in the
    VCF's folder and those its sample workspace recorded; both files must
    still be the ones the manifest describes (same fingerprint), and the gVCF
    indexed. Of several such runs the newest wins. A server job follows only
    manifests and files inside its allowed folders (``read_scope``)."""
    from wgsextract_cli.core import read_scope, sample_workspace
    from wgsextract_cli.core.results import MANIFEST_SUFFIX, manifest_dir_for

    given = Path(vcf).expanduser().absolute()
    path = given.resolve()
    if not path.is_file():
        return None
    search_folders = {
        candidate
        for parent in {given.parent, path.parent}
        for candidate in (manifest_dir_for(parent), parent)
    }
    candidates = {
        manifest
        for folder in search_folders
        for manifest in folder.glob(f"*{MANIFEST_SUFFIX}")
    }
    workspace = sample_workspace.workspace_for(given)
    if workspace is not None:
        candidates.update(workspace.recorded_manifests())
    found: list[tuple[str, str]] = []
    for manifest in candidates:
        if not read_scope.may_follow(manifest):
            continue
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(document, dict) or document.get("status") != "ok":
            continue
        outputs = [o for o in document.get("outputs") or [] if isinstance(o, dict)]
        if not any(
            o.get("role") != ROLE
            and (p := _recorded_file(o, manifest, document)) is not None
            and p.resolve() == path
            for o in outputs
        ):
            continue
        provenance = document.get("provenance")
        finished = str(
            (provenance.get("finished") if isinstance(provenance, dict) else "") or ""
        )
        for record in outputs:
            if record.get("role") != ROLE:
                continue
            gvcf = _recorded_file(record, manifest, document)
            if gvcf is not None and any(
                Path(f"{gvcf}{suffix}").is_file() for suffix in (".tbi", ".csi")
            ):
                found.append((finished, str(gvcf)))
    return max(found)[1] if found else None
