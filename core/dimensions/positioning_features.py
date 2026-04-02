"""
维度5: 持仓杠杆结构 (POSITIONING)

物理对应: 场内仓位的脆弱性分布。

数据来源:
  - open_interest   (merge_asof 自 open_interest parquet, 5m 粒度)
  - long_short_ratio (merge_asof 自 long_short_ratio parquet, 5m 粒度)
  - funding_rate    (merge_asof 自 funding_rate parquet, 8h 粒度)

这些列在 feature_engine.py 中合并，本函数接收已合并的 df。
如果列不存在 (历史数据不足)，对应特征填 NaN，不影响其他维度。

输出列 (追加到df):
  oi_change_rate_5m           — OI 5分钟变化率
  oi_change_rate_1h           — OI 1小时变化率
  funding_rate_trend          — 资金费率方向趋势 (+1/-1/0)
  consecutive_extreme_funding — 连续极端费率期数 (|FR|>0.05%)
  oi_price_divergence_duration— OI涨但价格不涨的持续分钟数
  ls_ratio_change_5m          — 多空比5分钟变化 (供持仓结构与方向确认使用)
"""

import numpy as np
import pandas as pd


def compute_positioning_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算持仓杠杆结构特征，追加列并返回。

    Args:
        df: 包含 open_interest, long_short_ratio, funding_rate 列 (可为NaN)。

    Returns:
        追加了持仓特征列的 DataFrame。
    """

    # ── OI 变化率 ─────────────────────────────────────────────────────────
    if "open_interest" in df.columns:
        oi = df["open_interest"]
        df["oi_change_rate_5m"] = oi.pct_change(5).astype("float32")
        df["oi_change_rate_1h"] = oi.pct_change(60).astype("float32")
    else:
        df["oi_change_rate_5m"] = pd.Series(np.nan, index=df.index, dtype="float32")
        df["oi_change_rate_1h"] = pd.Series(np.nan, index=df.index, dtype="float32")

    # ── 多空比变化 ────────────────────────────────────────────────────────
    if "long_short_ratio" in df.columns:
        ls = df["long_short_ratio"]
        df["ls_ratio_change_5m"] = ls.pct_change(5).astype("float32")
    else:
        df["ls_ratio_change_5m"] = pd.Series(np.nan, index=df.index, dtype="float32")

    # ── 资金费率趋势 ──────────────────────────────────────────────────────
    if "funding_rate" in df.columns:
        fr = df["funding_rate"]

        # 趋势: rolling 3 期方向 (+1=连续为正, -1=连续为负, 0=混合)
        fr_sign = np.sign(fr)
        roll3_sum = fr_sign.rolling(3, min_periods=2).sum()
        trend = pd.cut(
            roll3_sum,
            bins=[-4, -2.5, 2.5, 4],
            labels=[-1, 0, 1]
        ).astype("float32")
        df["funding_rate_trend"] = trend

        # 连续极端费率期数 (|FR| > 0.05% = 0.0005)
        is_extreme = (fr.abs() > 0.0005).astype(int)
        # 用累计分组计算连续
        grp = (is_extreme != is_extreme.shift()).cumsum()
        consecutive = is_extreme.groupby(grp).cumsum()
        df["consecutive_extreme_funding"] = consecutive.where(
            is_extreme == 1, 0
        ).astype("float32")
    else:
        df["funding_rate_trend"]           = pd.Series(np.nan, index=df.index, dtype="float32")
        df["consecutive_extreme_funding"]  = pd.Series(np.nan, index=df.index, dtype="float32")

    # ── OI-价格背离持续时间 ───────────────────────────────────────────────
    # OI 5分钟增长 > +1% 但 close 5分钟变化 < +0.5% → 认为背离
    if "oi_change_rate_5m" in df.columns and "close" in df.columns:
        price_change_5m = df["close"].pct_change(5)
        is_diverging = (
            (df["oi_change_rate_5m"] > 0.01) &
            (price_change_5m < 0.005)
        ).astype(int)
        grp2 = (is_diverging != is_diverging.shift()).cumsum()
        dur  = is_diverging.groupby(grp2).cumsum()
        df["oi_price_divergence_duration"] = dur.where(
            is_diverging == 1, 0
        ).astype("float32")
    else:
        df["oi_price_divergence_duration"] = pd.Series(np.nan, index=df.index, dtype="float32")

    return df
