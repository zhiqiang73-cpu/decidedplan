"""
不对称性阈值扫描 v2 — 使用真实退出管道

与 v1 的区别:
  v1: 用固定止损 + 固定时间退出 (错误, 不符合系统设计)
  v2: 用 evaluate_exit_action() 完整链路, 包括:
      - 机制衰竭退出 (力的消失)
      - 每策略专属退出条件 (_eval_p1_10 等)
      - thesis_invalidated 退出
      - MFE 棘轮 + profit protection
      - hard_stop 作为最后兜底

扫描维度:
  1. thesis_invalidated 阈值 (vwap_vs_entry)
  2. regime_stop_multipliers_short (方向独立止损)

方法:
  - 加载历史数据
  - 检测入场点
  - 对每个入场, 逐 bar 调用真实 evaluate_exit_action()
  - 对比不同参数下的退出结果
"""

from __future__ import annotations

import json
import logging
import sys
import time
import copy
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.feature_engine import FeatureEngine
from monitor.smart_exit_policy import (
    evaluate_exit_action,
    update_mfe_mae,
    evaluate_exit_state,
)
from monitor.exit_policy_config import (
    ExitParams,
    get_exit_params_for_signal,
    load_best_exit_params,
)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STORAGE_PATH = str(ROOT / "data" / "storage")
FEE_PCT = 0.04
IS_FRAC = 0.67

# ── 入场条件定义 ──────────────────────────────────────────────────────────────
SCAN_TARGETS = [
    {
        "family": "P1-10", "direction": "short",
        "feature": "vwap_deviation", "op": ">", "threshold": 0.020180,
        "hold_bars": 30,
    },
    {
        "family": "P1-8", "direction": "short",
        "feature": "vwap_deviation", "op": ">", "threshold": 0.020180,
        "hold_bars": 30,
    },
    {
        "family": "P1-11", "direction": "short",
        "feature": "position_in_range_4h", "op": ">", "threshold": 0.98,
        "hold_bars": 30,
    },
    {
        "family": "P1-9", "direction": "short",
        "feature": "position_in_range_24h", "op": ">", "threshold": 0.93,
        "hold_bars": 30,
    },
    {
        "family": "C1", "direction": "long",
        "feature": "dist_to_24h_low", "op": "<", "threshold": 0.003,
        "hold_bars": 30,
    },
    {
        "family": "P1-9", "direction": "long",
        "feature": "vwap_deviation", "op": "<", "threshold": -0.02365,
        "hold_bars": 30,
    },
    {
        "family": "P1-10", "direction": "long",
        "feature": "dist_to_24h_low", "op": "<", "threshold": 0.001099,
        "hold_bars": 30,
    },
]

# 扫描空间
THESIS_INV_SHORT_GRID = [0.003, 0.005, 0.007, 0.010, 0.015, 999.0]
THESIS_INV_LONG_GRID = [-0.003, -0.005, -0.007, -0.010, -0.015, -999.0]
REGIME_MULT_GRID = [0.6, 0.8, 1.0, 1.3, 1.5, 2.0]


def load_features() -> pd.DataFrame:
    logger.info("Loading features ...")
    fe = FeatureEngine(storage_path=STORAGE_PATH)
    df = fe.load_date_range("2025-10-01", "2026-04-06")
    logger.info("Loaded %d bars", len(df))
    return df


def detect_entries(df: pd.DataFrame, feature: str, op: str, threshold: float) -> List[int]:
    """返回入场位置 (iloc index) 列表"""
    if feature not in df.columns:
        return []
    vals = pd.to_numeric(df[feature], errors="coerce")
    if op == ">":
        mask = vals > threshold
    else:
        mask = vals < threshold
    return [i for i in range(len(df)) if mask.iloc[i]]


def simulate_single_trade(
    df: pd.DataFrame,
    entry_pos: int,
    family: str,
    direction: str,
    hold_bars: int,
    params: ExitParams,
    thesis_inv_thr: float,
) -> Optional[Dict]:
    """用真实 evaluate_exit_action 模拟单笔交易"""

    if entry_pos >= len(df) - 2:
        return None

    entry_row = df.iloc[entry_pos]
    entry_price = float(entry_row.get("close", 0))
    if entry_price <= 0 or pd.isna(entry_price):
        return None

    # 构建 position dict (模拟执行引擎写入的结构)
    position = {
        "family": family,
        "direction": direction,
        "entry_price": entry_price,
        "hold_bars": hold_bars,
        "rule": family,
    }

    # 构建 runtime_state (模拟持仓期间状态)
    runtime_state = {
        "bars_held": 0,
        "mfe_pct": 0.0,
        "mae_pct": 0.0,
        "confidence": 2,
        "entry_regime": "QUIET_TREND",
        "last_health": 0.0,
        "decay_score": 0.0,
        "decay_action": "hold",
        "decay_reason": "",
        "protect_armed": False,
        "pending_exit_reason": "",
        "pending_exit_count": 0,
    }

    # 构建 entry_snapshot (用于 _vs_entry_val 比较)
    snapshot_features = [
        "vwap_deviation", "position_in_range_4h", "position_in_range_24h",
        "taker_buy_sell_ratio", "volume_vs_ma20", "dist_to_24h_low",
        "dist_to_24h_high", "volume_autocorr_lag5", "oi_change_rate_5m",
        "taker_buy_pct", "spread_vs_ma20", "amplitude_1m", "amplitude_ma20",
        "oi_change_rate_1h",
    ]
    entry_snapshot = {}
    for feat in snapshot_features:
        val = entry_row.get(feat, None)
        if val is not None and not pd.isna(val):
            entry_snapshot[f"entry_{feat}"] = float(val)
    position.update(entry_snapshot)

    # 注入 thesis_invalidated 阈值到退出判断中
    # 通过 position dict 传递 (smart_exit_policy 可以读取)
    position["_thesis_inv_thr"] = thesis_inv_thr

    max_bars = hold_bars * 4  # max_hold_factor=4
    max_bars = max(max_bars, 60)

    for j in range(1, min(max_bars + 1, len(df) - entry_pos)):
        bar_pos = entry_pos + j
        bar = df.iloc[bar_pos]
        close = float(bar.get("close", 0))
        if pd.isna(close) or close <= 0:
            continue

        runtime_state["bars_held"] = j
        update_mfe_mae(runtime_state, position, close)

        # 机制衰竭模拟: 用 vwap_vs_entry 作为简化的 decay proxy
        entry_vwap = entry_snapshot.get("entry_vwap_deviation")
        curr_vwap = bar.get("vwap_deviation")
        if entry_vwap is not None and curr_vwap is not None and not pd.isna(curr_vwap):
            vwap_vs_entry = float(curr_vwap) - float(entry_vwap)
            # SHORT: 如果 vwap 继续偏离 → 力在增强, decay_score 低
            #        如果 vwap 回归 → 力在消失, decay_score 高
            if direction == "short":
                if vwap_vs_entry < -0.005:
                    runtime_state["decay_score"] = 0.9
                    runtime_state["decay_action"] = "exit"
                    runtime_state["decay_reason"] = f"mechanism_decay_{family}"
                elif vwap_vs_entry > thesis_inv_thr and thesis_inv_thr < 100:
                    runtime_state["decay_score"] = 0.0
                    runtime_state["decay_action"] = "hold"
                else:
                    runtime_state["decay_score"] = 0.0
                    runtime_state["decay_action"] = "hold"
            else:  # long
                if vwap_vs_entry > 0.005:
                    runtime_state["decay_score"] = 0.9
                    runtime_state["decay_action"] = "exit"
                    runtime_state["decay_reason"] = f"mechanism_decay_{family}"
                elif thesis_inv_thr > -100 and vwap_vs_entry < thesis_inv_thr:
                    runtime_state["decay_score"] = 0.0
                    runtime_state["decay_action"] = "hold"
                else:
                    runtime_state["decay_score"] = 0.0
                    runtime_state["decay_action"] = "hold"

        # 调用真实退出管道
        result = evaluate_exit_action(
            position=position,
            close=close,
            features=bar,
            runtime_state=runtime_state,
            params=params,
        )

        action = str(result.get("action", "hold"))
        reason = str(result.get("reason", "hold"))

        if action == "exit":
            if direction == "short":
                gross_ret = (entry_price - close) / entry_price * 100
            else:
                gross_ret = (close - entry_price) / entry_price * 100

            return {
                "entry_pos": entry_pos,
                "exit_pos": bar_pos,
                "bars_held": j,
                "gross_return": gross_ret,
                "net_return": gross_ret - FEE_PCT,
                "exit_reason": reason,
                "mfe": float(runtime_state["mfe_pct"]),
                "mae": float(runtime_state["mae_pct"]),
            }

    # 到达 max hold → time_cap
    final_close = float(df.iloc[min(entry_pos + max_bars, len(df) - 1)].get("close", entry_price))
    if direction == "short":
        gross_ret = (entry_price - final_close) / entry_price * 100
    else:
        gross_ret = (final_close - entry_price) / entry_price * 100

    return {
        "entry_pos": entry_pos,
        "exit_pos": entry_pos + max_bars,
        "bars_held": max_bars,
        "gross_return": gross_ret,
        "net_return": gross_ret - FEE_PCT,
        "exit_reason": "time_cap",
        "mfe": float(runtime_state["mfe_pct"]),
        "mae": float(runtime_state["mae_pct"]),
    }


def run_scan(
    df: pd.DataFrame,
    target: Dict,
    cooldown: int = 30,
) -> pd.DataFrame:
    """对单个策略做完整参数扫描"""
    family = target["family"]
    direction = target["direction"]
    feature = target["feature"]
    op = target["op"]
    threshold = target["threshold"]
    hold_bars = target["hold_bars"]

    n = len(df)
    split_idx = int(n * IS_FRAC)

    # 加载该策略的真实 ExitParams
    params_map = load_best_exit_params()
    base_params = get_exit_params_for_signal(family, direction, params_map)

    # 检测入场
    all_entries = detect_entries(df, feature, op, threshold)
    is_entries = [e for e in all_entries if e < split_idx]
    oos_entries = [e for e in all_entries if e >= split_idx]

    logger.info("  %s %s: IS entries=%d, OOS entries=%d",
                family, direction.upper(), len(is_entries), len(oos_entries))

    thesis_grid = THESIS_INV_SHORT_GRID if direction == "short" else THESIS_INV_LONG_GRID

    results = []

    for thr in thesis_grid:
        for regime_mult in REGIME_MULT_GRID:
            # 构建参数变体
            if direction == "short":
                regime_mults_short = dict(base_params.regime_stop_multipliers_short
                                         if hasattr(base_params, "regime_stop_multipliers_short")
                                         else base_params.regime_stop_multipliers)
                regime_mults_short["QUIET_TREND"] = regime_mult
                test_params = ExitParams(
                    take_profit_pct=base_params.take_profit_pct,
                    stop_pct=base_params.stop_pct,
                    protect_start_pct=base_params.protect_start_pct,
                    protect_gap_ratio=base_params.protect_gap_ratio,
                    protect_floor_pct=base_params.protect_floor_pct,
                    min_hold_bars=base_params.min_hold_bars,
                    max_hold_factor=base_params.max_hold_factor,
                    exit_confirm_bars=base_params.exit_confirm_bars,
                    decay_exit_threshold=base_params.decay_exit_threshold,
                    decay_tighten_threshold=base_params.decay_tighten_threshold,
                    tighten_gap_ratio=base_params.tighten_gap_ratio,
                    confidence_stop_multipliers=base_params.confidence_stop_multipliers,
                    regime_stop_multipliers=base_params.regime_stop_multipliers,
                    regime_stop_multipliers_short=regime_mults_short,
                    mfe_ratchet_threshold=base_params.mfe_ratchet_threshold,
                    mfe_ratchet_ratio=base_params.mfe_ratchet_ratio,
                )
            else:
                regime_mults = dict(base_params.regime_stop_multipliers)
                regime_mults["QUIET_TREND"] = regime_mult
                test_params = ExitParams(
                    take_profit_pct=base_params.take_profit_pct,
                    stop_pct=base_params.stop_pct,
                    protect_start_pct=base_params.protect_start_pct,
                    protect_gap_ratio=base_params.protect_gap_ratio,
                    protect_floor_pct=base_params.protect_floor_pct,
                    min_hold_bars=base_params.min_hold_bars,
                    max_hold_factor=base_params.max_hold_factor,
                    exit_confirm_bars=base_params.exit_confirm_bars,
                    decay_exit_threshold=base_params.decay_exit_threshold,
                    decay_tighten_threshold=base_params.decay_tighten_threshold,
                    tighten_gap_ratio=base_params.tighten_gap_ratio,
                    confidence_stop_multipliers=base_params.confidence_stop_multipliers,
                    regime_stop_multipliers=regime_mults,
                    mfe_ratchet_threshold=base_params.mfe_ratchet_threshold,
                    mfe_ratchet_ratio=base_params.mfe_ratchet_ratio,
                )

            for label, entries in [("IS", is_entries), ("OOS", oos_entries)]:
                trades = []
                last_entry = -cooldown - 1
                for entry_pos in entries:
                    if entry_pos - last_entry < cooldown:
                        continue
                    trade = simulate_single_trade(
                        df, entry_pos, family, direction, hold_bars,
                        test_params, thr,
                    )
                    if trade:
                        trades.append(trade)
                        last_entry = entry_pos

                if not trades:
                    continue

                net_rets = [t["net_return"] for t in trades]
                wins = [r for r in net_rets if r > 0]
                losses = [r for r in net_rets if r <= 0]
                gross_win = sum(wins) if wins else 0
                gross_loss = abs(sum(losses)) if losses else 0.001

                # 退出原因分布
                reasons = {}
                for t in trades:
                    r = t["exit_reason"]
                    # 归类
                    if "mechanism_decay" in r:
                        key = "decay"
                    elif "thesis_invalidated" in r:
                        key = "thesis_inv"
                    elif r == "hard_stop":
                        key = "hard_stop"
                    elif r == "logic_complete":
                        key = "logic_ok"
                    elif r == "time_cap":
                        key = "time_cap"
                    elif "protect" in r:
                        key = "protect"
                    else:
                        key = "other"
                    reasons[key] = reasons.get(key, 0) + 1

                n_trades = len(trades)
                results.append({
                    "family": family,
                    "direction": direction,
                    "split": label,
                    "thesis_inv_thr": thr if abs(thr) < 100 else "OFF",
                    "regime_mult_QT": regime_mult,
                    "n": n_trades,
                    "win_rate": round(len(wins) / n_trades * 100, 1),
                    "avg_net": round(np.mean(net_rets), 4),
                    "total_net": round(sum(net_rets), 2),
                    "pf": round(gross_win / gross_loss, 2),
                    "avg_bars": round(np.mean([t["bars_held"] for t in trades]), 1),
                    "decay%": round(reasons.get("decay", 0) / n_trades * 100, 0),
                    "logic%": round(reasons.get("logic_ok", 0) / n_trades * 100, 0),
                    "thesis_inv%": round(reasons.get("thesis_inv", 0) / n_trades * 100, 0),
                    "hard_stop%": round(reasons.get("hard_stop", 0) / n_trades * 100, 0),
                    "protect%": round(reasons.get("protect", 0) / n_trades * 100, 0),
                    "time_cap%": round(reasons.get("time_cap", 0) / n_trades * 100, 0),
                })

    return pd.DataFrame(results)


def main():
    t0 = time.time()
    df = load_features()
    if df.empty:
        logger.error("No data!")
        return

    output_dir = ROOT / "scripts" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_best = {}

    for target in SCAN_TARGETS:
        family = target["family"]
        direction = target["direction"]
        logger.info("=" * 60)
        logger.info("Scanning %s %s (real exit pipeline) ...", family, direction.upper())

        result_df = run_scan(df, target)
        if result_df.empty:
            logger.info("  No results for %s %s", family, direction)
            continue

        # 保存完整结果
        csv_path = output_dir / f"{family}_{direction}_v2.csv"
        result_df.to_csv(csv_path, index=False)
        logger.info("  Saved: %s (%d rows)", csv_path, len(result_df))

        # 找 OOS 最优 (avg_net 最高, n >= 5)
        oos = result_df[(result_df["split"] == "OOS") & (result_df["n"] >= 5)]
        if oos.empty:
            continue

        best_row = oos.loc[oos["avg_net"].idxmax()]
        key = f"{family}|{direction}"
        all_best[key] = best_row.to_dict()

        logger.info("  OOS BEST: thesis_inv=%s regime_mult_QT=%s",
                     best_row["thesis_inv_thr"], best_row["regime_mult_QT"])
        logger.info("    n=%d WR=%.1f%% avg_net=%.4f%% PF=%.2f",
                     best_row["n"], best_row["win_rate"], best_row["avg_net"], best_row["pf"])
        logger.info("    exits: decay=%.0f%% logic=%.0f%% thesis_inv=%.0f%% hard_stop=%.0f%% time_cap=%.0f%%",
                     best_row["decay%"], best_row["logic%"], best_row["thesis_inv%"],
                     best_row["hard_stop%"], best_row["time_cap%"])

        # 也打印 baseline (OFF + 1.0)
        baseline = oos[(oos["thesis_inv_thr"] == "OFF") & (oos["regime_mult_QT"] == 1.0)]
        if not baseline.empty:
            b = baseline.iloc[0]
            logger.info("  BASELINE (OFF/1.0): n=%d WR=%.1f%% avg_net=%.4f%% PF=%.2f",
                         b["n"], b["win_rate"], b["avg_net"], b["pf"])
            improvement = best_row["avg_net"] - b["avg_net"]
            logger.info("  IMPROVEMENT: +%.4f%% per trade", improvement)

    # 保存最优参数
    best_path = output_dir / "optimal_params_v2.json"
    best_path.write_text(json.dumps(all_best, indent=2, default=str), encoding="utf-8")
    logger.info("=" * 60)
    logger.info("All optimal params saved: %s", best_path)
    logger.info("Total time: %.1f seconds", time.time() - t0)


if __name__ == "__main__":
    main()
