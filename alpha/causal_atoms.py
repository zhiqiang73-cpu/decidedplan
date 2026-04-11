"""
因果原子 (Causal Atoms): 可解释的单条件预测规则

CausalAtom 的形式:
  IF feature {>|<} threshold THEN 方向 direction 在研究观察窗 horizon 内具有可预测偏移

挖掘流程:
  1. 对 scanner 排名靠前的特征，扫描分位数阈值（10th~90th，步长5%）
  2. 每个 (feature, operator, threshold, horizon) 计算:
       - IC         信息系数（Spearman）
       - ICIR       IC / std(IC)（稳定性）
       - win_rate   正收益占比
       - profit_factor  总盈 / 总亏
       - n_triggers 触发次数
  3. 选取 ICIR 最高、且触发次数足够（>= min_triggers）的原子

用法:
  miner = AtomMiner(min_triggers=50, n_thresholds=20)
  atoms = miner.mine_from_scan(df, scan_results, top_n=15)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


@dataclass
class CausalAtom:
    """
    一个因果原子：单特征阈值规则 + 预测统计。

    Attributes:
        feature:       特征列名
        operator:      '>' 或 '<'
        threshold:     触发阈值
        direction:     入场方向 'long' | 'short'
        horizon:       研究观察窗 K 线数（不是 live 固定离场承诺）
        ic:            平均日 IC
        icir:          IC 信息比率
        win_rate:      胜率（正收益比例）
        profit_factor: 盈亏比
        avg_return:    平均收益（%）
        n_triggers:    触发次数
        trigger_rate:  触发率（%）
    """
    feature:       str
    operator:      str
    threshold:     float
    direction:     str
    horizon:       int

    # 统计指标（挖掘后填充）
    ic:            float = 0.0
    icir:          float = 0.0
    win_rate:      float = 0.0
    profit_factor: float = 0.0
    avg_return:    float = 0.0
    n_triggers:    int   = 0
    trigger_rate:  float = 0.0

    def rule_str(self) -> str:
        """返回人类可读规则字符串。"""
        return (
            f"{self.feature} {self.operator} {self.threshold:.4g}"
            f" → {self.direction} {self.horizon}bars"
        )

    def to_dict(self) -> dict:
        return {
            "rule":           self.rule_str(),
            "feature":        self.feature,
            "operator":       self.operator,
            "threshold":      round(float(self.threshold), 6),
            "direction":      self.direction,
            "horizon":        self.horizon,
            "IC":             round(self.ic, 5),
            "ICIR":           round(self.icir, 4),
            "win_rate":       round(self.win_rate, 3),
            "profit_factor":  round(self.profit_factor, 3),
            "avg_return_pct": round(self.avg_return, 4),
            "n_triggers":     self.n_triggers,
            "trigger_rate":   round(self.trigger_rate, 4),
        }


class AtomMiner:
    """
    因果原子挖掘器。

    Args:
        min_triggers:   原子至少需要触发多少次（过滤样本量不足的规则）
        n_thresholds:   每个特征扫描多少个分位点（均匀分布在 5%~95%）
        min_icir:       保留原子的最低 |ICIR| 门槛
    """

    def __init__(
        self,
        min_triggers: int = 50,
        n_thresholds: int = 20,
        min_icir: float = 0.1,
        max_trigger_rate: float = 0.25,   # 最多触发25%的K线，保证规则有选择性
        force_direction: Optional[str] = None,  # 'long'/'short'/None=自动
    ):
        self.min_triggers     = min_triggers
        self.n_thresholds     = n_thresholds
        self.min_icir         = min_icir
        self.max_trigger_rate = max_trigger_rate
        self.force_direction  = force_direction

    # ── 对单个特征挖掘最优原子 ──────────────────────────────────────────────
    def mine_feature(
        self,
        df: pd.DataFrame,
        feature: str,
        horizon: int,
        preferred_direction: Optional[str] = None,
    ) -> Optional[CausalAtom]:
        """
        扫描特征的所有分位阈值，返回最优 CausalAtom。

        preferred_direction: 'long' | 'short' | None（None = 自动根据 IC 方向选择）
        """
        # 拒绝 TIME 维度特征 — 统计季节性不是物理行为
        _TIME_FEATURES = {"hour_in_day", "minute_in_hour", "day_of_week",
                          "minutes_to_funding", "is_funding_hour", "session"}
        _DELAYED_API_FEATURES = {
            "taker_ratio_api",
            "long_short_ratio",
            "buy_volume",
            "sell_volume",
        }
        if feature in _TIME_FEATURES or feature.startswith("hour_") or feature.startswith("session"):
            logger.debug(f"[AtomMiner] 跳过 TIME 特征: {feature}")
            return None
        if feature in _DELAYED_API_FEATURES:
            logger.debug(f"[AtomMiner] 跳过延迟 API 特征: {feature}")
            return None

        fwd_col = f"fwd_ret_{horizon}"
        if fwd_col not in df.columns:
            return None

        col   = df[feature]
        fwd   = df[fwd_col]
        valid = col.notna() & fwd.notna()
        if valid.sum() < self.min_triggers * 2:
            return None

        col_v = col[valid].values
        fwd_v = fwd[valid].values
        idx_v = np.where(valid)[0]

        # 扫描分位点
        # 只扫描两端（极高/极低区域），确保规则有选择性（触发率 < max_trigger_rate）
        # IC < 0 → 高值预测下跌 → 主要扫描高端阈值（feature > high_pct）
        # IC > 0 → 高值预测上涨 → 主要扫描低端阈值（feature < low_pct）
        # 但两端都扫，让数据决定最优
        max_pct = self.max_trigger_rate * 100          # e.g. 25% → pct上限25
        lo_pcts = np.linspace(max_pct, 49, self.n_thresholds // 2)    # '<' 方向
        hi_pcts = np.linspace(100 - max_pct, 51, self.n_thresholds // 2)  # '>' 方向
        all_pcts = np.concatenate([lo_pcts, hi_pcts])
        thresholds = np.unique(np.percentile(col_v, all_pcts))

        best_atom: Optional[CausalAtom] = None
        best_abs_icir = -1.0

        for thresh in thresholds:
            for op in [">", "<"]:
                mask = col_v > thresh if op == ">" else col_v < thresh
                n = mask.sum()
                if n < self.min_triggers:
                    continue
                if n / len(col_v) > self.max_trigger_rate:
                    continue   # 触发太频繁，规则缺乏选择性

                sub_fwd = fwd_v[mask]

                # IC（用整组有效数据的 Spearman）
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ic_val, _ = spearmanr(col_v[mask], sub_fwd)
                if np.isnan(ic_val):
                    continue

                # 日级 IC 用于计算 ICIR（简化：用滚动窗口切片）
                icir_val = self._rolling_icir(
                    col[valid].reset_index(drop=True),
                    fwd[valid].reset_index(drop=True),
                    op, thresh
                )

                # 方向优先级：force_direction > preferred_direction > IC自动判断
                if self.force_direction:
                    direction = self.force_direction
                elif preferred_direction:
                    direction = preferred_direction
                else:
                    direction = "long" if ic_val > 0 else "short"

                # force_direction 时：跳过与目标方向相反的条件
                if self.force_direction:
                    expected_ic_sign = 1 if self.force_direction == "long" else -1
                    if ic_val * expected_ic_sign <= 0:
                        continue  # 该条件预测方向错误，跳过

                # 收益统计
                rets     = sub_fwd if direction == "long" else -sub_fwd
                wins     = rets[rets > 0]
                losses   = rets[rets <= 0]
                win_rate = len(wins) / len(rets)
                avg_win  = wins.mean()  if len(wins)  > 0 else 0.0
                avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
                pf = (
                    (avg_win * len(wins)) / (avg_loss * len(losses))
                    if len(losses) > 0 and avg_loss > 0
                    else float("inf")
                )

                if abs(icir_val) > best_abs_icir:
                    best_abs_icir = abs(icir_val)
                    best_atom = CausalAtom(
                        feature       = feature,
                        operator      = op,
                        threshold     = float(thresh),
                        direction     = direction,
                        horizon       = horizon,
                        ic            = float(ic_val),
                        icir          = float(icir_val),
                        win_rate      = float(win_rate),
                        profit_factor = float(pf),
                        avg_return    = float(rets.mean() * 100),
                        n_triggers    = int(n),
                        trigger_rate  = float(n / len(col_v) * 100),
                    )

        if best_atom is None or abs(best_atom.icir) < self.min_icir:
            return None
        return best_atom

    # ── 从 scan_all() 结果批量挖掘 ──────────────────────────────────────────
    def mine_from_scan(
        self,
        df: pd.DataFrame,
        scan_df: pd.DataFrame,
        top_n: int = 15,
    ) -> List[CausalAtom]:
        """
        从 FeatureScanner.scan_all() 的结果中，
        取 |ICIR| 最高的 top_n 行对应的 (feature, horizon) 组合进行挖掘。

        Returns:
            按 |ICIR| 降序排列的 CausalAtom 列表
        """
        top = scan_df.head(top_n)
        atoms = []

        for _, row in top.iterrows():
            feature = row["feature"]
            horizon = int(row["horizon"])

            # 双向挖掘: 对每个 (feature, horizon) 同时挖 long 和 short,
            # 让数据决定哪个方向有预测力, 消除 IC 符号导致的 SHORT 偏差。
            logger.info(f"挖掘原子: {feature} @ horizon={horizon} (双向)...")
            for direction in ("long", "short"):
                atom = self.mine_feature(df, feature, horizon, direction)
                if atom is not None:
                    atoms.append(atom)
                    logger.info(
                        f"  -> {atom.rule_str()} | ICIR={atom.icir:.3f}"
                        f" WR={atom.win_rate*100:.1f}% PF={atom.profit_factor:.2f}"
                        f" n={atom.n_triggers} dir={direction}"
                    )

        atoms.sort(key=lambda a: abs(a.icir), reverse=True)
        return atoms

    # ── 日级 ICIR 辅助计算 ──────────────────────────────────────────────────
    @staticmethod
    def _rolling_icir(
        col: pd.Series,
        fwd: pd.Series,
        op: str,
        thresh: float,
        window: int = 1440,   # 1 天
        min_periods: int = 30,
    ) -> float:
        """
        用滚动窗口（1天）近似计算 ICIR。
        每个窗口内：满足 op/thresh 的子集的 IC。
        返回 mean(IC) / std(IC)。
        """
        n = len(col)
        daily_ics = []

        step = window
        for start in range(0, n - window, step):
            end = start + window
            c_w = col.iloc[start:end]
            f_w = fwd.iloc[start:end]

            mask = (c_w > thresh) if op == ">" else (c_w < thresh)
            sub_c = c_w[mask]
            sub_f = f_w[mask]
            valid  = sub_c.notna() & sub_f.notna()

            if valid.sum() < min_periods:
                continue

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ic, _ = spearmanr(sub_c[valid], sub_f[valid])
            if not np.isnan(ic):
                daily_ics.append(ic)

        if len(daily_ics) < 5:
            return 0.0

        ics = np.array(daily_ics)
        std = ics.std()
        return float(ics.mean() / std) if std > 0 else 0.0
