"""
Alpha strategy live discovery entrypoint.

Usage:
  python run_live_discovery.py --once
  python run_live_discovery.py --watch --interval 6
  python run_live_discovery.py --once --data-days 60
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_DISCOVERY_LOG = ROOT / "alpha" / "output" / "discovery.log"
_ALERTS_LOG = ROOT / "monitor" / "output" / "alerts.log"
_CONFIG_MAP = {
    "BTCUSDT": Path("config/exchanges.yaml"),
    "ETHUSDT": Path("config/exchanges_eth.yaml"),
}

sys.path.insert(0, str(ROOT))
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

from alpha.live_discovery import LiveDiscoveryEngine
from alpha.auto_promoter import AutoPromoter

_REST_ENDPOINTS = [
    "klines",
    "funding_rate",
    "open_interest",
    "taker_ratio",
    "long_short_ratio",
]


def _setup_logging() -> None:
    _DISCOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(_DISCOVERY_LOG, encoding="utf-8"),
        ],
        force=True,
    )


_setup_logging()
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alpha strategy live discovery engine")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run one discovery pass")
    mode.add_argument("--watch", action="store_true", help="Run discovery continuously")

    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol, default BTCUSDT")
    parser.add_argument("--interval", type=float, default=6.0, help="Watch mode interval in hours")
    parser.add_argument("--data-days", type=int, default=365, help="Lookback window in days")
    parser.add_argument(
        "--skip-rest-sync",
        action="store_true",
        help="Skip REST prefetch and use local parquet data only",
    )
    parser.add_argument(
        "--storage",
        default=None,
        help="Parquet storage root, default data/storage for BTCUSDT and data/storage/{symbol} for others",
    )
    parser.add_argument("--top-n", type=int, default=20, help="Top-N features for atom mining")
    parser.add_argument("--min-triggers", type=int, default=30, help="Minimum rule trigger count")
    parser.add_argument(
        "--direction",
        choices=["long", "short", "both"],
        default="both",
        help="Discovery direction filter: long, short, or both (default: both)",
    )
    return parser.parse_args()


def _resolve_storage(args: argparse.Namespace) -> str:
    if args.storage:
        storage = Path(args.storage)
        return str(storage if storage.is_absolute() else ROOT / storage)
    symbol = args.symbol.upper()
    default_storage = ROOT / "data" / "storage"
    return str(default_storage if symbol == "BTCUSDT" else default_storage / symbol)


def _resolve_config_path(symbol: str) -> str:
    config_path = ROOT / _CONFIG_MAP[symbol.upper()]
    return str(config_path)


def _prefetch_rest_data(config_path: str, storage_path: str, data_days: int) -> None:
    async def _sync() -> None:
        from data.downloader.binance_rest import BinanceRestDownloader

        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start_iso = (now - timedelta(days=data_days + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        async with BinanceRestDownloader(config_path=config_path) as downloader:
            storage_root = Path(storage_path)
            downloader.processor.storage_path = storage_root
            downloader.checkpoint.storage_path = storage_root
            downloader.start_date = start_iso
            downloader.end_date = end_iso
            await downloader.download_all(endpoints=_REST_ENDPOINTS, show_progress=False)

    try:
        logger.info("[DISCOVERY] Syncing latest REST data before discovery")
        asyncio.run(_sync())
        logger.info("[DISCOVERY] REST sync complete")
    except Exception as exc:
        logger.warning("[DISCOVERY] REST sync failed, using existing data: %s", exc)


def _write_discovery_alert(cards: list[dict]) -> None:
    _ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sep = "=" * 60
    lines = [
        "",
        sep,
        f"  *** DISCOVERY ALERT ***  {now}",
        f"  Alpha discovery found {len(cards)} new candidate rules",
        sep,
    ]

    for idx, card in enumerate(cards, 1):
        entry = card["entry"]
        stats = card["stats"]
        exit_info = card.get("exit")
        lines.append(f"  [{idx}] {card['rule_str']}")
        lines.append(
            f"       {entry['direction'].upper()} {entry['horizon']}bar | "
            f"OOS win={stats['oos_win_rate']:.1f}% n={stats['n_oos']} "
            f"net={stats['oos_net_return']:+.4f}%"
        )
        if exit_info and isinstance(exit_info, dict):
            top3 = exit_info.get("top3", [])
            if top3 and isinstance(top3, list):
                first = top3[0]
                conds = first.get("conditions", [])
                desc = " & ".join(
                    f"{c.get('feature','')} {c.get('operator','')} {c.get('threshold','')}"
                    for c in conds if isinstance(c, dict)
                )
                lines.append(f"       exit: [{desc}]")
            elif "feature" in exit_info:
                lines.append(
                    f"       exit: {exit_info['feature']} {exit_info.get('operator','')} "
                    f"{exit_info.get('threshold','')}"
                )

    lines.extend(
        [
            sep,
            "  >>> review candidates in the dashboard Alpha tab <<<",
            sep,
            "",
        ]
    )

    with _ALERTS_LOG.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print("\n".join(lines))


def run_once(args: argparse.Namespace) -> int:
    (ROOT / "alpha" / "output").mkdir(parents=True, exist_ok=True)

    storage = _resolve_storage(args)
    symbol = args.symbol.upper()
    config_path = _resolve_config_path(symbol)

    if not args.skip_rest_sync:
        _prefetch_rest_data(config_path, storage, args.data_days)
    else:
        logger.info("[DISCOVERY] Skipping REST sync, using local parquet only")

    engine = LiveDiscoveryEngine(
        storage_path=storage,
        symbol=symbol,
        top_n=args.top_n,
        min_triggers=args.min_triggers,
        direction_filter=args.direction,
    )
    cards = engine.run_once(data_days=args.data_days)
    if cards:
        try:
            _write_discovery_alert(cards)
        except Exception as exc:
            logger.warning("[DISCOVERY] Alert log write failed (non-blocking): %s", exc)

    # LLM 自动验证：对新进入 pending 的候选立即跑一次促进器
    try:
        promoter = AutoPromoter()
        summary = promoter.run_once()
        if any(summary.get(k, 0) > 0 for k in ("approved", "rejected", "review")):
            logger.info(
                "[PROMOTER] LLM 验证完成: 批准=%d 拒绝=%d 审查=%d",
                summary["approved"], summary["rejected"], summary["review"],
            )
    except Exception as exc:
        logger.warning("[PROMOTER] LLM 验证跳过 (配置未就绪或网络异常): %s", exc)

    return len(cards)


_DISCOVERY_HEARTBEAT = ROOT / "monitor" / "output" / "discovery_heartbeat.json"
_DISCOVERY_HEARTBEAT_INTERVAL_S = 60.0


def _set_discovery_alive(alive: bool) -> None:
    """Write discovery heartbeat to a dedicated file (read by run_monitor.py).

    Uses a separate file so run_monitor.py does NOT overwrite this flag when
    it regenerates system_state.json every tick.
    """
    import json
    import os
    try:
        _DISCOVERY_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        payload = {"alive": alive, "pid": os.getpid(), "updated": time.time()}
        tmp = _DISCOVERY_HEARTBEAT.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_DISCOVERY_HEARTBEAT)
    except Exception as exc:
        logger.warning("[DISCOVERY] Could not write discovery_heartbeat.json: %s", exc)


def _sleep_with_heartbeat(total_seconds: float) -> None:
    """Sleep in short chunks so runtime status can observe fresh discovery heartbeats."""
    remaining = max(0.0, float(total_seconds))
    while remaining > 0:
        _set_discovery_alive(True)
        chunk = min(_DISCOVERY_HEARTBEAT_INTERVAL_S, remaining)
        time.sleep(chunk)
        remaining -= chunk


def main() -> None:
    args = parse_args()
    import atexit
    atexit.register(_set_discovery_alive, False)

    if args.once:
        _set_discovery_alive(True)
        try:
            count = run_once(args)
        finally:
            _set_discovery_alive(False)
        if count > 0:
            logger.info("[DISCOVERY] Completed with %s candidate rules", count)
        else:
            logger.info("[DISCOVERY] Completed with no new candidates")
        return

    interval_seconds = args.interval * 3600
    logger.info(
        "[DISCOVERY] Watch mode started symbol=%s interval=%.1fh",
        args.symbol.upper(),
        args.interval,
    )
    _set_discovery_alive(True)

    run_count = 0
    while True:
        run_count += 1
        logger.info("[DISCOVERY] Scan %s starting", run_count)
        try:
            count = run_once(args)
            if count > 0:
                logger.info("[DISCOVERY] Scan %s finished with %s new candidates", run_count, count)
            else:
                logger.info("[DISCOVERY] Scan %s finished with no new candidates", run_count)
        except Exception as exc:
            logger.error("[DISCOVERY] Scan %s failed: %s", run_count, exc, exc_info=True)

        next_run = time.time() + interval_seconds
        logger.info(
            "[DISCOVERY] Next run at %s (wait %.1fh)",
            time.strftime("%H:%M:%S", time.localtime(next_run)),
            args.interval,
        )
        _sleep_with_heartbeat(interval_seconds)


if __name__ == "__main__":
    main()

