"""Exit parameter loading and lookup helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict


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

    def to_dict(self) -> Dict[str, float | int]:
        return asdict(self)


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
        if not isinstance(payload, dict):
            continue
        try:
            base = result.get(key, ExitParams())
            result[key] = ExitParams(
                take_profit_pct=float(payload.get("take_profit_pct", base.take_profit_pct)),
                stop_pct=float(payload.get("stop_pct", base.stop_pct)),
                protect_start_pct=float(payload.get("protect_start_pct", base.protect_start_pct)),
                protect_gap_ratio=float(payload.get("protect_gap_ratio", base.protect_gap_ratio)),
                protect_floor_pct=float(payload.get("protect_floor_pct", base.protect_floor_pct)),
                min_hold_bars=int(payload.get("min_hold_bars", base.min_hold_bars)),
                max_hold_factor=int(payload.get("max_hold_factor", base.max_hold_factor)),
                exit_confirm_bars=int(payload.get("exit_confirm_bars", base.exit_confirm_bars)),
                decay_exit_threshold=float(payload.get("decay_exit_threshold", base.decay_exit_threshold)),
                decay_tighten_threshold=float(payload.get("decay_tighten_threshold", base.decay_tighten_threshold)),
                tighten_gap_ratio=float(payload.get("tighten_gap_ratio", base.tighten_gap_ratio)),
            )
        except Exception:
            continue
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
