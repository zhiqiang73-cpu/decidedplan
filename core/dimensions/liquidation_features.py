"""
维度7: 清算流 (LIQUIDATION)

物理对应: 强制平仓事件揭示杠杆拥挤程度和方向性失衡。

数据来源:
  - liquidations parquet (event_time, symbol, side, quantity, avg_price, filled_qty)
  - 仅使用 symbol=BTCUSDT 的行
  - BUY = 空头被清算 (看涨信号), SELL = 多头被清算 (看跌信号)

这些列在 feature_engine.py 中合并，本函数接收已合并的 df。
如果列不存在 (历史数据不足或未采集)，对应特征填 NaN，不影响其他维度。

pre-merged 列 (feature_engine 聚合后合并):
  liq_sell_usd_1m   — 1分钟内多头清算USD总量 (SELL side, 看跌)
  liq_buy_usd_1m    — 1分钟内空头清算USD总量 (BUY side, 看涨)
  liq_size_max_1m   — 1分钟内单笔清算最大USD (大鲸清算代理)

输出列 (追加到df):
  btc_liq_net_pressure  — (多头清算 - 空头清算) / (总量 + 1), 正值=看跌压力
  total_liq_usd_5m      — 5分钟滚动清算USD总量
  liq_size_p90_5m       — 5分钟滚动单分钟最大清算的 p90 (鲸鱼清算程度)
"""

import numpy as np
import pandas as pd


def compute_liquidation_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算清算流特征，追加列并返回。

    Args:
        df: 包含 liq_sell_usd_1m, liq_buy_usd_1m, liq_size_max_1m 列 (可为NaN)。
            这些列由 feature_engine.py 在调用本函数前合并。

    Returns:
        追加了清算特征列的 DataFrame。
    """

    has_liq = (
        "liq_sell_usd_1m" in df.columns
        and "liq_buy_usd_1m" in df.columns
    )

    if has_liq:
        sell_usd = df["liq_sell_usd_1m"].fillna(0.0)
        buy_usd  = df["liq_buy_usd_1m"].fillna(0.0)
        total    = sell_usd + buy_usd

        # ── 净清算压力 (归一化到 [-1, +1] 区间) ──────────────────────────
        # 正值 = 多头清算更多 = 看跌压力
        df["btc_liq_net_pressure"] = (
            (sell_usd - buy_usd) / (total + 1.0)
        ).astype("float32")

        # ── 5分钟滚动清算总量 ─────────────────────────────────────────────
        df["total_liq_usd_5m"] = (
            total.rolling(5, min_periods=1).sum()
        ).astype("float32")

    else:
        df["btc_liq_net_pressure"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )
        df["total_liq_usd_5m"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    # ── 5分钟滚动最大单笔清算 p90 ─────────────────────────────────────────
    if "liq_size_max_1m" in df.columns:
        liq_max = df["liq_size_max_1m"].fillna(0.0)
        df["liq_size_p90_5m"] = (
            liq_max.rolling(5, min_periods=1)
            .quantile(0.9)
        ).astype("float32")
    else:
        df["liq_size_p90_5m"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    return df
