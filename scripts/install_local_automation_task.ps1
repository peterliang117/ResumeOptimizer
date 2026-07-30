[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Status')]
    [string]$Action = 'Install',
    [string]$TaskName = 'ResumeOptimizerLocalAutomation',
    [int]$EveryHours = 4,
    [datetime]$StartAt = (Get-Date).AddMinutes(5),
    [switch]$RunNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($EveryHours -lt 1) {
    throw 'EveryHours must be at least 1.'
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
$EntryPoint = Join-Path $RepositoryRoot 'scripts\local_automation.py'
$Config = Join-Path $RepositoryRoot 'profile\local_automation.json'

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

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual-environment Python was not found: $Python. Create the environment first."
}
if (-not (Test-Path -LiteralPath $EntryPoint)) {
    throw "Automation entrypoint was not found: $EntryPoint."
}

$arguments = "`"$EntryPoint`" --config `"$Config`""
$scheduledAction = New-ScheduledTaskAction -Execute $Python -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At $StartAt -RepetitionInterval (New-TimeSpan -Hours $EveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $scheduledAction -Trigger $trigger -Settings $settings -Principal $principal -Description 'Runs Resume Optimizer local maintenance only; no browser submission.' -Force | Out-Null
Write-Output "Installed '$TaskName' every $EveryHours hour(s), starting $StartAt. It runs only while this user is logged in."
if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Started '$TaskName'. Review logs\local_automation.log for results."
}
