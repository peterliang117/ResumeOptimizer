$env:LOCAL_LLM_ENABLED = "1"
$env:LOCAL_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:LOCAL_LLM_SCREENING_MODEL = "qwen3:8b"
$env:LOCAL_LLM_RESUME_MODEL = "qwen3:14b"
$env:LOCAL_LLM_APPLICATION_MODEL = "qwen3:14b"
$env:RESUME_OPTIMIZER_LLM_PROVIDER = "local"

$ollamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$ollamaExe = Join-Path $ollamaDir "ollama.exe"
if ((Test-Path $ollamaExe) -and ($env:Path -notlike "*$ollamaDir*")) {
    $env:Path = "$ollamaDir;$env:Path"
}

Write-Host "Local LLM environment configured for this PowerShell session."
Write-Host "Base URL: $env:LOCAL_LLM_BASE_URL"
Write-Host "Screening model: $env:LOCAL_LLM_SCREENING_MODEL"
Write-Host "Resume/application model: $env:LOCAL_LLM_RESUME_MODEL"
