# Builds dist\htnwalletgui.exe from source: installs the pinned dependencies into a
# private .venv folder, runs the tests, then builds. Nothing is installed system-wide.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Invoke-Checked "py" @("-3.13", "-m", "venv", ".venv")
}
$python = ".venv\Scripts\python.exe"

# --require-hashes refuses any package that differs from what was pinned.
Invoke-Checked $python @("-m", "pip", "install", "--quiet", "--require-hashes", "-r", "requirements.txt", "-r", "requirements-dev.txt")
Invoke-Checked $python @("-m", "pytest", "-q")
Invoke-Checked $python @("-m", "PyInstaller", "--noconfirm", "--clean", "packaging\guiwallet.spec")

Write-Host "Built dist\htnwalletgui.exe"
