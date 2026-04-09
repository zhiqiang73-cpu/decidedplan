# -*- coding: utf-8 -*-
"""
Test 3 LONG-specific exit adaptations to solve fast-feature decay problem.

Problem: 50% revert-to-neutral exits too fast for taker/liq_press (2-3 bars).
Solutions tested:

A) CONFIRM_BARS=3: require 3 consecutive bars of decay before exiting
   (same logic as TREND_UP confirmation — single bar is noise, 3 bars is real)

B) SMOOTHED feature: use 5-bar MA of taker instead of raw value for decay check
   (smooths out bar-to-bar oscillation, only exits on sustained revert)

C) HIGHER decay threshold for LONG: 70% revert instead of 50%
   (gives accumulation more room to develop before calling force dead)

D) COMPOSITE: primary OI decay 50% OR (taker_ma5 < 0.95 AND vol < 0.8)
   (multi-feature confirmation — no single fast feature can force exit)

All tests on SQ-1 and BI-1 which failed with standard 50% decay.
Also re-test OA-1 to see if these improvements help further.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.feature_engine import FeatureEngine

TREND_PRICE_SLOPE_PCT  = 0.002
TREND_DIR_AUTOCORR_MIN = 0.12
TREND_DIR_NET_MIN      = 0.05
TREND_RANGE_HIGH       = 0.75
TREND_LOOKBACK_BARS    = 20
CONFIRM_BARS           = 3
HARD_STOP_PCT          = -0.003
MAKER_FEE_RT           = 0.0004


def compute_trend_up(df):
    n = len(df)
    close = df["close"].values
    dir_ac = df.get("direction_autocorr", pd.Series(np.nan, index=df.index)).values
    dir_net = df.get("direction_net_1m", pd.Series(np.nan, index=df.index)).values
    pos24 = df.get("position_in_range_24h", pd.Series(0.5, index=df.index)).values
    raw = np.zeros(n, dtype=int)
    for i in range(TREND_LOOKBACK_BARS, n):
        up, dn = 0, 0
        fc = close[i - TREND_LOOKBACK_BARS]
        if fc > 0:
            pct = (close[i] - fc) / fc
            if pct > TREND_PRICE_SLOPE_PCT: up += 1
            elif pct < -TREND_PRICE_SLOPE_PCT: dn += 1
        ac = dir_ac[i] if not np.isnan(dir_ac[i]) else 0.0
        nt = dir_net[i] if not np.isnan(dir_net[i]) else 0.0
        if ac > TREND_DIR_AUTOCORR_MIN and nt > TREND_DIR_NET_MIN: up += 1
        elif ac > TREND_DIR_AUTOCORR_MIN and nt < -TREND_DIR_NET_MIN: dn += 1
        rp = pos24[i] if not np.isnan(pos24[i]) else 0.5
        if rp > TREND_RANGE_HIGH: up += 1
        elif rp < (1 - TREND_RANGE_HIGH): dn += 1
        if up >= 2: raw[i] = 1
        elif dn >= 2: raw[i] = -1
    confirmed = np.zeros(n, dtype=int)
    cur = 0; ct = 0; cc = 0
    for i in range(n):
        if raw[i] != cur and raw[i] != 0:
            if raw[i] == ct: cc += 1
            else: ct = raw[i]; cc = 1
            if cc >= CONFIRM_BARS: cur = raw[i]; cc = 0; ct = 0
        elif raw[i] == cur: cc = 0; ct = 0
        confirmed[i] = cur
    return confirmed


def simulate(df, entry_mask, exit_func, cooldown=5, time_cap=60, label=""):
    """Generic trade simulator with pluggable exit function."""
    close = df["close"].values
    n = len(close)
    trades = []
    last_exit = -cooldown

    for ei in np.where(entry_mask.values)[0]:
        if ei - last_exit < cooldown or ei >= n - 2:
            continue
        ep = close[ei]
        max_p = ep

        exit_bar = None
        exit_reason = None

        for j in range(1, time_cap + 1):
            bi = ei + j
            if bi >= n:
                exit_bar = bi - 1; exit_reason = "data_end"; break
            cp = close[bi]
            ret = (cp - ep) / ep
            max_p = max(max_p, cp)
            mfe = (max_p - ep) / ep

            if ret <= HARD_STOP_PCT:
                exit_bar = bi; exit_reason = "hard_stop"; break
            if mfe > 0.0015 and ret < 0.0002:
                exit_bar = bi; exit_reason = "mfe_ratchet"; break

            reason = exit_func(ei, bi, df)
            if reason:
                exit_bar = bi; exit_reason = reason; break

        if exit_bar is None:
            exit_bar = min(ei + time_cap, n - 1)
            exit_reason = "time_cap"

        xp = close[exit_bar]
        gross = (xp - ep) / ep
        net = gross - MAKER_FEE_RT
        trades.append({
            "entry_bar": ei, "exit_bar": exit_bar,
            "gross_ret": gross, "net_ret": net,
            "hold_bars": exit_bar - ei, "exit_reason": exit_reason,
        })
        last_exit = exit_bar

    return pd.DataFrame(trades)


def report(tdf, label, is_end):
    for period, sub in [("IS", tdf[tdf["entry_bar"] < is_end]),
                        ("OOS", tdf[tdf["entry_bar"] >= is_end])]:
        if sub.empty:
            print(f"  {label} [{period}]: 0 trades")
            continue
        n = len(sub)
        nwr = (sub["net_ret"] > 0).mean()
        an = sub["net_ret"].mean()
        ah = sub["hold_bars"].mean()
        wins = sub[sub["net_ret"] > 0]["net_ret"].sum()
        losses = abs(sub[sub["net_ret"] <= 0]["net_ret"].sum())
        pf = wins / losses if losses > 0 else float('inf')
        reasons = sub["exit_reason"].value_counts()
        rs = ", ".join(f"{r}={c}" for r, c in reasons.items())
        print(f"  {label} [{period}] n={n} NetWR={nwr:.1%} AvgNet={an:.4%} PF={pf:.2f} Hold={ah:.1f} | {rs}")


def main():
    print("Loading features...")
    fe = FeatureEngine(storage_path="data/storage")
    df = fe.load_date_range("2026-02-18", "2026-04-07", include_heavy=True)
    print(f"Loaded {len(df):,} bars")

    trend_arr = compute_trend_up(df)
    df["trend_up"] = trend_arr == 1

    n = len(df)
    is_end = int(n * 0.6)

    # Pre-compute smoothed features
    df["taker_ma5"] = df["taker_buy_sell_ratio"].rolling(5, min_periods=1).mean()
    df["liq_press_ma5"] = df["btc_liq_net_pressure"].rolling(5, min_periods=1).mean()
    df["vol_ma5"] = df["volume_vs_ma20"].rolling(5, min_periods=1).mean()
    df["oi5_ma5"] = df["oi_change_rate_5m"].rolling(5, min_periods=1).mean()

    taker = df["taker_buy_sell_ratio"].fillna(1.0)
    oi5 = df["oi_change_rate_5m"].fillna(0)
    vol_ma = df["volume_vs_ma20"].fillna(1.0)
    liq_press = df["btc_liq_net_pressure"].fillna(0)

    # ===================================================================
    # BI-1: TREND_UP + taker > 1.15 + vol > 1.5
    # ===================================================================
    bi1_mask = df["trend_up"] & (taker > 1.15) & (vol_ma > 1.5)
    print(f"\n{'='*80}")
    print(f"BI-1 (taker>1.15, vol>1.5, TREND_UP) — {bi1_mask.sum()} trigger bars")
    print(f"{'='*80}")

    taker_vals = df["taker_buy_sell_ratio"].values
    taker_ma5_vals = df["taker_ma5"].values
    vol_vals = df["volume_vs_ma20"].values

    # Baseline: raw 50% decay on taker
    def bi1_exit_raw50(ei, bi, df):
        ev = taker_vals[ei]
        cv = taker_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 1.0: return None
        if abs(cv - 1.0) <= abs(ev - 1.0) * 0.5: return "decay_raw50"
        return None

    # A) 3-bar confirmed decay
    decay_streak = np.zeros(len(df), dtype=int)
    def bi1_exit_confirm3(ei, bi, df):
        ev = taker_vals[ei]
        cv = taker_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 1.0: return None
        decayed = abs(cv - 1.0) <= abs(ev - 1.0) * 0.5
        if not decayed:
            decay_streak[bi] = 0
            return None
        decay_streak[bi] = decay_streak[bi-1] + 1 if bi > 0 else 1
        if decay_streak[bi] >= 3: return "decay_confirm3"
        return None

    # B) Smoothed MA5 decay 50%
    def bi1_exit_smooth(ei, bi, df):
        ev = taker_vals[ei]
        cv = taker_ma5_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 1.0: return None
        if abs(cv - 1.0) <= abs(ev - 1.0) * 0.5: return "decay_smooth"
        return None

    # C) 70% decay threshold
    def bi1_exit_70pct(ei, bi, df):
        ev = taker_vals[ei]
        cv = taker_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 1.0: return None
        if abs(cv - 1.0) <= abs(ev - 1.0) * 0.3: return "decay_70pct"
        return None

    # D) Composite: taker_ma5 < 0.98 AND vol < 0.9 (both must decay)
    def bi1_exit_composite(ei, bi, df):
        tm = taker_ma5_vals[bi]
        vm = vol_vals[bi]
        if np.isnan(tm) or np.isnan(vm): return None
        if tm < 0.98 and vm < 0.9: return "decay_composite"
        return None

    for name, exit_fn in [
        ("Baseline: raw 50%", bi1_exit_raw50),
        ("A: 3-bar confirm", bi1_exit_confirm3),
        ("B: MA5 smoothed 50%", bi1_exit_smooth),
        ("C: 70% decay (stricter)", bi1_exit_70pct),
        ("D: Composite (taker_ma5<0.98 AND vol<0.9)", bi1_exit_composite),
    ]:
        decay_streak[:] = 0
        trades = simulate(df, bi1_mask, exit_fn, cooldown=5, label=name)
        report(trades, name, is_end)

    # ===================================================================
    # SQ-1: TREND_UP + liq_press < -0.1 + taker > 1.0
    # ===================================================================
    sq1_mask = (df["trend_up"] &
                df["btc_liq_net_pressure"].notna() &
                (liq_press < -0.1) & (taker > 1.0))
    print(f"\n{'='*80}")
    print(f"SQ-1 (liq<-0.1, taker>1.0, TREND_UP) — {sq1_mask.sum()} trigger bars")
    print(f"{'='*80}")

    liq_vals = df["btc_liq_net_pressure"].values
    liq_ma5_vals = df["liq_press_ma5"].values

    def sq1_exit_raw50(ei, bi, df):
        ev = liq_vals[ei]
        cv = liq_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev >= 0: return None
        if abs(cv) <= abs(ev) * 0.5: return "decay_raw50"
        return None

    def sq1_exit_smooth(ei, bi, df):
        ev = liq_vals[ei]
        cv = liq_ma5_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev >= 0: return None
        if abs(cv) <= abs(ev) * 0.5: return "decay_smooth"
        return None

    sq1_decay_streak = np.zeros(len(df), dtype=int)
    def sq1_exit_confirm3(ei, bi, df):
        ev = liq_vals[ei]
        cv = liq_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev >= 0: return None
        decayed = abs(cv) <= abs(ev) * 0.5
        if not decayed:
            sq1_decay_streak[bi] = 0
            return None
        sq1_decay_streak[bi] = sq1_decay_streak[bi-1] + 1 if bi > 0 else 1
        if sq1_decay_streak[bi] >= 3: return "decay_confirm3"
        return None

    def sq1_exit_70pct(ei, bi, df):
        ev = liq_vals[ei]
        cv = liq_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev >= 0: return None
        if abs(cv) <= abs(ev) * 0.3: return "decay_70pct"
        return None

    def sq1_exit_composite(ei, bi, df):
        lm = liq_ma5_vals[bi]
        tm = taker_ma5_vals[bi]
        if np.isnan(lm) or np.isnan(tm): return None
        if lm > -0.03 and tm < 1.0: return "decay_composite"
        return None

    for name, exit_fn in [
        ("Baseline: raw 50%", sq1_exit_raw50),
        ("A: 3-bar confirm", sq1_exit_confirm3),
        ("B: MA5 smoothed 50%", sq1_exit_smooth),
        ("C: 70% decay", sq1_exit_70pct),
        ("D: Composite (liq_ma5>-0.03 AND taker_ma5<1.0)", sq1_exit_composite),
    ]:
        sq1_decay_streak[:] = 0
        trades = simulate(df, sq1_mask, exit_fn, cooldown=3, label=name)
        report(trades, name, is_end)

    # ===================================================================
    # OA-1 re-test with improved exits (already good, check if better)
    # ===================================================================
    oa1_mask = (df["trend_up"] &
                df["oi_change_rate_5m"].notna() &
                (oi5 > 0.003) & (taker > 0.95) & (vol_ma > 1.2))
    print(f"\n{'='*80}")
    print(f"OA-1 (oi>0.003, taker>0.95, vol>1.2, TREND_UP) — {oa1_mask.sum()} trigger bars")
    print(f"{'='*80}")

    oi5_vals = df["oi_change_rate_5m"].values

    def oa1_exit_raw50(ei, bi, df):
        ev = oi5_vals[ei]
        cv = oi5_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 0: return None
        if abs(cv) <= abs(ev) * 0.5: return "decay_raw50"
        return None

    oa1_decay_streak = np.zeros(len(df), dtype=int)
    def oa1_exit_confirm3(ei, bi, df):
        ev = oi5_vals[ei]
        cv = oi5_vals[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 0: return None
        decayed = abs(cv) <= abs(ev) * 0.5
        if not decayed:
            oa1_decay_streak[bi] = 0
            return None
        oa1_decay_streak[bi] = oa1_decay_streak[bi-1] + 1 if bi > 0 else 1
        if oa1_decay_streak[bi] >= 3: return "decay_confirm3"
        return None

    def oa1_exit_smooth(ei, bi, df):
        ev = oi5_vals[ei]
        cv = df["oi5_ma5"].values[bi]
        if np.isnan(ev) or np.isnan(cv) or ev <= 0: return None
        if abs(cv) <= abs(ev) * 0.5: return "decay_smooth"
        return None

    for name, exit_fn in [
        ("Baseline: raw 50%", oa1_exit_raw50),
        ("A: 3-bar confirm", oa1_exit_confirm3),
        ("B: MA5 smoothed 50%", oa1_exit_smooth),
    ]:
        oa1_decay_streak[:] = 0
        trades = simulate(df, oa1_mask, exit_fn, cooldown=5, label=name)
        report(trades, name, is_end)

    print(f"\n{'='*80}")
    print("DONE")


if __name__ == "__main__":
    main()
