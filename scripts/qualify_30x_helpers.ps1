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
        $Rows | Select-Object step, status, exit_code, seconds, peak_tree_ws_gib, peak_system_used_gib, output_records |
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
        ''
        '| Step | Status | Exit | Wall seconds | Peak tree WS GiB | Peak system GiB | Records |'
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |'
    )
    foreach ($Row in $Rows) {
        $Table += "| $($Row.step) | $($Row.status) | $($Row.exit_code) | $($Row.seconds) | $($Row.peak_tree_ws_gib) | $($Row.peak_system_used_gib) | $($Row.output_records) |"
    }
    $Table | Set-Content (Join-Path $Evidence 'summary.md')
    # Only this dedicated evidence directory is archived, never the genome outputs.
    Compress-Archive -Path "$Evidence/*" -DestinationPath (Join-Path $Root 'evidence.zip') -Force
}

function Invoke-Step {
    param(
        [string]$Name, [string[]]$Command, [string[]]$Outputs = @(),
        [switch]$ExpectedFailure, [ValidateSet('', 'bam', 'vcf')][string]$CountKind = ''
    )
    if (Test-Complete $Name) { Write-Host "SKIP $Name (completed)"; return }
    $StdoutPath = Join-Path $Evidence "$Name.stdout.log"
    $StderrPath = Join-Path $Evidence "$Name.stderr.log"
    $Info = [Diagnostics.ProcessStartInfo]::new()
    $Info.FileName = $Command[0]
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
        status = 'failed'; exit_code = $null; seconds = 0
        peak_tree_ws_gib = 0; peak_system_used_gib = 0; output_records = $null
        outputs = @(); command = $Command
    }
    $Started = $false
    Write-Host "START $Name (logs: $StdoutPath / $StderrPath)"
    try {
        $Started = $Process.Start()
        if (-not $Started) { throw "Could not start $Name" }
        $OutTask = $Process.StandardOutput.BaseStream.CopyToAsync($OutStream)
        $ErrTask = $Process.StandardError.BaseStream.CopyToAsync($ErrStream)
        $Ids = [Collections.Generic.HashSet[int]]::new()
        [void]$Ids.Add($Process.Id)
        do {
            $Processes = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, WorkingSetSize)
            do {
                $Added = $false
                foreach ($Child in $Processes) {
                    if ($Ids.Contains([int]$Child.ParentProcessId) -and $Ids.Add([int]$Child.ProcessId)) { $Added = $true }
                }
            } while ($Added)
            $Used = ($Processes | Where-Object { $Ids.Contains([int]$_.ProcessId) } | Measure-Object WorkingSetSize -Sum).Sum / 1GB
            $Memory = Get-CimInstance Win32_OperatingSystem -Property TotalVisibleMemorySize, FreePhysicalMemory
            $Row.peak_tree_ws_gib = [math]::Max($Row.peak_tree_ws_gib, $Used)
            $Row.peak_system_used_gib = [math]::Max($Row.peak_system_used_gib, ($Memory.TotalVisibleMemorySize - $Memory.FreePhysicalMemory) / 1MB)
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
        if ($Started -and -not $Process.HasExited) { $Process.Kill($true); $Process.WaitForExit() }
        if ($Started) {
            [void]$OutTask.GetAwaiter().GetResult()
            [void]$ErrTask.GetAwaiter().GetResult()
        }
        $OutStream.Dispose()
        $ErrStream.Dispose()
        $Process.Dispose()
        $Row.seconds = [math]::Round($Watch.Elapsed.TotalSeconds, 3)
        $Row.peak_tree_ws_gib = [math]::Round($Row.peak_tree_ws_gib, 3)
        $Row.peak_system_used_gib = [math]::Round($Row.peak_system_used_gib, 3)
        $Row | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Evidence "$Name.step.json")
        Save-Summary
    }
}

function Invoke-Wgse {
    param([string]$Name, [string[]]$Arguments, [switch]$ExpectedFailure, [string[]]$Outputs = @())
    Invoke-Step $Name (@($Pixi, 'run', '--frozen', '--no-install', 'wgsextract', '--config', $Config) + $Arguments) `
        -ExpectedFailure:$ExpectedFailure -Outputs $Outputs
}
