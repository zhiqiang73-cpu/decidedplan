"""
维度9: 订单流结构 (ORDER_FLOW)

物理对应: 大单方向、成交节奏和方向延续性揭示知情交易者行为。

数据来源:
  - agg_trades parquet (agg_trade_id, price, quantity, timestamp: Int64 ms,
                         is_buyer_maker: bool)
  - is_buyer_maker=False 表示买方为 taker (主动买入)
  - is_buyer_maker=True  表示卖方为 taker (主动卖出)

这些列在 feature_engine.py 中合并，本函数接收已合并的 df。
如果列不存在 (历史数据未采集)，对应特征填 NaN，不影响其他维度。

pre-merged 列 (feature_engine 聚合后合并):
  at_large_buy_ratio   — 大单(>p90 USD)中买方主动占比 (per minute)
  at_burst_index       — 1分钟内成交时间间隔变异系数 (高=成交聚集)
  at_dir_net_1m        — 1分钟内方向净值 (买单数-卖单数) / 总单数

输出列 (追加到df):
  large_trade_buy_ratio — at_large_buy_ratio 直通, 0~1, >0.5=大单偏买
  trade_burst_index     — at_burst_index 直通, 高=成交爆发性集中
  direction_autocorr    — 5分钟窗口内 at_dir_net_1m 的 lag-1 自相关
                          正值=方向延续(趋势), 负值=均值回归
"""

import numpy as np
import pandas as pd


def compute_order_flow_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算订单流结构特征，追加列并返回。

    Args:
        df: 包含 at_large_buy_ratio, at_burst_index, at_dir_net_1m 列 (可为NaN)。
            这些列由 feature_engine.py 在调用本函数前合并。

    Returns:
        追加了订单流特征列的 DataFrame。
    """

    # ── 大单买方主动占比 ──────────────────────────────────────────────────
    if "at_large_buy_ratio" in df.columns:
        df["large_trade_buy_ratio"] = df["at_large_buy_ratio"].astype("float32")
    else:
        df["large_trade_buy_ratio"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    if "at_dir_net_1m" in df.columns:
        df["direction_net_1m"] = df["at_dir_net_1m"].astype("float32")
    else:
        df["direction_net_1m"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    if "buy_usd_1m" in df.columns and "sell_usd_1m" in df.columns:
        total_notional = (df["buy_usd_1m"] + df["sell_usd_1m"]).replace(0, np.nan)
        df["sell_notional_share_1m"] = (
            df["sell_usd_1m"] / total_notional
        ).astype("float32")
    else:
        df["sell_notional_share_1m"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    # ── 成交爆发指数 (时间间隔变异系数) ──────────────────────────────────
    if "at_burst_index" in df.columns:
        df["trade_burst_index"] = df["at_burst_index"].astype("float32")
    else:
        df["trade_burst_index"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    # ── 方向延续性自相关 (5分钟滚动 lag-1) ───────────────────────────────
    if "at_dir_net_1m" in df.columns:
        dir_net = df["at_dir_net_1m"]
        dir_lag1 = dir_net.shift(1)
        # rolling corr(x, x.shift(1)) 即 lag-1 自相关
        autocorr = dir_net.rolling(5, min_periods=3).corr(dir_lag1)
        df["direction_autocorr"] = autocorr.astype("float32")
    else:
        df["direction_autocorr"] = pd.Series(
            np.nan, index=df.index, dtype="float32"
        )

    return df
