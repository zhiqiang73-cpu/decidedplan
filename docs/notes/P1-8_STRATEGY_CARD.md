# Strategy Card: P1-8 VWAP偏离+量枯竭

> Status: legacy research note
> Scope: preserve an old fixed-hold benchmark only
> Production truth source: `LIVE_STRATEGY_LOGIC.md` + `monitor/smart_exit_policy.py`

## What This Note Is

This file keeps an old benchmark that once tested whether `90 bars` of passive holding could harvest the long reversion arc in `P1-8`.

That benchmark is **not** the live doctrine.

## What Live Uses Now

| Layer | Production meaning |
|---|---|
| Entry | VWAP displacement + volume drought captures a temporary imbalance |
| Main exit | Hardcoded `vs_entry` combos in `monitor/smart_exit_policy.py` |
| Lifecycle overlay | Mechanism decay can tighten or terminate the trade |
| Profit guard | Trailing floor after enough MFE |
| Final fallback | `safety_cap`, not a recommended fixed hold |

## Legacy Benchmark Snapshot

| Metric | Old benchmark observation |
|---|---|
| Observation window | `120 bars` |
| Typical MFE peak | around `68.8 bars` |
| Old passive-hold candidate | `90 bars` |
| Purpose | Compare a dumb baseline against causal exits |

## Interpretation Rule

When you read "Hold `90 bars`" in older notebooks or scripts, translate it as:

> "This was a research baseline used to test whether the entry had directional edge over a long repair arc."

Do **not** translate it as:

> "Live P1-8 should always exit after `90 bars`."

## Guardrail

If any future doc or agent tries to present this note as the production exit logic, treat that as doctrine drift and route back to `LIVE_STRATEGY_LOGIC.md`.
