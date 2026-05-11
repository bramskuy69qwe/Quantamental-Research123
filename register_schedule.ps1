# Register the quant-research scout as a daily Windows Task Scheduler job.
#
# Usage (from an elevated PowerShell):
#     cd "E:\Quantamental Models\Research Layer\Quantamental Research"
#     powershell -ExecutionPolicy Bypass -File .\register_schedule.ps1
#
# To unregister later:
#     Unregister-ScheduledTask -TaskName "QuantResearchScout" -Confirm:$false
#
# To run it on demand without waiting for the trigger:
#     Start-ScheduledTask -TaskName "QuantResearchScout"

param(
    [string]$TaskName = "QuantResearchScout",
    [string]$Time     = "07:00",          # 24h, local time
    [string]$BatPath  = (Join-Path $PSScriptRoot "run_daily.bat")
)

if (-not (Test-Path $BatPath)) {
    Write-Error "run_daily.bat not found at $BatPath"
    exit 1
}

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatPath`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $Time

# Run whether or not the user is logged on; allow start on battery; restart on failure.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# Run as the current user, with stored credentials so it works when logged out.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Daily quant-research scout: scans arXiv/GitHub/Reddit/HN/Quantocracy and saves vetted findings to research.db" `
    -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at $Time."
Write-Host "Log files: $PSScriptRoot\logs\daily-YYYY-MM-DD.log"
Write-Host "Run now:   Start-ScheduledTask -TaskName '$TaskName'"
