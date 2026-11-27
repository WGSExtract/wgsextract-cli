$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$library = [string]$env:WGSEXTRACT_REFERENCE_LIBRARY
& (Join-Path $scriptDir "run-wgsextract-env.ps1") python (Join-Path $scriptDir "..\bootstrap_reference_library.py") "--library=$library" @args
exit $LASTEXITCODE
