$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoUrl = if ($env:WGSEXTRACT_REPO_URL) { $env:WGSEXTRACT_REPO_URL } else { "https://github.com/WGSExtract/wgsextract-cli" }
$scriptDir = Split-Path -Parent $PSCommandPath
$scriptsRoot = Split-Path -Parent $scriptDir
$defaultReleaseTagFile = if ($env:WGSEXTRACT_RELEASE_TAG_FILE) { $env:WGSEXTRACT_RELEASE_TAG_FILE } else { Join-Path $scriptsRoot "wgsextract-release-tag.txt" }
$defaultReleaseTag = if ($env:WGSEXTRACT_DEFAULT_RELEASE_TAG) {
    $env:WGSEXTRACT_DEFAULT_RELEASE_TAG
} elseif (Test-Path -LiteralPath $defaultReleaseTagFile -PathType Leaf) {
    $tag = Get-Content -LiteralPath $defaultReleaseTagFile -TotalCount 1
    if ($tag) { $tag.Trim() } else { "" }
} else {
    ""
}
if (-not $defaultReleaseTag) { $defaultReleaseTag = "latest" }
$requestedRef = if ($env:WGSEXTRACT_REF) { $env:WGSEXTRACT_REF } elseif ($env:WGSEXTRACT_RELEASE_TAG) { $env:WGSEXTRACT_RELEASE_TAG } else { $defaultReleaseTag }
$bundleRoot = if ($env:WGSEXTRACT_GUI_BUNDLE_WORKSPACE) { $env:WGSEXTRACT_GUI_BUNDLE_WORKSPACE } else { Split-Path -Parent $scriptsRoot }
# The runtime goes in the app's data folder, apart from the user's files in the workspace. Apps up to
# 0.8.6 installed it in the workspace ($legacyRuntime).
$legacyRuntime = Join-Path $bundleRoot "runtime\wgsextract-cli"
$installDir = if ($env:WGSEXTRACT_INSTALL_DIR) {
    $env:WGSEXTRACT_INSTALL_DIR
} elseif ($env:WGSEXTRACT_GUI_APP_DATA) {
    Join-Path $env:WGSEXTRACT_GUI_APP_DATA "runtime\wgsextract-cli"
} else {
    $legacyRuntime
}
$movesLegacyRuntime = [System.IO.Path]::GetFullPath($legacyRuntime).TrimEnd("\") -ne [System.IO.Path]::GetFullPath($installDir).TrimEnd("\")
$appDir = Join-Path $installDir "app"
$binDir = Join-Path $installDir "bin"

. (Join-Path $scriptDir "wgsextract-msys2.ps1")
. (Join-Path $scriptDir "setup-transaction.ps1")
$msys2Root = Get-WGSExtractMsys2Root

function Test-MsysInstall {
    param([Parameter(Mandatory = $true)][string]$Root)
    return (Test-Path -LiteralPath (Join-Path $Root "usr\bin\pacman.exe") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Root "usr\bin\bash.exe") -PathType Leaf)
}

function Find-Pixi {
    if ($env:PIXI -and (Test-Path -LiteralPath $env:PIXI -PathType Leaf)) { return $env:PIXI }
    $command = Get-Command pixi -ErrorAction SilentlyContinue
    if ($command -and $command.Path) { return $command.Path }
    if ($command -and $command.Source) { return $command.Source }
    # Pixi's home: PIXI_HOME (Pixi's own setting), then ~\.pixi.
    foreach ($pixiHome in @($env:PIXI_HOME, (Join-Path $HOME ".pixi"))) {
        if (-not $pixiHome) { continue }
        $homePixi = Join-Path $pixiHome "bin\pixi.exe"
        if (Test-Path -LiteralPath $homePixi -PathType Leaf) { return $homePixi }
    }
    return $null
}

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($LiteralPath, $Value, $utf8NoBom)
}

function Get-SourceReleaseTag {
    param([string]$AppDir)

    $tagFile = Join-Path $AppDir ".wgsextract-release-tag"
    if (Test-Path -LiteralPath $tagFile -PathType Leaf) {
        return ([string](Get-Content -LiteralPath $tagFile -Raw)).Trim()
    }

    # Released versions through 0.8.10 did not record the tag in the source tree.
    $manifest = Join-Path $AppDir "pixi.toml"
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { return }
    $workspace = $false
    foreach ($line in Get-Content -LiteralPath $manifest) {
        if ($line -match '^\s*\[') {
            $workspace = $line -match '^\s*\[(workspace|project)\]\s*(?:#.*)?$'
        } elseif ($workspace -and $line -match '^\s*version\s*=\s*["'']([^"'']+)["'']\s*(?:#.*)?$') {
            return "v$($Matches[1])"
        }
    }
}

function Restore-AppSource {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$AppDir,
        [Parameter(Mandatory = $true)][string]$ReleaseTag,
        [Parameter(Mandatory = $true)][string]$Journal,
        [Parameter(Mandatory = $true)][hashtable]$Transaction
    )

    if ($ReleaseTag -cne "custom" -and
        (Test-Path -LiteralPath (Join-Path $AppDir "install_windows.bat") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $AppDir "pixi.toml") -PathType Leaf) -and
        (Get-SourceReleaseTag -AppDir $AppDir) -ceq $ReleaseTag) {
        return $false
    }

    # The .zip, as the CLI's own Windows installer uses: the release has
    # symlinks, which tar.exe can't create without Developer Mode ("Invalid
    # argument") and ZIP extraction writes as small files. Stage beside app so
    # publishing is a rename. Use the .NET Framework's two-argument API: an inherited PSModulePath
    # can load PowerShell 7's Archive module into Windows PowerShell 5.1, where
    # its three-argument ExtractToDirectory overload doesn't exist.
    $staging = $Transaction.Staging
    $previousAppDir = Join-Path $Transaction.WorkDir "previous-app"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Archive, $staging)
        $top = @(Get-ChildItem -LiteralPath $staging -Directory)
        if ($top.Count -ne 1) {
            throw "Downloaded archive did not contain a single source directory."
        }
        foreach ($required in @("install_windows.bat", "pixi.toml")) {
            if (-not (Test-Path -LiteralPath (Join-Path $top[0].FullName $required) -PathType Leaf)) {
                throw "Downloaded archive did not contain $required."
            }
        }
        Save-AppSetupTransaction $Journal $Transaction
        if ($Transaction.HadApp) { Move-AppDirectory $AppDir $previousAppDir }
        Move-AppDirectory $top[0].FullName $AppDir
        return $true
    }
    finally {
        try { Remove-DirectoryWithRetry -Path $staging }
        catch { Write-Warning "Could not remove source staging directory ${staging}: $_" }
    }
}

# Pre-state snapshot so the manifest records who owns each prerequisite.
$msys2PreInstalled = Test-MsysInstall -Root $msys2Root
$pixiPreInstalled = ($null -ne (Find-Pixi))
# Where install_windows.bat installs Pixi. A PIXI_HOME that already holds
# anything may be shared with other files, so it is never the app's to remove.
$pixiHomeBefore = if ($env:PIXI_HOME) { $env:PIXI_HOME } else { Join-Path $HOME ".pixi" }
$pixiHomeExisted = (Test-Path -LiteralPath $pixiHomeBefore) -and ($null -ne (Get-ChildItem -LiteralPath $pixiHomeBefore -Force -ErrorAction SilentlyContinue | Select-Object -First 1))

$ref = $requestedRef
if ($env:WGSEXTRACT_ARCHIVE_URL) {
    if (-not ($env:WGSEXTRACT_REF -or $env:WGSEXTRACT_RELEASE_TAG)) { $ref = "custom" }
    $archiveUrls = @($env:WGSEXTRACT_ARCHIVE_URL)
} else {
    if ($requestedRef -eq "latest" -or -not $requestedRef) {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$repoUrl/releases/latest" -MaximumRedirection 0 -ErrorAction SilentlyContinue
        $ref = if ($response.Headers.Location) { Split-Path $response.Headers.Location -Leaf } else { "main" }
    } else {
        $ref = $requestedRef
    }
    # Build a fallback list. github.com/.../archive/$ref.zip is the
    # canonical URL but it intermittently returns 504 from the github.com
    # edge while the underlying codeload CDN remains healthy. Always include
    # codeload as a fallback so flaky edge outages don't break setup.
    $archiveUrls = @("$repoUrl/archive/$ref.zip")
    if ($repoUrl -match '^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$') {
        $owner = $Matches[1]
        $repo = $Matches[2]
        if ($ref -like 'v*' -or $ref -match '^\d') {
            $archiveUrls += "https://codeload.github.com/$owner/$repo/zip/refs/tags/$ref"
        }
        $archiveUrls += "https://codeload.github.com/$owner/$repo/zip/refs/heads/$ref"
        $archiveUrls += "https://codeload.github.com/$owner/$repo/zip/$ref"
    }
}

New-Item -ItemType Directory -Force -Path $installDir, (Join-Path $installDir "tmp"), $binDir | Out-Null
$journal = Join-Path $installDir ".setup-transaction.json"
$lock = Open-AppSetupLock $installDir
$workDir = Join-Path (Join-Path $installDir "tmp") ("install." + [guid]::NewGuid().ToString("N"))
$archive = Join-Path $workDir "wgsextract-cli.zip"
$previousAppDir = Join-Path $workDir "previous-app"
$canClean = $false

try {
    Complete-AppSetupTransaction $journal $appDir
    $canClean = $true
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null
    $transaction = @{
        WorkDir = $workDir
        Staging = "$appDir." + [guid]::NewGuid().ToString("N").Substring(0, 8)
        HadApp = Test-Path -LiteralPath $appDir
        Environments = @()
    }
    $downloadAttempts = 5
    $downloaded = $false
    foreach ($currentUrl in $archiveUrls) {
        Write-Host "Downloading WGS Extract CLI from $currentUrl"
        for ($attempt = 1; $attempt -le $downloadAttempts; $attempt += 1) {
            try {
                if ([IO.File]::Exists($currentUrl)) {
                    Copy-Item -LiteralPath $currentUrl -Destination $archive
                } else {
                    Invoke-WebRequest -UseBasicParsing -Uri $currentUrl -OutFile $archive -TimeoutSec 120
                }
                $downloaded = $true
                break
            }
            catch {
                $message = $_.Exception.Message
                if ($attempt -eq $downloadAttempts) {
                    Write-Host "All $downloadAttempts attempts failed for $currentUrl ($message). Trying next fallback URL if any..."
                    break
                }
                $delay = [int][math]::Min(60, [math]::Pow(2, $attempt))
                Write-Host "Download attempt $attempt/$downloadAttempts failed ($message). Retrying in $delay s..."
                Start-Sleep -Seconds $delay
            }
        }
        if ($downloaded) { break }
    }
    if (-not $downloaded) {
        throw "Failed to download WGS Extract CLI from any of: $($archiveUrls -join ', ')"
    }
    $sourceReplaced = Restore-AppSource -Archive $archive -AppDir $appDir -ReleaseTag $ref -Journal $journal -Transaction $transaction
    $installedRef = if ($ref -ceq "custom") {
        $sourceTag = Get-SourceReleaseTag -AppDir $appDir
        if ($sourceTag) { $sourceTag } else { $ref }
    } else { $ref }

    $installBat = Join-Path $appDir "install_windows.bat"
    if (-not (Test-Path -LiteralPath $installBat -PathType Leaf)) {
        Write-Error "Upstream install_windows.bat not found at $installBat. Tarball layout changed?"
        exit 1
    }

    # Redirect Pixi cache+envs to a short machine-local path to avoid Windows MAX_PATH
    # (260) issues when nested under the workspace. Tracked in install-manifest so the
    # uninstaller removes them.
    $pixiBaseDir = if ($env:WGSEXTRACT_PIXI_BASE_DIR) { $env:WGSEXTRACT_PIXI_BASE_DIR } else { Join-Path $env:LOCALAPPDATA "WGSExtractPixi" }
    $pixiCacheDirNew = Join-Path $pixiBaseDir "cache"
    $pixiEnvsDirNew = Join-Path $pixiBaseDir "envs"
    New-Item -ItemType Directory -Force -Path $pixiCacheDirNew, $pixiEnvsDirNew | Out-Null
    $env:PIXI_CACHE_DIR = $pixiCacheDirNew
    $env:PIXI_PROJECT_ENVIRONMENT_DIR = $pixiEnvsDirNew
    if ($sourceReplaced -and (Test-Path -LiteralPath $previousAppDir)) {
        $previousProjects = @(Get-AppPixiProjects $pixiEnvsDirNew $appDir)
        if ($previousProjects.Count) {
            # Install at the final path: Pixi environments contain absolute prefixes.
            $transaction.EnvironmentBackupDir = Join-Path $pixiEnvsDirNew ("old-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
            $transaction.Environments = @(foreach ($project in $previousProjects) {
                @{ Path = $project.FullName; Backup = (Join-Path $transaction.EnvironmentBackupDir $project.Name) }
            })
            Save-AppSetupTransaction $journal $transaction
            New-Item -ItemType Directory -Path $transaction.EnvironmentBackupDir | Out-Null
            foreach ($project in $transaction.Environments) {
                Move-AppDirectory $project.Path $project.Backup
            }
        }
    }

    # CLI releases before native Windows tools (0.6.x and older) install MSYS2
    # and its pacman tools; newer ones take every tool from Pixi and need none.
    $usesMsys2 = Test-Path -LiteralPath (Join-Path $appDir "scripts\setup_pacman_runtime.ps1") -PathType Leaf
    if ($usesMsys2) {
        # The app's own folder for MSYS2 when %LOCALAPPDATA% can't hold it.
        $userFolder = Get-WGSExtractUserFolder
        $msys2InUserFolder = $msys2Root.StartsWith("$userFolder\", [StringComparison]::OrdinalIgnoreCase)
        if ($msys2InUserFolder) {
            New-WGSExtractUserFolder | Out-Null
        }
        $env:MSYS2_ROOT = $msys2Root
    } else {
        $msys2InUserFolder = $false
    }
    # install_windows.bat saves its runtime defaults in the user's own CLI config
    # (platformdirs' user_config_dir("wgsextract", "theontho")). The app's commands
    # use the bundle's config and find MSYS2 through MSYS2_ROOT, so that file is put
    # back as it was: a user's own CLI settings stay theirs, and none is left behind.
    $userCliConfig = Join-Path $env:LOCALAPPDATA "theontho\wgsextract\config.toml"
    $userCliConfigBefore = if (Test-Path -LiteralPath $userCliConfig -PathType Leaf) { [System.IO.File]::ReadAllBytes($userCliConfig) } else { $null }

    Write-Host "Delegating to upstream install_windows.bat (installs Pixi as needed and the CLI's tools)..."
    Push-Location $appDir
    try {
        if ($sourceReplaced) {
            $transaction.InstallerStarted = $true
            $transaction.EnvsDir = $pixiEnvsDirNew
            Save-AppSetupTransaction $journal $transaction
        }
        & $env:ComSpec /d /c "call `"$installBat`""
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
        if ($null -ne $userCliConfigBefore) {
            [System.IO.File]::WriteAllBytes($userCliConfig, $userCliConfigBefore)
        } elseif (Test-Path -LiteralPath $userCliConfig) {
            Remove-Item -LiteralPath $userCliConfig -Force
            foreach ($dir in (Split-Path -Parent $userCliConfig), (Join-Path $env:LOCALAPPDATA "theontho")) {
                if ((Test-Path -LiteralPath $dir) -and -not (Get-ChildItem -LiteralPath $dir -Force)) {
                    Remove-Item -LiteralPath $dir -Force
                }
            }
        }
    }

    if ($usesMsys2 -and -not (Test-MsysInstall -Root $msys2Root)) {
        Write-Error "MSYS2 was not present at $msys2Root after install_windows.bat completed."
        exit 1
    }
    $pixi = Find-Pixi
    if (-not $pixi) {
        Write-Error "Pixi was not found on PATH or in ~\.pixi after install_windows.bat completed."
        exit 1
    }

    $shimPath = Join-Path $binDir "wgsextract.cmd"
    $runScript = Join-Path $scriptDir "run-wgsextract.ps1"
    Write-Utf8File -LiteralPath $shimPath -Value "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$runScript"" %*`r`n"

    Push-Location $appDir
    $previousConsoleEncoding = [Console]::OutputEncoding
    $previousOutputEncoding = $OutputEncoding
    try {
        # Capture before activation. In an already activated shell, Pixi's
        # hook omits values it didn't need to change.
        $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        [Console]::OutputEncoding = $OutputEncoding
        $activationPath = Join-Path $appDir ".pixi\wgsextract-activation.json"
        Remove-Item -LiteralPath $activationPath -ErrorAction SilentlyContinue
        $activation = & $pixi shell-hook --json --frozen | Out-String
        if ($LASTEXITCODE -ne 0) { throw "Pixi activation failed" }
        $prefix = ($activation | ConvertFrom-Json).environment_variables.CONDA_PREFIX
        if (-not $prefix) { throw "Pixi did not activate a Python environment" }
        $python = Join-Path $prefix "python.exe"
        $activation | & $python -X utf8 (Join-Path $scriptDir "..\installed_cli.py") capture $activationPath
        if ($LASTEXITCODE -ne 0) {
            throw "Could not save activation"
        }
    } catch {
        Write-Warning "Could not cache CLI activation; commands will continue to run through Pixi. $_"
    } finally {
        [Console]::OutputEncoding = $previousConsoleEncoding
        $OutputEncoding = $previousOutputEncoding
        Pop-Location
    }

    $manifestItems = New-Object System.Collections.Generic.List[object]
    if (-not $pixiPreInstalled) {
        $pixiBinDir = Split-Path -Parent $pixi
        $pixiInstallRoot = Split-Path -Parent $pixiBinDir
        if ($pixiInstallRoot -and -not $pixiHomeExisted) {
            $manifestItems.Add(@{ type = "directory"; path = $pixiInstallRoot; ownedBy = "install_windows.bat" })
        }
        if ($pixiBinDir) {
            $manifestItems.Add(@{ type = "userPathEntry"; path = $pixiBinDir; ownedBy = "install_windows.bat" })
        }
    }
    if ($usesMsys2 -and -not $msys2PreInstalled) {
        $manifestItems.Add(@{ type = "msys2Install"; root = $msys2Root; ownedBy = "install_windows.bat" })
    }
    if ($msys2InUserFolder) {
        # Only the app makes this folder; it and, once no other user's is left, its parent go.
        $manifestItems.Add(@{ type = "directory"; path = $userFolder; ownedBy = "setup-wgsextract-pixi.ps1" })
        $manifestItems.Add(@{ type = "directoryIfEmpty"; path = (Split-Path -Parent $userFolder); ownedBy = "setup-wgsextract-pixi.ps1" })
    }
    if (Test-Path -LiteralPath $pixiBaseDir) {
        $manifestItems.Add(@{ type = "directory"; path = $pixiBaseDir; ownedBy = "setup-wgsextract-pixi.ps1" })
    }
    # A rerun of setup finds what the first run installed already there; it is
    # still the app's to remove. So is what an earlier version's setup recorded
    # in the workspace's runtime.
    $manifestPath = Join-Path $installDir "install-manifest.json"
    $previousManifests = @($manifestPath)
    if ($movesLegacyRuntime) { $previousManifests += Join-Path $legacyRuntime "install-manifest.json" }
    foreach ($previousManifest in $previousManifests | Where-Object { Test-Path -LiteralPath $_ }) {
        $previous = @((Get-Content -LiteralPath $previousManifest -Raw -Encoding UTF8 | ConvertFrom-Json).items)
        foreach ($item in $previous) {
            $key = "$($item.type)|$($item.path)|$($item.root)"
            if (-not ($manifestItems | Where-Object { "$($_.type)|$($_.path)|$($_.root)" -eq $key })) {
                $copy = @{}
                foreach ($property in $item.PSObject.Properties) { $copy[$property.Name] = $property.Value }
                $manifestItems.Add($copy)
            }
        }
    }

    # Uninstall goes in this order: MSYS2 (with its own uninstaller) before the
    # folders that hold it.
    $order = @("msys2Package", "msys2Install", "userPathEntry", "directory", "directoryIfEmpty")
    $manifest = @{
        format = 1
        createdAt = (Get-Date).ToUniversalTime().ToString("o")
        wgsextractRef = $installedRef
        items = @(foreach ($type in $order) { $manifestItems | Where-Object { $_.type -eq $type } })
    }
    $stagedManifest = Join-Path $workDir "install-manifest.json"
    Set-Content -LiteralPath $stagedManifest -Encoding UTF8 -Value ($manifest | ConvertTo-Json -Depth 5)
    $hadManifest = [IO.File]::Exists($manifestPath)
    if ($sourceReplaced) {
        $transaction.PublishingManifest = $true
        $transaction.HadManifest = $hadManifest
        Save-AppSetupTransaction $journal $transaction
    }
    if ($hadManifest) {
        [IO.File]::Replace($stagedManifest, $manifestPath, (Join-Path $workDir "previous-manifest.json"))
    } else {
        [IO.File]::Move($stagedManifest, $manifestPath)
    }
    Write-Utf8File -LiteralPath (Join-Path $appDir ".wgsextract-release-tag") -Value "$installedRef`n"
    if ($sourceReplaced) {
        $transaction.Committed = $true
        Save-AppSetupTransaction $journal $transaction
    }

    # The runtime an earlier version installed in the workspace is unused now; what it recorded is in the
    # new manifest. Its Pixi environments were never in it (they are in $pixiBaseDir).
    if ($movesLegacyRuntime -and (Test-Path -LiteralPath $legacyRuntime)) {
        Write-Host "Removing the runtime an earlier version installed in the workspace: $legacyRuntime"
        # Its Pixi environment is in $pixiEnvsDirNew, recorded as installed for its pixi.toml; this
        # runtime's has a new one there.
        foreach ($project in Get-AppPixiProjects $pixiEnvsDirNew (Join-Path $legacyRuntime "app")) {
            try {
                Remove-Item -LiteralPath $project.FullName -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Warning "Could not check or remove the Pixi environment in $($project.FullName): $($_.Exception.Message)"
            }
        }
        try {
            Remove-Item -LiteralPath $legacyRuntime -Recurse -Force -ErrorAction Stop
            $legacyParent = Split-Path -Parent $legacyRuntime
            if (-not (Get-ChildItem -LiteralPath $legacyParent -Force | Select-Object -First 1)) {
                Remove-Item -LiteralPath $legacyParent -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Warning "Could not remove all of ${legacyRuntime}: $($_.Exception.Message). It is no longer used and can be deleted."
        }
    }

    Write-Host "WGS Extract CLI is installed in $installDir"
    exit 0
} finally {
    try {
        if ($canClean) {
            Complete-AppSetupTransaction $journal $appDir
            Remove-DirectoryWithRetry $workDir
        }
    } catch {
        Write-Warning "Could not finish Setup cleanup; recovery state is at ${journal}: $_"
    } finally {
        $lock.Dispose()
    }
}
