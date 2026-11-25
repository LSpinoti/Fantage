# One-time setup on Windows: venv, dependencies, Playwright's Chromium, C++ planner.
# Run from this folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

# --- find Python 3.10+ (the 'py' launcher first; 'python' may be the Microsoft Store stub)
$pyExe = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pyExe = 'py'; $pyArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pyExe = 'python'
}
if ($pyExe) {
    & $pyExe @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) { $pyExe = $null }
}
if (-not $pyExe) {
    Write-Host "Python 3.10+ not found. Install it from https://www.python.org/downloads/"
    Write-Host "(tick 'Add python.exe to PATH'), then run this script again."
    exit 1
}

# --- virtual environment and dependencies
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Invoke-Checked $pyExe ($pyArgs + @('-m', 'venv', '.venv'))
}
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Invoke-Checked $venvPy @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked $venvPy @('-m', 'pip', 'install', '-r', 'requirements.txt')
Invoke-Checked $venvPy @('-m', 'playwright', 'install', 'chromium')

# --- C++ planner (optional: needs a g++ compiler, e.g. MinGW-w64 from https://www.msys2.org/)
& $venvPy -c "import cplanner; print('C++ planner:', 'ready' if cplanner.available() else 'not built - the Python planner will be used (install MinGW-w64 g++ for the faster one)')"

# --- login file, readable only by you
if (-not (Test-Path '.env')) {
    Set-Content -Path '.env' -Value "EMAIL=`r`nPASSWORD=" -Encoding ascii
    Write-Host "Created .env - put your Fantage login in it (notepad .env)."
}
& icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host ""
Write-Host "Run:"
Write-Host "  .venv\Scripts\python stairies_bot.py --headless"
Write-Host "For overnight runs, stop Windows from sleeping while plugged in:"
Write-Host "  Settings > System > Power > Screen and sleep  (or: powercfg /change standby-timeout-ac 0)"
