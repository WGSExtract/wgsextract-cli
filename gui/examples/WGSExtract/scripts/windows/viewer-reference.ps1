$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $PSCommandPath
& (Join-Path $scriptDir "run-wgsextract-env.ps1") python (Join-Path $scriptDir "..\viewer_reference.py") @args
exit $LASTEXITCODE
