param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $root ".venv\Scripts\python.exe"
$lockFile = Join-Path $root "requirements.codex.lock"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Run scripts/bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
    throw "The hash-locked Codex SDK requirements file is missing."
}

& $pythonExe -m pip install --require-hashes -r $lockFile
if ($LASTEXITCODE -ne 0) {
    throw "Official Codex Python SDK installation failed with exit code $LASTEXITCODE."
}

$codexPath = & $pythonExe -c "import codex_cli_bin; print(codex_cli_bin.bundled_codex_path())"
if ($LASTEXITCODE -ne 0 -or -not $codexPath) {
    throw "The official Python SDK was installed but its pinned codex.exe was not found."
}
$codexExe = Get-Item -LiteralPath $codexPath.Trim()
& $codexExe.FullName --version
if ($LASTEXITCODE -ne 0) {
    throw "Installed Codex runtime did not pass its version smoke test."
}

Write-Host "Codex Python SDK runtime ready."
Write-Host "PROJECT_COPILOT_CODEX_BIN=$($codexExe.FullName)"
Write-Host "No API credential was created, copied, or printed."
