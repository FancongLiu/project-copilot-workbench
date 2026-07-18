param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ProjectPath,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RuntimePath,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CodexRuntimeRoot,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CodexConfig,
    [int]$Port = 8790,
    [ValidateSet("low", "medium", "high", "xhigh")]
    [string]$ReasoningEffort = "high"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root ".venv\Scripts\project-copilot.exe"
$preflight = Join-Path $root ".venv\Scripts\project-copilot-codex-preflight.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Run scripts/bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $preflight -PathType Leaf)) {
    throw "Run scripts/bootstrap.ps1 again to install the Codex preflight command."
}

$resolvedProject = (Resolve-Path -LiteralPath $ProjectPath).Path
$resolvedConfig = (Resolve-Path -LiteralPath $CodexConfig).Path
$resolvedRuntime = [System.IO.Path]::GetFullPath($RuntimePath)
$resolvedCodexRoot = (Resolve-Path -LiteralPath $CodexRuntimeRoot).Path
$codexExe = Get-ChildItem -LiteralPath (Join-Path $resolvedCodexRoot "node_modules") `
    -Filter "codex.exe" -File -Recurse | Sort-Object FullName | Select-Object -First 1
if (-not $codexExe) {
    throw "codex.exe was not found. Run scripts/bootstrap-codex-runtime.ps1 first."
}

New-Item -ItemType Directory -Force -Path $resolvedRuntime | Out-Null
$env:HAYSTACK_TELEMETRY_ENABLED = "False"
$env:PROJECT_COPILOT_AGENT_RUNTIME = "codex"
$env:PROJECT_COPILOT_ACK_CODEX_SWITCH = "true"
$env:PROJECT_COPILOT_CODEX_CONFIG = $resolvedConfig
$env:PROJECT_COPILOT_CODEX_BIN = $codexExe.FullName
$env:PROJECT_COPILOT_CODEX_RUNTIME_ROOT = Join-Path $resolvedRuntime "codex-agent"
$env:PROJECT_COPILOT_CODEX_REASONING_EFFORT = $ReasoningEffort

& $preflight `
    --codex-bin $codexExe.FullName `
    --runtime-root $env:PROJECT_COPILOT_CODEX_RUNTIME_ROOT
if ($LASTEXITCODE -ne 0) {
    throw "Codex elevated sandbox preflight failed; the web service was not started."
}

$arguments = @(
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--project", $resolvedProject,
    "--runtime", $resolvedRuntime
)
& $exe @arguments
exit $LASTEXITCODE
