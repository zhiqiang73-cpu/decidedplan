"""Execution layer configuration."""

from __future__ import annotations

import os
from pathlib import Path

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

TESTNET = True
SYMBOL = "BTCUSDT"
LEVERAGE = 10
POSITION_PCT = 0.08
MAX_POSITIONS = 5
MIN_CONFIDENCE = 2

# Entry execution defaults to a passive maker attempt first, then one capped
# aggressive IOC retry. This matches the live execution path and the execution
# layer tests; if you want pure maker-only behavior, override via config.
ENTRY_TIMEOUT_S = 12
ENTRY_RETRY_TIMEOUT_S = 2
ENTRY_MAX_ATTEMPTS = 2
ENTRY_FINAL_CROSS_TICKS = 2
ENTRY_MAKER_ONLY = False

CLOSE_LIMIT_TIMEOUT_S = 45
ORDER_POLL_INTERVAL_S = 2.0
MAKER_FEE_RATE = 0.0002
TAKER_FEE_RATE = 0.0005
HTTP_TIMEOUT_S = 10.0

API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()
ENABLED = bool(API_KEY and API_SECRET)


def fee_rate_for_type(fee_type: str | None) -> float:
    fee_type = str(fee_type or "").lower()
    if fee_type == "maker":
        return MAKER_FEE_RATE
    if fee_type == "taker":
        return TAKER_FEE_RATE
    return TAKER_FEE_RATE

