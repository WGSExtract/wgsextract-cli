# Helpers share the qualification script's scope; this file is not an entry point.
function Find-Tool([string]$Name) {
    $Found = & $Pixi run --frozen --no-install python -c "import shutil; print(shutil.which('$Name') or '')"
    if ($LASTEXITCODE -or -not $Found -or -not (Test-Path $Found)) {
        throw "$Name missing from installed environment. Re-run install_windows.bat."
    }
    return "$Found".Trim()
}

function Test-Complete([string]$Name) {
    $Path = Join-Path $Evidence "$Name.step.json"
    if (-not (Test-Path $Path)) { return $false }
    $Row = Get-Content $Path -Raw | ConvertFrom-Json
    if ($Row.status -ne 'complete') { return $false }
    foreach ($Output in $Row.outputs) {
        if (-not (Test-Path -LiteralPath $Output.path) -or (Get-Item -LiteralPath $Output.path).Length -ne $Output.bytes) {
            throw "Completed output missing/changed: $($Output.path). Restore it or choose a new Root; downstream results will not be mixed."
        }
    }
    return $true
}

function Save-Summary {
    $Rows = @(Get-ChildItem $Evidence -Filter '*.step.json' | ForEach-Object {
        Get-Content $_.FullName -Raw | ConvertFrom-Json
    } | Sort-Object started_utc)
    if ($Rows.Count) {
        [ordered]@{ machine = $Machine; steps = $Rows } | ConvertTo-Json -Depth 8 |
            Set-Content (Join-Path $Evidence 'summary.json')
        $Rows | Select-Object step, status, exit_code, attempt_count, seconds, peak_tree_ws_gib, peak_system_used_gib, output_records |
            Export-Csv (Join-Path $Evidence 'summary.csv') -NoTypeInformation
    }
    $Table = @(
        '# Windows HG002 30x qualification'
        ''
        "CPU: $($Machine.cpu); physical cores: $($Machine.cores); logical CPUs: $($Machine.logical_cpus); threads: $Threads"
        "RAM: $($Machine.ram_gib) GiB; disk: $($Machine.disk); OS: $($Machine.os) $($Machine.build); CLI: $($Machine.cli)"
        ''
        'Memory is sampled every second: process-tree working set (shared pages may count twice) and whole-system physical use (includes other applications). Short-lived peaks can be missed.'
        'Records are alignment records (mapped + unmapped, including secondary/supplementary), or VCF records; q30dp10 rows are filtered counts. Blank means not applicable.'
        'Wall seconds sum all attempts (excluding time between retries); peaks are maxima across attempts, including failed/resumed work. Per-attempt measurements and logs are retained under attempts/. Interrupted attempts contain measurements through the last sample.'
        ''
        '| Step | Status | Exit | Attempts | Wall seconds | Peak tree WS GiB | Peak system GiB | Records |'
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |'
    )
    foreach ($Row in $Rows) {
        $Table += "| $($Row.step) | $($Row.status) | $($Row.exit_code) | $($Row.attempt_count) | $($Row.seconds) | $($Row.peak_tree_ws_gib) | $($Row.peak_system_used_gib) | $($Row.output_records) |"
    }
    $Table | Set-Content (Join-Path $Evidence 'summary.md')
    # Only this dedicated evidence directory is archived, never the genome outputs.
    Compress-Archive -Path "$Evidence/*" -DestinationPath (Join-Path $Root 'evidence.zip') -Force
}

function Get-ProcessTreeWorkingSet($Processes, [hashtable]$Known) {
    $Processes = @($Processes | Where-Object { $null -ne $_.CreationDate })
    $Now = [DateTime]::UtcNow
    $Current = @{}
    foreach ($Item in $Processes) {
        $Current[[int]$Item.ProcessId] = $Item
        $Key = [int]$Item.ProcessId
        if ($Known.ContainsKey($Key) -and $Known[$Key].created -eq ([DateTime]$Item.CreationDate).ToUniversalTime()) {
            $Known[$Key].last_seen = $Now
        }
    }
    do {
        $Added = $false
        foreach ($Item in $Processes) {
            $Key = [int]$Item.ProcessId
            $Created = ([DateTime]$Item.CreationDate).ToUniversalTime()
            if ($Known.ContainsKey($Key) -and $Known[$Key].created -eq $Created) { continue }
            $Parent = $Known[[int]$Item.ParentProcessId]
            if (-not $Parent -or $Created -lt $Parent.created -or $Created -gt $Parent.last_seen) { continue }
            $LiveParent = $Current[[int]$Item.ParentProcessId]
            # A reused PID is not the previously observed parent.
            if ($LiveParent -and ([DateTime]$LiveParent.CreationDate).ToUniversalTime() -ne $Parent.created) { continue }
            $Known[$Key] = @{ created = $Created; last_seen = $Now }
            $Added = $true
        }
    } while ($Added)
    [long]$Bytes = 0
    foreach ($Item in $Processes) {
        $Identity = $Known[[int]$Item.ProcessId]
        if ($Identity -and $Identity.created -eq ([DateTime]$Item.CreationDate).ToUniversalTime()) {
            $Bytes += [long]$Item.WorkingSetSize
        }
    }
    return $Bytes
}

function Save-StepAttempt($Row, [string]$AttemptDir, [Diagnostics.Stopwatch]$Watch) {
    $Row.seconds = [math]::Round($Watch.Elapsed.TotalSeconds, 3)
    $Row | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $AttemptDir 'step.json')
}

function Invoke-Step {
    param(
        [string]$Name, [string[]]$Command, [string[]]$Outputs = @(),
        [switch]$ExpectedFailure, [ValidateSet('', 'bam', 'vcf')][string]$CountKind = ''
    )
    if (Test-Complete $Name) { Write-Host "SKIP $Name (completed)"; return }
    $AttemptsDir = Join-Path $Evidence "attempts/$Name"
    New-Item -ItemType Directory -Force $AttemptsDir | Out-Null
    $Previous = @(Get-ChildItem $AttemptsDir -Directory | Sort-Object Name | ForEach-Object {
        $Path = Join-Path $_.FullName 'step.json'
        $Attempt = Get-Content $Path -Raw | ConvertFrom-Json
        if ($Attempt.status -eq 'running') {
            $Attempt.status = 'interrupted'
            $Attempt | ConvertTo-Json -Depth 6 | Set-Content $Path
        }
        $Attempt
    })
    $AttemptDir = Join-Path $AttemptsDir ('{0:D4}' -f ($Previous.Count + 1))
    New-Item -ItemType Directory $AttemptDir | Out-Null
    $StdoutPath = Join-Path $AttemptDir 'stdout.log'
    $StderrPath = Join-Path $AttemptDir 'stderr.log'
    $Info = [Diagnostics.ProcessStartInfo]::new()
    $Info.FileName = $Command[0]
    $Info.WorkingDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath('.')
    $Info.UseShellExecute = $false
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    foreach ($Argument in $Command[1..($Command.Count - 1)]) { $Info.ArgumentList.Add($Argument) }
    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $Info
    $OutStream = [IO.File]::Create($StdoutPath)
    $ErrStream = [IO.File]::Create($StderrPath)
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Row = [ordered]@{
        step = $Name; started_utc = [DateTime]::UtcNow.ToString('o')
        status = 'running'; exit_code = $null; seconds = 0
        peak_tree_ws_gib = 0; peak_system_used_gib = 0; output_records = $null
        outputs = @(); command = $Command; stdout = $StdoutPath; stderr = $StderrPath
    }
    Save-StepAttempt $Row $AttemptDir $Watch
    $Started = $false
    Write-Host "START $Name (logs: $StdoutPath / $StderrPath)"
    try {
        $Started = $Process.Start()
        if (-not $Started) { throw "Could not start $Name" }
        $StartedAt = $Process.StartTime.ToUniversalTime()
        $OutTask = $Process.StandardOutput.BaseStream.CopyToAsync($OutStream)
        $ErrTask = $Process.StandardError.BaseStream.CopyToAsync($ErrStream)
        $Known = @{}
        do {
            $Processes = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, CreationDate, WorkingSetSize)
            if (-not $Known.Count) {
                $RootProcess = $Processes | Where-Object {
                    $null -ne $_.CreationDate -and $_.ProcessId -eq $Process.Id -and
                    [math]::Abs((([DateTime]$_.CreationDate).ToUniversalTime() - $StartedAt).TotalMilliseconds) -lt 1
                }
                if ($RootProcess) {
                    $Known[$Process.Id] = @{ created = ([DateTime]$RootProcess.CreationDate).ToUniversalTime(); last_seen = [DateTime]::UtcNow }
                }
            }
            $Used = (Get-ProcessTreeWorkingSet $Processes $Known) / 1GB
            $Memory = Get-CimInstance Win32_OperatingSystem -Property TotalVisibleMemorySize, FreePhysicalMemory
            $Row.peak_tree_ws_gib = [math]::Max($Row.peak_tree_ws_gib, $Used)
            $Row.peak_system_used_gib = [math]::Max($Row.peak_system_used_gib, ($Memory.TotalVisibleMemorySize - $Memory.FreePhysicalMemory) / 1MB)
            Save-StepAttempt $Row $AttemptDir $Watch
        } while (-not $Process.WaitForExit(1000))
        [void]$OutTask.GetAwaiter().GetResult()
        [void]$ErrTask.GetAwaiter().GetResult()
        $OutStream.Dispose()
        $ErrStream.Dispose()
        $Row.exit_code = $Process.ExitCode
        if ($ExpectedFailure) {
            if ($Process.ExitCode -eq 0 -or -not (Select-String -Path $StdoutPath, $StderrPath -Pattern 'deliberately-missing' -Quiet)) {
                throw 'Forced missing-input test did not fail for the intended missing input.'
            }
        } elseif ($Process.ExitCode -ne 0) { throw "$Name exited $($Process.ExitCode); see $StderrPath" }
        foreach ($Pattern in $Outputs) {
            $Files = @(Get-Item $Pattern -ErrorAction SilentlyContinue)
            if (-not $Files.Count) { throw "No expected output: $Pattern" }
            foreach ($File in $Files) {
                if ($File.Length -eq 0) { throw "Empty output: $($File.FullName)" }
                $Row.outputs += @{ path = $File.FullName; bytes = $File.Length }
            }
        }
        if ($CountKind -eq 'bam') {
            [long]$Count = 0
            foreach ($Line in [IO.File]::ReadLines($StdoutPath)) {
                $Fields = $Line.Split("`t")
                if ($Fields.Count -eq 4) { $Count += [long]$Fields[2] + [long]$Fields[3] }
            }
            if ($Count -le 0) { throw "No alignment records found by $Name" }
            $Row.output_records = $Count
        } elseif ($CountKind -eq 'vcf') {
            $Match = Select-String -Path $StdoutPath -Pattern '^SN\s+\d+\s+number of records:\s+(\d+)'
            if (-not $Match) { throw "No VCF record count found by $Name" }
            $Row.output_records = [long]$Match.Matches[0].Groups[1].Value
        }
        $Row.status = 'complete'
        Write-Host "COMPLETE $Name"
    } finally {
        if ($Row.status -ne 'complete') { $Row.status = 'failed' }
        if ($Started -and -not $Process.HasExited) { $Process.Kill($true); $Process.WaitForExit() }
        if ($Started) {
            [void]$OutTask.GetAwaiter().GetResult()
            [void]$ErrTask.GetAwaiter().GetResult()
        }
        $OutStream.Dispose()
        $ErrStream.Dispose()
        $Process.Dispose()
        $Row.peak_tree_ws_gib = [math]::Round($Row.peak_tree_ws_gib, 3)
        $Row.peak_system_used_gib = [math]::Round($Row.peak_system_used_gib, 3)
        Save-StepAttempt $Row $AttemptDir $Watch
        $Attempts = @($Previous) + @([pscustomobject]$Row)
        $Summary = [ordered]@{}
        foreach ($Key in $Row.Keys) { $Summary[$Key] = $Row[$Key] }
        $Summary.started_utc = $Attempts[0].started_utc
        $Summary.attempt_count = $Attempts.Count
        $Summary.seconds = [math]::Round(($Attempts | Measure-Object seconds -Sum).Sum, 3)
        $Summary.peak_tree_ws_gib = ($Attempts | Measure-Object peak_tree_ws_gib -Maximum).Maximum
        $Summary.peak_system_used_gib = ($Attempts | Measure-Object peak_system_used_gib -Maximum).Maximum
        $Summary.attempts = $Attempts
        $Summary | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $Evidence "$Name.step.json")
        Save-Summary
    }
}

function Invoke-Wgse {
    param([string]$Name, [string[]]$Arguments, [switch]$ExpectedFailure, [string[]]$Outputs = @())
    Invoke-Step $Name (@($Pixi, 'run', '--frozen', '--no-install', 'wgsextract', '--config', $Config) + $Arguments) `
        -ExpectedFailure:$ExpectedFailure -Outputs $Outputs
}

# Shared with the offline tiny-genome smoke runner: these are the real qualification commands.
function Invoke-GenomeQualification([string]$Part, [string]$InputBam, [string]$Ref, [string]$Work) {
    Invoke-Wgse "${Part}_realign" @('realign', '--input', $InputBam, '--ref', $Ref, '--aligner', 'bwa', '--format', 'BAM',
        '--outdir', "$Work/realign", '--threads', "$Threads") -Outputs @("$Work/realign/*_aligned.bam")
    $Aligned = (Get-Item "$Work/realign/*_aligned.bam").FullName
    Invoke-Step "${Part}_aligned_records" @($Sam, 'idxstats', $Aligned) -CountKind bam
    # HG002 is known Male; chr22-only coverage cannot infer sex. Apply the same declaration to both phases.
    Invoke-Wgse "${Part}_sex" @('sample', 'set', '--input', $Aligned, '--sex', 'Male')
    foreach ($Kind in @('snp', 'indel')) {
        Invoke-Wgse "${Part}_$Kind" @('vcf', $Kind, '--input', $Aligned, '--ref', $Ref, '--ploidy', 'GRCh38',
            '--outdir', "$Work/$Kind", '--threads', "$Threads") -Outputs @("$Work/$Kind/*.vcf.gz")
        $Vcfs = @(Get-Item "$Work/$Kind/*.vcf.gz")
        if ($Vcfs.Count -ne 1) { throw "Expected one $Kind VCF, found $($Vcfs.Count)." }
        Invoke-Step "${Part}_${Kind}_stats" @($Bcf, 'stats', $Vcfs[0].FullName) -CountKind vcf
        Invoke-Step "${Part}_${Kind}_q30dp10" @($Bcf, 'stats', '-i', 'QUAL>=30 && INFO/DP>=10', $Vcfs[0].FullName) -CountKind vcf
    }
}
