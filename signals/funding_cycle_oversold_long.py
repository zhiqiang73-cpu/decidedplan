"""
C1: funding cycle oversold long.

Physical logic:
  Negative or near-zero funding rate = shorts are paying (or market is neutral),
  combined with price near 24h low or significantly below VWAP.
  Under these conditions algorithmic participants reduce short exposure near
  funding collection time, providing a mean-reversion tailwind for longs.

Thresholds reflect OOS-validated entry conditions from 2024-10-01 ~ 2026-03-16.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)

COOLDOWN_BARS = 40

# 价格斜率守卫：连续下降趋势中不开多单
# 物理依据：funding 超卖 + 价格在下跌 ≠ 超卖反弹；每一个新低都会触发信号但不会反弹
# 实盘教训（2026-04-15）：BTC 从74274跌至73945期间，C1 连续触发4次多单全部亏损
_SLOPE_GUARD_LOOKBACK = 60   # 用近60根K线斜率判断是否在下降中（20根太短，多小时下跌中会反复翻正）
_SLOPE_GUARD_MIN = 0.0       # 斜率 <= 0 = 下降，阻止 C1 触发

# 2 core price-based variants + 2 VWAP-based variants
# All require negative or zero funding as physical confirmation
_VARIANTS = (
    {
        "name": "low_funding_neg_A",
        "price_col": "dist_to_24h_low",
        "price_max": 0.003,
        "confirm_col": "funding_rate",
        "confirm_max": 0.0,
    },
    {
        "name": "range_funding_neg_B",
        "price_col": "position_in_range_24h",
        "price_max": 0.15,
        "confirm_col": "funding_rate",
        "confirm_max": 0.0,
    },
    {
        "name": "vwap_low_funding_C",
        "price_col": "vwap_deviation",
        "price_max": -0.003,
        "confirm_col": "funding_rate",
        "confirm_max": 0.0001,
    },
    {
        "name": "dist_vwap_low_D",
        "price_col": "dist_to_24h_low",
        "price_max": 0.008,
        "confirm_col": "vwap_deviation",
        "confirm_max": -0.005,
    },
)


class FundingCycleOversoldLong(SignalDetector):
    name = "C1_funding_cycle_oversold_long"
    direction = "long"
    research_horizon_bars = 30
    hold_bars = research_horizon_bars
    required_columns = [
        "dist_to_24h_low",
        "position_in_range_24h",
        "funding_rate",
        "vwap_deviation",
    ]

    # 1 hour between runner triggers (ms)
    runner_cooldown_ms = 1800000

    def detect(self, df: pd.DataFrame) -> pd.Series:
        result = pd.Series(False, index=df.index)
        if not self.validate_columns(df):
            return result

        # detect() 与 check_live() 保持一致：必须 >= _MIN_VARIANTS 个变体同时触发
        multi_variant_mask = self._multi_variant_mask(df)
        return self._apply_cooldown(multi_variant_mask)

    # Minimum number of variants that must fire simultaneously.
    # Single-variant fires are weak (e.g. variant C alone fires almost every bar
    # when price is slightly below VWAP and funding is near zero).
    # Requiring 2+ means genuine multi-dimensional oversold confirmation.
    _MIN_VARIANTS = 2

    def check_live(self, df: pd.DataFrame) -> dict | None:
        if df is None or df.empty:
            return None
        if not self.validate_columns(df):
            return None

        _, matched = self._union_mask(df)
        if len(matched) < self._MIN_VARIANTS:
            return None

        # 斜率守卫：近60根K线价格斜率 <= 0 = 下降趋势，不开多单
        # 实盘教训（2026-04-15）：20根太短，多小时下跌中斜率会反复翻正，造成连续亏损
        if "close" in df.columns and len(df) >= _SLOPE_GUARD_LOOKBACK:
            try:
                import numpy as _np
                closes = df["close"].iloc[-_SLOPE_GUARD_LOOKBACK:].values.astype(float)
                slope = _np.polyfit(range(_SLOPE_GUARD_LOOKBACK), closes, 1)[0]
                if slope <= _SLOPE_GUARD_MIN:
                    logger.info(
                        "[C1] LONG blocked: price declining slope=%.2f (60-bar)",
                        slope,
                    )
                    return None
            except Exception:
                pass

        # 趋势确认（可选）：direction_autocorr < 0.3 表示价格方向持续向下，不开多单
        # 该特征来自 ORDER_FLOW 维度，不一定总存在；不存在时不阻止
        latest_for_trend = df.iloc[-1]
        if "direction_autocorr" in df.columns:
            try:
                dac = float(latest_for_trend["direction_autocorr"])
                if not (dac != dac) and dac < 0.3:  # NaN 检查
                    logger.info(
                        "[C1] LONG blocked: direction_autocorr=%.3f < 0.3 (下跌趋势持续)",
                        dac,
                    )
                    return None
            except Exception:
                pass

        latest = df.iloc[-1]
        latest_ts = int(latest.get("timestamp", 0))
        dist_low = float(latest["dist_to_24h_low"])
        fr = float(latest["funding_rate"])
        range_pos = float(latest["position_in_range_24h"])
        variant_names = ",".join(matched)
        research_horizon = self.resolved_research_horizon_bars()

        logger.info(
            "[C1 FUNDING OVERSOLD] LONG | %d variants=%s | dist_low=%.5f | "
            "fr=%.6f | range24h=%.4f",
            len(matched),
            variant_names,
            dist_low,
            fr,
            range_pos,
        )

        conf = 3 if len(matched) >= 3 else 2
        label = "HIGH" if conf >= 3 else "MEDIUM"

        return {
            "phase": "P1",
            "name": self.name,
            "direction": self.direction,
            "horizon": research_horizon,
            "research_horizon_bars": research_horizon,
            "timestamp_ms": latest_ts,
            "desc": (
                "[C1] funding oversold LONG | %d variants | dist_low=%.5f | fr=%.6f | range=%.4f"
                % (len(matched), dist_low, fr, range_pos)
            ),
            "confidence": conf,
            "confidence_label": label,
            "apply_fatigue": True,
            "feature": "funding_rate",
            "feature_value": fr,
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

    def _multi_variant_mask(self, df: pd.DataFrame) -> pd.Series:
        """每根 K 线同时触发的变体数 >= _MIN_VARIANTS，才算有效信号（与 check_live 一致）。"""
        count = pd.Series(0, index=df.index)
        for spec in _VARIANTS:
            count += self._build_variant_mask(df, spec).astype(int)
        return count >= self._MIN_VARIANTS

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
