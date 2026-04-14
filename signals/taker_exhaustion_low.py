"""
P1-10: taker exhaustion at extremes.

Physical idea:
  LONG:
    Price is pinned near the local low while aggressive selling dries up, or
    buyers suddenly step in from the bottom.

  SHORT:
    Price is stretched above VWAP while aggressive buying fades quickly. The
    trade bets on reversion only when that exhaustion happens near a real top
    zone, not in the middle of a live squeeze.
"""

from __future__ import annotations

import logging

import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)

_DIST_LOW_THR_A = 0.001099
_TAKER_RATIO_THR_A = 0.206743

_RANGE24H_LOW_THR_B = 0.041596
_TAKER_DELTA_THR_B = 2.271699

_VWAP_HIGH_THR_D = 0.020180
_TAKER_DELTA_THR_D = -1.092845
_HOLD_BARS_SHORT = 30

# 持续性要求：三个 Variant 均需连续 N 根 K 线满足核心条件才发射
# 物理依据：耗尽必须是持续状态，不是单棒瞬间闪现
# 效果：发射频率降低到原来 1/3~1/5，进场质量显著提升
_PERSIST_BARS = 3          # 所有 Variant 统一用 3 根 K 线（3 分钟）持续性检查

_TREND_GUARD_LOOKBACK = 20
_UPTREND_SHORT_RANGE4H_MIN = 0.95
_LIQUIDATION_TOTAL_USD_5M_MIN = 50_000.0
_LIQUIDATION_UPTREND_RANGE4H_MIN = 0.97
_LIQUIDATION_UPTREND_VWAP_MIN = 0.030
_LIQUIDATION_UPTREND_TAKER_DELTA_MAX = -1.50

COOLDOWN_BARS = 20


class TakerExhaustionLowDetector(SignalDetector):
    name = "P1-10_taker_exhaustion_low"
    direction = "both"
    research_horizon_bars = 30
    hold_bars = research_horizon_bars
    runner_cooldown_ms = 180_000   # 3 分钟最小间隔；防止同一市场状态反复发射
    required_columns = [
        "dist_to_24h_low",
        "position_in_range_24h",
        "taker_buy_sell_ratio",
        "vwap_deviation",
    ]

    def detect(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=df.index)

    def check_live(self, df: pd.DataFrame) -> dict | None:
        if df is None or df.empty:
            return None
        if not self.validate_columns(df):
            return None

        latest = df.iloc[-1]

        def _get(col: str) -> float:
            value = latest.get(col, float("nan"))
            return float(value) if not pd.isna(value) else float("nan")

        dist_low = _get("dist_to_24h_low")
        r24h = _get("position_in_range_24h")
        taker = _get("taker_buy_sell_ratio")
        ts = int(latest.get("timestamp", 0))
        research_horizon = self.resolved_research_horizon_bars()

        # Variant A: 底部 + 主动卖单极少
        # 持续性检查：最近 _PERSIST_BARS 根 K 线均处于底部区间
        # 防止价格瞬间触底又弹回时的单棒误触发
        if not pd.isna(dist_low) and dist_low < _DIST_LOW_THR_A:
            persist_ok = False
            if len(df) >= _PERSIST_BARS and "dist_to_24h_low" in df.columns:
                recent_dist = df["dist_to_24h_low"].iloc[-_PERSIST_BARS:]
                if not recent_dist.isna().any() and (recent_dist < _DIST_LOW_THR_A).all():
                    persist_ok = True
                else:
                    logger.info(
                        "[P1-10 A] LONG blocked: dist persistence failed recent=%s",
                        [round(float(v), 6) for v in recent_dist.tolist()],
                    )
            if persist_ok and not pd.isna(taker) and taker < _TAKER_RATIO_THR_A:
                conf = 3 if dist_low < 0.000599 else 2
                label = "HIGH" if conf >= 3 else "MEDIUM"
                logger.info("[P1-10 A] LONG | dist_low=%.5f taker=%.3f", dist_low, taker)
                return {
                    "phase": "P1",
                    "name": self.name,
                    "direction": "long",
                    "horizon": research_horizon,
                    "research_horizon_bars": research_horizon,
                    "timestamp_ms": ts,
                    "desc": (
                        f"[P1-10] seller exhaustion at bottom "
                        f"(dist_low={dist_low:.5f}, taker={taker:.3f})"
                    ),
                    "confidence": conf,
                    "confidence_label": label,
                    "apply_fatigue": False,
                    "feature": "taker_buy_sell_ratio",
                    "feature_value": taker,
                }

        # Variant B: 底部 + 主动买单突然激增
        # 持续性检查：最近 _PERSIST_BARS 根 K 线均在 24h 底部区间
        if not pd.isna(r24h) and r24h < _RANGE24H_LOW_THR_B:
            persist_ok_b = False
            if len(df) >= _PERSIST_BARS and "position_in_range_24h" in df.columns:
                recent_r24h = df["position_in_range_24h"].iloc[-_PERSIST_BARS:]
                if not recent_r24h.isna().any() and (recent_r24h < _RANGE24H_LOW_THR_B).all():
                    persist_ok_b = True
                else:
                    logger.info(
                        "[P1-10 B] LONG blocked: r24h persistence failed recent=%s",
                        [round(float(v), 4) for v in recent_r24h.tolist()],
                    )
            if persist_ok_b and "taker_buy_sell_ratio" in df.columns and len(df) >= 6:
                curr_taker = float(df["taker_buy_sell_ratio"].iloc[-1])
                prev_taker = float(df["taker_buy_sell_ratio"].iloc[-6])
                delta5 = curr_taker - prev_taker
                if not pd.isna(delta5) and delta5 > _TAKER_DELTA_THR_B:
                    logger.info("[P1-10 B] LONG | r24h=%.3f taker_delta=%.3f", r24h, delta5)
                    return {
                        "phase": "P1",
                        "name": self.name,
                        "direction": "long",
                        "horizon": research_horizon,
                        "research_horizon_bars": research_horizon,
                        "timestamp_ms": ts,
                        "desc": (
                            f"[P1-10] buyer surge at bottom "
                            f"(r24h={r24h:.3f}, taker_delta={delta5:.3f})"
                        ),
                        "confidence": 2,
                        "confidence_label": "MEDIUM",
                        "apply_fatigue": False,
                        "feature": "taker_buy_sell_ratio",
                        "feature_value": curr_taker,
                    }

        vwap_dev = _get("vwap_deviation")
        if not pd.isna(vwap_dev) and vwap_dev > _VWAP_HIGH_THR_D:
            if len(df) < _PERSIST_BARS:
                return None
            recent_vwap = df["vwap_deviation"].iloc[-_PERSIST_BARS:]
            if recent_vwap.isna().any() or not (recent_vwap > _VWAP_HIGH_THR_D).all():
                logger.info(
                    "[P1-10 D] SHORT blocked: vwap persistence failed recent=%s",
                    [round(float(v), 6) for v in recent_vwap.tolist()],
                )
                return None
            if "taker_buy_sell_ratio" in df.columns and len(df) >= 6:
                curr_taker = float(df["taker_buy_sell_ratio"].iloc[-1])
                prev_taker = float(df["taker_buy_sell_ratio"].iloc[-6])
                delta5 = curr_taker - prev_taker
                if not pd.isna(delta5) and delta5 < _TAKER_DELTA_THR_D:
                    slope = None
                    if "close" in df.columns and len(df) >= _TREND_GUARD_LOOKBACK:
                        try:
                            import numpy as _np

                            closes = df["close"].iloc[-_TREND_GUARD_LOOKBACK:].values
                            slope = _np.polyfit(range(_TREND_GUARD_LOOKBACK), closes, 1)[0]
                        except Exception:
                            slope = None

                    if slope is not None and slope > 0:
                        r4h = _get("position_in_range_4h")
                        liq_total = _get("total_liq_usd_5m")

                        if not pd.isna(liq_total) and liq_total >= _LIQUIDATION_TOTAL_USD_5M_MIN:
                            if (
                                pd.isna(r4h)
                                or r4h < _LIQUIDATION_UPTREND_RANGE4H_MIN
                                or vwap_dev < _LIQUIDATION_UPTREND_VWAP_MIN
                                or delta5 > _LIQUIDATION_UPTREND_TAKER_DELTA_MAX
                            ):
                                logger.info(
                                    "[P1-10 D] SHORT blocked: liquidation uptrend "
                                    "slope=%.2f liq=%.0f r4h=%.3f vwap=%.4f delta=%.3f",
                                    slope,
                                    liq_total,
                                    r4h if not pd.isna(r4h) else -1.0,
                                    vwap_dev,
                                    delta5,
                                )
                                return None
                        elif pd.isna(r4h) or r4h < _UPTREND_SHORT_RANGE4H_MIN:
                            logger.info(
                                "[P1-10 D] SHORT blocked: uptrend slope=%.2f, "
                                "r4h=%.3f < %.2f",
                                slope,
                                r4h if not pd.isna(r4h) else -1.0,
                                _UPTREND_SHORT_RANGE4H_MIN,
                            )
                            return None

                    conf = 3 if vwap_dev > 0.033000 else 2
                    label = "HIGH" if conf >= 3 else "MEDIUM"
                    logger.info("[P1-10 D] SHORT | vwap_dev=%.4f taker_delta=%.3f", vwap_dev, delta5)
                    return {
                        "phase": "P1",
                        "name": self.name,
                        "direction": "short",
                        "horizon": _HOLD_BARS_SHORT,
                        "research_horizon_bars": _HOLD_BARS_SHORT,
                        "timestamp_ms": ts,
                        "desc": (
                            f"[P1-10 D] VWAP overextended + buyer collapse "
                            f"(vwap_dev={vwap_dev:.4f}, taker_delta={delta5:.3f})"
                        ),
                        "confidence": conf,
                        "confidence_label": label,
                        "apply_fatigue": False,
                        "feature": "vwap_deviation",
                        "feature_value": vwap_dev,
                    }

        return None
