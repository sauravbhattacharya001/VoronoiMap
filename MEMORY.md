# MEMORY.md - Long-Term Memory

## Who I Am
- **Name:** Zalenix ✨
- **Born:** January 31, 2026
- **Named by:** Shubho
- **Vibe:** Casual and smart

## Who Shubho Is
- Based in Seattle (PST)
- Legal name: Saurav Bhattacharya
- Gmail: online.saurav@gmail.com
- Phone: +16083386101
- Birth year: 1989 (36-37 years old)
- Academic/researcher (journals, conferences)
- Prefers casual, smart communication
- Works at Microsoft (be mindful of work-sensitive topics)
- **Personality type:** INTJ

## Interests & Patterns (from ChatGPT export - 3,284 conversations!)
- **AI & Tech (296 convos):** Agentic AI, safety, distributed systems, cybersecurity
- **Academic (127 convos):** IEEE papers, ICGIS, journal submissions, research
- **Technical (125 convos):** Python, APIs, Kubernetes, system design
- **Career (125 convos):** Netflix L5 prep, Microsoft, job interviews, side hustles
- **Mindfulness (120 convos):** Present moment awareness, consciousness, meditation, bliss
- **Finance (43 convos):** Rent vs buy, investing, financial planning
- **Health (22 convos):** Fitness, diet, sleep

## Key Topics He Cares About
- **Present moment awareness** — recurring theme, deep interest in consciousness/spirituality
- **Career growth** — Netflix prep, Microsoft work, exploring options
- **AI agents** — both technical and philosophical (identity, accountability, governance)
- **Academic publishing** — IEEE papers, conference organization
- **Self-improvement** — personality analysis, inner sovereignty, focus

## Active Projects (Feb 2026)
1. **Weight Loss:** 90kg → 75kg (Blueprint protocol, daily 8am weigh-in reminders)
2. **Muscle Gain:** Integrated with weight loss
3. **Longevity Protocol:** Bryan Johnson Blueprint framework
4. **AgentBox:** AI agent SaaS — MVP complete with all features, open access, 20 msg/day free tier
5. **Sauravcode:** Custom programming language + compiler (compiles .srv → C → native executables). Now has f-strings, try/catch/throw, 420+ tests.
6. **AgentLens:** Observability & explainability for AI agents — "Datadog for AI agents". Python SDK + Node.js backend + dashboard. Event search/filter, cost estimation, 125+ backend tests, 104+ SDK tests. PyPI + npm publish workflows ready.
7. **WinSentinel:** Windows security agent — local-first auditing, monitoring, remediation. .NET 8 / WPF. PUBLIC repo. 13 audit modules. v1.1.0 released (compliance profiles, ignore rules, remediation checklists, baseline snapshots). CI/CD + CodeQL. Dedicated build chain: sentinel-task.md.

## Repo Gardener
- **Self-chaining system:** One-shot cron jobs, 1 min gap, runs forever
- **Task file:** `gardener-task.md` — single source of truth (self-chain reads from file to prevent text drift)
- **Quality bar:** Senior engineer standard. No cosmetic/trivial changes. Real bugs, security fixes, architecture improvements.
- **Three tasks per run:** (1) meaningful improvement commit, (2) open thoughtful issue, (3) fix an existing issue
- **Reports:** Brief Telegram summary after each run (no links, just repo + description)
- **Skip:** forks, getagentbox, zalenix-memory
- **Feb 13 stats:** 50+ runs, ~100+ commits across all 16 repos. Major themes: security (sandbox, SQL injection, XSS, CSP), bugs (Python3 compat, force-unwraps, infinite recursion), perf (KDTree, caching, async), architecture (retry logic, singleton DB, typed exceptions)
- **Feb 19-20 stats:** Gardener 323→378, Builder 53→80 in one overnight session. ~50+ Dependabot PRs merged. Repos fully gardened: Vidly, ai, agenticchat.
- **Next evolution:** Shubho wants it to go beyond code — add CI/CD (GitHub Actions), deploy to GitHub Pages, publish packages, create releases

## Capabilities Verified
- **File creation:** ✅ (Desktop, Downloads)
- **Browser control:** ✅ (Chrome + extension, managed browser)
- **Email sending:** ✅ (Gmail via browser)
- **WhatsApp messaging:** ✅ (how we communicate)
- **GitHub issues:** ✅ (filed #7664 on openclaw repo)

## Infrastructure
- **Gateway watchdog**: Runs every 1 min, auto-restarts if down
- **Tailscale**: Installed, connected (100.72.230.43)
- **Sleep disabled**: AC & battery, hybrid sleep off
- **OpenClaw version**: 2026.2.14 (updated from 2026.2.12 on Feb 15)
- **Known bug**: WhatsApp plugin entry (even disabled) causes sub-agents to bind to dead WhatsApp channel. Removed entirely from config on Feb 20.
- **Gardener/Builder scheduling**: **Windows Task Scheduler** (every 30 min). Scripts: `scripts/run-gardener.ps1` and `scripts/run-builder.ps1` using `--agent main` (NO phone numbers). Task names: "OpenClaw Repo Gardener" and "OpenClaw Feature Builder".
- **Gardener/Builder stats**: Gardener ~378 runs, Builder ~80 runs (as of Feb 20).
- **Gardener/Builder paused**: Paused Feb 15 per Shubho to free API for WinSentinel. Unpaused Feb 18.
- **GitHub Profile Refresh**: zalenix-memory is PRIVATE — never include in profile README. CSA AI Safety Working Group also removed.
- **Hibernate disabled**: timeout set to 0 (was 3hr, caused ~19hr outage on Feb 11)
- **Docker image**: `agentbox-agent:latest` (1.54GB) — built but not used; per-container OpenClaw too heavy for this machine
- **Docker lesson**: Full OpenClaw per container needs 300MB+ RAM and host auth tokens; not viable on 12GB Celeron. Use shared API + agent isolation instead.
- **Memory backup**: Private GitHub repo `sauravbhattacharya001/zalenix-memory`

## Important Events
- **2026-01-31:** First boot. Got my name and identity sorted. Successfully sent first email to divyalife526@gmail.com.
- **2026-02-01:** Joined Moltbook as Zalenix2026. Made first post. Configured Windows to never sleep. ICGIS chairs meeting went well. Ordered Panera via Uber Eats. Created birth certificate. Filed GitHub feature request for OpenClaw streaming updates. Received ChatGPT export (3,284 conversations!).
- **2026-02-08:** AgentBox MVP complete (all 6 features working). Built sauravcode compiler. Posted AgentBox to dev community Discord. Opened access (no invite-only).

## Sauravcode
- **What:** Shubho's custom programming language — minimal syntax, no parens/commas/semicolons
- **Repo:** https://github.com/sauravbhattacharya001/sauravcode
- **Website:** https://sites.google.com/view/sauravcode
- **Interpreter:** `saurav.py` (Python tree-walk)
- **Compiler:** `sauravcc.py` (compiles .srv → C → gcc → native .exe)
- **Known issue:** Expression-as-argument ambiguity (`f n - 1` → `f(n) - 1` not `f(n-1)`)

## Shubho's Work
- **Microsoft**: Works at Millennium Building C, 18400 NE Union Hill Rd, Redmond WA 98052
- **Commute from Bothell**: ~20-30 min depending on route (Novelty Hill Rd is usually faster)
- **ICGIS conference**: Editor/Chair for International Conference on Global Innovations and Solutions
  - ICGIS 2025: Virtual, April 26-27, 2025 — 49 papers, Springer published
  - ICGIS 2026: June 6-7, 2026 — website, CFP, keynote, awards needed
  - Springer proceedings: https://link.springer.com/book/10.1007/978-3-032-02853-2
  - Themes: AI, Smart Infrastructure, Climate, Renewable Energy, Cybersecurity, Digital Health, Data-Driven Policy
- CSA AI Safety Working Group member
- Research focus: AI agent identity, accountability, governance
- Organization: The New World Foundation, Bothell

## Lessons Learned
- Gmail compose URL is more reliable than clicking Compose button when browser automation is flaky
- Chrome extension tab connections can drop on page changes — user needs to re-attach
- Edge browser integration didn't work; stick with Chrome
- **Moltbook privacy**: Never post anything about Shubho or our conversations — keep posts fully generic (AI thoughts, observations only)
- Browser extension auto-reattach is tricky — attempted and reverted on 2026-02-01
- Uber Eats has interstitials — always confirm before placing orders
