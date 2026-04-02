"""CSV trade logging for the execution layer."""

from __future__ import annotations

import csv
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Callable

# UTC+8 濠电偞鍨堕幐鎼佹晝閵夆晛绠?闂備礁鎲￠悧妤呭Φ閻愬搫瑙︽い鎰剁畱缁秹鏌涢锝嗙闁?
_TZ_SHANGHAI = timezone(timedelta(hours=8))
from pathlib import Path

from execution import config

logger = logging.getLogger(__name__)

_CSV_HEADER = [
    "trade_id",
    "signal_name",
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


class TradeLogger:
    """Persist execution results to CSV."""

    def __init__(
        self,
        csv_path: str | Path = "execution/logs/trades.csv",
        default_total_fee_rate: float = config.MAKER_FEE_RATE + config.MAKER_FEE_RATE,
        on_trade_complete: Callable | None = None,
    ) -> None:
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_total_fee_rate = default_total_fee_rate
        self._on_trade_complete = on_trade_complete
        self._lock = threading.Lock()
        self._trade_counter = 0
        self._init_csv()
        self._validate_csv_header()
        self._restore_counter()

    def _validate_csv_header(self) -> None:
        """If CSV exists but header does not match _CSV_HEADER, back it up and start fresh."""
        if not self.csv_path.exists():
            return
        try:
            with self.csv_path.open("r", encoding="utf-8") as f:
                first_line = f.readline().strip()
            expected = ",".join(_CSV_HEADER)
            if first_line != expected:
                from datetime import datetime as _dt

                backup = self.csv_path.with_name(
                    self.csv_path.stem
                    + f".bak_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
                    + self.csv_path.suffix
                )
                self.csv_path.rename(backup)
                logger.warning(
                    f"[TRADE_LOG] Stale CSV header detected. "
                    f"Backed up to {backup.name}, starting fresh."
                )
                self._init_csv()
        except Exception as exc:
            logger.warning(f"[TRADE_LOG] CSV header validation failed: {exc}")

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
                logger.info(f"[TRADE_LOG] Restored counter to {max_id} from existing CSV")
        except Exception as exc:
            logger.warning(f"[TRADE_LOG] Failed to restore counter: {exc}")

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
    ) -> None:
        self._write_row(
            {
                "signal_name": signal_name,
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
    ) -> None:
        gross_return_pct = self._compute_return_pct(direction, entry_price, exit_price)
        fee_rate = (
            self.default_total_fee_rate
            if total_fee_rate is None
            else float(total_fee_rate)
        )
        net_return_pct = gross_return_pct - (fee_rate * 100.0)

        self._write_row(
            {
                "signal_name": signal_name,
                "direction": direction,
                "entry_time": self._format_dt(entry_time),
                "entry_price": self._format_float(entry_price),
                "exit_time": self._format_dt(exit_time),
                "exit_price": self._format_float(exit_price)
                if exit_price is not None
                else "",
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
                        "signal_name": signal_name,
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
    def _compute_return_pct(
        direction: str, entry_price: float, exit_price: float | None
    ) -> float:
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
