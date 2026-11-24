"""The typed Python API of WGS Extract.

Every operation takes a frozen ``*Params`` dataclass and returns a
``Result`` (the ``wgsextract.result/1`` manifest model the CLI writes)::

    from pathlib import Path
    from wgsextract_cli import api

    res = api.vcf.snp(api.VcfSnpParams(input=Path("x.cram"), region="chrM"))
    res.outputs, res.metrics, res.warnings

Operations raise ``CommandFailed`` (carrying the failed ``Result``) unless
called with ``check=False``. They accept a ``progress`` callback and a
``CancelToken``. Operations that write files or run long run one at a time
per process; quick read-only ones (``info``, ``info chip``, ``lookup``, ``sample show``,
``ref status``, ``ref recheck``, ``ref library-list``, ``report-delta``,
``example-genome list``, ``deps check``)
may run on several threads at once, each call with its own result, progress,
cancellation and settings, unless asked to save files (``info`` with
``detailed`` or ``table_format``, ``ref library-list`` with ``remote_sizes``,
``ref recheck`` with ``online``),
which makes them one at a time too. They write the same
output files and manifests as the CLI but print nothing to stdout: the
human-readable report the CLI would print is in ``Result.text`` instead, and
external tools' stdout goes to stderr.
Stdout itself is left alone, so the caller's own writes (including from a
progress callback or other threads) still reach it, e.g. a stdio JSON-RPC server.

Unset fields with a config.toml setting take it, as on the CLI. The user's
config.toml is loaded on the first call; ``use_config(path)`` selects another
for the process (from anywhere, a ``scoped_config`` block included), and ``with scoped_config(path):`` one for the calls made in
the block, on that thread and the worker threads they start.

The CLI is built on this layer: each migrated command's options are generated
from its Params fields, so the API and ``--help`` cannot drift.

Stability: the names exported here and the group modules (``api.extract``,
``api.info``, ``api.bam``, ``api.qc``, ``api.align``, ``api.vcf``,
``api.lineage``, ``api.methylation``, ``api.microarray``, ``api.panel``, ``api.ref``,
``api.repair``, ``api.genotyping``, ``api.report``, ``api.lookup``, ``api.sample``,
``api.support``, ``api.pet``, ``api.analyze``, ``api.sign``, ``api.deps``,
``api.examples``, ``api.vep``, ``api.benchmark``, ``api.score``) follow
semantic versioning from the 1.0 release; adding fields with defaults is not
a breaking change.
Modules whose names start with ``_`` are private and may change at any time.
See docs/api.md.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._public import *  # noqa: F403 -- the public re-exports


def __getattr__(name: str) -> Any:
    if name == "__all__" or not name.startswith("_"):
        public = import_module("._public", __package__)
        if name == "__all__":
            return public._exported_names()
        try:
            return getattr(public, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    public = import_module("._public", __package__)
    return sorted(set(globals()) | set(public._exported_names()))
