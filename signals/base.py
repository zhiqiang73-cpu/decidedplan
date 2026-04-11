"""
信号检测器基类

所有信号检测器继承此类，统一接口:
  - detect(df) → pd.Series[bool]   信号触发时刻
  - report(df)  → dict              触发统计 + 初步胜率

胜率计算说明:
  对每个触发点，按 research_horizon_bars 根K线后的收益衡量胜负。
  这只是 legacy fixed-hold baseline，用来衡量入场判断方向对不对；
  live 出场仍然必须看 vs_entry / 机制衰竭 / safety_cap。
  方向由子类的 direction 属性决定 ("long" or "short")。
"""

from abc import ABC, abstractmethod
from typing import List, Optional

import numpy as np
import pandas as pd


class SignalDetector(ABC):
    """
    信号检测器基类。

    子类必须实现:
        name (class attribute)
        required_columns (class attribute)
        direction (class attribute): "long" or "short"
        detect(df) -> pd.Series[bool]
    """

    name: str = "base"
    required_columns: List[str] = []
    direction: str = "long"   # 入场方向，用于胜率计算

    # 研究观察窗（K线数）。仅用于 legacy baseline 和 safety_cap 估算。
    research_horizon_bars: int = 15
    hold_bars: int = 15  # legacy alias，避免旧调用点失效

    def resolved_research_horizon_bars(self) -> int:
        value = getattr(self, "research_horizon_bars", None)
        if value is None:
            value = getattr(self, "hold_bars", 15)
        return int(value)

    def detect(self, df: pd.DataFrame) -> pd.Series:
        """
        检测信号触发时刻。

        Args:
            df: 完整特征 DataFrame (来自 FeatureEngine.load_date_range)

        Returns:
            bool Series，index 与 df 相同，True 表示该分钟信号触发。
        """
        raise NotImplementedError

    def validate_columns(self, df: pd.DataFrame) -> bool:
        """检查 df 是否包含所需列，并警告全 NaN 列。"""
        import logging
        _log = logging.getLogger(self.name)

        missing = [c for c in self.required_columns if c not in df.columns]
        if missing:
            _log.warning("missing columns: %s -- signal may not fire", missing)
            return False

        # 检查存在但全为 NaN 的列
        all_nan = [
            c for c in self.required_columns
            if c in df.columns and df[c].isna().all()
        ]
        if all_nan:
            _log.warning("columns all-NaN: %s -- signal may not fire", all_nan)
            return False

        return True

    @staticmethod
    def _debounce(arr: np.ndarray, cooldown: int) -> np.ndarray:
        """Apply cooldown between consecutive triggers."""
        result = np.zeros(len(arr), dtype=bool)
        last_trigger = -cooldown
        for i, v in enumerate(arr):
            if v and (i - last_trigger) >= cooldown:
                result[i] = True
                last_trigger = i
        return result

    def report(self, df: pd.DataFrame, fee_pct: float = 0.10) -> dict:
        """
        运行检测并输出统计报告。

        Args:
            fee_pct: 单次往返手续费百分比，默认 0.10%（开仓0.05% + 平仓0.05%，taker费率）。
                     费后收益 = 毛收益 - fee_pct。

        Returns:
            dict with keys:
              trigger_count, trigger_rate, win_rate, profit_factor,
              avg_return_gross_pct, avg_return_net_pct, fee_pct

        入场口径说明:
          entry_price = close[idx]（信号触发 bar 的收盘价）。
          exit_price  = close[idx + research_horizon_bars]。
          注意：这里是 legacy fixed-hold baseline，不代表 live 会在固定 N bar 后离场。
          注意：真实执行会在下一 bar 的市价成交，存在约 0~1 bar 的滑点，
                因此实际表现可能比统计数字差。
        """
        signals = self.detect(df)
        trigger_idx = np.where(signals)[0]
        trigger_count = len(trigger_idx)
        trigger_rate  = trigger_count / max(len(df), 1)
        research_horizon_bars = self.resolved_research_horizon_bars()

        result = {
            "name":                self.name,
            "trigger_count":       trigger_count,
            "trigger_rate":        round(trigger_rate * 100, 3),
            "fee_pct":             fee_pct,
            "research_horizon_bars": research_horizon_bars,
            "win_rate":            None,
            "profit_factor":       None,
            "avg_return_gross_pct": None,
            "avg_return_net_pct":   None,
        }

        if trigger_count == 0:
            return result

        # ── Legacy fixed-hold baseline（只用于研究入场方向，不是 live 出场）────
        close = df["close"].values
        returns_gross = []

        for idx in trigger_idx:
            entry_idx = idx              # 信号 bar 收盘入场（与实时执行口径一致）
            exit_idx  = idx + research_horizon_bars
            if exit_idx >= len(close):
                continue

            entry_price = close[entry_idx]
            exit_price  = close[exit_idx]

            if entry_price <= 0:
                continue

            if self.direction == "long":
                ret = (exit_price - entry_price) / entry_price
            elif self.direction == "short":
                ret = (entry_price - exit_price) / entry_price
            else:  # neutral: 取多空中较大的绝对收益（衡量方向性Alpha）
                ret_long  = (exit_price - entry_price) / entry_price
                ret_short = (entry_price - exit_price) / entry_price
                ret = max(ret_long, ret_short)

            returns_gross.append(ret)

        if not returns_gross:
            return result

        gross = np.array(returns_gross)
        net   = gross - fee_pct / 100     # 扣除往返手续费

        wins_gross = gross[gross > 0]
        losses_gross = gross[gross <= 0]
        wins_net  = net[net > 0]
        losses_net = net[net <= 0]

        win_rate_gross = len(wins_gross) / len(gross)
        win_rate_net   = len(wins_net)   / len(net)

        avg_win_g  = wins_gross.mean()        if len(wins_gross)  > 0 else 0
        avg_loss_g = abs(losses_gross.mean()) if len(losses_gross) > 0 else 0
        avg_win_n  = wins_net.mean()          if len(wins_net)    > 0 else 0
        avg_loss_n = abs(losses_net.mean())   if len(losses_net)  > 0 else 0

        pf_gross = (
            (avg_win_g * len(wins_gross)) / (avg_loss_g * len(losses_gross))
            if len(losses_gross) > 0 and avg_loss_g > 0 else float("inf")
        )
        pf_net = (
            (avg_win_n * len(wins_net)) / (avg_loss_n * len(losses_net))
            if len(losses_net) > 0 and avg_loss_n > 0 else float("inf")
        )

        max_consec_loss_net = _max_consecutive(net <= 0)

        result.update({
            "evaluated":              len(gross),
            "win_rate_gross":         round(win_rate_gross * 100, 1),
            "win_rate_net":           round(win_rate_net   * 100, 1),
            "profit_factor_gross":    round(pf_gross, 2),
            "profit_factor_net":      round(pf_net,   2),
            "avg_return_gross_pct":   round(float(gross.mean()) * 100, 4),
            "avg_return_net_pct":     round(float(net.mean())   * 100, 4),
            "avg_win_gross_pct":      round(avg_win_g  * 100, 4),
            "avg_loss_gross_pct":     round(avg_loss_g * 100, 4),
            "max_consecutive_losses": int(max_consec_loss_net),
        })
        return result


def _max_consecutive(bool_arr: np.ndarray) -> int:
    """计算布尔数组中最长连续 True 的长度。"""
    max_run = cur = 0
    for v in bool_arr:
        if v:
            cur += 1
            max_run = max(max_run, cur)
        else:
            cur = 0
    return max_run
