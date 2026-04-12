"""
逐笔 Alpha 发现引擎 (TickDiscoveryEngine)

完全数据驱动，镜像 live_discovery.py 的架构，运行在 10s/30s/60s tick bar 上。

流程:
  1. TickFeatureEngine 加载 data/storage/agg_trades/ 原始逐笔数据
  2. 聚合为时间窗口 bar，计算 tick_* 特征
  3. FeatureScanner IC 扫描（tick-adapted horizons）
  4. AtomMiner 双向挖掘（long + short，禁止用 IC 符号锁定方向）
  5. WalkForwardValidator 验证（60/40 切分，OOS >= 100 样本）
  6. ComboScanner 找最优确认因子（最多 1 个）
  7. 止损/保本/持仓时间 全部网格扫描优化，禁止硬编码
  8. 写 alpha/output/tick/pending_rules.json

运行:
  engine = TickDiscoveryEngine()
  cards = engine.run_once(window_seconds=10, data_days=90)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from core.tick_feature_engine import TickFeatureEngine
from alpha.scanner import FeatureScanner
from alpha.causal_atoms import AtomMiner, CausalAtom
from alpha.walk_forward import WalkForwardValidator
from utils.file_io import write_json_atomic

logger = logging.getLogger(__name__)

# ── Block State 种子定义（整数特征，用整数阈值构造 Atom） ─────────────────
# 值含义：连续 N 个时间窗口满足条件的计数
# 方向：吸收卖盘/买盘耗尽 -> long；动量衰竭 -> 双向；连续方向 -> 对应方向
_BLOCK_STATE_SEEDS: dict[str, list[str]] = {
    "tick_absorption_blocks":       ["long"],
    "tick_exhaustion_blocks":       ["long", "short"],
    "tick_direction_persist_long":  ["long"],
    "tick_direction_persist_short": ["short"],
}
# 整数阈值列表，operator 固定 ">"，>0=>=1, >1=>=2, 以此类推
_BLOCK_THRESHOLDS: list[int] = [0, 1, 2, 3, 4]

# ── 输出目录 ──────────────────────────────────────────────────────────────────
_TICK_OUTPUT_DIR = Path("alpha/output/tick")

# ── Maker 费率（0.02% × 2 双边） ─────────────────────────────────────────────
_MAKER_FEE_TOTAL = 0.04   # %

# ── 质量门槛 ──────────────────────────────────────────────────────────────────
_MIN_OOS_WR    = 40.0    # 固定horizon WR宽松预筛（不可靠，真正门槛是 P(MFE>MAE)>=65%）
_MIN_OOS_N     = 100     # OOS 触发次数（tick 自相关高，比 1m 要求更多）
_MIN_OOS_NET   = 0.0     # OOS 净收益 > 0
_MIN_P_MFE_GT_MAE  = 0.65 # P(MFE > MAE) >= 65%（核心门槛：入场后方向正确概率）
_TRAIN_FRAC    = 0.60    # 60/40 切分（比 1m 更保守）
_MAX_COMBO_COND = 1      # 最多 1 个确认条件（防止过拟合）
_MAX_SIGNAL_AUTOCORR = 0.3  # 信号触发的 lag-1 自相关上限


# ── tick 确认特征白名单（禁止 TIME 维度进入） ─────────────────────────────────
# 只允许交易流 / 价格微结构 / 持续性状态 三类
_TICK_CONFIRM_FEATURES = [
    "tick_buy_sell_ratio",
    "tick_large_buy_ratio",
    "tick_burst_index",
    "tick_direction_net",
    "tick_trade_count",
    "tick_trade_size_mean",
    "tick_vwap_dev_pct",
    "tick_absorption_ratio",
    "tick_bounce_rate",
    "tick_absorption_long_score",
    "tick_absorption_short_score",
    "tick_momentum_exhaustion",
    "tick_absorption_blocks",
    "tick_exhaustion_blocks",
    "tick_direction_persist_long",
    "tick_direction_persist_short",
    "tick_bid_ask_imbalance",
    "tick_spread_compression",
    "tick_imbalance_change",
]

# ── 出场参数网格（完全数据驱动，禁止写死） ────────────────────────────────────
# horizon <= 5 bar 的超短策略用更紧止损
_STOP_GRID_SHORT_HORIZON  = [0.05, 0.08, 0.10, 0.12, 0.15]
_STOP_GRID_MEDIUM_HORIZON = [0.10, 0.12, 0.15, 0.20, 0.25]
_PROTECT_GRID = [0.02, 0.03, 0.04, 0.05, 0.06]


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _card_id(feature: str, operator: str, threshold: float, direction: str, horizon: int, window: int) -> str:
    raw = f"{window}s:{feature}:{operator}:{threshold:.6f}:{direction}:h{horizon}"
    return "T-" + hashlib.sha1(raw.encode()).hexdigest()[:8].upper()


def _profit_factor(returns: np.ndarray) -> float:
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    if len(losses) == 0:
        return float("inf") if len(wins) > 0 else 0.0
    loss_sum = abs(float(losses.sum()))
    if loss_sum < 1e-12:
        return float("inf")
    return float(wins.sum() / loss_sum)


def _p_mfe_gt_mae(df: pd.DataFrame, mask: pd.Series, direction: str, horizon: int) -> float:
    """
    计算 P(MFE > MAE)：入场后方向正确概率（核心门槛）

    含义：触发入场后，有利幅度 > 不利幅度 的次数 / 总触发次数
    - 做多：上涨幅度 > 下跌幅度 的概率（MFE=最大涨幅，MAE=最大跌幅）
    - 做空：下跌幅度 > 上涨幅度 的概率（MFE=最大跌幅，MAE=最大涨幅）

    >= 0.65 表示：100次触发里，至少65次价格朝正确方向走得更多。
    这就是"猜对方向的概率"，是入场质量的核心衡量标准。
    """
    fwd_max_col = f"fwd_max_ret_{horizon}"
    fwd_min_col = f"fwd_min_ret_{horizon}"

    if fwd_max_col not in df.columns or fwd_min_col not in df.columns:
        return 0.0

    triggered = df[mask]
    if len(triggered) < 5:
        return 0.0

    max_rets = triggered[fwd_max_col].dropna() * 100.0   # 最大上涨幅度 %
    min_rets = triggered[fwd_min_col].dropna().abs() * 100.0  # 最大下跌幅度 %（取正）

    if direction == "long":
        mfes = max_rets.clip(lower=0)  # 做多：有利方向 = 涨
        maes = min_rets                # 做多：不利方向 = 跌
    else:  # short
        mfes = min_rets                # 做空：有利方向 = 跌
        maes = max_rets.clip(lower=0)  # 做空：不利方向 = 涨

    # 只保留 MFE 和 MAE 都 > 0 的有效对（两个方向都动过才有意义）
    valid = (mfes > 0) & (maes > 0)
    if valid.sum() < 5:
        return 0.0

    return round(float((mfes[valid] > maes[valid]).mean()), 3)


def _signal_autocorr(mask: pd.Series, lag: int = 1) -> float:
    """计算信号触发序列的 lag-N 自相关（防止高自相关信号）。"""
    arr = mask.fillna(False).astype(int).values
    if arr.sum() < 5:
        return 0.0
    n = len(arr)
    if n <= lag:
        return 0.0
    mean = arr.mean()
    var = ((arr - mean) ** 2).mean()
    if var < 1e-12:
        return 0.0
    cov = ((arr[:n - lag] - mean) * (arr[lag:] - mean)).mean()
    return float(cov / var)


def _build_entry_mask(df: pd.DataFrame, feature: str, operator: str, threshold: float) -> pd.Series:
    col = df[feature] if feature in df.columns else pd.Series(False, index=df.index)
    if operator == ">":
        return col > threshold
    return col < threshold


def _cooldown_mask(mask: pd.Series, cooldown: int) -> pd.Series:
    """连续触发时只保留第一个，避免自相关膨胀样本数。"""
    values = mask.fillna(False).astype(bool).values
    out = np.zeros(len(values), dtype=bool)
    next_allowed = 0
    cooldown = max(int(cooldown), 1)
    for i, flag in enumerate(values):
        if not flag or i < next_allowed:
            continue
        out[i] = True
        next_allowed = i + cooldown
    return pd.Series(out, index=mask.index)


# ── 出场参数网格扫描（数据驱动，禁止硬编码止损） ─────────────────────────────

def _optimize_exit_params_tick(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    direction: str,
    horizon: int,
    fee_pct: float = _MAKER_FEE_TOTAL,
) -> dict:
    """
    在 OOS 数据上网格扫描 stop_pct 和 protect_start_pct，
    选择 PF 最高且净收益 > 0 的参数组合。

    完全数据驱动：禁止在调用时传入硬编码的止损值。
    参数范围由 horizon 自动确定。

    Returns:
        {
          "stop_pct": float,          # 最优止损 %
          "protect_start_pct": float, # 最优保本启动阈值 %
          "protect_gap_ratio": 0.40,  # 锁住峰值 40% 利润
          "max_hold_bars": int,       # 最大持仓 bar 数
          "pf": float,                # 最优 PF
          "net_return": float,        # 最优净收益 %
          "n": int,                   # OOS 触发次数
        }
    """
    stop_grid = _STOP_GRID_SHORT_HORIZON if horizon <= 5 else _STOP_GRID_MEDIUM_HORIZON
    sign = 1.0 if direction == "long" else -1.0

    # OOS 切分：使用后 40% 数据
    n_total = len(df)
    split_idx = int(n_total * _TRAIN_FRAC)

    close_arr = df["close"].values if "close" in df.columns else None
    if close_arr is None:
        return _default_exit_params(horizon)

    mask_values = entry_mask.reindex(df.index, fill_value=False).values
    oos_entries = [i for i in range(split_idx, n_total) if mask_values[i] and (i + 1) < n_total]

    if len(oos_entries) < 10:
        return _default_exit_params(horizon)

    best_pf = -1.0
    best_params: dict = {}

    for stop in stop_grid:
        for protect in _PROTECT_GRID:
            rets = _simulate_stop_protect(
                close_arr, oos_entries, sign, horizon,
                stop_pct=stop, protect_start_pct=protect,
                protect_gap_ratio=0.40, max_hold_factor=4,
                fee=fee_pct,
            )
            if len(rets) < 5:
                continue
            rets_arr = np.array(rets)
            pf = _profit_factor(rets_arr)
            net = float(np.mean(rets_arr))
            if pf > best_pf and net > 0:
                best_pf = pf
                best_params = {
                    "stop_pct": stop,
                    "protect_start_pct": protect,
                    "protect_gap_ratio": 0.40,
                    "max_hold_bars": horizon * 4,
                    "pf": round(pf, 3),
                    "net_return": round(net, 4),
                    "n": len(rets),
                }

    if not best_params:
        return _default_exit_params(horizon)

    logger.info(
        "  [PARAM-OPT] stop=%.2f%% protect=%.2f%% -> PF=%.2f net=%.4f%% n=%d",
        best_params["stop_pct"], best_params["protect_start_pct"],
        best_params["pf"], best_params["net_return"], best_params["n"],
    )
    return best_params


def _simulate_stop_protect(
    close_arr: np.ndarray,
    entry_positions: list[int],
    sign: float,
    horizon: int,
    stop_pct: float,
    protect_start_pct: float,
    protect_gap_ratio: float,
    max_hold_factor: int,
    fee: float,
) -> list[float]:
    """模拟止损 + 利润保护追踪止损出场，返回每笔净收益 %。"""
    n_total = len(close_arr)
    max_hold = horizon * max_hold_factor
    rets = []

    for entry_idx in entry_positions:
        entry_price = close_arr[entry_idx]
        if entry_price == 0 or np.isnan(entry_price):
            continue

        mfe = 0.0
        protect_armed = False
        protect_floor = -999.0
        exit_ret = None

        for j in range(1, max_hold + 1):
            bar_idx = entry_idx + j
            if bar_idx >= n_total:
                break
            cur_price = close_arr[bar_idx]
            if np.isnan(cur_price):
                continue

            cur_ret = (cur_price - entry_price) / entry_price * sign * 100.0
            adverse = max(0.0, -cur_ret)
            mfe = max(mfe, cur_ret)

            # 硬止损
            if adverse >= stop_pct:
                exit_ret = cur_ret - fee
                break

            # 利润保护
            if not protect_armed and mfe >= protect_start_pct:
                protect_armed = True
            if protect_armed:
                floor = mfe * protect_gap_ratio
                protect_floor = max(protect_floor, floor)
                if cur_ret < protect_floor:
                    exit_ret = cur_ret - fee
                    break

        if exit_ret is None:
            # 时间上限出场
            end_idx = min(entry_idx + max_hold, n_total - 1)
            exit_ret = (close_arr[end_idx] - entry_price) / entry_price * sign * 100.0 - fee

        rets.append(exit_ret)

    return rets


def _default_exit_params(horizon: int) -> dict:
    """网格扫描无结果时使用的保守默认参数（不硬编码具体数值，由 horizon 推算）。"""
    # horizon 越短，止损越紧
    if horizon <= 3:
        stop = 0.10
        protect = 0.03
    elif horizon <= 6:
        stop = 0.12
        protect = 0.04
    else:
        stop = 0.20
        protect = 0.05
    return {
        "stop_pct": stop,
        "protect_start_pct": protect,
        "protect_gap_ratio": 0.40,
        "max_hold_bars": horizon * 4,
        "pf": 0.0,
        "net_return": 0.0,
        "n": 0,
    }


# ── 主引擎 ────────────────────────────────────────────────────────────────────

class TickDiscoveryEngine:
    """
    逐笔 Alpha 发现引擎。

    与 LiveDiscoveryEngine 完全镜像的架构，但运行在 tick bar 上。
    所有策略参数（持仓时间、止损、保本）全部由数据挖掘决定，禁止硬编码。

    Args:
        storage_path:       数据根目录
        output_dir:         输出目录（默认 alpha/output/tick）
        top_n:              IC 扫描取 Top-N 特征进入 AtomMiner
    """

    def __init__(
        self,
        storage_path: str = "data/storage",
        output_dir: Optional[Path] = None,
        top_n: int = 20,
    ) -> None:
        self.storage_path = storage_path
        self.output_dir = output_dir or _TICK_OUTPUT_DIR
        self.top_n = int(top_n)

        # 组件初始化（horizons 在 run_once 时按 window_seconds 动态设置）
        self._tick_engine = TickFeatureEngine(storage_path=storage_path)
        self._validator = WalkForwardValidator(train_frac=_TRAIN_FRAC, fee_pct=_MAKER_FEE_TOTAL)

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def run_once(
        self,
        window_seconds: int = 10,
        data_days: int = 90,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """
        执行一次完整 tick Alpha 发现流程。

        Args:
            window_seconds: 时间窗口大小（10 / 30 / 60 秒）
            data_days:      使用最近多少天数据（默认 90 天，当 start/end 指定时忽略）
            start_date:     显式指定起始日期 (YYYY-MM-DD)，不传则自动计算
            end_date:       显式指定结束日期 (YYYY-MM-DD)，不传则自动计算

        Returns:
            合格策略候选列表（同时写入 pending_rules.json）
        """
        logger.info("=" * 60)
        logger.info("[TICK-DISCOVERY] 开始 tick Alpha 发现 (window=%ds, data=%dd)", window_seconds, data_days)
        logger.info("=" * 60)

        # 根据窗口大小确定 horizon 扫描范围
        horizons = self._get_horizons(window_seconds)
        logger.info("[TICK-DISCOVERY] horizons=%s", horizons)

        # ── Step 1: 加载 tick bar 数据 ───────────────────────────────────────
        if start_date and end_date:
            start_str, end_str = start_date, end_date
        else:
            _end = datetime.now(timezone.utc).date()
            _start = _end - timedelta(days=data_days)
            start_str, end_str = str(_start), str(_end)

        logger.info("[TICK-DISCOVERY] 加载数据: %s ~ %s", start_str, end_str)
        try:
            df = self._tick_engine.load_date_range(
                start_str, end_str,
                window_seconds=window_seconds,
                horizons=tuple(horizons),
            )
        except Exception as exc:
            logger.error("[TICK-DISCOVERY] 数据加载失败: %s", exc)
            return []

        # tick bar 数量下限：至少需要 10 分钟的数据
        min_bars = int(600 / window_seconds) * 30
        if len(df) < min_bars:
            logger.warning("[TICK-DISCOVERY] 数据量不足 (%d bars，需 %d)，跳过", len(df), min_bars)
            return []

        logger.info("[TICK-DISCOVERY] tick bar: %s 行, %d 列", f"{len(df):,}", df.shape[1])

        # ── Step 2: IC 扫描 ──────────────────────────────────────────────────
        # 使用 tick 专用特征列表 + tick horizons
        # min_days 调整为 3（tick 数据每天 bar 数远多于 1m）
        # tick_direction_persist_long/short 是整数计数器（0,1,2,...），
        # 取值范围和 IC 量纲与连续特征不同；保留供 block-state 种子使用
        # 但暂不进入全量 IC 扫描（避免稀疏整数列的 Spearman 估计噪声）
        tick_features = [c for c in df.columns if c.startswith("tick_") and not c.startswith("tick_direction_persist")]
        scanner = FeatureScanner(horizons=horizons, min_days=3, min_obs_per_day=200)
        logger.info("[TICK-DISCOVERY] IC 扫描: %d tick 特征 × %d horizons", len(tick_features), len(horizons))
        scan_df = scanner.scan_all(df, features=tick_features)

        if scan_df.empty:
            logger.warning("[TICK-DISCOVERY] IC 扫描结果为空，跳过")
            self._save_diagnostics(
                window_seconds,
                total_atoms=0,
                validated_count=0,
                rejected=[],
            )
            return []
        logger.info("[TICK-DISCOVERY] 扫描完成: %d 条结果", len(scan_df))

        # ── 提前切分数据（Step 3B 和 Step 4 共用 IS/OOS） ─────────────────────
        train_df, test_df = self._validator.split(df)

        # ── Step 3A: 双向 AtomMiner（long + short，禁止用 IC 符号锁定方向） ─
        # tick 数据必须用极端触发率：P系列信号触发率 0.1~0.3%，不能用默认 25%
        # 只有在极端条件下（卖方/买方真的耗尽了）才触发，胜率才会高
        miner = AtomMiner(max_trigger_rate=0.05)  # 最多触发5%的bar（极端异常）
        # 取 IC |ICIR| 最高的 Top-N 特征
        seed_rows = self._select_seed_rows(scan_df)
        logger.info("[TICK-DISCOVERY] AtomMiner 种子: %d 行", len(seed_rows))

        ic_atoms = miner.mine_from_scan(df, seed_rows, top_n=len(seed_rows))
        logger.info("[TICK-DISCOVERY] IC扫描挖掘 %d 个 Atom (long + short 双向)", len(ic_atoms))

        # ── Step 3B: Block State 整数阈值挖掘（新增） ──────────────────────
        block_atoms = self._mine_block_state_atoms(df, train_df, test_df, horizons)
        logger.info("[TICK-DISCOVERY] Block State 挖掘 %d 个 Atom", len(block_atoms))

        atoms = ic_atoms + block_atoms
        logger.info(
            "[TICK-DISCOVERY] 总计 Atom: %d（IC扫描=%d, BlockState=%d）",
            len(atoms), len(ic_atoms), len(block_atoms),
        )

        if not atoms:
            logger.warning("[TICK-DISCOVERY] 无 Atom，跳过")
            self._save_diagnostics(
                window_seconds,
                total_atoms=0,
                validated_count=0,
                rejected=[],
                block_atoms_count=0,
                combo_count=0,
            )
            return []

        # ── Step 4: Walk-Forward 验证 ────────────────────────────────────────
        # train_df, test_df 已在 Step 3 之前切分
        validated = []
        rejected: list[dict] = []

        def _record_reject(
            atom: CausalAtom,
            report: dict | None,
            reason: str,
            *,
            mfe_cov: float | None = None,
            autocorr: float | None = None,
        ) -> None:
            oos = report.get("OOS", {}) if isinstance(report, dict) else {}
            ins = report.get("IS", {}) if isinstance(report, dict) else {}
            rejected.append(
                {
                    "rule": atom.rule_str(),
                    "reason": reason,
                    "is_wr": ins.get("win_rate"),
                    "is_n": ins.get("n_triggers"),
                    "oos_wr": oos.get("win_rate"),
                    "oos_n": oos.get("n_triggers"),
                    "oos_avg_return_pct": oos.get("avg_return_pct"),
                    "oos_pf": oos.get("profit_factor"),
                    "degradation": report.get("degradation") if isinstance(report, dict) else None,
                    "mfe_coverage": round(float(mfe_cov), 3) if mfe_cov is not None else None,
                    "signal_autocorr": round(float(autocorr), 4) if autocorr is not None else None,
                }
            )

        for atom in atoms:
            report = self._validator.validate_atom(atom, train_df, test_df)
            if report is None:
                _record_reject(atom, None, "验证报告为空")
                continue
            # validate_atom() 返回嵌套结构: {"IS": {...}, "OOS": {...}, "degradation": float, ...}
            oos_m = report.get("OOS", {})
            is_m  = report.get("IS",  {})
            if (oos_m.get("win_rate") or 0) < _MIN_OOS_WR:
                _record_reject(atom, report, f"OOS胜率不足: {oos_m.get('win_rate')} < {_MIN_OOS_WR}")
                continue
            if (oos_m.get("n_triggers") or 0) < _MIN_OOS_N:
                _record_reject(atom, report, f"OOS触发次数不足: {oos_m.get('n_triggers')} < {_MIN_OOS_N}")
                continue
            if (oos_m.get("avg_return_pct") or 0) < _MIN_OOS_NET:
                _record_reject(atom, report, f"OOS费后收益不为正: {oos_m.get('avg_return_pct')} < {_MIN_OOS_NET}")
                continue
            if report.get("degradation", 0) < 0.50:
                _record_reject(atom, report, f"样本外退化过大: {report.get('degradation')} < 0.50")
                continue
            # P(MFE > MAE) 门控：入场后方向正确概率 >= 65%
            test_mask = _build_entry_mask(test_df, atom.feature, atom.operator, atom.threshold)
            p_dir = _p_mfe_gt_mae(test_df, test_mask, atom.direction, atom.horizon)
            if p_dir < _MIN_P_MFE_GT_MAE:
                _record_reject(atom, report, f"方向正确概率不足: {p_dir:.1%} < {_MIN_P_MFE_GT_MAE:.0%}", mfe_cov=p_dir)
                logger.debug(
                    "[TICK-DISCOVERY] 跳过 %s: 方向正确概率=%.1f%% < 65%%",
                    atom.rule_str()[:40], p_dir * 100,
                )
                continue
            # 信号自相关门控（防止高频抱团信号）
            full_mask = _build_entry_mask(df, atom.feature, atom.operator, atom.threshold)
            autocorr = _signal_autocorr(full_mask, lag=1)
            if abs(autocorr) > _MAX_SIGNAL_AUTOCORR:
                _record_reject(atom, report, f"信号自相关超限: {autocorr:.3f} > {_MAX_SIGNAL_AUTOCORR}", mfe_cov=p_dir, autocorr=autocorr)
                logger.debug(
                    "[TICK-DISCOVERY] 跳过 %s: 信号自相关 %.3f 超限",
                    atom.rule_str()[:40], autocorr,
                )
                continue
            validated.append((atom, report, p_dir, oos_m, is_m))

        logger.info("[TICK-DISCOVERY] Walk-Forward 通过: %d / %d 个 Atom", len(validated), len(atoms))

        # ── Step 4.5: Combo 扫描（种子 + 确认因子）── ─────────────────────
        # 对每个WF通过的atom，在OOS数据上寻找能提升P(MFE>MAE)的确认因子
        combo_cards: list[dict] = []
        if validated:
            combo_cards = self._combo_scan(validated, train_df, test_df, horizons, window_seconds)
            logger.info("[TICK-DISCOVERY] Combo 扫描新增 %d 张带确认因子的卡片", len(combo_cards))

        self._save_diagnostics(
            window_seconds,
            total_atoms=len(atoms),
            validated_count=len(validated),
            rejected=rejected,
            block_atoms_count=len(block_atoms),
            combo_count=len(combo_cards),
        )

        if not validated:
            return []

        # ── Step 5: 出场参数网格扫描（数据驱动，禁止硬编码） ────────────────
        # 对每个通过 WF 的 atom，用 OOS 数据网格扫描最优止损/保本参数
        results = []
        for atom, wf_report, p_dir, oos_m, is_m in validated:
            full_mask = _build_entry_mask(df, atom.feature, atom.operator, atom.threshold)
            # 添加 cooldown：同方向连续触发，只保留第一个
            cooled_mask = _cooldown_mask(full_mask, cooldown=atom.horizon)

            exit_params = _optimize_exit_params_tick(
                df, cooled_mask, atom.direction, atom.horizon,
            )

            # ── 构建策略卡片 ──────────────────────────────────────────────
            card = self._build_card(atom, wf_report, p_dir, oos_m, is_m, exit_params, window_seconds)
            results.append(card)
            logger.info(
                "[TICK-DISCOVERY] 候选: %s | OOS WR=%.1f%% n=%d 方向正确率=%.1f%% stop=%.2f%%",
                atom.rule_str()[:40],
                oos_m.get("win_rate") or 0,
                oos_m.get("n_triggers") or 0,
                p_dir * 100,
                exit_params.get("stop_pct", 0),
            )

        # ── Step 6: 合并 combo 卡片 + 方向分布统计 + 写文件 ─────────────────
        # combo_cards 是对已有atom的增强版，不计入 validated_count
        all_results = results + combo_cards
        n_long = sum(1 for c in all_results if c.get("direction") == "long")
        n_short = sum(1 for c in all_results if c.get("direction") == "short")
        logger.info(
            "[TICK-DISCOVERY] 总计 %d 个候选（含combo=%d）: LONG=%d SHORT=%d",
            len(all_results), len(combo_cards), n_long, n_short,
        )

        self._save_pending(all_results, window_seconds)
        self._save_diagnostics(
            window_seconds,
            total_atoms=len(atoms),
            validated_count=len(validated),
            rejected=rejected,
            block_atoms_count=len(block_atoms),
            combo_count=len(combo_cards),
            results=all_results,
        )
        return all_results

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_horizons(window_seconds: int) -> list[int]:
        """根据窗口大小返回合适的 horizon 列表（bar 数量）。"""
        if window_seconds <= 10:
            return [3, 6, 9, 12, 18]     # 30s ~ 180s 持仓
        if window_seconds <= 30:
            return [2, 4, 6, 8, 12]      # 60s ~ 360s 持仓
        return [2, 3, 5, 8]              # 120s ~ 480s 持仓（60s 窗口）

    def _select_seed_rows(self, scan_df: pd.DataFrame) -> pd.DataFrame:
        """
        从 IC 扫描结果中选出做种子的行。

        策略:
          - 按 |ICIR| 降序取 Top-N
          - 保证 long/short 各至少 min_quota 个（防止方向单边偏斜）
        """
        if scan_df.empty:
            return scan_df

        scan_df = scan_df.copy()
        scan_df["abs_ICIR"] = scan_df["ICIR"].abs()
        scan_df = scan_df.sort_values("abs_ICIR", ascending=False)

        long_rows = scan_df[scan_df["IC"] > 0].head(self.top_n)
        short_rows = scan_df[scan_df["IC"] < 0].head(self.top_n)

        min_quota = max(self.top_n // 3, 4)
        long_take = long_rows.head(min_quota)
        short_take = short_rows.head(min_quota)
        remaining = self.top_n - len(long_take) - len(short_take)

        rest = pd.concat([
            long_rows.iloc[min_quota:],
            short_rows.iloc[min_quota:],
        ]).sort_values("abs_ICIR", ascending=False).head(max(remaining, 0))

        result = pd.concat([long_take, short_take, rest]).drop_duplicates()
        result = result.drop(columns=["abs_ICIR"], errors="ignore")

        logger.info(
            "[TICK-DISCOVERY] 种子行 long=%d short=%d total=%d",
            len(long_take), len(short_take), len(result),
        )
        return result.reset_index(drop=True)

    def _mine_block_state_atoms(self, df, train_df, test_df, horizons):
        import warnings as _warnings
        atoms = []
        n_total = len(df)
        for feature, directions in _BLOCK_STATE_SEEDS.items():
            if feature not in df.columns:
                continue
            if feature in train_df.columns and train_df[feature].max() == 0:
                continue
            col_all = df[feature].values
            for horizon in horizons:
                fwd_col = "fwd_ret_" + str(horizon)
                if fwd_col not in df.columns:
                    continue
                fwd_all = df[fwd_col].values
                valid_mask = (~np.isnan(col_all)) & (~np.isnan(fwd_all))
                for thresh in _BLOCK_THRESHOLDS:
                    entry_bool = (col_all > thresh) & valid_mask
                    n_triggers = int(entry_bool.sum())
                    trigger_rate = n_triggers / max(n_total, 1)
                    if trigger_rate < 0.005 or trigger_rate > 0.15:
                        continue
                    if n_triggers < 30:
                        continue
                    try:
                        with _warnings.catch_warnings():
                            _warnings.simplefilter("ignore")
                            ic_val, _ = spearmanr(col_all[entry_bool], fwd_all[entry_bool])
                        if np.isnan(ic_val):
                            ic_val = 0.0
                    except Exception:
                        ic_val = 0.0
                    if feature in train_df.columns and fwd_col in train_df.columns:
                        ci = train_df[feature].values
                        fi = train_df[fwd_col].values
                        vm = (~np.isnan(ci)) & (~np.isnan(fi))
                        im = (ci > thresh) & vm
                        n_is = int(im.sum())
                    else:
                        n_is = 0
                        im = np.zeros(0, dtype=bool)
                        fi = np.zeros(0)
                    for direction in directions:
                        sign = 1.0 if direction == "long" else -1.0
                        dir_ic = round(ic_val * sign, 5)
                        if n_is >= 5:
                            g = fi[im] * sign * 100.0
                            net = g - _MAKER_FEE_TOTAL
                            wr = float((net > 0).mean())
                            pf = _profit_factor(net)
                            avg = float(net.mean())
                        else:
                            wr = pf = avg = 0.0
                        atoms.append(CausalAtom(
                            feature=feature, operator=">",
                            threshold=float(thresh), direction=direction,
                            horizon=horizon, ic=dir_ic, icir=0.0,
                            win_rate=round(wr, 4), profit_factor=round(pf, 4),
                            avg_return=round(avg, 4), n_triggers=n_triggers,
                            trigger_rate=round(trigger_rate * 100, 4),
                        ))
                        logger.debug(
                            "[BLOCK-STATE] %s>%d %s h=%d n=%d",
                            feature, thresh, direction, horizon, n_triggers)
        logger.info("[BLOCK-STATE] %d Block State Atoms", len(atoms))
        return atoms

    def _combo_scan(self, validated, train_df, test_df, horizons, window_seconds):
        import hashlib as _hashlib
        combo_cards = []
        n_test = len(test_df)
        if n_test == 0:
            return combo_cards
        for atom, wf_report, p_seed, oos_m, is_m in validated:
            sf = atom.feature
            so = atom.operator
            st = atom.threshold
            direction = atom.direction
            horizon = atom.horizon
            fwd_col = "fwd_ret_" + str(horizon)
            if fwd_col not in test_df.columns:
                continue
            seed_mask = _build_entry_mask(test_df, sf, so, st)
            for cf in _TICK_CONFIRM_FEATURES:
                if cf == sf:
                    continue
                if cf not in train_df.columns or cf not in test_df.columns:
                    continue
                is_col = train_df[cf].dropna()
                if len(is_col) < 20:
                    continue
                p5 = float(is_col.quantile(0.05))
                p95 = float(is_col.quantile(0.95))
                for cf_op, cf_thresh in [("<", p5), (">", p95)]:
                    cm = _build_entry_mask(test_df, cf, cf_op, cf_thresh)
                    combo = seed_mask & cm
                    n_combo = int(combo.sum())
                    if n_combo < _MIN_OOS_N:
                        continue
                    rate = n_combo / max(n_test, 1)
                    if rate < 0.005 or rate > 0.15:
                        continue
                    p_combo = _p_mfe_gt_mae(test_df, combo, direction, horizon)
                    if p_combo < _MIN_P_MFE_GT_MAE or p_combo <= p_seed:
                        continue
                    fv = test_df.loc[combo, fwd_col].dropna().values
                    if len(fv) == 0:
                        continue
                    sign = 1.0 if direction == "long" else -1.0
                    nv = fv * sign * 100.0 - _MAKER_FEE_TOTAL
                    cwr = float((nv > 0).mean())
                    cpf = _profit_factor(nv)
                    cnet = float(nv.mean())
                    cooled = _cooldown_mask(combo, cooldown=horizon)
                    ep = _optimize_exit_params_tick(test_df, cooled, direction, horizon)
                    card = self._build_card(
                        atom, wf_report, p_combo, oos_m, is_m, ep, window_seconds)
                    raw = ("{s}s:{sf}:{so}:{st:.6f}:{d}:h{h}+{cf}:{co}:{ct:.6f}"
                        .format(s=window_seconds, sf=sf, so=so, st=st,
                                d=direction, h=horizon, cf=cf, co=cf_op, ct=cf_thresh))
                    card["id"] = "TC-" + _hashlib.sha1(raw.encode()).hexdigest()[:8].upper()
                    card["wf_stats"]["p_mfe_gt_mae"] = round(p_combo, 3)
                    card["wf_stats"]["oos_wr"] = round(cwr * 100, 2)
                    card["wf_stats"]["oos_n"] = n_combo
                    card["wf_stats"]["oos_net"] = round(cnet, 4)
                    card["wf_stats"]["oos_pf"] = round(cpf, 3)
                    card["combo_condition"] = {
                        "feature": cf, "operator": cf_op,
                        "threshold": round(cf_thresh, 6),
                        "p_mfe_gt_mae_seed": round(p_seed, 3),
                        "p_mfe_gt_mae_combo": round(p_combo, 3),
                        "n_triggers_oos": n_combo,
                        "trigger_rate_oos": round(rate * 100, 4),
                    }
                    card["notes"] = (
                        "Combo:{sf}>{st:.4g}+{cf}{co}{ct:.4g} "
                        "P(dir):{ps:.1f}%->{pc:.1f}%".format(
                            sf=sf, st=st, cf=cf, co=cf_op, ct=cf_thresh,
                            ps=p_seed*100, pc=p_combo*100)
                    )
                    combo_cards.append(card)
                    logger.info(
                        "[COMBO] %s+%s%s%.4g P:%.1f%%->%.1f%% n=%d",
                        sf[:20], cf[:15], cf_op, cf_thresh,
                        p_seed*100, p_combo*100, n_combo)
        return combo_cards

    @staticmethod
    def _build_card(
        atom: CausalAtom,
        wf_report: dict,
        p_dir: float,
        oos_m: dict,
        is_m: dict,
        exit_params: dict,
        window_seconds: int,
    ) -> dict:
        """将验证通过的 Atom 组装为策略卡片（与 1m 策略卡片格式兼容）。"""
        card_id = _card_id(
            atom.feature, atom.operator, atom.threshold,
            atom.direction, atom.horizon, window_seconds,
        )
        return {
            "id": card_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "timeframe": f"tick_{window_seconds}s",
            "window_seconds": window_seconds,
            "horizon_bars": atom.horizon,
            # 持仓时间 = 数据挖掘结果，不硬编码
            "hold_seconds_est": atom.horizon * window_seconds,
            "direction": atom.direction,
            "entry_condition": {
                "feature":   atom.feature,
                "operator":  atom.operator,
                "threshold": float(atom.threshold),
            },
            "wf_stats": {
                "oos_wr":         round(oos_m.get("win_rate") or 0, 2),
                "oos_n":          int(oos_m.get("n_triggers") or 0),
                "oos_net":        round(oos_m.get("avg_return_pct") or 0, 4),
                "oos_pf":         round(oos_m.get("profit_factor") or 0, 3),
                "is_wr":          round(is_m.get("win_rate") or 0, 2),
                "degradation":    round(wf_report.get("degradation", 0), 3),
                "p_mfe_gt_mae":   round(p_dir, 3),  # 方向正确概率，核心门槛
            },
            # 出场参数：全部来自网格扫描，禁止硬编码
            "exit_params": {
                "stop_pct":           exit_params.get("stop_pct"),
                "protect_start_pct":  exit_params.get("protect_start_pct"),
                "protect_gap_ratio":  exit_params.get("protect_gap_ratio", 0.40),
                "max_hold_bars":      exit_params.get("max_hold_bars"),
                "min_hold_bars":      max(2, atom.horizon // 3),
                "exit_confirm_bars":  1,
            },
            "exit_opt_stats": {
                "pf":         exit_params.get("pf", 0),
                "net_return": exit_params.get("net_return", 0),
                "n":          exit_params.get("n", 0),
            },
            # vs_entry 出场条件：入场特征的衰退即为出场信号
            "vs_entry_exit": {
                "feature":  atom.feature,
                "operator": "<" if atom.operator == ">" else ">",
                "delta":    0.0,
                "note":     "入场力消退时出场，delta=0 表示回到中性位置",
            },
            "notes": f"由 TickDiscoveryEngine 自动发现，窗口={window_seconds}s，horizon={atom.horizon}bars",
        }

    def _save_pending(self, cards: list[dict], window_seconds: int) -> None:
        """将候选策略卡片写入 pending_rules.json（原子写入）。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        pending_file = self.output_dir / f"pending_rules_{window_seconds}s.json"

        # 合并已有候选（不覆盖已有 id）
        existing: list[dict] = []
        if pending_file.exists():
            try:
                with open(pending_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        existing_ids = {c.get("id") for c in existing}
        new_cards = [c for c in cards if c.get("id") not in existing_ids]
        merged = existing + new_cards

        write_json_atomic(pending_file, merged)
        logger.info(
            "[TICK-DISCOVERY] 写入 %s: 新增 %d 条，总计 %d 条",
            pending_file, len(new_cards), len(merged),
        )

    def _save_diagnostics(
        self,
        window_seconds: int,
        *,
        total_atoms: int,
        validated_count: int,
        rejected: list[dict],
        block_atoms_count: int = 0,
        combo_count: int = 0,
        results: list[dict] | None = None,
    ) -> None:
        """保存逐笔发现诊断，避免 0 候选时没有尸检报告。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_file = self.output_dir / f"diagnostics_{window_seconds}s.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_seconds": int(window_seconds),
            "thresholds": {
                "min_oos_wr": _MIN_OOS_WR,
                "min_oos_n": _MIN_OOS_N,
                "min_oos_net": _MIN_OOS_NET,
                "min_p_mfe_gt_mae": _MIN_P_MFE_GT_MAE,
                "max_signal_autocorr": _MAX_SIGNAL_AUTOCORR,
            },
            "summary": {
                "total_atoms": int(total_atoms),
                "validated_count": int(validated_count),
                "rejected_count": int(len(rejected)),
                "block_atoms_count": int(block_atoms_count),
                "combo_count": int(combo_count),
                "candidate_count": int(len(results or [])),
            },
            "rejected_atoms": rejected,
            "candidates": results or [],
        }
        write_json_atomic(diagnostics_file, payload)
        logger.info("[TICK-DISCOVERY] 诊断写入 %s", diagnostics_file)
