# Pending LC Blurb — Two Pointers Day 1
**Generated:** 2026-04-27 7:10 PM PST
**Status:** ⚠️ Could not send — no messaging channels (Telegram/WhatsApp/Discord) are configured.

---

🧠 **LC Pattern: Two Pointers — Day 1**
_Why do two indices solve what one can't?_

📦 **Start with a raw problem:**
You have a SORTED array. Find two numbers that sum to a target. Brute force? Check every pair → O(n²). Gross.

But wait — the array is sorted. That's a superpower.

🏔️ **The Mountain Analogy:**
Imagine you're standing at both ends of a mountain ridge — one person at the lowest point (left), one at the highest (right). You're trying to find a pair of elevations that sum to exactly X.

• Sum too small? The left person walks right (bigger numbers)
• Sum too big? The right person walks left (smaller numbers)
• Each step eliminates an entire row/column of possibilities

You never backtrack. Each pointer moves only inward. That's **O(n)**.

🔑 **WHY it works — the key insight:**
Two pointers exploit **monotonicity** — when moving one pointer in a direction has a PREDICTABLE effect on the result. Sorted order guarantees: move left pointer right → sum increases. Move right pointer left → sum decreases.

This is the same "monotonic property" we needed for sliding window! Two Pointers is the **generalization** — sliding window is just two pointers where both move the same direction.

📐 **Two flavors:**
1. **Opposite-direction** (converging) — start at both ends, meet in middle. Used when sorted input lets you eliminate from both sides. (Two Sum II, Container With Most Water)
2. **Same-direction** (fast/slow) — both start at left. Used for in-place modification, cycle detection. (Remove Duplicates, Linked List Cycle)

Today = opposite-direction. Tomorrow = same-direction.

🔍 **Walkthrough — Container With Most Water (LC #11):**
Array of heights: [1, 8, 6, 2, 5, 4, 8, 3, 7]
Area = min(height[L], height[R]) × (R - L)

Start: L=0, R=8 → min(1,7) × 8 = 8
Which pointer moves? The SHORT one. Why? Moving the tall one can only decrease width AND can't increase the min-height. Moving the short one MIGHT find something taller.

L=1, R=8 → min(8,7) × 7 = 49 ✅ better
L=1, R=7 → min(8,3) × 6 = 18
...keep going until L meets R.

The greedy choice: **always move the shorter side** — it's the bottleneck.

✏️ **Your turn:**

**3Sum (LC #15):** Given an unsorted array, find ALL unique triplets that sum to zero. No duplicate triplets allowed.

Hints: sorting is your friend. Think about how to reduce 3Sum to a problem you now know how to solve.

What's your conceptual approach?
