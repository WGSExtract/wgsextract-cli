$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$bundleRoot = if ($env:WGSEXTRACT_GUI_BUNDLE_WORKSPACE) { $env:WGSEXTRACT_GUI_BUNDLE_WORKSPACE } else { (Get-Location).Path }
$referenceLibrary = if ($env:WGSEXTRACT_REFERENCE_LIBRARY) { $env:WGSEXTRACT_REFERENCE_LIBRARY } else { Join-Path $bundleRoot "reference" }

& (Join-Path $scriptDir "run-wgsextract.ps1") ref bootstrap --ref $referenceLibrary
exit $LASTEXITCODE
