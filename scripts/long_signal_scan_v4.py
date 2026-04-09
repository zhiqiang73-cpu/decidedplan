# -*- coding: utf-8 -*-
"""
V4: OA-1 focused backtest — primary mechanism decay ONLY (no taker confirm).

Key change from v3: remove confirm_decay (taker < 0.92 was killing trades too early).
Exit ONLY when:
  1. Primary mechanism decay: oi_change_rate_5m reverts > 50% toward 0
  2. Hard stop: -0.3%
  3. MFE ratchet: if MFE > 0.15%, lock at entry + 0.02%
  4. Time cap: 60 bars

Also test: what if we use a LESS aggressive primary decay (40% revert instead of 50%)?
And test: adding TREND reversal as exit (TREND_UP → not TREND_UP).
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

HARD_STOP_PCT    = -0.003
MAKER_FEE_RT     = 0.0004


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
    current = 0
    consec_target = 0
    consec_count = 0
    for i in range(n):
        if raw[i] != current and raw[i] != 0:
            if raw[i] == consec_target:
                consec_count += 1
            else:
                consec_target = raw[i]
                consec_count = 1
            if consec_count >= CONFIRM_BARS:
                current = raw[i]
                consec_count = 0
                consec_target = 0
        elif raw[i] == current:
            consec_count = 0
            consec_target = 0
        confirmed[i] = current
    return confirmed


def simulate_oa1(df, entry_mask, oi_vals, trend_confirmed, close_vals,
                 decay_pct=0.5, use_trend_exit=False,
                 mfe_ratchet_thr=0.0015, mfe_ratchet_lock=0.0002,
                 time_cap=60, cooldown_bars=5):
    """Simulate OA-1 trades with pure OI mechanism decay exit."""
    n = len(close_vals)
    trades = []
    last_exit = -cooldown_bars
    entries = np.where(entry_mask.values)[0]

    for ei in entries:
        if ei - last_exit < cooldown_bars or ei >= n - 2:
            continue

        ep = close_vals[ei]
        entry_oi = oi_vals[ei]
        if np.isnan(entry_oi) or entry_oi <= 0:
            continue

        # Entry OI distance from neutral (0)
        entry_dist = abs(entry_oi)  # neutral = 0
        decay_threshold = entry_dist * decay_pct

        max_p = ep
        exit_bar = None
        exit_reason = None

        for j in range(1, time_cap + 1):
            bi = ei + j
            if bi >= n:
                exit_bar = bi - 1
                exit_reason = "data_end"
                break

            cp = close_vals[bi]
            ret = (cp - ep) / ep
            max_p = max(max_p, cp)
            mfe = (max_p - ep) / ep

            # 1. Hard stop
            if ret <= HARD_STOP_PCT:
                exit_bar = bi
                exit_reason = "hard_stop"
                break

            # 2. MFE ratchet
            if mfe > mfe_ratchet_thr and ret < mfe_ratchet_lock:
                exit_bar = bi
                exit_reason = "mfe_ratchet"
                break

            # 3. Primary mechanism decay: OI reverts > decay_pct toward 0
            current_oi = oi_vals[bi]
            if not np.isnan(current_oi):
                current_dist = abs(current_oi)
                if current_dist <= decay_threshold:
                    exit_bar = bi
                    exit_reason = "mechanism_decay"
                    break

            # 4. Trend exit: TREND_UP disappears
            if use_trend_exit and trend_confirmed[bi] != 1:
                exit_bar = bi
                exit_reason = "trend_exit"
                break

        if exit_bar is None:
            exit_bar = min(ei + time_cap, n - 1)
            exit_reason = "time_cap"

        xp = close_vals[exit_bar]
        gross = (xp - ep) / ep
        net = gross - MAKER_FEE_RT
        hold = exit_bar - ei
        slice_p = close_vals[ei:exit_bar+1]
        mfe_actual = (slice_p.max() - ep) / ep
        mae_actual = (slice_p.min() - ep) / ep

        trades.append({
            "entry_bar": ei, "exit_bar": exit_bar,
            "gross_ret": gross, "net_ret": net,
            "hold_bars": hold, "exit_reason": exit_reason,
            "mfe": mfe_actual, "mae": mae_actual,
            "entry_oi": entry_oi,
        })
        last_exit = exit_bar

    return pd.DataFrame(trades)


def report(tdf, label, is_end):
    if tdf.empty:
        print(f"  {label}: 0 trades")
        return

    for period, sub in [("IS", tdf[tdf["entry_bar"] < is_end]),
                        ("OOS", tdf[tdf["entry_bar"] >= is_end])]:
        if sub.empty:
            print(f"  {label} [{period}]: 0 trades")
            continue
        n = len(sub)
        gwr = (sub["gross_ret"] > 0).mean()
        nwr = (sub["net_ret"] > 0).mean()
        ag = sub["gross_ret"].mean()
        an = sub["net_ret"].mean()
        ah = sub["hold_bars"].mean()
        am = sub["mfe"].mean()
        aa = sub["mae"].mean()
        reasons = sub["exit_reason"].value_counts()
        rs = ", ".join(f"{r}={c}" for r, c in reasons.items())
        pf_wins = sub[sub["net_ret"] > 0]["net_ret"].sum()
        pf_losses = abs(sub[sub["net_ret"] <= 0]["net_ret"].sum())
        pf = pf_wins / pf_losses if pf_losses > 0 else float('inf')

        print(f"\n  {label} [{period}] n={n}")
        print(f"    Gross WR: {gwr:.1%}  Net WR: {nwr:.1%}  PF: {pf:.2f}")
        print(f"    Avg gross: {ag:.4%}  Avg net: {an:.4%}")
        print(f"    Avg hold: {ah:.1f} bars  MFE: {am:.3%}  MAE: {aa:.3%}")
        print(f"    Exits: {rs}")

        for reason in sub["exit_reason"].unique():
            s = sub[sub["exit_reason"] == reason]
            wr = (s["net_ret"] > 0).mean()
            avg = s["net_ret"].mean()
            print(f"      {reason}: n={len(s)} wr={wr:.1%} avg_net={avg:.4%} hold={s['hold_bars'].mean():.1f}")


def main():
    print("Loading features...")
    fe = FeatureEngine(storage_path="data/storage")
    df = fe.load_date_range("2026-02-18", "2026-04-07", include_heavy=True)
    print(f"Loaded {len(df):,} bars")

    trend_arr = compute_trend_up(df)
    df["trend_up"] = trend_arr == 1
    print(f"TREND_UP: {df['trend_up'].sum():,} ({df['trend_up'].mean()*100:.1f}%)")

    n = len(df)
    is_end = int(n * 0.6)
    close = df["close"].values
    oi5 = df["oi_change_rate_5m"].values
    taker = df["taker_buy_sell_ratio"].fillna(1.0)
    vol_ma = df["volume_vs_ma20"].fillna(1.0)

    oi_valid = df["oi_change_rate_5m"].notna()

    print(f"\n{'='*70}")
    print("TEST A: Pure OI mechanism decay (50% revert), NO confirm, NO trend exit")
    print(f"{'='*70}")

    for oi_thr in [0.002, 0.003, 0.005]:
        for tt in [0.95, 1.0, 1.05]:
            for vt in [0.8, 1.0, 1.2]:
                mask = df["trend_up"] & oi_valid & (df["oi_change_rate_5m"] > oi_thr) & (taker > tt) & (vol_ma > vt)
                if mask.sum() < 5:
                    continue
                label = f"oi>{oi_thr} t>{tt} v>{vt}"
                trades = simulate_oa1(df, mask, oi5, trend_arr, close,
                                      decay_pct=0.5, use_trend_exit=False)
                report(trades, label, is_end)

    print(f"\n{'='*70}")
    print("TEST B: OI decay 50% + TREND_EXIT (exit when TREND_UP disappears)")
    print(f"{'='*70}")

    for oi_thr in [0.002, 0.003, 0.005]:
        for tt in [0.95, 1.0, 1.05]:
            mask = df["trend_up"] & oi_valid & (df["oi_change_rate_5m"] > oi_thr) & (taker > tt) & (vol_ma > 1.0)
            if mask.sum() < 5:
                continue
            label = f"oi>{oi_thr} t>{tt} v>1.0 +trend_exit"
            trades = simulate_oa1(df, mask, oi5, trend_arr, close,
                                  decay_pct=0.5, use_trend_exit=True)
            report(trades, label, is_end)

    print(f"\n{'='*70}")
    print("TEST C: Relaxed OI decay (30% revert instead of 50%), NO trend exit")
    print(f"{'='*70}")

    for oi_thr in [0.003, 0.005]:
        for tt in [0.95, 1.0, 1.05]:
            mask = df["trend_up"] & oi_valid & (df["oi_change_rate_5m"] > oi_thr) & (taker > tt) & (vol_ma > 1.0)
            if mask.sum() < 5:
                continue
            label = f"oi>{oi_thr} t>{tt} decay30%"
            trades = simulate_oa1(df, mask, oi5, trend_arr, close,
                                  decay_pct=0.3, use_trend_exit=False)
            report(trades, label, is_end)

    print(f"\n{'='*70}")
    print("TEST D: Relaxed OI decay 30% + TREND_EXIT")
    print(f"{'='*70}")

    for oi_thr in [0.003, 0.005]:
        for tt in [0.95, 1.0, 1.05]:
            mask = df["trend_up"] & oi_valid & (df["oi_change_rate_5m"] > oi_thr) & (taker > tt) & (vol_ma > 1.0)
            if mask.sum() < 5:
                continue
            label = f"oi>{oi_thr} t>{tt} decay30% +trend_exit"
            trades = simulate_oa1(df, mask, oi5, trend_arr, close,
                                  decay_pct=0.3, use_trend_exit=True)
            report(trades, label, is_end)

    print(f"\n{'='*70}")
    print("DONE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
