"""订单流分类器。"""

import logging
import threading
import typing

import pandas as pd

logger = logging.getLogger(__name__)

LIQUIDATION = "LIQUIDATION"
AGGRESSIVE_BUY = "AGGRESSIVE_BUY"
AGGRESSIVE_SELL = "AGGRESSIVE_SELL"
PASSIVE = "PASSIVE"

LIQ_USD_THRESHOLD = 50000.0
OI_DROP_THRESHOLD = -0.005
AMP_SPIKE_MULT = 2.0

TAKER_IMBALANCE = 0.15
VOL_SURGE = 1.5
OI_GROWTH_MIN = 0.001

CONFIRM_BARS = 3


class FlowClassifier:
    """
    订单流分类器（规则-based，无 ML）。

    物理原理:
      不同类型的订单流会直接改变信号是否可靠。
      机构方向性建仓更像“有主力在推”，被动换手则更像噪声背景。

    分类优先级: LIQUIDATION > AGGRESSIVE > PASSIVE
    """

    def __init__(self):
        self._flow_history: list[str] = []
        self._current_flow: str = PASSIVE
        self._lock = threading.Lock()

    def classify(self, row: pd.Series) -> str:
        """
        判断当前 bar 的主导订单流类型。

        Args:
            row: 当前 K 线的特征行。

        Returns:
            流类型（LIQUIDATION / AGGRESSIVE_BUY /
            AGGRESSIVE_SELL / PASSIVE）。
        """
        raw = self._classify_raw(row)
        with self._lock:
            self._flow_history.append(raw)
            if len(self._flow_history) > CONFIRM_BARS:
                self._flow_history.pop(0)

            if raw == LIQUIDATION:
                if self._current_flow != LIQUIDATION:
                    logger.info(
                        "[FLOW] %s -> %s (immediate switch)",
                        self._current_flow,
                        LIQUIDATION,
                    )
                    self._current_flow = LIQUIDATION
                return self._current_flow

            if len(self._flow_history) == CONFIRM_BARS:
                if all(flow == raw for flow in self._flow_history):
                    if raw != self._current_flow:
                        logger.info(
                            "[FLOW] %s -> %s (confirmed %s consecutive bars)",
                            self._current_flow,
                            raw,
                            CONFIRM_BARS,
                        )
                        self._current_flow = raw

            return self._current_flow

    @property
    def current_flow(self) -> str:
        """返回当前已确认的订单流类型。"""
        return self._current_flow

    def _classify_raw(self, row: pd.Series) -> str:
        """
        对单根 bar 做原始订单流判断（未经过确认）。

        优先级: LIQUIDATION > AGGRESSIVE_BUY > AGGRESSIVE_SELL > PASSIVE
        """
        total_liq = _safe_get(row, "total_liq_usd_5m", default=0.0)
        oi_5m = _safe_get(row, "oi_change_rate_5m", default=0.0)
        amp_1m = _safe_get(row, "amplitude_1m", default=0.0)
        amp_ma20 = _safe_get(row, "amplitude_ma20", default=None)
        taker_ratio = _safe_get(row, "taker_buy_sell_ratio", default=1.0)
        vol_vs_ma20 = _safe_get(row, "volume_vs_ma20", default=1.0)

        if total_liq > LIQ_USD_THRESHOLD:
            return LIQUIDATION
        if (
            oi_5m < OI_DROP_THRESHOLD
            and amp_ma20 is not None
            and amp_1m > amp_ma20 * AMP_SPIKE_MULT
        ):
            return LIQUIDATION

        if (
            taker_ratio > 1.0 + TAKER_IMBALANCE
            and vol_vs_ma20 > VOL_SURGE
            and oi_5m > OI_GROWTH_MIN
        ):
            return AGGRESSIVE_BUY

        if (
            taker_ratio < 1.0 - TAKER_IMBALANCE
            and vol_vs_ma20 > VOL_SURGE
            and oi_5m > OI_GROWTH_MIN
        ):
            return AGGRESSIVE_SELL

        return PASSIVE


def _safe_get(row: pd.Series, col: str, default: typing.Any = None):
    """安全获取特征值，缺失或 NaN 时返回默认值。"""
    if col not in row.index:
        return default
    val = row[col]
    if pd.isna(val):
        return default
    return float(val)
