param(
    [string]$ProjectPath = "",
    [string]$RuntimePath = "",
    [int]$Port = 8788
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root ".venv\Scripts\project-copilot.exe"
if (-not (Test-Path $exe)) {
    throw "Run scripts/bootstrap.ps1 first."
}

$env:HAYSTACK_TELEMETRY_ENABLED = "False"
$arguments = @("--port", "$Port")
if ($ProjectPath) { $arguments += @("--project", $ProjectPath) }
if ($RuntimePath) { $arguments += @("--runtime", $RuntimePath) }
& $exe @arguments
exit $LASTEXITCODE
