"""Weather signal backtest: taker burst reversal with smart 2-phase exit."""

import sys
sys.path.insert(0, r"D:\MyAI\My work team\Decided plan")

from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

import logging
logging.disable(logging.WARNING)

import numpy as np
import pandas as pd
from core.feature_engine import FeatureEngine

print("[1/3] Loading data...")
fe = FeatureEngine(storage_path="data/storage")
df = fe.load_date_range("2026-02-04", "2026-04-04")
print(f"  {len(df):,} bars loaded")

close = df["close"].values.astype(float)
high = df["high"].values.astype(float)
low = df["low"].values.astype(float)
taker = df["taker_buy_sell_ratio"].values.astype(float)
amp = df["amplitude_1m"].values.astype(float)
vol = df["volume_vs_ma20"].values.astype(float)

print("[2/3] Computing rolling percentiles...")
LOOKBACK = 120
taker_p95 = pd.Series(taker).rolling(LOOKBACK, min_periods=60).quantile(0.95).values
taker_p05 = pd.Series(taker).rolling(LOOKBACK, min_periods=60).quantile(0.05).values
amp_p90 = pd.Series(amp).rolling(LOOKBACK, min_periods=60).quantile(0.90).values

# Exit parameters
FEE = 0.04
STOP_PCT = 0.25
HALF_EXIT_TARGET = 0.06
TRAILING_GAP = 0.40
MAX_HOLD = 30
COOLDOWN = 3

print("[3/3] Running backtest...")
print()

trades = []
cooldown_counter = 0

for i in range(LOOKBACK + 300, len(df) - MAX_HOLD - 1):
    if cooldown_counter > 0:
        cooldown_counter -= 1
        continue

    if np.isnan(taker[i]) or np.isnan(amp[i]) or np.isnan(taker_p95[i]):
        continue

    direction = None
    vol_ok = not np.isnan(vol[i]) and vol[i] > 1.5
    # Persistence: taker must be extreme for 2+ consecutive bars (not just a blip)
    prev_taker_ok = i > 0 and not np.isnan(taker[i-1])
    taker_persist_high = prev_taker_ok and taker[i] > taker_p95[i] and taker[i-1] > 1.0
    taker_persist_low = prev_taker_ok and taker[i] < taker_p05[i] and taker[i-1] < 1.0
    if taker_persist_high and amp[i] > amp_p90[i] and vol_ok:
        direction = "short"
    elif taker_persist_low and amp[i] > amp_p90[i] and vol_ok:
        direction = "long"

    if direction is None:
        continue

    entry_price = close[i]

    # Simulate trade
    half_exited = False
    half_exit_pnl = 0.0
    remaining_peak = 0.0
    trailing_floor = -999.0
    exit_bar = None
    exit_reason = None
    final_pnl = 0.0

    for j in range(1, MAX_HOLD + 1):
        bar = i + j
        if bar >= len(df):
            break

        if direction == "long":
            current_ret = (close[bar] - entry_price) / entry_price * 100
            bar_high_ret = (high[bar] - entry_price) / entry_price * 100
            bar_low_ret = (entry_price - low[bar]) / entry_price * 100
        else:
            current_ret = (entry_price - close[bar]) / entry_price * 100
            bar_high_ret = (entry_price - low[bar]) / entry_price * 100
            bar_low_ret = (high[bar] - entry_price) / entry_price * 100

        # Layer 1: Hard stop
        if bar_low_ret >= STOP_PCT:
            exit_bar = j
            if half_exited:
                exit_reason = "stop_remaining"
                final_pnl = half_exit_pnl * 0.5 + (-STOP_PCT) * 0.5
            else:
                exit_reason = "stop_full"
                final_pnl = -STOP_PCT
            break

        # Layer 2: Half exit at target
        if not half_exited and bar_high_ret >= HALF_EXIT_TARGET:
            half_exited = True
            half_exit_pnl = HALF_EXIT_TARGET
            remaining_peak = current_ret
            trailing_floor = max(0.0, current_ret * (1 - TRAILING_GAP))

        # Layer 3: Signal disappearance (taker reverts to normal)
        if not np.isnan(taker[bar]) and j >= 2:
            taker_reverted = False
            if direction == "short" and taker[bar] < 1.0:
                taker_reverted = True
            elif direction == "long" and taker[bar] > 1.0:
                taker_reverted = True

            if taker_reverted:
                exit_bar = j
                if half_exited:
                    exit_reason = "signal_gone_half"
                    final_pnl = half_exit_pnl * 0.5 + current_ret * 0.5
                else:
                    exit_reason = "signal_gone_full"
                    final_pnl = current_ret
                break

        # Layer 4: Trailing stop on remaining half
        if half_exited:
            if current_ret > remaining_peak:
                remaining_peak = current_ret
                trailing_floor = remaining_peak * (1 - TRAILING_GAP)
            if current_ret <= trailing_floor and trailing_floor > 0:
                exit_bar = j
                exit_reason = "trailing_remaining"
                final_pnl = half_exit_pnl * 0.5 + trailing_floor * 0.5
                break

    if exit_bar is None:
        exit_bar = MAX_HOLD
        exit_reason = "time_cap"
        j_final = min(i + MAX_HOLD, len(df) - 1)
        if direction == "long":
            ret_final = (close[j_final] - entry_price) / entry_price * 100
        else:
            ret_final = (entry_price - close[j_final]) / entry_price * 100
        if half_exited:
            final_pnl = half_exit_pnl * 0.5 + ret_final * 0.5
        else:
            final_pnl = ret_final

    net_pnl = final_pnl - FEE

    trades.append({
        "bar": i,
        "direction": direction,
        "exit_bar": exit_bar,
        "exit_reason": exit_reason,
        "gross_pnl": round(final_pnl, 4),
        "net_pnl": round(net_pnl, 4),
        "half_exited": half_exited,
    })

    cooldown_counter = COOLDOWN

# Results
tdf = pd.DataFrame(trades)
print(f"Total trades: {len(tdf)}")
if len(tdf) == 0:
    print("No trades generated!")
    sys.exit(0)

wins = tdf["net_pnl"] > 0
days = 59
print(f"Win rate:       {wins.mean()*100:.1f}%")
print(f"Avg net PnL:    {tdf['net_pnl'].mean():+.4f}%")
print(f"Total net PnL:  {tdf['net_pnl'].sum():+.3f}%")
print(f"Avg hold bars:  {tdf['exit_bar'].mean():.1f}")
print(f"Half-exit rate: {tdf['half_exited'].mean()*100:.1f}%")
print(f"Trades/day:     {len(tdf) / days:.1f}")
print()

print("=== BY DIRECTION ===")
for d in ["long", "short"]:
    sub = tdf[tdf["direction"] == d]
    if len(sub) == 0:
        continue
    wr = (sub["net_pnl"] > 0).mean() * 100
    print(f"  {d:6s}: n={len(sub):4d}, WR={wr:.1f}%, avg={sub['net_pnl'].mean():+.4f}%, total={sub['net_pnl'].sum():+.3f}%")

print()
print("=== BY EXIT REASON ===")
for reason in sorted(tdf["exit_reason"].unique()):
    grp = tdf[tdf["exit_reason"] == reason]
    wr = (grp["net_pnl"] > 0).mean() * 100
    print(f"  {reason:25s}: n={len(grp):4d}, WR={wr:.1f}%, avg={grp['net_pnl'].mean():+.4f}%")

print()
print("=== VS BASELINE (fixed 10-bar hold) ===")
baseline_pnl = []
for _, t in tdf.iterrows():
    bi = int(t["bar"])
    ep = close[bi]
    if bi + 10 < len(df):
        if t["direction"] == "long":
            ret = (close[bi + 10] - ep) / ep * 100 - FEE
        else:
            ret = (ep - close[bi + 10]) / ep * 100 - FEE
        baseline_pnl.append(ret)
baseline_pnl = np.array(baseline_pnl)
bwr = (baseline_pnl > 0).mean() * 100
print(f"  Baseline: WR={bwr:.1f}%, avg={baseline_pnl.mean():+.4f}%, total={baseline_pnl.sum():+.3f}%")
print(f"  Smart:    WR={wins.mean()*100:.1f}%, avg={tdf['net_pnl'].mean():+.4f}%, total={tdf['net_pnl'].sum():+.3f}%")
