"""Incremental REST data sync daemon."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from data.downloader.binance_rest import BinanceRestDownloader

SYNC_GROUPS: list[tuple[str, int]] = [
    ("klines", 2),
    ("open_interest", 2),
    ("taker_ratio", 2),
    ("long_short_ratio", 2),
    ("funding_rate", 5),
]


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fh = logging.FileHandler(log_dir / "data_sync.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(fmt))
        root.addHandler(fh)
        root.addHandler(sh)
    return logging.getLogger("data_sync")


async def _sync_one(endpoint: str, days_back: int, now: datetime, logger: logging.Logger) -> bool:
    start_iso = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info("syncing %s | last %d days (%s ~ %s)", endpoint, days_back, start_iso, end_iso)
    try:
        async with BinanceRestDownloader() as downloader:
            downloader.start_date = start_iso
            downloader.end_date = end_iso
            await downloader.download_all(endpoints=[endpoint], show_progress=False)
        logger.info("sync ok: %s", endpoint)
        return True
    except Exception as exc:
        logger.error("sync failed for %s: %s", endpoint, exc, exc_info=True)
        return False


async def run_sync_cycle(log_dir: Path, logger: logging.Logger) -> dict:
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    synced: list[str] = []
    failed: list[str] = []

    for endpoint, days_back in SYNC_GROUPS:
        ok = await _sync_one(endpoint, days_back, now, logger)
        if ok:
            synced.append(endpoint)
        else:
            failed.append(endpoint)

    status = "ok"
    if failed and synced:
        status = "partial"
    elif failed:
        status = "error"

    state = {
        "last_sync": now.isoformat(),
        "status": status,
        "endpoints_synced": synced,
        "endpoints_failed": failed,
    }
    (log_dir / "data_sync_status.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("cycle done: status=%s synced=%s failed=%s", status, synced, failed)
    return state


async def main_loop(args: argparse.Namespace) -> None:
    log_dir = Path(args.log_dir)
    logger = setup_logging(log_dir)
    logger.info("data sync daemon starting | interval=%dm once=%s", args.interval, args.once)

    while True:
        await run_sync_cycle(log_dir, logger)
        if args.once:
            return
        logger.info("next sync in %d minutes", args.interval)
        await asyncio.sleep(args.interval * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental REST data sync daemon")
    parser.add_argument("--once", action="store_true", help="Run one sync cycle and exit")
    parser.add_argument("--interval", type=int, default=60, metavar="MINUTES", help="Minutes between sync cycles")
    parser.add_argument("--log-dir", default="monitor/output", metavar="DIR", help="Directory for logs and status JSON")
    return parser.parse_args()


if __name__ == "__main__":
    _args = parse_args()
    try:
        asyncio.run(main_loop(_args))
    except KeyboardInterrupt:
        print("\ndata sync daemon stopped")
