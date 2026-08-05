# Registers a Task Scheduler job that starts the tracker daemon (hidden) at logon.
# Run:    powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
# Remove: Unregister-ScheduledTask -TaskName InternshipTracker -Confirm:$false

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { $pythonw = (Get-Command python).Source }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$here\tracker.py`" daemon" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName "InternshipTracker" -Action $action `
    -Trigger $trigger -Settings $settings -Force

Write-Host "Registered. It will start at next logon; starting it now..."
Start-ScheduledTask -TaskName "InternshipTracker"
