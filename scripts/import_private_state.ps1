[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Archive,
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
    'import',
    $Archive
)
if ($Force) {
    $Arguments += '--force'
}

& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Private-state import failed with exit code $LASTEXITCODE."
}

& $Python (Join-Path $RepositoryRoot 'scripts\migrate_to_sqlite.py') --export-csv
if ($LASTEXITCODE -ne 0) {
    throw "State was restored, but CSV export failed with exit code $LASTEXITCODE."
}

& $Python (Join-Path $RepositoryRoot 'scripts\doctor.py')
if ($LASTEXITCODE -ne 0) {
    throw "State was restored, but the doctor check failed with exit code $LASTEXITCODE."
}

& $Python (Join-Path $RepositoryRoot 'scripts\verify_tracker.py')
if ($LASTEXITCODE -ne 0) {
    throw "State was restored, but tracker validation failed with exit code $LASTEXITCODE."
}
