#requires -Version 7.2
param([string]$Helpers, [string]$Root)
$ErrorActionPreference = 'Stop'
$Evidence = Join-Path $Root 'evidence'
$Threads = 2
$Machine = @{ cpu = 'OS boundary fixture'; cores = 2; logical_cpus = 2; ram_gib = 64; disk = 'SSD'; os = 'fixture'; build = 'fixture'; cli = 'fixture' }
New-Item -ItemType Directory -Force $Evidence | Out-Null
. $Helpers
# The OS is the only substituted boundary; commands are real child processes.
function Get-CimInstance {
    param($ClassName, $Property)
    if ($ClassName -eq 'Win32_Process') {
        Get-Process | Select-Object @{n='ProcessId';e={$_.Id}}, @{n='ParentProcessId';e={0}}, @{n='WorkingSetSize';e={$_.WorkingSet64}}
    } else {
        [pscustomobject]@{ TotalVisibleMemorySize = 64MB; FreePhysicalMemory = 60MB }
    }
}
$Pwsh = (Get-Process -Id $PID).Path
$Output = Join-Path $Root "space & apostrophe's.bam"
$Code = '[IO.File]::WriteAllText($args[0], "public fixture"); [Console]::WriteLine($args[1])'
$Child = Join-Path $Root 'child.ps1'
$Code | Set-Content $Child
Invoke-Step 'success' @($Pwsh, '-NoProfile', '-File', $Child, $Output, 'literal & "quotes"') @($Output)
if ((Get-Content (Join-Path $Evidence 'success.stdout.log') -Raw).Trim() -ne 'literal & "quotes"') {
    throw 'Argument quoting changed'
}
$Original = Get-Content (Join-Path $Evidence 'success.step.json') -Raw
Invoke-Step 'success' @($Pwsh, '-NoProfile', '-Command', 'exit 9') @($Output)
if ((Get-Content (Join-Path $Evidence 'success.step.json') -Raw) -ne $Original) { throw 'Completed step reran' }
try {
    Invoke-Step 'failure' @($Pwsh, '-NoProfile', '-Command', 'exit 7')
    throw 'Nonzero exit was accepted'
} catch {
    if ($_ -notmatch 'exited 7') { throw }
}
Invoke-Step 'forced_missing_input' @($Pwsh, '-NoProfile', '-Command', '[Console]::Error.WriteLine("deliberately-missing.bam"); exit 3') -ExpectedFailure
Invoke-Step 'vcf_stats' @($Pwsh, '-NoProfile', '-Command', 'Write-Output "SN`t0`tnumber of records:`t42"') -CountKind vcf
Invoke-Step 'bam_records' @($Pwsh, '-NoProfile', '-Command', 'Write-Output "chr22`t50000`t10`t2"; Write-Output "*`t0`t0`t9"') -CountKind bam
[IO.File]::WriteAllText($Output, 'changed')
try {
    [void](Test-Complete 'success')
    throw 'Changed output was accepted'
} catch {
    if ($_ -notmatch 'Completed output missing/changed') { throw }
}
