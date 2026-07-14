param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"

if (-not (Test-Path $venv)) {
    & $Python -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$pythonExe = Join-Path $venv "Scripts\python.exe"
& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonExe -m pip install --require-hashes -r (Join-Path $root "requirements.lock")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonExe -m pip install --no-deps -e $root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Environment ready: $venv"
