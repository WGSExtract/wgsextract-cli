from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.vep import (
    VepDeleteParams,
    VepDownloadParams,
    VepRunParams,
    VepVerifyParams,
)
from wgsextract_cli.cli.registration.vep import register as register

from ._vep_resources import run_vep_delete, run_vep_download, run_vep_verify
from ._vep_run import run_vep

cli_vep_run = cli_handler(VepRunParams, run_vep)
cli_vep_download = cli_handler(VepDownloadParams, run_vep_download)
cli_vep_verify = cli_handler(VepVerifyParams, run_vep_verify)
cli_vep_delete = cli_handler(VepDeleteParams, run_vep_delete)
