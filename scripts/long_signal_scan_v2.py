# -*- coding: utf-8 -*-
"""
V2: Broader LONG signal scan with relaxed conditions.

Changes from v1:
- SQ-1: Drop position_in_range requirement, relax flow to include bars where
  liq_press is very negative (even without LIQUIDATION flow classification)
- OA-1: Try without TREND_UP requirement (OI + taker alone)
- BI-1: Relax to TREND_UP + taker dominance (without AGGRESSIVE_BUY flow)
- Also scan: pure TREND_UP forward returns (baseline)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.feature_engine import FeatureEngine

# Reuse from v1
TREND_PRICE_SLOPE_PCT  = 0.002
TREND_DIR_AUTOCORR_MIN = 0.12
TREND_DIR_NET_MIN      = 0.05
TREND_RANGE_HIGH       = 0.75
TREND_LOOKBACK_BARS    = 20
CONFIRM_BARS           = 3


def compute_trend_up(df):
    n = len(df)
    close = df["close"].values
    dir_autocorr = df["direction_autocorr"].values if "direction_autocorr" in df.columns else np.full(n, np.nan)
    dir_net = df["direction_net_1m"].values if "direction_net_1m" in df.columns else np.full(n, np.nan)
    pos_range = df["position_in_range_24h"].values if "position_in_range_24h" in df.columns else np.full(n, 0.5)

    raw_trend = np.zeros(n, dtype=int)
    for i in range(TREND_LOOKBACK_BARS, n):
        up, dn = 0, 0
        first_close = close[i - TREND_LOOKBACK_BARS]
        if first_close > 0:
            pct = (close[i] - first_close) / first_close
            if pct > TREND_PRICE_SLOPE_PCT: up += 1
            elif pct < -TREND_PRICE_SLOPE_PCT: dn += 1

        ac = dir_autocorr[i] if not np.isnan(dir_autocorr[i]) else 0.0
        net = dir_net[i] if not np.isnan(dir_net[i]) else 0.0
        if ac > TREND_DIR_AUTOCORR_MIN and net > TREND_DIR_NET_MIN: up += 1
        elif ac > TREND_DIR_AUTOCORR_MIN and net < -TREND_DIR_NET_MIN: dn += 1

        rp = pos_range[i] if not np.isnan(pos_range[i]) else 0.5
        if rp > TREND_RANGE_HIGH: up += 1
        elif rp < (1 - TREND_RANGE_HIGH): dn += 1

        if up >= 2: raw_trend[i] = 1
        elif dn >= 2: raw_trend[i] = -1

    confirmed = np.zeros(n, dtype=int)
    current = 0
    consec = 0
    for i in range(n):
        if raw_trend[i] == current:
            consec = 0
        elif raw_trend[i] != 0:
            if raw_trend[i] != current:
                consec += 1
                if consec >= CONFIRM_BARS:
                    current = raw_trend[i]
                    consec = 0
            else:
                consec = 0
        else:
            consec = 0
        confirmed[i] = current

    return pd.Series(confirmed == 1, index=df.index)


def compute_fwd(df, max_bars=60):
    close = df["close"].values
    n = len(close)
    result = {}
    for h in [5, 10, 15, 20, 30, 45, 60]:
        fwd = np.full(n, np.nan)
        for i in range(n - h):
            fwd[i] = (close[i + h] - close[i]) / close[i]
        result[f"fwd_{h}"] = fwd

    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    for i in range(n - max_bars):
        fp = close[i+1:i+1+max_bars]
        entry = close[i]
        mfe[i] = (fp.max() - entry) / entry
        mae[i] = (fp.min() - entry) / entry
    result["mfe_60"] = mfe
    result["mae_60"] = mae
    return result


def scan(df, name, mask, is_split=0.6, min_samples=5):
    n = len(df)
    is_end = int(n * is_split)
    is_mask = mask.iloc[:is_end]
    oos_mask = mask.iloc[is_end:]

    is_n = is_mask.sum()
    oos_n = oos_mask.sum()

    if is_n < min_samples:
        print(f"  {name}: IS triggers={is_n} < {min_samples}, skip")
        return None

    is_data = df.iloc[:is_end][is_mask]
    oos_data = df.iloc[is_end:][oos_mask]

    result = {"name": name, "is_n": int(is_n), "oos_n": int(oos_n)}

    for period, data in [("is", is_data), ("oos", oos_data)]:
        if len(data) == 0:
            for h in [10, 20, 30]:
                result[f"{period}_wr{h}"] = 0
            result[f"{period}_ret20"] = 0
            result[f"{period}_mfe"] = 0
            result[f"{period}_mae"] = 0
            continue
        for h in [10, 20, 30]:
            fwd = data[f"fwd_{h}"].dropna()
            result[f"{period}_wr{h}"] = (fwd > 0).mean() if len(fwd) > 0 else 0
        result[f"{period}_ret20"] = data["fwd_20"].dropna().mean() if len(data["fwd_20"].dropna()) > 0 else 0
        result[f"{period}_mfe"] = data["mfe_60"].dropna().mean() if len(data["mfe_60"].dropna()) > 0 else 0
        result[f"{period}_mae"] = data["mae_60"].dropna().mean() if len(data["mae_60"].dropna()) > 0 else 0

    return result


def main():
    print("Loading features...")
    fe = FeatureEngine(storage_path="data/storage")
    df = fe.load_date_range("2026-02-18", "2026-04-07", include_heavy=True)
    print(f"Loaded {len(df):,} bars")

    # Feature availability
    liq_avail = df["btc_liq_net_pressure"].notna().sum()
    oi_avail = df["oi_change_rate_5m"].notna().sum()
    print(f"  btc_liq_net_pressure: {liq_avail:,} ({liq_avail/len(df)*100:.1f}%)")
    print(f"  oi_change_rate_5m: {oi_avail:,} ({oi_avail/len(df)*100:.1f}%)")

    print("\nComputing TREND_UP...")
    df["trend_up"] = compute_trend_up(df)
    print(f"  TREND_UP: {df['trend_up'].sum():,} ({df['trend_up'].mean()*100:.1f}%)")

    print("Computing forward returns...")
    fwd = compute_fwd(df)
    for k, v in fwd.items():
        df[k] = v

    taker = df["taker_buy_sell_ratio"].fillna(1.0)
    oi5 = df["oi_change_rate_5m"].fillna(0)
    vol_ma = df["volume_vs_ma20"].fillna(1.0)
    liq_press = df["btc_liq_net_pressure"].fillna(0)
    pos24 = df["position_in_range_24h"].fillna(0.5)

    results = []

    # ── BASELINE: Just TREND_UP ──
    print("\n" + "="*80)
    print("BASELINE SCANS")
    print("="*80)
    r = scan(df, "Baseline: TREND_UP only", df["trend_up"])
    if r: results.append(r)

    r = scan(df, "Baseline: TREND_UP + taker>1.0", df["trend_up"] & (taker > 1.0))
    if r: results.append(r)

    r = scan(df, "Baseline: TREND_UP + taker>1.05", df["trend_up"] & (taker > 1.05))
    if r: results.append(r)

    # ── SQ-1: Relax to just liq_press very negative (no flow classification needed) ──
    print("\n" + "="*80)
    print("SQ-1 SCANS (Short Squeeze - liq_pressure based)")
    print("="*80)

    liq_valid = df["btc_liq_net_pressure"].notna()
    for lt in [-0.05, -0.1, -0.15, -0.2, -0.3]:
        for tt in [0.95, 1.0, 1.05]:
            mask = liq_valid & df["trend_up"] & (liq_press < lt) & (taker > tt)
            label = f"SQ-1: liq<{lt}, taker>{tt}, TREND_UP"
            r = scan(df, label, mask)
            if r: results.append(r)

    # Without TREND_UP requirement
    for lt in [-0.1, -0.2, -0.3]:
        mask = liq_valid & (liq_press < lt) & (taker > 1.0) & (pos24 > 0.4)
        label = f"SQ-1 (no trend): liq<{lt}, taker>1.0, pos24>0.4"
        r = scan(df, label, mask)
        if r: results.append(r)

    # ── OA-1: OI accumulation scans ──
    print("\n" + "="*80)
    print("OA-1 SCANS (OI Accumulation)")
    print("="*80)

    oi_valid = df["oi_change_rate_5m"].notna()
    for oit in [0.0005, 0.001, 0.002, 0.003, 0.005]:
        for tt in [0.95, 1.0, 1.05]:
            # With TREND_UP
            mask = oi_valid & df["trend_up"] & (oi5 > oit) & (taker > tt)
            label = f"OA-1: oi>{oit}, taker>{tt}, TREND_UP"
            r = scan(df, label, mask)
            if r: results.append(r)

    # Without TREND_UP but with position filter
    for oit in [0.001, 0.002, 0.003]:
        for tt in [1.0, 1.05]:
            mask = oi_valid & (oi5 > oit) & (taker > tt) & (pos24 > 0.4)
            label = f"OA-1 (no trend): oi>{oit}, taker>{tt}, pos24>0.4"
            r = scan(df, label, mask)
            if r: results.append(r)

    # ── BI-1: Buyer dominance scans (relax from AGGRESSIVE_BUY to just taker spike) ──
    print("\n" + "="*80)
    print("BI-1 SCANS (Buyer Dominance)")
    print("="*80)

    for tt in [1.10, 1.15, 1.20, 1.25, 1.30]:
        for vt in [1.0, 1.5, 2.0]:
            mask = df["trend_up"] & (taker > tt) & (vol_ma > vt)
            label = f"BI-1: taker>{tt}, vol>{vt}, TREND_UP"
            r = scan(df, label, mask)
            if r: results.append(r)

    # Taker spike without trend
    for tt in [1.15, 1.20, 1.25]:
        mask = (taker > tt) & (vol_ma > 1.5) & (pos24 > 0.3)
        label = f"BI-1 (no trend): taker>{tt}, vol>1.5, pos24>0.3"
        r = scan(df, label, mask)
        if r: results.append(r)

    # ── PRINT SUMMARY ──
    print("\n" + "="*80)
    print("SUMMARY (all scans with OOS samples >= 3)")
    print("="*80)

    rdf = pd.DataFrame(results)
    rdf = rdf[rdf["oos_n"] >= 3].sort_values("oos_wr20", ascending=False)

    if rdf.empty:
        print("No scans with >= 3 OOS samples found.")
        return

    print(f"{'Signal':<55} {'IS_n':>5} {'IS_WR20':>7} {'IS_ret':>7} | {'OOS_n':>5} {'OOS_WR20':>8} {'OOS_ret':>8} {'MFE':>7} {'MAE':>7} {'MFE/MAE':>7}")
    print("-" * 140)
    for _, row in rdf.iterrows():
        mfe_mae = abs(row["oos_mfe"] / row["oos_mae"]) if row["oos_mae"] != 0 else 0
        print(f"{row['name']:<55} {int(row['is_n']):>5} {row['is_wr20']:>7.1%} {row['is_ret20']:>7.3%} | "
              f"{int(row['oos_n']):>5} {row['oos_wr20']:>8.1%} {row['oos_ret20']:>8.3%} {row['oos_mfe']:>7.3%} {row['oos_mae']:>7.3%} {mfe_mae:>7.2f}")


if __name__ == "__main__":
    main()
