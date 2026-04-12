"""
Tick Alpha 发现引擎入口。

用法:
  python run_tick_discovery.py                      # 全部三个窗口，90天数据
  python run_tick_discovery.py --window 10          # 只跑 10s 窗口
  python run_tick_discovery.py --window 30 60       # 跑 30s 和 60s 窗口
  python run_tick_discovery.py --days 60            # 使用最近 60 天数据
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

from alpha.tick_discovery import TickDiscoveryEngine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "alpha" / "output" / "tick_discovery.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_tick_discovery")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tick Alpha Discovery Engine")
    parser.add_argument(
        "--window", "-w",
        nargs="+",
        type=int,
        choices=[10, 30, 60],
        default=[10, 30, 60],
        help="时间窗口大小（秒），可多选，默认全部三个",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=90,
        help="使用最近多少天的 agg_trades 数据（默认 90 天）",
    )
    args = parser.parse_args()

    engine = TickDiscoveryEngine()
    total_cards: list[dict] = []

    for window in args.window:
        logger.info("=" * 60)
        logger.info("开始 %ds 窗口扫描（data_days=%d）", window, args.days)
        logger.info("=" * 60)
        try:
            cards = engine.run_once(window_seconds=window, data_days=args.days)
            total_cards.extend(cards)
            logger.info("%ds 窗口完成: 发现 %d 个候选策略", window, len(cards))
        except Exception as exc:
            logger.error("%ds 窗口扫描失败: %s", window, exc, exc_info=True)

    logger.info("=" * 60)
    logger.info("全部完成: 共 %d 个候选策略", len(total_cards))
    for card in total_cards:
        entry = card.get("entry_condition", {})
        stats = card.get("wf_stats", {})
        logger.info(
            "  [%s] %s %s %s | dir=%s | OOS WR=%.1f%% n=%d MFE/MAE=%.2f",
            card.get("timeframe"),
            entry.get("feature"),
            entry.get("operator"),
            entry.get("threshold"),
            card.get("direction"),
            stats.get("oos_wr", 0),
            stats.get("oos_n", 0),
            stats.get("mfe_mae_ratio", 0),
        )


if __name__ == "__main__":
    main()
