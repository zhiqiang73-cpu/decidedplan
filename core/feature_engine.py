# -*- coding: utf-8 -*-
"""Feature engine for loading parquet data and computing all feature dimensions."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from core.dimensions.time_features import compute_time_features
from core.dimensions.price_features import compute_price_features
from core.dimensions.trade_flow_features import compute_trade_flow_features
from core.dimensions.liquidity_features import compute_liquidity_features
from core.dimensions.positioning_features import compute_positioning_features
from core.dimensions.cross_market_features import compute_cross_market_features
from core.dimensions.liquidation_features import compute_liquidation_features
from core.dimensions.microstructure_features import compute_microstructure_features
from core.dimensions.order_flow_features import compute_order_flow_features
from core.dimensions.mark_price_features import compute_mark_price_features

logger = logging.getLogger(__name__)


class FeatureEngine:
    """Load parquet-backed endpoint data and compute the full feature set."""

    MERGE_TOLERANCE_MS = {
        "funding_rate": 8 * 60 * 60 * 1000,
        "open_interest": 5 * 60 * 1000,
        "long_short_ratio": 5 * 60 * 1000,
        "taker_ratio": 5 * 60 * 1000,
    }

    def __init__(self, storage_path: str = "data/storage"):
        self.storage_path = Path(storage_path)

    def load_date_range(
        self,
        start_date: str,
        end_date: str,
        eth_df: Optional[pd.DataFrame] = None,
        side_endpoints: Optional[list[str]] = None,
        include_heavy: bool = True,
        feature_dims: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Load endpoint data for a date range and compute all features."""
        start_ts, end_ts = self._parse_date_range(start_date, end_date)
        logger.info("Loading data: %s ~ %s", start_date, end_date)

        df = self._load_parquet_range("klines", start_ts, end_ts)
        if df.empty:
            logger.warning("klines data empty, skip feature computation")
            return df

        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.info(
            "  klines: %s rows (%s ~ %s)",
            f"{len(df):,}",
            df["timestamp"].iloc[0],
            df["timestamp"].iloc[-1],
        )

        if side_endpoints is None:
            side_endpoints = ["funding_rate", "open_interest", "long_short_ratio", "taker_ratio"]
        for endpoint in side_endpoints:
            df = self._merge_side_data(df, endpoint, start_ts, end_ts)

        if "taker_buy_sell_ratio" in df.columns:
            df = df.rename(columns={"taker_buy_sell_ratio": "taker_ratio_api"})

        # -- 新维度数据源聚合合并
        if include_heavy:
            df = self._merge_liquidation_data(df, start_ts, end_ts)
            df = self._merge_book_ticker_data(df, start_ts, end_ts)
            df = self._merge_agg_trades_data(df, start_ts, end_ts)
            df = self._merge_mark_price_data(df, start_ts, end_ts)
        else:
            logger.info("Skipping heavy merges: liquidations, book_ticker, agg_trades, mark_price")

        if feature_dims is None:
            feature_dims = [
                "TIME",
                "PRICE",
                "TRADE_FLOW",
                "LIQUIDITY",
                "POSITIONING",
                "CROSS_MARKET",
                "LIQUIDATION",
                "MICROSTRUCTURE",
                "ORDER_FLOW",
                "MARK_PRICE",
            ]
        dims = {d.upper() for d in feature_dims}

        logger.info("Computing feature dimensions: %s", ", ".join(sorted(dims)))
        if "TIME" in dims:
            df = compute_time_features(df)
        if "PRICE" in dims:
            df = compute_price_features(df)
        if "TRADE_FLOW" in dims:
            df = compute_trade_flow_features(df)
        if "LIQUIDITY" in dims:
            df = compute_liquidity_features(df)
        # MARK_PRICE 必须先于 POSITIONING 执行：
        # compute_positioning_features 的 funding_rate_trend/consecutive_extreme_funding
        # 依赖实时资金费率；compute_mark_price_features 将 mp_funding_rate 写入 df，
        # 使 POSITIONING 能使用 WebSocket 实时值而非 REST 8h 粗数据。
        if "MARK_PRICE" in dims:
            df = compute_mark_price_features(df)
        if "POSITIONING" in dims:
            df = compute_positioning_features(df)
        if "CROSS_MARKET" in dims:
            df = compute_cross_market_features(df, eth_df=eth_df)
        if "LIQUIDATION" in dims:
            df = compute_liquidation_features(df)
        if "MICROSTRUCTURE" in dims:
            df = compute_microstructure_features(df)
        if "ORDER_FLOW" in dims:
            df = compute_order_flow_features(df)

        # -- 持续性状态特征（P 系列核心能力，Alpha 管道必需） --
        df = _add_state_block_features(df)

        logger.info("Feature computation complete: %s cols, %s rows", len(df.columns), f"{len(df):,}")
        return df

    @staticmethod
    def _parse_date_range(start_date: str, end_date: str) -> tuple[int, int]:
        def to_ms(date_str: str, end_of_day: bool = False) -> int:
            dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp() * 1000)

        return to_ms(start_date), to_ms(end_date, end_of_day=True)

    @staticmethod
    def _normalize_timestamp(df: pd.DataFrame) -> pd.DataFrame:
        if "timestamp" in df.columns:
            df["timestamp"] = df["timestamp"].astype("int64")
        return df

    def _load_parquet_range(self, endpoint: str, start_ts: int, end_ts: int) -> pd.DataFrame:
        endpoint_path = self.storage_path / endpoint
        if not endpoint_path.exists():
            logger.debug("  %s: directory missing, skip", endpoint)
            return pd.DataFrame()

        try:
            dataset = ds.dataset(endpoint_path, format="parquet", partitioning="hive")
            table = dataset.to_table(
                filter=(ds.field("timestamp") >= start_ts) & (ds.field("timestamp") <= end_ts)
            )
            df = table.to_pandas()
            if df.empty:
                logger.debug("  %s: no rows in range", endpoint)
            return self._normalize_timestamp(df)
        except Exception as exc:
            logger.debug("  %s: dataset filter failed (%s), fallback to file scan", endpoint, exc)
            return self._load_parquet_files(endpoint, start_ts, end_ts)

    def _load_parquet_files(self, endpoint: str, start_ts: int, end_ts: int) -> pd.DataFrame:
        import pyarrow.parquet as pq

        endpoint_path = self.storage_path / endpoint
        if not endpoint_path.exists():
            return pd.DataFrame()

        parquet_files = sorted(endpoint_path.rglob("*.parquet"))
        if not parquet_files:
            return pd.DataFrame()

        frames = []
        for fp in parquet_files:
            try:
                df_part = pq.read_table(fp).to_pandas()
                if "timestamp" in df_part.columns:
                    df_part = df_part[
                        (df_part["timestamp"] >= start_ts) & (df_part["timestamp"] <= end_ts)
                    ]
                if not df_part.empty:
                    frames.append(df_part)
            except Exception as exc:
                logger.debug("  skip %s: %s", fp, exc)

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values("timestamp").reset_index(drop=True)
        return self._normalize_timestamp(result)

    def _merge_side_data(self, df: pd.DataFrame, endpoint: str, start_ts: int, end_ts: int) -> pd.DataFrame:
        side = self._load_parquet_range(endpoint, start_ts, end_ts)
        if side.empty:
            logger.debug("  %s: no data to merge", endpoint)
            return df

        side = side.sort_values("timestamp").reset_index(drop=True)
        merge_cols = [col for col in side.columns if col != "timestamp"]
        new_cols = [col for col in merge_cols if col not in df.columns]
        if not new_cols:
            return df

        tolerance = self.MERGE_TOLERANCE_MS.get(endpoint)
        merged = pd.merge_asof(
            df,
            side[["timestamp"] + new_cols],
            on="timestamp",
            tolerance=tolerance,
            direction="backward",
        )
        logger.info("  %s: merged %s rows, added cols %s", endpoint, f"{len(side):,}", new_cols)
        return merged
    # -- 清算数据聚合合并

    def _merge_liquidation_data(self, df, start_ts, end_ts):
        import pandas as pd
        raw = self._load_parquet_range_ts("liquidations", "event_time", start_ts, end_ts)
        if raw.empty:
            return df
        if "symbol" in raw.columns:
            raw = raw[raw["symbol"] == "BTCUSDT"].copy()
        if raw.empty:
            return df
        raw["liq_usd"] = raw["filled_qty"] * raw["avg_price"]
        raw["minute_ts"] = (raw["event_time"] // 60_000) * 60_000
        sell_liq = raw[raw["side"] == "SELL"].groupby("minute_ts")["liq_usd"].sum().rename("liq_sell_usd_1m")
        buy_liq = raw[raw["side"] == "BUY"].groupby("minute_ts")["liq_usd"].sum().rename("liq_buy_usd_1m")
        max_liq = raw.groupby("minute_ts")["liq_usd"].max().rename("liq_size_max_1m")
        agg = (pd.concat([sell_liq, buy_liq, max_liq], axis=1).reset_index()
               .rename(columns={"minute_ts": "timestamp"}).sort_values("timestamp").reset_index(drop=True))
        return pd.merge_asof(df, agg, on="timestamp", tolerance=60_000, direction="backward")

    # -- book_ticker 数据聚合合并

    def _merge_book_ticker_data(self, df, start_ts, end_ts):
        import pandas as pd, numpy as np
        raw = self._load_parquet_range_ts("book_ticker", "timestamp_ms", start_ts, end_ts)
        if raw.empty:
            return df
        raw = raw.copy()
        mid = (raw["ask_price"] + raw["bid_price"]) / 2.0
        raw["rel_spread"] = (raw["ask_price"] - raw["bid_price"]) / mid.replace(0.0, float("nan"))
        raw["minute_ts"] = (raw["timestamp_ms"] // 60_000) * 60_000
        agg = (raw.groupby("minute_ts")
               .agg(bk_bid_qty_mean=("bid_qty","mean"),bk_ask_qty_mean=("ask_qty","mean"),bk_spread_mean=("rel_spread","mean"))
               .reset_index().rename(columns={"minute_ts":"timestamp"}).sort_values("timestamp").reset_index(drop=True))
        return pd.merge_asof(df, agg, on="timestamp", tolerance=60_000, direction="backward")

    # -- agg_trades 数据聚合合并

    def _merge_agg_trades_data(self, df, start_ts, end_ts):
        import pandas as pd, numpy as np
        raw = self._load_parquet_range_ts("agg_trades", "timestamp", start_ts, end_ts)
        if raw.empty:
            return df
        raw = raw.copy()
        raw["timestamp"] = raw["timestamp"].astype("int64")
        has_tick_payload = "price" in raw.columns and raw["price"].notna().any()
        has_preag_payload = "at_large_buy_ratio" in raw.columns and raw["at_large_buy_ratio"].notna().any()
        if not has_tick_payload and not has_preag_payload:
            logger.warning(
                "  agg_trades: mixed schema dataset lost payload columns, retrying file-wise load"
            )
            raw = self._load_parquet_files_ts("agg_trades", "timestamp", start_ts, end_ts)
            if raw.empty:
                return df
            raw = raw.copy()
            raw["timestamp"] = raw["timestamp"].astype("int64")

        # 区分新旧格式：
        #   新格式 (ws_collector): 已预聚合1m bar，含 at_large_buy_ratio
        #   旧格式 (历史下载):     原始逐笔成交，含 price/quantity/is_buyer_maker
        # PyArrow 加载混合格式时会统一 schema，缺失列填 NaN，
        # 所以需要按列是否有效值来拆分，分别处理。
        has_preag_col = "at_large_buy_ratio" in raw.columns
        has_tick_col = "price" in raw.columns

        frames: list = []

        if has_preag_col:
            preag_rows = raw[raw["at_large_buy_ratio"].notna()].copy()
            if not preag_rows.empty:
                keep = ["timestamp"] + [c for c in [
                    "at_large_buy_ratio", "at_burst_index", "at_dir_net_1m",
                    "buy_usd_1m", "sell_usd_1m", "trade_count",
                ] if c in preag_rows.columns]
                frames.append(preag_rows[keep])
                logger.info("  agg_trades: %s pre-aggregated 1m rows", f"{len(preag_rows):,}")

        if has_tick_col:
            # 取预聚合列为 NaN 的行（即旧格式逐笔数据），或全是旧格式的情况
            if has_preag_col:
                tick_rows = raw[raw["at_large_buy_ratio"].isna() & raw["price"].notna()].copy()
            else:
                tick_rows = raw[raw["price"].notna()].copy()
            if not tick_rows.empty:
                agg_from_ticks = self._aggregate_raw_ticks(tick_rows)
                frames.append(agg_from_ticks)
                logger.info("  agg_trades: %s raw tick rows -> %s 1m bars",
                            f"{len(tick_rows):,}", f"{len(agg_from_ticks):,}")

        if not frames:
            logger.warning("  agg_trades: 无法解析数据格式，跳过合并")
            return df

        agg = (pd.concat(frames, ignore_index=True)
               .sort_values("timestamp")
               .drop_duplicates(subset="timestamp", keep="last")
               .reset_index(drop=True))
        return pd.merge_asof(df, agg, on="timestamp", tolerance=60_000, direction="backward")

    def _aggregate_raw_ticks(self, raw):
        """将原始逐笔成交数据聚合为1分钟 bar，计算完整微结构特征。"""
        import numpy as np, pandas as pd
        raw = raw.copy()
        raw["minute_ts"] = (raw["timestamp"] // 60_000) * 60_000
        raw["trade_usd"] = raw["price"] * raw["quantity"]
        # is_buyer_maker=False → 主动买方（taker buy）；=True → 主动卖方（taker sell）
        is_buy = ~raw["is_buyer_maker"].astype(bool)
        raw["buy_usd"]  = np.where(is_buy,  raw["trade_usd"], 0.0)
        raw["sell_usd"] = np.where(~is_buy, raw["trade_usd"], 0.0)
        raw["direction"] = np.where(raw["is_buyer_maker"], -1, 1)

        grp = raw.groupby("minute_ts")

        # 向量化聚合（快，无 apply）
        buy_usd_1m  = grp["buy_usd"].sum().rename("buy_usd_1m")
        sell_usd_1m = grp["sell_usd"].sum().rename("sell_usd_1m")
        trade_count = grp["trade_usd"].count().rename("trade_count")
        dn = (grp["direction"].sum() / grp["direction"].count()).rename("at_dir_net_1m")

        # 大单买方比 + 爆发指数需要 apply（含分位数/间隔计算）
        def _lbr(g):
            if len(g) < 2: return np.nan
            thr = g["trade_usd"].quantile(0.9)
            lg = g[g["trade_usd"] >= thr]
            return np.nan if lg.empty else float((lg["direction"] == 1).sum() / len(lg))

        def _bi(g):
            if len(g) < 3: return np.nan
            iv = g["timestamp"].sort_values().diff().dropna()
            mu = iv.mean()
            return np.nan if mu == 0 else float(iv.std() / mu)

        try:
            lb = grp.apply(_lbr, include_groups=False).rename("at_large_buy_ratio")
            bi = grp.apply(_bi,  include_groups=False).rename("at_burst_index")
        except TypeError:
            lb = grp[["trade_usd", "direction"]].apply(_lbr).rename("at_large_buy_ratio")
            bi = grp[["timestamp"]].apply(_bi).rename("at_burst_index")

        agg = pd.concat([lb, bi, dn, buy_usd_1m, sell_usd_1m, trade_count], axis=1)
        return (agg.reset_index()
                .rename(columns={"minute_ts": "timestamp"})
                .sort_values("timestamp")
                .reset_index(drop=True))

    # -- mark_price 数据聚合合并

    def _merge_mark_price_data(self, df, start_ts, end_ts):
        import pandas as pd
        raw = self._load_parquet_range_ts("mark_price", "timestamp_ms", start_ts, end_ts)
        if raw.empty:
            return df
        raw = raw.copy()
        raw["minute_ts"] = (raw["timestamp_ms"] // 60_000) * 60_000
        agg = (raw.groupby("minute_ts")
               .agg(
                   mp_funding_rate     =("funding_rate",      "last"),
                   mp_mark_price       =("mark_price",        "last"),
                   mp_index_price      =("index_price",       "last"),
                   mp_next_funding_time=("next_funding_time", "last"),
               )
               .reset_index()
               .rename(columns={"minute_ts": "timestamp"})
               .sort_values("timestamp")
               .reset_index(drop=True))
        logger.info("  mark_price: %s 1m samples", f"{len(agg):,}")
        return pd.merge_asof(df, agg, on="timestamp", tolerance=60_000, direction="backward")

    # -- 通用 parquet 加载 (支持任意时间戳列名)

    def _load_parquet_range_ts(self, endpoint, ts_col, start_ts, end_ts):
        # 使用 pyarrow filter pushdown 避免全量加载 OOM。
        # 原实现先 to_table() 全量读取再内存过滤，高频数据（book_ticker/agg_trades）
        # 在 365 天区间可能超过数千万行触发 OOM。
        import pandas as pd
        import pyarrow.dataset as ds
        import pyarrow.compute as pc
        ep = self.storage_path / endpoint
        if not ep.exists():
            return pd.DataFrame()
        try:
            filt = (pc.field(ts_col) >= start_ts) & (pc.field(ts_col) <= end_ts)
            raw_table = ds.dataset(ep, format="parquet", partitioning="hive").to_table(filter=filt)
            if raw_table.num_rows == 0:
                return pd.DataFrame()
            raw = raw_table.to_pandas()
            raw[ts_col] = raw[ts_col].astype("int64")
            return raw.reset_index(drop=True)
        except Exception:
            return self._load_parquet_files_ts(endpoint, ts_col, start_ts, end_ts)

    def _load_parquet_files_ts(self, endpoint, ts_col, start_ts, end_ts):
        import pyarrow.parquet as pq, pandas as pd
        ep = self.storage_path / endpoint
        if not ep.exists(): return pd.DataFrame()
        files = sorted(ep.rglob("*.parquet"))
        if not files: return pd.DataFrame()
        frames = []
        for fp in files:
            try:
                p = pq.read_table(fp).to_pandas()
                if ts_col in p.columns:
                    p[ts_col] = p[ts_col].astype("int64")
                    p = p[(p[ts_col]>=start_ts)&(p[ts_col]<=end_ts)]
                if not p.empty: frames.append(p)
            except Exception: pass
        return pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()


def _add_state_block_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算持续性状态特征: 连续枯竭/压缩 block 计数。

    P 系列策略的核心能力 -- 检测"力持续存在了 N 个 block"。
    Alpha 管道用这些特征作为种子或确认因子。
    """
    required = {"volume", "high", "low"}
    if not required.issubset(df.columns) or len(df) < 60:
        for col in ("vol_drought_blocks_5m", "vol_drought_blocks_10m",
                     "price_compression_blocks_5m", "price_compression_blocks_10m"):
            df[col] = 0
        return df

    vol = df["volume"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)

    for tf_min in (5, 10):
        suffix = f"_{tf_min}m"
        n = tf_min
        n_blocks = len(df) // n
        if n_blocks < 25:
            df[f"vol_drought_blocks{suffix}"] = 0
            df[f"price_compression_blocks{suffix}"] = 0
            continue

        # 聚合到 tf_min 粒度（向量化，比 list comprehension 快 10-50x）
        trimmed = n_blocks * n
        blk_vol = vol[:trimmed].reshape(-1, n).sum(axis=1)
        blk_rng = high[:trimmed].reshape(-1, n).max(axis=1) - low[:trimmed].reshape(-1, n).min(axis=1)

        # 20-block 滚动基准
        drought_flag = np.zeros(n_blocks, dtype=bool)
        compress_flag = np.zeros(n_blocks, dtype=bool)
        for i in range(20, n_blocks):
            w = blk_vol[i-20:i]
            drought_flag[i] = blk_vol[i] < w.mean() * 0.5
            r = blk_rng[i-20:i]
            compress_flag[i] = blk_rng[i] < np.median(r)

        # 向量化计算连续 True 的 run length
        def _run_lengths(flags):
            counts = np.zeros(len(flags), dtype=int)
            for i in range(len(flags)):
                if flags[i]:
                    counts[i] = counts[i-1] + 1 if i > 0 else 1
                else:
                    counts[i] = 0
            return counts

        drought_runs = _run_lengths(drought_flag)
        compress_runs = _run_lengths(compress_flag)

        # 展开回 1 分钟粒度 (forward-fill: 每个 block 内的值相同)
        d_1m = np.zeros(len(df), dtype=int)
        c_1m = np.zeros(len(df), dtype=int)
        for i in range(n_blocks):
            s, e = i * n, min((i + 1) * n, len(df))
            d_1m[s:e] = drought_runs[i]
            c_1m[s:e] = compress_runs[i]
        # 尾部不完整 block: 用最后一个完整 block 的值填充
        tail_start = n_blocks * n
        if tail_start < len(df) and n_blocks > 0:
            d_1m[tail_start:] = drought_runs[n_blocks - 1]
            c_1m[tail_start:] = compress_runs[n_blocks - 1]

        df[f"vol_drought_blocks{suffix}"] = d_1m
        df[f"price_compression_blocks{suffix}"] = c_1m

    return df
