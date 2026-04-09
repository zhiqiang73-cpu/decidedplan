"""
WebSocket 实时数据采集启动脚本

采集内容:
  - !forceOrder@arr         全市场爆仓清算流
  - btcusdt@bookTicker      最优买卖挂单价 / Spread (采样: 每秒1条)
  - btcusdt@aggTrade        逐笔成交 (实时聚合成1分钟bar)
  - btcusdt@markPrice@1s    标记价格 + 实时资金费率 (每分钟采样)

数据存储:
  data/storage/liquidations/ ...
  data/storage/book_ticker/  ...
  data/storage/agg_trades/   ...  (1m bar)
  data/storage/mark_price/   ...  (1m采样)

用法:
    # 采集全部流（推荐）
    python run_ws.py

    # 只采集部分流
    python run_ws.py --streams liquidations book_ticker

    # 加大 BookTicker 采样间隔 (降低存储量)
    python run_ws.py --book-ticker-sample 5

停止: Ctrl-C  (已积攒的缓冲数据会在退出时自动写盘)
"""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

from data.downloader.ws_collector import (
    BinanceWSCollector,
    LIQUIDATION_STREAM,
    BOOK_TICKER_STREAM,
    AGG_TRADE_STREAM,
    MARK_PRICE_STREAM,
)


def setup_logging(level: str = "INFO") -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/ws_collector.log", encoding="utf-8"),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Binance Futures WebSocket collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--streams",
        nargs="+",
        choices=["liquidations", "book_ticker", "agg_trades", "mark_price"],
        default=["liquidations", "book_ticker", "agg_trades", "mark_price"],
        help="Streams to collect",
    )
    parser.add_argument(
        "--storage-path",
        default="data/storage",
        help="Parquet storage root",
    )
    parser.add_argument(
        "--flush-interval",
        type=int,
        default=60,
        help="Flush interval in seconds",
    )
    parser.add_argument(
        "--book-ticker-sample",
        type=float,
        default=1.0,
        help="Minimum book ticker sample interval in seconds",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("run_ws")

    stream_map = {
        "liquidations": LIQUIDATION_STREAM,
        "book_ticker": BOOK_TICKER_STREAM,
        "agg_trades": AGG_TRADE_STREAM,
        "mark_price": MARK_PRICE_STREAM,
    }
    selected_streams = [stream_map[name] for name in args.streams]

    logger.info("=" * 60)
    logger.info("Binance Futures WebSocket collector")
    logger.info("  streams: %s", args.streams)
    logger.info("  storage: %s", args.storage_path)
    logger.info("  flush interval: %ss", args.flush_interval)
    logger.info("  book ticker sample: %ss", args.book_ticker_sample)
    logger.info("Press Ctrl-C to stop safely; buffered data will be flushed on exit.")
    logger.info("=" * 60)

    collector = BinanceWSCollector(
        storage_path=args.storage_path,
        streams=selected_streams,
        flush_interval_s=args.flush_interval,
        book_ticker_sample_s=args.book_ticker_sample,
    )

    # 娉ㄥ唽 SIGINT / SIGTERM 淇″彿
    loop = asyncio.get_running_loop()

    def _shutdown(sig_name: str) -> None:
        logger.info(f"收到 {sig_name}，正在退出..")
        collector.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            # Windows 涓嶆敮鎸?add_signal_handler锛屼緷璧?KeyboardInterrupt
            pass

    try:
        await collector.run()
    except KeyboardInterrupt:
        logger.info("收到 KeyboardInterrupt，正在退出..")
        collector.stop()


if __name__ == "__main__":
    asyncio.run(main())



