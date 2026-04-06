"""Exit parameter loading and lookup helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict


CORE_EXIT_FAMILIES = (
    "P0-2",
    "P1-2",
    "P1-6",
    "P1-8",
    "P1-9",
    "P1-10",
    "P1-11",
    "C1",
)

FAMILY_MIN_HOLD_CAPS = {
    "P0-2": 6,
    "P1-2": 12,
    "P1-6": 20,
    "P1-8": 24,
    "P1-9": 24,
    "P1-10": 20,
    "P1-11": 24,
    "C1": 30,
}

BEST_PARAMS_PATH = Path("monitor/output/exit_policy_best_params.json")


@dataclass(frozen=True)
class ExitParams:
    take_profit_pct: float = 0.0
    stop_pct: float = 0.70
    protect_start_pct: float = 0.12
    protect_gap_ratio: float = 0.50
    protect_floor_pct: float = 0.03
    min_hold_bars: int = 3
    max_hold_factor: int = 4
    exit_confirm_bars: int = 2
    decay_exit_threshold: float = 0.85
    decay_tighten_threshold: float = 0.5
    tighten_gap_ratio: float = 0.30
    # Adaptive stop multipliers
    confidence_stop_multipliers: dict = field(default_factory=lambda: {1: 0.7, 2: 1.0, 3: 1.3})
    regime_stop_multipliers: dict = field(default_factory=lambda: {
        "QUIET_TREND": 0.8,
        "RANGE_BOUND": 1.0,
        "VOLATILE_TREND": 1.5,
        "VOL_EXPANSION": 1.5,
        "CRISIS": 0.5,
    })
    mfe_ratchet_threshold: float = 0.15
    mfe_ratchet_ratio: float = 0.4

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _coerce_float(value: object, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_multiplier_map(
    payload: object,
    *,
    base: dict,
    int_keys: bool = False,
) -> dict:
    if not isinstance(payload, dict):
        return dict(base)

    normalized: dict = {}
    for raw_key, raw_value in payload.items():
        try:
            key = int(float(raw_key)) if int_keys else str(raw_key)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        normalized[key] = value
    return normalized or dict(base)


def build_exit_params(payload: Any, base: ExitParams | None = None) -> ExitParams | None:
    if not isinstance(payload, dict):
        return None

    base = base or ExitParams()
    try:
        return ExitParams(
            take_profit_pct=_coerce_float(payload.get("take_profit_pct"), base.take_profit_pct),
            stop_pct=_coerce_float(payload.get("stop_pct"), base.stop_pct),
            protect_start_pct=_coerce_float(payload.get("protect_start_pct"), base.protect_start_pct),
            protect_gap_ratio=_coerce_float(payload.get("protect_gap_ratio"), base.protect_gap_ratio),
            protect_floor_pct=_coerce_float(payload.get("protect_floor_pct"), base.protect_floor_pct),
            min_hold_bars=_coerce_int(payload.get("min_hold_bars"), base.min_hold_bars),
            max_hold_factor=_coerce_int(payload.get("max_hold_factor"), base.max_hold_factor),
            exit_confirm_bars=_coerce_int(payload.get("exit_confirm_bars"), base.exit_confirm_bars),
            decay_exit_threshold=_coerce_float(payload.get("decay_exit_threshold"), base.decay_exit_threshold),
            decay_tighten_threshold=_coerce_float(payload.get("decay_tighten_threshold"), base.decay_tighten_threshold),
            tighten_gap_ratio=_coerce_float(payload.get("tighten_gap_ratio"), base.tighten_gap_ratio),
            confidence_stop_multipliers=_coerce_multiplier_map(
                payload.get("confidence_stop_multipliers"),
                base=base.confidence_stop_multipliers,
                int_keys=True,
            ),
            regime_stop_multipliers=_coerce_multiplier_map(
                payload.get("regime_stop_multipliers"),
                base=base.regime_stop_multipliers,
            ),
            mfe_ratchet_threshold=_coerce_float(payload.get("mfe_ratchet_threshold"), base.mfe_ratchet_threshold),
            mfe_ratchet_ratio=_coerce_float(payload.get("mfe_ratchet_ratio"), base.mfe_ratchet_ratio),
        )
    except Exception:
        return None


DEFAULT_EXIT_PARAMS: Dict[str, ExitParams] = {
    family: ExitParams() for family in CORE_EXIT_FAMILIES
}


def resolve_max_hold_bars(family: str, base_horizon: int, params: ExitParams) -> int:
    family_cap = FAMILY_MIN_HOLD_CAPS.get(family, max(6, base_horizon * 2))
    scaled_cap = max(base_horizon * params.max_hold_factor, params.min_hold_bars + 1)
    return max(family_cap, scaled_cap)


def _load_raw_params() -> dict:
    if not BEST_PARAMS_PATH.exists():
        return {}
    try:
        return json.loads(BEST_PARAMS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def load_best_exit_params() -> Dict[str, ExitParams]:
    raw = _load_raw_params()
    if not raw:
        return dict(DEFAULT_EXIT_PARAMS)

    result = dict(DEFAULT_EXIT_PARAMS)
    for key, payload in raw.items():
        params = build_exit_params(payload, base=result.get(key, ExitParams()))
        if params is not None:
            result[key] = params
    return result


def resolve_exit_params_key(family: str, direction: str | None = None) -> str:
    direction_text = str(direction or "").lower()
    if direction_text in {"long", "short"}:
        return f"{family}|{direction_text}"
    return family


def get_exit_params_for_signal(
    family: str,
    direction: str | None = None,
    params_map: Dict[str, ExitParams] | None = None,
) -> ExitParams:
    params_map = params_map or load_best_exit_params()
    direction_key = resolve_exit_params_key(family, direction)
    if direction_key in params_map:
        return params_map[direction_key]
    if family in params_map:
        return params_map[family]
    return ExitParams()


def has_explicit_exit_params(family: str, direction: str | None = None) -> bool:
    raw = _load_raw_params()
    if not raw:
        return False
    direction_key = resolve_exit_params_key(family, direction)
    return direction_key in raw or family in raw


def save_exit_params(key: str, params: ExitParams) -> None:
    """Merge a single family|direction entry into best_params.json (atomic write)."""
    raw = _load_raw_params()
    raw[key] = params.to_dict()
    BEST_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BEST_PARAMS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=4), encoding="utf-8")
    tmp.replace(BEST_PARAMS_PATH)
