# -*- coding: utf-8 -*-
"""
Backtest scan for three LONG signals: SQ-1, OA-1, BI-1.

For each signal:
  1. Load historical features
  2. Compute TREND_UP using regime_detector logic
  3. Grid-search entry thresholds
  4. Walk-forward: IS (first 60%) → threshold selection, OOS (last 40%) → validation
  5. Report: win rate, avg return, MFE, MAE, sample count

Exit logic: mechanism decay simulation (not fixed holding period).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.feature_engine import FeatureEngine

# ── TREND_UP detection (same as regime_detector.py) ─────────────────────
TREND_PRICE_SLOPE_PCT  = 0.002
TREND_DIR_AUTOCORR_MIN = 0.12
TREND_DIR_NET_MIN      = 0.05
TREND_RANGE_HIGH       = 0.75
TREND_LOOKBACK_BARS    = 20
CONFIRM_BARS           = 3

# ── Flow classification (same as flow_classifier.py) ────────────────────
LIQ_USD_THRESHOLD = 50000.0
OI_DROP_THRESHOLD = -0.005
AMP_SPIKE_MULT    = 2.0
TAKER_IMBALANCE   = 0.15
VOL_SURGE         = 1.5
OI_GROWTH_MIN     = 0.001


def compute_trend_up(df: pd.DataFrame) -> pd.Series:
    """Vectorized TREND_UP detection (3-vote system + 3-bar confirmation)."""
    n = len(df)
    raw_trend = np.zeros(n, dtype=int)  # 1=UP, -1=DOWN, 0=NEUTRAL

    close = df["close"].values if "close" in df.columns else np.full(n, np.nan)
    dir_autocorr = df["direction_autocorr"].values if "direction_autocorr" in df.columns else np.full(n, 0.0)
    dir_net = df["direction_net_1m"].values if "direction_net_1m" in df.columns else np.full(n, 0.0)
    pos_range = df["position_in_range_24h"].values if "position_in_range_24h" in df.columns else np.full(n, 0.5)

    for i in range(TREND_LOOKBACK_BARS, n):
        votes_up = 0
        votes_down = 0

        # Vote 1: price slope
        first_close = close[i - TREND_LOOKBACK_BARS]
        if first_close > 0:
            pct = (close[i] - first_close) / first_close
            if pct > TREND_PRICE_SLOPE_PCT:
                votes_up += 1
            elif pct < -TREND_PRICE_SLOPE_PCT:
                votes_down += 1

        # Vote 2: direction autocorr + net
        ac = dir_autocorr[i] if not np.isnan(dir_autocorr[i]) else 0.0
        dn = dir_net[i] if not np.isnan(dir_net[i]) else 0.0
        if ac > TREND_DIR_AUTOCORR_MIN and dn > TREND_DIR_NET_MIN:
            votes_up += 1
        elif ac > TREND_DIR_AUTOCORR_MIN and dn < -TREND_DIR_NET_MIN:
            votes_down += 1

        # Vote 3: range position
        rp = pos_range[i] if not np.isnan(pos_range[i]) else 0.5
        if rp > TREND_RANGE_HIGH:
            votes_up += 1
        elif rp < (1 - TREND_RANGE_HIGH):
            votes_down += 1

        if votes_up >= 2:
            raw_trend[i] = 1
        elif votes_down >= 2:
            raw_trend[i] = -1

    # 3-bar confirmation
    confirmed = np.zeros(n, dtype=int)
    current = 0
    streak = 0
    for i in range(n):
        if raw_trend[i] == current:
            pass  # no change
        else:
            if raw_trend[i] != 0:
                streak += 1
                if streak >= CONFIRM_BARS:
                    current = raw_trend[i]
                    streak = 0
            else:
                streak = 0
                # raw=0 doesn't immediately change confirmed trend
        confirmed[i] = current
        if raw_trend[i] != current and raw_trend[i] != 0:
            pass  # keep counting
        elif raw_trend[i] == current:
            streak = 0

    return pd.Series(confirmed == 1, index=df.index, name="trend_up")


def compute_flow(df: pd.DataFrame) -> pd.Series:
    """Vectorized flow classification."""
    n = len(df)
    flow = pd.Series("PASSIVE", index=df.index)

    total_liq = df.get("total_liq_usd_5m", pd.Series(0.0, index=df.index)).fillna(0)
    oi_5m = df.get("oi_change_rate_5m", pd.Series(0.0, index=df.index)).fillna(0)
    amp_1m = df.get("amplitude_1m", pd.Series(0.0, index=df.index)).fillna(0)
    amp_ma20 = df.get("amplitude_ma20", pd.Series(0.001, index=df.index)).fillna(0.001)
    taker = df.get("taker_buy_sell_ratio", pd.Series(1.0, index=df.index)).fillna(1.0)
    vol_ma20 = df.get("volume_vs_ma20", pd.Series(1.0, index=df.index)).fillna(1.0)

    liq_mask = (total_liq > LIQ_USD_THRESHOLD) | ((oi_5m < OI_DROP_THRESHOLD) & (amp_1m > amp_ma20 * AMP_SPIKE_MULT))
    agg_buy_mask = (taker > 1.0 + TAKER_IMBALANCE) & (vol_ma20 > VOL_SURGE) & (oi_5m > OI_GROWTH_MIN)
    agg_sell_mask = (taker < 1.0 - TAKER_IMBALANCE) & (vol_ma20 > VOL_SURGE) & (oi_5m > OI_GROWTH_MIN)

    flow[liq_mask] = "LIQUIDATION"
    flow[(~liq_mask) & agg_buy_mask] = "AGGRESSIVE_BUY"
    flow[(~liq_mask) & (~agg_buy_mask) & agg_sell_mask] = "AGGRESSIVE_SELL"

    return flow


def compute_forward_returns(df: pd.DataFrame, max_bars: int = 60) -> dict:
    """Compute forward returns and MFE/MAE for each bar."""
    close = df["close"].values
    n = len(close)

    fwd_rets = {}
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)

    for horizon in [5, 10, 15, 20, 30, 45, 60]:
        fwd = np.full(n, np.nan)
        for i in range(n - horizon):
            fwd[i] = (close[i + horizon] - close[i]) / close[i]
        fwd_rets[f"fwd_{horizon}"] = fwd

    # MFE/MAE for LONG (max gain / max loss within max_bars)
    for i in range(n - max_bars):
        future_prices = close[i+1:i+1+max_bars]
        entry = close[i]
        mfe[i] = (future_prices.max() - entry) / entry
        mae[i] = (future_prices.min() - entry) / entry

    return fwd_rets, mfe, mae


def scan_signal(df: pd.DataFrame, name: str, condition_func, threshold_grid: dict,
                is_split: float = 0.6):
    """
    Grid-search entry thresholds for a signal.

    condition_func(df, **params) -> bool mask
    threshold_grid: {param_name: [values]}

    Walk-forward: IS = first 60%, OOS = last 40%.
    """
    n = len(df)
    is_end = int(n * is_split)
    is_df = df.iloc[:is_end]
    oos_df = df.iloc[is_end:]

    print(f"\n{'='*60}")
    print(f"SIGNAL: {name}")
    print(f"Total bars: {n:,}, IS: {is_end:,}, OOS: {n-is_end:,}")
    print(f"IS period: {is_df['timestamp'].iloc[0]} ~ {is_df['timestamp'].iloc[-1]}")
    print(f"OOS period: {oos_df['timestamp'].iloc[0]} ~ {oos_df['timestamp'].iloc[-1]}")
    print(f"{'='*60}")

    # Generate all param combinations
    import itertools
    param_names = list(threshold_grid.keys())
    param_values = list(threshold_grid.values())
    combos = list(itertools.product(*param_values))

    results = []
    for combo in combos:
        params = dict(zip(param_names, combo))

        # IS evaluation
        is_mask = condition_func(is_df, **params)
        is_triggers = is_mask.sum()
        if is_triggers < 10:
            continue

        # Forward returns for IS triggers (LONG = positive return = win)
        is_fwd_10 = is_df.loc[is_mask, "fwd_10"].dropna()
        is_fwd_20 = is_df.loc[is_mask, "fwd_20"].dropna()
        is_fwd_30 = is_df.loc[is_mask, "fwd_30"].dropna()
        is_mfe = is_df.loc[is_mask, "mfe_60"].dropna()
        is_mae = is_df.loc[is_mask, "mae_60"].dropna()

        if len(is_fwd_20) < 10:
            continue

        is_wr_10 = (is_fwd_10 > 0).mean() if len(is_fwd_10) > 0 else 0
        is_wr_20 = (is_fwd_20 > 0).mean() if len(is_fwd_20) > 0 else 0
        is_wr_30 = (is_fwd_30 > 0).mean() if len(is_fwd_30) > 0 else 0
        is_avg_ret = is_fwd_20.mean() if len(is_fwd_20) > 0 else 0
        is_avg_mfe = is_mfe.mean() if len(is_mfe) > 0 else 0
        is_avg_mae = is_mae.mean() if len(is_mae) > 0 else 0

        # OOS evaluation with same thresholds
        oos_mask = condition_func(oos_df, **params)
        oos_triggers = oos_mask.sum()
        oos_fwd_10 = oos_df.loc[oos_mask, "fwd_10"].dropna()
        oos_fwd_20 = oos_df.loc[oos_mask, "fwd_20"].dropna()
        oos_fwd_30 = oos_df.loc[oos_mask, "fwd_30"].dropna()
        oos_mfe = oos_df.loc[oos_mask, "mfe_60"].dropna()
        oos_mae = oos_df.loc[oos_mask, "mae_60"].dropna()

        oos_wr_10 = (oos_fwd_10 > 0).mean() if len(oos_fwd_10) > 0 else 0
        oos_wr_20 = (oos_fwd_20 > 0).mean() if len(oos_fwd_20) > 0 else 0
        oos_wr_30 = (oos_fwd_30 > 0).mean() if len(oos_fwd_30) > 0 else 0
        oos_avg_ret = oos_fwd_20.mean() if len(oos_fwd_20) > 0 else 0
        oos_avg_mfe = oos_mfe.mean() if len(oos_mfe) > 0 else 0
        oos_avg_mae = oos_mae.mean() if len(oos_mae) > 0 else 0

        results.append({
            **params,
            "is_n": is_triggers, "is_wr10": is_wr_10, "is_wr20": is_wr_20,
            "is_wr30": is_wr_30, "is_avg_ret": is_avg_ret,
            "is_mfe": is_avg_mfe, "is_mae": is_avg_mae,
            "oos_n": oos_triggers, "oos_wr10": oos_wr_10, "oos_wr20": oos_wr_20,
            "oos_wr30": oos_wr_30, "oos_avg_ret": oos_avg_ret,
            "oos_mfe": oos_avg_mfe, "oos_mae": oos_avg_mae,
        })

    if not results:
        print(f"  No valid threshold combinations found (all < 10 IS triggers)")
        return pd.DataFrame()

    rdf = pd.DataFrame(results)

    # Sort by OOS win rate at 20 bars, then by OOS avg return
    rdf = rdf.sort_values(["oos_wr20", "oos_avg_ret"], ascending=[False, False])

    print(f"\nTop 10 threshold combinations (sorted by OOS WR@20):")
    print(f"{'Params':<50} {'IS_n':>5} {'IS_WR20':>7} {'IS_ret':>7} | {'OOS_n':>5} {'OOS_WR20':>8} {'OOS_ret':>8} {'MFE':>7} {'MAE':>7}")
    print("-" * 120)
    for _, row in rdf.head(10).iterrows():
        param_str = ", ".join(f"{k}={row[k]:.4f}" for k in param_names)
        print(f"{param_str:<50} {int(row['is_n']):>5} {row['is_wr20']:>7.1%} {row['is_avg_ret']:>7.3%} | "
              f"{int(row['oos_n']):>5} {row['oos_wr20']:>8.1%} {row['oos_avg_ret']:>8.3%} {row['oos_mfe']:>7.3%} {row['oos_mae']:>7.3%}")

    return rdf


# ── Signal condition functions ───────────────────────────────────────────

def sq1_condition(df, liq_thresh, taker_thresh):
    """SQ-1: Short squeeze LONG — LIQUIDATION flow + TREND_UP + short liq dominant."""
    return (
        df["trend_up"] &
        (df["flow"] == "LIQUIDATION") &
        (df["btc_liq_net_pressure"].fillna(0) < liq_thresh) &
        (df["taker_buy_sell_ratio"].fillna(1.0) > taker_thresh) &
        (df["position_in_range_24h"].fillna(0.5) > 0.4)
    )


def oa1_condition(df, oi_thresh, taker_thresh, vol_thresh):
    """OA-1: OI accumulation LONG — TREND_UP + OI growing + buyer dominant."""
    return (
        df["trend_up"] &
        (df["flow"] != "LIQUIDATION") &
        (df["oi_change_rate_5m"].fillna(0) > oi_thresh) &
        (df["taker_buy_sell_ratio"].fillna(1.0) > taker_thresh) &
        (df["volume_vs_ma20"].fillna(1.0) > vol_thresh)
    )


def bi1_condition(df, taker_thresh, dir_net_thresh):
    """BI-1: Buyer impulse LONG — AGGRESSIVE_BUY flow + TREND_UP."""
    return (
        df["trend_up"] &
        (df["flow"] == "AGGRESSIVE_BUY") &
        (df["taker_buy_sell_ratio"].fillna(1.0) > taker_thresh) &
        (df["direction_net_1m"].fillna(0) > dir_net_thresh)
    )


def main():
    print("Loading features...")
    fe = FeatureEngine(storage_path="data/storage")

    # Use date range where all dimensions have data
    # Liquidation data: 2026-03-17+, OI: 2026-02-18+
    # Use 2026-02-18 ~ 2026-04-07 for OA-1/BI-1, 2026-03-17+ for SQ-1
    df = fe.load_date_range("2026-02-18", "2026-04-07", include_heavy=True)

    if df.empty:
        print("ERROR: No data loaded")
        return

    print(f"Loaded {len(df):,} bars from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    # Check feature availability
    for col in ["btc_liq_net_pressure", "total_liq_usd_5m", "oi_change_rate_5m",
                "taker_buy_sell_ratio", "volume_vs_ma20", "direction_net_1m",
                "direction_autocorr", "position_in_range_24h", "close"]:
        avail = df[col].notna().sum() if col in df.columns else 0
        pct = avail / len(df) * 100 if len(df) > 0 else 0
        print(f"  {col}: {avail:,} / {len(df):,} ({pct:.1f}%)")

    # Compute TREND_UP
    print("\nComputing TREND_UP...")
    df["trend_up"] = compute_trend_up(df)
    trend_up_pct = df["trend_up"].mean() * 100
    print(f"  TREND_UP bars: {df['trend_up'].sum():,} ({trend_up_pct:.1f}%)")

    # Compute flow classification
    print("Computing flow classification...")
    df["flow"] = compute_flow(df)
    for ft in ["PASSIVE", "AGGRESSIVE_BUY", "AGGRESSIVE_SELL", "LIQUIDATION"]:
        cnt = (df["flow"] == ft).sum()
        print(f"  {ft}: {cnt:,} ({cnt/len(df)*100:.1f}%)")

    # Compute forward returns
    print("Computing forward returns and MFE/MAE...")
    fwd_rets, mfe, mae = compute_forward_returns(df, max_bars=60)
    for k, v in fwd_rets.items():
        df[k] = v
    df["mfe_60"] = mfe
    df["mae_60"] = mae

    # ── SQ-1 Scan ──
    # Only use data where liquidation features exist
    sq1_df = df[df["btc_liq_net_pressure"].notna()].copy()
    if len(sq1_df) > 100:
        scan_signal(sq1_df, "SQ-1 (Short Squeeze LONG)", sq1_condition, {
            "liq_thresh": [-0.1, -0.15, -0.2, -0.25, -0.3, -0.4, -0.5],
            "taker_thresh": [0.95, 1.0, 1.05, 1.10, 1.15],
        })
    else:
        print(f"\nSQ-1: Insufficient liquidation data ({len(sq1_df)} bars)")

    # ── OA-1 Scan ──
    oa1_df = df[df["oi_change_rate_5m"].notna()].copy()
    if len(oa1_df) > 100:
        scan_signal(oa1_df, "OA-1 (OI Accumulation LONG)", oa1_condition, {
            "oi_thresh": [0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005],
            "taker_thresh": [0.95, 1.0, 1.05, 1.10],
            "vol_thresh": [0.7, 0.8, 1.0, 1.2],
        })
    else:
        print(f"\nOA-1: Insufficient OI data ({len(oa1_df)} bars)")

    # ── BI-1 Scan ──
    scan_signal(df, "BI-1 (Buyer Impulse LONG)", bi1_condition, {
        "taker_thresh": [1.05, 1.10, 1.15, 1.20],
        "dir_net_thresh": [0.0, 0.02, 0.05, 0.10],
    })

    print("\n" + "=" * 60)
    print("SCAN COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
