"""
P0-2: 资金费率套利 (Funding Rate Arbitrage)

检测逻辑 (两层，优先使用真实数据):

[层1] 有 funding_rate 数据时:
  |funding_rate| > 0.01% AND 距结算<30min AND taker方向确认

[层2] 无 funding_rate 数据时 (代理模式):
  使用"结算时刻附近持续的Taker方向极端化"作为代理:
  - minutes_to_funding < 30
  - taker_buy_pct 5根K线均值 < 0.38 (空头主导 → 隐含费率为正)
  - 或 taker_buy_pct 5根K线均值 > 0.62 (多头主导 → 隐含费率为负)
  - volume_vs_ma20 > 1.2 (结算附近成交量略微放大)

冷却期: 240 bars (4h，避免同一结算周期重复触发)
"""

import logging

import numpy as np
import pandas as pd

from signals.base import SignalDetector

logger = logging.getLogger(__name__)


class FundingRateDetector(SignalDetector):

    name      = "P0-2_资金费率套利"
    direction = "short"
    hold_bars = 30
    # Runner-level cooldown: 5 min (dedup lock prevents double-entry while position open)
    runner_cooldown_ms = 5 * 60 * 1000

    required_columns = ["minutes_to_funding", "taker_buy_pct"]

    FR_THRESHOLD    = 0.0001  # |funding_rate| > 0.01% (BTC均值0.0054%，此阈值约75百分位)
    WINDOW_MINUTES  = 30
    TAKER_SHORT_MAX = 0.38    # 代理模式：空头主导阈值（放宽）
    TAKER_LONG_MIN  = 0.62    # 代理模式：多头主导阈值（放宽）
    VOL_MIN         = 1.2     # 代理模式：最低成交量倍数
    COOLDOWN        = 5       # 5min 冷却（实盘 runner_cooldown_ms 同步）

    def detect(self, df: pd.DataFrame) -> pd.Series:
        self.validate_columns(df)

        cond_window = df["minutes_to_funding"] < self.WINDOW_MINUTES

        has_real_fr = (
            "funding_rate" in df.columns
            and not df["funding_rate"].isna().all()
        )

        if has_real_fr:
            fr = df["funding_rate"]
            cond_extreme_pos = fr >  self.FR_THRESHOLD
            cond_extreme_neg = fr < -self.FR_THRESHOLD
        else:
            # ── 代理模式: 5根K线均值确认持续方向性 ──────────────────────
            taker_ma5 = df["taker_buy_pct"].rolling(5, min_periods=3).mean()
            cond_extreme_pos = taker_ma5 < self.TAKER_SHORT_MAX  # 空头主导=隐含正费率
            cond_extreme_neg = taker_ma5 > self.TAKER_LONG_MIN   # 多头主导=隐含负费率

        # Taker 方向确认
        taker = df["taker_buy_pct"]
        cond_vol = df["volume_vs_ma20"] > self.VOL_MIN if "volume_vs_ma20" in df.columns else pd.Series(True, index=df.index)

        if has_real_fr:
            # 真实费率时用更严格的taker条件
            short_signal = cond_window & cond_extreme_pos & (taker < 0.40)
            long_signal  = cond_window & cond_extreme_neg & (taker > 0.60)
        else:
            # 代理模式: taker方向 + 成交量
            short_signal = cond_window & cond_extreme_pos & cond_vol
            long_signal  = cond_window & cond_extreme_neg & cond_vol

        combined = short_signal | long_signal
        signal_arr = self._debounce(combined.values, cooldown=self.COOLDOWN)

        self._last_short_mask = short_signal
        self._last_long_mask  = long_signal

        return pd.Series(signal_arr, index=df.index)

    def check_live(self, df: pd.DataFrame) -> dict | None:
        """实时检测接口：使用 df 最后一行评估当前是否触发信号。"""
        if df is None or df.empty:
            return None
        if not self.validate_columns(df):
            return None

        latest = df.iloc[-1]
        latest_ts = int(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else 0

        mtf = float(latest["minutes_to_funding"])
        if mtf >= self.WINDOW_MINUTES:
            return None

        taker_val = float(latest["taker_buy_pct"])

        has_real_fr = (
            "funding_rate" in df.columns
            and not df["funding_rate"].isna().all()
            and pd.notna(latest.get("funding_rate", float("nan")))
        )

        if has_real_fr:
            fr_val = float(latest["funding_rate"])
            short_fire = (fr_val > self.FR_THRESHOLD) and (taker_val < 0.40)
            long_fire  = (fr_val < -self.FR_THRESHOLD) and (taker_val > 0.60)
            display_val = fr_val
            mode_label = "FR"
        else:
            # 代理模式：用 df 末尾最多 5 行的 taker_buy_pct 均值
            window = df["taker_buy_pct"].iloc[-5:]
            if window.notna().sum() < 3:
                return None
            taker_ma5 = float(window.mean())
            vol_ok = (
                float(latest["volume_vs_ma20"]) > self.VOL_MIN
                if "volume_vs_ma20" in df.columns and pd.notna(latest.get("volume_vs_ma20"))
                else False
            )
            short_fire = (taker_ma5 < self.TAKER_SHORT_MAX) and vol_ok
            long_fire  = (taker_ma5 > self.TAKER_LONG_MIN) and vol_ok
            display_val = taker_ma5
            mode_label = "proxy_taker_ma5"

        if not short_fire and not long_fire:
            return None

        direction = "short" if short_fire else "long"

        logger.info(
            "[P0-2 FR ARBI] %s | %s=%.6f | minutes_to_funding=%.0f",
            direction.upper(),
            mode_label,
            display_val,
            mtf,
        )

        return {
            "phase": "P1",
            "name": self.name,
            "direction": direction,
            "horizon": self.hold_bars,
            "timestamp_ms": latest_ts,
            "desc": (
                f"[{self.name}] {mode_label}={display_val:.6f} "
                f"direction={direction} (minutes_to_funding={mtf:.0f})"
            ),
            "confidence": 2,
            "confidence_label": "MEDIUM",
        }

    def report(self, df: pd.DataFrame, fee_pct: float = 0.10) -> dict:
        signals = self.detect(df)
        trigger_count = int(signals.sum())

        base = {
            "name":          self.name,
            "trigger_count": trigger_count,
            "trigger_rate":  round(trigger_count / max(len(df), 1) * 100, 3),
            "fee_pct":       fee_pct,
        }
        if trigger_count == 0:
            return base

        trigger_idx = np.where(signals.values)[0]
        close = df["close"].values
        returns = []

        short_mask = self._last_short_mask.values
        long_mask  = self._last_long_mask.values

        for idx in trigger_idx:
            entry_idx = idx              # 与实时执行口径一致
            exit_idx  = idx + self.hold_bars
            if exit_idx >= len(close):
                continue
            ep = close[entry_idx]
            xp = close[exit_idx]
            if ep <= 0:
                continue
            ret = (ep - xp) / ep if short_mask[idx] else (xp - ep) / ep
            returns.append(ret)

        if not returns:
            return base

        gross = np.array(returns)
        net   = gross - fee_pct / 100
        wins_n = net[net > 0]; losses_n = net[net <= 0]
        win_rate = len(wins_n) / len(net)
        avg_win  = wins_n.mean()        if len(wins_n)  > 0 else 0
        avg_loss = abs(losses_n.mean()) if len(losses_n) > 0 else 0
        pf_net = (avg_win * len(wins_n)) / (avg_loss * len(losses_n)) if len(losses_n) > 0 and avg_loss > 0 else float("inf")

        base.update({
            "evaluated":            len(gross),
            "win_rate_gross":       round(len(gross[gross > 0]) / len(gross) * 100, 1),
            "win_rate_net":         round(win_rate * 100, 1),
            "profit_factor_net":    round(pf_net, 2),
            "avg_return_gross_pct": round(float(gross.mean()) * 100, 4),
            "avg_return_net_pct":   round(float(net.mean())   * 100, 4),
        })
        return base
