# TEAM.md — Agent Organization

## Org Chart

```
Shubho (Founder)
  ├── TheRedBull 🐂 (Direct Line / Control UI)
  └── Zalenix ✨ (CTO / Main Agent)
        ├── Repo Gardener 🌿 (Maintenance Lead)
        ├── Feature Builder 🔨 (Product Lead)
        │     └── Codex 🤖 (Developer)
        ├── Daily Digest 📊 (Reporting)
        ├── Memory Backup 💾 (Ops)
        └── Profile Refresh 👤 (Marketing)
```

## Roster

| Agent | Role | Schedule | Model | Status |
|---|---|---|---|---|
| **TheRedBull** 🐂 | Shubho's direct line — primary comm on the box | Always on (Control UI) | Opus 4.7 | ✅ Active |
| **Zalenix** ✨ | CTO — orchestrates everything, talks via Telegram | Always on (Telegram) | Opus 4.6 | ✅ Active |
| **Repo Gardener** 🌿 | Maintenance — security fixes, perf improvements, refactors, releases | Every 30 min (cron) | Opus 4.6 | ⏸️ Blocked (bug #66068) |
| **Feature Builder** 🔨 | Product — picks & implements new features across all repos | Every 30 min (cron) | Opus 4.6 | ⏸️ Blocked (bug #66068) |
| **Codex** 🤖 | Developer — writes code when spawned by Builder or Zalenix | On demand (ACP) | GPT via Copilot | ✅ Available |
| **Daily Digest** 📊 | Reporting — sends daily activity summary to Telegram | 9 PM PST (cron) | Opus 4.6 | ⏸️ Blocked (bug #66068) |
| **Memory Backup** 💾 | Ops — commits workspace to private git repo | 11 PM PST (cron) | Opus 4.6 | ⏸️ Blocked (bug #66068) |
| **Profile Refresh** 👤 | Marketing — updates GitHub profile README with latest stats | 10 AM PST (cron) | Opus 4.6 | ⏸️ Blocked (bug #66068) |

## Agent Details

### TheRedBull 🐂 (Direct Line)
- **Type:** OpenClaw agent (Control UI)
- **Channel:** Control UI (local web dashboard)
- **Model:** Opus 4.7
- **Workspace:** Same as Zalenix
- **Role:** Shubho's primary communication channel on the box. Direct access, no Telegram latency.

### Zalenix ✨ (Main Agent)
- **Type:** OpenClaw main session
- **Channel:** Telegram DM
- **Access:** Full — browser, exec, files, phone sensors, cron, messaging
- **Personality:** Casual, smart, opinionated
- **Responsibilities:**
  - Direct communication with Shubho
  - Orchestrates all other agents
  - Handles ad-hoc tasks (email, research, debugging)
  - Manages infrastructure (gateway, Tailscale, configs)
  - Delegates long tasks to sub-agents

### Repo Gardener 🌿
- **Type:** Isolated cron session
- **Cron ID:** `20f1a1bc-74e3-4edf-8ef7-343a6773d28a`
- **Task file:** `gardener-task.md`
- **Scope:** All 16 repos (skip forks, zalenix-memory)
- **Work:** 2 tasks per run, weighted random selection from 30 task types
- **Rules:** Push directly, never open PRs, senior engineer quality bar
- **Stats:** ~2166 runs as of Mar 31

### Feature Builder 🔨
- **Type:** Isolated cron session
- **Cron ID:** `aee62c7d-b5fe-44dd-a706-826834b2f276`
- **Task file:** `builder-task.md`
- **Scope:** All repos, 1 feature per run
- **Work:** Picks repo → plans feature → spawns Codex → reviews → pushes
- **Creative direction:** Gradually building agentic capabilities into apps
- **Rules:** Push directly, verify builds, never break existing functionality
- **Stats:** ~700 runs as of Mar 31

### Codex 🤖
- **Type:** ACP sub-agent (on-demand)
- **CLI:** codex-cli v0.121.0
- **Provider:** GitHub Copilot (free)
- **Spawned by:** Zalenix or Feature Builder
- **Scope:** Coding tasks — implementation, refactoring, debugging
- **Config:** `acp.defaultAgent = "codex"`

### Daily Digest 📊
- **Type:** Isolated cron session
- **Cron ID:** `428cd548-df92-4678-a2be-01fcd450a915`
- **Schedule:** Daily 9 PM PST
- **Output:** Telegram message to Shubho with gardener/builder highlights

### Memory Backup 💾
- **Type:** Isolated cron session
- **Cron ID:** `e52fb1c7-8c11-4471-b18f-68edcd7583bd`
- **Schedule:** Daily 11 PM PST
- **Repo:** `sauravbhattacharya001/zalenix-memory` (private)

### Profile Refresh 👤
- **Type:** Isolated cron session
- **Cron ID:** `9b78ad18-1647-4b81-bd33-f8cb616a267c`
- **Schedule:** Daily 10 AM PST
- **Repo:** `sauravbhattacharya001/sauravbhattacharya001`

## Infrastructure

- **Host:** Windows 11, Shubho's laptop
- **Gateway:** OpenClaw 2026.4.15, port 18789
- **Networking:** Tailscale (100.72.230.43), Tailscale Serve for phone
- **Phone node:** Samsung Z Fold4 via Tailscale (wss://)
- **Auth:** GitHub Copilot (free tier)
- **Known issue:** Bug #66068 — all cron jobs broken (missing Editor-Version header in isolated sessions)

## Future Scaling

- Add more Codex workers for parallel builds
- Cloud VPS for 24/7 uptime (currently depends on laptop being on)
- Paid API keys if Copilot rate limits bite
- Potential agents: QA Tester, Security Auditor, Community Manager
