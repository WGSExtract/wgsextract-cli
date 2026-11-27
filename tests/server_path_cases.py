"""Inputs shared by the server's exhaustive path authorization tests."""

import ntpath
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
    "vcf/regulatory": {"ref": _DATA},
    "panel/audit": {"research_panel": True},
    "panel/bed": {"genes": ["BRCA1"]},
    "qc/vcf": {"input": _FILE},
    "ref/library": {"install": "hs38"},
    "ref/library-delete": {"genome": "hs38", "ref": _DATA},
    "ref/library-list": {"ref": _DATA},
    "ref/acmg-data": {"ref": _DATA},
    "ref/imputation": {"ref": _DATA},
    "ref/regulatory": {"ref": _DATA},
    "ref/ybrowse": {"ref": _DATA},
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
    ("panel/audit", "target_cnv"): {"target_cnv_manifest": _FILE},
    ("panel/audit", "target_cnv_manifest"): {"target_cnv": _FILE},
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


class _RecordingFlavour:
    """Windows path rules on any OS, recording what would touch the disk."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(ntpath, name)

    def abspath(self, path: str) -> str:
        assert ntpath.isabs(path), path  # else it would read the working folder
        return ntpath.normpath(path)

    def realpath(self, path: str) -> str:
        self.opened.append(path)
        return ntpath.normpath(path)


_IN = r"C:\data\a.bam"


_ALIGN_REQUESTS = {
    "align": {"r1": r"C:\data\r1.fq", "ref": r"C:\data\ref.fa"},
    "realign": {"input": r"C:\data\a.bam", "ref": r"C:\data\ref.fa"},
}


_VCF_FILE_OPS = (
    "annotate", "filter", "table", "to-fasta", "trio", "clinvar", "revel",
    "phylop", "cadd", "gerp", "gnomad", "spliceai", "alphamissense", "pharmgkb",
    "pharmcat", "regulatory", "chain-annotate",
)  # fmt: skip
_VCF_PATHS = [
    (f"vcf/{name}", item)
    for name in _VCF_FILE_OPS
    for item in find(f"vcf/{name}").fields()
    if item.spec.path and not item.spec.local_only
]


_REF_PATHS = [
    (name, item)
    for name, op in operations().items()
    if name.startswith("ref/")
    for item in op.fields()
    if item.spec.path and not item.spec.local_only
]


_PET_ANALYZE_REQUESTS = {
    "pet-align": {"r1": r"C:\data\r1.fq", "species": "dog"},
    "analyze/comprehensive": {"input": _IN},
    "analyze/batch-gen": {
        "directory": r"C:\data",
        "output": r"C:\data\batch.csv",
    },
}
_PET_ANALYZE_PATHS = [
    (op, item)
    for op in _PET_ANALYZE_REQUESTS
    for item in find(op).fields()
    if item.spec.path and not item.spec.local_only
]


_CALLER_OPS = ("cnv", "sv", "freebayes", "gatk", "deepvariant", "clair3", "phase")
_CALLER_PATHS = [
    (f"vcf/{name}", item)
    for name in _CALLER_OPS
    for item in find(f"vcf/{name}").fields()
    if item.spec.path or item.spec.may_name_file
]


_REPAIR_PATHS = [
    (op, item)
    for op in ("repair/ftdna-bam", "repair/ftdna-vcf")
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]


_SIGN_PATHS = [
    (op, item)
    for op in ("sign/create", "sign/verify")
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]
_SIGN_REQUESTS = {
    "sign/create": {"paths": [r"C:\data\a.bam"], "outdir": r"C:\data"},
    "sign/verify": {"manifest": r"C:\data\SHA256SUMS"},
}


_EXAMPLE_PATHS = [
    (op, item)
    for op in ("example-genome/list", "example-genome/download")
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]


_TYPING_PATHS = [
    (op, item)
    for op in (
        "typing/cyp2d6",
        "typing/hla",
        "typing/smn",
        "typing/str",
        "typing/target-cnv",
    )
    for item in find(op).fields()
    if item.spec.path or item.spec.may_name_file
]


_VEP_PATHS = [
    item
    for item in find("vep/run").fields()
    if item.spec.path or item.spec.may_name_file
]


_METHYLATION_PATHS = [
    item
    for item in find("methylation/pileup").fields()
    if item.spec.path or item.spec.may_name_file
]


# Each backend's region list: DeepVariant splits on whitespace, Parabricks
# (deepvariant/gatk --gpu) and GATK on commas.
_REGION_LISTS = [
    ("vcf/deepvariant", {}, " "),
    ("vcf/deepvariant", {"gpu": True}, ","),
    ("vcf/gatk", {}, ","),
]
