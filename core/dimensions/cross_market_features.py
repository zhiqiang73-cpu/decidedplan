"""
维度6: 跨市场信息流 (CROSS_MARKET)

物理对应: 信息在不同市场之间传播的速度和方向。

第一阶段说明:
  - basis_perp_spot: 需要现货价格，暂标 NaN
  - btc_eth_correlation: 需要 ETHUSDT K线，暂标 NaN
  这两个维度不影响第一阶段的5个信号检测器运行。

当 eth_df 传入时会自动计算 BTC-ETH 相关系数。

输入列:
  df      — BTCUSDT 永续合约 klines (close 列)
  eth_df  — (可选) ETHUSDT klines，需包含 timestamp, close

输出列 (追加到df):
  basis_perp_spot       — NaN (第一阶段占位)
  btc_eth_corr_30m      — BTC-ETH 30分钟滚动相关系数 (需 eth_df)
  btc_eth_corr_change   — 相关性变化率 (去相关化检测)
"""

import numpy as np
import pandas as pd


def compute_cross_market_features(
    df: pd.DataFrame,
    eth_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    计算跨市场信息流特征，追加列并返回。

    Args:
        df:     BTCUSDT klines DataFrame，必须包含 timestamp, close。
        eth_df: (可选) ETHUSDT klines DataFrame，包含 timestamp, close。

    Returns:
        追加了跨市场特征列的 DataFrame。
    """

    # ── 基差 (永续-现货) ──────────────────────────────────────────────────
    # 第一阶段暂无现货价格，填 NaN
    df["basis_perp_spot"] = np.nan

    # ── BTC-ETH 滚动相关系数 ─────────────────────────────────────────────
    if eth_df is not None and "close" in eth_df.columns:
        # 按 timestamp 对齐 (merge_asof，容忍 1 分钟误差)
        eth_aligned = pd.merge_asof(
            df[["timestamp"]].copy(),
            eth_df[["timestamp", "close"]].rename(columns={"close": "eth_close"}),
            on="timestamp",
            tolerance=60_000,   # 1 分钟
            direction="nearest",
        )
        eth_close = eth_aligned["eth_close"]
        btc_close = df["close"]

        corr_30m = btc_close.rolling(30, min_periods=15).corr(eth_close)
        df["btc_eth_corr_30m"]    = corr_30m.astype("float32")
        df["btc_eth_corr_change"] = corr_30m.diff(5).astype("float32")
    else:
        df["btc_eth_corr_30m"]    = np.nan
        df["btc_eth_corr_change"] = np.nan

    return df
