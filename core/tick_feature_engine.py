"""
逐笔特征引擎 (TickFeatureEngine)

职责:
  1. 从 data/storage/agg_trades/ 加载原始逐笔成交数据
  2. 聚合为时间窗口 bar（10s / 30s / 60s）
  3. 调用 core/dimensions/tick_features.py 计算全部 tick_* 特征
  4. 可选：合并 data/storage/book_ticker/ 盘口数据
  5. 添加前向收益列（供 AtomMiner / IC 扫描使用）

用法:
  engine = TickFeatureEngine()
  df = engine.load_date_range("2026-01-01", "2026-04-01", window_seconds=10)
  # df 包含全部 tick_* 特征 + fwd_ret_N 列，可直接喂给 AtomMiner

数据来源:
  - agg_trades: 106 天 (2025-03-23 ~ 2026-04-11)
    schema: agg_trade_id, price, quantity, timestamp(ms), is_buyer_maker
  - book_ticker: 19 天 (2026-03-17 ~)
    schema: timestamp_ms, symbol, bid_price, bid_qty, ask_price, ask_qty
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.dimensions.tick_features import (
    compute_tick_flow_features,
    compute_tick_microstructure_features,
    compute_tick_composite_scores,
    compute_tick_block_state,
    compute_tick_book_features,
    compute_tick_forward_returns,
)

logger = logging.getLogger(__name__)

# 加载原始 tick 时只读这几列（agg_trade_id 等无用列跳过）
_AGG_TRADE_COLS = ("timestamp", "price", "quantity", "is_buyer_maker")
_BOOK_TICKER_COLS = ("timestamp_ms", "bid_price", "bid_qty", "ask_price", "ask_qty")


class TickFeatureEngine:
    """
    离线批量逐笔特征引擎。

    Args:
        storage_path:     Parquet 数据根目录（默认 data/storage）
        large_trade_pct:  大单阈值百分位（默认 90）
    """

    def __init__(
        self,
        storage_path: str = "data/storage",
        large_trade_pct: float = 90.0,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.large_trade_pct = float(large_trade_pct)

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def load_date_range(
        self,
        start_date: str,
        end_date: str,
        window_seconds: int = 10,
        add_book: bool = True,
        horizons: tuple[int, ...] = (2, 3, 5, 8, 12),
    ) -> pd.DataFrame:
        """
        加载日期范围内的逐笔数据，聚合并计算全部特征。

        Args:
            start_date:     起始日期 "YYYY-MM-DD"
            end_date:       结束日期 "YYYY-MM-DD"
            window_seconds: 时间窗口大小（10 / 30 / 60 秒）
            add_book:       是否合并盘口特征（如果 book_ticker 数据存在）
            horizons:       前向收益窗口数（bar 数量）
        Returns:
            包含全部 tick_* 特征 + fwd_ret_N 列的 DataFrame。
            按时间戳升序，index 重置为 0..N-1。
        """
        window_seconds = int(window_seconds)
        if window_seconds not in (10, 30, 60):
            raise ValueError(f"window_seconds 必须是 10/30/60，got {window_seconds}")

        logger.info(
            "[TickFeatureEngine] 加载 %s ~ %s, 窗口=%ds",
            start_date, end_date, window_seconds,
        )

        # Step 1: 加载原始 tick
        raw_ticks = self._load_raw_ticks(start_date, end_date)
        if raw_ticks.empty:
            logger.warning("[TickFeatureEngine] 没有找到 agg_trades 数据: %s ~ %s", start_date, end_date)
            return pd.DataFrame()

        logger.info("[TickFeatureEngine] 原始 tick: %s 条", f"{len(raw_ticks):,}")

        # Step 2: 聚合为时间窗口 bar
        bars = self._aggregate_ticks_to_bars(raw_ticks, window_seconds)
        if bars.empty:
            logger.warning("[TickFeatureEngine] 聚合后没有 bar")
            return pd.DataFrame()

        logger.info(
            "[TickFeatureEngine] %ds bars: %s 行 (%s ~ %s)",
            window_seconds,
            f"{len(bars):,}",
            pd.to_datetime(bars["timestamp"].iloc[0], unit="ms", utc=True).strftime("%Y-%m-%d"),
            pd.to_datetime(bars["timestamp"].iloc[-1], unit="ms", utc=True).strftime("%Y-%m-%d"),
        )

        # Step 3: 计算窗口参数
        window_5m = max(int(round(300 / window_seconds)), 2)
        window_15m = max(int(round(900 / window_seconds)), window_5m + 1)

        # Step 4: 计算特征（顺序固定，后面的依赖前面的）
        bars = compute_tick_flow_features(bars)
        bars = compute_tick_microstructure_features(bars, window_5m)
        bars = compute_tick_composite_scores(bars)
        bars = compute_tick_block_state(bars, window_5m)

        # Step 5: 可选盘口特征
        if add_book:
            bars = self._merge_book_ticker(bars, start_date, end_date, window_seconds)
        bars = compute_tick_book_features(bars)

        # Step 6: 前向收益（供 AtomMiner / IC 扫描）
        bars = compute_tick_forward_returns(bars, horizons=horizons)

        # 清理
        bars = bars.sort_values("timestamp").reset_index(drop=True)

        n_feat = sum(1 for c in bars.columns if c.startswith("tick_"))
        logger.info(
            "[TickFeatureEngine] 完成: %s 行, %d tick 特征, %d fwd 列",
            f"{len(bars):,}", n_feat, len(horizons) * 3,
        )
        return bars

    # ── 原始数据加载 ──────────────────────────────────────────────────────────

    def _load_raw_ticks(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        加载 agg_trades Parquet 文件（Hive 分区）。

        agg_trades schema:
          agg_trade_id  Int64
          price         float64
          quantity      float64
          first_trade_id / last_trade_id  Int64  (忽略)
          timestamp     Int64  (UTC 毫秒)
          is_buyer_maker bool  (True = 卖方主动成交)
        """
        base = self.storage_path / "agg_trades"
        if not base.exists():
            logger.warning("[TickFeatureEngine] agg_trades 目录不存在: %s", base)
            return pd.DataFrame()

        start_ts, end_ts = self._parse_date_range(start_date, end_date)

        skipped_agg = 0
        frames: list[pd.DataFrame] = []
        for parquet_file in sorted(base.rglob("*.parquet")):
            file_start, file_end = self._file_ts_range(parquet_file)
            if file_end < start_ts or file_start > end_ts:
                continue
            # 先读 schema，过滤掉已聚合的 1m bar 文件（缺少 price/quantity/is_buyer_maker）
            try:
                import pyarrow.parquet as pq
                pq_file = pq.ParquetFile(parquet_file)
                schema_names = pq_file.schema_arrow.names
                if not all(c in schema_names for c in _AGG_TRADE_COLS):
                    skipped_agg += 1
                    continue
            except Exception:
                pass  # schema 检测失败则继续尝试读取
            try:
                df = pd.read_parquet(parquet_file, columns=list(_AGG_TRADE_COLS))
            except Exception as exc:
                logger.warning("[TickFeatureEngine] 读取失败 %s: %s", parquet_file, exc)
                continue
            if df.empty:
                continue
            # 过滤时间范围
            df["timestamp"] = df["timestamp"].astype("int64")
            df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
            if not df.empty:
                frames.append(df)

        if skipped_agg > 0:
            logger.info(
                "[TickFeatureEngine] 跳过 %d 个已聚合文件（缺少 price/quantity/is_buyer_maker）",
                skipped_agg,
            )
        if not frames:
            return pd.DataFrame()

        ticks = pd.concat(frames, ignore_index=True)
        ticks = ticks.dropna(subset=["timestamp", "price", "quantity", "is_buyer_maker"])
        ticks["timestamp"] = ticks["timestamp"].astype("int64")
        ticks["price"] = ticks["price"].astype("float64")
        ticks["quantity"] = ticks["quantity"].astype("float64")
        ticks["is_buyer_maker"] = ticks["is_buyer_maker"].astype(bool)
        ticks = ticks.sort_values("timestamp").reset_index(drop=True)
        return ticks

    # ── 聚合为时间窗口 bar ────────────────────────────────────────────────────

    def _aggregate_ticks_to_bars(
        self,
        ticks: pd.DataFrame,
        window_seconds: int,
    ) -> pd.DataFrame:
        """
        将原始 tick 按固定时间窗口聚合为 OHLCV bar。

        复用 raw_tick_rhythm_research.py 的聚合逻辑，保持算法一致性。
        """
        bucket_ms = window_seconds * 1000
        df = ticks.copy()
        df["bucket"] = (df["timestamp"] // bucket_ms) * bucket_ms
        df["trade_usd"] = df["price"] * df["quantity"]
        df["is_buy"] = ~df["is_buyer_maker"]
        df["buy_usd"] = np.where(df["is_buy"], df["trade_usd"], 0.0)
        df["sell_usd"] = np.where(~df["is_buy"], df["trade_usd"], 0.0)
        df["direction"] = np.where(df["is_buy"], 1.0, -1.0)

        group = df.groupby("bucket", sort=True)

        bars = group.agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
            notional=("trade_usd", "sum"),
            buy_usd=("buy_usd", "sum"),
            sell_usd=("sell_usd", "sum"),
            trade_count=("trade_usd", "count"),
        )
        bars["direction_net"] = group["direction"].mean()
        bars["window_vwap"] = bars["notional"] / bars["volume"].replace(0.0, np.nan)

        # 大单买方占比（需要逐组计算）
        large_trade_pct = self.large_trade_pct

        def _large_buy_ratio(frame: pd.DataFrame) -> float:
            if len(frame) < 4:
                return math.nan
            threshold = float(frame["trade_usd"].quantile(large_trade_pct / 100.0))
            large = frame[frame["trade_usd"] >= threshold]
            if large.empty:
                return math.nan
            return float(large["is_buy"].mean())

        def _burst_index(frame: pd.DataFrame) -> float:
            if len(frame) < 3:
                return math.nan
            intervals = frame["timestamp"].sort_values().diff().dropna()
            if intervals.empty:
                return math.nan
            mean_interval = float(intervals.mean())
            if mean_interval <= 0:
                return math.nan
            return float(intervals.std() / mean_interval)

        try:
            bars["large_buy_ratio"] = group.apply(_large_buy_ratio, include_groups=False)
            bars["burst_index"] = group.apply(_burst_index, include_groups=False)
        except TypeError:
            # pandas < 2.2 不支持 include_groups
            bars["large_buy_ratio"] = group.apply(_large_buy_ratio)
            bars["burst_index"] = group.apply(_burst_index)

        bars = bars.reset_index().rename(columns={"bucket": "timestamp"})
        return bars

    # ── 盘口数据合并 ──────────────────────────────────────────────────────────

    def _merge_book_ticker(
        self,
        bars: pd.DataFrame,
        start_date: str,
        end_date: str,
        window_seconds: int,
    ) -> pd.DataFrame:
        """
        将 book_ticker 数据聚合到相同时间窗口，并合并到 bars。

        book_ticker 列 (timestamp_ms) 聚合后生成:
          bt_bid_price, bt_ask_price, bt_bid_qty, bt_ask_qty

        如果 book_ticker 数据不存在或日期不在范围内，直接返回原 bars（不报错）。
        """
        base = self.storage_path / "book_ticker"
        if not base.exists():
            return bars

        start_ts, end_ts = self._parse_date_range(start_date, end_date)
        bucket_ms = window_seconds * 1000

        frames: list[pd.DataFrame] = []
        for parquet_file in sorted(base.rglob("*.parquet")):
            file_start, file_end = self._file_ts_range(parquet_file, ts_col="timestamp_ms")
            if file_end < start_ts or file_start > end_ts:
                continue
            try:
                df = pd.read_parquet(parquet_file, columns=list(_BOOK_TICKER_COLS))
            except Exception as exc:
                logger.debug("[TickFeatureEngine] book_ticker 读取失败 %s: %s", parquet_file, exc)
                continue
            if df.empty:
                continue
            df = df.rename(columns={"timestamp_ms": "timestamp"})
            df["timestamp"] = df["timestamp"].astype("int64")
            df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
            if not df.empty:
                frames.append(df)

        if not frames:
            return bars  # 没有 book_ticker 数据，静默跳过

        book = pd.concat(frames, ignore_index=True)
        book = book.sort_values("timestamp").reset_index(drop=True)
        book["bucket"] = (book["timestamp"] // bucket_ms) * bucket_ms

        # 聚合：每个窗口取均值（盘口快照的平均）
        agg_book = book.groupby("bucket", sort=True).agg(
            bt_bid_price=("bid_price", "mean"),
            bt_ask_price=("ask_price", "mean"),
            bt_bid_qty=("bid_qty", "mean"),
            bt_ask_qty=("ask_qty", "mean"),
        ).reset_index().rename(columns={"bucket": "timestamp"})

        bars = bars.merge(agg_book, on="timestamp", how="left")
        # 统计 bars 里有多少窗口成功合并到盘口数据
        book_rows = bars["bt_bid_price"].notna().sum() if "bt_bid_price" in bars.columns else 0
        logger.info(
            "[TickFeatureEngine] book_ticker 合并: %d / %d 个窗口有盘口数据 (%.1f%%)",
            book_rows, len(bars), book_rows / max(len(bars), 1) * 100,
        )
        return bars

    # ── 工具函数 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_date_range(start_date: str, end_date: str) -> tuple[int, int]:
        """将 'YYYY-MM-DD' 字符串转为 UTC 毫秒时间戳范围。"""
        def to_ms(date_str: str, end_of_day: bool = False) -> int:
            dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp() * 1000)

        return to_ms(start_date), to_ms(end_date, end_of_day=True)

    @staticmethod
    def _file_ts_range(
        parquet_file: Path,
        ts_col: str = "timestamp",
    ) -> tuple[int, int]:
        """
        从 Hive 分区路径推断文件的时间范围（毫秒）。
        year=YYYY/month=MM/day=DD/ -> 当天 00:00:00 ~ 23:59:59 UTC
        如果路径解析失败，返回 (0, 2**53) 不过滤。
        """
        import re
        posix = parquet_file.as_posix()
        match = re.search(r"year=(\d{4}).*?month=(\d{2}).*?day=(\d{2})", posix)
        if not match:
            return 0, 2**53
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            day_start = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
            day_end = datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc)
            return int(day_start.timestamp() * 1000), int(day_end.timestamp() * 1000)
        except ValueError:
            return 0, 2**53
