"""Single source of truth for live strategies and execution coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


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


LIVE_STRATEGIES: tuple[LiveStrategySpec, ...] = (
    LiveStrategySpec(
        phase="P1",
        family="P0-2",
        label="funding_rate_arbitrage",
        directions=("long", "short"),
        execution_directions=("long", "short"),
        notes="Live via check_live(); both directions execution-whitelisted.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-2",
        label="vwap_twap_slicing",
        directions=("long",),
        execution_directions=("long",),
        notes="Live detector, execution-whitelisted, and exit params exist.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-6",
        label="bottom_volume_drought",
        directions=("long",),
        execution_directions=("long",),
        notes="First-line long family. Live detector bypasses fatigue and is trade-ready.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-8",
        label="vwap_vol_drought",
        directions=("long", "short"),
        execution_directions=("long", "short"),
        notes="Live detector with both long and short execution coverage.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-9",
        label="position_compression",
        directions=("long", "short"),
        execution_directions=("long",),
        notes="Live detector, but only the long side is execution-whitelisted.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-10",
        label="taker_exhaustion_low",
        directions=("long", "short"),
        execution_directions=("long",),
        notes="Live detector, but only the long side is execution-whitelisted.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="P1-11",
        label="high_pos_funding",
        directions=("short",),
        execution_directions=("short",),
        notes="Live detector, execution-whitelisted, and exit params exist.",
    ),
    LiveStrategySpec(
        phase="P1",
        family="C1",
        label="funding_cycle_oversold_long",
        directions=("long",),
        execution_directions=("long",),
        notes="Funding cycle oversold LONG. Smart exit configured and execution-ready.",
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
    ),
    LiveStrategySpec(
        phase="P1",
        family="RT-1",
        label="regime_transition_long",
        directions=("long",),
        execution_directions=("long",),
        notes="RANGE_BOUND->QUIET_TREND phase transition. Early trend entry.",
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
