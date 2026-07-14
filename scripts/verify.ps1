$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Run scripts/bootstrap.ps1 first."
}

Push-Location $root
try {
    & $pythonExe -m ruff check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonExe -m ruff format --check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonExe -m pytest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonExe -m project_copilot.release_guard .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
