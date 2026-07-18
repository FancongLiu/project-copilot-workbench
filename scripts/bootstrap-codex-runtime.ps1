param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RuntimeRoot,
    [string]$Npm = "npm"
)

$ErrorActionPreference = "Stop"
$CodexPackage = "@openai/codex@0.144.5"

if (-not (Get-Command $Npm -ErrorAction SilentlyContinue)) {
    throw "Node.js 18+ and npm are required to install the official Codex runtime."
}

$resolvedRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
New-Item -ItemType Directory -Force -Path $resolvedRoot | Out-Null

& $Npm install --prefix $resolvedRoot --no-save $CodexPackage
if ($LASTEXITCODE -ne 0) {
    throw "Official Codex package installation failed with exit code $LASTEXITCODE."
}

$codexExe = Get-ChildItem -LiteralPath (Join-Path $resolvedRoot "node_modules") `
    -Filter "codex.exe" -File -Recurse | Sort-Object FullName | Select-Object -First 1
if (-not $codexExe) {
    throw "The official package was installed but its native Windows codex.exe was not found."
}
& $codexExe.FullName --version
if ($LASTEXITCODE -ne 0) {
    throw "Installed Codex runtime did not pass its version smoke test."
}

Write-Host "Codex runtime ready."
Write-Host "PROJECT_COPILOT_CODEX_BIN=$($codexExe.FullName)"
Write-Host "No API credential was created, copied, or printed."
