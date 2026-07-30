param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
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

& $python (Join-Path $PSScriptRoot "jobctl.py") @Args
