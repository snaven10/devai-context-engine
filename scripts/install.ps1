#Requires -Version 5.1
<#
.SYNOPSIS
    DevAI Installer for Windows.
.DESCRIPTION
    Downloads precompiled Go binary + portable Python, creates venv, installs deps.
    No Go or Python required on the host system.
.PARAMETER Gpu
    Install PyTorch with CUDA support instead of CPU-only.
.PARAMETER Version
    Install a specific release version (default: latest).
.PARAMETER Uninstall
    Remove DevAI and all its files.
#>
[CmdletBinding()]
param(
    [switch]$Gpu,
    [string]$Version,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$Repo = "snaven10/devai-context-engine"
$GitHubApi = "https://api.github.com/repos/$Repo/releases"
$PythonStandaloneRepo = "astral-sh/python-build-standalone"
$PythonVersion = "3.12"

$InstallDir = Join-Path $env:LOCALAPPDATA "devai"
$BinDir = Join-Path $InstallDir "bin"
$PythonDir = Join-Path $InstallDir "python"
$VenvDir = Join-Path $PythonDir "venv"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Info    { param([string]$Msg) Write-Host "[INFO] " -ForegroundColor Blue -NoNewline; Write-Host $Msg }
function Write-Ok      { param([string]$Msg) Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $Msg }
function Write-Warn    { param([string]$Msg) Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline; Write-Host $Msg }
function Write-Err     { param([string]$Msg) Write-Host "[ERROR] " -ForegroundColor Red -NoNewline; Write-Host $Msg }
function Write-Step    { param([string]$Msg) Write-Host "`n> $Msg" -ForegroundColor Cyan }

function Invoke-Download {
    param(
        [string]$Url,
        [string]$Dest
    )
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -MaximumRetryCount 3 -RetryIntervalSec 2
    } catch {
        throw "Download failed: $Url -> $_"
    }
}

function Get-GitHubJson {
    param([string]$Url)
    $ProgressPreference = 'SilentlyContinue'
    try {
        $response = Invoke-RestMethod -Uri $Url -UseBasicParsing -MaximumRetryCount 3 -RetryIntervalSec 2
        return $response
    } catch {
        throw "Failed to fetch: $Url -> $_"
    }
}

# ── Uninstall ─────────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step "Uninstalling DevAI"

    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Ok "Removed $InstallDir"
    } else {
        Write-Warn "Nothing to remove - $InstallDir does not exist."
    }

    # Remove from User PATH
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -and $userPath.Contains($BinDir)) {
        $newPath = ($userPath.Split(';') | Where-Object { $_ -ne $BinDir }) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Ok "Removed $BinDir from User PATH"
    }

    Write-Host "`nDevAI has been uninstalled." -ForegroundColor Green
    exit 0
}

# ── Detect Architecture ──────────────────────────────────────────────────────
function Get-Platform {
    $arch = if ([Environment]::Is64BitOperatingSystem) {
        $cpuArch = (Get-CimInstance Win32_Processor).Architecture
        # Architecture: 0=x86, 5=ARM, 9=x64, 12=ARM64
        switch ($cpuArch) {
            9     { "amd64" }
            12    { "arm64" }
            default { "amd64" }
        }
    } else {
        throw "32-bit systems are not supported."
    }

    return @{
        Os   = "windows"
        Arch = $arch
        PythonArch = if ($arch -eq "amd64") { "x86_64" } else { "aarch64" }
    }
}

# ── Main Install ─────────────────────────────────────────────────────────────
Write-Host "DevAI Installer" -ForegroundColor White

$platform = Get-Platform
Write-Info "Detected: OS=windows ARCH=$($platform.Arch)"

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "devai-install-$([guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
Write-Info "Working in $tmpDir"

try {
    # ── Resolve Release ───────────────────────────────────────────────────
    Write-Step "Resolving DevAI release"

    if ($Version) {
        $releaseTag = $Version
        Write-Info "Using specified version: $releaseTag"
    } else {
        Write-Info "Fetching latest release from GitHub..."
        $release = Get-GitHubJson "$GitHubApi/latest"
        $releaseTag = $release.tag_name
        if (-not $releaseTag) {
            throw "Could not determine latest release tag."
        }
        Write-Info "Latest release: $releaseTag"
    }

    # ── Download & Install Binary ─────────────────────────────────────────
    Write-Step "Installing DevAI binary"

    $archiveName = "devai_windows_$($platform.Arch).tar.gz"
    $downloadUrl = "https://github.com/$Repo/releases/download/$releaseTag/$archiveName"
    $archivePath = Join-Path $tmpDir $archiveName

    Write-Info "Downloading $archiveName..."
    Invoke-Download -Url $downloadUrl -Dest $archivePath

    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null

    # Extract tar.gz — PowerShell 5.1+ with tar (Windows 10 1803+)
    tar -xzf $archivePath -C $tmpDir
    $binary = Get-ChildItem -Path $tmpDir -Recurse -Filter "devai.exe" | Select-Object -First 1
    if (-not $binary) {
        # Try without .exe (might be renamed during extraction)
        $binary = Get-ChildItem -Path $tmpDir -Recurse -Filter "devai" -File | Select-Object -First 1
        if ($binary) {
            Copy-Item $binary.FullName (Join-Path $BinDir "devai.exe")
        } else {
            throw "Could not find devai binary in archive."
        }
    } else {
        Copy-Item $binary.FullName (Join-Path $BinDir "devai.exe")
    }

    Write-Ok "Installed devai binary to $BinDir\devai.exe"

    # ── Download & Extract Portable Python ────────────────────────────────
    Write-Step "Installing portable Python $PythonVersion"

    $pythonExe = Join-Path $PythonDir "python.exe"
    $skipPython = $false

    if (Test-Path $pythonExe) {
        $existingVer = & $pythonExe --version 2>&1
        if ($existingVer -match $PythonVersion) {
            Write-Ok "Portable Python $PythonVersion already installed - skipping."
            $skipPython = $true
        }
    }

    if (-not $skipPython) {
        Write-Info "Fetching latest python-build-standalone release..."
        $pyRelease = Get-GitHubJson "https://api.github.com/repos/$PythonStandaloneRepo/releases/latest"

        $pyAsset = $pyRelease.assets | Where-Object {
            $_.name -match "cpython-$PythonVersion" -and
            $_.name -match "$($platform.PythonArch)-pc-windows-msvc" -and
            $_.name -match "install_only_stripped" -and
            $_.name -notmatch "debug"
        } | Select-Object -First 1

        # Fallback to install_only (non-stripped)
        if (-not $pyAsset) {
            $pyAsset = $pyRelease.assets | Where-Object {
                $_.name -match "cpython-$PythonVersion" -and
                $_.name -match "$($platform.PythonArch)-pc-windows-msvc" -and
                $_.name -match "install_only" -and
                $_.name -notmatch "debug"
            } | Select-Object -First 1
        }

        if (-not $pyAsset) {
            throw "Could not find Python $PythonVersion build for Windows $($platform.PythonArch)"
        }

        $pyArchivePath = Join-Path $tmpDir $pyAsset.name
        Write-Info "Downloading $($pyAsset.name)..."
        Invoke-Download -Url $pyAsset.browser_download_url -Dest $pyArchivePath

        New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null

        # Extract — strip the top-level "python/" directory
        $pyTmpExtract = Join-Path $tmpDir "python-extract"
        New-Item -ItemType Directory -Path $pyTmpExtract -Force | Out-Null
        tar -xzf $pyArchivePath -C $pyTmpExtract

        $pyExtracted = Get-ChildItem -Path $pyTmpExtract -Directory | Select-Object -First 1
        if ($pyExtracted) {
            Copy-Item -Path (Join-Path $pyExtracted.FullName "*") -Destination $PythonDir -Recurse -Force
        } else {
            Copy-Item -Path (Join-Path $pyTmpExtract "*") -Destination $PythonDir -Recurse -Force
        }

        if (-not (Test-Path $pythonExe)) {
            throw "Python extraction failed - python.exe not found."
        }

        Write-Ok "Installed portable Python to $PythonDir"
    }

    # ── Create Virtual Environment ────────────────────────────────────────
    Write-Step "Creating Python virtual environment"

    $venvPython = Join-Path $VenvDir "Scripts\python.exe"

    if (Test-Path $venvPython) {
        Write-Ok "Virtual environment already exists - skipping."
    } else {
        & $pythonExe -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
        Write-Ok "Created venv at $VenvDir"
    }

    # ── Install Python Dependencies ───────────────────────────────────────
    Write-Step "Installing Python dependencies"

    $pip = Join-Path $VenvDir "Scripts\pip.exe"

    # Upgrade pip
    & $venvPython -m pip install --upgrade pip --quiet 2>$null

    if ($Gpu) {
        Write-Info "Installing with GPU (CUDA) PyTorch support"
        $reqFile = "requirements-gpu.txt"
    } else {
        Write-Info "Installing with CPU-only PyTorch (use -Gpu for CUDA)"
        $reqFile = "requirements-cpu.txt"
    }

    # Try release assets first, fallback to bundled
    $reqUrl = "https://github.com/$Repo/releases/download/$releaseTag/$reqFile"
    $reqPath = Join-Path $tmpDir $reqFile
    $useRemote = $false

    try {
        Invoke-Download -Url $reqUrl -Dest $reqPath
        $useRemote = $true
    } catch {
        $useRemote = $false
    }

    if ($useRemote) {
        Write-Info "Using requirements from release assets"
        & $pip install -r $reqPath --quiet
    } else {
        Write-Info "Release requirements not found - using bundled list"
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
        $localReq = Join-Path $scriptDir $reqFile

        if (Test-Path $localReq) {
            & $pip install -r $localReq --quiet
        } else {
            throw "Could not find requirements file. Looked in release assets and $localReq"
        }
    }

    if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies." }
    Write-Ok "Python dependencies installed"

    # ── PATH Setup ────────────────────────────────────────────────────────
    Write-Step "Checking PATH"

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -and $userPath.Contains($BinDir)) {
        Write-Ok "$BinDir already in PATH"
    } else {
        $newPath = if ($userPath) { "$BinDir;$userPath" } else { $BinDir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        # Also update current session
        $env:Path = "$BinDir;$env:Path"
        Write-Ok "Added $BinDir to User PATH"
        Write-Warn "Restart your terminal for PATH changes to take effect."
    }

    # ── Summary ───────────────────────────────────────────────────────────
    $devaiVersion = try { & (Join-Path $BinDir "devai.exe") version 2>&1 } catch { $releaseTag }
    $pyVersion = try { & $venvPython --version 2>&1 } catch { "Python $PythonVersion" }
    $torchMode = if ($Gpu) { "GPU (CUDA)" } else { "CPU-only" }

    Write-Host ""
    Write-Host "======================================" -ForegroundColor Green
    Write-Host "   DevAI installed successfully!" -ForegroundColor Green
    Write-Host "======================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Binary:  $BinDir\devai.exe ($devaiVersion)"
    Write-Host "  Python:  $pyVersion"
    Write-Host "  Venv:    $VenvDir"
    Write-Host "  PyTorch: $torchMode"
    Write-Host ""
    Write-Host "  Run " -NoNewline; Write-Host "devai --help" -ForegroundColor Cyan -NoNewline; Write-Host " to get started."
    Write-Host ""

} finally {
    # Cleanup temp directory
    if (Test-Path $tmpDir) {
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    }
}
