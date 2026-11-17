$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
# Into the settings file's reference_library (in the users workspace), unless a
# folder is named here.
if ($env:WGSEXTRACT_REFERENCE_LIBRARY) {
    & (Join-Path $scriptDir "run-wgsextract.ps1") ref bootstrap --ref $env:WGSEXTRACT_REFERENCE_LIBRARY
} else {
    & (Join-Path $scriptDir "run-wgsextract.ps1") ref bootstrap
}
exit $LASTEXITCODE
