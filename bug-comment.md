## Additional Diagnostics

After further investigation, the issue persists even after:

1. **Full identity reset** — deleted `identity/`, `devices/`, and `credentials/telegram-pairing.json`, then ran `openclaw setup` and `openclaw gateway start`
2. New `device.json` and `paired.json` are generated with matching deviceIds
3. The `gateway-client` device is approved with `operator.admin` scope

**Key finding:** `openclaw agent --agent main --channel webchat --message "test"` also fails with `pairing required` but falls back to embedded mode. The gateway rejects ALL new websocket connections — only the pre-existing main session (established before identity files were regenerated) continues to work.

**Theory:** When `sessions_spawn` or `openclaw agent` connects to the gateway, it presents device credentials that the gateway doesn't recognize, even though `paired.json` lists the device as approved. Possible causes:
- The gateway process has an in-memory copy of the old paired devices and doesn't reload from disk
- The sub-agent/CLI uses a different identity than `device.json` (maybe a per-process ephemeral key)
- `gateway install --force` changed the gateway's own keypair, and now the gateway-side validation rejects even approved devices because the handshake uses stale keys

**Environment:**
- Windows 11 (10.0.26200)
- OpenClaw 2026.2.21-2
- Gateway: local, loopback, token auth
- Node.js v22.18.0

**Reproduction:** Run `gateway install --force`, then try `sessions_spawn` or `openclaw agent` — all connections rejected with `pairing required`.

Any guidance on how to fully reset the pairing state so new connections are accepted again?
