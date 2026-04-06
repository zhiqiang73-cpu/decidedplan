"""
P1-6: bottom volume drought long.

This is the first-line long signal family:
price is already near the 24h floor and volume has dried up.
Thresholds are frozen from the 67%/33% train-test scan on
2024-10-01 ~ 2026-03-16.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)

COOLDOWN_BARS = 30

_VARIANTS = (
    {
        "name": "low_p2_vol_p3",
        "price_col": "dist_to_24h_low",
        "price_max": 0.0010988174,
        "confirm_col": "volume_vs_ma20",
        "confirm_max": 0.2411594689,
    },
    {
        "name": "low_p2_vol_p5",
        "price_col": "dist_to_24h_low",
        "price_max": 0.0010988174,
        "confirm_col": "volume_vs_ma20",
        "confirm_max": 0.2797883451,
    },
    {
        "name": "range_p5_vol_p1",
        "price_col": "position_in_range_24h",
        "price_max": 0.0881837457,
        "confirm_col": "volume_vs_ma20",
        "confirm_max": 0.1799293607,
    },
    {
        "name": "range_p3_vol_p2",
        "price_col": "position_in_range_24h",
        "price_max": 0.0585962422,
        "confirm_col": "volume_vs_ma20",
        "confirm_max": 0.2155294865,
    },
)


class BottomVolumeDroughtDetector(SignalDetector):
    name = "P1-6_bottom_volume_drought"
    direction = "long"
    hold_bars = 30
    required_columns = ["dist_to_24h_low", "position_in_range_24h", "volume_vs_ma20"]

    def detect(self, df: pd.DataFrame) -> pd.Series:
        result = pd.Series(False, index=df.index)
        if not self.validate_columns(df):
            return result

        union_mask, _ = self._union_mask(df)
        return self._apply_cooldown(union_mask)

    def check_live(self, df: pd.DataFrame) -> dict | None:
        if df is None or df.empty:
            return None
        if not self.validate_columns(df):
            return None

        _, matched = self._union_mask(df)
        if not matched:
            return None

        latest = df.iloc[-1]
        latest_ts = int(latest.get("timestamp", 0))
        dist_low = float(latest["dist_to_24h_low"])
        range_pos = float(latest["position_in_range_24h"])
        vol_ratio = float(latest["volume_vs_ma20"])
        variant_names = ",".join(matched)

        logger.info(
            "[BOTTOM VOL DROUGHT] LONG | variants=%s | dist_low=%.5f | "
            "range24h=%.4f | vol=%.3f",
            variant_names,
            dist_low,
            range_pos,
            vol_ratio,
        )

        return {
            "phase": "P1",
            "name": self.name,
            "direction": self.direction,
            "horizon": self.hold_bars,
            "timestamp_ms": latest_ts,
            "desc": (
                f"[{self.name}] seller exhaustion rebound "
                f"(variants={variant_names}, vol={vol_ratio:.3f})"
            ),
            "confidence": 2,
            "confidence_label": "MEDIUM",
            "apply_fatigue": False,
            "feature": "volume_vs_ma20",
            "feature_value": vol_ratio,
            "variant": variant_names,
        }

    @staticmethod
    def _apply_cooldown(mask: pd.Series) -> pd.Series:
        result = pd.Series(False, index=mask.index)
        last_trigger = -COOLDOWN_BARS - 1
        for idx in np.flatnonzero(mask.to_numpy()):
            if idx - last_trigger < COOLDOWN_BARS:
                continue
            result.iloc[idx] = True
            last_trigger = idx
        return result

    def _union_mask(self, df: pd.DataFrame) -> tuple[pd.Series, list[str]]:
        union = pd.Series(False, index=df.index)
        matched: list[str] = []
        for spec in _VARIANTS:
            mask = self._build_variant_mask(df, spec)
            union |= mask
            if bool(mask.iloc[-1]):
                matched.append(spec["name"])
        return union, matched

    @staticmethod
    def _build_variant_mask(df: pd.DataFrame, spec: dict) -> pd.Series:
        return (
            df[spec["price_col"]].notna()
            & df[spec["confirm_col"]].notna()
            & (df[spec["price_col"]] <= spec["price_max"])
            & (df[spec["confirm_col"]] <= spec["confirm_max"])
        )
