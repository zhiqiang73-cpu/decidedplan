"""CSV trade logging for the execution layer."""

from __future__ import annotations

import csv
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from execution import config
from monitor.live_catalog import resolve_strategy_id_from_signal_name

logger = logging.getLogger(__name__)

_TZ_SHANGHAI = timezone(timedelta(hours=8))

_CSV_HEADER = [
    "trade_id",
    "strategy_id",
    "signal_name",
    "raw_signal_name",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "qty",
    "gross_return_pct",
    "net_return_pct",
    "exit_reason",
    "confidence",
    "horizon_min",
    "flow_type",
    "regime",
]

_DISPLAY_SPLIT_FAMILIES = frozenset({"P1-9", "P1-10"})
_EXACT_LEGACY_FAMILY_MAP = {
    "20260403_233751_position_oi_chang": "A4-PIR",
    "20260404_222317_position_oi_chang": "A4-PIR-CANDIDATE",
}
_GENERIC_LEGACY_FAMILIES = frozenset(
    {
        "LEGACY-HIGH-DISTRIBUTION",
        "LEGACY-POSITIONING",
    }
)


def _split_signal_tokens(signal_name: str) -> list[str]:
    text = str(signal_name or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def _resolve_legacy_family_from_token(token: str) -> str:
    token_text = str(token or "").strip()
    if not token_text:
        return ""

    exact = _EXACT_LEGACY_FAMILY_MAP.get(token_text)
    if exact:
        return exact

    if "dist_to__" in token_text:
        return "LEGACY-HIGH-DISTRIBUTION"
    if "position_" in token_text:
        return "LEGACY-POSITIONING"
    return ""


def _resolve_trade_family(signal_name: str) -> str:
    raw_text = str(signal_name or "").strip()
    if not raw_text:
        return ""

    resolved = resolve_strategy_id_from_signal_name(raw_text)
    if resolved and resolved != raw_text:
        return resolved

    mapped: list[str] = []
    seen: set[str] = set()
    for token in _split_signal_tokens(raw_text) or [raw_text]:
        family = _resolve_legacy_family_from_token(token)
        if not family or family in seen:
            continue
        seen.add(family)
        mapped.append(family)

    has_live_family = any(family not in _GENERIC_LEGACY_FAMILIES for family in mapped)
    if has_live_family:
        mapped = [family for family in mapped if family not in _GENERIC_LEGACY_FAMILIES]

    if mapped:
        return " | ".join(mapped)
    return raw_text


def _display_strategy_name(strategy_id: str, direction: str) -> str:
    direction_text = str(direction or "").strip().upper()
    parts = [part.strip() for part in str(strategy_id or "").split("|") if part.strip()]
    display_parts: list[str] = []

    for part in parts or [str(strategy_id or "").strip()]:
        if part in _DISPLAY_SPLIT_FAMILIES and direction_text in {"LONG", "SHORT"}:
            display_parts.append(f"{part}-{direction_text}")
        elif part == "external":
            display_parts.append("EXTERNAL")
        else:
            display_parts.append(part)

    return " | ".join([part for part in display_parts if part])


def _canonicalize_trade_identity(
    signal_name: str,
    direction: str,
    strategy_id: str = "",
    raw_signal_name: str = "",
) -> tuple[str, str, str]:
    raw_text = str(raw_signal_name or signal_name or "").strip()
    family = str(strategy_id or "").strip() or _resolve_trade_family(raw_text)
    display_name = _display_strategy_name(family, direction) or family or raw_text
    return family or display_name, display_name, raw_text


class TradeLogger:
    """Persist execution results to CSV."""

    def __init__(
        self,
        csv_path: str | Path = "execution/logs/trades.csv",
        default_total_fee_rate: float = config.MAKER_FEE_RATE + config.TAKER_FEE_RATE,
        on_trade_complete: Callable | None = None,
    ) -> None:
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_total_fee_rate = default_total_fee_rate
        self._on_trade_complete = on_trade_complete
        self._lock = threading.Lock()
        self._trade_counter = 0
        self._init_csv()
        self._ensure_csv_schema()
        self._restore_counter()

    def _ensure_csv_schema(self) -> None:
        """Normalize legacy CSV rows in place while preserving a backup copy."""
        if not self.csv_path.exists():
            return

        try:
            with self.csv_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
        except Exception as exc:
            logger.warning("[TRADE_LOG] CSV schema validation failed: %s", exc)
            return

        expected = list(_CSV_HEADER)
        needs_rewrite = fieldnames != expected
        normalized_rows: list[dict[str, str]] = []

        for row in rows:
            normalized = self._normalize_existing_row(row)
            normalized_rows.append(normalized)
            if not needs_rewrite:
                for key in expected:
                    if str(row.get(key, "") or "").strip() != str(normalized.get(key, "") or "").strip():
                        needs_rewrite = True
                        break

        if not needs_rewrite:
            return

        backup = self.csv_path.with_name(
            self.csv_path.stem
            + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            + self.csv_path.suffix
        )
        self.csv_path.replace(backup)
        with self.csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=expected)
            writer.writeheader()
            writer.writerows(normalized_rows)
        logger.info(
            "[TRADE_LOG] Normalized legacy trade CSV to canonical schema. Backup: %s",
            backup.name,
        )

    def _normalize_existing_row(self, row: dict[str, str]) -> dict[str, str]:
        raw_signal_name = str(row.get("raw_signal_name") or row.get("signal_name") or "").strip()
        direction = str(row.get("direction") or "").strip()
        strategy_id, display_name, raw_text = _canonicalize_trade_identity(
            signal_name=str(row.get("signal_name") or "").strip(),
            direction=direction,
            strategy_id=str(row.get("strategy_id") or "").strip(),
            raw_signal_name=raw_signal_name,
        )

        normalized = {key: "" for key in _CSV_HEADER}
        for key in _CSV_HEADER:
            if key in {"strategy_id", "signal_name", "raw_signal_name"}:
                continue
            normalized[key] = str(row.get(key, "") or "").strip()
        normalized["strategy_id"] = strategy_id
        normalized["signal_name"] = display_name
        normalized["raw_signal_name"] = raw_text
        return normalized

    def _restore_counter(self) -> None:
        """Read existing CSV and set counter to max trade_id to avoid duplicates on restart."""
        if not self.csv_path.exists():
            return
        try:
            max_id = 0
            with self.csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        tid = int(row.get("trade_id", 0))
                        if tid > max_id:
                            max_id = tid
                    except (ValueError, TypeError):
                        continue
            if max_id > 0:
                self._trade_counter = max_id
                logger.info("[TRADE_LOG] Restored counter to %s from existing CSV", max_id)
        except Exception as exc:
            logger.warning("[TRADE_LOG] Failed to restore counter: %s", exc)

    def log_not_filled(
        self,
        signal_name: str,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        exit_time: datetime,
        qty: float,
        confidence: int,
        horizon_min: int,
        flow_type: str = "",
        regime: str = "",
        strategy_id: str = "",
        raw_signal_name: str = "",
    ) -> None:
        strategy_id, display_name, raw_signal_name = _canonicalize_trade_identity(
            signal_name=signal_name,
            direction=direction,
            strategy_id=strategy_id,
            raw_signal_name=raw_signal_name,
        )
        self._write_row(
            {
                "strategy_id": strategy_id,
                "signal_name": display_name,
                "raw_signal_name": raw_signal_name,
                "direction": direction,
                "entry_time": self._format_dt(entry_time),
                "entry_price": self._format_float(entry_price),
                "exit_time": self._format_dt(exit_time),
                "exit_price": "",
                "qty": self._format_float(qty),
                "gross_return_pct": self._format_float(0.0),
                "net_return_pct": self._format_float(0.0),
                "exit_reason": "not_filled",
                "confidence": confidence,
                "horizon_min": horizon_min,
                "flow_type": flow_type,
                "regime": regime,
            }
        )

    def log_trade(
        self,
        signal_name: str,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        exit_time: datetime,
        exit_price: float | None,
        qty: float,
        exit_reason: str,
        confidence: int,
        horizon_min: int,
        total_fee_rate: float | None = None,
        flow_type: str = "",
        regime: str = "",
        strategy_id: str = "",
        raw_signal_name: str = "",
    ) -> None:
        gross_return_pct = self._compute_return_pct(direction, entry_price, exit_price)
        fee_rate = self.default_total_fee_rate if total_fee_rate is None else float(total_fee_rate)
        net_return_pct = gross_return_pct - (fee_rate * 100.0)
        strategy_id, display_name, raw_signal_name = _canonicalize_trade_identity(
            signal_name=signal_name,
            direction=direction,
            strategy_id=strategy_id,
            raw_signal_name=raw_signal_name,
        )

        self._write_row(
            {
                "strategy_id": strategy_id,
                "signal_name": display_name,
                "raw_signal_name": raw_signal_name,
                "direction": direction,
                "entry_time": self._format_dt(entry_time),
                "entry_price": self._format_float(entry_price),
                "exit_time": self._format_dt(exit_time),
                "exit_price": self._format_float(exit_price) if exit_price is not None else "",
                "qty": self._format_float(qty),
                "gross_return_pct": self._format_float(gross_return_pct),
                "net_return_pct": self._format_float(net_return_pct),
                "exit_reason": exit_reason,
                "confidence": confidence,
                "horizon_min": horizon_min,
                "flow_type": flow_type,
                "regime": regime,
            }
        )

        if self._on_trade_complete is not None:
            try:
                self._on_trade_complete(
                    {
                        "strategy_id": strategy_id,
                        "signal_name": display_name,
                        "raw_signal_name": raw_signal_name,
                        "direction": direction,
                        "net_return_pct": net_return_pct,
                        "exit_reason": exit_reason,
                        "confidence": confidence,
                        "horizon_min": horizon_min,
                        "flow_type": flow_type,
                        "regime": regime,
                    }
                )
            except Exception as exc:
                logger.warning("[TRADE_LOG] on_trade_complete callback failed: %s", exc)

    def _init_csv(self) -> None:
        if self.csv_path.exists():
            return
        with self.csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HEADER)
            writer.writeheader()

    def _write_row(self, row: dict[str, object]) -> None:
        with self._lock:
            self._trade_counter += 1
            row = {"trade_id": self._trade_counter, **row}
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.csv_path.exists():
                self._init_csv()
            with self.csv_path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_HEADER)
                writer.writerow(row)

    @staticmethod
    def _compute_return_pct(direction: str, entry_price: float, exit_price: float | None) -> float:
        if exit_price is None or entry_price <= 0:
            return 0.0
        if direction.lower() == "long":
            return (exit_price - entry_price) / entry_price * 100.0
        if direction.lower() == "short":
            return (entry_price - exit_price) / entry_price * 100.0
        return 0.0

    @staticmethod
    def _format_dt(value: datetime) -> str:
        return value.astimezone(_TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S CST")

    @staticmethod
    def _format_float(value: float) -> str:
        return f"{float(value):.6f}"
