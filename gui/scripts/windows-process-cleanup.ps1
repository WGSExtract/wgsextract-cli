function Wait-WindowsProcessCleanup {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$GetProcesses,
        [Parameter(Mandatory = $true)][double]$TimeoutSeconds
    )

    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $reported = $false
    do {
        $remaining = @(& $GetProcesses)
        if ($remaining.Count -eq 0) { return }
        $details = $remaining | ForEach-Object {
            "pid=$($_.ProcessId) parent=$($_.ParentProcessId) name=$($_.Name) command=$($_.CommandLine)"
        }
        if (-not $reported) {
            Write-Host "Waiting for processes after close: $($details -join '; ')"
            $reported = $true
        }
        if ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            throw "Processes remained after close: $($details -join '; ')"
        }
        Start-Sleep -Milliseconds 200
    } while ($true)
}
