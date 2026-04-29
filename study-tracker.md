# LC/SD Study Tracker

## Progress
- **Start date:** April 21, 2026
- **Target:** Meta E5/E6 interview prep
- **Approach:** FEYNMAN-LEVEL. Understand so deeply that coding is trivial.

## Phased Plan

### Phase 1: Grokking (May–June 2026) — BEFORE Piku arrives in July
- **Goal:** Complete all LC patterns + SD components. Pure understanding, no performance pressure.
- **Mon-Fri 7 PM PST:** LeetCode patterns — conceptual drops + interactive deep dives
- **Sat-Sun 10 AM PST:** System Design component deep dives
- **LC:** ~17 patterns remaining, ~3-4 weekday sessions each = fits in 13 weeks ✅
- **SD:** ~19 components, grouped into ~14 weekend sessions = fits in 13 weeks ✅
- **Deadline:** End of June / early July (before Piku)

### Phase 2: Rest (July 2026)
- First month with Piku 💛. Light review only. No pressure.

### Phase 3: Performance (Aug–Oct 2026)
- Problem solving under time pressure (20-25 min per LC problem)
- Mock interviews (LC + SD + Behavioral)
- Behavioral prep (leadership stories from ICGIS, Microsoft, EB-1A coaching)
- Full system design compositions ("Design Instagram" etc.) using grokked building blocks

## Schedule
- **Mon-Fri 7 PM PST:** LeetCode pattern drops
- **Sat-Sun 10 AM PST:** SD component deep dives

## Covered Topics

### LeetCode Patterns
- 2026-04-21 — Sliding Window — Day 1: Core intuition, grow/shrink, caterpillar analogy, queue connection, LP-in-1D insight, Kadane's, negative numbers breaking monotonicity
- 2026-04-22 — Sliding Window — Day 2: Longest vs shortest (opposing pointer triggers), monotonic property requirement, LC #3 (longest substring no repeats — set), LC #159 (at most K distinct — hashmap), hashmap vs set bookkeeping
- 2026-04-27 — Two Pointers — Day 1: ⚠️ PENDING DELIVERY (saved to lc-blurb-pending.md, retry failed 2026-04-28) — opposite-direction pointers, mountain analogy, monotonicity as core requirement, connection to sliding window, Container With Most Water walkthrough, 3Sum as exercise

### System Design Components
- 2026-04-26 — Rate Limiter (mentioned only, not deep-dived)
- 2026-04-27 — Rate Limiter ✅ — four algorithms (fixed window, sliding window counter, token bucket, leaky bucket), burst handling (window = volume control, bucket = spike control), distributed (Redis single-region default, local counters + sync for multi-region), placement (API gateway / middleware / sidecar), fail-open vs fail-closed, 429 response headers (Retry-After, Remaining, Reset)

## Completed Patterns
- **Sliding Window** ✅ GROKKED
  - Key insights: caterpillar analogy, queue structure, "LP applied to 1D arrays", opposing pointer triggers for longest vs shortest, monotonic property requirement, hashmap bookkeeping

## Completed SD Components
- **Rate Limiter** ✅ GROKKED
  - Algorithms: fixed window (counter+reset), sliding window counter (N buckets + running sum), token bucket (prepaid balance + refill), leaky bucket (queue + constant drain)
  - Burst insight: windows control volume, buckets control spikes. Layer when business rules ≠ infra protection.
  - Distributed: Redis in same region is default (shared counter). Local counters with split limits only when accuracy doesn't matter or extreme scale. Multi-region = Redis per region + async sync.
  - Concurrency limiter ≠ rate limiter (in-flight vs history)
  - Placement: API gateway (infra), middleware (business logic), sidecar (microservices)
  - Fail-open (most) vs fail-closed (security-critical)
  - 429 + headers: Retry-After, X-RateLimit-Remaining, X-RateLimit-Reset

## SD Components to Cover (grouped for efficiency)
1. Load Balancer
2. Consistent Hashing + Sharding/Partitioning
3. Caching (layers, eviction, invalidation)
4. Message Queues + Pub/Sub
5. CDN
6. Database Replication
7. Consensus (Paxos/Raft) + Leader Election
8. Service Discovery
9. API Gateway + Reverse Proxy
10. Circuit Breaker + Heartbeat/Failure Detection
11. Bloom Filters
12. Write-Ahead Log + SSTable/LSM Tree
13. Connection Pooling

~14 weekend sessions (some grouped pairs = single session)

## LC Patterns to Cover
Two Pointers, Binary Search variations, BFS/DFS, Topological Sort, Union-Find, Trie, Monotonic Stack/Queue, Dynamic Programming (1D, 2D, interval, bitmask), Graph algorithms, Greedy, Backtracking, Segment Tree/BIT, String matching (KMP, Rabin-Karp), Prefix Sum, Heap/Priority Queue, Intervals
