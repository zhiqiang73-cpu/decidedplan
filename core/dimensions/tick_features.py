"""
维度: 逐笔成交特征 (TICK)

从原始 aggTrade 数据聚合而成的时间窗口 bar 上计算微结构特征。
支持 10s / 30s / 60s 三个窗口。

本模块为纯函数集合，输入 bar DataFrame，追加 tick_* 列后返回。
不含任何硬编码阈值或持仓时间。

输入 bar DataFrame 必须包含（由 TickFeatureEngine 生成）:
  open, high, low, close       -- OHLC 价格
  buy_usd, sell_usd, notional  -- USD 成交量方向分解
  trade_count                  -- 窗口内成交笔数
  direction_net                -- 买卖方向净值 (买单占比 - 卖单占比)
  large_buy_ratio              -- 大单买方占比 (>p90 USD)
  burst_index                  -- 成交时间集中度 (间隔 CV)
  window_vwap                  -- 窗口内 VWAP
  timestamp                    -- 窗口起始时间 (ms)

可选（来自 book_ticker 合并，缺失时对应 tick_book_* 特征全为 NaN）:
  bt_bid_price, bt_ask_price   -- 窗口内 bid/ask 均值
  bt_bid_qty, bt_ask_qty       -- 窗口内 bid/ask 均量

输出 tick_* 列:
  交易流:
    tick_direction_net          买卖方向净值 (直通 direction_net)
    tick_buy_sell_ratio         买方 USD / 总 USD
    tick_large_buy_ratio        大单买方占比 (直通 large_buy_ratio)
    tick_burst_index            成交时间集中度 (直通 burst_index)
    tick_trade_count            窗口成交笔数
    tick_trade_size_mean        平均单笔 USD 大小

  价格微结构:
    tick_return_pct             窗口收益率 %
    tick_range_pct              窗口振幅 % (high-low)/close
    tick_vwap_dev_pct           收盘价偏离窗口 VWAP %
    tick_absorption_ratio       吸收比 = sell_usd / max(|return|, eps)
    tick_price_impact           单位成交量的价格冲击
    tick_bounce_rate            反弹率 = (close - low) / (high - low)

  复合得分:
    tick_absorption_long_score  做多吸收强度
    tick_absorption_short_score 做空吸收强度
    tick_momentum_exhaustion    动量枯竭分

  持续性块（状态计数）:
    tick_absorption_blocks      连续 N 个窗口出现做多吸收
    tick_exhaustion_blocks      连续 N 个窗口动量递减
    tick_direction_persist      连续 N 个窗口方向偏向同侧

  盘口（可选，需 bt_* 列）:
    tick_bid_ask_imbalance      (bid_qty - ask_qty) / (bid_qty + ask_qty)
    tick_spread_pct             (ask_price - bid_price) / mid * 100
    tick_spread_compression     spread / rolling_20_mean_spread
    tick_imbalance_change       bid_ask_imbalance 当前 - 前一窗口
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_EPS = 1e-10


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _consecutive_blocks(condition: pd.Series) -> pd.Series:
    """
    计算连续满足 condition 的 bar 数量（重置到 0 当条件不满足）。
    例: [F,T,T,T,F,T,T] -> [0,1,2,3,0,1,2]

    numpy 向量化实现（避免 Python 循环，10s bar 90天 = 77万行时仍在毫秒级）。
    原理: 在每段 True 连续区间内，用全局位置编号减去段起点编号得到局部计数。
    """
    arr = condition.fillna(False).astype(bool).values
    n = len(arr)
    if n == 0:
        return pd.Series(dtype="int32")

    # 每个位置的全局索引
    idx = np.arange(n, dtype=np.int32)

    # 在 arr=False 的位置记录其索引，arr=True 的位置记录 0
    # maximum.accumulate 向前传播最近一次 False 位置的索引
    reset_idx = np.where(arr, 0, idx)
    last_false = np.maximum.accumulate(reset_idx)

    # 局部计数 = 当前位置 - 最近 False 位置
    # arr=False 时结果为 0（idx[i] == last_false[i]）
    # arr=True  时结果为从 False 之后数的序号
    out = np.where(arr, idx - last_false, 0).astype(np.int32)
    return pd.Series(out, index=condition.index, dtype="int32")


# ── 交易流特征 ────────────────────────────────────────────────────────────────

def compute_tick_flow_features(bars: pd.DataFrame) -> pd.DataFrame:
    """
    计算交易流节奏特征，追加 tick_direction_net ... tick_trade_size_mean。

    Args:
        bars: 包含 direction_net, buy_usd, sell_usd, notional,
              trade_count, large_buy_ratio, burst_index 的 bar DataFrame。
    Returns:
        追加了 tick_flow 特征列的 DataFrame（原地修改并返回）。
    """
    df = bars

    # 方向净值：>0 偏买，<0 偏卖，范围 [-1, 1]
    if "direction_net" in df.columns:
        df["tick_direction_net"] = df["direction_net"].astype("float32")
    else:
        df["tick_direction_net"] = np.nan

    # 买卖 USD 比：买方 USD 占总 USD
    notional = df["notional"].where(df["notional"] > _EPS, np.nan)
    if "buy_usd" in df.columns:
        df["tick_buy_sell_ratio"] = (df["buy_usd"] / notional).astype("float32")
    else:
        df["tick_buy_sell_ratio"] = np.nan

    # 大单买方占比
    if "large_buy_ratio" in df.columns:
        df["tick_large_buy_ratio"] = df["large_buy_ratio"].astype("float32")
    else:
        df["tick_large_buy_ratio"] = np.nan

    # 成交时间集中度
    if "burst_index" in df.columns:
        df["tick_burst_index"] = df["burst_index"].astype("float32")
    else:
        df["tick_burst_index"] = np.nan

    # 窗口内成交笔数
    if "trade_count" in df.columns:
        df["tick_trade_count"] = df["trade_count"].astype("float32")
    else:
        df["tick_trade_count"] = np.nan

    # 平均单笔 USD 大小
    if "trade_count" in df.columns and "notional" in df.columns:
        tc = df["trade_count"].where(df["trade_count"] > 0, np.nan)
        df["tick_trade_size_mean"] = (df["notional"] / tc).astype("float32")
    else:
        df["tick_trade_size_mean"] = np.nan

    return df


# ── 价格微结构特征 ────────────────────────────────────────────────────────────

def compute_tick_microstructure_features(
    bars: pd.DataFrame,
    window_5m: int,
) -> pd.DataFrame:
    """
    计算价格微结构特征，追加 tick_return_pct ... tick_bounce_rate。

    Args:
        bars:      包含 open, high, low, close, sell_usd, notional, window_vwap 的 bar DataFrame。
        window_5m: 对应 5 分钟的 bar 数量（例如 10s 窗口 -> 30 bars）。
    Returns:
        追加了微结构特征列的 DataFrame。
    """
    df = bars
    close = df["close"].replace(0.0, np.nan)

    # 窗口收益率 %
    df["tick_return_pct"] = (close.pct_change() * 100.0).astype("float32")

    # 振幅 % = (high - low) / close
    df["tick_range_pct"] = ((df["high"] - df["low"]) / close * 100.0).astype("float32")

    # 偏离窗口 VWAP %
    if "window_vwap" in df.columns:
        vwap = df["window_vwap"].replace(0.0, np.nan)
        df["tick_vwap_dev_pct"] = ((close / vwap - 1.0) * 100.0).astype("float32")
    else:
        df["tick_vwap_dev_pct"] = np.nan

    # 吸收比：卖方 USD 大但价格没跌（量越大、影响越小 = 吸收越强）
    # = sell_usd / max(|return_pct|, eps) — 值越高说明卖压被吸收
    if "sell_usd" in df.columns:
        abs_ret = df["tick_return_pct"].abs().fillna(0.0)
        denominator = abs_ret.where(abs_ret > 0.001, 0.001)
        df["tick_absorption_ratio"] = (
            (df["sell_usd"] / df["notional"].replace(0.0, np.nan)) / denominator
        ).astype("float32")
    else:
        df["tick_absorption_ratio"] = np.nan

    # 价格冲击：每单位 USD 成交量带来的价格变化 bps
    if "notional" in df.columns:
        notional = df["notional"].where(df["notional"] > _EPS, np.nan)
        df["tick_price_impact"] = (
            (df["tick_return_pct"].abs() / notional * 1e6).astype("float32")
        )
    else:
        df["tick_price_impact"] = np.nan

    # 反弹率：窗口内价格从低点反弹的比例，(close - low) / (high - low)
    hl_range = (df["high"] - df["low"]).where((df["high"] - df["low"]) > _EPS, np.nan)
    df["tick_bounce_rate"] = ((close - df["low"]) / hl_range).astype("float32")

    return df


# ── 复合得分特征 ──────────────────────────────────────────────────────────────

def compute_tick_composite_scores(bars: pd.DataFrame) -> pd.DataFrame:
    """
    计算吸收/枯竭复合得分。

    基于 tick_buy_sell_ratio, tick_direction_net, tick_large_buy_ratio,
    tick_burst_index, tick_return_pct 计算。
    这些列须已由前面的函数填充。

    Returns:
        追加了 tick_absorption_long_score, tick_absorption_short_score,
        tick_momentum_exhaustion 的 DataFrame。
    """
    df = bars

    sell_share = 1.0 - df["tick_buy_sell_ratio"].fillna(0.5)
    buy_share = df["tick_buy_sell_ratio"].fillna(0.5)
    ret_pct = df["tick_return_pct"].fillna(0.0)
    burst_z = df["tick_burst_index"].fillna(0.0)
    large_buy = df["tick_large_buy_ratio"].fillna(0.5)
    dir_net = df["tick_direction_net"].fillna(0.0)

    # 做多吸收：卖压大 + 价格没跌 + 成交集中
    df["tick_absorption_long_score"] = (
        (sell_share - 0.5) * 100.0
        - ret_pct
        + burst_z * 0.8
    ).astype("float32")

    # 做空吸收：买压大 + 价格没涨 + 成交集中
    df["tick_absorption_short_score"] = (
        (buy_share - 0.5) * 100.0
        + ret_pct
        + burst_z * 0.8
    ).astype("float32")

    # 动量枯竭（带方向）：
    # 正值 = 当前方向净值/大单低于近期均值（做多动量在减弱，短期看空）
    # 负值 = 当前方向净值/大单高于近期均值（做多动量在加速，短期看多）
    # IC 扫描时：枯竭分 > 阈值 → 方向动量在衰退，倾向反转
    dir_delta = dir_net - dir_net.rolling(5, min_periods=2).mean()
    large_delta = large_buy - large_buy.rolling(5, min_periods=2).mean()
    df["tick_momentum_exhaustion"] = (
        (-dir_delta * 40.0) + (-large_delta * 60.0)
    ).astype("float32")

    return df


# ── 持续性状态块特征 ──────────────────────────────────────────────────────────

def compute_tick_block_state(bars: pd.DataFrame, window_5m: int) -> pd.DataFrame:
    """
    计算持续性状态计数特征（P 系列 vol_drought_blocks 的 tick 等价物）。

    Args:
        bars:      已含 tick_absorption_long_score, tick_momentum_exhaustion,
                   tick_direction_net 的 bar DataFrame。
        window_5m: 5 分钟对应的 bar 数量（用于计算滚动中位数作为自适应阈值）。
    Returns:
        追加了 tick_absorption_blocks, tick_exhaustion_blocks,
        tick_direction_persist 的 DataFrame。
    """
    df = bars

    if "tick_absorption_long_score" in df.columns:
        # 自适应阈值：滚动 5m 中位数，超过中位数才算"正在吸收"
        median_abs = df["tick_absorption_long_score"].rolling(
            window_5m, min_periods=max(window_5m // 2, 5)
        ).median()
        absorbing = df["tick_absorption_long_score"] > median_abs.fillna(0.0)
        df["tick_absorption_blocks"] = _consecutive_blocks(absorbing)
    else:
        df["tick_absorption_blocks"] = 0

    if "tick_momentum_exhaustion" in df.columns:
        # 枯竭：枯竭分高于滚动中位数
        median_ex = df["tick_momentum_exhaustion"].rolling(
            window_5m, min_periods=max(window_5m // 2, 5)
        ).median()
        exhausting = df["tick_momentum_exhaustion"] > median_ex.fillna(0.0)
        df["tick_exhaustion_blocks"] = _consecutive_blocks(exhausting)
    else:
        df["tick_exhaustion_blocks"] = 0

    if "tick_direction_net" in df.columns:
        # 方向持续：方向净值持续为正（做多）或持续为负（做空）
        dir_pos = df["tick_direction_net"] > 0.1
        dir_neg = df["tick_direction_net"] < -0.1
        df["tick_direction_persist_long"] = _consecutive_blocks(dir_pos).astype("int32")
        df["tick_direction_persist_short"] = _consecutive_blocks(dir_neg).astype("int32")
    else:
        df["tick_direction_persist_long"] = 0
        df["tick_direction_persist_short"] = 0

    return df


# ── 盘口特征（可选） ──────────────────────────────────────────────────────────

def compute_tick_book_features(bars: pd.DataFrame) -> pd.DataFrame:
    """
    计算盘口压力特征。

    需要 bars 中已包含 bt_bid_price, bt_ask_price, bt_bid_qty, bt_ask_qty 列
    （由 TickFeatureEngine 从 book_ticker 数据聚合合并而来）。
    如果这些列不存在，输出列全为 NaN，不报错。

    Returns:
        追加了 tick_bid_ask_imbalance, tick_spread_pct,
        tick_spread_compression, tick_imbalance_change 的 DataFrame。
    """
    df = bars
    has_book = all(c in df.columns for c in ("bt_bid_qty", "bt_ask_qty"))
    has_prices = all(c in df.columns for c in ("bt_bid_price", "bt_ask_price"))

    if has_book:
        total_qty = df["bt_bid_qty"] + df["bt_ask_qty"]
        total_qty = total_qty.where(total_qty > _EPS, np.nan)
        df["tick_bid_ask_imbalance"] = (
            (df["bt_bid_qty"] - df["bt_ask_qty"]) / total_qty
        ).astype("float32")
        df["tick_imbalance_change"] = df["tick_bid_ask_imbalance"].diff().astype("float32")
    else:
        df["tick_bid_ask_imbalance"] = np.nan
        df["tick_imbalance_change"] = np.nan

    if has_prices:
        mid_price = (df["bt_bid_price"] + df["bt_ask_price"]) / 2.0
        mid_price = mid_price.where(mid_price > _EPS, np.nan)
        spread = df["bt_ask_price"] - df["bt_bid_price"]
        df["tick_spread_pct"] = (spread / mid_price * 100.0).astype("float32")

        # 价差压缩：当前价差 / 过去 20 窗口均值价差
        spread_ma20 = df["tick_spread_pct"].rolling(20, min_periods=5).mean()
        df["tick_spread_compression"] = (
            df["tick_spread_pct"] / spread_ma20.replace(0.0, np.nan)
        ).astype("float32")
    else:
        df["tick_spread_pct"] = np.nan
        df["tick_spread_compression"] = np.nan

    return df


# ── 前向收益（供 IC 扫描和 AtomMiner 使用） ───────────────────────────────────

def compute_tick_forward_returns(
    bars: pd.DataFrame,
    horizons: tuple[int, ...] = (2, 3, 5, 8, 12),
) -> pd.DataFrame:
    """
    计算前向收益列：fwd_ret_N, fwd_max_ret_N, fwd_min_ret_N。

    与 alpha/scanner.py 的 add_forward_returns() 完全一致的逻辑，
    供 AtomMiner / WalkForwardValidator 直接使用。

    Args:
        bars:     包含 close 列的 bar DataFrame。
        horizons: 前向 bar 数量元组，默认 (2,3,5,8,12)。
    Returns:
        追加了 fwd_ret_N / fwd_max_ret_N / fwd_min_ret_N 的 DataFrame。
    """
    df = bars
    close_s = df["close"].astype("float64")
    close = close_s.values
    n = len(close)

    for h in horizons:
        # 前向收益（O(N) shift）
        df[f"fwd_ret_{h}"] = (close_s.shift(-h) / close_s - 1.0).astype("float32")

        # MFE/MAE：使用 reverse-rolling trick，与 alpha/scanner.py 完全一致，O(N)
        rev = close[::-1].copy()
        rev_s = pd.Series(rev)
        fwd_max_fwd = rev_s.rolling(h, min_periods=1).max().values[::-1].copy()
        fwd_min_fwd = rev_s.rolling(h, min_periods=1).min().values[::-1].copy()

        max_ret = np.full(n, np.nan, dtype="float32")
        min_ret = np.full(n, np.nan, dtype="float32")
        if n > 1:
            # exclude 当前 bar：future = close[i+1..i+h]，shift by 1
            max_ret[:-1] = ((fwd_max_fwd[1:] / close[:-1]) - 1.0).astype("float32")
            min_ret[:-1] = ((fwd_min_fwd[1:] / close[:-1]) - 1.0).astype("float32")
        # 末尾 h 行对齐 fwd_ret 设为 NaN
        max_ret[max(0, n - h):] = np.nan
        min_ret[max(0, n - h):] = np.nan

        df[f"fwd_max_ret_{h}"] = max_ret
        df[f"fwd_min_ret_{h}"] = min_ret

    return df
