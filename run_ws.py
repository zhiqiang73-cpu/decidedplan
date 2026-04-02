"""
WebSocket 瀹炴椂鏁版嵁閲囬泦鍚姩鑴氭湰

閲囬泦鍐呭:
  - !forceOrder@arr         鍏ㄥ競鍦虹垎浠撴竻绠楁祦
  - btcusdt@bookTicker      鏈€浼樹拱鍗栨寕鍗?/ Spread (閲囨牱: 姣忕1鏉?
  - btcusdt@aggTrade        閫愮瑪鎴愪氦 (瀹炴椂鑱氬悎涓?鍒嗛挓bar)
  - btcusdt@markPrice@1s    鏍囪浠锋牸 + 瀹炴椂璧勯噾璐圭巼 (姣忓垎閽熼噰鏍?

鏁版嵁瀛樺偍:
  data/storage/liquidations/ ...
  data/storage/book_ticker/  ...
  data/storage/agg_trades/   ...  (1m bar)
  data/storage/mark_price/   ...  (1m閲囨牱)

鐢ㄦ硶:
    # 閲囬泦鍏ㄩ儴娴?(鎺ㄨ崘)
    python run_ws.py

    # 鍙噰闆嗛儴鍒嗘祦
    python run_ws.py --streams liquidations book_ticker

    # 鍔犲ぇ BookTicker 閲囨牱闂撮殧 (闄嶄綆瀛樺偍閲?
    python run_ws.py --book-ticker-sample 5

鍋滄: Ctrl-C  (宸茬Н绱殑缂撳啿鏁版嵁浼氬湪閫€鍑烘椂鑷姩鍐欑洏)
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
        logger.info(f"鏀跺埌 {sig_name}锛屾鍦ㄩ€€鍑?..")
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
        logger.info("鏀跺埌 KeyboardInterrupt锛屾鍦ㄩ€€鍑?..")
        collector.stop()


if __name__ == "__main__":
    asyncio.run(main())



