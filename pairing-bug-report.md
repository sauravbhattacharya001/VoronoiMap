# OpenClaw Pairing Bug — Root Cause Analysis & Fix

**Date:** February 21, 2026  
**Duration:** ~6 hours (14:35 – 20:34 PST)  
**Environment:** Windows 11, OpenClaw 2026.2.21-2 (gateway running 2026.2.19 binary), Node v22.18.0  
**Issue:** [GitHub #23044](https://github.com/openclaw/openclaw/issues/23044)  
**Impact:** All sub-agent spawning blocked — Gardener, Builder, and any `sessions_spawn` call failed with `gateway closed (1008): pairing required`

---

## Symptom

Every `sessions_spawn` call returned:

```
gateway closed (1008): pairing required
Gateway target: ws://127.0.0.1:18789
Source: local loopback
```

Even `openclaw agent --agent main --channel webchat --message "test"` failed. The main session (webchat, Telegram) continued to work fine — only new sub-agent connections were rejected.

---

## Timeline of Investigation

### Phase 1: Initial Diagnosis (14:35 – 15:00)

The issue started after running `openclaw gateway install --force` followed by a gateway restart. We assumed the force-install had corrupted identity files.

- Ran `openclaw gateway status` — confirmed gateway was running, port 18789 active
- Checked `devices/paired.json` — had stale entries from old device identities
- Checked `identity/device.json` — had a valid device identity

### Phase 2: Identity Reset Attempts (15:00 – 15:45)

**Attempt 1:** Delete `devices/paired.json` and `devices/pending.json`, restart gateway.  
**Result:** ❌ Gateway recreated pending.json on startup.

**Attempt 2:** Delete `identity/`, `devices/`, and `credentials/telegram-pairing.json`, then run `openclaw setup` to regenerate fresh identity.  
**Result:** ❌ New deviceId `eee0d55b...` generated, but same error persisted.

**Attempt 3:** Full identity nuke — delete `identity/` AND `devices/` together, restart gateway.  
**Result:** ❌ Gateway auto-created new pending request, never auto-approved it.

### Phase 3: External Help (15:45 – 20:00)

- Filed detailed diagnostics on [GitHub issue #23044](https://github.com/openclaw/openclaw/issues/23044)
- Posted to OpenClaw Discord `#support` channel
- Received response suggesting service might use a different state directory than CLI

**Attempt 4:** Checked `Config (cli)` vs `Config (service)` paths from `openclaw gateway status`.  
**Result:** Both pointed to `~/.openclaw` — same directory. Not the issue.

**Attempt 5:** Changed `gateway.auth.mode` from `"token"` to `"none"` (documented as explicit no-auth for trusted loopback).  
**Result:** ❌ Device pairing is a **separate system** from gateway auth. Auth mode has no effect on device pairing.

**Attempt 6:** Used `openclaw devices list` to find pending requests, then `openclaw devices approve <requestId>` to manually approve.  
**Result:** ⚠️ Device was approved and appeared in paired list, but next spawn created a NEW pending request with `repair` flag for the same deviceId.

### Phase 4: Source Code Analysis (20:20 – 20:30)

Read the gateway source code (`gateway-cli-CIYEdmIv.js`) to understand the pairing flow:

```javascript
// The auto-approval logic:
if (pairing.request.silent === true) {
    const approved = await approveDevicePairing(pairing.request.requestId);
    // ...
}

// Silent is only set for "not-paired" reason on local connections:
silent: isLocalClient && reason === "not-paired"

// The decision tree:
const paired = await getPairedDevice(device.id);
if (!(paired?.publicKey === devicePublicKey)) {
    // → reason: "not-paired" → silent → auto-approved ✅
} else {
    // Device IS paired, check scopes:
    if (!roleScopesAllow({ role, requestedScopes: scopes, allowedScopes: pairedScopes })) {
        // → reason: "scope-upgrade" → NOT silent → REJECTED ❌
    }
}
```

Key insight: **scope upgrades are never auto-approved**, even for local loopback connections. Only `reason === "not-paired"` gets the `silent: true` flag that triggers auto-approval.

### Phase 5: The Gateway Logs (20:30 – 20:34)

Checked `\tmp\openclaw\openclaw-2026-02-21.log` and found the smoking gun:

```
security audit: device access upgrade requested
  reason=scope-upgrade
  device=0c1a1b...
  scopesFrom=operator.admin,operator.approvals,operator.pairing
  scopesTo=operator.write
```

**The device was paired. The keys matched. But the sub-agent requested `operator.write` scope, which wasn't in the paired device's approved scopes.**

---

## Root Cause

The paired device entry in `devices/paired.json` had scopes:
```json
["operator.admin", "operator.approvals", "operator.pairing"]
```

Sub-agents connect requesting scope:
```json
["operator.write"]
```

Since `operator.write` ∉ `{operator.admin, operator.approvals, operator.pairing}`, the gateway treated this as a **scope upgrade** — which requires explicit approval. The scope-upgrade pairing request was created with `silent: false`, meaning it was never auto-approved, and the connection was immediately closed with `1008: pairing required`.

**How did this happen?** The original `openclaw gateway install --force` regenerated the gateway's keypair, invalidating all existing device tokens. When the device re-paired (either automatically or via `openclaw setup`), the new pairing was created with only `operator.admin` scope (or a limited set). The `operator.write` scope that sub-agents need was never included in the re-pairing approval.

---

## Fix

Added `operator.write` to the paired device's `scopes` and `approvedScopes` arrays in `~/.openclaw/devices/paired.json`:

```json
{
  "0c1a1b...": {
    "scopes": [
      "operator.admin",
      "operator.approvals",
      "operator.pairing",
      "operator.write"          // ← added
    ],
    "approvedScopes": [
      "operator.admin",
      "operator.approvals",
      "operator.pairing",
      "operator.write"          // ← added
    ]
  }
}
```

Then restarted the gateway. `sessions_spawn` worked immediately.

---

## Lessons Learned

1. **Check gateway logs first.** The log file at `\tmp\openclaw\openclaw-YYYY-MM-DD.log` contains the exact rejection reason. The line `security audit: device access upgrade requested reason=scope-upgrade` would have solved this in 5 minutes instead of 6 hours.

2. **"Pairing required" is misleading.** The error message doesn't distinguish between "device not paired" and "device paired but requesting higher scopes." Both return the same `1008: pairing required` close code.

3. **`operator.write` is required for sub-agents.** This scope isn't included by default when a device re-pairs. If you ever reset identity/pairing files, you must manually add `operator.write` to the paired device's scopes.

4. **`auth.mode` ≠ device pairing.** Gateway auth (token/none) controls whether connections need a shared secret. Device pairing is a separate identity + scope verification layer that runs regardless of auth mode.

5. **`openclaw gateway install --force` is dangerous.** It can regenerate the gateway's keypair, silently invalidating all paired device tokens and triggering cascading re-pairing issues.

6. **`openclaw devices list` is the right diagnostic tool.** It shows both pending and paired devices with their scopes, making scope mismatches visible.

---

## Suggested Improvements for OpenClaw

1. **Better error messages**: `pairing required` should include the reason — e.g., `pairing required (scope-upgrade: operator.write not in approved scopes)`. This alone would have saved hours.

2. **Auto-approve scope upgrades for local loopback**: If the connection is local (127.0.0.1) and the device is already paired, scope upgrades should be auto-approved (or at least configurable). Local connections are inherently trusted.

3. **Include `operator.write` in default pairing scopes**: When a device is auto-approved via `openclaw setup` or local pairing, the `operator.write` scope should be included by default since sub-agents require it.

4. **Gateway log hint in error output**: When a pairing error occurs, the CLI output could suggest checking the gateway log file for details: `"Hint: check gateway logs for details: \tmp\openclaw\openclaw-YYYY-MM-DD.log"`.

---

*Written by Zalenix ✨ — Shubho's AI assistant*
