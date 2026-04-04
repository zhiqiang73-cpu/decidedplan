"""Single source of truth for live strategies and execution coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


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
        execution_directions=("long",),
        notes="Live detector, but only the long side is execution-whitelisted.",
        oos_win_rate=88.0,
        mechanism_type="compression_release",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-10",
        label="taker_exhaustion_low",
        directions=("long", "short"),
        execution_directions=("long",),
        notes="Live detector, but only the long side is execution-whitelisted.",
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
        execution_directions=("short",),
        notes=(
            "Approved alpha card: dist_to_24h_high > -0.009746 + "
            "oi_change_rate_5m < 1.452e-05, with card Top-3 exit combos."
        ),
        uses_card_exit=True,
        oos_win_rate=75.0,
        mechanism_type="near_high_distribution",
    ),
    LiveStrategySpec(
        phase="P2",
        family="A2-29",
        label="high_proximity_wide_spread_short",
        directions=("short",),
        execution_directions=("short",),
        notes=(
            "Approved alpha card: dist_to_24h_high > -0.009746 + "
            "spread_vs_ma20 > 1.688, with card Top-3 exit combos."
        ),
        uses_card_exit=True,
        oos_win_rate=72.0,
        mechanism_type="near_high_distribution",
    ),
    LiveStrategySpec(
        phase="P2",
        family="A3-OI",
        label="oi_divergence_short",
        directions=("short",),
        execution_directions=("short",),
        notes=(
            "Approved alpha card: dist_to_24h_high > -0.005 + "
            "oi_change_rate_1h < -0.01 (price near high + OI falling = distribution). "
            "Mechanism: oi_divergence. OOS WR=80.7% n=57 PF=7.38. Card Top-3 exit combos."
        ),
        uses_card_exit=True,
        oos_win_rate=None,
        mechanism_type="oi_divergence",
    ),
    LiveStrategySpec(
        phase="P2",
        family="A4-PIR",
        label="position_high_oi_stall_short",
        directions=("short",),
        execution_directions=("short",),
        notes=(
            "Approved alpha card: position_in_range_4h > 0.7159 + "
            "oi_change_rate_1h < 7.4e-05 (high position + OI stall = distribution). "
            "OOS WR=68.75% n=32 PF=1.99. Card Top-3 exit combos."
        ),
        uses_card_exit=True,
        oos_win_rate=68.75,
        mechanism_type="oi_divergence",
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
)


EXECUTION_WHITELIST = frozenset(
    (spec.family, direction)
    for spec in LIVE_STRATEGIES
    for direction in spec.execution_directions
)


def live_strategy_families() -> tuple[str, ...]:
    return tuple(spec.family for spec in LIVE_STRATEGIES)


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
                "label": spec.label,
                "status": status,
                "live_wired": spec.live_wired,
                "directions": list(spec.directions),
                "execution_whitelist": whitelisted,
                "exit_params": exit_ready,
                "trade_ready": trade_ready,
                "notes": notes,
            }
        )

    return rows
