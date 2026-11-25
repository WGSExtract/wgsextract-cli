"""The public API's flat namespace, loaded only when callers request it."""

from functools import cache as _cache

from wgsextract_cli.core.results import CommandResult as _CommandResult
from wgsextract_cli.core.results import FileRecord as FileRecord
from wgsextract_cli.core.results import ResultWarning as ResultWarning
from wgsextract_cli.core.utils import MissingDependencyError as MissingDependencyError
from wgsextract_cli.core.utils import UsageError as UsageError
from wgsextract_cli.core.utils import WGSExtractError as WGSExtractError

from ._exports.align import *  # noqa: F403
from ._exports.analyze import *  # noqa: F403
from ._exports.bam import *  # noqa: F403
from ._exports.benchmark import *  # noqa: F403
from ._exports.cohort import *  # noqa: F403
from ._exports.deps import *  # noqa: F403
from ._exports.examples import *  # noqa: F403
from ._exports.extract import *  # noqa: F403
from ._exports.genotyping import *  # noqa: F403
from ._exports.info import *  # noqa: F403
from ._exports.lineage import *  # noqa: F403
from ._exports.lookup import *  # noqa: F403
from ._exports.methylation import *  # noqa: F403
from ._exports.microarray import *  # noqa: F403
from ._exports.panel import *  # noqa: F403
from ._exports.pet import *  # noqa: F403
from ._exports.qc import *  # noqa: F403
from ._exports.ref import *  # noqa: F403
from ._exports.repair import *  # noqa: F403
from ._exports.report import *  # noqa: F403
from ._exports.sample import *  # noqa: F403
from ._exports.score import *  # noqa: F403
from ._exports.sign import *  # noqa: F403
from ._exports.support import *  # noqa: F403
from ._exports.vcf import *  # noqa: F403
from ._exports.vep import *  # noqa: F403
from ._params import Params as Params
from ._params import json_schema as json_schema
from ._runtime import Cancelled as Cancelled
from ._runtime import CancelToken as CancelToken
from ._runtime import CommandFailed as CommandFailed
from ._runtime import ProgressCallback as ProgressCallback
from ._runtime import ProgressEvent as ProgressEvent
from ._runtime import scoped_config as scoped_config
from ._runtime import use_config as use_config

Result = _CommandResult


@_cache
def _exported_names() -> list[str]:
    return sorted(name for name in globals() if not name.startswith("_"))
