$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
& (Join-Path $scriptDir "run-wgsextract-env.ps1") python (Join-Path $scriptDir "..\bootstrap_reference_library.py") $env:WGSEXTRACT_REFERENCE_LIBRARY
exit $LASTEXITCODE
