"""
维度1: 时间结构 (TIME)

物理对应: 算法的执行调度时钟。

输入列 (来自klines DataFrame):
  timestamp  — 毫秒时间戳 (UTC)

输出列 (追加到df):
  minute_in_hour              — 分钟在小时中的位置 (0-59)
  hour_in_day                 — 小时在天中的位置 (0-23)
  minutes_to_funding          — 距下次资金费率结算的分钟数 (每8h: 00/08/16 UTC)
  hours_to_options_expiry     — 距下次BTC期权到期的小时数 (每周五08:00 UTC)
  minutes_since_last_big_move — 距上次1分钟振幅>5x均值的分钟数
  is_weekend                  — 是否UTC周末 (bool)
"""

import numpy as np
import pandas as pd


def compute_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算时间结构特征，就地追加列并返回。

    Args:
        df: 必须包含 'timestamp' (ms) 列。

    Returns:
        追加了时间特征列的 DataFrame。
    """
    ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    df["minute_in_hour"] = ts.dt.minute.astype("int16")
    df["hour_in_day"]    = ts.dt.hour.astype("int16")
    df["is_weekend"]     = (ts.dt.dayofweek >= 5).astype("int8")  # 5=Sat,6=Sun

    # ── 距下次资金费率结算的分钟数 ──────────────────────────────────────
    # 结算时刻: 00:00, 08:00, 16:00 UTC → 每天3次，间隔8h
    minutes_in_day = ts.dt.hour * 60 + ts.dt.minute
    funding_anchors = np.array([0, 480, 960])          # 0, 8*60, 16*60
    # 找到下一个结算点
    mins_to = np.full(len(df), 1441.0)                 # 初始化为大值（非NaN，np.minimum会传播NaN）
    for anchor in funding_anchors:
        diff = anchor - minutes_in_day
        diff = diff.where(diff >= 0, diff + 1440)      # 负数→加一天；0表示恰好在结算时刻
        mins_to = np.minimum(mins_to, diff.values)
    df["minutes_to_funding"] = mins_to.astype("float32")

    # ── 距下次期权到期的小时数 ───────────────────────────────────────────
    # 到期: 每周五 08:00 UTC
    # dayofweek: Mon=0 … Fri=4 … Sun=6
    dow          = ts.dt.dayofweek                     # 0=Mon
    hour         = ts.dt.hour
    minute       = ts.dt.minute
    hours_in_week = dow * 24 + hour + minute / 60.0    # 本周已过小时数
    friday_8h     = 4 * 24 + 8                         # 周五08:00 = 本周第 104 小时
    diff_hours    = friday_8h - hours_in_week
    # 负数 → 已过当周五，指向下周五
    diff_hours    = diff_hours.where(diff_hours > 0, diff_hours + 7 * 24)
    df["hours_to_options_expiry"] = diff_hours.astype("float32")

    # ── 距上次大振幅的分钟数 ─────────────────────────────────────────────
    # 大振幅定义: (high - low) / close > 5 * rolling_mean(20)
    if "high" in df.columns and "low" in df.columns and "close" in df.columns:
        amplitude = (df["high"] - df["low"]) / df["close"]
        amp_ma20  = amplitude.rolling(20, min_periods=1).mean()
        big_move  = (amplitude > 5 * amp_ma20)

        # 计算距上次 big_move=True 的分钟数
        # 用累计索引差：对每行找最近的 big_move 行索引
        idx = np.arange(len(df))
        last_big = pd.Series(np.where(big_move, idx, np.nan)).ffill().values
        mins_since = np.where(np.isnan(last_big), np.nan, idx - last_big)
        df["minutes_since_last_big_move"] = mins_since.astype("float32")
        # 顺便保存振幅供其他维度复用
        df["amplitude_1m"] = amplitude.astype("float32")
        df["amplitude_ma20"] = amp_ma20.astype("float32")
    else:
        df["minutes_since_last_big_move"] = np.nan
        df["amplitude_1m"]  = np.nan
        df["amplitude_ma20"] = np.nan

    return df
