"""
Alpha Scanner: 特征 → 未来收益 预测能力扫描

核心指标:
  IC   — Spearman 秩相关 (feature vs forward_return_N)
  ICIR — mean(IC) / std(IC)，信息比率，衡量 IC 的稳定性
  t    — t 统计量，判断 IC 是否显著异于 0

扫描方法:
  按日分组计算 IC (避免样本内时序自相关高估显著性)
  对所有数值特征、多个预测周期自动化全扫描

用法:
  scanner = FeatureScanner(horizons=[5, 15, 30, 60])
  df = scanner.add_forward_returns(df)
  results = scanner.scan_all(df)
  print(results.head(20))
"""

import logging
from typing import List, Optional

import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

# 不参与 IC 扫描的列（原始数据列、派生标签列）
EXCLUDE_COLS = {
    "timestamp", "open", "high", "low", "close", "volume",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
    "open_interest", "long_short_ratio", "long_account", "short_account",
    "buy_volume", "sell_volume",
    "taker_ratio_api",
    "mark_price", "funding_rate",       # 原始数据，已有派生特征
    "volume_ma20",                       # 均量绝对值，不如比率有用
}

# 特征 → 维度映射（用于分组展示）
FEATURE_DIM = {
    # TIME
    "minute_in_hour": "TIME", "hour_in_day": "TIME", "is_weekend": "TIME",
    "minutes_to_funding": "TIME", "hours_to_options_expiry": "TIME",
    "minutes_since_last_big_move": "TIME",
    # PRICE
    "dist_to_round_1000": "PRICE", "dist_to_round_100": "PRICE",
    "dist_to_24h_high": "PRICE", "dist_to_24h_low": "PRICE",
    "position_in_range_4h": "PRICE", "position_in_range_24h": "PRICE",
    "vwap_24h": "PRICE", "vwap_deviation": "PRICE",
    "amplitude_1m": "PRICE", "amplitude_ma20": "PRICE",
    # TRADE_FLOW
    "taker_buy_sell_ratio": "TRADE_FLOW", "taker_buy_pct": "TRADE_FLOW",
    "volume_vs_ma20": "TRADE_FLOW", "avg_trade_size": "TRADE_FLOW",
    "volume_acceleration": "TRADE_FLOW", "trade_interval_cv": "TRADE_FLOW",
    "volume_autocorr_lag5": "TRADE_FLOW", "avg_trade_size_cv_10m": "TRADE_FLOW",
    # LIQUIDITY
    "kyle_lambda": "LIQUIDITY", "spread_proxy": "LIQUIDITY",
    "spread_vs_ma20": "LIQUIDITY",
    # POSITIONING
    "oi_change_rate_5m": "POSITIONING", "oi_change_rate_1h": "POSITIONING",
    "ls_ratio_change_5m": "POSITIONING", "funding_rate_trend": "POSITIONING",
    "consecutive_extreme_funding": "POSITIONING",
    "oi_price_divergence_duration": "POSITIONING",
    # LIQUIDATION
    "btc_liq_net_pressure": "LIQUIDATION",
    "total_liq_usd_5m": "LIQUIDATION",
    "liq_size_p90_5m": "LIQUIDATION",
    # MICROSTRUCTURE
    "quote_imbalance": "MICROSTRUCTURE",
    "spread_anomaly": "MICROSTRUCTURE",
    "bid_depth_ratio": "MICROSTRUCTURE",
    # ORDER_FLOW
    "large_trade_buy_ratio": "ORDER_FLOW",
    "direction_net_1m": "ORDER_FLOW",
    "sell_notional_share_1m": "ORDER_FLOW",
    "trade_burst_index": "ORDER_FLOW",
    "direction_autocorr": "ORDER_FLOW",
    # MARK_PRICE
    "rt_funding_rate": "MARK_PRICE",
    "mark_basis": "MARK_PRICE",
    "mark_basis_ma10": "MARK_PRICE",
    "funding_countdown_m": "MARK_PRICE",
}


class FeatureScanner:
    """
    全量特征 IC 扫描器。

    Args:
        horizons: 预测周期列表（单位：K线根数，1根=1分钟）
        min_days:  每个特征至少需要多少天有效 IC 才纳入报告
        min_obs_per_day: 每天至少多少个有效观测才计算当天 IC
    """

    def __init__(
        self,
        horizons: List[int] = None,
        min_days: int = 20,
        min_obs_per_day: int = 30,
    ):
        self.horizons = horizons or [5, 15, 30, 60]
        self.min_days = min_days
        self.min_obs_per_day = min_obs_per_day

    # ── 前向收益计算 ────────────────────────────────────────────────────────
    def add_forward_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        在 df 上追加每个 horizon 的前向收益列。

        前向收益 = close[t+h] / close[t] - 1
        注意末尾 h 行会产生 NaN（无法计算未来收益）。
        """
        close = df["close"]
        for h in self.horizons:
            df[f"fwd_ret_{h}"] = (close.shift(-h) / close - 1).astype("float32")
        return df

    # ── 日级 IC 计算 ────────────────────────────────────────────────────────
    def _compute_daily_ic(
        self, df: pd.DataFrame, feature: str, horizon: int
    ) -> pd.Series:
        """
        按自然日分组，计算每天的 Spearman IC。
        返回 pd.Series，index 为日期，value 为当天 IC。
        """
        fwd_col = f"fwd_ret_{horizon}"
        if fwd_col not in df.columns:
            return pd.Series(dtype=float)

        # 从 timestamp(ms) 提取 UTC 日期
        dates = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.date

        sub = df[[feature, fwd_col]].copy()
        sub["date"] = dates.values

        daily_ics = {}
        for date, grp in sub.groupby("date"):
            valid = grp[[feature, fwd_col]].dropna()
            if len(valid) < self.min_obs_per_day:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ic, _ = spearmanr(valid[feature], valid[fwd_col])
            if not np.isnan(ic):
                daily_ics[date] = ic

        return pd.Series(daily_ics)

    # ── 单特征单周期 IC 汇总 ────────────────────────────────────────────────
    def scan_feature(
        self, df: pd.DataFrame, feature: str, horizon: int
    ) -> Optional[dict]:
        """
        计算一个特征在一个周期上的 IC 汇总指标。
        不满足条件时返回 None。
        """
        daily_ics = self._compute_daily_ic(df, feature, horizon)
        if len(daily_ics) < self.min_days:
            return None

        mean_ic = float(daily_ics.mean())
        std_ic  = float(daily_ics.std())
        if std_ic == 0:
            return None

        icir   = mean_ic / std_ic
        t_stat = mean_ic / (std_ic / np.sqrt(len(daily_ics)))
        ic_pos = float((daily_ics > 0).mean())   # IC 为正的天数比例

        return {
            "feature":   feature,
            "horizon":   horizon,
            "IC":        round(mean_ic, 5),
            "IC_std":    round(std_ic,  5),
            "ICIR":      round(icir,    4),
            "t_stat":    round(t_stat,  3),
            "IC_pos_pct": round(ic_pos * 100, 1),
            "n_days":    len(daily_ics),
        }

    # ── 全量扫描 ────────────────────────────────────────────────────────────
    def scan_all(
        self,
        df: pd.DataFrame,
        features: Optional[List[str]] = None,
        horizons: Optional[List[int]] = None,
    ) -> pd.DataFrame:
        """
        扫描所有（或指定）特征在所有 horizon 上的 IC 指标。

        Args:
            df:       包含前向收益列（已调用 add_forward_returns）的 DataFrame
            features: 指定特征列表；None 表示自动检测所有数值列
            horizons: 覆盖初始化时的 horizons

        Returns:
            按 |ICIR| 降序排列的 DataFrame
        """
        horizons = horizons or self.horizons

        if features is None:
            features = self._auto_select_features(df)

        logger.info(f"扫描 {len(features)} 个特征 × {len(horizons)} 个周期 ...")

        rows = []
        for feat in features:
            for h in horizons:
                row = self.scan_feature(df, feat, h)
                if row is not None:
                    rows.append(row)

        if not rows:
            logger.warning("扫描结果为空，请检查特征列和前向收益列是否已正确计算。")
            return pd.DataFrame()

        result = pd.DataFrame(rows)
        # 追加维度标签
        result["dimension"] = result["feature"].map(
            lambda f: FEATURE_DIM.get(f, "OTHER")
        )
        # 追加信号方向预测（IC<0 → short, IC>0 → long）
        result["signal_dir"] = result["IC"].map(
            lambda ic: "short" if ic < 0 else "long"
        )
        result["abs_ICIR"] = result["ICIR"].abs()
        result = result.sort_values("abs_ICIR", ascending=False).drop(
            columns=["abs_ICIR"]
        )
        return result.reset_index(drop=True)

    # ── 按维度分组展示扫描结果 ──────────────────────────────────────────────
    def scan_by_dimension(
        self,
        scan_df: pd.DataFrame,
        top_per_dim: int = 5,
    ) -> None:
        """
        按维度分组打印 IC 扫描排名，每个维度展示 top_per_dim 条。
        同时按 signal_dir 区分潜在 long / short 信号。
        """
        dims = [
            "TIME",
            "PRICE",
            "TRADE_FLOW",
            "LIQUIDITY",
            "POSITIONING",
            "LIQUIDATION",
            "MICROSTRUCTURE",
            "ORDER_FLOW",
            "MARK_PRICE",
            "OTHER",
        ]
        header = (
            f"{'特征':<32} {'周期':>5} {'方向':>6} "
            f"{'IC':>8} {'ICIR':>8} {'t':>7} {'IC正%':>7} {'天数':>5}"
        )
        sep = "-" * len(header)

        print()
        print("=" * len(header))
        print("  特征 IC 扫描 — 按维度分组")
        print("=" * len(header))

        for dim in dims:
            sub = scan_df[scan_df["dimension"] == dim]
            if sub.empty:
                continue
            print(f"\n  [{dim}]  (共 {len(sub)} 条结果)")
            print(sep)

            # 展示 LONG 和 SHORT 各 top_per_dim 条
            for direction in ["long", "short"]:
                dir_sub = sub[sub["signal_dir"] == direction].head(top_per_dim)
                if dir_sub.empty:
                    continue
                label = "  LONG 潜力" if direction == "long" else "  SHORT 潜力"
                print(f"{label}:")
                print(f"  {header}")
                print(f"  {sep}")
                for _, row in dir_sub.iterrows():
                    print(
                        f"  {row['feature']:<32} {int(row['horizon']):>5} "
                        f"  {row['signal_dir']:>4}  "
                        f"{row['IC']:>+8.5f} {row['ICIR']:>+8.4f} "
                        f"{row['t_stat']:>+7.3f} {row['IC_pos_pct']:>6.1f}% "
                        f"{int(row['n_days']):>5}"
                    )
        print()
        print("=" * len(header))

    # ── 最佳预测周期汇总（每个特征取最优 horizon）─────────────────────────
    def best_per_feature(self, scan_df: pd.DataFrame) -> pd.DataFrame:
        """
        对 scan_all() 的结果，每个特征只保留 |ICIR| 最高的那个 horizon。
        返回按 |ICIR| 降序排列的 DataFrame。
        """
        idx = scan_df.groupby("feature")["ICIR"].apply(
            lambda s: s.abs().idxmax()
        )
        return scan_df.loc[idx.values].sort_values(
            "ICIR", key=abs, ascending=False
        ).reset_index(drop=True)

    # ── 内部辅助 ────────────────────────────────────────────────────────────
    def _auto_select_features(self, df: pd.DataFrame) -> List[str]:
        """
        自动选取适合扫描的数值列：
          - 排除 EXCLUDE_COLS 和前向收益列
          - 排除 NaN 占比 > 80% 的列
          - 排除常数列（std == 0）
        """
        fwd_cols = {f"fwd_ret_{h}" for h in self.horizons}
        candidates = [
            c for c in df.columns
            if c not in EXCLUDE_COLS
            and c not in fwd_cols
            and pd.api.types.is_numeric_dtype(df[c])
        ]

        valid = []
        for c in candidates:
            nan_frac = df[c].isna().mean()
            if nan_frac > 0.80:
                continue
            if df[c].std() == 0:
                continue
            valid.append(c)

        logger.info(f"自动选取 {len(valid)} 个有效特征列（共 {len(candidates)} 个候选）")
        return valid
