"""
Decision Logger

Records every signal decision (EXECUTE or BLOCKED) to a JSONL file for
post-hoc analysis of filter effectiveness and signal throughput.

Output file: <log_dir>/decisions.jsonl  (one JSON object per line)
Rotation: auto-rename to decisions.jsonl.1 when file exceeds 50 MB.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---- Constants -----------------------------------------------------------

# Maximum JSONL file size before size-based rotation triggers.
MAX_FILE_BYTES: int = 50 * 1024 * 1024  # 50 MB

# Canonical set of blocked_by filter names used across the trading system.
# Initialises per-filter counters and soft-validates incoming values.
VALID_BLOCKED_BY: frozenset[str] = frozenset(
    {
        "p1_cooldown",
        "p2_cooldown",
        "regime_filter",
        "trend_filter",
        "health_degraded",
        "health_retired",
        "confidence_gate",
        "whitelist",
        "force_concentration",
        "execution_cooldown",
        "burst_cap",
        "liquidation_flow",
        "a2_persistence",
    }
)

_LOG_FILENAME = "decisions.jsonl"
_LOG_FILENAME_BACKUP = "decisions.jsonl.1"


# ---- DecisionLogger -------------------------------------------------------


class DecisionLogger:
    """Append-only JSONL decision log with daily in-memory stats and size-based rotation.

    Each call to log_blocked or log_executed appends one JSON line to
    decisions.jsonl.  In-memory counters accumulate until reset_daily_stats
    is called or the process restarts.

    The file is opened and closed on every write -- same pattern used by
    AlertHandler.send() -- so there is no stale handle after rotation.

    Args:
        log_dir: Directory for decisions.jsonl. Created if absent.
                 Accepts relative or absolute paths.
    """

    def __init__(self, log_dir: str = "monitor/output") -> None:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)

        self._log_path: Path = log_dir_path / _LOG_FILENAME
        self._backup_path: Path = log_dir_path / _LOG_FILENAME_BACKUP

        # In-memory daily stats.  Not persisted; reset on restart or explicit call.
        self._execute_count: int = 0
        self._blocked_by_counts: dict[str, int] = {k: 0 for k in VALID_BLOCKED_BY}
        self._total_count: int = 0

        logger.info("[DecisionLogger] Logging decisions to: %s", self._log_path)

    # ---- Public API -------------------------------------------------------

    def log_blocked(
        self,
        signal: str,
        family: str,
        direction: str,
        blocked_by: str,
        confidence: int,
        regime: str,
        trend: str,
        flow: str,
        reason: str,
    ) -> None:
        """Append a BLOCKED decision record and increment daily stats.

        Args:
            signal:     Full signal name, e.g. P1-9_position_compression.
            family:     Strategy family identifier, e.g. P1-9.
            direction:  long or short.
            blocked_by: Name of the filter that rejected execution.
                        Should be one of VALID_BLOCKED_BY; unknown values
                        are accepted but emit a WARNING log.
            confidence: Signal confidence level (1-3).
            regime:     Current market regime string from RegimeDetector.
            trend:      Current trend direction from RegimeDetector.
            flow:       Current flow type string from FlowClassifier.
            reason:     Human-readable explanation for the block.
        """
        blocked_by_str = str(blocked_by)
        if blocked_by_str not in VALID_BLOCKED_BY:
            logger.warning(
                "[DecisionLogger] Unknown blocked_by value: %r  (known: %s)",
                blocked_by_str,
                sorted(VALID_BLOCKED_BY),
            )

        record = self._build_record(
            signal=signal,
            family=family,
            direction=direction,
            decision="BLOCKED",
            blocked_by=blocked_by_str,
            confidence=confidence,
            regime=regime,
            trend=trend,
            flow=flow,
            reason=reason,
        )
        self._write(record)

        # Update stats after the write so a write failure does not inflate counts.
        self._total_count += 1
        # dict.get so an unknown filter name accumulates without KeyError.
        self._blocked_by_counts[blocked_by_str] = (
            self._blocked_by_counts.get(blocked_by_str, 0) + 1
        )

    def log_executed(
        self,
        signal: str,
        family: str,
        direction: str,
        confidence: int,
        regime: str,
        trend: str,
        flow: str,
        reason: str,
    ) -> None:
        """Append an EXECUTE decision record and increment daily stats.

        Args:
            signal:     Full signal name.
            family:     Strategy family identifier.
            direction:  long or short.
            confidence: Signal confidence level (1-3).
            regime:     Current market regime string.
            trend:      Current trend direction string.
            flow:       Current flow type string.
            reason:     Human-readable confirmation that all filters passed.
        """
        record = self._build_record(
            signal=signal,
            family=family,
            direction=direction,
            decision="EXECUTE",
            blocked_by=None,
            confidence=confidence,
            regime=regime,
            trend=trend,
            flow=flow,
            reason=reason,
        )
        self._write(record)

        self._total_count += 1
        self._execute_count += 1

    def get_stats(self) -> dict:
        """Return a snapshot of current session decision counts.

        Returns:
            A dict with keys:
            - total (int): all decisions logged this session
            - executed (int): EXECUTE decisions
            - blocked (int): BLOCKED decisions (total - executed)
            - by_filter (dict[str, int]): per-filter BLOCKED counts

            The returned dict is a shallow copy; mutating it does not
            affect the logger state.
        """
        return {
            "total": self._total_count,
            "executed": self._execute_count,
            "blocked": self._total_count - self._execute_count,
            "by_filter": dict(self._blocked_by_counts),
        }

    def reset_daily_stats(self) -> None:
        """Reset all in-memory counters to zero.

        Intended to be called at UTC midnight for clean per-day breakdowns
        without requiring a process restart.  Has no effect on the JSONL file.
        """
        self._execute_count = 0
        self._blocked_by_counts = {k: 0 for k in VALID_BLOCKED_BY}
        self._total_count = 0
        logger.info("[DecisionLogger] Daily stats reset.")

    # ---- Internal helpers -------------------------------------------------

    @staticmethod
    def _build_record(
        signal: str,
        family: str,
        direction: str,
        decision: str,
        blocked_by: Optional[str],
        confidence: int,
        regime: str,
        trend: str,
        flow: str,
        reason: str,
    ) -> dict:
        """Build a flat dict ready for JSON serialisation."""
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "signal": str(signal),
            "family": str(family),
            "direction": str(direction),
            "decision": str(decision),
            "blocked_by": blocked_by,          # str or None; preserved as-is
            "confidence": int(confidence),
            "regime": str(regime),
            "trend": str(trend),
            "flow": str(flow),
            "reason": str(reason),
        }

    def _write(self, record: dict) -> None:
        """Serialise record to a JSONL line and append it to the log file.

        The file is opened and closed on each call -- identical to the pattern
        in AlertHandler.send() -- so rotation can freely rename the path
        without leaving a stale open handle behind.
        """
        line: str = json.dumps(record, ensure_ascii=False)
        try:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + chr(10))
        except Exception as exc:
            logger.warning("[DecisionLogger] Write failed: %s", exc)
            return  # do not attempt rotation after a failed write

        self._maybe_rotate()

    def _maybe_rotate(self) -> None:
        """Rename the active log to .1 and start fresh if it exceeds MAX_FILE_BYTES.

        Rotation behaviour:
          - Only one backup generation is kept; any existing .1 file is
            overwritten by os.replace (atomic on POSIX, best-effort on Windows).
          - If stat() fails (file deleted externally) the check is silently skipped.
          - If the rename fails (permission error) a WARNING is emitted but no
            exception is raised -- the monitor process must remain alive.
        """
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return  # file missing or unreadable; skip rotation silently

        if size <= MAX_FILE_BYTES:
            return

        logger.info(
            "[DecisionLogger] Rotating %s (%.1f MB >= 50 MB)",
            self._log_path.name,
            size / (1024 * 1024),
        )
        try:
            # os.replace is atomic on POSIX and best-effort on Windows.
            # It overwrites the destination if it already exists.
            os.replace(str(self._log_path), str(self._backup_path))
            logger.info(
                "[DecisionLogger] Rotation complete: %s -> %s",
                _LOG_FILENAME,
                _LOG_FILENAME_BACKUP,
            )
        except OSError as exc:
            logger.warning("[DecisionLogger] Rotation rename failed: %s", exc)
