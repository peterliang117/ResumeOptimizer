param(
    [int]$Port = 8770,
    [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
}

$localLlmScript = Join-Path $PSScriptRoot "use_local_llm.ps1"
if (Test-Path $localLlmScript) {
    . $localLlmScript
}

& $python (Join-Path $PSScriptRoot "workflow_dashboard.py") --host $HostName --port $Port
