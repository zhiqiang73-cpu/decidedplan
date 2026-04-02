"""
维度8: 盘口微结构 (MICROSTRUCTURE)

物理对应: 最优买卖档位揭示瞬时供需失衡和流动性状态。

数据来源:
  - book_ticker parquet (timestamp_ms, symbol=BTCUSDT, bid_price, bid_qty,
                          ask_price, ask_qty)
  - 已过滤为 BTCUSDT，约1秒一条

这些列在 feature_engine.py 中合并，本函数接收已合并的 df。
如果列不存在 (历史数据未采集)，对应特征填 NaN，不影响其他维度。

pre-merged 列 (feature_engine 聚合后合并，1分钟均值):
  bk_bid_qty_mean   — 1分钟内 bid_qty 均值
  bk_ask_qty_mean   — 1分钟内 ask_qty 均值
  bk_spread_mean    — 1分钟内相对价差均值 (ask-bid)/mid

输出列 (追加到df):
  quote_imbalance   — 盘口量失衡 (bid-ask)/(bid+ask), 正=买盘更厚
  spread_anomaly    — 价差相对20bar均值的偏离, 正=价差异常扩大
  bid_depth_ratio   — bid_qty/(bid+ask), >0.5 = 买方更深
"""

import numpy as np
import pandas as pd


def compute_microstructure_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算盘口微结构特征，追加列并返回。

    Args:
        df: 包含 bk_bid_qty_mean, bk_ask_qty_mean, bk_spread_mean 列 (可为NaN)。
            这些列由 feature_engine.py 在调用本函数前合并。

    Returns:
        追加了微结构特征列的 DataFrame。
    """

    has_bk = (
        "bk_bid_qty_mean" in df.columns
        and "bk_ask_qty_mean" in df.columns
    )

    if has_bk:
        bid = df["bk_bid_qty_mean"]
        ask = df["bk_ask_qty_mean"]
        total = (bid + ask).replace(0.0, np.nan)

        # ── 盘口量失衡 ────────────────────────────────────────────────────
        # +1 = 纯买盘, -1 = 纯卖盘
        df["quote_imbalance"] = (
            (bid - ask) / total
        ).astype("float32")

        # ── 买方深度占比 ──────────────────────────────────────────────────
        df["bid_depth_ratio"] = (
            bid / total
        ).astype("float32")

    else:
        df["quote_imbalance"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )
        df["bid_depth_ratio"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    # ── 价差异常 (相对20bar均值) ──────────────────────────────────────────
    if "bk_spread_mean" in df.columns:
        spread = df["bk_spread_mean"]
        spread_ma20 = spread.rolling(20, min_periods=5).mean().replace(0.0, np.nan)
        df["spread_anomaly"] = (
            (spread / spread_ma20 - 1.0)
        ).astype("float32")
    else:
        df["spread_anomaly"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    return df
