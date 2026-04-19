# HEARTBEAT.md

## Cron Health Check
- Verify Feature Builder cron job exists and has run in the last 2 hours
- If missing, recreate it
- **Repo Gardener**: KNOWN BUG — `every`-type cron jobs silently drop. Do NOT recreate on every heartbeat (wastes tokens). The gardener will be re-added once the OpenClaw cron bug is fixed or a workaround is found. Filed as a known issue.
- Next step: try merging gardener into builder task file so one cron runs both, OR wait for OpenClaw fix

## Daily OpenClaw Update Check
- All 5 cron jobs broken since Apr 18 due to bug #66068 (missing Editor-Version header in isolated sessions)
- Check `openclaw update` daily until fixed
- If a new version is available, update and verify cron jobs recover
- Once fixed, remove this section
