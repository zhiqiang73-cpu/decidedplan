"""
P1-11: 高位 + 负资金费率 (HighPosFunding)

物理逻辑:
  价格在 4h 高位（做市商库存积压），但资金费率为负（空头主导或多头无溢价）。
  上涨不由真实需求驱动，多方合约溢价消失；
  一旦做市商开始卸货，价格回落速度很快（无人接盘）。

入场条件（2 个 Variant）:
  Variant A: position_in_range_4h > p98 (0.980368)
             + funding_rate < p3 (~-0.000025)
             → 4h 高位 + 资金费率负值（空方更愿意持仓）
  Variant B: position_in_range_4h > p99 (0.991977)
             + funding_rate < p2 (-0.000034)
             → 更严格：极端高位 + 明显负费率

阈值来源: IS 期间 (2024-10-01 ~ 2025-12-04) OOS 验证:
  TF=2m: OOS=80.4% (n=51, 2m), MFE=+0.387%, MFE>fee=90%
"""

from __future__ import annotations

import logging

import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)

# ── 冻结阈值（IS 2024-10-01 ~ 2025-12-04）────────────────────────────────────

# Variant A（p98 + p3）
_RANGE4H_THR_A   = 0.980368    # position_in_range_4h > (p98 at 1m, IS)
_FUNDING_THR_A   = -0.000025   # funding_rate < (p3 approx at 1m, IS)

# Variant B（p99 + p2，更严格）
_RANGE4H_THR_B   = 0.991977    # position_in_range_4h > (p99 at 1m, IS)
_FUNDING_THR_B   = -0.000034   # funding_rate < (p2 at 1m, IS)

COOLDOWN_BARS = 30


class HighPosFundingDetector(SignalDetector):
    name = "P1-11_high_pos_funding"
    direction = "short"
    hold_bars = 30
    required_columns = [
        "position_in_range_4h",
        "funding_rate",
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

        r4h     = _get("position_in_range_4h")
        funding = _get("funding_rate")
        ts      = int(latest.get("timestamp", 0))

        if pd.isna(r4h) or pd.isna(funding):
            return None

        # Variant B 优先（更严格，置信度更高）
        if r4h > _RANGE4H_THR_B and funding < _FUNDING_THR_B:
            logger.info(
                "[P1-11 B] SHORT | r4h=%.4f funding=%.6f",
                r4h, funding,
            )
            return _alert(
                ts=ts,
                horizon=self.hold_bars,
                conf=3,
                detail=f"r4h={r4h:.4f} funding={funding:.6f}",
                feature="funding_rate",
                fval=funding,
            )

        # Variant A（宽松条件，中等置信度）
        if r4h > _RANGE4H_THR_A and funding < _FUNDING_THR_A:
            logger.info(
                "[P1-11 A] SHORT | r4h=%.4f funding=%.6f",
                r4h, funding,
            )
            return _alert(
                ts=ts,
                horizon=self.hold_bars,
                conf=2,
                detail=f"r4h={r4h:.4f} funding={funding:.6f}",
                feature="funding_rate",
                fval=funding,
            )

        return None


def _alert(ts: int, horizon: int, conf: int,
           detail: str, feature: str, fval: float) -> dict:
    label = "HIGH" if conf >= 3 else "MEDIUM"
    return {
        "phase":            "P1",
        "name":             "P1-11_high_pos_funding",
        "direction":        "short",
        "horizon":          horizon,
        "timestamp_ms":     ts,
        "desc":             f"[P1-11 SHORT] high 4h pos + negative funding ({detail})",
        "confidence":       conf,
        "confidence_label": label,
        "apply_fatigue":    False,
        "feature":          feature,
        "feature_value":    fval,
    }
