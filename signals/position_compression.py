"""
P1-9: 极端位置 + 价格压缩 (PositionCompression)

物理逻辑:
  价格在极端位置（24h 高位或低位）长时间压缩（振幅极小的 K 线堆叠），
  是做市商调整库存或止损单聚集的痕迹。
  一旦触发，形成单方向的级联（高位压缩 → 向下释放，低位压缩 → 向上反弹）。

入场条件（4 个 Variant）:
  SHORT-A: position_in_range_24h > p95 (0.9338) + 连续 ≥8 个 10min 块价格压缩
  SHORT-B: position_in_range_4h  > p98 (0.9804) + 连续 ≥6 个 5min  块价格压缩
  LONG-A:  dist_to_24h_high      < p2  (-0.0602) + 连续 ≥8 个 10min 块价格压缩
           （dist_to_24h_high 极小 = 价格远低于 24h 高点 = 价格在底部）
  LONG-B:  vwap_deviation        < p2  (-0.0237) + 连续 ≥6 个 10min 块价格压缩

阈值来源: IS 期间 (2024-10-01 ~ 2025-12-04) OOS 验证:
  SHORT-A OOS=86% (n=28, 10m), SHORT-B OOS=87% (n=46, 5m)
  LONG-A  OOS=90% (n=29, 3m),  LONG-B  OOS=90% (n=30, 10m)
"""

from __future__ import annotations

import logging

import pandas as pd

from signals.base import SignalDetector
from signals._mtf_utils import compute_state_blocks

logger = logging.getLogger(__name__)

# ── 冻结阈值（IS 2024-10-01 ~ 2025-12-04）────────────────────────────────────

# SHORT
_RANGE24H_SHORT_THR   = 0.933835    # position_in_range_24h > (p95 at 1m)
_RANGE4H_SHORT_THR    = 0.980368    # position_in_range_4h  > (p98 at 1m)
_COMP_SHORT_10M_MIN   = 8           # 连续 10min 压缩块 (p98 at 10m)
_COMP_SHORT_5M_MIN    = 6           # 连续 5min  压缩块 (p97 at 5m)

# LONG
_DIST24H_HIGH_LONG_THR = -0.060173  # dist_to_24h_high < (p2 at 1m = 价格在底部)
_VWAP_LONG_THR         = -0.023646  # vwap_deviation   < (p2 at 10m)
_COMP_LONG_10M_MIN     = 8          # 连续 10min 压缩块
_COMP_LONG_10M_VWAP    = 6          # 连续 10min 压缩块（VWAP 版本，条件稍宽）

COOLDOWN_BARS = 60


class PositionCompressionDetector(SignalDetector):
    name = "P1-9_position_compression"
    direction = "both"
    research_horizon_bars = 30
    hold_bars = research_horizon_bars
    required_columns = [
        "position_in_range_24h", "position_in_range_4h",
        "dist_to_24h_high", "vwap_deviation",
        "volume", "high", "low",
    ]

    def detect(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=df.index)

    def check_live(self, df: pd.DataFrame) -> dict | None:
        if df is None or df.empty or len(df) < 50:
            return None
        if not self.validate_columns(df):
            return None

        latest = df.iloc[-1]

        def _get(col: str) -> float:
            v = latest.get(col, float("nan"))
            return float(v) if not pd.isna(v) else float("nan")

        r24h  = _get("position_in_range_24h")
        r4h   = _get("position_in_range_4h")
        d_hi  = _get("dist_to_24h_high")
        vwap  = _get("vwap_deviation")

        comp10, _ = compute_state_blocks(df, 10)
        comp5,  _ = compute_state_blocks(df, 5)

        ts = int(latest.get("timestamp", 0))
        research_horizon = self.resolved_research_horizon_bars()

        # ── SHORT-A: 24h 范围高位 + 10min 压缩 ───────────────────────────
        if (not pd.isna(r24h) and r24h > _RANGE24H_SHORT_THR
                and comp10 >= _COMP_SHORT_10M_MIN):
            conf = 3 if comp10 >= 10 else 2
            logger.info("[P1-9 SHORT-A] r24h=%.3f comp10=%d", r24h, comp10)
            return _alert("short", research_horizon, ts, conf,
                          f"range24h={r24h:.3f} comp10={comp10}blk", "position_in_range_24h", r24h)

        # ── SHORT-B: 4h 范围高位 + 5min 压缩 ────────────────────────────
        if (not pd.isna(r4h) and r4h > _RANGE4H_SHORT_THR
                and comp5 >= _COMP_SHORT_5M_MIN):
            conf = 3 if comp5 >= 9 else 2
            logger.info("[P1-9 SHORT-B] r4h=%.3f comp5=%d", r4h, comp5)
            return _alert("short", research_horizon, ts, conf,
                          f"range4h={r4h:.3f} comp5={comp5}blk", "position_in_range_4h", r4h)

        # ── LONG-A: 价格在 24h 底部 + 10min 压缩 ────────────────────────
        if (not pd.isna(d_hi) and d_hi < _DIST24H_HIGH_LONG_THR
                and comp10 >= _COMP_LONG_10M_MIN):
            conf = 3 if comp10 >= 10 else 2
            logger.info("[P1-9 LONG-A] dist_hi=%.4f comp10=%d", d_hi, comp10)
            return _alert("long", research_horizon, ts, conf,
                          f"dist_24h_high={d_hi:.4f} comp10={comp10}blk", "dist_to_24h_high", d_hi)

        # ── LONG-B: VWAP 下方 + 10min 压缩 ─────────────────────────────
        if (not pd.isna(vwap) and vwap < _VWAP_LONG_THR
                and comp10 >= _COMP_LONG_10M_VWAP):
            conf = 3 if comp10 >= 9 else 2
            logger.info("[P1-9 LONG-B] vwap=%.4f comp10=%d", vwap, comp10)
            return _alert("long", research_horizon, ts, conf,
                          f"vwap_dev={vwap:.4f} comp10={comp10}blk", "vwap_deviation", vwap)

        return None


def _alert(direction: str, horizon: int, ts: int, conf: int,
           detail: str, feature: str, fval: float) -> dict:
    label = "HIGH" if conf >= 3 else "MEDIUM"
    return {
        "phase":            "P1",
        "name":             "P1-9_position_compression",
        "direction":        direction,
        "horizon":          horizon,
        "research_horizon_bars": horizon,
        "timestamp_ms":     ts,
        "desc":             f"[P1-9 {direction.upper()}] spring-load at extreme ({detail})",
        "confidence":       conf,
        "confidence_label": label,
        "apply_fatigue":    False,
        "feature":          feature,
        "feature_value":    fval,
    }
