"""Single source of truth for live strategies and execution coverage."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Optional

from monitor.strategy_contracts import validate_live_specs


@dataclass(frozen=True)
class LiveStrategySpec:
    phase: str
    family: str
    label: str
    directions: tuple[str, ...]
    execution_directions: tuple[str, ...]
    notes: str
    uses_card_exit: bool = False
    live_wired: bool = True
    oos_win_rate: Optional[float] = None
    mechanism_type: str = ""
    exit_contract: tuple[str, ...] = ("vs_entry", "mechanism_decay")


LIVE_STRATEGIES: tuple[LiveStrategySpec, ...] = (
    LiveStrategySpec(
        phase="P1",
        family="P0-2",
        label="funding_rate_arbitrage",
        directions=("long", "short"),
        execution_directions=("long", "short"),
        notes="Live via check_live(); both directions execution-whitelisted.",
        oos_win_rate=67.0,
        mechanism_type="funding_settlement",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-2",
        label="vwap_twap_slicing",
        directions=("long",),
        execution_directions=("long",),
        notes="Live detector, execution-whitelisted, and exit params exist.",
        oos_win_rate=None,
        mechanism_type="algo_slicing",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-6",
        label="bottom_volume_drought",
        directions=("long",),
        execution_directions=("long",),
        notes="First-line long family. Live detector bypasses fatigue and is trade-ready.",
        oos_win_rate=54.0,
        mechanism_type="seller_drought",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-8",
        label="vwap_vol_drought",
        directions=("long", "short"),
        execution_directions=("long", "short"),
        notes="Live detector with both long and short execution coverage.",
        oos_win_rate=93.5,
        mechanism_type="vwap_reversion",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-9",
        label="position_compression",
        directions=("long", "short"),
        execution_directions=("long", "short"),
        notes="Live detector, both directions execution-whitelisted. SHORT OOS=86-87%.",
        oos_win_rate=88.0,
        mechanism_type="compression_release",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-10",
        label="taker_exhaustion_low",
        directions=("long", "short"),
        execution_directions=("long", "short"),
        notes="Live detector, both directions execution-whitelisted. SHORT-D OOS=80%.",
        oos_win_rate=78.5,
        mechanism_type="bottom_taker_exhaust",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-11",
        label="high_pos_funding",
        directions=("short",),
        execution_directions=("short",),
        notes="Live detector, execution-whitelisted, and exit params exist.",
        oos_win_rate=80.4,
        mechanism_type="funding_divergence",
    ),
    LiveStrategySpec(
        phase="P1",
        family="C1",
        label="funding_cycle_oversold_long",
        directions=("long",),
        execution_directions=("long",),
        notes="Funding cycle oversold LONG. Smart exit configured and execution-ready.",
        oos_win_rate=None,
        mechanism_type="funding_cycle_oversold",
    ),
    LiveStrategySpec(
        phase="P2",
        family="A2-26",
        label="high_proximity_oi_cooldown_short",
        directions=("short",),
        execution_directions=(),  # SUSPENDED 2026-04-10: 旧审批流程不合格，等新引擎重新挖掘
        notes="SUSPENDED: 旧Alpha管道产出，缺少持续性检测/方向性验证/因果出场。等新引擎重新挖掘验证后再上线。",
        uses_card_exit=True,
        oos_win_rate=75.0,
        mechanism_type="near_high_distribution",
        exit_contract=("vs_entry",),
    ),
    LiveStrategySpec(
        phase="P2",
        family="A2-29",
        label="high_proximity_wide_spread_short",
        directions=("short",),
        execution_directions=(),  # SUSPENDED: live WR=30%, spread confirmation non-directional
        notes="SUSPENDED: spread_vs_ma20 confirmation lacks directional edge.",
        uses_card_exit=True,
        oos_win_rate=72.0,
        mechanism_type="near_high_distribution",
        exit_contract=("vs_entry",),
    ),
    LiveStrategySpec(
        phase="P2",
        family="A3-OI",
        label="oi_divergence_short",
        directions=("short",),
        execution_directions=(),  # SUSPENDED: 仅1笔交易，巨亏-0.69%
        notes="SUSPENDED: 旧Alpha管道产出，等新引擎重新挖掘。",
        uses_card_exit=True,
        oos_win_rate=None,
        mechanism_type="oi_divergence",
        exit_contract=("vs_entry",),
    ),
    LiveStrategySpec(
        phase="P2",
        family="A4-PIR",
        label="position_high_oi_stall_short",
        directions=("short",),
        execution_directions=(),  # SUSPENDED: WR=52.6%, 基本持平
        notes="SUSPENDED: 旧Alpha管道产出，等新引擎重新挖掘。",
        uses_card_exit=True,
        oos_win_rate=68.75,
        mechanism_type="oi_divergence",
        exit_contract=("vs_entry",),
    ),
    LiveStrategySpec(
        phase="P1",
        family="RT-1",
        label="regime_transition_long",
        directions=("long",),
        execution_directions=("long",),
        notes="RANGE_BOUND->QUIET_TREND phase transition. Early trend entry.",
        oos_win_rate=None,
        mechanism_type="regime_transition",
    ),
    LiveStrategySpec(
        phase="P1",
        family="OA-1",
        label="oi_accumulation_long",
        directions=("long",),
        execution_directions=("long",),
        notes="OI accumulation LONG: TREND_UP + OI growth + buyer dominant. "
              "OOS: WR=75% n=8 PF=8.91 (MA5 smoothed decay exit). A3-OI mirror.",
        mechanism_type="oi_accumulation_long",
    ),
)

_SPEC_BY_FAMILY: dict[str, LiveStrategySpec] = {
    spec.family: spec for spec in LIVE_STRATEGIES
}
_KNOWN_FAMILY_PREFIXES: tuple[str, ...] = tuple(
    sorted(_SPEC_BY_FAMILY.keys(), key=len, reverse=True)
)
_APPROVED_RULES_PATH = Path(__file__).resolve().parent.parent / "alpha" / "output" / "approved_rules.json"
_approved_card_family_map: dict[str, str] = {}
_approved_card_family_mtime: float = -1.0


EXECUTION_WHITELIST = frozenset(
    (spec.family, direction)
    for spec in LIVE_STRATEGIES
    for direction in spec.execution_directions
)


def get_live_strategy_spec(family: str) -> LiveStrategySpec | None:
    return _SPEC_BY_FAMILY.get(str(family or "").strip())


def canonical_strategy_id(family: str) -> str:
    return str(family or "").strip()


def canonical_signal_name(family: str) -> str:
    family_text = canonical_strategy_id(family)
    spec = get_live_strategy_spec(family_text)
    if spec is None or not spec.label:
        return family_text
    return f"{family_text}_{spec.label}"


def _load_approved_card_family_map() -> dict[str, str]:
    global _approved_card_family_map, _approved_card_family_mtime

    try:
        mtime = _APPROVED_RULES_PATH.stat().st_mtime
    except OSError:
        _approved_card_family_map = {}
        _approved_card_family_mtime = -1.0
        return _approved_card_family_map

    if mtime == _approved_card_family_mtime:
        return _approved_card_family_map

    try:
        approved = json.loads(_APPROVED_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        _approved_card_family_map = {}
        _approved_card_family_mtime = mtime
        return _approved_card_family_map

    mapping: dict[str, str] = {}
    if isinstance(approved, list):
        for card in approved:
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("id") or "").strip()
            family = canonical_strategy_id(str(card.get("family") or ""))
            if card_id and family:
                mapping[card_id] = family

    _approved_card_family_map = mapping
    _approved_card_family_mtime = mtime
    return _approved_card_family_map


def _resolve_single_family_from_token(token: str) -> str:
    token_text = str(token or "").strip()
    if not token_text:
        return ""

    if token_text in _SPEC_BY_FAMILY:
        return token_text

    for family in _KNOWN_FAMILY_PREFIXES:
        if not token_text.startswith(family):
            continue
        if len(token_text) == len(family):
            return family
        next_char = token_text[len(family)]
        if next_char in {"_", " ", "|"}:
            return family

    return _load_approved_card_family_map().get(token_text, "")


def resolve_strategy_id_from_signal_name(signal_name: str, family: str = "") -> str:
    family_text = canonical_strategy_id(family)
    if family_text:
        return family_text

    signal_text = str(signal_name or "").strip()
    if not signal_text:
        return ""

    parts = [part.strip() for part in signal_text.split("|") if part.strip()]
    if not parts:
        parts = [signal_text]

    resolved: list[str] = []
    seen: set[str] = set()
    for part in parts:
        resolved_family = _resolve_single_family_from_token(part)
        if not resolved_family or resolved_family in seen:
            continue
        seen.add(resolved_family)
        resolved.append(resolved_family)

    if resolved:
        return " | ".join(resolved)
    return signal_text


def resolve_logged_signal_name(signal_name: str, family: str = "") -> str:
    strategy_id = resolve_strategy_id_from_signal_name(signal_name, family=family)
    return strategy_id or str(signal_name or "").strip()


def live_strategy_families() -> tuple[str, ...]:
    return tuple(spec.family for spec in LIVE_STRATEGIES)


def validate_live_strategy_specs() -> list[str]:
    return validate_live_specs(LIVE_STRATEGIES)


def build_strategy_status_rows(
    has_exit_params: Callable[[str, str], bool],
) -> list[dict]:
    rows: list[dict] = []

    for spec in LIVE_STRATEGIES:
        directions = [d for d in spec.directions if d in {"long", "short"}]
        whitelisted = [
            d for d in directions if (spec.family, d) in EXECUTION_WHITELIST
        ]
        if spec.uses_card_exit:
            exit_ready = list(directions)
        else:
            exit_ready = [d for d in directions if has_exit_params(spec.family, d)]
        trade_ready = [d for d in whitelisted if d in exit_ready]

        if directions and set(trade_ready) == set(directions):
            status = "trade_ready"
        elif trade_ready:
            status = "partial_trade_ready"
        else:
            status = "wired_monitor_only"

        notes = spec.notes
        if directions and not whitelisted:
            notes = f"{notes} Not in execution whitelist."
        elif whitelisted:
            missing_exit = [d for d in whitelisted if d not in exit_ready]
            if missing_exit:
                notes = f"{notes} Missing exit params for: {','.join(missing_exit)}."

        rows.append(
            {
                "phase": spec.phase,
                "family": spec.family,
                "strategy_id": spec.family,
                "label": spec.label,
                "canonical_signal_name": canonical_signal_name(spec.family),
                "status": status,
                "live_wired": spec.live_wired,
                "directions": list(spec.directions),
                "execution_whitelist": whitelisted,
                "exit_params": exit_ready,
                "trade_ready": trade_ready,
                "exit_contract": list(spec.exit_contract),
                "notes": notes,
            }
        )

    return rows
