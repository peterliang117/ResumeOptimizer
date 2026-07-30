[CmdletBinding()]
param(
    [string]$Out,
    [switch]$IncludePackets,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run scripts\bootstrap_workstation.ps1 first."
}

$Arguments = @(
    (Join-Path $RepositoryRoot 'scripts\private_state.py'),
    'export'
)
if ($Out) {
    $Arguments += @('--out', $Out)
}
if ($IncludePackets) {
    $Arguments += '--include-packets'
}
if ($Force) {
    $Arguments += '--force'
}

& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Private-state export failed with exit code $LASTEXITCODE."
}
