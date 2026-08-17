[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall,
    [switch]$InstallScheduledTask,
    [int]$EveryHours = 4
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$VirtualEnvironment = Join-Path $RepositoryRoot '.venv'
$Python = Join-Path $VirtualEnvironment 'Scripts\python.exe'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    Write-Host "== $Label =="
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Resolve-PythonLauncher {
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) {
        return @{ Command = $Py.Source; Arguments = @('-3') }
    }
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        return @{ Command = $PythonCommand.Source; Arguments = @() }
    }
    throw 'Python 3 was not found. Install Python 3.11 or newer and rerun this script.'
}

Push-Location $RepositoryRoot
try {
    if (-not (Test-Path -LiteralPath $Python)) {
        $Launcher = Resolve-PythonLauncher
        Invoke-Checked -Label 'Create virtual environment' -Command $Launcher.Command `
            -Arguments ($Launcher.Arguments + @('-m', 'venv', $VirtualEnvironment))
    }

    if (-not $SkipDependencyInstall) {
        Invoke-Checked -Label 'Upgrade pip' -Command $Python `
            -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
        Invoke-Checked -Label 'Install Python dependencies' -Command $Python `
            -Arguments @('-m', 'pip', 'install', '-r', 'requirements.txt')
    }

    foreach ($Directory in @(
        'applications',
        'backups',
        'data',
        'jobs',
        'logs',
        'outputs',
        'profile',
        'resumes',
        'tailored_resumes',
        'tmp',
        'tracker'
    )) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }

    $TemplateMappings = @{
        'profile\facts.example.md' = 'profile\facts.md'
        'profile\application_answers.example.json' = 'profile\application_answers.json'
        'profile\search_criteria.example.md' = 'profile\search_criteria.md'
        'profile\portals.example.yml' = 'profile\portals.yml'
        'profile\local_automation.example.json' = 'profile\local_automation.json'
        'profile\resume_variants.example.json' = 'profile\resume_variants.private.json'
    }
    foreach ($Source in $TemplateMappings.Keys) {
        $Destination = $TemplateMappings[$Source]
        if (-not (Test-Path -LiteralPath $Destination)) {
            Copy-Item -LiteralPath $Source -Destination $Destination
            Write-Host "Created local template: $Destination"
        }
    }

    Invoke-Checked -Label 'Initialize SQLite state' -Command $Python `
        -Arguments @('scripts\migrate_to_sqlite.py')
    Invoke-Checked -Label 'Configure reconciliation cursors' -Command $Python `
        -Arguments @('scripts\scheduled_reconcile.py', 'configure')
    Invoke-Checked -Label 'Validate public/private boundaries' -Command $Python `
        -Arguments @('scripts\security_check.py', '--fail-on-finding')
    Invoke-Checked -Label 'Validate local setup' -Command $Python `
        -Arguments @('scripts\doctor.py')

    if (Test-Path -LiteralPath 'resumes\master.docx') {
        Invoke-Checked -Label 'Initialize resume evidence' -Command $Python `
            -Arguments @('scripts\resume_evidence.py', 'init')
    }
    else {
        Write-Warning 'Add resumes\master.docx, then run scripts\resume_evidence.py init.'
    }

    if (Test-Path -LiteralPath 'tracker\applications.csv') {
        Invoke-Checked -Label 'Validate tracker' -Command $Python `
            -Arguments @('scripts\verify_tracker.py')
    }

    if ($InstallScheduledTask) {
        & (Join-Path $PSScriptRoot 'install_local_automation_task.ps1') `
            -Action Install -EveryHours $EveryHours
    }

    Write-Host ''
    Write-Host 'Bootstrap complete.'
    Write-Host 'Next: replace the generated profile files with truthful local values, add resumes\master.docx, and reconnect Outlook/Chrome in Codex.'
}
finally {
    Pop-Location
}
