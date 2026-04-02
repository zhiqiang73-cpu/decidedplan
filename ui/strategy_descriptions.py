"""12 live/UI strategy descriptions used by the dashboard and system_state.json."""

from __future__ import annotations

STRATEGY_ZH: dict[str, dict[str, str]] = {
    "P0-2": {
        "name": "Funding Rate Arbitrage",
        "direction": "short",
        "entry_zh": (
            "Short when funding rate is above 0.01%.\n"
            "Physical logic: longs keep paying shorts, so carry traders can lock in funding income."
        ),
        "exit_zh": (
            "Stop: 1.50% | Exit when funding normalizes.\n"
            "Profit protect starts at 0.40%."
        ),
    },
    "P1-2": {
        "name": "VWAP/TWAP Slicing",
        "direction": "long",
        "entry_zh": (
            "Current code requires all 3 footprints together: volume_autocorr_lag5 > 0.55, "
            "key-minute volume_vs_ma20 > 1.5, and avg_trade_size_cv_10m < 0.30.\n"
            "Physical logic: sliced algo flow only counts when rhythm, timing, and uniformity align."
        ),
        "exit_zh": (
            "Stop: 0.30% | Exit when the slicing rhythm fades.\n"
            "Profit protect starts at 0.10%."
        ),
    },
    "P1-6": {
        "name": "Bottom Volume Drought",
        "direction": "long",
        "entry_zh": (
            "Price sits near the 24h low while volume dries below average.\n"
            "Physical logic: sellers stop pressing and bounce odds rise."
        ),
        "exit_zh": (
            "Stop: 0.70% | Exit when volume comes back or price lifts away from the low.\n"
            "Profit protect starts at 0.20%."
        ),
    },
    "P1-8": {
        "name": "VWAP Deviation + Volume Drought",
        "direction": "both",
        "entry_zh": (
            "LONG: below VWAP with persistent volume drought. SHORT: above VWAP with persistent volume drought.\n"
            "Physical logic: price is stretched away from balance but no follow-through flow is left."
        ),
        "exit_zh": (
            "LONG stop 1.50% | SHORT stop 1.00%.\n"
            "Exit when VWAP deviation closes or volume normalizes."
        ),
    },
    "P1-9": {
        "name": "Position Compression",
        "direction": "long",
        "entry_zh": (
            "Open interest keeps falling while price compresses.\n"
            "Physical logic: shorts leave, the base gets cleaner, and breakout odds improve."
        ),
        "exit_zh": (
            "Stop: 0.30% | Exit when OI turns back up or price breaks structure.\n"
            "Profit protect starts at 0.10%."
        ),
    },
    "P1-10": {
        "name": "Taker Exhaustion Bottom",
        "direction": "long",
        "entry_zh": (
            "Taker selling pressure is extremely low while price sits near the 24h low.\n"
            "Physical logic: aggressive sellers run out of ammo and price starts to bottom."
        ),
        "exit_zh": (
            "Stop: 1.00% | Exit when taker ratio recovers or price leaves the bottom zone.\n"
            "Profit protect starts at 0.30%."
        ),
    },
    "P1-11": {
        "name": "High Position + Negative Funding",
        "direction": "short",
        "entry_zh": (
            "Price is high in the 4h range while funding is negative.\n"
            "Physical logic: shorts stay pressed even at elevation, so downside continuation odds stay high."
        ),
        "exit_zh": (
            "Stop: 1.50% | Exit when price leaves the high zone or funding flips.\n"
            "Profit protect starts at 0.40%."
        ),
    },
    "C1": {
        "name": "Funding Window Oversold Long",
        "direction": "long",
        "entry_zh": (
            "Price is near the 24h low while funding is negative.\n"
            "Physical logic: shorts keep paying and can be forced to cover after an oversold flush."
        ),
        "exit_zh": (
            "Stop: 0.70% | Exit when volatility expands or market state normalizes.\n"
            "Minimum hold 30 bars; max hold uses max(base_horizon * 4, family_cap), usually about 120 bars on the current live chain."
        ),
    },
    "A2-26": {
        "name": "High Proximity + OI Cooldown Short",
        "direction": "short",
        "entry_zh": (
            "Price stays close to the 24h high (dist_to_24h_high > -0.009746) while "
            "5m OI change is near-flat to down (oi_change_rate_5m < 1.452e-05).\n"
            "Physical logic: fresh long leverage stops adding near highs, so upside push weakens and pullback odds rise."
        ),
        "exit_zh": (
            "Stop: 0.25%.\n"
            "Exit follows alpha card Top-3 exit combos + mechanism decay; max hold is dynamic (typically up to 240 bars for 60-bar base horizon)."
        ),
    },
    "A2-29": {
        "name": "High Proximity + Wide Spread Short",
        "direction": "short",
        "entry_zh": (
            "Price stays close to the 24h high (dist_to_24h_high > -0.009746) and "
            "micro spread stays elevated (spread_vs_ma20 > 1.688).\n"
            "Physical logic: orderbook gets thinner at the top, making continuation fragile and mean-reversion shorts more favorable."
        ),
        "exit_zh": (
            "Stop: 0.20%.\n"
            "Exit follows alpha card Top-3 exit combos + mechanism decay; max hold is dynamic (typically up to 240 bars for 60-bar base horizon)."
        ),
    },
    "A3-OI": {
        "name": "OI Divergence Short",
        "direction": "short",
        "entry_zh": (
            "Price stays very close to the 24h high while 1h open interest is clearly falling.\n"
            "Physical logic: price still hangs high, but leverage is leaving underneath it, so the top is being distributed instead of strengthened."
        ),
        "exit_zh": (
            "Stop: 0.30%.\n"
            "Exit follows alpha card Top-3 exit combos + mechanism decay; this family tracks oi_divergence and leaves when the divergence finishes or price structure breaks."
        ),
    },
    "RT-1": {
        "name": "Regime Transition Long",
        "direction": "long",
        "entry_zh": (
            "Confirmed RANGE_BOUND -> QUIET_TREND transition, with volume_vs_ma20 > 1.1 "
            "and position_in_range_24h > 0.60.\n"
            "Physical logic: the market just leaves a sideways box, fresh directional flow appears, and the early trend starts forming."
        ),
        "exit_zh": (
            "Stop: 1.00% | Exit when the new trend loses confirmation or price falls back into the old range.\n"
            "Profit protect starts at 0.30%. Minimum hold 10 bars; dynamic max hold uses max(base_horizon * 4, family cap)."
        ),
    },
}


def get_strategy_info(family: str) -> dict[str, str]:
    """Return UI strategy metadata with a safe fallback."""
    return STRATEGY_ZH.get(
        family,
        {
            "name": family,
            "direction": "unknown",
            "entry_zh": "Entry description is not configured.",
            "exit_zh": "Exit description is not configured.",
        },
    )



