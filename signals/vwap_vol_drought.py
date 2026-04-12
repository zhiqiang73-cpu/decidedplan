"""
P1-8: VWAP 偏离 + 成交量干旱 (VwapVolDrought)

物理逻辑:
  VWAP 算法交易商持续往公允价方向执行订单。
  当价格严重偏离 VWAP 且成交量同时干旱，
  说明推动价格的主动买/卖已停止——价格靠惯性撑着，
  一旦 VWAP 回归压力累积到临界点，直接反转。

入场条件:
  SHORT: vwap_deviation > p95 (0.02018) + 连续 ≥4 个 10min 块成交量干旱
  LONG:  vwap_deviation < p2  (-0.02365) + 连续 ≥3 个 10min 块成交量干旱

阈值来源: IS 期间 (2024-10-01 ~ 2025-12-04) OOS 验证:
  SHORT OOS=96% (n=25, 10m), LONG OOS=91% (n=33, 10m)
"""

from __future__ import annotations

import logging

import pandas as pd

from signals.base import SignalDetector
from signals._mtf_utils import compute_state_blocks

logger = logging.getLogger(__name__)

# ── 冻结阈值（来自 IS 期间 2024-10-01 ~ 2025-12-04）─────────────────────────
_VWAP_SHORT_THR   =  0.020180   # vwap_deviation > 此值 = 偏高 (p95 at 10m)
_VWAP_LONG_THR    = -0.023646   # vwap_deviation < 此值 = 偏低 (p2  at 10m)
_DROUGHT_SHORT_MIN = 4          # 连续 10min 干旱块数（SHORT，p97 at 10m）
_DROUGHT_LONG_MIN  = 3          # 连续 10min 干旱块数（LONG， p95 at 10m）
_TF_MIN = 10                    # 聚合粒度（分钟）
COOLDOWN_BARS = 40              # 1-min 冷却（60 分钟）


class VwapVolDroughtDetector(SignalDetector):
    name = "P1-8_vwap_vol_drought"
    direction = "both"   # SHORT 或 LONG，由实际条件决定
    research_horizon_bars = 30
    hold_bars = research_horizon_bars
    required_columns = ["vwap_deviation", "volume", "high", "low"]

    def detect(self, df: pd.DataFrame) -> pd.Series:
        # 此信号仅通过 check_live() 激活；batch detect 返回空。
        return pd.Series(False, index=df.index)

    def check_live(self, df: pd.DataFrame) -> dict | None:
        if df is None or df.empty or len(df) < _TF_MIN * 4:
            return None
        if not self.validate_columns(df):
            return None

        latest = df.iloc[-1]
        vwap_dev = float(latest.get("vwap_deviation", float("nan")))
        if pd.isna(vwap_dev):
            return None
        research_horizon = self.resolved_research_horizon_bars()

        drought_cnt, _ = compute_state_blocks(df, _TF_MIN)

        # ── SHORT ────────────────────────────────────────────────────────────
        if vwap_dev > _VWAP_SHORT_THR and drought_cnt >= _DROUGHT_SHORT_MIN:
            conf = 3 if drought_cnt >= 5 else 2
            label = "HIGH" if conf >= 3 else "MEDIUM"
            logger.info(
                "[P1-8 SHORT] vwap_dev=%.4f drought=%d", vwap_dev, drought_cnt
            )
            return {
                "phase":            "P1",
                "name":             self.name,
                "direction":        "short",
                "horizon":          research_horizon,
                "research_horizon_bars": research_horizon,
                "timestamp_ms":     int(latest.get("timestamp", 0)),
                "desc":             (
                    f"[P1-8] VWAP overextended + vol drought "
                    f"(dev={vwap_dev:.4f}, drought={drought_cnt}blk)"
                ),
                "confidence":       conf,
                "confidence_label": label,
                "apply_fatigue":    False,
                "feature":          "vwap_deviation",
                "feature_value":    vwap_dev,
            }

        # ── LONG ─────────────────────────────────────────────────────────────
        if vwap_dev < _VWAP_LONG_THR and drought_cnt >= _DROUGHT_LONG_MIN:
            conf = 3 if drought_cnt >= 5 else 2
            label = "HIGH" if conf >= 3 else "MEDIUM"
            logger.info(
                "[P1-8 LONG] vwap_dev=%.4f drought=%d", vwap_dev, drought_cnt
            )
            return {
                "phase":            "P1",
                "name":             self.name,
                "direction":        "long",
                "horizon":          research_horizon,
                "research_horizon_bars": research_horizon,
                "timestamp_ms":     int(latest.get("timestamp", 0)),
                "desc":             (
                    f"[P1-8] VWAP oversold + vol drought "
                    f"(dev={vwap_dev:.4f}, drought={drought_cnt}blk)"
                ),
                "confidence":       conf,
                "confidence_label": label,
                "apply_fatigue":    False,
                "feature":          "vwap_deviation",
                "feature_value":    vwap_dev,
            }

        return None
