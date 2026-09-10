# Registers a Task Scheduler job that runs gmail_push.py on a repeating
# interval (hidden, no login required), pushing LinkedIn/Indeed postings
# parsed from your Gmail alerts into the shared Turso db so they show up
# on the site and go out over Discord like any other posting.
#
# Requires: Gmail already set up (credentials.json + a token.json that is
# NOT from an OAuth consent screen still in "Testing" status, or it will
# stop working after 7 days) and config.local.yaml's turso: block filled
# in (or TURSO_DATABASE_URL / TURSO_AUTH_TOKEN set some other way).
#
# Run:    powershell -ExecutionPolicy Bypass -File setup_gmail_push.ps1
# Remove: Unregister-ScheduledTask -TaskName InternshipTrackerGmailPush -Confirm:$false

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { $pythonw = (Get-Command python).Source }

$intervalMinutes = 20   # matches schedule.gmail_minutes in config.yaml

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$here\gmail_push.py`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $intervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName "InternshipTrackerGmailPush" -Action $action `
    -Trigger $trigger -Settings $settings -Force

Write-Host "Registered. Runs every $intervalMinutes min; starting it now..."
Start-ScheduledTask -TaskName "InternshipTrackerGmailPush"
