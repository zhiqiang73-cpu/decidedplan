# -*- coding: utf-8 -*-
"""
V3: LONG signal backtest with REAL mechanism decay exit.

Exit logic (from mechanism_tracker._check_revert_to_neutral):
  - Record entry feature value
  - Neutral value for feature (oi_change_rate_5m→0, taker→1.0, liq_press→0)
  - Exit when: |current - neutral| <= |entry - neutral| * 0.5
  - i.e. the entry force has reverted more than halfway back to neutral

Also tracks:
  - Hard stop at 0.3% (same as live system)
  - Time cap at 60 bars (absolute maximum)
  - MFE ratchet: if MFE > 0.15%, tighten stop to entry+0.02%

For each signal, sweep entry thresholds and report:
  IS/OOS win rate, avg return, avg hold bars, exit reason distribution
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.feature_engine import FeatureEngine

# ── TREND_UP detection constants ─────────────────────────────────────
TREND_PRICE_SLOPE_PCT  = 0.002
TREND_DIR_AUTOCORR_MIN = 0.12
TREND_DIR_NET_MIN      = 0.05
TREND_RANGE_HIGH       = 0.75
TREND_LOOKBACK_BARS    = 20
CONFIRM_BARS           = 3

# ── Exit parameters ──────────────────────────────────────────────────
HARD_STOP_PCT    = -0.003   # -0.3%
TIME_CAP_BARS    = 60
MFE_RATCHET_THR  = 0.0015  # 0.15% MFE triggers ratchet
MFE_RATCHET_LOCK = 0.0002  # lock at entry + 0.02%
MAKER_FEE_RT     = 0.0004  # 0.04% round-trip maker fee

# ── Neutral values (from mechanism_tracker) ──────────────────────────
NEUTRAL = {
    "oi_change_rate_5m":    0.0,
    "taker_buy_sell_ratio": 1.0,
    "btc_liq_net_pressure": 0.0,
    "volume_vs_ma20":       1.0,
    "direction_net_1m":     0.0,
}


def compute_trend_up(df):
    n = len(df)
    close = df["close"].values
    dir_ac = df["direction_autocorr"].values if "direction_autocorr" in df.columns else np.full(n, np.nan)
    dir_net = df["direction_net_1m"].values if "direction_net_1m" in df.columns else np.full(n, np.nan)
    pos24 = df["position_in_range_24h"].values if "position_in_range_24h" in df.columns else np.full(n, 0.5)

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
    return pd.Series(confirmed == 1, index=df.index)


def mechanism_decay_exit(entry_feature_val, current_feature_val, neutral_val):
    """Check if entry force has reverted > 50% toward neutral."""
    if entry_feature_val is None or current_feature_val is None:
        return False
    if np.isnan(entry_feature_val) or np.isnan(current_feature_val):
        return False
    entry_dist = abs(entry_feature_val - neutral_val)
    if entry_dist <= 0:
        return False
    current_dist = abs(current_feature_val - neutral_val)
    return current_dist <= entry_dist * 0.5


def simulate_trades(df, entry_mask, entry_feature_col, confirm_features=None,
                    cooldown_bars=5, label="signal"):
    """
    Simulate LONG trades with mechanism decay exit.

    Args:
        df: DataFrame with close prices and features
        entry_mask: boolean Series for entry signals
        entry_feature_col: primary feature to track for decay exit
        confirm_features: list of (feature_col, neutral_val) for secondary decay checks
        cooldown_bars: minimum bars between entries
        label: signal name for logging
    """
    close = df["close"].values
    feature_vals = df[entry_feature_col].values if entry_feature_col in df.columns else np.full(len(df), np.nan)
    neutral = NEUTRAL.get(entry_feature_col, 0.0)
    n = len(df)

    # Prepare confirm feature arrays
    confirm_arrays = []
    if confirm_features:
        for feat_col, feat_neutral in confirm_features:
            if feat_col in df.columns:
                confirm_arrays.append((df[feat_col].values, feat_neutral))

    trades = []
    last_exit_bar = -cooldown_bars

    entry_indices = np.where(entry_mask.values)[0]

    for entry_idx in entry_indices:
        if entry_idx - last_exit_bar < cooldown_bars:
            continue
        if entry_idx >= n - 2:
            continue

        entry_price = close[entry_idx]
        entry_feature_value = feature_vals[entry_idx]
        max_price = entry_price
        exit_bar = None
        exit_reason = None

        for j in range(1, TIME_CAP_BARS + 1):
            bar_idx = entry_idx + j
            if bar_idx >= n:
                exit_bar = bar_idx - 1
                exit_reason = "data_end"
                break

            current_price = close[bar_idx]
            current_ret = (current_price - entry_price) / entry_price
            max_price = max(max_price, current_price)
            mfe = (max_price - entry_price) / entry_price

            # 1. Hard stop
            if current_ret <= HARD_STOP_PCT:
                exit_bar = bar_idx
                exit_reason = "hard_stop"
                break

            # 2. MFE ratchet: once MFE > 0.15%, don't let it go below +0.02%
            if mfe > MFE_RATCHET_THR and current_ret < MFE_RATCHET_LOCK:
                exit_bar = bar_idx
                exit_reason = "mfe_ratchet"
                break

            # 3. Primary mechanism decay: entry feature reverts > 50% to neutral
            current_feature = feature_vals[bar_idx]
            if mechanism_decay_exit(entry_feature_value, current_feature, neutral):
                exit_bar = bar_idx
                exit_reason = "mechanism_decay"
                break

            # 4. Confirm decay: ANY confirm feature fully reverts past neutral
            for c_arr, c_neutral in confirm_arrays:
                c_val = c_arr[bar_idx]
                if not np.isnan(c_val):
                    # For LONG: if taker reverts below neutral (1.0) = buyers gone
                    if entry_feature_col == "oi_change_rate_5m":
                        # taker dropping below 1.0 = buyer support lost
                        if c_arr is not None and c_neutral == 1.0 and c_val < 0.92:
                            exit_bar = bar_idx
                            exit_reason = "confirm_decay"
                            break
                    elif c_neutral == 0.0:
                        # Feature reverted past neutral to opposite side
                        entry_c = c_arr[entry_idx]
                        if not np.isnan(entry_c) and entry_c > 0 and c_val < 0:
                            exit_bar = bar_idx
                            exit_reason = "confirm_decay"
                            break
                if exit_reason:
                    break
            if exit_reason:
                break

        if exit_bar is None:
            exit_bar = min(entry_idx + TIME_CAP_BARS, n - 1)
            exit_reason = "time_cap"

        exit_price = close[exit_bar]
        gross_ret = (exit_price - entry_price) / entry_price
        net_ret = gross_ret - MAKER_FEE_RT
        hold_bars = exit_bar - entry_idx

        # MFE/MAE over actual hold period
        actual_prices = close[entry_idx:exit_bar+1]
        actual_mfe = (actual_prices.max() - entry_price) / entry_price
        actual_mae = (actual_prices.min() - entry_price) / entry_price

        trades.append({
            "entry_bar": entry_idx,
            "exit_bar": exit_bar,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_ret": gross_ret,
            "net_ret": net_ret,
            "hold_bars": hold_bars,
            "exit_reason": exit_reason,
            "mfe": actual_mfe,
            "mae": actual_mae,
            "entry_feature": entry_feature_value,
        })
        last_exit_bar = exit_bar

    return pd.DataFrame(trades)


def report_trades(trades_df, label, is_end_bar):
    """Print trade statistics for IS and OOS periods."""
    if trades_df.empty:
        print(f"  {label}: 0 trades")
        return

    is_trades = trades_df[trades_df["entry_bar"] < is_end_bar]
    oos_trades = trades_df[trades_df["entry_bar"] >= is_end_bar]

    for period, tdf in [("IS", is_trades), ("OOS", oos_trades)]:
        if tdf.empty:
            print(f"  {label} [{period}]: 0 trades")
            continue

        n = len(tdf)
        gross_wr = (tdf["gross_ret"] > 0).mean()
        net_wr = (tdf["net_ret"] > 0).mean()
        avg_gross = tdf["gross_ret"].mean()
        avg_net = tdf["net_ret"].mean()
        avg_hold = tdf["hold_bars"].mean()
        avg_mfe = tdf["mfe"].mean()
        avg_mae = tdf["mae"].mean()

        # Exit reason distribution
        reasons = tdf["exit_reason"].value_counts()
        reason_str = ", ".join(f"{r}={c}" for r, c in reasons.items())

        print(f"\n  {label} [{period}] n={n}")
        print(f"    Gross WR: {gross_wr:.1%}  Net WR: {net_wr:.1%}")
        print(f"    Avg gross: {avg_gross:.4%}  Avg net: {avg_net:.4%}")
        print(f"    Avg hold: {avg_hold:.1f} bars  MFE: {avg_mfe:.3%}  MAE: {avg_mae:.3%}")
        print(f"    Exits: {reason_str}")

        # Win/loss by exit reason
        for reason in tdf["exit_reason"].unique():
            sub = tdf[tdf["exit_reason"] == reason]
            wr = (sub["net_ret"] > 0).mean()
            avg = sub["net_ret"].mean()
            print(f"      {reason}: n={len(sub)} wr={wr:.1%} avg_net={avg:.4%}")


def main():
    print("Loading features...")
    fe = FeatureEngine(storage_path="data/storage")
    df = fe.load_date_range("2026-02-18", "2026-04-07", include_heavy=True)
    print(f"Loaded {len(df):,} bars")

    print("Computing TREND_UP...")
    df["trend_up"] = compute_trend_up(df)
    print(f"  TREND_UP: {df['trend_up'].sum():,} ({df['trend_up'].mean()*100:.1f}%)")

    n = len(df)
    is_end = int(n * 0.6)
    print(f"  IS: bars 0-{is_end}, OOS: bars {is_end}-{n}")

    taker = df["taker_buy_sell_ratio"].fillna(1.0)
    oi5 = df["oi_change_rate_5m"].fillna(0)
    vol_ma = df["volume_vs_ma20"].fillna(1.0)
    liq_press = df["btc_liq_net_pressure"].fillna(0)
    pos24 = df["position_in_range_24h"].fillna(0.5)

    # ═══════════════════════════════════════════════════════════════════
    # OA-1: OI Accumulation LONG
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("OA-1: OI Accumulation LONG (mechanism decay exit)")
    print("=" * 70)

    oa1_configs = [
        {"oi_thr": 0.003, "taker_thr": 0.95, "vol_thr": 1.0},
        {"oi_thr": 0.003, "taker_thr": 1.0,  "vol_thr": 1.0},
        {"oi_thr": 0.003, "taker_thr": 1.05, "vol_thr": 1.0},
        {"oi_thr": 0.003, "taker_thr": 1.0,  "vol_thr": 1.2},
        {"oi_thr": 0.003, "taker_thr": 1.05, "vol_thr": 1.2},
        {"oi_thr": 0.005, "taker_thr": 0.95, "vol_thr": 0.8},
        {"oi_thr": 0.005, "taker_thr": 1.0,  "vol_thr": 0.8},
        {"oi_thr": 0.005, "taker_thr": 1.05, "vol_thr": 0.8},
        {"oi_thr": 0.002, "taker_thr": 1.0,  "vol_thr": 1.0},
        {"oi_thr": 0.002, "taker_thr": 1.05, "vol_thr": 1.0},
    ]

    for cfg in oa1_configs:
        mask = (
            df["trend_up"] &
            df["oi_change_rate_5m"].notna() &
            (oi5 > cfg["oi_thr"]) &
            (taker > cfg["taker_thr"]) &
            (vol_ma > cfg["vol_thr"])
        )
        label = f"oi>{cfg['oi_thr']}, taker>{cfg['taker_thr']}, vol>{cfg['vol_thr']}"
        trades = simulate_trades(
            df, mask,
            entry_feature_col="oi_change_rate_5m",
            confirm_features=[("taker_buy_sell_ratio", 1.0)],
            cooldown_bars=5,
            label=label,
        )
        report_trades(trades, label, is_end)

    # ═══════════════════════════════════════════════════════════════════
    # SQ-1: Short Squeeze LONG (re-evaluate with mechanism decay exit)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("SQ-1: Short Squeeze LONG (mechanism decay exit)")
    print("=" * 70)

    sq1_configs = [
        {"liq_thr": -0.1, "taker_thr": 0.95},
        {"liq_thr": -0.1, "taker_thr": 1.0},
        {"liq_thr": -0.15, "taker_thr": 0.95},
        {"liq_thr": -0.15, "taker_thr": 1.0},
        {"liq_thr": -0.2, "taker_thr": 0.95},
        {"liq_thr": -0.2, "taker_thr": 1.0},
        {"liq_thr": -0.05, "taker_thr": 1.0},
    ]

    for cfg in sq1_configs:
        mask = (
            df["trend_up"] &
            df["btc_liq_net_pressure"].notna() &
            (liq_press < cfg["liq_thr"]) &
            (taker > cfg["taker_thr"])
        )
        label = f"liq<{cfg['liq_thr']}, taker>{cfg['taker_thr']}"
        trades = simulate_trades(
            df, mask,
            entry_feature_col="btc_liq_net_pressure",
            confirm_features=[("taker_buy_sell_ratio", 1.0)],
            cooldown_bars=3,
            label=label,
        )
        report_trades(trades, label, is_end)

    # ═══════════════════════════════════════════════════════════════════
    # BI-1: Buyer Impulse LONG (re-evaluate with mechanism decay exit)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("BI-1: Buyer Impulse LONG (mechanism decay exit)")
    print("=" * 70)

    bi1_configs = [
        {"taker_thr": 1.10, "vol_thr": 1.0},
        {"taker_thr": 1.10, "vol_thr": 1.5},
        {"taker_thr": 1.15, "vol_thr": 1.0},
        {"taker_thr": 1.15, "vol_thr": 1.5},
        {"taker_thr": 1.20, "vol_thr": 1.0},
        {"taker_thr": 1.20, "vol_thr": 1.5},
        {"taker_thr": 1.25, "vol_thr": 1.5},
    ]

    for cfg in bi1_configs:
        mask = (
            df["trend_up"] &
            (taker > cfg["taker_thr"]) &
            (vol_ma > cfg["vol_thr"])
        )
        label = f"taker>{cfg['taker_thr']}, vol>{cfg['vol_thr']}"
        trades = simulate_trades(
            df, mask,
            entry_feature_col="taker_buy_sell_ratio",
            confirm_features=[("volume_vs_ma20", 1.0)],
            cooldown_bars=5,
            label=label,
        )
        report_trades(trades, label, is_end)

    print("\n" + "=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
