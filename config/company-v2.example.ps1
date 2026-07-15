param(
    [ValidateSet("company", "deterministic")]
    [string]$ModelMode = "company",

    [string]$OpenAIBaseUrl = "https://ai-gateway.example.invalid/v1",

    [string]$OpenAIModel = "approved-model-id",

    [string[]]$AllowedHosts = @("ai-gateway.example.invalid"),

    [string]$CaBundle = "",

    [string]$EmbeddingModel = "",

    [string]$EmbeddingBaseUrl = "",

    [switch]$ApproveEmbeddings,

    [string]$DoclingTokenizerPath = "",

    [string]$DoclingArtifactsPath = "",

    [string]$RerankerModelPath = "",

    [switch]$ApproveReranker
)

$ErrorActionPreference = "Stop"

$env:HAYSTACK_TELEMETRY_ENABLED = "False"
$env:PROJECT_COPILOT_KNOWLEDGE_PROVIDER = "local"
$env:PROJECT_COPILOT_MODEL_MODE = $ModelMode

if ($ModelMode -eq "deterministic") {
    Remove-Item Env:PROJECT_COPILOT_OPENAI_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_OPENAI_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_ALLOWED_HOSTS -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_CA_BUNDLE -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_EMBEDDING_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_EMBEDDING_BASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_ACK_EMBEDDINGS_APPROVED -ErrorAction SilentlyContinue
    Write-Host "Configured deterministic test-double mode (no model egress)."
}
else {
    if (-not $env:PROJECT_COPILOT_OPENAI_API_KEY) {
        throw "Inject PROJECT_COPILOT_OPENAI_API_KEY through the approved secret launcher before loading this file."
    }

    $ParsedBaseUrl = [Uri]$OpenAIBaseUrl
    $NormalizedAllowedHosts = @(
        $AllowedHosts |
            ForEach-Object { $_.Trim().ToLowerInvariant() } |
            Where-Object { $_ } |
            Select-Object -Unique
    )

    if (-not $ParsedBaseUrl.Host) {
        throw "OpenAIBaseUrl must contain a hostname."
    }

    $IsLoopback = $ParsedBaseUrl.Host -in @("127.0.0.1", "::1", "localhost")
    if (-not $IsLoopback -and $ParsedBaseUrl.Scheme -ne "https") {
        throw "A non-loopback company endpoint must use HTTPS."
    }

    if ($ParsedBaseUrl.Host.ToLowerInvariant() -notin $NormalizedAllowedHosts) {
        throw "The OpenAIBaseUrl hostname must appear exactly in AllowedHosts."
    }

    if (-not $OpenAIModel.Trim()) {
        throw "OpenAIModel is required."
    }

    if ($CaBundle) {
        if (-not (Test-Path -LiteralPath $CaBundle -PathType Leaf)) {
            throw "CA bundle does not exist: $CaBundle"
        }
        $env:PROJECT_COPILOT_CA_BUNDLE = (Resolve-Path -LiteralPath $CaBundle).Path
    }
    else {
        Remove-Item Env:PROJECT_COPILOT_CA_BUNDLE -ErrorAction SilentlyContinue
    }

    $env:PROJECT_COPILOT_OPENAI_BASE_URL = $OpenAIBaseUrl.TrimEnd("/")
    $env:PROJECT_COPILOT_OPENAI_MODEL = $OpenAIModel.Trim()
    $env:PROJECT_COPILOT_ALLOWED_HOSTS = $NormalizedAllowedHosts -join ","

    if ($EmbeddingModel.Trim()) {
        if (-not $ApproveEmbeddings) {
            throw "EmbeddingModel requires the explicit -ApproveEmbeddings switch."
        }
        $ResolvedEmbeddingBaseUrl = if ($EmbeddingBaseUrl.Trim()) {
            $EmbeddingBaseUrl.TrimEnd("/")
        }
        else {
            $env:PROJECT_COPILOT_OPENAI_BASE_URL
        }
        $ParsedEmbeddingUrl = [Uri]$ResolvedEmbeddingBaseUrl
        $EmbeddingLoopback = $ParsedEmbeddingUrl.Host -in @("127.0.0.1", "::1", "localhost")
        if (-not $EmbeddingLoopback -and $ParsedEmbeddingUrl.Scheme -ne "https") {
            throw "A non-loopback embedding endpoint must use HTTPS."
        }
        if ($ParsedEmbeddingUrl.Host.ToLowerInvariant() -notin $NormalizedAllowedHosts) {
            throw "The embedding endpoint hostname must appear exactly in AllowedHosts."
        }
        $env:PROJECT_COPILOT_EMBEDDING_MODEL = $EmbeddingModel.Trim()
        $env:PROJECT_COPILOT_EMBEDDING_BASE_URL = $ResolvedEmbeddingBaseUrl
        $env:PROJECT_COPILOT_ACK_EMBEDDINGS_APPROVED = "true"
    }
    else {
        Remove-Item Env:PROJECT_COPILOT_EMBEDDING_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:PROJECT_COPILOT_EMBEDDING_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:PROJECT_COPILOT_ACK_EMBEDDINGS_APPROVED -ErrorAction SilentlyContinue
    }

    Write-Host "Configured company OpenAI-compatible mode."
    Write-Host "Base URL: $($env:PROJECT_COPILOT_OPENAI_BASE_URL)"
    Write-Host "Model: $($env:PROJECT_COPILOT_OPENAI_MODEL)"
    Write-Host "Allowed hosts: $($env:PROJECT_COPILOT_ALLOWED_HOSTS)"
    Write-Host "API key: injected (value not displayed)"
    if ($env:PROJECT_COPILOT_EMBEDDING_MODEL) {
        Write-Host "Embeddings: approved model $($env:PROJECT_COPILOT_EMBEDDING_MODEL)"
        Write-Host "Embedding base URL: $($env:PROJECT_COPILOT_EMBEDDING_BASE_URL)"
    }
    else {
        Write-Host "Embeddings: disabled; persistent BM25 remains active"
    }
}

if ($DoclingTokenizerPath.Trim()) {
    if (-not (Test-Path -LiteralPath $DoclingTokenizerPath -PathType Container)) {
        throw "Docling tokenizer directory does not exist: $DoclingTokenizerPath"
    }
    $env:PROJECT_COPILOT_DOCLING_TOKENIZER_PATH = (
        Resolve-Path -LiteralPath $DoclingTokenizerPath
    ).Path
    Write-Host "Docling tokenizer: approved local directory configured"
}
else {
    Remove-Item Env:PROJECT_COPILOT_DOCLING_TOKENIZER_PATH -ErrorAction SilentlyContinue
}

if ($DoclingArtifactsPath.Trim()) {
    if (-not (Test-Path -LiteralPath $DoclingArtifactsPath -PathType Container)) {
        throw "Docling model artifacts directory does not exist: $DoclingArtifactsPath"
    }
    $ResolvedDoclingArtifacts = (
        Resolve-Path -LiteralPath $DoclingArtifactsPath
    ).Path
    $env:PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH = $ResolvedDoclingArtifacts
    $env:DOCLING_ARTIFACTS_PATH = $ResolvedDoclingArtifacts
    Write-Host "Docling PDF artifacts: approved local directory configured"
}
else {
    Remove-Item Env:PROJECT_COPILOT_DOCLING_ARTIFACTS_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:DOCLING_ARTIFACTS_PATH -ErrorAction SilentlyContinue
}

if ($RerankerModelPath.Trim()) {
    if (-not $ApproveReranker) {
        throw "RerankerModelPath requires the explicit -ApproveReranker switch."
    }
    if (-not (Test-Path -LiteralPath $RerankerModelPath -PathType Container)) {
        throw "Reranker model directory does not exist: $RerankerModelPath"
    }
    $env:PROJECT_COPILOT_RERANKER_MODEL_PATH = (
        Resolve-Path -LiteralPath $RerankerModelPath
    ).Path
    $env:PROJECT_COPILOT_ACK_RERANKER_APPROVED = "true"
    Write-Host "Reranker: approved local model directory configured"
}
else {
    Remove-Item Env:PROJECT_COPILOT_RERANKER_MODEL_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT_COPILOT_ACK_RERANKER_APPROVED -ErrorAction SilentlyContinue
}
