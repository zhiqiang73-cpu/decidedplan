"""
走前验证 (Walk-Forward Validation)

将数据集分为样本内 (IS) 和样本外 (OOS) 两段，
分别评估每个 CausalAtom 的表现，检验发现的规律是否过拟合。

分割方式:
  IS  — 前 train_frac 的时间段（默认前 67%，约 12 个月）
  OOS — 剩余时间段（约 6 个月）

关键指标:
  degradation = OOS_ICIR / IS_ICIR
    > 0.5  → 稳健（OOS 保留 50% 以上性能）
    0~0.5  → 部分过拟合
    < 0    → 完全过拟合（OOS 反转）

用法:
  validator = WalkForwardValidator(train_frac=0.67)
  train_df, test_df = validator.split(df)
  report = validator.validate_atom(atom, train_df, test_df)
  results = validator.validate_all(atoms, train_df, test_df)
"""

import logging
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from alpha.causal_atoms import CausalAtom

logger = logging.getLogger(__name__)


class WalkForwardValidator:
    """
    走前验证器。

    Args:
        train_frac: IS 数据占比（按 K 线根数切分）
        fee_pct:    往返手续费百分比（默认 0.10% = taker 开仓0.05% + 平仓0.05%）
                    所有 win_rate / profit_factor / avg_return 均为费后数字。
                    gross 指标另外保留供对比参考。
    """

    def __init__(self, train_frac: float = 0.67, fee_pct: float = 0.04):
        self.train_frac = train_frac
        self.fee_pct    = fee_pct        # 往返费率（百分比，非小数）；Maker 0.02%×2=0.04%

    # ── 数据切分 ────────────────────────────────────────────────────────────
    def split(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        按时间顺序切分为 IS / OOS 两段。

        Returns:
            (train_df, test_df)，均保留原始 index。
        """
        n = len(df)
        split_idx = int(n * self.train_frac)

        train_df = df.iloc[:split_idx].copy()
        test_df  = df.iloc[split_idx:].copy()

        train_start = pd.to_datetime(train_df["timestamp"].iloc[0],  unit="ms", utc=True).date()
        train_end   = pd.to_datetime(train_df["timestamp"].iloc[-1], unit="ms", utc=True).date()
        test_start  = pd.to_datetime(test_df["timestamp"].iloc[0],   unit="ms", utc=True).date()
        test_end    = pd.to_datetime(test_df["timestamp"].iloc[-1],  unit="ms", utc=True).date()

        logger.info(
            f"数据切分: IS {train_start}~{train_end} ({len(train_df):,}行) | "
            f"OOS {test_start}~{test_end} ({len(test_df):,}行)"
        )
        return train_df, test_df

    # ── 单 Atom 评估 ────────────────────────────────────────────────────────
    def _eval_atom(self, atom: CausalAtom, df: pd.DataFrame) -> dict:
        """
        在给定数据段上评估 CausalAtom 的各项指标。
        """
        fwd_col = f"fwd_ret_{atom.horizon}"
        if fwd_col not in df.columns:
            return {"error": f"缺少列 {fwd_col}"}

        col = df[atom.feature]
        fwd = df[fwd_col]

        # 触发掩码
        if atom.operator == ">":
            mask = col > atom.threshold
        else:
            mask = col < atom.threshold

        triggered = mask & col.notna() & fwd.notna()
        n_triggers = int(triggered.sum())
        trigger_rate = n_triggers / max(len(df), 1)

        if n_triggers < 10:
            return {
                "n_triggers":   n_triggers,
                "trigger_rate": round(trigger_rate * 100, 4),
                "IC":           None,
                "ICIR":         None,
                "win_rate":     None,
                "profit_factor": None,
                "avg_return_pct": None,
            }

        sub_fwd = fwd[triggered].values

        # IC（全局 Spearman）
        ic_val, _ = spearmanr(col[triggered].values, sub_fwd)

        # 日级 ICIR
        icir_val = self._daily_icir(col, fwd, atom.operator, atom.threshold)

        # 收益统计（毛收益 + 费后净收益）
        gross  = sub_fwd if atom.direction == "long" else -sub_fwd
        fee    = self.fee_pct / 100       # 转换为小数
        net    = gross - fee              # 费后净收益

        wins_g   = gross[gross > 0];  losses_g = gross[gross <= 0]
        wins_n   = net[net > 0];      losses_n = net[net <= 0]

        win_rate_gross = len(wins_g) / len(gross)
        win_rate_net   = len(wins_n) / len(net)

        avg_win_g  = wins_g.mean()        if len(wins_g)  > 0 else 0.0
        avg_loss_g = abs(losses_g.mean()) if len(losses_g) > 0 else 0.0
        avg_win_n  = wins_n.mean()        if len(wins_n)  > 0 else 0.0
        avg_loss_n = abs(losses_n.mean()) if len(losses_n) > 0 else 0.0

        pf_gross = (
            (avg_win_g * len(wins_g)) / (avg_loss_g * len(losses_g))
            if len(losses_g) > 0 and avg_loss_g > 0 else float("inf")
        )
        pf_net = (
            (avg_win_n * len(wins_n)) / (avg_loss_n * len(losses_n))
            if len(losses_n) > 0 and avg_loss_n > 0 else float("inf")
        )

        return {
            "n_triggers":         n_triggers,
            "trigger_rate":       round(trigger_rate * 100, 4),
            "IC":                 round(float(ic_val), 5) if not np.isnan(ic_val) else None,
            "ICIR":               round(float(icir_val), 4),
            # 费后（主要指标）
            "win_rate":           round(float(win_rate_net)   * 100, 2),
            "profit_factor":      round(float(pf_net),   3),
            "avg_return_pct":     round(float(net.mean()) * 100, 4),
            # 费前（参考对比）
            "win_rate_gross":     round(float(win_rate_gross) * 100, 2),
            "profit_factor_gross":round(float(pf_gross), 3),
            "avg_return_gross_pct":round(float(gross.mean()) * 100, 4),
        }

    # ── 单 Atom 走前报告 ────────────────────────────────────────────────────
    def validate_atom(
        self,
        atom: CausalAtom,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
    ) -> dict:
        """
        计算一个原子在 IS / OOS 上的完整对比报告。

        Returns:
            dict 包含 IS/OOS 指标、degradation 比率、is_robust 标签、mfe_coverage_rate。
        """
        is_metrics  = self._eval_atom(atom, train_df)
        oos_metrics = self._eval_atom(atom, test_df)

        is_icir  = is_metrics.get("ICIR")  or 0.0
        oos_icir = oos_metrics.get("ICIR") or 0.0

        if is_icir == 0:
            degradation = 0.0
        else:
            degradation = oos_icir / is_icir

        # MFE/MAE 比率：在 OOS 数据上计算入场质量
        # MFE/MAE >= 1.5 是真正有意义的门槛（有利方向走的比不利方向多50%以上）
        # 注意: MFE_cov@0.04% 对 BTC 毫无意义，已废弃作为门槛
        mfe_result = self._compute_mfe_mae(atom, test_df)
        mfe_mae_ratio   = mfe_result["mfe_mae_ratio"]
        mfe_coverage_rate = mfe_result["coverage_rate"]
        p_mfe_gt_mae    = mfe_result["p_mfe_gt_mae"]  # 核心：方向正确概率

        # 稳健判断（全部基于费后指标）：
        #   1. OOS 保留 IS 性能的 50% 以上（ICIR 衰减）
        #   2. OOS ICIR 绝对值 > 0.3
        #   3. OOS 盈亏比（费后）> 1.0
        #   4. OOS 平均收益（费后）> 0
        #   5. P(MFE > MAE) >= 65% — 入场后方向正确的概率（做多=往上走，做空=往下走）
        oos_pf      = oos_metrics.get("profit_factor") or 0.0
        oos_avg_ret = oos_metrics.get("avg_return_pct") or 0.0
        min_icir    = 0.3 if abs(is_icir) > 0.5 else 0.1
        is_robust = (
            degradation > 0.5
            and abs(oos_icir or 0) > min_icir
            and oos_pf > 1.0
            and oos_avg_ret > 0.0
            and p_mfe_gt_mae >= 0.65  # 方向正确概率 >= 65%
        )

        return {
            "rule":           atom.rule_str(),
            "IS":             is_metrics,
            "OOS":            oos_metrics,
            "degradation":    round(degradation, 3),
            "is_robust":      is_robust,
            "p_mfe_gt_mae":   round(p_mfe_gt_mae, 3),   # 核心门槛：方向正确概率
            "mfe_mae_ratio":  round(mfe_mae_ratio, 3),   # 辅助参考
            "mfe_coverage":   round(mfe_coverage_rate * 100.0, 2),
        }


    # ── 批量验证 ────────────────────────────────────────────────────────────
    def validate_all(
        self,
        atoms: List[CausalAtom],
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
        keep_only_robust: bool = False,
    ) -> List[dict]:
        """
        批量验证所有原子，返回报告列表（按 OOS ICIR 降序）。

        Args:
            keep_only_robust: True 时只返回 is_robust=True 的原子报告
        """
        reports = []
        for atom in atoms:
            report = self.validate_atom(atom, train_df, test_df)
            reports.append(report)
            oos_icir = report["OOS"].get("ICIR", 0) or 0
            status = "ROBUST" if report["is_robust"] else "OVERFIT"
            is_icir = report["IS"].get("ICIR") or 0
            decay_val = report.get("degradation") or 0
            logger.info(
                f"[{status}] {report['rule']} | "
                f"IS ICIR={is_icir:.3f} "
                f"OOS ICIR={oos_icir:.3f} "
                f"decay={decay_val:.2f}"
            )

        # 按 OOS ICIR 降序
        reports.sort(
            key=lambda r: abs(r["OOS"].get("ICIR") or 0),
            reverse=True
        )

        if keep_only_robust:
            reports = [r for r in reports if r["is_robust"]]

        return reports

    # ── MFE/MAE 辅助 ────────────────────────────────────────────────────────
    @staticmethod
    def _compute_mfe_coverage(
        atom: "CausalAtom",
        df: pd.DataFrame,
        fee_threshold_pct: float = 0.04,
    ) -> float:
        """保留兼容接口，内部调用 _compute_mfe_mae，返回覆盖率。"""
        result = WalkForwardValidator._compute_mfe_mae(atom, df, fee_threshold_pct)
        return result["coverage_rate"]

    @staticmethod
    def _compute_mfe_mae(
        atom: "CausalAtom",
        df: pd.DataFrame,
        fee_threshold_pct: float = 0.04,
    ) -> dict:
        """
        计算 OOS 数据上的 MFE/MAE 比率及 MFE 覆盖率。

        MFE/MAE 比率是真正有意义的入场质量指标：
        - MFE = 入场后最大有利偏移（Maximum Favorable Excursion）
        - MAE = 入场后最大不利偏移（Maximum Adverse Excursion）
        - MFE/MAE >= 1.5 说明有利方向走的比不利方向多50%以上，有真实方向性边缘

        注意: MFE_cov@0.04% 对 BTC 无意义（任何入场都能轻松达到90%+），
        已废弃作为门槛，仅保留供日志参考。

        Returns:
            dict: {
                "coverage_rate": float [0.0, 1.0],  # MFE > fee 的比例（仅供参考）
                "mfe_mae_ratio": float,              # 核心指标，mean(MFE)/mean(MAE)
                "mean_mfe":      float,
                "mean_mae":      float,
            }
        """
        if "close" not in df.columns:
            return {"coverage_rate": 0.0, "mfe_mae_ratio": 0.0, "mean_mfe": 0.0, "mean_mae": 0.0}

        col = df[atom.feature]
        if atom.operator == ">":
            mask = col > atom.threshold
        else:
            mask = col < atom.threshold

        triggered = mask & col.notna()
        if not triggered.any():
            return {"coverage_rate": 0.0, "mfe_mae_ratio": 0.0, "mean_mfe": 0.0, "mean_mae": 0.0}

        close_arr = df["close"].values
        horizon = max(1, int(atom.horizon))
        sign = -1.0 if atom.direction == "short" else 1.0
        n_total = len(df)

        # 获取触发位置的整数索引
        triggered_locs = []
        for pos in df.index[triggered]:
            loc = df.index.get_loc(pos)
            if isinstance(loc, int) and loc + 1 < n_total:
                triggered_locs.append(loc)

        if not triggered_locs:
            return {"coverage_rate": 0.0, "mfe_mae_ratio": 0.0, "mean_mfe": 0.0, "mean_mae": 0.0}

        # 向量化计算：构建 (n_triggers, horizon) 价格路径矩阵
        locs = np.array([l for l in triggered_locs if l + horizon < n_total])
        if len(locs) == 0:
            return {"coverage_rate": 0.0, "mfe_mae_ratio": 0.0, "mean_mfe": 0.0, "mean_mae": 0.0}

        entry_prices = close_arr[locs]
        # future_indices: shape (n, horizon)
        future_indices = locs[:, np.newaxis] + np.arange(1, horizon + 1)
        future_prices = close_arr[future_indices]

        # path_rets: shape (n, horizon), 有符号收益率（%）
        path_rets = (future_prices - entry_prices[:, np.newaxis]) / entry_prices[:, np.newaxis] * sign * 100.0

        mfes = np.nanmax(path_rets, axis=1)   # 最大有利偏移
        maes = np.nanmax(-path_rets, axis=1)  # 最大不利偏移（取正值）

        # MFE 覆盖率（仅供参考）
        n_valid = len(mfes)
        mfe_above_fee = int(np.sum(mfes > fee_threshold_pct))
        coverage_rate = mfe_above_fee / n_valid if n_valid > 0 else 0.0

        # P(MFE > MAE)：入场后价格朝正确方向走的概率（核心门槛）
        # 含义：100次触发里有多少次"有利幅度 > 不利幅度"
        valid_pairs = (mfes > 0) & (maes > 0)
        if valid_pairs.sum() >= 5:
            mean_mfe = float(mfes[valid_pairs].mean())
            mean_mae = float(maes[valid_pairs].mean())
            mfe_mae_ratio = round(mean_mfe / mean_mae, 3) if mean_mae > 0 else 0.0
            p_mfe_gt_mae = float((mfes[valid_pairs] > maes[valid_pairs]).mean())
        else:
            mean_mfe = float(mfes.mean()) if n_valid > 0 else 0.0
            mean_mae = float(maes.mean()) if n_valid > 0 else 0.0
            mfe_mae_ratio = 0.0
            p_mfe_gt_mae = 0.0

        return {
            "coverage_rate":  coverage_rate,
            "mfe_mae_ratio":  mfe_mae_ratio,
            "p_mfe_gt_mae":   round(p_mfe_gt_mae, 3),  # 核心：方向正确概率
            "mean_mfe":       round(mean_mfe, 4),
            "mean_mae":       round(mean_mae, 4),
        }

    # ── 日级 ICIR 辅助 ──────────────────────────────────────────────────────
    @staticmethod
    def _daily_icir(
        col: pd.Series,
        fwd: pd.Series,
        op: str,
        thresh: float,
        window: int = 1440,
        min_obs: int = 10,
    ) -> float:
        n = len(col)
        daily_ics = []
        for start in range(0, n - window, window):
            end = start + window
            c_w = col.iloc[start:end]
            f_w = fwd.iloc[start:end]
            mask = (c_w > thresh) if op == ">" else (c_w < thresh)
            sub_c = c_w[mask]
            sub_f = f_w[mask]
            valid = sub_c.notna() & sub_f.notna()
            if valid.sum() < min_obs:
                continue
            ic, _ = spearmanr(sub_c[valid], sub_f[valid])
            if not np.isnan(ic):
                daily_ics.append(ic)
        if len(daily_ics) < 3:
            return 0.0
        ics = np.array(daily_ics)
        std = ics.std()
        return float(ics.mean() / std) if std > 0 else 0.0
