"""
P1-10: 主动单极端 + 价格位置反转 (TakerExhaustionLow)

物理逻辑:
  做多方向 (LONG): 价格在 24h 最低点附近，但主动卖单极少（taker sell 占比极低）。
    说明愿意主动砸盘的空头已经耗尽；价格在低位"撑着"，大概率向上反弹。
  做空方向 (SHORT): 价格严重偏离 VWAP 向上，且主动买盘突然急速萎缩。
    说明追涨力量已经衰竭，VWAP 回归压力将推动价格向下。

入场条件（3 个 Variant，均经 OOS 验证）:
  Variant A [LONG]:  dist_to_24h_low      < p2  (0.001099)
                     + taker_buy_sell_ratio < p5  (0.206743)
                     → 价格在底 + 主动卖单极少
                     OOS=80% (n=35, 2m)
  Variant B [LONG]:  position_in_range_24h < p3  (0.041596)
                     + taker_ratio_delta5  > p95 (2.271699 at 2m)
                     → 价格在底 + 主动买单突然激增（买家进场）
                     OOS=77% (n=26, 3m)
  Variant D [SHORT]: vwap_deviation        > p95 (0.020180)
                     + taker_ratio_delta5  < p5  (-1.092845 at 10m)
                     → 价格在 VWAP 之上 + 主动买盘急速萎缩
                     OOS=80% (n=30, 10m)  hold=20bar

注: Variant C（taker_buy_sell_ratio 高位做空）经扫描 OOS 仅 66~68%，已移除。

阈值来源: IS 期间 (2024-10-01 ~ 2025-12-04), OOS 期间 (2025-12-05 ~ 2026-03-16)
"""

from __future__ import annotations

import logging

import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)

# ── 冻结阈值（IS 2024-10-01 ~ 2025-12-04）────────────────────────────────────

# Variant A [LONG]: 底部 + 主动卖单极少
_DIST_LOW_THR_A    = 0.001099    # dist_to_24h_low < (p2 at 1m, IS)
_TAKER_RATIO_THR_A = 0.206743    # taker_buy_sell_ratio < (p5 at 1m, IS)

# Variant B [LONG]: 底部 + 主动买单突然激增
_RANGE24H_LOW_THR_B  = 0.041596  # position_in_range_24h < (p3 at 1m, IS)
_TAKER_DELTA_THR_B   = 2.271699  # taker_ratio_delta5 > (p95 at 2m IS)

# Variant D [SHORT]: VWAP 偏高 + 主动买盘急速萎缩（OOS 验证: 80.0% n=30, 10m）
_VWAP_HIGH_THR_D   = 0.020180    # vwap_deviation > (p95 at 10m IS) — 与 P1-8 SHORT 共用
_TAKER_DELTA_THR_D = -1.092845   # taker_ratio_delta5 < (p5 at 10m IS)
_HOLD_BARS_SHORT   = 30

COOLDOWN_BARS = 30


class TakerExhaustionLowDetector(SignalDetector):
    name = "P1-10_taker_exhaustion_low"
    direction = "both"   # LONG(A/B) + SHORT(C/D 待验证)
    hold_bars = 30
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
            v = latest.get(col, float("nan"))
            return float(v) if not pd.isna(v) else float("nan")

        dist_low = _get("dist_to_24h_low")
        r24h     = _get("position_in_range_24h")
        taker    = _get("taker_buy_sell_ratio")
        ts       = int(latest.get("timestamp", 0))

        # Variant A: 低位 + 主动卖单极少
        if not pd.isna(dist_low) and dist_low < _DIST_LOW_THR_A:
            if not pd.isna(taker) and taker < _TAKER_RATIO_THR_A:
                conf = 3 if dist_low < 0.000599 else 2  # p1 以内升为 HIGH
                label = "HIGH" if conf >= 3 else "MEDIUM"
                logger.info(
                    "[P1-10 A] LONG | dist_low=%.5f taker=%.3f",
                    dist_low, taker,
                )
                return {
                    "phase":            "P1",
                    "name":             self.name,
                    "direction":        "long",
                    "horizon":          self.hold_bars,
                    "timestamp_ms":     ts,
                    "desc":             (
                        f"[P1-10] seller exhaustion at bottom "
                        f"(dist_low={dist_low:.5f}, taker={taker:.3f})"
                    ),
                    "confidence":       conf,
                    "confidence_label": label,
                    "apply_fatigue":    False,
                    "feature":          "taker_buy_sell_ratio",
                    "feature_value":    taker,
                }

        # Variant B: 低位 + taker 买盘突然激增
        if not pd.isna(r24h) and r24h < _RANGE24H_LOW_THR_B:
            # 计算 taker_ratio_delta5（当前 vs 5 根 bar 前）
            if "taker_buy_sell_ratio" in df.columns and len(df) >= 6:
                curr_taker = float(df["taker_buy_sell_ratio"].iloc[-1])
                prev_taker = float(df["taker_buy_sell_ratio"].iloc[-6])
                delta5 = curr_taker - prev_taker
                if not pd.isna(delta5) and delta5 > _TAKER_DELTA_THR_B:
                    logger.info(
                        "[P1-10 B] LONG | r24h=%.3f taker_delta=%.3f",
                        r24h, delta5,
                    )
                    return {
                        "phase":            "P1",
                        "name":             self.name,
                        "direction":        "long",
                        "horizon":          self.hold_bars,
                        "timestamp_ms":     ts,
                        "desc":             (
                            f"[P1-10] buyer surge at bottom "
                            f"(r24h={r24h:.3f}, taker_delta={delta5:.3f})"
                        ),
                        "confidence":       2,
                        "confidence_label": "MEDIUM",
                        "apply_fatigue":    False,
                        "feature":          "taker_buy_sell_ratio",
                        "feature_value":    curr_taker,
                    }

        # ── SHORT 方向（Variant D，VWAP 偏高 + 买盘萎缩）────────────────────────
        # OOS 验证: vwap_deviation p95 + taker_ratio_delta5 p5 → 80.0% (n=30, 10m)
        vwap_dev = _get("vwap_deviation")

        if not pd.isna(vwap_dev) and vwap_dev > _VWAP_HIGH_THR_D:
            if "taker_buy_sell_ratio" in df.columns and len(df) >= 6:
                curr_taker = float(df["taker_buy_sell_ratio"].iloc[-1])
                prev_taker = float(df["taker_buy_sell_ratio"].iloc[-6])
                delta5 = curr_taker - prev_taker
                if not pd.isna(delta5) and delta5 < _TAKER_DELTA_THR_D:
                    # vwap_dev > p98 升为 HIGH
                    conf = 3 if vwap_dev > 0.033000 else 2
                    label = "HIGH" if conf >= 3 else "MEDIUM"
                    logger.info(
                        "[P1-10 D] SHORT | vwap_dev=%.4f taker_delta=%.3f",
                        vwap_dev, delta5,
                    )
                    return {
                        "phase":            "P1",
                        "name":             self.name,
                        "direction":        "short",
                        "horizon":          _HOLD_BARS_SHORT,
                        "timestamp_ms":     ts,
                        "desc":             (
                            f"[P1-10 D] VWAP overextended + buyer collapse "
                            f"(vwap_dev={vwap_dev:.4f}, taker_delta={delta5:.3f})"
                        ),
                        "confidence":       conf,
                        "confidence_label": label,
                        "apply_fatigue":    False,
                        "feature":          "vwap_deviation",
                        "feature_value":    vwap_dev,
                    }

        return None
