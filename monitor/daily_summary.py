"""Daily performance summary generator.

Parses execution/logs/trades.csv to generate daily_summary.json with:
- Today's trade count, win rate, P&L
- Per-strategy breakdown
- Signal pipeline efficiency (if decision_logger stats available)
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRADES_CSV = Path("execution/logs/trades.csv")
_OUTPUT_DIR = Path("monitor/output")


def generate_daily_summary(
    decision_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate today's performance summary from trades.csv.

    Args:
        decision_stats: Optional pipeline stats from DecisionLogger.get_stats().

    Returns:
        Summary dict (also written to monitor/output/daily_summary.json).
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trades_today: list[dict[str, str]] = []
    if _TRADES_CSV.exists():
        try:
            with open(_TRADES_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.get("exit_time") or row.get("entry_time") or ""
                    if ts.startswith(today_str):
                        trades_today.append(row)
        except Exception as exc:
            logger.warning("[DAILY] Failed to read trades.csv: %s", exc)

    # Aggregate stats
    total = len(trades_today)
    wins = 0
    total_pnl = 0.0
    by_family: dict[str, dict[str, Any]] = {}

    for trade in trades_today:
        net_ret = 0.0
        try:
            net_ret = float(trade.get("net_return_pct") or trade.get("return_pct") or 0)
        except (ValueError, TypeError):
            pass

        is_win = net_ret > 0
        if is_win:
            wins += 1
        total_pnl += net_ret

        family = trade.get("family") or trade.get("strategy_id") or "unknown"
        direction = trade.get("direction") or ""
        key = f"{family}|{direction}"

        if key not in by_family:
            by_family[key] = {"family": family, "direction": direction,
                              "count": 0, "wins": 0, "pnl": 0.0}
        by_family[key]["count"] += 1
        if is_win:
            by_family[key]["wins"] += 1
        by_family[key]["pnl"] += net_ret

    # Find best and worst strategy
    best_strategy = None
    worst_strategy = None
    for key, stats in by_family.items():
        stats["win_rate"] = round(stats["wins"] / stats["count"] * 100, 1) if stats["count"] > 0 else 0
        stats["pnl"] = round(stats["pnl"], 4)
        if best_strategy is None or stats["pnl"] > by_family.get(best_strategy, {}).get("pnl", 0):
            best_strategy = key
        if worst_strategy is None or stats["pnl"] < by_family.get(worst_strategy, {}).get("pnl", 0):
            worst_strategy = key

    summary = {
        "date": today_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate_pct": round(wins / total * 100, 1) if total > 0 else 0,
        "total_pnl_pct": round(total_pnl, 4),
        "avg_return_pct": round(total_pnl / total, 4) if total > 0 else 0,
        "best_strategy": by_family.get(best_strategy) if best_strategy else None,
        "worst_strategy": by_family.get(worst_strategy) if worst_strategy else None,
        "by_strategy": list(by_family.values()),
    }

    if decision_stats:
        summary["signal_pipeline"] = decision_stats

    # Write to disk
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _OUTPUT_DIR / "daily_summary.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("[DAILY] Failed to write daily_summary.json: %s", exc)

    return summary
