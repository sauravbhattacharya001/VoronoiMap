# 🧠 Zalenix Workspace

Private workspace for **Zalenix**, an AI assistant powered by [OpenClaw](https://github.com/openclaw/openclaw).

This repository stores persistent memory, identity configuration, project context, and automated task definitions that Zalenix uses across sessions.

## ⚠️ Private Repository

This repository contains personal context and workspace data. It is not intended for public use.

## Structure

```
├── SOUL.md              # Personality and behavioral guidelines
├── IDENTITY.md          # Name, creature type, vibe, avatar
├── USER.md              # Information about the human operator
├── AGENTS.md            # Workspace conventions and session rules
├── HEARTBEAT.md         # Periodic background task checklist
├── MEMORY.md            # Curated long-term memory
├── TOOLS.md             # Local tool configuration and notes
│
├── memory/              # Daily logs (memory/YYYY-MM-DD.md)
├── projects/            # Active project context and state
├── scripts/             # Utility scripts (PowerShell, Python)
│
├── gardener-task.md     # Repo Gardener automated task definition
├── gardener-weights.json# Task selection weights for Repo Gardener
├── builder-task.md      # Builder automated task definition
├── sentinel-task.md     # WinSentinel task definition
│
├── runs.md              # Sub-agent run history (most recent first)
└── status.md            # Current sub-agent progress
```

## Key Concepts

- **Memory system**: Daily files (`memory/YYYY-MM-DD.md`) capture raw session logs. `MEMORY.md` holds curated long-term knowledge distilled from daily files.
- **Heartbeats**: Periodic polls where Zalenix checks email, calendar, and other services. Configured in `HEARTBEAT.md`.
- **Sub-agents**: Long-running tasks are delegated to sub-agents that report progress via `status.md` and log results to `runs.md`.
- **Automated tasks**: Cron-triggered tasks (Repo Gardener, Builder, Sentinel) have their own task definition files with weights and instructions.

## Author

[Saurav Bhattacharya](https://github.com/sauravbhattacharya001)
