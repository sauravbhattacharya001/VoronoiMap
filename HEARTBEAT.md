# HEARTBEAT.md

## Cron Health Check
- Verify both Repo Gardener and Feature Builder cron jobs exist and have run in the last 2 hours
- If either is missing, recreate it and notify Shubho
- Gardener job name: "Repo Gardener", Builder job name: "Feature Builder"
- Both should be: every 30min, isolated, delivery none, timeout 1800s
