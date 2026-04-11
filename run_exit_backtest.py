"""
出场回测：对 approved_rules.json 中的已批准策略，
逐 bar 模拟完整出场逻辑（止损 / 保本 / 力消失），
对比固定持仓基准，输出净收益 / 胜率对比表。

用法：
  python run_exit_backtest.py
  python run_exit_backtest.py --days 180
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

import numpy as np
import pandas as pd

from core.feature_engine import FeatureEngine
from alpha.live_discovery import LiveDiscoveryEngine
from alpha.combo_scanner import _crossing_mask
from monitor.exit_policy_config import ExitParams

logger = logging.getLogger(__name__)

_APPROVED_FILE = ROOT / "alpha" / "output" / "approved_rules.json"
FEE_PCT = 0.04  # 来回 Maker 0.04%


def _apply_op(series: pd.Series, op: str, threshold: float) -> pd.Series:
    """把 > / < / >= / <= 操作符应用到 Series，返回 bool Series。"""
    if op == ">":
        return series > threshold
    if op == "<":
        return series < threshold
    if op == ">=":
        return series >= threshold
    if op == "<=":
        return series <= threshold
    raise ValueError(f"不支持的操作符: {op}")


def _build_entry_mask(df: pd.DataFrame, rule: dict) -> pd.Series:
    """
    从策略卡片的入场条件重建入场掩码，与 WalkForward 保持一致：
    - 主条件（种子）：穿越检测 + 60 bar 冷却（rising edge only）
    - 确认因子（combo_conditions）：主条件触发时的静态检查（AND 关系）
    """
    entry = rule.get("entry", {})
    feature = entry.get("feature", "")
    op = entry.get("operator", ">")
    threshold = float(entry.get("threshold", 0))

    if feature not in df.columns:
        logger.warning("入场特征 %s 不在数据列中，跳过", feature)
        return pd.Series(False, index=df.index)

    # 种子：穿越检测（与 WalkForward._build_combo_entry_mask 一致）
    seed_arr = _crossing_mask(df[feature].values, op, threshold, cooldown=60)
    mask = pd.Series(seed_arr, index=df.index)

    # 确认因子：静态条件（在种子触发时同时满足才入场）
    for cond in rule.get("combo_conditions", []):
        feat = cond.get("feature", "")
        cond_op = cond.get("op") or cond.get("operator", ">")
        cond_thr = float(cond.get("threshold", 0))
        if feat not in df.columns:
            logger.warning("确认因子 %s 不在数据列中，忽略该条件", feat)
            continue
        mask = mask & _apply_op(df[feat], cond_op, cond_thr)

    return mask


def _fixed_hold_backtest(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    direction: str,
    horizon: int,
    split_idx: int,
) -> dict:
    """固定持仓 horizon 根 bar 的 OOS 基准。"""
    close_arr = df["close"].values
    n = len(close_arr)
    sign = -1.0 if direction == "short" else 1.0
    oos_positions = [
        i for i in range(split_idx, n)
        if entry_mask.iloc[i] and (i + horizon) < n
    ]
    if not oos_positions:
        return {"n": 0, "win_rate": 0.0, "avg_net": 0.0}

    rets = []
    for idx in oos_positions:
        ep = close_arr[idx]
        xp = close_arr[idx + horizon]
        if ep == 0 or np.isnan(ep) or np.isnan(xp):
            continue
        ret = (xp - ep) / ep * sign * 100.0 - FEE_PCT
        rets.append(ret)

    if not rets:
        return {"n": 0, "win_rate": 0.0, "avg_net": 0.0}

    win_rate = sum(1 for r in rets if r > 0) / len(rets) * 100.0
    avg_net = float(np.mean(rets))
    return {"n": len(rets), "win_rate": win_rate, "avg_net": avg_net}


def run_backtest(data_days: int = 365) -> None:
    if not _APPROVED_FILE.exists():
        print("approved_rules.json 不存在，无法回测")
        return

    rules = json.loads(_APPROVED_FILE.read_text(encoding="utf-8"))
    if not rules:
        print("approved_rules.json 为空")
        return

    end_date = date.today()
    start_date = end_date - timedelta(days=data_days)
    print(f"\n加载特征数据: {start_date} ~ {end_date} ({data_days}天)...")

    engine_fe = FeatureEngine()
    df = engine_fe.load_date_range(str(start_date), str(end_date))
    if df.empty:
        print("数据加载失败")
        return
    print(f"加载完成: {len(df):,} 根 K 线，{df.shape[1]} 列特征\n")

    # 准备 LiveDiscoveryEngine（只用于调用 _shadow_smart_exit_backtest）
    discovery_engine = LiveDiscoveryEngine()

    split_idx = int(len(df) * 0.67)  # OOS = 后 33%
    oos_start_rows = len(df) - split_idx
    print(f"OOS 段: 最后 {oos_start_rows:,} 行（{33:.0f}%）\n")

    header = (
        f"{'策略':<40} {'方向':<6} {'固定持仓WR':>10} {'固定净收益':>10}"
        f" {'智能出场WR':>10} {'智能净收益':>10} {'改善':>8} {'n':>5}"
    )
    print(header)
    print("-" * len(header))

    for rule in rules:
        rule_id = rule.get("id", "?")[:35]
        entry = rule.get("entry", {})
        direction = entry.get("direction", "long")
        horizon = int(entry.get("horizon", 30))
        origin = rule.get("origin", {})
        mechanism = (origin.get("mechanism_type") if isinstance(origin, dict) else None) or rule.get("group", "?")

        # 策略简称
        seed = entry.get("feature", "?")
        label = f"{seed[:25]}({direction[0].upper()})"

        # 重建入场掩码
        entry_mask = _build_entry_mask(df, rule)
        n_total_signals = entry_mask.sum()
        if n_total_signals == 0:
            print(f"{label:<40} 无入场信号（特征缺失或阈值无匹配）")
            continue

        # 固定持仓基准
        baseline = _fixed_hold_backtest(df, entry_mask, direction, horizon, split_idx)

        # 出场参数
        ep_raw = rule.get("exit_params", {})
        exit_params = ExitParams(
            take_profit_pct=float(ep_raw.get("take_profit_pct", 0.0)),
            stop_pct=float(ep_raw.get("stop_pct", 0.70)),
            protect_start_pct=float(ep_raw.get("protect_start_pct", 0.12)),
            protect_gap_ratio=float(ep_raw.get("protect_gap_ratio", 0.50)),
            protect_floor_pct=float(ep_raw.get("protect_floor_pct", 0.03)),
            min_hold_bars=int(ep_raw.get("min_hold_bars", 3)),
            max_hold_factor=int(ep_raw.get("max_hold_factor", 4)),
            exit_confirm_bars=int(ep_raw.get("exit_confirm_bars", 2)),
        )

        # 智能出场回测
        exit_info = rule.get("exit", {})
        smart = discovery_engine._shadow_smart_exit_backtest(
            df=df,
            entry_mask=entry_mask,
            exit_info=exit_info,
            direction=direction,
            horizon=horizon,
            params=exit_params,
        )

        if smart["n_samples"] == 0:
            print(f"{label:<40} OOS 无样本（数据不足或出场条件编译失败）")
            continue

        base_wr = f"{baseline['win_rate']:.1f}%"
        base_ret = f"{baseline['avg_net']:+.4f}%"
        smart_wr = f"{smart['win_rate']:.1f}%"
        smart_ret = f"{smart['net_return_with_exit']:+.4f}%"
        improvement = smart['net_return_with_exit'] - baseline['avg_net']
        imp_str = f"{improvement:+.4f}%"

        print(
            f"{label:<40} {direction:<6} {base_wr:>10} {base_ret:>10}"
            f" {smart_wr:>10} {smart_ret:>10} {imp_str:>8} {smart['n_samples']:>5}"
        )

        # 出场原因分布
        reasons = smart.get("exit_reason_counts", {})
        n_s = smart["n_samples"]
        reason_parts = [
            f"{k}={v}({v/n_s*100:.0f}%)" for k, v in reasons.items() if v > 0
        ]
        print(f"  {'':40} 出场原因: {' | '.join(reason_parts)}")
        print(f"  {'':40} 平均持仓: {smart['avg_bars_held']:.1f} bars  "
              f"触发出场: {smart['triggered_exit_pct']:.1f}%  "
              f"WF-OOS-WR: {rule.get('stats',{}).get('oos_win_rate','?')}%")
        print()


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="出场回测工具")
    parser.add_argument("--days", type=int, default=365, help="回测数据天数")
    args = parser.parse_args()
    run_backtest(data_days=args.days)


if __name__ == "__main__":
    main()
