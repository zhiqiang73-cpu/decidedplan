"""
P1-2: VWAP/TWAP 拆单痕迹 (VWAP/TWAP Slicing)

当前 live 代码口径:
  需同时满足以下 3/3 个条件:
    A. volume_autocorr_lag5 > 0.55
    B. 整刻钟时点且 volume_vs_ma20 > 1.5
    C. avg_trade_size_cv_10m < 0.30

研究观察窗: 30 bars（仅用于统计基线与 safety_cap 估算）
Runner 冷却: 5 min
"""

import logging

import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)


class VWAPTWAPDetector(SignalDetector):

    name = "P1-2_VWAP/TWAP拆单"
    direction = "long"
    research_horizon_bars = 30
    hold_bars = research_horizon_bars
    # Runner-level cooldown: 5 min (dedup lock prevents double-entry while position open)
    runner_cooldown_ms = 5 * 60 * 1000

    required_columns = [
        "volume_autocorr_lag5",
        "volume_vs_ma20",
        "minute_in_hour",
        "avg_trade_size_cv_10m",
    ]

    AUTOCORR_THRESHOLD = 0.55  # 当前 live 口径：三条件同时满足，先把噪声压下去
    VOLUME_MULT = 1.5
    CV_MAX = 0.30
    COOLDOWN = 5

    def detect(self, df: pd.DataFrame) -> pd.Series:
        self.validate_columns(df)

        cond_autocorr = df["volume_autocorr_lag5"] > self.AUTOCORR_THRESHOLD

        at_key_minute = df["minute_in_hour"].isin([0, 15, 30, 45])
        cond_timing = at_key_minute & (df["volume_vs_ma20"] > self.VOLUME_MULT)

        cond_uniform = df["avg_trade_size_cv_10m"] < self.CV_MAX

        # 当前 live 代码要求 3/3 全满足，不再使用旧的放宽口径
        score = (
            cond_autocorr.astype(int)
            + cond_timing.astype(int)
            + cond_uniform.astype(int)
        )
        combined = score >= 3

        signal_arr = self._debounce(combined.values, cooldown=self.COOLDOWN)
        return pd.Series(signal_arr, index=df.index)

    def check_live(self, df: pd.DataFrame) -> dict | None:
        """实时检测接口：使用 df 最后一行评估当前是否触发信号。"""
        if df is None or df.empty:
            return None
        if not self.validate_columns(df):
            return None

        latest = df.iloc[-1]
        latest_ts = int(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else 0

        ac = (
            float(latest["volume_autocorr_lag5"])
            if pd.notna(latest["volume_autocorr_lag5"])
            else 0.0
        )
        cond_autocorr = ac > self.AUTOCORR_THRESHOLD

        minute = (
            int(latest["minute_in_hour"])
            if pd.notna(latest["minute_in_hour"])
            else -1
        )
        vol = (
            float(latest["volume_vs_ma20"])
            if pd.notna(latest["volume_vs_ma20"])
            else 0.0
        )
        cond_timing = (minute in [0, 15, 30, 45]) and (vol > self.VOLUME_MULT)

        cv = (
            float(latest["avg_trade_size_cv_10m"])
            if pd.notna(latest["avg_trade_size_cv_10m"])
            else float("inf")
        )
        cond_uniform = cv < self.CV_MAX

        score = int(cond_autocorr) + int(cond_timing) + int(cond_uniform)

        if score < 3:
            return None

        logger.info(
            "[P1-2 VWAP/TWAP] LONG | autocorr=%.3f | vol=%.1fx | cv=%.3f | score=%d/3",
            ac,
            vol,
            cv,
            score,
        )
        research_horizon = self.resolved_research_horizon_bars()

        return {
            "phase": "P1",
            "name": self.name,
            "direction": "long",
            "horizon": research_horizon,
            "research_horizon_bars": research_horizon,
            "timestamp_ms": latest_ts,
            "desc": (
                f"[{self.name}] autocorr={ac:.3f} vol={vol:.1f}x cv={cv:.3f} score={score}/3"
            ),
            "confidence": 2,
            "confidence_label": "MEDIUM",
            "apply_fatigue": False,
            "feature": "volume_autocorr_lag5",
            "feature_value": ac,
        }
