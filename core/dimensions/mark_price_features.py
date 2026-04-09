"""
维度10: 实时标记价格 (MARK_PRICE)

物理对应:
  - 标记价格与指数价格的偏差 = 期货相对现货的溢价/折价
    正溢价 = 多头愿意为期货付溢价, 预示回调压力
    负溢价 (折价) = 多头不愿进场, 预示反弹机会
  - 实时资金费率比8小时REST更及时
    能捕捉每个计息周期内 funding 从0累积到结算的全过程

数据来源:
  - mark_price parquet (btcusdt@markPrice@1s, 每分钟采样一条)
  - 列: timestamp_ms, mark_price, index_price, funding_rate, next_funding_time

pre-merged 列 (feature_engine 聚合后合并):
  mp_funding_rate      -- 当前计息周期实时资金费率 (非结算值)
  mp_mark_price        -- 标记价格
  mp_index_price       -- 指数价格 (多交易所现货综合)
  mp_next_funding_time -- 下次结算时间戳 ms

输出列:
  rt_funding_rate      -- 实时资金费率, 与 REST funding_rate 互补
  mark_basis           -- (mark - index) / index, 正=期货溢价
  mark_basis_ma10      -- 10分钟滚动均值, 过滤噪声
  funding_countdown_m  -- 距下次结算分钟数 (0~480, 物理约束)
"""

import numpy as np
import pandas as pd


def compute_mark_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算实时标记价格特征，追加列并返回。

    如果 mp_* 列不存在 (历史数据未采集)，所有输出列填 NaN，不影响其他维度。
    """

    # ── 实时资金费率 ──────────────────────────────────────────────────────────
    if "mp_funding_rate" in df.columns:
        df["rt_funding_rate"] = df["mp_funding_rate"].astype("float32")
    else:
        df["rt_funding_rate"] = pd.Series(np.nan, index=df.index, dtype="float32")

    if "funding_rate" in df.columns:
        base_funding = pd.to_numeric(df["funding_rate"], errors="coerce")
        realtime_funding = pd.to_numeric(df["rt_funding_rate"], errors="coerce")
        df["funding_rate"] = realtime_funding.where(realtime_funding.notna(), base_funding).astype("float32")
    else:
        df["funding_rate"] = pd.to_numeric(df["rt_funding_rate"], errors="coerce").astype("float32")

    # ── 期货溢价 (标记价格 vs 指数价格) ──────────────────────────────────────
    has_basis = "mp_mark_price" in df.columns and "mp_index_price" in df.columns
    if has_basis:
        mark  = df["mp_mark_price"].astype("float64")
        index = df["mp_index_price"].astype("float64").replace(0.0, np.nan)
        basis = (mark - index) / index
        df["mark_basis"]     = basis.astype("float32")
        df["mark_basis_ma10"] = basis.rolling(10, min_periods=3).mean().astype("float32")
    else:
        df["mark_basis"]      = pd.Series(np.nan, index=df.index, dtype="float32")
        df["mark_basis_ma10"] = pd.Series(np.nan, index=df.index, dtype="float32")

    # ── 距下次结算倒计时 ──────────────────────────────────────────────────────
    # 物理约束: 币安每8小时结算一次 (480分钟), 结算前后行为会变化
    if "mp_next_funding_time" in df.columns and "timestamp" in df.columns:
        nft = df["mp_next_funding_time"].astype("float64")
        ts  = df["timestamp"].astype("float64")
        countdown = ((nft - ts) / 60_000.0).clip(0.0, 480.0)
        df["funding_countdown_m"] = countdown.astype("float32")
    else:
        df["funding_countdown_m"] = pd.Series(np.nan, index=df.index, dtype="float32")

    return df
