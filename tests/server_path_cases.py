"""Inputs shared by the server's exhaustive path authorization tests."""

import typing
from typing import Any

from wgsextract_cli.server.operations import find, operations

_DATA = r"C:\data"
_FILE = _DATA + r"\in.vcf.gz"
# Requests each operation accepts with every path inside _DATA, beyond its
# required fields; a field's companions are what it needs beside it.
_BASE_REQUESTS: dict[str, dict[str, Any]] = {
    "align": {"r1": _FILE},
    "pet-align": {"r1": _FILE},
    "qc/fastp": {"r1": _FILE},
    "analyze/batch-gen": {"output": _DATA + r"\batch.csv"},
    # The library defaults to one outside the allowed folders.
    "example-genome/download": {"target_root": _DATA},
    "bam/to-ubam": {"input": _FILE},
    "extract/custom": {"region": "chrM"},
    "extract/region": {"formats": "bam"},
    "info/chip": {"input": _FILE},
    "info/calculate-coverage": {"input": _FILE},
    "lookup": {"locus": "GRCh38:chr1:1"},
    "vcf/compare": {"callset": [f"a={_FILE}", f"b={_FILE}"]},
    "vcf/impute": {"input": _FILE, "ref": _DATA},
    "panel/bed": {"genes": ["BRCA1"]},
    "qc/vcf": {"input": _FILE},
    "ref/library": {"install": "hs38"},
    "ref/library-delete": {"genome": "hs38", "ref": _DATA},
    "ref/library-list": {"ref": _DATA},
    "ref/acmg-data": {"ref": _DATA},
    "ref/imputation": {"ref": _DATA},
    "report": {"input": _FILE},
    "sample/show": {"input": _FILE},
    "sample/set": {"input": _FILE, "sex": "female"},
    "sample/unset": {"input": _FILE, "sex": True},
    # With no cache folder, the default one (~/.vep) is checked too.
    "vep/download": {"vep_cache": _DATA},
    "vep/verify": {"vep_cache": _DATA},
    "vep/delete": {"vep_cache": _DATA},
}
_COMPANIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("basecall", "regions_bed"): {"alignment": _FILE, "regions_bed": _FILE},
    ("basecall", "alignment"): {"alignment": _FILE, "regions_bed": _FILE},
    ("bam/to-ubam", "r1"): {"input": None, "r1": _FILE},
    ("bam/to-ubam", "r2"): {"input": None, "r1": _FILE},
    ("methylation/regions", "phase_blocks"): {
        "input": _DATA + r"\haplotagged.bam",
        "partition_hp": True,
        "phase_blocks": _DATA + r"\phased.blocks.tsv",
    },
    ("vcf/impute", "vcf_input"): {"input": None, "vcf_input": _FILE},
    ("ref/recheck", "save_answers"): {"online": True},
}


def _path_value(item: Any, path: str) -> Any:
    return [path] if typing.get_origin(item.base_type) in (tuple, list) else path


def _filler(item: Any) -> Any:
    if item.spec.path:
        return _path_value(item, _DATA if item.spec.path == "directory" else _FILE)
    if item.spec.choices:
        return item.spec.choices[0]
    return {int: 1, float: 1.0, bool: True}.get(item.base_type, "x")


def _base_request(name: str) -> dict[str, Any]:
    op = find(name)
    body = {f.name: _filler(f) for f in op.fields() if f.required}
    if any(f.name == "outdir" for f in op.fields()):
        body["outdir"] = _DATA
    return {**body, **_BASE_REQUESTS.get(name, {})}


_EVERY_PATH = [
    (name, item.name)
    for name, op in operations().items()
    for item in op.fields()
    if item.spec.path and not item.spec.local_only
]
