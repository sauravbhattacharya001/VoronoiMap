## Bug Report

**Version:** 2026.2.21-2 (also reproduced on 2026.2.19 after downgrade)
**OS:** Windows 11 (10.0.26200)
**Gateway mode:** local, loopback, token auth

## Problem

`sessions_spawn` always fails with `gateway closed (1008): pairing required` after running `openclaw gateway install --force`.

The main session works fine (receives/sends Telegram messages), but spawning sub-agents fails every time.

## What triggered it

1. Upgraded from 2026.2.6-3 to 2026.2.21-2 via `pnpm add -g openclaw@latest`
2. `gateway.cmd` still pointed to old version path (known issue)
3. `openclaw doctor` detected gateway service config mismatch
4. Ran `openclaw gateway install --force` per docs/gateway/troubleshooting.md
5. After that, all `sessions_spawn` calls fail with `pairing required`

## What we tried (all failed)

- `openclaw gateway stop/start` (multiple times)
- `openclaw doctor --fix` and `openclaw doctor --fix --force`
- `openclaw pairing list --channel telegram` (no pending requests)
- `openclaw setup` (full re-setup)
- Downgrade to 2026.2.19 and upgrade back to 2026.2.21-2
- Deleted `identity/device.json`, `identity/device-auth.json`, `devices/paired.json` and restarted
- Fixed config `meta.lastTouchedVersion` mismatch
- Killed all node processes and clean restart
- Session restart (`/restart`)

## Observations

- Main session (Telegram DM) works perfectly - messages flow both ways
- Only `sessions_spawn` (sub-agent creation) fails
- `paired.json` shows a valid device with `gateway-client` role and `operator.admin` scope
- `device-auth.json` has matching deviceId
- Gateway token in config matches `gateway.cmd`
- The error is always: `gateway closed (1008): pairing required`

## Expected behavior

`sessions_spawn` should work for local loopback connections with valid token auth, especially after `openclaw setup`.

## Error output

```json
{
  "status": "error",
  "error": "gateway closed (1008): pairing required\nGateway target: ws://127.0.0.1:18789\nSource: local loopback\nBind: loopback"
}
```
