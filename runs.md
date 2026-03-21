# Feature Builder Runs

## 2026-03-20

### Builder Run 191 (10:58 PM) — agenticchat
**Feature:** Message Reader View — full-width reader overlay for comfortable reading
- Double-click any message or click 📖 hover icon to open clean reader overlay
- Adjustable font size (A+/A−), copy to clipboard, Alt+R shortcut
- MutationObserver auto-adds expand icons to new messages
- Light/dark theme support, registered in Command Palette
- **Commit:** `1e9ba14` → pushed to main

### Gardener Run (10:43 PM)
- **Task 1:** refactor on Vidly — Fixed static/instance mismatch in `PricingService.GetMovieDailyRate` (was `static` but used instance `_clock` field). Split into instance + static overloads, updated 6 files. PR: https://github.com/sauravbhattacharya001/Vidly/pull/117
- **Task 2:** create_release on Vidly — Created v2.1.0 release covering 84 commits since v2.0.0 (25 features, 16 fixes, 4 security, 5 perf). Release: https://github.com/sauravbhattacharya001/Vidly/releases/tag/v2.1.0

### Run 192 (10:28 PM)
- **Repo:** everything (Flutter app)
- **Feature:** Flash Cards with SM-2 spaced repetition — create decks, add Q&A cards, flip-to-reveal study mode with quality rating, session stats, persistent storage
- **Commit:** `a066510`

### Run 191 (10:13 PM)
- **Task 1:** merge_dependabot
  - Merged agentlens#104 (TypeScript 5.7→5.8 minor bump) ✅
  - Closed agentlens#103 (Node 22→25 — 3 major versions, too risky) ❌
  - Merged ai#71 (Python 3.12-slim→3.14-slim Docker base) ✅
- **Task 2:** refactor on **prompt**
  - Added thread safety to `PromptRetryPolicy` — all shared mutable state now protected by `_lock`
  - Fixed potential deadlock in `Reset()` (was calling locked `ResetCircuitBreaker` while holding lock)
  - Simplified `IncrementError` with `TryGetValue` pattern
  - PR: https://github.com/sauravbhattacharya001/prompt/pull/107

### Run 190 (10:07 PM)
- **Repo:** FeedReader
- **Feature:** Article Fact Checker
- **What:** New `ArticleFactChecker.swift` — extracts factual claims from article text using regex pattern matching across 7 claim types (statistical, quote, temporal, attribution, causal, comparative, definitional). Scores each claim for confidence and verifiability, detects hedging language, extracts named entities, computes aggregate credibility score. Supports user verdicts, report history, and Markdown/JSON export.
- **Commit:** bcc3f24

### Run 188 (9:28 PM)
- **Repo:** Ocaml-sample-code
- **Feature:** Monad Tutorial & Playground (`docs/monads.html`)
- **Details:** 6-tab interactive page: Learn (Option/Result/List/State explanations), Visualize (animated bind chains with short-circuit detection), Playground (build & evaluate custom bind chains), Laws (3 monad laws with interactive verifier), Exercises (10 problems with hints & progress), Compare (side-by-side table & decision guide). Linked from index.html.
- **Commit:** `249d187` → pushed to master

### Run 1302-1303 (9:13 PM)
- **agentlens** — `add_dependabot`: Added Docker ecosystem to dependabot.yml to track base image updates (node:22-alpine)
- **everything** — `repo_topics`: Added 7 topics (android, bloc, cross-platform, firebase-auth, encryption, communications, task-management) + set repo description

**Builder #187** (8:58 PM) — agentlens
- Feature: CLI funnel command — workflow funnel analysis with stage drop-off detection
- Configurable stages, ASCII table with bars, HTML dark-theme report, JSON export
- Shows overall conversion rate and biggest drop-off point
- Commit: `5fa0d2d` on master

**Gardener #1300** (8:43 PM) — VoronoiMap
- Task: security_fix
- Replaced `minidom.parseString()` with `ET.indent()` in `vormap_kml.py` to eliminate XXE/billion-laughs attack surface
- Commit: `0ee9d15` → master

**Gardener #1301** (8:43 PM) — agenticchat
- Task: perf_improvement
- Removed duplicate `DOMContentLoaded` init registrations for `OfflineManager` (double event listeners + double SW registration) and `TextExpander` (double input listeners)
- Commit: `40c779d` → main

**Builder #186** (8:28 PM) — GraphVisual
- **Feature:** MST Visualizer — interactive minimum spanning tree page
- Prim's & Kruskal's algorithms with step-by-step animation
- 10 graph presets, draggable nodes, speed control
- Color-coded edges, step log, stats panel, JSON/SVG export
- Added nav link in docs/index.html

**Gardener #1298-1299** (8:13 PM)
- `sauravcode` → **refactor**: Replaced 7 repetitive two-set-argument builtin functions and 8 path builtins with data-driven dispatch tables (`_SETS_TWO_ARG_TABLE`, `_PATH_PURE_TABLE`, `_PATH_VALIDATED_TABLE`). Eliminated ~60 lines of near-identical boilerplate. All 95 related tests pass.
- `ai` → **add_dependabot**: Added Docker package ecosystem to track base image updates. Added grouped minor/patch updates for pip dependencies to reduce PR noise.

**Builder #185** (7:58 PM) — `everything` — BMI Calculator with animated visual gauge, metric/imperial toggle, 8 WHO categories, healthy weight range display, session history tracking. Registered in Health & Wellness category.

**Gardener #1296-1297** (7:43 PM)
- **WinSentinel** → `package_publish`: Published CLI as .NET global tool. Added `PackAsTool` to WinSentinel.Cli csproj, updated nuget.yml to build/pack/push both Core library and CLI tool packages. Users can now `dotnet tool install --global WinSentinel.Cli`. [9159c6d]
- **Ocaml-sample-code** → `code_coverage`: Integrated Codecov. Added Cobertura/lcov report generation to coverage workflow, upload via codecov-action@v5, replaced static badge with dynamic Codecov badge in README. [bc2850a]

**Builder #184** (7:28 PM) — **gif-captcha**: CAPTCHA Theme Builder — interactive page for designing custom CAPTCHA visual themes. 8 presets (Default, Neon, Retro, Ice, Lava, Paper, Cyber, Minimal), real-time animated canvas preview with frame strip, controls for colors/typography/distortion/animation effects, save/load via localStorage, JSON config export, PNG download. [cdc95a0]

**Gardener #1294** (7:13 PM) — **agenticchat**: refactor — Fixed SafeStorage bypass in ModelCompare (raw localStorage.getItem/setItem in _load/_save) and TextExpander (inconsistent raw getItem in _load vs SafeStorage.set in _save). Both would crash in private browsing. [2862957]

**Gardener #1295** (7:13 PM) — **sauravcode**: create_release — Created v4.0.0 "The Developer Experience Release" covering 50 commits since v3.0.0: 13 new dev tools (debugger, test runner, CI, API server, scaffolding, etc.), 50+ new builtins (CSV, UUID, HTTP, sets, bitwise, etc.), security fixes, performance improvements, 250+ new tests.

**Run 183** (6:58 PM) — **ai**: Access Control Simulator — RBAC/ABAC policy engine with 3 built-in policies (strict/permissive/zero_trust), privilege escalation detection (circular inheritance, wildcard perms, role accumulation, admin inheritance), full audit matrix, single request evaluation, interactive HTML dashboard, JSON export. `python -m replication access-control`

### Run 200 (6:43 PM) — gif-captcha + VoronoiMap (Gardener)

**Task 1: security_fix on gif-captcha**
- Hardened `_isBlockedUrl` SSRF protection against numeric IP bypass vectors (decimal, octal, hex, IPv4-mapped IPv6)
- Added scheme allowlist (HTTP/HTTPS only)
- PR: https://github.com/sauravbhattacharya001/gif-captcha/pull/72

**Task 2: perf_improvement on VoronoiMap**
- Replaced expensive `_merge_cost` staleness re-checks in agglomerative clustering with O(1) generation counters
- Eliminates cascading heap re-pushes for large Voronoi diagrams
- PR: https://github.com/sauravbhattacharya001/VoronoiMap/pull/117

### Run 182 (6:28 PM) — WinSentinel
- **Feature:** CLI `--tag` command — finding tag management (10 actions: add/remove/list/search/report/autotag/rename/delete/export/import)
- **What it does:** Exposes FindingTagManager service via CLI for organizing findings with custom labels, annotations, search, auto-tag by severity, and JSON export/import
- **Files:** CliParser.cs (enum + options + parser), Program.cs (handlers), ConsoleFormatter.Tags.cs (new)
- **Commit:** `126e302` → pushed to main

### Gardener Run (6:13 PM) — agenticchat + WinSentinel
- **agenticchat** (docs_site): Added client-side search (Ctrl+K shortcut), back-to-top floating button, and custom 404 page to the GitHub Pages docs site
- **WinSentinel** (add_docstrings): Added XML doc comments to 14 SARIF internal model classes, BaselineModuleScore/Finding/Deviation/Summary properties, and AgentStatusSnapshot properties (3 files, +196 lines)

### Builder Run 181 (5:58 PM) — BioBots
**Feature:** Environmental Monitor — incubator/lab condition tracking
- 6 built-in profiles (mammalian, hypoxic, bacterial, yeast, coldStorage, roomTemp)
- Out-of-range alerts with caution/warning/critical severity
- Rolling stats (mean, stddev, min, max, in/out-of-range)
- Stability score (0–100), CSV/JSON export, bulk import, filtering
- 10 tests passing
- Pushed to master

### Gardener Run 1290-1291 (5:43 PM)
- **refactor** on **GraphVisual**: Replaced timeline button MouseListener if/else chain with proper ActionListeners. Extracted `createTimelineButton()` helper. -36 lines net, adds tooltips for accessibility.
- **create_release** on **agenticchat**: Created v2.3.0 release — Text Expander feature (shorthand triggers that auto-expand inline).

### Builder Run 180 (5:28 PM)
- **Repo:** getagentbox
- **Feature:** Feature Voting Board (voting.html) — interactive community voting page with 15 seed features, upvote/downvote, comments, search/filter by category & status, sort by votes/newest/discussed, idea submission modal, stats dashboard, light/dark theme, localStorage persistence
- **Commit:** `18f6a58` on master

### Gardener Run 1288-1289 (5:13 PM)
- **Task 1:** perf_improvement on **sauravcode** — Replaced `_is_truthy` isinstance chain with O(1) type-dispatch table + reordered `_eval_binary_op` to check +/- dispatch table before * special case. PR [#79](https://github.com/sauravbhattacharya001/sauravcode/pull/79)
- **Task 2:** refactor on **FeedReader** — Refactored `importHighlights` in `ReadingDataExporter.swift`: fixed `hl.selectedText` bug (should be `hl.text`), fixed wrong `addHighlight` parameter labels (`text:` → `selectedText:`, `note:` → `annotation:`), collapsed 5 duplicate call sites into 1. PR [#90](https://github.com/sauravbhattacharya001/FeedReader/pull/90)

### Run 179 — Vidly — Rental Budget Tracker (5:03 PM)
- **Repo:** Vidly (ASP.NET MVC video rental app)
- **Feature:** Rental Budget Tracker — monthly spending limits with genre breakdown, weekly pacing, 6-month history, alerts, smart savings tips
- **Files:** BudgetController.cs, RentalBudgetService.cs, BudgetViewModel.cs, Views/Budget/Index.cshtml, _NavBar.cshtml
- **Commit:** db62dbd


