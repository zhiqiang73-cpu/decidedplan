"""
多时间粒度信号工具函数

供 P1-8 ~ P1-11 信号检测器共用，计算"状态变化"特征：
  - vol_drought_blocks: 在 tf_min 粒度上，连续多少个时间块的成交量低迷
  - price_compression_blocks: 在 tf_min 粒度上，连续多少个时间块的振幅收缩
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_state_blocks(
    df: pd.DataFrame,
    tf_min: int,
    vol_drought_ratio: float = 0.5,
) -> tuple[int, int]:
    """
    从 1 分钟 DataFrame 计算 vol_drought_blocks 和 price_compression_blocks。

    Args:
        df: 1 分钟 K 线 DataFrame，需包含 volume / high / low 列。
        tf_min: 聚合粒度（分钟）。
        vol_drought_ratio: 成交量低于均值多少比例算干旱，默认 0.5。

    Returns:
        (vol_drought_count, price_compression_count)
        - vol_drought_count: 当前连续多少个 tf_min 块成交量低迷
        - price_compression_count: 当前连续多少个 tf_min 块振幅收缩
    """
    vol  = df["volume"].values.astype(float)
    high = df["high"].values.astype(float)
    low  = df["low"].values.astype(float)
    n = len(df)

    if n < tf_min * 2:
        return 0, 0

    # 完整 tf_min 块数（不含末尾不足一块的部分）
    n_blocks = n // tf_min
    if n_blocks < 3:
        return 0, 0

    vol_blocks   = np.zeros(n_blocks)
    range_blocks = np.zeros(n_blocks)

    for i in range(n_blocks):
        s = i * tf_min
        e = s + tf_min
        vol_blocks[i]   = vol[s:e].sum()
        range_blocks[i] = high[s:e].max() - low[s:e].min()

    # 20 块滚动均值/中位数（窗口不足时用全部已知数据）
    vol_drought  = np.zeros(n_blocks, dtype=bool)
    compress     = np.zeros(n_blocks, dtype=bool)
    ma_window    = 20

    for i in range(1, n_blocks):
        start = max(0, i - ma_window)
        hist_vol   = vol_blocks[start:i]
        hist_range = range_blocks[start:i]
        if len(hist_vol) == 0:
            continue
        vol_ma  = hist_vol.mean()
        rng_med = np.median(hist_range)
        vol_drought[i] = vol_blocks[i] < vol_ma * vol_drought_ratio
        compress[i]    = range_blocks[i] < rng_med

    # 从末尾往前数连续计数
    vol_drought_count = 0
    for v in reversed(vol_drought):
        if v:
            vol_drought_count += 1
        else:
            break

    price_compression_count = 0
    for v in reversed(compress):
        if v:
            price_compression_count += 1
        else:
            break

    return vol_drought_count, price_compression_count
