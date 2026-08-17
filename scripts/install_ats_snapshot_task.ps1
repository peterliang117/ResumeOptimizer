[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Status')]
    [string]$Action = 'Install',
    [string]$TaskName = 'ResumeOptimizerATSSnapshot',
    [int]$EveryHours = 2,
    [datetime]$StartAt = (Get-Date).AddMinutes(2),
    [switch]$RunNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($EveryHours -lt 1) {
    throw 'EveryHours must be at least 1.'
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
$Scanner = Join-Path $RepositoryRoot 'scripts\ats_scan.py'
$Config = Join-Path $RepositoryRoot 'profile\portals.yml'
$Snapshot = Join-Path $RepositoryRoot 'outputs\ats_discovery_snapshot.json'

switch ($Action) {
    'Status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Output "Task '$TaskName' is not installed."
            exit 0
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        [pscustomobject]@{
            TaskName = $task.TaskName
            State = $task.State
            LastRunTime = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
            NextRunTime = $info.NextRunTime
        } | Format-List
        exit 0
    }
    'Uninstall' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output "Removed task '$TaskName'."
        } else {
            Write-Output "Task '$TaskName' is not installed."
        }
        exit 0
    }
}

foreach ($requiredPath in @($Python, $Scanner, $Config)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required ATS snapshot path was not found: $requiredPath"
    }
}

$arguments = "`"$Scanner`" --config `"$Config`" --dry-run --snapshot `"$Snapshot`""
$scheduledAction = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $RepositoryRoot
$trigger = New-ScheduledTaskTrigger -Once -At $StartAt -RepetitionInterval (New-TimeSpan -Hours $EveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $scheduledAction -Trigger $trigger -Settings $settings -Principal $principal -Description 'Writes a read-only ResumeOptimizer ATS discovery snapshot; never queues or submits applications.' -Force | Out-Null
Write-Output "Installed '$TaskName' every $EveryHours hour(s), starting $StartAt."
if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Started '$TaskName'. Review outputs\ats_discovery_snapshot.json for results."
}
