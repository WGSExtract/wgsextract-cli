"""Reuse microarray outputs whose inputs have not changed.

Each output unit (the combined kit, each vendor kit, each AADR panel) gets a
``<output>.inputs.json`` sidecar beside it, recording the run's inputs (input
and reference identity, every option that changes the genotypes, the CLI
version) and the files the unit wrote. A later run reuses a unit only when
its sidecar's inputs equal the new run's and every recorded file is still
there with the recorded size and mtime; anything else (no sidecar, a corrupt
one, a changed input or option, a touched output) regenerates the unit and
rewrites its sidecar.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wgsextract_cli import __version__
from wgsextract_cli.core.microarray_identity import identity_policy
from wgsextract_cli.core.microarray_quality import quality_policy
from wgsextract_cli.core.microarray_quirks import quirks_mode
from wgsextract_cli.core.microarray_utils import GenotypeTally
from wgsextract_cli.core.results import FileRecord, current_result

SIDECAR_SUFFIX = ".inputs.json"
SCHEMA = "wgsextract.microarray-inputs/1"

COMBINED = "all"
"""The unit holding the combined kit and everything else not a vendor or
AADR file (called VCFs, hg19 liftover, genotype table, reasons report)."""


def file_identity(path: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    """Path, size and mtime: what tells a changed input apart without reading it."""
    if not path:
        return None
    absolute = Path(path).expanduser().absolute()
    try:
        info = absolute.stat()
    except OSError:
        return {"path": str(absolute)}
    return {"path": str(absolute), "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def run_inputs(
    params: Any,
    *,
    input_path: str,
    ref_fasta: str,
    build: str | None,
    ref_vcf_tab: str | None,
    ploidy_file: str | None,
    liftover_chain: str | None,
    sample: str | None,
) -> dict[str, Any]:
    """The inputs shared by every unit; options are resolved through
    config.toml, so changing a ``microarray_*`` setting counts too."""
    return {
        "cli_version": __version__,
        "input": file_identity(input_path),
        "reference": file_identity(ref_fasta),
        "build": build,
        "ref_vcf_tab": file_identity(ref_vcf_tab),
        "ploidy_file": file_identity(ploidy_file),
        "liftover_chain": file_identity(liftover_chain),
        "region": params.region,
        "sample": sample,
        "quality": asdict(quality_policy(params)),
        "identity": asdict(identity_policy(params)),
        "quirks": quirks_mode(params),
    }


@dataclass
class Unit:
    """One reusable output: ``key`` is ``all``, a vendor format or ``aadr_<panel>``."""

    key: str
    sidecar: Path
    extra: dict[str, Any] = field(default_factory=dict)
    recorded: dict[str, Any] | None = None

    @property
    def fresh(self) -> bool:
        return self.recorded is not None

    def outputs(self) -> list[dict[str, Any]]:
        return list((self.recorded or {}).get("outputs", []))

    def output_with_role(self, role: str) -> str | None:
        return next((o["path"] for o in self.outputs() if o.get("role") == role), None)

    def tally(self) -> GenotypeTally | None:
        """The vendor kit's autosomal tally when it was written."""
        return _tally((self.recorded or {}).get("tally"))

    def autosomal(self) -> GenotypeTally | None:
        """The run's autosomal tally of the combined kit, for runs that don't
        keep the kit (AADR)."""
        return _tally((self.recorded or {}).get("autosomal"))


def _tally(saved: object) -> GenotypeTally | None:
    if not isinstance(saved, dict):
        return None
    try:
        return GenotypeTally(
            sites=int(saved.get("sites", 0)),
            called=int(saved.get("called", 0)),
            het=int(saved.get("het", 0)),
        )
    except (TypeError, ValueError):
        return None


def _tally_json(tally: GenotypeTally) -> dict[str, int]:
    return {"sites": tally.sites, "called": tally.called, "het": tally.het}


def _expected(unit: Unit, inputs: Mapping[str, Any]) -> dict[str, Any]:
    return {**inputs, "unit": unit.key, **unit.extra}


def check(unit: Unit, inputs: Mapping[str, Any]) -> Unit:
    """Load ``unit``'s sidecar into ``unit.recorded`` when it is reusable."""
    unit.recorded = None
    try:
        saved = json.loads(unit.sidecar.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return unit
    except (OSError, UnicodeDecodeError, ValueError) as e:
        logging.info(f"Ignoring unreadable {unit.sidecar.name}: {e}")
        return unit
    if not isinstance(saved, dict) or saved.get("schema") != SCHEMA:
        logging.info(f"Ignoring {unit.sidecar.name}: not a {SCHEMA} file")
        return unit
    if saved.get("inputs") != _expected(unit, inputs):
        logging.info(f"Regenerating {unit.key}: its inputs changed")
        return unit
    outputs = saved.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return unit
    for output in outputs:
        if not isinstance(output, dict) or not isinstance(output.get("path"), str):
            return unit
        if file_identity(output["path"]) != output.get("file"):
            logging.info(f"Regenerating {unit.key}: {output['path']} changed")
            return unit
    unit.recorded = saved
    return unit


def replay(unit: Unit) -> None:
    """Register a reused unit's outputs as this run's, marked ``reused``."""
    for output in unit.outputs():
        metrics = dict(output.get("metrics") or {})
        metrics["reused"] = True
        current_result().add_output(output["path"], output["role"], metrics=metrics)


def save(
    unit: Unit,
    inputs: Mapping[str, Any],
    outputs: Iterable[FileRecord],
    *,
    tally: GenotypeTally | None = None,
    autosomal: GenotypeTally | None = None,
) -> None:
    """Write ``unit``'s sidecar for the files it now has on disk."""
    recorded = []
    for record in outputs:
        identity = file_identity(record.path)
        if identity is None or "size" not in identity:
            continue
        metrics = {k: v for k, v in record.metrics.items() if k != "reused"}
        recorded.append(
            {"path": record.path, "role": record.role, "metrics": metrics}
            | {"file": identity}
        )
    if not recorded:
        unit.sidecar.unlink(missing_ok=True)
        return
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "inputs": _expected(unit, inputs),
        "outputs": recorded,
    }
    if tally is not None:
        document["tally"] = _tally_json(tally)
    if autosomal is not None:
        document["autosomal"] = _tally_json(autosomal)
    tmp = unit.sidecar.with_name(unit.sidecar.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        tmp.replace(unit.sidecar)
    except OSError as e:
        logging.warning(f"Could not save {unit.sidecar}: {e}")
        tmp.unlink(missing_ok=True)
