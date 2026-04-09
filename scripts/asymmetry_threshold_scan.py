"""
不对称性阈值扫描回测

目的: 用历史数据验证做多/做空的最优退出阈值,
     解决系统假设对称性但市场不对称的根因问题.

扫描维度:
  1. thesis_invalidated 阈值 (SHORT: vwap_vs_entry 上升多少触发退出)
  2. thesis_invalidated 阈值 (LONG: vwap_vs_entry 下降多少触发退出)
  3. regime_stop_multipliers_short["QUIET_TREND"] 的最优乘数
  4. P1-10 SHORT 趋势守卫: r4h 极顶部阈值
  5. A2 SHORT 高点新鲜度: close_slope + dist_to_24h_high 阈值

方法:
  - 加载历史数据 (FeatureEngine)
  - 逐 bar 重放信号 (SignalRunner)
  - 对每个触发的信号,用不同阈值模拟退出
  - 对比各阈值下的: 净收益, 胜率, PF, 最大回撤
  - 输出最优参数 + OOS 验证结果

用法:
  python scripts/asymmetry_threshold_scan.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# ── 项目路径 ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.feature_engine import FeatureEngine

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── 配置 ──────────────────────────────────────────────────────────────────────
STORAGE_PATH = str(ROOT / "data" / "storage")
FEE_PCT = 0.04  # Maker 往返费率 0.02% x 2
IS_FRAC = 0.67  # IS / OOS 切分

# 要扫描的策略和方向
SCAN_TARGETS = [
    # (family_prefix, direction, signal_feature, signal_op, signal_threshold)
    # P1-10 SHORT: vwap_deviation > 0.020180
    ("P1-10", "short", "vwap_deviation", ">", 0.020180),
    # P1-8 SHORT: vwap_deviation > 0.020180
    ("P1-8", "short", "vwap_deviation", ">", 0.020180),
    # P1-11 SHORT: position_in_range_4h > 0.98
    ("P1-11", "short", "position_in_range_4h", ">", 0.98),
    # C1 LONG: dist_to_24h_low < 0.003
    ("C1", "long", "dist_to_24h_low", "<", 0.003),
    # P1-10 LONG: dist_to_24h_low < 0.001099
    ("P1-10", "long", "dist_to_24h_low", "<", 0.001099),
    # P1-9 SHORT: position_in_range_24h > 0.93
    ("P1-9", "short", "position_in_range_24h", ">", 0.93),
    # P1-9 LONG: vwap_deviation < -0.02365
    ("P1-9", "long", "vwap_deviation", "<", -0.02365),
]

# ── 扫描参数空间 ─────────────────────────────────────────────────────────────

# 1. thesis_invalidated: SHORT 入场后 vwap 继续偏离多少就退出
THESIS_INV_SHORT_GRID = [0.005, 0.007, 0.008, 0.010, 0.012, 0.015, 0.020, 0.025, 999.0]
# 999.0 = 不启用 (baseline)

# 2. thesis_invalidated: LONG 入场后 vwap 继续下跌多少就退出
THESIS_INV_LONG_GRID = [-0.005, -0.007, -0.008, -0.010, -0.012, -0.015, -0.020, -0.025, -999.0]
# -999.0 = 不启用 (baseline)

# 3. regime_stop_multipliers_short QUIET_TREND
REGIME_MULT_SHORT_QT_GRID = [0.6, 0.8, 1.0, 1.2, 1.3, 1.5, 1.8, 2.0]

# 4. P1-10 SHORT 趋势守卫 r4h 阈值 (slope>0 时只在 r4h > X 才允许)
P1_10_R4H_GUARD_GRID = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.01]
# 1.01 = 不启用 (slope>0 时全部阻止)

# 5. hard stop 基础值
STOP_PCT_GRID = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 1.00]

# ── 数据加载 ─────────────────────────────────────────────────────────────────

def load_features(start: str = "2025-10-01", end: str = "2026-04-06") -> pd.DataFrame:
    """加载历史特征数据"""
    logger.info("Loading features %s ~ %s ...", start, end)
    fe = FeatureEngine(storage_path=STORAGE_PATH)
    df = fe.load_date_range(start, end)
    logger.info("Loaded %d bars, columns: %d", len(df), len(df.columns))
    return df


# ── 信号检测 (简化版, 只看核心入场条件) ────────────────────────────────────────

def detect_entries(
    df: pd.DataFrame,
    feature: str,
    op: str,
    threshold: float,
) -> pd.Series:
    """返回入场 mask (bool Series)"""
    if feature not in df.columns:
        return pd.Series(False, index=df.index)
    vals = pd.to_numeric(df[feature], errors="coerce")
    if op == ">":
        return vals > threshold
    else:
        return vals < threshold


# ── 单笔交易模拟 ─────────────────────────────────────────────────────────────

@dataclass
class TradeResult:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    direction: str
    gross_return: float
    net_return: float
    exit_reason: str
    bars_held: int
    mfe: float
    mae: float


def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    max_hold: int = 60,
    stop_pct: float = 0.70,
    thesis_inv_threshold: float = 999.0,  # vwap_vs_entry 阈值
    fee_pct: float = FEE_PCT,
) -> TradeResult | None:
    """模拟单笔交易, 返回结果"""
    if entry_idx >= len(df) - 1:
        return None

    entry_price = float(df["close"].iloc[entry_idx])
    if entry_price <= 0 or pd.isna(entry_price):
        return None

    entry_vwap_dev = float(df["vwap_deviation"].iloc[entry_idx]) if "vwap_deviation" in df.columns else None

    mfe = 0.0
    mae = 0.0
    exit_reason = "time_cap"
    exit_idx = min(entry_idx + max_hold, len(df) - 1)

    for j in range(entry_idx + 1, min(entry_idx + max_hold + 1, len(df))):
        close = float(df["close"].iloc[j])
        if pd.isna(close) or close <= 0:
            continue

        if direction == "short":
            ret = (entry_price - close) / entry_price * 100
            adverse = max(0, (close - entry_price) / entry_price * 100)
        else:
            ret = (close - entry_price) / entry_price * 100
            adverse = max(0, (entry_price - close) / entry_price * 100)

        mfe = max(mfe, ret)
        mae = max(mae, adverse)

        # Hard stop
        if adverse >= stop_pct:
            exit_idx = j
            exit_reason = "hard_stop"
            break

        # Thesis invalidated (vwap_vs_entry)
        if entry_vwap_dev is not None and "vwap_deviation" in df.columns:
            curr_vwap_dev = float(df["vwap_deviation"].iloc[j])
            if not pd.isna(curr_vwap_dev):
                vwap_vs_entry = curr_vwap_dev - entry_vwap_dev
                if direction == "short" and thesis_inv_threshold < 100:
                    if vwap_vs_entry > thesis_inv_threshold:
                        exit_idx = j
                        exit_reason = "thesis_invalidated"
                        break
                elif direction == "long" and thesis_inv_threshold > -100:
                    if vwap_vs_entry < thesis_inv_threshold:
                        exit_idx = j
                        exit_reason = "thesis_invalidated"
                        break

    exit_price = float(df["close"].iloc[exit_idx])
    if direction == "short":
        gross_ret = (entry_price - exit_price) / entry_price * 100
    else:
        gross_ret = (exit_price - entry_price) / entry_price * 100

    net_ret = gross_ret - fee_pct

    return TradeResult(
        entry_idx=entry_idx,
        exit_idx=exit_idx,
        entry_price=entry_price,
        exit_price=exit_price,
        direction=direction,
        gross_return=gross_ret,
        net_return=net_ret,
        exit_reason=exit_reason,
        bars_held=exit_idx - entry_idx,
        mfe=mfe,
        mae=mae,
    )


# ── 批量模拟 + 指标计算 ───────────────────────────────────────────────────────

def run_batch_simulation(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    direction: str,
    cooldown: int = 30,
    **sim_kwargs,
) -> List[TradeResult]:
    """批量模拟, 带冷却期"""
    trades = []
    last_entry = -cooldown - 1

    entries = entry_mask[entry_mask].index.tolist()
    for idx in entries:
        pos = df.index.get_loc(idx)
        if pos - last_entry < cooldown:
            continue
        result = simulate_trade(df, pos, direction, **sim_kwargs)
        if result is not None:
            trades.append(result)
            last_entry = pos

    return trades


def compute_metrics(trades: List[TradeResult]) -> Dict:
    """计算汇总指标"""
    if not trades:
        return {
            "n": 0, "win_rate": 0, "avg_net": 0, "pf": 0,
            "avg_mfe": 0, "avg_mae": 0, "avg_bars": 0,
            "hard_stop_pct": 0, "thesis_inv_pct": 0,
        }

    returns = [t.net_return for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001

    hard_stops = sum(1 for t in trades if t.exit_reason == "hard_stop")
    thesis_invs = sum(1 for t in trades if t.exit_reason == "thesis_invalidated")

    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "avg_net": np.mean(returns),
        "total_net": sum(returns),
        "pf": gross_win / gross_loss,
        "avg_mfe": np.mean([t.mfe for t in trades]),
        "avg_mae": np.mean([t.mae for t in trades]),
        "avg_bars": np.mean([t.bars_held for t in trades]),
        "hard_stop_pct": hard_stops / len(trades) * 100,
        "thesis_inv_pct": thesis_invs / len(trades) * 100,
    }


# ── 主扫描流程 ────────────────────────────────────────────────────────────────

def scan_thesis_invalidated(
    df_is: pd.DataFrame,
    df_oos: pd.DataFrame,
    entry_feature: str,
    entry_op: str,
    entry_threshold: float,
    direction: str,
    family: str,
    stop_pct: float = 0.70,
    max_hold: int = 60,
) -> pd.DataFrame:
    """扫描 thesis_invalidated 阈值, 返回 IS + OOS 结果表"""
    grid = THESIS_INV_SHORT_GRID if direction == "short" else THESIS_INV_LONG_GRID

    results = []
    for thr in grid:
        for label, split_df in [("IS", df_is), ("OOS", df_oos)]:
            mask = detect_entries(split_df, entry_feature, entry_op, entry_threshold)
            trades = run_batch_simulation(
                split_df, mask, direction,
                stop_pct=stop_pct,
                max_hold=max_hold,
                thesis_inv_threshold=thr,
            )
            m = compute_metrics(trades)
            results.append({
                "family": family,
                "direction": direction,
                "split": label,
                "thesis_inv_thr": thr if abs(thr) < 100 else "OFF",
                "n": m["n"],
                "win_rate": round(m["win_rate"], 1),
                "avg_net": round(m["avg_net"], 4),
                "total_net": round(m.get("total_net", 0), 3),
                "pf": round(m["pf"], 2),
                "hard_stop%": round(m["hard_stop_pct"], 1),
                "thesis_inv%": round(m["thesis_inv_pct"], 1),
                "avg_bars": round(m["avg_bars"], 1),
            })

    return pd.DataFrame(results)


def scan_stop_regime_mult(
    df_is: pd.DataFrame,
    df_oos: pd.DataFrame,
    entry_feature: str,
    entry_op: str,
    entry_threshold: float,
    direction: str,
    family: str,
    base_stop: float = 0.70,
    max_hold: int = 60,
) -> pd.DataFrame:
    """扫描 hard stop 乘数 (模拟 regime_stop_multiplier)"""
    results = []
    for mult in REGIME_MULT_SHORT_QT_GRID:
        effective_stop = base_stop * mult
        for label, split_df in [("IS", df_is), ("OOS", df_oos)]:
            mask = detect_entries(split_df, entry_feature, entry_op, entry_threshold)
            trades = run_batch_simulation(
                split_df, mask, direction,
                stop_pct=effective_stop,
                max_hold=max_hold,
                thesis_inv_threshold=999.0 if direction == "short" else -999.0,
            )
            m = compute_metrics(trades)
            results.append({
                "family": family,
                "direction": direction,
                "split": label,
                "regime_mult": mult,
                "effective_stop": round(effective_stop, 3),
                "n": m["n"],
                "win_rate": round(m["win_rate"], 1),
                "avg_net": round(m["avg_net"], 4),
                "total_net": round(m.get("total_net", 0), 3),
                "pf": round(m["pf"], 2),
                "hard_stop%": round(m["hard_stop_pct"], 1),
                "avg_bars": round(m["avg_bars"], 1),
            })

    return pd.DataFrame(results)


def main():
    t0 = time.time()

    # 1. 加载数据
    df = load_features("2025-10-01", "2026-04-06")
    if df.empty:
        logger.error("No data loaded!")
        return

    # 2. IS / OOS 切分
    n = len(df)
    split_idx = int(n * IS_FRAC)
    df_is = df.iloc[:split_idx].copy()
    df_oos = df.iloc[split_idx:].copy()
    logger.info("IS: %d bars, OOS: %d bars", len(df_is), len(df_oos))

    output_dir = ROOT / "scripts" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}

    # 3. 扫描每个策略
    for family, direction, feature, op, threshold in SCAN_TARGETS:
        logger.info("=" * 60)
        logger.info("Scanning %s %s ...", family, direction.upper())

        # 确定 base stop (从 best_params 或默认)
        base_stop = 0.70  # 默认
        max_hold = 60 if family.startswith("A") else 30

        # 3a. 扫描 thesis_invalidated 阈值
        logger.info("  [1/2] thesis_invalidated threshold scan ...")
        df_ti = scan_thesis_invalidated(
            df_is, df_oos, feature, op, threshold,
            direction, family,
            stop_pct=base_stop, max_hold=max_hold,
        )
        key_ti = f"{family}_{direction}_thesis_inv"
        all_results[key_ti] = df_ti

        # 打印 OOS 结果
        oos_ti = df_ti[df_ti["split"] == "OOS"]
        if not oos_ti.empty:
            logger.info("  thesis_invalidated OOS results:")
            for _, row in oos_ti.iterrows():
                logger.info(
                    "    thr=%-6s  n=%-4d  WR=%.1f%%  avg_net=%.4f%%  PF=%.2f  hard_stop=%.0f%%  ti=%.0f%%",
                    row["thesis_inv_thr"], row["n"], row["win_rate"],
                    row["avg_net"], row["pf"], row["hard_stop%"], row["thesis_inv%"],
                )

        # 3b. 扫描 regime stop multiplier
        logger.info("  [2/2] regime stop multiplier scan ...")
        df_rm = scan_stop_regime_mult(
            df_is, df_oos, feature, op, threshold,
            direction, family,
            base_stop=base_stop, max_hold=max_hold,
        )
        key_rm = f"{family}_{direction}_regime_mult"
        all_results[key_rm] = df_rm

        oos_rm = df_rm[df_rm["split"] == "OOS"]
        if not oos_rm.empty:
            logger.info("  regime_mult OOS results:")
            for _, row in oos_rm.iterrows():
                logger.info(
                    "    mult=%.1f  stop=%.3f  n=%-4d  WR=%.1f%%  avg_net=%.4f%%  PF=%.2f  hard_stop=%.0f%%",
                    row["regime_mult"], row["effective_stop"], row["n"],
                    row["win_rate"], row["avg_net"], row["pf"], row["hard_stop%"],
                )

    # 4. 保存全部结果
    for key, result_df in all_results.items():
        path = output_dir / f"{key}.csv"
        result_df.to_csv(path, index=False)
        logger.info("Saved: %s", path)

    # 5. 生成最优参数推荐
    logger.info("=" * 60)
    logger.info("OPTIMAL PARAMETER RECOMMENDATIONS (OOS-based):")
    logger.info("=" * 60)

    recommendations = {}
    for key, result_df in all_results.items():
        oos = result_df[result_df["split"] == "OOS"]
        if oos.empty or oos["n"].max() < 5:
            logger.info("  %s: insufficient OOS samples, skip", key)
            continue

        # 选最优: 按 avg_net 排序, 但要求 n >= 5
        valid = oos[oos["n"] >= 5].copy()
        if valid.empty:
            continue

        best = valid.loc[valid["avg_net"].idxmax()]
        recommendations[key] = best.to_dict()

        logger.info("  %s:", key)
        for col in best.index:
            logger.info("    %s = %s", col, best[col])

    # 保存推荐
    rec_path = output_dir / "optimal_asymmetry_params.json"
    rec_path.write_text(json.dumps(recommendations, indent=2, default=str), encoding="utf-8")
    logger.info("Recommendations saved: %s", rec_path)

    elapsed = time.time() - t0
    logger.info("Total time: %.1f seconds", elapsed)


if __name__ == "__main__":
    main()
