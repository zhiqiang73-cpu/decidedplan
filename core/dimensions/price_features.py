"""
维度2: 价格拓扑 (PRICE)

物理对应: 限价单和止损单在价格空间中的分布地形。

输入列 (来自klines DataFrame):
  timestamp, open, high, low, close, volume, quote_volume

输出列 (追加到df):
  dist_to_round_1000     — 距最近整千关口的百分比
  dist_to_round_100      — 距最近整百关口的百分比
  dist_to_24h_high       — 距24小时最高价的百分比（负值=低于24h高点）
  dist_to_24h_low        — 距24小时最低价的百分比（正值=高于24h低点）
  position_in_range_4h   — 当前价在过去4小时范围中的位置 (0%=最低, 100%=最高)
  position_in_range_24h  — 当前价在过去24小时范围中的位置
  vwap_deviation         — 距24小时VWAP的偏离百分比
"""

import numpy as np
import pandas as pd


def compute_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算价格拓扑特征，追加列并返回。

    Args:
        df: 必须包含 close, high, low, volume, quote_volume 列。

    Returns:
        追加了价格特征列的 DataFrame。
    """
    close = df["close"]

    # ── 整数关口距离 ─────────────────────────────────────────────────────
    dist1000 = close % 1000                        # 距下方整千
    dist1000 = dist1000.where(dist1000 <= 500, 1000 - dist1000)  # 取近侧
    df["dist_to_round_1000"] = (dist1000 / close).astype("float32")

    dist100 = close % 100
    dist100 = dist100.where(dist100 <= 50, 100 - dist100)
    df["dist_to_round_100"]  = (dist100 / close).astype("float32")

    # ── 24h / 4h 高低点 ──────────────────────────────────────────────────
    w24 = 1440   # 24h = 1440 根 1m K线
    w4  = 240    # 4h  = 240  根 1m K线

    high_24h = df["high"].rolling(w24, min_periods=1).max()
    low_24h  = df["low"].rolling(w24, min_periods=1).min()
    high_4h  = df["high"].rolling(w4, min_periods=1).max()
    low_4h   = df["low"].rolling(w4, min_periods=1).min()

    df["dist_to_24h_high"] = ((close - high_24h) / close).astype("float32")
    df["dist_to_24h_low"]  = ((close - low_24h)  / close).astype("float32")

    rng_24h = (high_24h - low_24h).replace(0, np.nan)
    rng_4h  = (high_4h  - low_4h).replace(0, np.nan)

    df["position_in_range_24h"] = ((close - low_24h) / rng_24h).astype("float32")
    df["position_in_range_4h"]  = ((close - low_4h)  / rng_4h).astype("float32")

    # ── 24h VWAP 偏离 ────────────────────────────────────────────────────
    # VWAP = Σ(quote_volume) / Σ(volume) 滚动24h
    if "quote_volume" in df.columns and "volume" in df.columns:
        cum_qv  = df["quote_volume"].rolling(w24, min_periods=1).sum()
        cum_vol = df["volume"].rolling(w24, min_periods=1).sum().replace(0, np.nan)
        vwap_24h = cum_qv / cum_vol
        df["vwap_24h"]      = vwap_24h.astype("float32")
        df["vwap_deviation"] = ((close - vwap_24h) / vwap_24h).astype("float32")
    else:
        df["vwap_24h"]       = np.nan
        df["vwap_deviation"]  = np.nan

    return df
