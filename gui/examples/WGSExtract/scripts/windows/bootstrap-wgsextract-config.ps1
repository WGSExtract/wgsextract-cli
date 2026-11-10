$ErrorActionPreference = "Stop"

$workspace = if ($env:GUI_FOR_CLI_BUNDLE_WORKSPACE) { $env:GUI_FOR_CLI_BUNDLE_WORKSPACE } else { (Get-Location).Path }
$configPath = if ($env:GUI_FOR_CLI_CONFIG_PATH) { $env:GUI_FOR_CLI_CONFIG_PATH } else { Join-Path $workspace "config.toml" }
$outputPath = Join-Path $workspace "output"
$referencePath = Join-Path $workspace "reference"
# New installs keep datasets in datasets\ and reference genomes in genomes\.
# An existing config keeps folders that hold files: its old genome_library stays
# the dataset library, and genomes already in <reference_library>\genomes stay there.
function Test-HasFiles {
    param([string]$Path)
    return (Test-Path -LiteralPath $Path -PathType Container) -and [bool](Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1)
}
function Test-SamePath {
    param([string]$A, [string]$B)
    $full = { param($p) [System.IO.Path]::GetFullPath($p).TrimEnd("\", "/") }
    return [string]::Equals((& $full $A), (& $full $B), [System.StringComparison]::OrdinalIgnoreCase)
}
function Get-ExistingValue {
    param([string]$Key)
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { return "" }
    foreach ($line in Get-Content -LiteralPath $configPath -Encoding UTF8) {
        if ($line -match "^$Key = `"(.*)`"\s*$") {
            return $Matches[1].Replace('\"', '"').Replace("\\", "\")
        }
    }
    return ""
}
$oldGenomeLibrary = Get-ExistingValue "genome_library"
$oldReferenceLibrary = Get-ExistingValue "reference_library"
$oldDefaultGenomes = Join-Path $workspace "genomes"
$datasetLibraryPath = Join-Path $workspace "datasets"
if ($oldGenomeLibrary -and (-not (Test-SamePath $oldGenomeLibrary $oldDefaultGenomes) -or (Test-HasFiles $oldGenomeLibrary))) {
    $datasetLibraryPath = $oldGenomeLibrary
}
$referenceGenomeLibraryPath = $oldDefaultGenomes
if ($oldReferenceLibrary -and (Test-HasFiles (Join-Path $oldReferenceLibrary "genomes"))) {
    $referenceGenomeLibraryPath = Join-Path $oldReferenceLibrary "genomes"
} elseif (Test-SamePath $datasetLibraryPath $oldDefaultGenomes) {
    # genomes\ already holds datasets.
    $referenceGenomeLibraryPath = Join-Path $(if ($oldReferenceLibrary) { $oldReferenceLibrary } else { $referencePath }) "genomes"
}
$tempPath = Join-Path $workspace "temp"
$logPath = Join-Path $workspace "logs"
# The CLI requires its temporary directory to exist already.
New-Item -ItemType Directory -Force -Path $tempPath, $logPath | Out-Null

function ConvertTo-TomlBasicString {
    param([string]$Value)
    return $Value.Replace("\", "\\").Replace('"', '\"')
}

$tomlOutputPath = ConvertTo-TomlBasicString $outputPath
$tomlReferencePath = ConvertTo-TomlBasicString $referencePath
$tomlReferenceGenomeLibraryPath = ConvertTo-TomlBasicString $referenceGenomeLibraryPath
$tomlDatasetLibraryPath = ConvertTo-TomlBasicString $datasetLibraryPath
$tomlTempPath = ConvertTo-TomlBasicString $tempPath
$tomlLogPath = ConvertTo-TomlBasicString $logPath

[ordered]@{
    path = $configPath
    contents = "output_directory = `"$tomlOutputPath`"`nreference_library = `"$tomlReferencePath`"`nreference_genome_library = `"$tomlReferenceGenomeLibraryPath`"`ndataset_library = `"$tomlDatasetLibraryPath`"`ntemp_directory = `"$tomlTempPath`"`nlog_dir = `"$tomlLogPath`"`nreference_fasta = `"`"`ndefault_input_vcf = `"`"`nmother_vcf_path = `"`"`nfather_vcf_path = `"`"`nhaplogrep_executable = `"`"`n"
} | ConvertTo-Json -Compress
