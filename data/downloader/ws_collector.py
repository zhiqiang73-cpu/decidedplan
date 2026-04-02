"""
币安期货 WebSocket 实时数据采集器

采集流:
  - !forceOrder@arr         全市场爆仓清算流
  - btcusdt@bookTicker      最优挂单/Spread (采样: 每秒最多1条)
  - btcusdt@aggTrade        逐笔成交 (实时聚合为1分钟bar后落盘)
  - btcusdt@markPrice@1s    标记价格 + 实时资金费率 (每分钟采样一次)

存储路径 (Parquet, 按日期分区):
  data/storage/liquidations/year=YYYY/month=MM/day=DD/YYYYMMDD.parquet
  data/storage/book_ticker/year=YYYY/month=MM/day=DD/YYYYMMDD.parquet
  data/storage/agg_trades/year=YYYY/month=MM/day=DD/YYYYMMDD.parquet   (1m bar)
  data/storage/mark_price/year=YYYY/month=MM/day=DD/YYYYMMDD.parquet   (1m采样)

使用方法:
  from data.downloader.ws_collector import BinanceWSCollector
  collector = BinanceWSCollector(storage_path="data/storage")
  asyncio.run(collector.run())
"""

import asyncio
import aiohttp
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────────────────────────
WS_BASE_URL = "wss://fstream.binance.com/stream"

LIQUIDATION_STREAM = "!forceOrder@arr"
BOOK_TICKER_STREAM = "btcusdt@bookTicker"
AGG_TRADE_STREAM   = "btcusdt@aggTrade"
MARK_PRICE_STREAM  = "btcusdt@markPrice@1s"

BOOK_TICKER_SAMPLE_S  = 1.0   # bookTicker 最小采样间隔 (秒)
MARK_PRICE_SAMPLE_S   = 60.0  # markPrice 采样间隔 (秒, 每分钟一条)
FLUSH_INTERVAL_S      = 300   # 缓冲区刷新到磁盘的间隔 (秒, 5分钟)
MAX_BUFFER_RECORDS    = 5_000  # 超过此数量时立即刷新
RECONNECT_DELAY_S     = 5     # 断连后初始重连延迟
MAX_RECONNECT_S       = 120   # 最大重连延迟 (指数退避上限)
PING_INTERVAL_S       = 180   # 发送 ping 的间隔 (Binance 要求 < 10min)

# ───────────────────────────────────────────────────────────────────────────────
# Arrow Schema
# ───────────────────────────────────────────────────────────────────────────────
LIQUIDATION_SCHEMA = pa.schema([
    pa.field("event_time",   pa.int64()),    # 事件时间 ms
    pa.field("trade_time",   pa.int64()),    # 成交时间 ms
    pa.field("symbol",       pa.string()),   # 交易对
    pa.field("side",         pa.string()),   # BUY/SELL
    pa.field("order_type",   pa.string()),   # 订单类型
    pa.field("time_in_force",pa.string()),   # 有效期
    pa.field("quantity",     pa.float64()),  # 原始数量
    pa.field("price",        pa.float64()),  # 委托价格
    pa.field("avg_price",    pa.float64()),  # 平均成交价
    pa.field("status",       pa.string()),   # 订单状态
    pa.field("filled_qty",   pa.float64()),  # 最新成交量
    pa.field("acc_qty",      pa.float64()),  # 累计成交量
])

BOOK_TICKER_SCHEMA = pa.schema([
    pa.field("timestamp_ms", pa.int64()),    # 本地接收时间 ms
    pa.field("symbol",       pa.string()),   # 交易对
    pa.field("bid_price",    pa.float64()),  # 最优买价
    pa.field("bid_qty",      pa.float64()),  # 最优买量
    pa.field("ask_price",    pa.float64()),  # 最优卖价
    pa.field("ask_qty",      pa.float64()),  # 最优卖量
])

# aggTrade 聚合为1分钟bar的 schema
AGG_TRADE_1M_SCHEMA = pa.schema([
    pa.field("timestamp",          pa.int64()),    # 分钟起始 ms
    pa.field("at_large_buy_ratio", pa.float64()),  # 大单(>p90 USD)中买方主动占比
    pa.field("at_burst_index",     pa.float64()),  # 成交时间间隔变异系数
    pa.field("at_dir_net_1m",      pa.float64()),  # 方向净值 (买-卖)/总
    pa.field("trade_count",        pa.int64()),    # 成交笔数
    pa.field("buy_usd_1m",         pa.float64()),  # 主动买入 USD 量
    pa.field("sell_usd_1m",        pa.float64()),  # 主动卖出 USD 量
])

# markPrice 1分钟采样 schema
MARK_PRICE_SCHEMA = pa.schema([
    pa.field("timestamp_ms",      pa.int64()),    # 本地接收时间 ms
    pa.field("mark_price",        pa.float64()),  # 标记价格
    pa.field("index_price",       pa.float64()),  # 指数价格
    pa.field("funding_rate",      pa.float64()),  # 当前计息周期资金费率 (实时)
    pa.field("next_funding_time", pa.int64()),    # 下次结算时间戳 ms
])


# ───────────────────────────────────────────────────────────────────────────────
# 缓冲区 & 存储
# ───────────────────────────────────────────────────────────────────────────────
class StreamBuffer:
    """
    线程安全的内存缓冲区，定期 flush 到按日期分区的 Parquet 文件。
    同一天的数据追加写入同一个文件。
    """

    def __init__(
        self,
        stream_name: str,
        storage_path: Path,
        schema: pa.Schema,
        flush_interval_s: int = FLUSH_INTERVAL_S,
        max_records: int = MAX_BUFFER_RECORDS,
    ):
        self.stream_name    = stream_name
        self.storage_path   = storage_path
        self.schema         = schema
        self.flush_interval = flush_interval_s
        self.max_records    = max_records

        self._records: List[dict] = []
        self._lock       = asyncio.Lock()
        self._last_flush = time.monotonic()
        self._total_written = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def add(self, record: dict) -> None:
        async with self._lock:
            self._records.append(record)
            if len(self._records) >= self.max_records:
                await self._do_flush()

    async def maybe_flush(self) -> None:
        """由外部定时器周期调用，到期则刷新。"""
        if time.monotonic() - self._last_flush >= self.flush_interval:
            async with self._lock:
                if self._records:
                    await self._do_flush()

    async def flush_all(self) -> None:
        """强制刷新，退出前调用。"""
        async with self._lock:
            if self._records:
                await self._do_flush()

    @property
    def total_written(self) -> int:
        return self._total_written

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    async def _do_flush(self) -> None:
        """将缓冲区按日期分组写入 Parquet (持有锁时调用)。"""
        if not self._records:
            return

        records = self._records
        self._records = []
        self._last_flush = time.monotonic()

        # 按 UTC 日期分组
        groups: Dict[str, List[dict]] = defaultdict(list)
        ts_field = "event_time" if self.stream_name == "liquidations" else "timestamp_ms"

        for rec in records:
            ts_ms = rec.get(ts_field, 0)
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            day_key = dt.strftime("%Y%m%d")
            groups[day_key].append(rec)

        for day_key, day_records in groups.items():
            self._write_day(day_key, day_records)

        self._total_written += len(records)
        logger.info(
            f"[{self.stream_name}] flush {len(records):,} 条"
            f" → 累计 {self._total_written:,} 条"
        )

    def _get_file_path(self, day_key: str) -> Path:
        year  = day_key[:4]
        month = day_key[4:6]
        day   = day_key[6:8]
        folder = (
            self.storage_path
            / self.stream_name
            / f"year={year}"
            / f"month={month}"
            / f"day={day}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{day_key}.parquet"

    def _write_day(self, day_key: str, records: List[dict]) -> None:
        """追加写入单日 Parquet 文件。"""
        file_path = self._get_file_path(day_key)
        df = pd.DataFrame(records)

        # 确保列顺序和类型与 schema 一致
        for field in self.schema:
            if field.name not in df.columns:
                df[field.name] = None
            col = df[field.name]
            if pa.types.is_floating(field.type):
                df[field.name] = pd.to_numeric(col, errors="coerce")
            elif pa.types.is_integer(field.type):
                df[field.name] = pd.to_numeric(col, errors="coerce").astype("Int64")

        new_table = pa.Table.from_pandas(
            df[[f.name for f in self.schema]],
            schema=self.schema,
            preserve_index=False,
        )

        if file_path.exists():
            try:
                existing = pq.read_table(file_path, schema=self.schema)
                new_table = pa.concat_tables([existing, new_table])
            except Exception as exc:
                logger.warning(f"读取已有文件失败，将覆盖: {exc}")

        pq.write_table(
            new_table,
            file_path,
            compression="snappy",
        )


# ───────────────────────────────────────────────────────────────────────────────
# AggTrade 1分钟聚合缓冲区
# ───────────────────────────────────────────────────────────────────────────────
class AggTrade1mBuffer:
    """
    逐笔成交实时聚合为1分钟bar。

    aggTrade 频率极高 (每分钟数千条)，不落原始数据。
    每当分钟切换时，自动计算并缓存完整的1m bar。
    由外部定时器调用 maybe_flush() 写盘。
    """

    def __init__(self, storage_path: Path, flush_interval_s: int = FLUSH_INTERVAL_S):
        self._storage_path  = storage_path
        self._flush_interval = flush_interval_s
        self._current_minute: int = -1
        self._minute_ticks: List[dict] = []
        self._completed_bars: List[dict] = []
        self._last_flush     = time.monotonic()
        self.total_written   = 0

    def add_tick(self, record: dict) -> None:
        """接收一条原始 aggTrade，不加锁 (单线程 asyncio)。"""
        ts_ms     = int(record["timestamp"])
        minute_ts = (ts_ms // 60_000) * 60_000

        if self._current_minute == -1:
            self._current_minute = minute_ts

        if minute_ts != self._current_minute:
            if self._minute_ticks:
                bar = self._aggregate(self._current_minute, self._minute_ticks)
                if bar:
                    self._completed_bars.append(bar)
            self._minute_ticks = []
            self._current_minute = minute_ts

        self._minute_ticks.append(record)

    def _aggregate(self, minute_ts: int, ticks: List[dict]) -> Optional[dict]:
        if not ticks:
            return None

        prices    = [float(t["price"])    for t in ticks]
        qtys      = [float(t["quantity"]) for t in ticks]
        makers    = [bool(t["is_buyer_maker"]) for t in ticks]
        timestamps = [int(t["timestamp"]) for t in ticks]

        trade_usd  = [p * q for p, q in zip(prices, qtys)]
        directions = [-1 if m else 1 for m in makers]  # -1=taker卖, +1=taker买

        # 大单方向占比: p90 USD 阈值以上的成交中买方比例
        n = len(ticks)
        if n >= 2:
            sorted_usd = sorted(trade_usd)
            p90 = sorted_usd[int(n * 0.9)]
            large = [(d, u) for d, u in zip(directions, trade_usd) if u >= p90]
            at_large_buy_ratio = (
                float(sum(1 for d, _ in large if d == 1) / len(large))
                if large else float("nan")
            )
        else:
            at_large_buy_ratio = float("nan")

        # 成交爆发指数: 时间间隔变异系数 (CV = std/mean)
        if n >= 2:
            sorted_ts = sorted(timestamps)
            intervals = [sorted_ts[i + 1] - sorted_ts[i] for i in range(n - 1)]
            mu = sum(intervals) / len(intervals) if intervals else 0
            if mu > 0:
                variance = sum((x - mu) ** 2 for x in intervals) / len(intervals)
                at_burst_index = float(variance ** 0.5 / mu)
            else:
                at_burst_index = float("nan")
        else:
            at_burst_index = float("nan")

        # 方向净值
        at_dir_net_1m = float(sum(directions) / n) if n else float("nan")

        # 买卖 USD
        buy_usd  = sum(u for d, u in zip(directions, trade_usd) if d == 1)
        sell_usd = sum(u for d, u in zip(directions, trade_usd) if d == -1)

        return {
            "timestamp":          minute_ts,
            "at_large_buy_ratio": at_large_buy_ratio,
            "at_burst_index":     at_burst_index,
            "at_dir_net_1m":      at_dir_net_1m,
            "trade_count":        n,
            "buy_usd_1m":         float(buy_usd),
            "sell_usd_1m":        float(sell_usd),
        }

    async def maybe_flush(self) -> None:
        if time.monotonic() - self._last_flush >= self._flush_interval:
            if self._completed_bars:
                self._write_bars(list(self._completed_bars))
                self.total_written += len(self._completed_bars)
                logger.info(f"[agg_trades_1m] flush {len(self._completed_bars)} bars"
                            f" -> total {self.total_written}")
                self._completed_bars = []
                self._last_flush = time.monotonic()

    async def flush_all(self) -> None:
        # 刷入当前未完成分钟 (部分数据，关机前保留)
        if self._minute_ticks and self._current_minute >= 0:
            bar = self._aggregate(self._current_minute, self._minute_ticks)
            if bar:
                self._completed_bars.append(bar)
            self._minute_ticks = []
        if self._completed_bars:
            self._write_bars(list(self._completed_bars))
            self.total_written += len(self._completed_bars)
            self._completed_bars = []

    def _write_bars(self, bars: List[dict]) -> None:
        groups: Dict[str, List[dict]] = defaultdict(list)
        for bar in bars:
            dt      = datetime.fromtimestamp(bar["timestamp"] / 1000, tz=timezone.utc)
            day_key = dt.strftime("%Y%m%d")
            groups[day_key].append(bar)
        for day_key, day_bars in groups.items():
            self._write_day(day_key, day_bars)

    def _write_day(self, day_key: str, bars: List[dict]) -> None:
        year = day_key[:4]; month = day_key[4:6]; day = day_key[6:8]
        folder = (self._storage_path / "agg_trades"
                  / f"year={year}" / f"month={month}" / f"day={day}")
        folder.mkdir(parents=True, exist_ok=True)
        file_path = folder / f"{day_key}.parquet"

        import numpy as np
        df = pd.DataFrame(bars)
        for col in ["at_large_buy_ratio", "at_burst_index", "at_dir_net_1m",
                    "buy_usd_1m", "sell_usd_1m"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["trade_count"] = pd.to_numeric(df["trade_count"], errors="coerce").astype("Int64")
        df["timestamp"]   = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")

        new_table = pa.Table.from_pandas(
            df[[f.name for f in AGG_TRADE_1M_SCHEMA]],
            schema=AGG_TRADE_1M_SCHEMA, preserve_index=False,
        )
        if file_path.exists():
            try:
                existing  = pq.read_table(file_path, schema=AGG_TRADE_1M_SCHEMA)
                merged_df = (pa.concat_tables([existing, new_table]).to_pandas()
                             .drop_duplicates("timestamp").sort_values("timestamp"))
                new_table = pa.Table.from_pandas(
                    merged_df[[f.name for f in AGG_TRADE_1M_SCHEMA]],
                    schema=AGG_TRADE_1M_SCHEMA, preserve_index=False,
                )
            except Exception as exc:
                logger.warning(f"[agg_trades_1m] 读取已有文件失败，将覆盖: {exc}")
        pq.write_table(new_table, file_path, compression="snappy")


# ───────────────────────────────────────────────────────────────────────────────
# 消息解析
# ───────────────────────────────────────────────────────────────────────────────
def _parse_liquidation(msg: dict) -> Optional[dict]:
    """解析 forceOrder 消息。"""
    try:
        o = msg.get("o", {})
        return {
            "event_time":    int(msg.get("E", 0)),
            "trade_time":    int(o.get("T",  0)),
            "symbol":        str(o.get("s",  "")),
            "side":          str(o.get("S",  "")),
            "order_type":    str(o.get("o",  "")),
            "time_in_force": str(o.get("f",  "")),
            "quantity":      float(o.get("q", 0)),
            "price":         float(o.get("p", 0)),
            "avg_price":     float(o.get("ap", 0)),
            "status":        str(o.get("X",  "")),
            "filled_qty":    float(o.get("l", 0)),
            "acc_qty":       float(o.get("z", 0)),
        }
    except Exception as exc:
        logger.debug(f"解析 liquidation 失败: {exc} — {msg}")
        return None


def _parse_book_ticker(msg: dict, recv_ms: int) -> Optional[dict]:
    """解析 bookTicker 消息。"""
    try:
        return {
            "timestamp_ms": recv_ms,
            "symbol":       str(msg.get("s",  "")),
            "bid_price":    float(msg.get("b", 0)),
            "bid_qty":      float(msg.get("B", 0)),
            "ask_price":    float(msg.get("a", 0)),
            "ask_qty":      float(msg.get("A", 0)),
        }
    except Exception as exc:
        logger.debug(f"解析 bookTicker 失败: {exc} — {msg}")
        return None


def _parse_agg_trade(msg: dict) -> Optional[dict]:
    """解析 aggTrade 消息，返回原始tick (由 AggTrade1mBuffer 聚合)。"""
    try:
        return {
            "timestamp":      int(msg.get("T", 0)),   # 成交时间 ms
            "price":          float(msg.get("p", 0)),
            "quantity":       float(msg.get("q", 0)),
            "is_buyer_maker": bool(msg.get("m", False)),
        }
    except Exception as exc:
        logger.debug(f"解析 aggTrade 失败: {exc} — {msg}")
        return None


def _parse_mark_price(msg: dict, recv_ms: int) -> Optional[dict]:
    """解析 markPrice 消息。"""
    try:
        return {
            "timestamp_ms":      recv_ms,
            "mark_price":        float(msg.get("p", 0)),
            "index_price":       float(msg.get("i", 0)),
            "funding_rate":      float(msg.get("r", 0)),
            "next_funding_time": int(msg.get("T", 0)),
        }
    except Exception as exc:
        logger.debug(f"解析 markPrice 失败: {exc} — {msg}")
        return None


# ───────────────────────────────────────────────────────────────────────────────
# 主采集器
# ───────────────────────────────────────────────────────────────────────────────
class BinanceWSCollector:
    """
    币安期货 WebSocket 实时采集器。

    用法:
        collector = BinanceWSCollector(storage_path="data/storage")
        asyncio.run(collector.run())
    """

    def __init__(
        self,
        storage_path: str = "data/storage",
        streams: Optional[List[str]] = None,
        flush_interval_s: int = FLUSH_INTERVAL_S,
        book_ticker_sample_s: float = BOOK_TICKER_SAMPLE_S,
        mark_price_sample_s: float = MARK_PRICE_SAMPLE_S,
    ):
        self.storage_path = Path(storage_path)
        self.streams = streams or [
            LIQUIDATION_STREAM,
            BOOK_TICKER_STREAM,
            AGG_TRADE_STREAM,
            MARK_PRICE_STREAM,
        ]
        self.flush_interval_s     = flush_interval_s
        self.book_ticker_sample_s = book_ticker_sample_s
        self.mark_price_sample_s  = mark_price_sample_s

        self._buffers: Dict[str, StreamBuffer] = {
            "liquidations": StreamBuffer(
                "liquidations", self.storage_path,
                LIQUIDATION_SCHEMA, flush_interval_s
            ),
            "book_ticker": StreamBuffer(
                "book_ticker", self.storage_path,
                BOOK_TICKER_SCHEMA, flush_interval_s
            ),
            "mark_price": StreamBuffer(
                "mark_price", self.storage_path,
                MARK_PRICE_SCHEMA, flush_interval_s
            ),
        }

        # aggTrade 使用专用1m聚合缓冲区
        self._agg_trade_buf = AggTrade1mBuffer(self.storage_path, flush_interval_s)

        self._running   = False
        self._last_book_ticker_ts: float = 0.0
        self._last_mark_price_ts:  float = 0.0

        # 统计
        self._msg_count: Dict[str, int] = defaultdict(int)
        self._connect_count = 0
        self._start_time: Optional[float] = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """主入口：连接 → 采集 → 断连重试。Ctrl-C 优雅退出。"""
        self._running    = True
        self._start_time = time.monotonic()
        delay = RECONNECT_DELAY_S

        logger.info("WebSocket 采集器启动")
        logger.info(f"  存储路径: {self.storage_path.resolve()}")
        logger.info(f"  订阅流: {self.streams}")

        # 启动定时 flush 任务
        flush_task = asyncio.create_task(self._flush_loop())

        try:
            while self._running:
                try:
                    await self._connect_and_collect()
                    delay = RECONNECT_DELAY_S   # 正常断开，重置延迟
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(f"连接异常: {exc}")

                if not self._running:
                    break

                logger.info(f"将在 {delay}s 后重连 (第 {self._connect_count} 次)")
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_S)

        finally:
            flush_task.cancel()
            await asyncio.gather(flush_task, return_exceptions=True)
            # 退出前刷新所有缓冲区
            for buf in self._buffers.values():
                await buf.flush_all()
            await self._agg_trade_buf.flush_all()
            self._print_stats()
            logger.info("采集器已退出")

    def stop(self) -> None:
        """请求停止采集器。"""
        self._running = False

    # ── 连接 & 消息处理 ───────────────────────────────────────────────────────

    async def _connect_and_collect(self) -> None:
        """建立 WebSocket 连接并处理消息，直到断连。"""
        stream_path = "/".join(self.streams)
        url = f"{WS_BASE_URL}?streams={stream_path}"

        self._connect_count += 1
        logger.info(f"正在连接 (#{self._connect_count}): {url}")

        timeout = aiohttp.ClientTimeout(total=None, sock_read=PING_INTERVAL_S + 30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(url, heartbeat=PING_INTERVAL_S) as ws:
                logger.info("WebSocket 已连接")
                async for msg in ws:
                    if not self._running:
                        await ws.close()
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_text(msg.data)

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.warning(f"WebSocket 错误帧: {ws.exception()}")
                        break

                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        logger.info("WebSocket 连接关闭")
                        break

    async def _handle_text(self, raw: str) -> None:
        """解析并路由一条消息。"""
        recv_ms = int(time.time() * 1000)
        try:
            outer = json.loads(raw)
        except json.JSONDecodeError:
            return

        # 组合流格式: {"stream": "...", "data": {...}}
        stream = outer.get("stream", "")
        data   = outer.get("data", outer)

        event_type = data.get("e", "")

        # ── 清算爆仓 ──
        if event_type == "forceOrder":
            record = _parse_liquidation(data)
            if record:
                await self._buffers["liquidations"].add(record)
                self._msg_count["liquidations"] += 1

        # ── BookTicker (限速采样) ──
        elif event_type == "bookTicker" or "bookTicker" in stream or (
            "b" in data and "a" in data and "s" in data and "e" not in data
        ):
            now = time.monotonic()
            if now - self._last_book_ticker_ts >= self.book_ticker_sample_s:
                record = _parse_book_ticker(data, recv_ms)
                if record:
                    await self._buffers["book_ticker"].add(record)
                    self._msg_count["book_ticker"] += 1
                    self._last_book_ticker_ts = now

        # ── aggTrade (高频, 聚合为1m bar) ──
        elif event_type == "aggTrade":
            record = _parse_agg_trade(data)
            if record:
                self._agg_trade_buf.add_tick(record)   # 同步，无 await
                self._msg_count["agg_trades"] += 1

        # ── markPrice (限速采样, 每分钟一条) ──
        elif event_type == "markPriceUpdate":
            now = time.monotonic()
            if now - self._last_mark_price_ts >= self.mark_price_sample_s:
                record = _parse_mark_price(data, recv_ms)
                if record:
                    await self._buffers["mark_price"].add(record)
                    self._msg_count["mark_price"] += 1
                    self._last_mark_price_ts = now

    # ── 定时刷新 ──────────────────────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        """后台任务：每 60 秒检查一次是否需要 flush。"""
        try:
            while True:
                await asyncio.sleep(60)
                for buf in self._buffers.values():
                    await buf.maybe_flush()
                await self._agg_trade_buf.maybe_flush()
        except asyncio.CancelledError:
            pass

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def _print_stats(self) -> None:
        elapsed = time.monotonic() - (self._start_time or time.monotonic())
        print("\n" + "=" * 60)
        print("WebSocket 采集统计")
        print("=" * 60)
        print(f"运行时长:    {elapsed / 60:.1f} 分钟")
        print(f"重连次数:    {self._connect_count}")
        print(f"清算消息:    {self._msg_count['liquidations']:,} 条")
        print(f"BookTicker:  {self._msg_count['book_ticker']:,} 条 (采样后)")
        print(f"aggTrade:    {self._msg_count['agg_trades']:,} 条 -> {self._agg_trade_buf.total_written} 个1m bar")
        print(f"markPrice:   {self._msg_count['mark_price']:,} 条 (1分钟采样)")
        for name, buf in self._buffers.items():
            print(f"  {name} 已写盘: {buf.total_written:,} 条")
        print("=" * 60 + "\n")

    def get_stats(self) -> dict:
        """返回当前统计信息 (供外部查询)。"""
        elapsed = time.monotonic() - (self._start_time or time.monotonic())
        written = {name: buf.total_written for name, buf in self._buffers.items()}
        written["agg_trades_1m_bars"] = self._agg_trade_buf.total_written
        return {
            "elapsed_minutes": elapsed / 60,
            "reconnects":      self._connect_count,
            "msg_counts":      dict(self._msg_count),
            "written":         written,
        }
