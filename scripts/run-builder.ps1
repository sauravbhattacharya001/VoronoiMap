# Run Feature Builder via OpenClaw CLI
# Triggered by Windows Task Scheduler every 30 minutes

$ErrorActionPreference = "Stop"
$workspace = Join-Path $env:USERPROFILE ".openclaw\workspace"
$logFile = Join-Path $workspace "scripts\builder-scheduler.log"
$taskFile = Join-Path $workspace "builder-task.md"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Add-Content -Path $logFile -Value "$timestamp - Triggering Builder run"

try {
    $content = Get-Content -Path $taskFile -Raw
    if ($content -match "PAUSED") {
        Add-Content -Path $logFile -Value "$timestamp - Builder is PAUSED, skipping"
        exit 0
    }

    $msg = "Read and follow the task instructions in $taskFile"
    & openclaw agent --agent main --channel telegram --message $msg --timeout 1800 --json 2>&1 | Out-Null
    Add-Content -Path $logFile -Value "$timestamp - Builder completed"
}
catch {
    Add-Content -Path $logFile -Value "$timestamp - ERROR: $($_.Exception.Message)"
}
