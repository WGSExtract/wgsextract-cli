#requires -Version 7.2
param([string]$Helpers, [string]$Root, [string]$InstallDir, [string]$Reference, [string]$Bam, [string]$ReferenceLibrary)
$ErrorActionPreference = 'Stop'
$Evidence = Join-Path $Root 'evidence'
$Config = Join-Path $Root 'config.toml'
$Threads = 2
$Machine = @{ cpu = 'Tiny genome smoke'; cores = 2; logical_cpus = 2; ram_gib = 8; disk = 'fixture'; os = $PSVersionTable.OS; build = 'fixture'; cli = 'real CLI' }
New-Item -ItemType Directory -Force $Evidence, (Join-Path $Root 'temp') | Out-Null
$Text = "reference_library = $($ReferenceLibrary | ConvertTo-Json -Compress)`nstate_directory = $((Join-Path $Root 'state') | ConvertTo-Json -Compress)`ntemp_directory = $((Join-Path $Root 'temp') | ConvertTo-Json -Compress)`ncpu_threads = 2`n"
[IO.File]::WriteAllText($Config, $Text, [Text.UTF8Encoding]::new($false))
. $Helpers
# Only the Windows telemetry boundary is replaced on Linux; every CLI/tool is real.
function Get-CimInstance {
    param($ClassName, $Property)
    if ($ClassName -eq 'Win32_Process') {
        Get-Process | Select-Object @{n='ProcessId';e={$_.Id}}, @{n='ParentProcessId';e={0}}, @{n='CreationDate';e={$_.StartTime.ToUniversalTime()}}, @{n='WorkingSetSize';e={$_.WorkingSet64}}
    } else {
        [pscustomobject]@{ TotalVisibleMemorySize = 8MB; FreePhysicalMemory = 6MB }
    }
}
$Pixi = (Get-Command pixi).Source
Push-Location $InstallDir
try {
    $Sam = Find-Tool 'samtools'
    $Bcf = Find-Tool 'bcftools'
    Invoke-Step 'input_check' @($Sam, 'quickcheck', '-v', $Bam)
    $Slice = Join-Path $Root 'chr22.bam'
    Invoke-Step 'slice_extract' @($Sam, 'view', '-@', "$Threads", '-b', '--fetch-pairs', '-o', $Slice, $Bam, 'chr22') @($Slice)
    Invoke-Step 'slice_index' @($Sam, 'index', '-@', "$Threads", $Slice) @("$Slice.bai")
    Invoke-Wgse 'forced_missing_input' @('realign', '--input', (Join-Path $Root 'deliberately-missing.bam'),
        '--ref', (Join-Path $Root 'deliberately-missing.fa'), '--outdir', (Join-Path $Root 'forced-failure')) -ExpectedFailure
    Invoke-Wgse 'bwa_index' @('ref', 'index', '--ref', $Reference, '--aligner', 'bwa') -Outputs @("$Reference.bwt")
    Invoke-GenomeQualification 'slice' $Slice $Reference (Join-Path $Root 'slice')
    Invoke-GenomeQualification 'full' $Bam $Reference (Join-Path $Root 'full')
} finally { Pop-Location }
