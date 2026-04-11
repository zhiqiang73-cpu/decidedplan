# LIVE STRATEGY LOGIC

> Updated: 2026-04-11
> Scope: live monitor + execution truth source
> Priority: when this file conflicts with exploratory notes, this file wins

## One Sentence

**Entry captures a temporary force imbalance. Exit asks whether that force is still alive.**

## Core Doctrine

| Topic | Truth |
|---|---|
| Entry | Detect a microstructure imbalance with a clear physical mechanism |
| Snapshot | At entry, store the feature fingerprint of that force |
| Main exit cause | `vs_entry` deltas and mechanism lifecycle decay |
| Hard stop | Survival constraint only, not alpha |
| `horizon` / `hold_bars` | 研究观察窗（research observation window）与 safety-cap seed，不是固定离场承诺 |
| `min_hold_bars` | Noise guard before dynamic exits, not a standalone exit logic |
| `safety_cap` | Final fallback only; formerly called `time_cap` in legacy code/comments |

## Unified Exit Waterfall

Every bar, the live engine should think in this order:

| Priority | Layer | Meaning |
|---|---|---|
| 1 | Hard stop | Loss reaches the family threshold (`0.30%~1.50%`) |
| 2 | Mechanism lifecycle | The original force is structurally exhausted; decay score says the thesis is ending |
| 3 | Smart exit (`vs_entry`) | Entry force has repaired enough relative to the entry snapshot |
| 4 | Profit protect | MFE has been earned and trailing floor now locks part of that repair |
| 5 | `safety_cap` | Only when all dynamic logic stays silent |

### Runtime Guards

These are implementation guards, not the main exit doctrine:

| Guard | Meaning |
|---|---|
| `min_hold_bars` | Skip dynamic exits for the first few bars to avoid entry-noise shakes |
| `exit_confirm_bars` | Debounce repeated dynamic exit signals |
| Family override | Exceptional temporary wrapper such as a family-specific TP/SL shell; never redefine the global doctrine |

## What `vs_entry` Means

At each new bar:

```text
vs_entry(feature) = current_feature_value - entry_snapshot_feature_value
```

The engine is not asking:

- "Has price moved for N bars?"
- "Has price reached some static level?"
- "Has the clock expired?"

It is asking:

- "Has the exact force I entered on already repaired, weakened, or reversed enough?"

That is why `vs_entry` is mandatory for causal exits. Absolute levels drift across days; the force fingerprint at entry does not.

## Strategy Families: Entry Force and Exit Contract

| Family | Entry force | Live directions | Exit contract |
|---|---|---|---|
| `P0-2` | Funding settlement inventory pressure | `long/short` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `P1-2` | VWAP/TWAP slicing footprint | `long` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `P1-6` | Seller drought near floor | `long` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `P1-8` | VWAP displacement with volume drought | `long/short` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `P1-9` | Compression release at extreme location | `long/short` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `P1-10` | Taker exhaustion / buyer collapse | `long/short` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `P1-11` | High position + negative funding divergence | `short` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `C1` | Funding-cycle oversold rebound | `long` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `RT-1` | Regime phase transition | `long` | Hard stop + mechanism decay + hardcoded `vs_entry` |
| `A*` approved cards | Discovery-approved physical force | per card | Hard stop + card `Top-3 vs_entry` combos + invalidation combos + `safety_cap` fallback |

## Live Implementation Mapping

| Concern | Primary file |
|---|---|
| Live family catalog | `monitor/live_catalog.py` |
| Entry snapshot | `monitor/smart_exit_policy.py` `build_entry_snapshot()` |
| Dynamic exit evaluation | `monitor/smart_exit_policy.py` `evaluate_exit_state()` |
| Full runtime action decision | `monitor/smart_exit_policy.py` `evaluate_exit_action()` |
| Mechanism lifecycle tracking | `monitor/mechanism_tracker.py` |
| Exit parameter loading | `monitor/exit_policy_config.py` |
| Order / position handling | `execution/execution_engine.py` |

## Research Terminology Rules

| Legacy term | Correct interpretation |
|---|---|
| `hold_bars` | Backward-compatible alias; read it as `research_horizon_bars` unless the code clearly says legacy baseline |
| `horizon` | 研究观察窗，用于前向收益挖掘与 safety-cap sizing |
| `fixed hold baseline` | A research benchmark only; never the production exit doctrine |
| `time_cap` | Legacy name; production doctrine should say `safety_cap` |

## Absolute Red Lines

- Do not describe production exits as "hold N bars then close".
- Do not approve a live or approved-card strategy without `vs_entry` or `mechanism_decay` in its exit contract.
- Do not use time features as alpha confirmation.
- Do not present a legacy fixed-hold baseline as if it were the live exit logic.
- Do not treat the 研究观察窗 as a guaranteed holding time.

## Short Reminder For Future Agents

If you are unsure, use this mental model:

> Entry records the force fingerprint.  
> Exit compares the current state with that fingerprint.  
> `safety_cap` exists only because markets sometimes refuse to resolve on schedule.
