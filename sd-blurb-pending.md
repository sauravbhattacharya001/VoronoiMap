# SD Blurb Pending Delivery — Load Balancer
**Generated:** 2026-05-03 10:00 AM PST
**Reason:** Telegram channel not configured — could not deliver

---

⚙️ **SD Deep Dive: Load Balancer**
Sunday morning component drop ☕

━━━━━━━━━━━━━━━━━━━━

🔴 **THE PAIN: What breaks without it?**

You have one server. Traffic spikes. That server melts. Even if you add more servers, clients only know one IP address. So 100% of traffic still hits server-1 while servers 2, 3, 4 sit idle.

The raw problem: **how do N clients discover and spread across M servers?**

Without a load balancer, you'd need every client to know every server's address AND agree on how to split work. That's impossible at internet scale.

━━━━━━━━━━━━━━━━━━━━

🧱 **First Principles: Deriving the mechanism**

Start simple. You have a single entry point (one IP/DNS). Behind it, multiple servers. You need something in between that:
→ Accepts all inbound connections
→ Picks a backend server
→ Forwards the request
→ Returns the response

That's it. A load balancer is a **reverse proxy that makes a routing decision per request.**

The interesting part is the **picking algorithm.**

━━━━━━━━━━━━━━━━━━━━

🎯 **Algorithms (from dumb to smart)**

1️⃣ **Round Robin** — server 1, 2, 3, 1, 2, 3...
Simple. Works when all servers are identical and all requests cost the same. (They rarely do.)

2️⃣ **Weighted Round Robin** — server 1 gets 3x traffic, server 2 gets 1x
Use when servers have different capacities.

3️⃣ **Least Connections** — route to whichever server has fewest active connections right now
Adapts to reality: if server 2 is handling a slow query, it naturally gets fewer new ones. This is the first "smart" algorithm.

4️⃣ **Least Response Time** — like least connections but factors in latency
Even smarter, but requires the LB to track response times (more state, more overhead).

5️⃣ **IP Hash / Consistent Hashing** — hash(client IP) → server
Guarantees same client hits same server. Useful for session affinity. (We'll connect this to consistent hashing next week.)

**Key insight:** There's a spectrum from **stateless** (round robin — no memory needed) to **stateful** (least connections — must track active connections). More state = smarter routing = more overhead on the LB itself.

━━━━━━━━━━━━━━━━━━━━

🏗️ **Physical Analogy**

Airport check-in counters. You arrive, there's a person at the front directing you. They could:
→ Point you to counter 1, next person to 2, etc. (round robin)
→ Look at which line is shortest (least connections)
→ Notice counter 3 has the fast agent (least response time)
→ Send all business class to counter 1 (weighted)

Without that person? Everyone crowds counter 1 because it's closest to the door.

━━━━━━━━━━━━━━━━━━━━

📍 **Layers: L4 vs L7**

**L4 (Transport)** — routes based on IP + port. Fast. Doesn't read the request body. Think: TCP packet forwarding. Can handle millions of connections/sec.

**L7 (Application)** — reads HTTP headers, URL path, cookies. Can route /api to backend-A and /images to backend-B. Slower but way more powerful.

**When to use which?**
→ L4 when you just need to spread TCP connections (databases, game servers, gRPC)
→ L7 when routing decisions depend on content (web apps, API gateways, A/B testing)

Most modern systems use **L7 at the edge** (Nginx, Envoy, ALB) with **L4 underneath** for the heavy lifting (NLB, IPVS).

━━━━━━━━━━━━━━━━━━━━

⚖️ **Tradeoffs**

**Single point of failure** — the LB itself can die. Solution: active-passive LB pair with a floating IP (VIP). If primary dies, secondary takes over. This is why cloud LBs (ALB, NLB) are managed — AWS runs the redundancy for you.

**Added latency** — every request hops through the LB. L4 adds microseconds. L7 adds milliseconds (TLS termination, header parsing).

**Scalability ceiling** — one LB has limits. Solution: DNS-based load balancing as the first tier (Route53 returns different LB IPs), then hardware/software LBs as second tier. This creates a **two-tier architecture**.

**Session affinity vs even distribution** — sticky sessions (same user → same server) break even load distribution. If server 2 gets all the whales, it's overloaded. Best practice: make backends stateless (store sessions in Redis), then you don't need affinity at all.

━━━━━━━━━━━━━━━━━━━━

🔗 **How it connects**

→ **API Gateway** sits in front of or behind the LB. Gateway handles auth, rate limiting, routing; LB handles distribution.
→ **Service Discovery** feeds the LB its list of healthy backends (Consul, etcd, k8s endpoints).
→ **Health checks** — the LB pings backends periodically. Unhealthy? Remove from rotation. This is your first line of failure detection.
→ **CDN** — a CDN IS a geographically distributed load balancer for static content.
→ **Consistent Hashing** — used inside some LBs to minimize disruption when backends are added/removed.

━━━━━━━━━━━━━━━━━━━━

🧠 **Scenario for you, Shubho:**

Your API serves 50k req/sec across 20 servers behind an L7 load balancer using round robin. Suddenly, you deploy a new feature that makes 3 of those 20 servers handle requests that take 10x longer (they hit a slow DB query). Users start complaining about timeouts — but only ~15% of users.

❓ What's happening? What change to the LB would fix it? What's the deeper architectural fix so this can't happen again?

Think it through — answer next time 💪
