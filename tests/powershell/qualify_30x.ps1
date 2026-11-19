#requires -Version 7.2
param(
    [string]$Helpers, [string]$Root,
    [ValidateSet('', 'throw-exited', 'null-exited', 'throw-live', 'null-live')][string]$UnavailableStartTime = ''
)
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
        Get-Process | Select-Object @{n='ProcessId';e={$_.Id}}, @{n='ParentProcessId';e={0}}, @{n='CreationDate';e={Get-ProcessStartTimeUtc $_}}, @{n='WorkingSetSize';e={if ($WorkingSetOverride) { $WorkingSetOverride } else { $_.WorkingSet64 }}}
    } else {
        [pscustomobject]@{ TotalVisibleMemorySize = 64MB; FreePhysicalMemory = 60MB }
    }
}
$Pwsh = (Get-Process -Id $PID).Path
if ($UnavailableStartTime) {
    # Force the OS-property race without replacing the real child process or pipes.
    $script:ChildProcessId = $null
    Update-TypeData -TypeName System.Diagnostics.Process -MemberType ScriptProperty -MemberName StartTime -Value {
        if ($null -eq $script:ChildProcessId) { $script:ChildProcessId = $this.Id }
        if ($this.Id -eq $script:ChildProcessId -and $UnavailableStartTime.EndsWith('exited')) { $this.WaitForExit() }
        if ($UnavailableStartTime.StartsWith('throw')) { throw 'Process start time is unavailable' }
        return $null
    } -Force
    try {
        Invoke-Step 'fast_stdout' @($Pwsh, '-NoProfile', '-Command', 'Start-Sleep -Milliseconds 100; [Console]::WriteLine("captured stdout"); [Console]::Error.WriteLine("captured stderr")')
        $script:ChildProcessId = $null
        Invoke-Step 'fast_empty' @($Pwsh, '-NoProfile', '-Command', 'exit 0')
        $script:ChildProcessId = $null
        try {
            Invoke-Step 'fast_failure' @($Pwsh, '-NoProfile', '-Command', '[Console]::Error.WriteLine("fast failure"); exit 7')
            throw 'Nonzero exit was accepted'
        } catch {
            if ($_ -notmatch 'exited 7') { throw }
        }
        if ($UnavailableStartTime.EndsWith('live')) {
            $script:ChildProcessId = $null
            try {
                Invoke-Step 'live_metadata_failure' @($Pwsh, '-NoProfile', '-Command', '[Console]::Error.WriteLine("still running"); Start-Sleep -Seconds 10')
                throw 'Missing start time for a live process was accepted'
            } catch {
                if ($_ -notmatch 'start time is unavailable') { throw }
            }
        }
    } finally {
        Remove-TypeData -TypeName System.Diagnostics.Process
    }
    return
}
$Output = Join-Path $Root "space & apostrophe's.bam"
$Code = '[IO.File]::WriteAllText($args[0], "public fixture"); [Console]::WriteLine($args[1])'
$Child = Join-Path $Root 'child.ps1'
$Code | Set-Content $Child
Invoke-Step 'success' @($Pwsh, '-NoProfile', '-File', $Child, $Output, 'literal & "quotes"') @($Output)
if ((Get-Content (Join-Path $Evidence 'attempts/success/0001/stdout.log') -Raw).Trim() -ne 'literal & "quotes"') {
    throw 'Argument quoting changed'
}
Push-Location $Root
try {
    Invoke-Step 'working_directory' @($Pwsh, '-NoProfile', '-Command', '[Console]::WriteLine([Environment]::CurrentDirectory)')
} finally { Pop-Location }
if ((Get-Content (Join-Path $Evidence 'attempts/working_directory/0001/stdout.log') -Raw).Trim() -ne $Root) {
    throw 'Child did not run in the selected PowerShell location'
}
$WorkingSetOverride = 3GB
try {
    Invoke-Step 'retry' @($Pwsh, '-NoProfile', '-Command', 'Write-Output "first failure"; Start-Sleep -Milliseconds 100; exit 8')
    throw 'Failed retry was accepted'
} catch {
    if ($_ -notmatch 'exited 8') { throw }
}
$WorkingSetOverride = 1GB
Invoke-Step 'retry' @($Pwsh, '-NoProfile', '-Command', 'Write-Output "second success"')
$WorkingSetOverride = $null
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

# CIM snapshots model PID reuse and stale parent IDs, not the command runner.
$Start = [DateTime]::UtcNow.AddMinutes(-1)
function Snapshot($Id, $Parent, $Offset, $Bytes) {
    [pscustomobject]@{ ProcessId = $Id; ParentProcessId = $Parent; CreationDate = $Start.AddSeconds($Offset); WorkingSetSize = $Bytes }
}
$Known = @{ 10 = @{ created = $Start; last_seen = $Start } }
$Bytes = Get-ProcessTreeWorkingSet @(
    (Snapshot 12 11 2 300), (Snapshot 11 10 1 200), (Snapshot 10 0 0 100),
    (Snapshot 13 10 -1 9999),
    ([pscustomobject]@{ ProcessId = 17; ParentProcessId = 10; CreationDate = $null; WorkingSetSize = 9999 })
) $Known
if ($Bytes -ne 600) { throw "Did not include live descendants or included older stale child: $Bytes" }
$Bytes = Get-ProcessTreeWorkingSet @(
    (Snapshot 10 0 20 9999), (Snapshot 14 10 21 9999), (Snapshot 11 10 1 200),
    (Snapshot 12 11 2 300)
) $Known
if ($Bytes -ne 500) { throw "Reused root PID inflated memory: $Bytes" }
$Bytes = Get-ProcessTreeWorkingSet @(
    (Snapshot 11 0 30 9999), (Snapshot 15 11 31 9999), (Snapshot 12 11 2 300)
) $Known
if ($Bytes -ne 300) { throw "Reused child PID inflated memory: $Bytes" }
$Known[12].last_seen = $Start.AddSeconds(3)
$Bytes = Get-ProcessTreeWorkingSet @((Snapshot 16 12 4 9999)) $Known
if ($Bytes -ne 0) { throw 'Child created after its known parent disappeared was included' }
