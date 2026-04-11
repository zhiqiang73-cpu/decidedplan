"""
维度3: 交易流 (TRADE_FLOW)

物理对应: 不同参与者(散户/算法/知情交易者/做市商)的行为指纹。

输入列 (来自klines DataFrame):
  volume, quote_volume, trades, taker_buy_base, taker_buy_quote, close

输出列 (追加到df):
  taker_buy_sell_ratio   — Taker买入量 / 卖出量
  volume_vs_ma20         — 当分钟成交量 / 20分钟均量
  avg_trade_size         — 平均单笔成交额 (USDT)
  volume_acceleration    — 成交量二阶导 (加速增长检测)
  trade_interval_cv      — 成交时间间隔变异系数代理 (低=TWAP均匀)
  volume_autocorr_lag5   — 成交量lag=5的自相关 (rolling 60min)
  avg_trade_size_cv_10m  — 10分钟内单笔成交额变异系数
"""

import numpy as np
import pandas as pd


def compute_trade_flow_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算交易流特征，追加列并返回。

    Args:
        df: 必须包含 volume, quote_volume, trades, taker_buy_base 列。

    Returns:
        追加了交易流特征列的 DataFrame。
    """
    volume   = df["volume"]
    qv       = df["quote_volume"]
    trades   = df["trades"].replace(0, np.nan)

    # ── Taker 买卖比 ─────────────────────────────────────────────────────
    taker_buy = df["taker_buy_base"]
    taker_sell = (volume - taker_buy).clip(lower=0)
    df["taker_buy_sell_ratio"] = (
        (taker_buy / taker_sell.replace(0, np.nan))
        .replace([np.inf, -np.inf], np.nan)
    ).astype("float32")

    # taker_buy_pct (用于 P0-2 信号): taker买占总量比
    df["taker_buy_pct"] = (taker_buy / volume.replace(0, np.nan)).astype("float32")

    # ── 成交量相对均量 ────────────────────────────────────────────────────
    vol_ma20 = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    df["volume_vs_ma20"] = (volume / vol_ma20).astype("float32")
    df["volume_ma20"]    = vol_ma20.astype("float32")

    # ── 平均单笔成交额 ────────────────────────────────────────────────────
    df["avg_trade_size"] = (qv / trades).astype("float32")

    # ── 成交量加速度 (二阶导) ─────────────────────────────────────────────
    df["volume_acceleration"] = volume.diff().diff().astype("float32")

    # ── 成交时间间隔变异系数代理 ──────────────────────────────────────────
    # 用 1/trades 的 rolling std / mean 近似 CV (trades多→间隔小且均匀)
    inv_trades = (1.0 / trades)
    roll_mean  = inv_trades.rolling(10, min_periods=3).mean().replace(0, np.nan)
    roll_std   = inv_trades.rolling(10, min_periods=3).std()
    df["trade_interval_cv"] = (roll_std / roll_mean).astype("float32")

    # ── 成交量 lag-5 自相关 (rolling 60 min) ─────────────────────────────
    # 用 rolling apply 计算: corr(volume[t-59:t], volume[t-54:t+1]) 近似
    # 因 rolling corr 需两列，用 shift(5)
    vol_lag5 = volume.shift(5)
    autocorr = (
        volume.rolling(60, min_periods=20)
        .corr(vol_lag5)
    ).astype("float32")
    df["volume_autocorr_lag5"] = autocorr

    # ── 10分钟内单笔成交额变异系数 ────────────────────────────────────────
    avg_ts = df["avg_trade_size"]
    cv_mean = avg_ts.rolling(10, min_periods=3).mean().replace(0, np.nan)
    cv_std  = avg_ts.rolling(10, min_periods=3).std()
    df["avg_trade_size_cv_10m"] = (cv_std / cv_mean).astype("float32")

    return df
