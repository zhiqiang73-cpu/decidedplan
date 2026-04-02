"""Historical downloader and live-only websocket bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))
from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from data.downloader.binance_rest import BinanceRestDownloader
from data.downloader.ws_collector import BinanceWSCollector

_CONFIG_MAP = {
    "BTCUSDT": ROOT / "config" / "exchanges.yaml",
    "ETHUSDT": ROOT / "config" / "exchanges_eth.yaml",
}
_DEFAULT_CONFIG_PATH = str(_CONFIG_MAP["BTCUSDT"])


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "downloader.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


async def download_history_all(config_path: str = _DEFAULT_CONFIG_PATH) -> str:
    async with BinanceRestDownloader(config_path=config_path) as downloader:
        await downloader.download_all(show_progress=True)
        downloader.print_stats()
        return str(downloader.processor.storage_path)


async def download_selective(config_path: str = _DEFAULT_CONFIG_PATH) -> None:
    async with BinanceRestDownloader(config_path=config_path) as downloader:
        await downloader.download_all(endpoints=["funding_rate"], show_progress=True)
        print("\nFunding history downloaded. Continuing with klines...")
        await downloader.download_all(endpoints=["klines"], show_progress=True)
        downloader.print_stats()


async def validate_checkpoint(config_path: str = _DEFAULT_CONFIG_PATH) -> None:
    async with BinanceRestDownloader(config_path=config_path) as downloader:
        downloader.repair_checkpoint_data()
        downloader.checkpoint.print_summary()


async def collect_live_only_streams(storage_path: str) -> None:
    print("\nHistorical REST backfill finished.")
    print("Starting live-only websocket collection for the remaining streams.")
    print("Press Ctrl-C to stop collection safely.\n")
    collector = BinanceWSCollector(storage_path=storage_path)
    await collector.run()


async def backfill_all_required_data(config_path: str = _DEFAULT_CONFIG_PATH) -> None:
    storage_path = await download_history_all(config_path=config_path)
    await collect_live_only_streams(storage_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance data downloader")
    parser.add_argument(
        "--symbol",
        default=None,
        choices=sorted(_CONFIG_MAP.keys()),
        help="Download a symbol directly without the interactive menu",
    )
    return parser.parse_args()


def _resolve_config_path(symbol: str) -> str:
    return str(_CONFIG_MAP[symbol.upper()])


def _read_choice(default: str = "3") -> str:
    if not sys.stdin.isatty():
        print(f"\n[Downloader] No interactive stdin detected; defaulting to option {default}.")
        return default

    try:
        return input("\nEnter choice (1-4): ").strip()
    except EOFError:
        print(f"\n[Downloader] stdin closed; defaulting to option {default}.")
        return default


def _run(coro) -> None:
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")


def main() -> None:
    setup_logging()
    args = parse_args()

    if args.symbol:
        symbol = args.symbol.upper()
        config_path = _resolve_config_path(symbol)
        print(f"\n[Downloader] Direct mode for {symbol} (config={config_path})")
        _run(download_history_all(config_path=config_path))
        print("\nDone.")
        return

    print("=" * 60)
    print("Binance Data Downloader")
    print("=" * 60)
    print("\nChoose an action:")
    print("1. Backfill required REST datasets, then continue live-only websocket collection")
    print("2. Selective download (funding_rate first, then klines)")
    print("3. Validate checkpoint state")
    print("4. Exit")

    choice = _read_choice()
    if choice == "1":
        print("\nStarting full backfill...")
        _run(backfill_all_required_data())
    elif choice == "2":
        print("\nStarting selective download...")
        _run(download_selective())
    elif choice == "3":
        print("\nValidating checkpoint...")
        _run(validate_checkpoint())
    elif choice == "4":
        print("Exit.")
        return
    else:
        print("Invalid choice.")
        return

    print("\nDone.")


if __name__ == "__main__":
    main()
