"""
剩余拍脑袋阈值扫描 — 用真实退出管道验证

扫描 3 个未验证的数字:
  1. MFE 棘轮下限系数 (base_stop * X, 当前 X=0.5)
  2. P1-10 SHORT 趋势守卫 r4h 阈值 (当前 0.85)
  3. A2 SHORT 高点新鲜度 dist_to_24h_high 阈值 (当前 -0.005)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.feature_engine import FeatureEngine
from monitor.smart_exit_policy import evaluate_exit_action, update_mfe_mae
from monitor.exit_policy_config import ExitParams, get_exit_params_for_signal, load_best_exit_params

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STORAGE_PATH = str(ROOT / "data" / "storage")
FEE_PCT = 0.04
IS_FRAC = 0.67

# ── 扫描空间 ──────────────────────────────────────────────────────────────────
MFE_FLOOR_GRID = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
R4H_GUARD_GRID = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.01]  # 1.01=全部阻止
A2_DIST_GRID = [-0.002, -0.003, -0.005, -0.007, -0.010, -0.015, -999.0]  # -999=不启用


def load_features() -> pd.DataFrame:
    logger.info("Loading features ...")
    fe = FeatureEngine(storage_path=STORAGE_PATH)
    df = fe.load_date_range("2025-10-01", "2026-04-06")
    logger.info("Loaded %d bars", len(df))
    return df


def detect_entries(df: pd.DataFrame, feature: str, op: str, threshold: float) -> List[int]:
    if feature not in df.columns:
        return []
    vals = pd.to_numeric(df[feature], errors="coerce")
    if op == ">":
        mask = vals > threshold
    else:
        mask = vals < threshold
    return [i for i in range(len(df)) if mask.iloc[i]]


def simulate_trade_with_mfe_floor(
    df: pd.DataFrame, entry_pos: int, family: str, direction: str,
    hold_bars: int, params: ExitParams, mfe_floor_ratio: float,
) -> Optional[Dict]:
    """模拟单笔交易, 用修改后的 MFE 棘轮下限"""
    if entry_pos >= len(df) - 2:
        return None

    entry_row = df.iloc[entry_pos]
    entry_price = float(entry_row.get("close", 0))
    if entry_price <= 0 or pd.isna(entry_price):
        return None

    position = {
        "family": family, "direction": direction,
        "entry_price": entry_price, "hold_bars": hold_bars, "rule": family,
    }
    for feat in ["vwap_deviation", "position_in_range_4h", "position_in_range_24h",
                 "taker_buy_sell_ratio", "volume_vs_ma20", "dist_to_24h_low",
                 "dist_to_24h_high", "volume_autocorr_lag5", "oi_change_rate_5m",
                 "taker_buy_pct", "spread_vs_ma20", "amplitude_1m", "amplitude_ma20",
                 "oi_change_rate_1h"]:
        val = entry_row.get(feat, None)
        if val is not None and not pd.isna(val):
            position[f"entry_{feat}"] = float(val)

    runtime_state = {
        "bars_held": 0, "mfe_pct": 0.0, "mae_pct": 0.0,
        "confidence": 2, "entry_regime": "QUIET_TREND",
        "last_health": 0.0, "decay_score": 0.0,
        "decay_action": "hold", "decay_reason": "",
        "protect_armed": False, "pending_exit_reason": "", "pending_exit_count": 0,
    }

    # 临时修改 params 的 mfe_ratchet_ratio 来模拟不同的 floor
    # 实际 floor 逻辑: effective_stop = min(effective_stop, max(ratcheted, base_stop * mfe_floor_ratio))
    # 我们通过 position dict 传递 floor ratio
    position["_mfe_floor_ratio"] = mfe_floor_ratio

    max_bars = max(hold_bars * 4, 60)

    # 机制衰竭用 vwap_vs_entry 做 proxy
    entry_vwap = position.get("entry_vwap_deviation")

    for j in range(1, min(max_bars + 1, len(df) - entry_pos)):
        bar = df.iloc[entry_pos + j]
        close = float(bar.get("close", 0))
        if pd.isna(close) or close <= 0:
            continue

        runtime_state["bars_held"] = j
        update_mfe_mae(runtime_state, position, close)

        if entry_vwap is not None:
            curr_vwap = bar.get("vwap_deviation")
            if curr_vwap is not None and not pd.isna(curr_vwap):
                vwap_vs = float(curr_vwap) - float(entry_vwap)
                if direction == "short" and vwap_vs < -0.005:
                    runtime_state["decay_score"] = 0.9
                    runtime_state["decay_action"] = "exit"
                    runtime_state["decay_reason"] = f"mechanism_decay_{family}"
                elif direction == "long" and vwap_vs > 0.005:
                    runtime_state["decay_score"] = 0.9
                    runtime_state["decay_action"] = "exit"
                    runtime_state["decay_reason"] = f"mechanism_decay_{family}"
                else:
                    runtime_state["decay_score"] = 0.0
                    runtime_state["decay_action"] = "hold"

        result = evaluate_exit_action(position, close, bar, runtime_state, params)
        if str(result.get("action", "hold")) == "exit":
            if direction == "short":
                gross_ret = (entry_price - close) / entry_price * 100
            else:
                gross_ret = (close - entry_price) / entry_price * 100
            return {
                "net_return": gross_ret - FEE_PCT,
                "exit_reason": str(result.get("reason", "")),
                "bars_held": j,
            }

    final_close = float(df.iloc[min(entry_pos + max_bars, len(df) - 1)].get("close", entry_price))
    if direction == "short":
        gross_ret = (entry_price - final_close) / entry_price * 100
    else:
        gross_ret = (final_close - entry_price) / entry_price * 100
    return {"net_return": gross_ret - FEE_PCT, "exit_reason": "time_cap", "bars_held": max_bars}


def compute_metrics(trades: List[Dict]) -> Dict:
    if not trades:
        return {"n": 0, "win_rate": 0, "avg_net": 0, "pf": 0}
    rets = [t["net_return"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_net": round(np.mean(rets), 4),
        "pf": round(sum(wins) / (abs(sum(losses)) or 0.001), 2),
        "hard_stop%": round(sum(1 for t in trades if t["exit_reason"] == "hard_stop") / len(trades) * 100, 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCAN 1: MFE 棘轮下限系数
# ══════════════════════════════════════════════════════════════════════════════
def scan_mfe_floor(df: pd.DataFrame) -> pd.DataFrame:
    """对全部 SHORT 策略扫描 MFE 棘轮下限"""
    logger.info("=== SCAN 1: MFE ratchet floor ratio ===")

    n = len(df)
    split_idx = int(n * IS_FRAC)

    targets = [
        ("P1-10", "short", "vwap_deviation", ">", 0.020180, 30),
        ("P1-8", "short", "vwap_deviation", ">", 0.020180, 30),
        ("P1-11", "short", "position_in_range_4h", ">", 0.98, 30),
    ]

    results = []
    params_map = load_best_exit_params()

    for family, direction, feature, op, threshold, hold in targets:
        base_params = get_exit_params_for_signal(family, direction, params_map)
        all_entries = detect_entries(df, feature, op, threshold)
        oos_entries = [e for e in all_entries if e >= split_idx]

        for floor_ratio in MFE_FLOOR_GRID:
            trades = []
            last_entry = -31
            for entry_pos in oos_entries:
                if entry_pos - last_entry < 30:
                    continue
                t = simulate_trade_with_mfe_floor(
                    df, entry_pos, family, direction, hold, base_params, floor_ratio
                )
                if t:
                    trades.append(t)
                    last_entry = entry_pos

            m = compute_metrics(trades)
            results.append({
                "family": family, "direction": direction,
                "mfe_floor_ratio": floor_ratio,
                **m,
            })

        logger.info("  %s %s done", family, direction)

    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════════
# SCAN 2: P1-10 SHORT 趋势守卫 r4h 阈值
# ══════════════════════════════════════════════════════════════════════════════
def scan_r4h_guard(df: pd.DataFrame) -> pd.DataFrame:
    """扫描 P1-10 SHORT 趋势守卫: slope>0 时需要 r4h > X 才允许入场"""
    logger.info("=== SCAN 2: P1-10 SHORT r4h guard threshold ===")

    n = len(df)
    split_idx = int(n * IS_FRAC)
    params_map = load_best_exit_params()
    base_params = get_exit_params_for_signal("P1-10", "short", params_map)

    results = []

    for r4h_thr in R4H_GUARD_GRID:
        # 检测满足条件的入场: vwap_dev > 0.020180 + (slope<=0 OR r4h>=thr)
        entries_oos = []
        for i in range(split_idx, len(df)):
            row = df.iloc[i]
            vwap_dev = row.get("vwap_deviation")
            if vwap_dev is None or pd.isna(vwap_dev) or vwap_dev <= 0.020180:
                continue

            # 计算 slope
            if i >= 20 and "close" in df.columns:
                closes = df["close"].iloc[i-19:i+1].values
                try:
                    slope = np.polyfit(range(20), closes, 1)[0]
                except Exception:
                    slope = 0
            else:
                slope = 0

            if slope > 0:
                r4h = row.get("position_in_range_4h")
                if r4h is None or pd.isna(r4h) or r4h < r4h_thr:
                    continue  # 趋势向上且不在极顶部 → 阻止

            entries_oos.append(i)

        # 模拟交易
        trades = []
        last_entry = -31
        for entry_pos in entries_oos:
            if entry_pos - last_entry < 30:
                continue
            t = simulate_trade_with_mfe_floor(
                df, entry_pos, "P1-10", "short", 30, base_params, 0.5
            )
            if t:
                trades.append(t)
                last_entry = entry_pos

        m = compute_metrics(trades)
        results.append({
            "r4h_guard": r4h_thr if r4h_thr <= 1.0 else "BLOCK_ALL",
            "entries_after_filter": len(entries_oos),
            **m,
        })
        logger.info("  r4h=%.2f: n=%d WR=%.1f%% avg_net=%.4f%%",
                     r4h_thr, m["n"], m["win_rate"], m["avg_net"])

    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════════
# SCAN 3: A2 SHORT 高点新鲜度 dist_to_24h_high 阈值
# ══════════════════════════════════════════════════════════════════════════════
def scan_a2_dist_guard(df: pd.DataFrame) -> pd.DataFrame:
    """扫描 A2 SHORT 高点新鲜度: slope>0 且 dist > thr 时阻止"""
    logger.info("=== SCAN 3: A2 SHORT dist_to_24h_high guard ===")

    n = len(df)
    split_idx = int(n * IS_FRAC)
    params_map = load_best_exit_params()
    base_params = get_exit_params_for_signal("A2-29", "short", params_map)

    results = []

    for dist_thr in A2_DIST_GRID:
        entries_oos = []
        for i in range(split_idx, len(df)):
            row = df.iloc[i]
            # A2-29 入场条件: dist_to_24h_high > -0.009746
            dist_high = row.get("dist_to_24h_high")
            if dist_high is None or pd.isna(dist_high) or dist_high <= -0.009746:
                continue

            # 高点新鲜度守卫
            if dist_thr > -100:
                if i >= 20 and "close" in df.columns:
                    closes = df["close"].iloc[i-19:i+1].values
                    try:
                        slope = np.polyfit(range(20), closes, 1)[0]
                    except Exception:
                        slope = 0
                else:
                    slope = 0

                if slope > 0 and dist_high > dist_thr:
                    continue  # 上涨趋势 + 离高点太近(趋势新高) → 阻止

            entries_oos.append(i)

        trades = []
        last_entry = -61
        for entry_pos in entries_oos:
            if entry_pos - last_entry < 60:
                continue
            t = simulate_trade_with_mfe_floor(
                df, entry_pos, "A2-29", "short", 60, base_params, 0.5
            )
            if t:
                trades.append(t)
                last_entry = entry_pos

        m = compute_metrics(trades)
        results.append({
            "dist_guard": dist_thr if dist_thr > -100 else "OFF",
            "entries_after_filter": len(entries_oos),
            **m,
        })
        logger.info("  dist=%.3f: n=%d WR=%.1f%% avg_net=%.4f%%",
                     dist_thr if dist_thr > -100 else -999, m["n"], m["win_rate"], m["avg_net"])

    return pd.DataFrame(results)


def main():
    t0 = time.time()
    df = load_features()
    if df.empty:
        logger.error("No data!")
        return

    output_dir = ROOT / "scripts" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Scan 1
    df_mfe = scan_mfe_floor(df)
    df_mfe.to_csv(output_dir / "mfe_floor_scan.csv", index=False)
    logger.info("MFE floor results saved")

    # Scan 2
    df_r4h = scan_r4h_guard(df)
    df_r4h.to_csv(output_dir / "r4h_guard_scan.csv", index=False)
    logger.info("R4H guard results saved")

    # Scan 3
    df_a2 = scan_a2_dist_guard(df)
    df_a2.to_csv(output_dir / "a2_dist_guard_scan.csv", index=False)
    logger.info("A2 dist guard results saved")

    # 汇总最优
    recommendations = {}

    # MFE floor - 按策略分组找最优
    for family in df_mfe["family"].unique():
        sub = df_mfe[df_mfe["family"] == family]
        valid = sub[sub["n"] >= 5]
        if not valid.empty:
            best = valid.loc[valid["avg_net"].idxmax()]
            recommendations[f"mfe_floor|{family}"] = {
                "mfe_floor_ratio": best["mfe_floor_ratio"],
                "avg_net": best["avg_net"], "pf": best["pf"],
                "n": int(best["n"]), "win_rate": best["win_rate"],
            }
            logger.info("MFE floor %s: best=%.1f, avg_net=%.4f%%",
                         family, best["mfe_floor_ratio"], best["avg_net"])

    # R4H guard
    valid = df_r4h[df_r4h["n"] >= 5]
    if not valid.empty:
        best = valid.loc[valid["avg_net"].idxmax()]
        recommendations["r4h_guard"] = {
            "r4h_threshold": best["r4h_guard"],
            "avg_net": best["avg_net"], "pf": best["pf"],
            "n": int(best["n"]), "win_rate": best["win_rate"],
        }
        logger.info("R4H guard: best=%s, avg_net=%.4f%%", best["r4h_guard"], best["avg_net"])

    # A2 dist guard
    valid = df_a2[df_a2["n"] >= 5]
    if not valid.empty:
        best = valid.loc[valid["avg_net"].idxmax()]
        recommendations["a2_dist_guard"] = {
            "dist_threshold": best["dist_guard"],
            "avg_net": best["avg_net"], "pf": best["pf"],
            "n": int(best["n"]), "win_rate": best["win_rate"],
        }
        logger.info("A2 dist guard: best=%s, avg_net=%.4f%%", best["dist_guard"], best["avg_net"])

    rec_path = output_dir / "remaining_optimal.json"
    rec_path.write_text(json.dumps(recommendations, indent=2, default=str), encoding="utf-8")
    logger.info("All saved: %s", rec_path)
    logger.info("Total: %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
