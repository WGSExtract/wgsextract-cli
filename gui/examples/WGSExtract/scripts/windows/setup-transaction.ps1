function Open-AppSetupLock {
    param([string]$InstallDir)
    try {
        return [IO.File]::Open((Join-Path $InstallDir ".setup.lock"), "OpenOrCreate", "ReadWrite", "None")
    } catch {
        throw "Cannot lock WGS Extract CLI setup in ${InstallDir}. Wait for any running Setup to finish. $_"
    }
}

function Save-AppSetupTransaction {
    param([string]$Journal, [hashtable]$Transaction)

    $next = "$Journal.next"
    [IO.File]::WriteAllText($next, ($Transaction | ConvertTo-Json -Depth 4))
    if ([IO.File]::Exists($Journal)) {
        [IO.File]::Replace($next, $Journal, [NullString]::Value)
    } else {
        [IO.File]::Move($next, $Journal)
    }
}

function Move-AppDirectory {
    param([string]$From, [string]$To)

    # Defender can briefly hold files in a freshly extracted directory.
    for ($i = 1; $i -le 6; $i += 1) {
        try {
            [IO.Directory]::Move($From, $To)
            return
        } catch {
            if ($i -eq 6) { throw }
            Start-Sleep -Milliseconds (500 * $i)
        }
    }
}

function Remove-DirectoryWithRetry {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    for ($i = 1; $i -le 8; $i += 1) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($i -eq 8) { throw }
            Start-Sleep -Milliseconds (500 * $i)
        }
    }
}

function Remove-AppEnvironmentLink {
    param([string]$AppDir)
    # Windows PowerShell must not recursively delete through Pixi's junction.
    $path = Join-Path $AppDir ".pixi\envs"
    $link = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($link -and ($link.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        [IO.Directory]::Delete($path)
    }
}

function Get-AppPixiProjects {
    param([string]$EnvsDir, [string]$AppDir)

    $manifest = [IO.Path]::GetFullPath((Join-Path $AppDir "pixi.toml"))
    foreach ($project in Get-ChildItem -LiteralPath $EnvsDir -Directory -Force -ErrorAction SilentlyContinue) {
        $record = Join-Path $project.FullName "envs\default\conda-meta\pixi"
        if (-not (Test-Path -LiteralPath $record -PathType Leaf)) { continue }
        try {
            $installedFor = (Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json).manifest_path
            if ($installedFor -and [string]::Equals([IO.Path]::GetFullPath($installedFor), $manifest, [StringComparison]::OrdinalIgnoreCase)) {
                $project
            }
        } catch {
            # An unrelated or incomplete environment is not ours to move.
        }
    }
}

function Complete-AppSetupTransaction {
    param([string]$Journal, [string]$AppDir)

    if (-not (Test-Path -LiteralPath $Journal)) { return }
    $transaction = Get-Content -LiteralPath $Journal -Raw -Encoding UTF8 | ConvertFrom-Json
    $previous = Join-Path $transaction.WorkDir "previous-app"
    if (-not $transaction.Committed) {
        if ((Test-Path -LiteralPath $previous) -or -not $transaction.HadApp) {
            Remove-AppEnvironmentLink $AppDir
            Remove-DirectoryWithRetry $AppDir
            if (Test-Path -LiteralPath $previous) { Move-AppDirectory $previous $AppDir }
        }
        $backups = @{}
        foreach ($project in $transaction.Environments) { $backups[$project.Path] = $project.Backup }
        if ($transaction.InstallerStarted) {
            foreach ($project in Get-AppPixiProjects $transaction.EnvsDir $AppDir) {
                if (-not $backups.ContainsKey($project.FullName) -or (Test-Path -LiteralPath $backups[$project.FullName])) {
                    Remove-DirectoryWithRetry $project.FullName
                }
            }
        }
        foreach ($destination in $backups.Keys) {
            if (Test-Path -LiteralPath $backups[$destination]) {
                Remove-DirectoryWithRetry $destination
                Move-AppDirectory $backups[$destination] $destination
            }
        }
        if ($transaction.PublishingManifest) {
            $manifest = Join-Path (Split-Path -Parent $AppDir) "install-manifest.json"
            $previousManifest = Join-Path $transaction.WorkDir "previous-manifest.json"
            if ([IO.File]::Exists($previousManifest)) {
                if ([IO.File]::Exists($manifest)) {
                    [IO.File]::Replace($previousManifest, $manifest, [NullString]::Value)
                } else {
                    [IO.File]::Move($previousManifest, $manifest)
                }
            } elseif (-not $transaction.HadManifest) {
                [IO.File]::Delete($manifest)
            }
        }
    }
    # Keep the journal and backups if recovery fails so the next run can retry.
    Remove-Item -LiteralPath $Journal -Force
    foreach ($dir in @($transaction.WorkDir, $transaction.Staging, $transaction.EnvironmentBackupDir)) {
        if (-not $dir) { continue }
        try {
            if ($dir -eq $transaction.WorkDir) { Remove-AppEnvironmentLink $previous }
            Remove-DirectoryWithRetry $dir
        } catch {
            Write-Warning "Could not remove temporary Setup directory ${dir}: $_"
        }
    }
}
