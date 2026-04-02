"""
维度4: 流动性结构 (LIQUIDITY)

物理对应: Orderbook的承载能力 — 信号→价格变动之间的传导介质。

第一阶段说明:
  depth/spread 实时数据暂不可用。用 klines 计算代理指标:
  - kyle_lambda = |Δclose| / volume  (价格冲击系数)
  - spread_proxy = (high - low) / close  (日内spread代理)

输入列 (来自klines DataFrame):
  close, high, low, volume

输出列 (追加到df):
  kyle_lambda         — 价格冲击系数 rolling 20min 均值
  spread_proxy        — (high-low)/close 代理 spread
  spread_vs_ma20      — 当前 spread_proxy / 20分钟均值
"""

import numpy as np
import pandas as pd


def compute_liquidity_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算流动性结构特征 (klines 代理版)，追加列并返回。

    Args:
        df: 必须包含 close, high, low, volume 列。
            time_features 已先运行（amplitude_1m 已存在则复用）。

    Returns:
        追加了流动性特征列的 DataFrame。
    """
    close  = df["close"]
    volume = df["volume"].replace(0, np.nan)

    # ── Kyle's Lambda (价格冲击系数) ─────────────────────────────────────
    # λ = |ΔP| / Q，rolling 20min 均值平滑
    abs_delta_close = close.diff().abs()
    raw_lambda = abs_delta_close / volume
    df["kyle_lambda"] = (
        raw_lambda.rolling(20, min_periods=5).mean()
    ).astype("float32")

    # ── Spread 代理 ───────────────────────────────────────────────────────
    # (high - low) / close 反映单分钟内的价格波动区间
    if "high" in df.columns and "low" in df.columns:
        spread_proxy = (df["high"] - df["low"]) / close.replace(0, np.nan)
    elif "amplitude_1m" in df.columns:
        spread_proxy = df["amplitude_1m"]
    else:
        spread_proxy = pd.Series(np.nan, index=df.index)

    df["spread_proxy"] = spread_proxy.astype("float32")

    sp_ma20 = spread_proxy.rolling(20, min_periods=5).mean().replace(0, np.nan)
    df["spread_vs_ma20"] = (spread_proxy / sp_ma20).astype("float32")

    return df
