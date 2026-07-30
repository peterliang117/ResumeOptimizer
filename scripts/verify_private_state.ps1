[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Archive
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run scripts\bootstrap_workstation.ps1 first."
}

& $Python (Join-Path $RepositoryRoot 'scripts\private_state.py') verify $Archive
if ($LASTEXITCODE -ne 0) {
    throw "Private-state verification failed with exit code $LASTEXITCODE."
}
