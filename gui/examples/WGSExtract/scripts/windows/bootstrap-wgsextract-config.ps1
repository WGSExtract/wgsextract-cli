$ErrorActionPreference = "Stop"

# The settings file and the folders the user manages live in the users workspace
# (%USERPROFILE%\wgs-extract); what only the app needs (run logs) in its hidden bundle workspace.
$bundle = if ($env:WGSEXTRACT_GUI_BUNDLE_WORKSPACE) { $env:WGSEXTRACT_GUI_BUNDLE_WORKSPACE } else { (Get-Location).Path }
$users = if ($env:WGSEXTRACT_GUI_USERS_WORKSPACE) { $env:WGSEXTRACT_GUI_USERS_WORKSPACE } else { $bundle }
$configPath = if ($env:WGSEXTRACT_GUI_CONFIG_PATH) { $env:WGSEXTRACT_GUI_CONFIG_PATH } else { Join-Path $users "config.toml" }
# bundled: the app installed the CLI, so its large system folders (reference data,
# reference genomes, temporary files) go in the users workspace too, where the user
# can see and move them. separate: a CLI installed on its own (on a server, say)
# keeps them where it does by default, local to it, so they are left unset.
$cliInstall = if ($env:WGSEXTRACT_GUI_CLI_INSTALL) { $env:WGSEXTRACT_GUI_CLI_INSTALL } else { "bundled" }

function Test-HasFiles {
    param([string]$Path)
    return (Test-Path -LiteralPath $Path -PathType Container) -and [bool](Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1)
}
function Test-SamePath {
    param([string]$A, [string]$B)
    $full = { param($p) [System.IO.Path]::GetFullPath($p).TrimEnd("\", "/") }
    return [string]::Equals((& $full $A), (& $full $B), [System.StringComparison]::OrdinalIgnoreCase)
}
# An existing config keeps every folder it names; only missing settings are added.
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

# The My Genomes library, under any of its older names. One that is a workspace's
# datasets\ folder is renamed mygenomes\, where it stays; an empty genomes\ one
# (the oldest default) is dropped.
$oldMyGenomes = Get-ExistingValue "mygenomes_library"
if (-not $oldMyGenomes) { $oldMyGenomes = Get-ExistingValue "dataset_library" }
if (-not $oldMyGenomes) { $oldMyGenomes = Get-ExistingValue "genome_library" }
$myGenomesPath = Join-Path $users "mygenomes"
if ($oldMyGenomes) {
    $isDatasets = (Test-SamePath $oldMyGenomes (Join-Path $users "datasets")) -or (Test-SamePath $oldMyGenomes (Join-Path $bundle "datasets"))
    $isEmptyGenomes = ((Test-SamePath $oldMyGenomes (Join-Path $users "genomes")) -or (Test-SamePath $oldMyGenomes (Join-Path $bundle "genomes"))) -and -not (Test-HasFiles $oldMyGenomes)
    if ($isDatasets) {
        $renamed = Join-Path (Split-Path -Parent $oldMyGenomes.TrimEnd("\", "/")) "mygenomes"
        if ((Test-Path -LiteralPath $oldMyGenomes -PathType Container) -and -not (Test-Path -LiteralPath $renamed)) {
            try { Move-Item -LiteralPath $oldMyGenomes -Destination $renamed } catch { $renamed = $oldMyGenomes }
        }
        $myGenomesPath = $renamed
    } elseif (-not $isEmptyGenomes) {
        $myGenomesPath = $oldMyGenomes
    }
}

$oldReferenceLibrary = Get-ExistingValue "reference_library"
$referenceGenomeLibraryPath = Join-Path $users "genomes"
if ($oldReferenceLibrary -and (Test-HasFiles (Join-Path $oldReferenceLibrary "genomes"))) {
    $referenceGenomeLibraryPath = Join-Path $oldReferenceLibrary "genomes"
} elseif (Test-SamePath $myGenomesPath (Join-Path $users "genomes")) {
    # genomes\ already holds the user's genomes.
    $referenceGenomeLibraryPath = Join-Path $(if ($oldReferenceLibrary) { $oldReferenceLibrary } else { Join-Path $users "reference" }) "genomes"
}

function ConvertTo-TomlBasicString {
    param([string]$Value)
    return $Value.Replace("\", "\\").Replace('"', '\"')
}
$contents = [System.Text.StringBuilder]::new()
function Add-Setting {
    param([string]$Key, [string]$Value)
    [void]$contents.Append("$Key = `"$(ConvertTo-TomlBasicString $Value)`"`n")
}
# A folder setting the config doesn't have yet. The CLI requires its temporary and
# log folders to exist already.
function Add-FolderSetting {
    param([string]$Key, [string]$Path)
    if (-not (Get-ExistingValue $Key) -and ($Key -eq "temp_directory" -or $Key -eq "log_dir")) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
    Add-Setting $Key $Path
}

# Unset: each command writes to a wgse folder beside its input files from the lab,
# whose own folder stays untouched. Only Advanced mode may point it elsewhere.
Add-Setting "output_directory" ""
Add-FolderSetting "mygenomes_library" $myGenomesPath
if ($cliInstall -eq "bundled") {
    Add-FolderSetting "reference_library" (Join-Path $users "reference")
    Add-FolderSetting "reference_genome_library" $referenceGenomeLibraryPath
    Add-FolderSetting "temp_directory" (Join-Path $users "temp")
}
Add-FolderSetting "log_dir" (Join-Path $bundle "logs")
foreach ($key in "reference_fasta", "default_input_vcf", "mother_vcf_path", "father_vcf_path", "haplogrep_executable") {
    Add-Setting $key ""
}

[ordered]@{
    path = $configPath
    contents = $contents.ToString()
} | ConvertTo-Json -Compress
