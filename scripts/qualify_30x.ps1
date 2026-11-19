#requires -Version 7.2
<#
.SYNOPSIS
Qualify an installed native Windows CLI with public HG002 30x data.
.DESCRIPTION
Install with install_windows.bat first (docs/windows.md). Run from that folder,
or pass -InstallDir. This script never installs software or uploads anything.
-Download explicitly permits the public HG002 BAM/BAI (Google Cloud Storage)
and reference bootstrap/hs38 downloads (the CLI's published reference mirrors).
Without it, missing public data causes a refusal. DryRun does no IO or network.
-Version checks the installed CLI version; -Commit additionally checks a Git
checkout's exact HEAD (requires Git). Neither changes the installed version.
#>
[CmdletBinding()]
param(
    [string]$Root = (Join-Path $HOME 'wgse-30x'),
    [ValidateRange(1, 1024)][int]$Threads = [Environment]::ProcessorCount,
    [string]$InstallDir = (Get-Location).Path,
    [string]$Version = '',
    [ValidatePattern('^([0-9a-fA-F]{40})?$')][string]$Commit = '',
    [ValidateSet('all', 'prepare', 'slice', 'full')][string]$Phase = 'all',
    [switch]$Download,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Root)
$InstallDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($InstallDir)
$Evidence = Join-Path $Root 'evidence'
$Config = Join-Path $Root 'config.toml'
$Bam = Join-Path $Root 'HG002.novaseq.pcr-free.30x.dedup.grch38.bam'
$Slice = Join-Path $Root 'chr22.bam'
$RefLibrary = Join-Path $Root 'reflib'
$Temp = Join-Path $Root 'temp'
$BaseUrl = 'https://storage.googleapis.com/brain-genomics-public/research/sequencing/grch38/bam/novaseq/wgs_pcr_free/30x'
$Plan = [ordered]@{
    prepare = @('download_bam', 'download_bai', 'reference_bootstrap', 'reference_install',
        'input_check', 'slice_extract', 'slice_index', 'input_records', 'slice_records')
    slice = @('forced_missing_input', 'bwa_index', 'slice_realign', 'slice_aligned_records', 'slice_sex',
        'slice_snp', 'slice_snp_stats', 'slice_snp_q30dp10', 'slice_indel',
        'slice_indel_stats', 'slice_indel_q30dp10')
    full = @('full_realign', 'full_aligned_records', 'full_sex', 'full_snp', 'full_snp_stats',
        'full_snp_q30dp10', 'full_indel', 'full_indel_stats', 'full_indel_q30dp10')
}
Write-Host "Root: $Root; installed CLI: $InstallDir; threads: $Threads; phase: $Phase"
Write-Host 'Requirements: Windows x64, PowerShell 7.2+, 48 GiB RAM (64 recommended), 400 GiB workspace budget on a local SSD.'
Write-Host 'Sizing: chr22 extraction only 45.848 s / 4.244 GiB; full processing baseline ~14.4 h on 16 vCPU, NOT a new-tools timing guarantee.'
Write-Host "Public downloads ONLY with -Download: $BaseUrl/HG002...bam and .bam.bai;"
Write-Host 'reference bootstrap from GitHub releases and hs38 from the published CLI reference mirrors. No sample data is uploaded.'
foreach ($part in $Plan.Keys) {
    if ($Phase -in @('all', $part)) { Write-Host "$part : $($Plan[$part] -join ' -> ')" }
}
Write-Host 'Completed steps are reused only with the same installation, script, root and thread count; evidence.zip contains logs/specs/counts, never BAM/VCF/FASTQ files.'
if ($DryRun) { return }
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'Actual qualification requires native x64 Windows. Use -DryRun to inspect the plan elsewhere.'
}
if (-not (Test-Path (Join-Path $InstallDir 'pixi.lock'))) {
    throw 'Installed CLI not found. Run install_windows.bat per docs/windows.md, then run here or pass -InstallDir.'
}
$Pixi = (Get-Command pixi.exe -ErrorAction SilentlyContinue).Source
if (-not $Pixi) {
    $Pixi = Join-Path $(if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME '.pixi' }) 'bin/pixi.exe'
}
if (-not (Test-Path $Pixi)) { throw 'Pixi missing: install per docs/windows.md and reopen PowerShell.' }
$Os = Get-CimInstance Win32_OperatingSystem
$Cpu = @(Get-CimInstance Win32_Processor)
$DriveRoot = [IO.Path]::GetPathRoot($Root)
if ($DriveRoot -notmatch '^[A-Za-z]:\\$') { throw 'Root must be on a local drive, not a network share.' }
$Disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($DriveRoot.TrimEnd('\'))'"
if (-not $Disk -or $Disk.DriveType -ne 3) { throw 'Root must be on a local fixed disk.' }
$RamGiB = [math]::Round($Os.TotalVisibleMemorySize / 1MB, 2)
if ($RamGiB -lt 47) { throw "48 GiB RAM required (detected $RamGiB GiB); send CPU/RAM/disk specs before attempting this run." }
if ($Os.FreePhysicalMemory / 1MB -lt 40) { throw 'Need 40 GiB available RAM. Close other workloads and retry; 64 GiB installed is recommended.' }
$Existing = 0
if (Test-Path $Root) {
    $Existing = (Get-ChildItem -LiteralPath $Root -File -Recurse | Measure-Object Length -Sum).Sum
    if (-not (Test-Path (Join-Path $Evidence 'identity.json'))) {
        throw 'Root already exists without qualification state. Choose a new, dedicated -Root; no existing files will be overwritten.'
    }
}
$NeededGiB = [math]::Max(25, 400 - $Existing / 1GB)
if ($Disk.FreeSpace / 1GB -lt $NeededGiB) {
    throw ('Need {0:N1} GiB free on {1} (400 GiB budget minus existing run files; HG002 may exceed 100 GB, plus reference, outputs/temp). Found {2:N1} GiB. Choose a larger SSD with -Root.' -f $NeededGiB, $DriveRoot, ($Disk.FreeSpace / 1GB))
}
New-Item -ItemType Directory -Force $Root, $Evidence, $Temp | Out-Null
$Lock = $null
try {
    $Lock = [IO.File]::Open((Join-Path $Root 'qualification.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    Push-Location $InstallDir
    try {
        $InstalledVersion = (& $Pixi run --frozen --no-install wgsextract --version | Out-String).Trim()
        if ($LASTEXITCODE) { throw 'CLI environment missing. Re-run install_windows.bat; this script does not install dependencies.' }
        if ($Version -and $InstalledVersion -notmatch "(?<![0-9.])$([regex]::Escape($Version.TrimStart('v')))(?![0-9.])") {
            throw "Requested version $Version; installed: $InstalledVersion. Install the desired release separately."
        }
        if ($Commit) {
            $Head = (& git -C $InstallDir rev-parse HEAD | Out-String).Trim()
            if ($LASTEXITCODE -or $Head -ne $Commit) { throw "-Commit requires a Git checkout at exactly $Commit. It never downloads or switches source." }
        }
        $Identity = [ordered]@{
            script = (Get-FileHash $PSCommandPath -Algorithm SHA256).Hash
            helpers = (Get-FileHash (Join-Path $PSScriptRoot 'qualify_30x_helpers.ps1') -Algorithm SHA256).Hash
            manifest = (Get-FileHash 'pixi.toml' -Algorithm SHA256).Hash
            lock = (Get-FileHash 'pixi.lock' -Algorithm SHA256).Hash
            install = $InstallDir; root = $Root; threads = $Threads
            version = $InstalledVersion; commit = $Commit
        } | ConvertTo-Json
        $IdentityPath = Join-Path $Evidence 'identity.json'
        if ((Test-Path $IdentityPath) -and (Get-Content $IdentityPath -Raw).Trim() -ne $Identity.Trim()) {
            throw 'Run identity changed. Use a new -Root to avoid mixing versions, scripts or thread settings.'
        }
        $Identity | Set-Content $IdentityPath
        # JSON strings are valid TOML basic strings, including apostrophes in paths.
        $ConfigText = "reference_library = $($RefLibrary | ConvertTo-Json -Compress)`nreference_genome_library = $((Join-Path $RefLibrary 'genomes') | ConvertTo-Json -Compress)`ntemp_directory = $($Temp | ConvertTo-Json -Compress)`ncpu_threads = $Threads`n"
        [IO.File]::WriteAllText($Config, $ConfigText, [Text.UTF8Encoding]::new($false))
        Copy-Item 'pixi.toml', 'pixi.lock' $Evidence
        $Media = 'Unknown (Windows storage provider did not report media type)'
        try {
            $Physical = Get-Partition -DriveLetter $DriveRoot.Substring(0, 1) | Get-Disk
            $Storage = @(Get-PhysicalDisk | Where-Object { $_.DeviceId -in $Physical.Number })
            $Media = ($Storage | Select-Object FriendlyName, BusType, MediaType | ConvertTo-Json -Compress)
            if (-not $Storage.Count) { $Media = 'Unknown (Windows storage provider did not report media type)' }
        } catch { Write-Warning $Media }
        $Machine = [ordered]@{
            cpu = ($Cpu.Name -join '; '); cores = ($Cpu | Measure-Object NumberOfCores -Sum).Sum
            logical_cpus = ($Cpu | Measure-Object NumberOfLogicalProcessors -Sum).Sum
            ram_gib = $RamGiB; os = $Os.Caption; build = $Os.BuildNumber
            disk = $Media; filesystem = $Disk.FileSystem; free_gib_at_start = [math]::Round($Disk.FreeSpace / 1GB, 2)
            threads = $Threads; cli = $InstalledVersion
        }
        $Machine | ConvertTo-Json | Set-Content (Join-Path $Evidence 'machine.json')
        . (Join-Path $PSScriptRoot 'qualify_30x_helpers.ps1')
        Invoke-Step 'deps_check' @($Pixi, 'run', '--frozen', '--no-install', 'wgsextract', 'deps', 'check')
        $Sam = Find-Tool 'samtools'
        $Bcf = Find-Tool 'bcftools'
        Invoke-Step 'samtools_version' @($Sam, '--version')
        Invoke-Step 'bcftools_version' @($Bcf, '--version')
        $Ref = $null
        if ($Phase -in @('all', 'prepare')) {
            foreach ($item in @(@('download_bam', $Bam), @('download_bai', "$Bam.bai"))) {
                if (-not (Test-Complete $item[0])) {
                    if (-not $Download) { throw "Missing public HG002 download: $($item[0]). Re-run with -Download to confirm the documented fetches." }
                    Invoke-Step $item[0] @('curl.exe', '--fail', '--location', '--retry', '3', '--continue-at', '-',
                        '--output', $item[1], "$BaseUrl/$([IO.Path]::GetFileName($item[1]))") @($item[1])
                }
            }
            foreach ($item in @(@('reference_bootstrap', 'ref', 'bootstrap'), @('reference_install', 'ref', 'library', '--install', 'hs38'))) {
                if (-not (Test-Complete $item[0])) {
                    if (-not $Download) { throw "Missing public reference step $($item[0]). Re-run with -Download to confirm the documented fetches." }
                    Invoke-Wgse $item[0] $item[1..($item.Count - 1)]
                }
            }
            Invoke-Step 'input_check' @($Sam, 'quickcheck', '-v', $Bam)
            Invoke-Step 'slice_extract' @($Sam, 'view', '-@', "$Threads", '-b', '--fetch-pairs', '-o', $Slice, $Bam, 'chr22') @($Slice)
            Invoke-Step 'slice_index' @($Sam, 'index', '-@', "$Threads", $Slice) @("$Slice.bai")
            Invoke-Step 'input_records' @($Sam, 'idxstats', $Bam) -CountKind bam
            Invoke-Step 'slice_records' @($Sam, 'idxstats', $Slice) -CountKind bam
        }
        if ($Phase -in @('all', 'slice', 'full')) {
            foreach ($name in $Plan.prepare) {
                if (-not (Test-Complete $name)) { throw "Prepare incomplete ($name). Run -Phase prepare -Download first." }
            }
            $Refs = @(Get-ChildItem $RefLibrary -Recurse -File | Where-Object { $_.Name -match '^hs38\.(fa|fasta)(\.gz)?$' })
            if ($Refs.Count -ne 1) { throw 'Expected exactly one hs38 FASTA in the qualification reference library; rerun preparation in a new Root.' }
            $Ref = $Refs[0].FullName
        }
        if ($Phase -in @('all', 'slice')) {
            Invoke-Wgse 'forced_missing_input' @('realign', '--input', (Join-Path $Root 'deliberately-missing.bam'),
                '--ref', (Join-Path $Root 'deliberately-missing.fa'), '--outdir', (Join-Path $Root 'forced-failure')) -ExpectedFailure
            Invoke-Wgse 'bwa_index' @('ref', 'index', '--ref', $Ref, '--aligner', 'bwa') -Outputs @("$Ref.bwt", "$Ref.sa", "$Ref.ann", "$Ref.amb", "$Ref.pac")
        }
        foreach ($part in @('slice', 'full')) {
            if ($Phase -notin @('all', $part)) { continue }
            if ($part -eq 'full') {
                foreach ($name in $Plan.slice) {
                    if (-not (Test-Complete $name)) { throw "Slice qualification incomplete ($name); run -Phase slice first." }
                }
            }
            $InputBam = $(if ($part -eq 'slice') { $Slice } else { $Bam })
            $Work = Join-Path $Root $part
            Invoke-GenomeQualification $part $InputBam $Ref $Work
        }
        Remove-Item (Join-Path $Evidence 'failure.txt') -ErrorAction SilentlyContinue
        Write-Host "Qualification phase complete. Attach $Evidence/summary.md and $Root/evidence.zip to issue #1024 (Windows whole-genome qualification)."
    } finally { Pop-Location }
} catch {
    if ($Lock) { $_ | Out-String | Set-Content (Join-Path $Evidence 'failure.txt') }
    Write-Error "Qualification FAILED: $_ Evidence: $Evidence" -ErrorAction Continue
    exit 1
} finally {
    if ($Lock -and (Get-Command Save-Summary -ErrorAction SilentlyContinue)) { Save-Summary }
    if ($Lock) { $Lock.Dispose() }
}
