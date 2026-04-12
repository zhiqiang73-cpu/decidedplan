"""
P1-RT: Regime Transition Detector (RegimeTransitionDetector)

Physical hypothesis:
  Market just transitioned from RANGE_BOUND to QUIET_TREND = phase transition.
  Early trend entry before most participants recognize the new regime.
  Once consensus forms, momentum is directional.

Entry conditions:
  1. Regime confirmed change: RANGE_BOUND -> QUIET_TREND (3-bar CONFIRM_BARS)
  2. volume_vs_ma20 > 1.1  (volume picking up = breakout has flow)
  3. position_in_range_24h > 0.60 (price breaking upper range = LONG confirmed)

Cooldown: 120 bars (2 hours minimum between signals)
"""
from __future__ import annotations
import logging
from typing import Optional
import pandas as pd
from signals.base import SignalDetector

logger = logging.getLogger(__name__)

# ── Regime constants (mirrored from monitor/regime_detector.py) ──────────────
QUIET_TREND    = "QUIET_TREND"
VOLATILE_TREND = "VOLATILE_TREND"
RANGE_BOUND    = "RANGE_BOUND"
VOL_EXPANSION  = "VOL_EXPANSION"
CRISIS         = "CRISIS"

AMP_QUIET_MAX   = 0.0015
AMP_VOLATILE    = 0.0025
AMP_CRISIS      = 0.0050
VOL_SPIKE       = 2.0
VOL_EXTREME     = 3.0
SPREAD_WIDE     = 2.0
SPREAD_CRISIS   = 3.5
OI_DELEVER      = -0.03
RANGE_CENTER_LOW  = 0.30
RANGE_CENTER_HIGH = 0.70
CONFIRM_BARS    = 3
COOLDOWN_BARS   = 60


def _safe_get(row, col, default=None):
    if col not in row.index: return default
    val = row[col]
    try:
        import math
        if math.isnan(float(val)): return default
    except (TypeError, ValueError):
        return default
    return float(val)


def _classify_raw(row) -> str:
    """Classify raw regime for a single bar (mirrors RegimeDetector._classify_raw)."""
    amp    = _safe_get(row, "amplitude_ma20",       default=0.001)
    vol    = _safe_get(row, "volume_vs_ma20",       default=1.0)
    spread = _safe_get(row, "spread_vs_ma20",       default=1.0)
    oi_1h  = _safe_get(row, "oi_change_rate_1h",    default=0.0)
    rpos   = _safe_get(row, "position_in_range_24h",default=0.5)
    if spread > SPREAD_CRISIS: return CRISIS
    if oi_1h is not None and oi_1h < OI_DELEVER and amp > AMP_VOLATILE: return CRISIS
    if amp > AMP_CRISIS and vol > VOL_EXTREME: return VOL_EXPANSION
    if amp > AMP_VOLATILE and vol > VOL_SPIKE and spread > SPREAD_WIDE: return VOL_EXPANSION
    if RANGE_CENTER_LOW <= (rpos or 0.5) <= RANGE_CENTER_HIGH and amp < AMP_VOLATILE:
        return RANGE_BOUND
    if amp > AMP_VOLATILE: return VOLATILE_TREND
    return QUIET_TREND


class RegimeTransitionDetector(SignalDetector):
    """
    Detects RANGE_BOUND -> QUIET_TREND regime transitions as LONG entry signals.

    Internal state:
      _prev_confirmed: last confirmed regime
      _candidate:      candidate raw regime being accumulated
      _candidate_cnt:  consecutive bars of candidate regime
      _cooldown:       bars until next signal allowed (decremented each check_live call)
    """
    name             = "P1-RT_regime_transition"
    direction        = "long"
    research_horizon_bars = 30
    hold_bars        = research_horizon_bars
    required_columns = [
        "amplitude_ma20", "volume_vs_ma20", "spread_vs_ma20",
        "oi_change_rate_1h", "position_in_range_24h", "timestamp",
    ]

    def __init__(self):
        self._prev_confirmed: str = QUIET_TREND   # start neutral
        self._candidate:      str = QUIET_TREND
        self._candidate_cnt:  int = 0
        self._cooldown:       int = 0

    def detect(self, df: pd.DataFrame) -> pd.Series:
        """Batch detect not implemented (live-only signal)."""
        return pd.Series(False, index=df.index)

    def check_live(self, df: pd.DataFrame) -> Optional[dict]:
        """
        Called on every new 1-min bar. Maintains internal regime history
        and fires a signal on confirmed RANGE_BOUND -> QUIET_TREND transitions
        when additional volume/position conditions are met.
        """
        if df is None or df.empty:
            return None
        if not self.validate_columns(df):
            return None

        row = df.iloc[-1]
        raw = _classify_raw(row)

        # ── Update CONFIRM_BARS counter ──────────────────────────────────────
        if raw == self._candidate:
            self._candidate_cnt += 1
        else:
            self._candidate     = raw
            self._candidate_cnt = 1

        # Confirmed regime requires CONFIRM_BARS consecutive identical raw bars
        confirmed = None
        if self._candidate_cnt >= CONFIRM_BARS:
            confirmed = self._candidate

        # ── Decrement cooldown ───────────────────────────────────────────────
        if self._cooldown > 0:
            self._cooldown -= 1

        # ── Check transition condition ───────────────────────────────────────
        if (confirmed is not None
                and self._prev_confirmed == RANGE_BOUND
                and confirmed == QUIET_TREND):
            # Update confirmed regime before checking signal
            self._prev_confirmed = confirmed

            # Additional confirmation conditions
            vol       = _safe_get(row, "volume_vs_ma20",       default=1.0)
            range_pos = _safe_get(row, "position_in_range_24h",default=0.5)

            if vol is None or range_pos is None:
                logger.debug("[RT] Transition detected but vol/range_pos unavailable")
                return None

            if vol <= 1.1:
                logger.debug("[RT] Transition detected but volume_vs_ma20=%.3f <= 1.1 (no flow)", vol)
                return None
            if range_pos <= 0.60:
                logger.debug("[RT] Transition detected but range_pos=%.3f <= 0.60 (not upper break)", range_pos)
                return None

            # Check cooldown
            if self._cooldown > 0:
                logger.debug("[RT] Transition detected but cooldown=%d remaining", self._cooldown)
                return None

            # Fire signal
            self._cooldown = COOLDOWN_BARS
            ts_ms = int(_safe_get(row, "timestamp", default=0) or 0)
            research_horizon = self.resolved_research_horizon_bars()
            logger.info(
                "[P1-RT] RANGE->TREND transition: vol=%.3f range_pos=%.3f", vol, range_pos
            )
            return {
                "phase":         "P1",
                "name":          self.name,
                "direction":     "long",
                "horizon":       research_horizon,
                "research_horizon_bars": research_horizon,
                "timestamp_ms":  ts_ms,
                "desc":          (
                    f"RANGE->TREND transition: momentum consensus forming "
                    f"(vol={vol:.3f}, pos={range_pos:.3f})"
                ),
                "feature":       "position_in_range_24h",
                "feature_value": float(range_pos),
                "threshold":     0.60,
                "op":            ">",
                "group":         "regime_transition",
                "family":        "RT-1",
                "confidence":    2,
            }

        # Update confirmed regime state even when no signal fires
        if confirmed is not None and confirmed != self._prev_confirmed:
            self._prev_confirmed = confirmed

        return None
