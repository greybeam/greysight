# Task 0 Spike Notes — Verify load-bearing Snowflake facts

**Date:** 2026-07-12
**Purpose:** Confirm the two facts Automated Savings (plan.md Tasks 5/6/8) is built on,
BEFORE building on them. Flagged as load-bearing by adversarial review (#3, #4) and Codex.

**Status:** ✅ RUN against a real account. **Task 0 is NOT blocked.** Two decisions below;
one **new blocking defect** found for Task 5/6 (absent cluster columns).

**Environment:**
- Account `GOPGUKF-JO19546`, user `KYLE`, role `ACCOUNTADMIN`
- `snowflake-connector-python` **4.3.0**, key-pair auth (`.env`)
- Warehouse: `COMPUTE_WH` (STANDARD, X-Small, Standard edition)
- Reproduce: `set -a; source .env; set +a` then `uv run python test.py [--measure-suspend COMPUTE_WH --runs N --idle-before S]`

> **⚠️ Methodology note (why this was re-run after a Codex review).** The *first* pass
> reported ~3.7s suspend latency from a single coarse sample and concluded "5 ticks is
> fine." That was wrong on both counts. Rigorous re-measurement (multi-run, idleness-
> verified, two precondition regimes) shows the real story is subtler — see Step 2. The
> lesson: a shipping gate needs the worst case across regimes, not one observation.

---

## Step 1 — `SHOW WAREHOUSES` field types

> **Timestamps are usable as-is (contradicts finding #3's worst case).** On connector
> 4.3.0 `resumed_on`/`created_on` are real **`datetime.datetime`, tz-AWARE (UTC)** — not
> strings. Uptime subtraction works directly.

| Field                | Python type          | tz-aware?          | Example |
|----------------------|----------------------|--------------------|---------|
| `resumed_on`         | `datetime.datetime`  | **tz-AWARE** (UTC) | `2026-07-13 05:53:07.624000+00:00` |
| `created_on`         | `datetime.datetime`  | **tz-AWARE** (UTC) | `2024-08-30 17:06:33.987000+00:00` |
| `auto_suspend`       | `int`                | n/a                | `60` |
| `running`            | `int`                | n/a                | `0` |
| `queued`             | `int`                | n/a                | `0` |
| `started_clusters`   | **ABSENT**           | n/a                | column not returned |
| `min_cluster_count`  | **ABSENT**           | n/a                | column not returned |
| `max_cluster_count`  | **ABSENT**           | n/a                | column not returned |
| `auto_resume`        | `str`                | n/a                | `'true'` (lowercase string, not bool) |
| `type`               | `str`                | n/a                | `'STANDARD'` |
| `state`              | `str`                | n/a                | `'SUSPENDED'` / `'STARTED'` / `'SUSPENDING'` |

Also observed: `quiescing` is `str` (`'0'` when active, `''` when suspended); a transient
`state == 'SUSPENDING'` appears between STARTED and SUSPENDED (parser treats anything
`!= 'STARTED'` as not-startable, so this is fine).

**Full 32-column list:** `name, state, type, size, running, queued, is_default,
is_current, auto_suspend, auto_resume, available, provisioning, quiescing, other,
created_on, resumed_on, updated_on, owner, comment, resource_monitor, actives, pendings,
failed, suspended, uuid, owner_role_type, resource_constraint, generation,
query_throughput_multiplier, max_query_performance_level, disabled_reasons, tables`

### 🚨 NEW BLOCKING DEFECT for Task 5/6 — cluster columns absent → feature never fires

`started_clusters`, `min_cluster_count`, `max_cluster_count` are **not returned** by
`SHOW WAREHOUSES` on this account. **Confirmed cause: these columns only exist on
Enterprise edition and above** (multi-cluster warehouses). On **Standard edition** they are
absent — so this is not account-specific noise, it deterministically affects *every
Standard-edition customer*. Trace it through the plan:

- Task 5 parser defaults (plan.md:961–963): `started_clusters → 0`, `min_cluster_count → 1`.
- Task 6 gate (plan.md:1003, 1094): `should_force_suspend()` requires
  `started_clusters == min_cluster_count`.
- On this account that evaluates **`0 == 1` → False → `should_force_suspend()` is ALWAYS
  False → the worker silently NEVER suspends anything.**

The feature would deploy, poll, and reclaim $0 — with no error. **Fix required in Task 5
before Task 6 is trusted:** when the cluster columns are absent, default
`started_clusters` and `min_cluster_count` to the **same** value (a single-cluster
STANDARD warehouse is safe to suspend), or make the Task 6 gate tolerate absence
explicitly. Add a parser test for the absent-columns row. Because these columns are
Enterprise+ only, a single-cluster default is exactly right for Standard-edition
warehouses (they are always single-cluster and safe to suspend).

---

## Step 2 — Suspend latency (the gating measurement)

Measured two regimes. **The precondition regime is what matters**, because the worker only
lowers `AUTO_SUSPEND` once `uptime >= uptime_floor_seconds (62s)` (plan.md config + Task 6).

### Regime A — fresh resume, `AUTO_SUSPEND=1` immediately (uptime ≈ 0), 3 runs

```
run 1: SUSPENDED after 16.73s   run 2: 20.74s (via a 'SUSPENDING' tick)   run 3: 18.98s
summary: min=16.73  median=18.98  max=20.74   (all idle throughout)
```

This ~17–20s is a **Snowflake minimum-uptime-after-resume floor**: a freshly-resumed
warehouse will not suspend for ~15–20s even when idle with `AUTO_SUSPEND=1`. **The worker
never operates here** — its 62s uptime floor clears this floor before it ever acts.

### Regime B — uptime held ≥65s first, THEN `AUTO_SUSPEND=1` (worker's real precondition), 2 runs

(Script raises `AUTO_SUSPEND=3600`, resumes, idles 65s to accumulate uptime like the
worker, then lowers to 1 and polls.)

```
run 1: SUSPENDED after 0.26s   run 2: 0.26s   (already SUSPENDED by the first poll)
summary: min=median=max = 0.26s
```

**Once past the resume floor, suspend is near-instant (≤0.26s).** Idle-timer + poll phase
add at most ~1s + ~3s → **phase-aware worst case at the real 3s cadence ≈ 3–4s.**

### Cadence compatibility

The 3s worker cadence is compatible: in Regime B the warehouse is already SUSPENDED by the
next tick, so reconcile hits the `SUSPENDED → restore + cooldown` branch, not the HOLD
branch. `max_intent_hold_ticks` is a rarely-fired backstop, not a hot path.

---

## Step 3 — Decisions that fall out

### (a) Must `parse_warehouses` string-parse `resumed_on`?

- **NOT required on this account/connector (4.3.0 returns tz-aware `datetime`).** The
  plan's `_coerce_ts` (plan.md:942) already handles this via its datetime branch.
- **Keep the string-parse fallback as REQUIRED behavior anyway** (per Codex): we exercised
  exactly one connector version + one result path (arrow vs JSON cursor untested), and the
  spec warns strings occur elsewhere. The plan's 4 fallback formats
  (`%Y-%m-%d %H:%M:%S.%f %z`, …) are a reasonable guess but **UNVERIFIED from a real string
  response** — the string format is not confirmed. Keep parser fixtures for tz-aware
  datetime, tz-naive datetime, str, and None.

### (b) `AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS` — the gating number

- **Realistic worker-regime suspend latency: sub-second (~0.26s), phase-aware worst ~3–4s.**
- Gating rule `intent_hold_seconds >= 2–3× latency`: even **1 tick** passes; **R2's default
  5 ticks (15s) is ~15× the realistic latency — adequate with large margin.**
- **Recommended: `AUTO_SAVINGS_MAX_INTENT_HOLD_TICKS = 8` (24s).** Rationale: satisfies the
  rule with huge margin AND dominates the observed ~20s resume-floor (Regime A) as
  defense-in-depth — so the age-backstop can't guillotine an in-flight suspend even if some
  edge path (e.g. a resume landing between the worker's poll and its ALTER, or uptime clock
  skew) puts a warehouse in the resume-floor regime. Still bounds anti-stranding recovery to
  ≤24s. **Keeping R2's 5 (15s) is acceptable** for the designed precondition; 8 is cheap
  insurance. Do **not** set it to the 14–21 ticks Regime A alone would imply — that
  needlessly slows stranding recovery for a regime the worker never enters.

> **Why both regimes had to be measured.** Regime B alone → "1 tick is enough" (too
> aggressive, no floor margin). Regime A alone → "need 14–21 ticks" (too slow, wrong
> regime). The original single crude sample (~3.7s) → coincidentally "5 is fine" for the
> wrong reason. The defensible number (5–8) only emerges from seeing both.

### Gate check

- [x] Suspend latency measured across both precondition regimes (n=3 fresh, n=2 aged), idle-verified
- [x] `intent_hold_seconds ≥ 2–3× latency` holds — 15s (default) or 24s (recommended) ≫ ~0.3s realistic latency
- [x] `resumed_on` typing recorded — tz-aware `datetime`; string-parse fallback kept as required, format UNVERIFIED
- [x] Connection authorized → Task 0 **NOT blocked**
- [ ] **BLOCKING for Task 5/6:** fix absent-cluster-column parser defaults before trusting Task 6's suspend gate
- [ ] Re-measure on the actual deploy target account (esp. Enterprise/multi-cluster) — cluster columns and resume-floor may differ
- [ ] Confirm the `_coerce_ts` string format against a real string-returning connector/result path before relying on it
