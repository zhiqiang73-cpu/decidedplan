"""
市场制度识别器 (Regime Detector)

物理原理:
  市场制度不同，Alpha 规则的有效性完全不同。
  在错误的制度下入场 = 统计上正确但物理上无效。

  制度识别不用 ML，用物理可解释的特征规则:
    - 波动率（amplitude_ma20）: 判断市场"安静"还是"激进"
    - 成交量（volume_vs_ma20）: 判断流量是否异常放大
    - 流动性（spread_vs_ma20, kyle_lambda）: 判断市场结构是否健康
    - 杠杆变化（oi_change_rate_1h）: 判断是否有大规模去杠杆

5 种制度及对信号的影响:

  QUIET_TREND    低波动趋势    → 所有信号正常入场
  VOLATILE_TREND 高波动趋势    → 仅 MEDIUM+ 置信度信号入场
  RANGE_BOUND    区间震荡      → SHORT 信号最有效（均值回归）
  VOL_EXPANSION  波动率爆发    → 高危，仅 HIGH 置信度 SHORT 入场，禁 LONG
  CRISIS         危机/去杠杆   → 禁止所有 P2 Alpha 入场，仅保留事件型 P1

实盘验证已知规则:
  - 大跌日应更严格限制抄底类 LONG
  - 全部 SHORT 规则在 VOLATILE_TREND 下跌最有效（Mar 18-19 数据）
  - CRISIS 期间 Alpha 规则系统性失效（极端事件打破统计规律）
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── 制度常量 ──────────────────────────────────────────────────────────────────
QUIET_TREND    = "QUIET_TREND"
VOLATILE_TREND = "VOLATILE_TREND"
RANGE_BOUND    = "RANGE_BOUND"
VOL_EXPANSION  = "VOL_EXPANSION"
CRISIS         = "CRISIS"

# ── 制度判断阈值（基于物理特征，不用 ML）─────────────────────────────────────

# 1分钟K线振幅均值（high-low/close）的制度边界
AMP_QUIET_MAX   = 0.0015   # < 0.15% 均振幅 = 安静
AMP_VOLATILE    = 0.0025   # > 0.25% 均振幅 = 活跃
AMP_CRISIS      = 0.0050   # > 0.50% 均振幅 = 极端

# 成交量倍数边界
VOL_SPIKE       = 2.0      # 成交量 > 2x 均值 = 放量
VOL_EXTREME     = 3.0      # 成交量 > 3x 均值 = 极端放量

# 价差边界
SPREAD_WIDE     = 2.0      # 价差 > 2x 均值 = 流动性恶化
SPREAD_CRISIS   = 3.5      # 价差 > 3.5x 均值 = 流动性危机

# OI 1小时变化（去杠杆阈值）
OI_DELEVER      = -0.03    # OI 1小时下降 > 3% = 大规模去杠杆

# 24h区间位置（判断震荡）
RANGE_CENTER_LOW  = 0.30
RANGE_CENTER_HIGH = 0.70

# 制度切换需要连续确认的 bar 数（避免单根 K 线抖动）
CONFIRM_BARS = 3


class RegimeDetector:
    """
    市场制度识别器（规则-based，无 ML）。

    设计原则：
      - 每条判断规则必须有物理解释
      - 宁可误判为保守制度（少入场），不要误判为激进制度（多入场）
      - 制度切换有惯性（CONFIRM_BARS 根 K 线确认），避免高频抖动

    用法:
        detector = RegimeDetector()
        regime = detector.detect(row, df_tail)
        allowed = detector.filter_alerts(alerts, regime)
    """

    def __init__(self):
        self._regime_history = []   # 最近 CONFIRM_BARS 根 K 线的原始制度判断
        self._current_regime = QUIET_TREND
        self._regime_bar_count = 0

    # ── 主接口 ────────────────────────────────────────────────────────────────

    def detect(self, row: pd.Series, df_tail: pd.DataFrame) -> str:
        """
        判断当前市场制度并返回制度名称。

        Args:
            row:     当前 K 线的特征行
            df_tail: 最近若干根 K 线（用于趋势判断）

        Returns:
            制度名称（QUIET_TREND / VOLATILE_TREND / RANGE_BOUND /
                      VOL_EXPANSION / CRISIS）
        """
        raw = self._classify_raw(row)

        # 制度切换惯性：连续 CONFIRM_BARS 根一致才切换
        self._regime_history.append(raw)
        if len(self._regime_history) > CONFIRM_BARS:
            self._regime_history.pop(0)

        # 如果最近 CONFIRM_BARS 根都是同一制度 → 切换
        if len(self._regime_history) == CONFIRM_BARS:
            if all(r == raw for r in self._regime_history):
                if raw != self._current_regime:
                    logger.info(
                        f"[REGIME] {self._current_regime} -> {raw} "
                        f"(confirmed {CONFIRM_BARS} consecutive bars)"
                    )
                    self._current_regime = raw

        return self._current_regime

    def filter_alerts(self, alerts: list, regime: str) -> list:
        """
        根据制度过滤信号列表。

        过滤逻辑（物理依据）:
          CRISIS:         禁止所有 P2 Alpha 信号
                          P2 = 统计均值回归，危机期间统计规律失效
          VOL_EXPANSION:  禁止 LONG 入场（死猫反弹风险极高）
                          仅允许 HIGH 置信度 SHORT 入场
          VOLATILE_TREND: 仅允许 MEDIUM+ 置信度信号
          RANGE_BOUND:    所有信号正常（震荡中均值回归最有效）
          QUIET_TREND:    所有信号正常

        Returns:
            过滤后的信号列表，每条被过滤的信号会打印 WARNING 日志
        """
        if not alerts:
            return []

        filtered = []
        for a in alerts:
            phase     = a.get("phase", "")
            direction = a.get("direction", "").lower()
            conf      = a.get("confidence", 1)
            name      = a.get("name", "")

            reject_reason = self._check_regime_filter(
                regime, phase, direction, conf
            )

            if reject_reason:
                logger.warning(
                    f"[REGIME {regime}] Rejected {name} ({direction.upper()}) "
                    f"confidence={conf}: {reject_reason}"
                )
                continue

            # 给信号附加制度标签（便于 execution_log 记录）
            a = dict(a)
            a["regime"] = regime
            filtered.append(a)

        return filtered

    @property
    def current_regime(self) -> str:
        return self._current_regime

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _classify_raw(self, row: pd.Series) -> str:
        """
        对单根 K 线做原始制度判断（未经惯性确认）。
        优先级: CRISIS > VOL_EXPANSION > RANGE_BOUND > VOLATILE_TREND > QUIET_TREND
        """
        amp    = _safe_get(row, "amplitude_ma20",    default=0.001)
        vol    = _safe_get(row, "volume_vs_ma20",    default=1.0)
        spread = _safe_get(row, "spread_vs_ma20",    default=1.0)
        oi_1h  = _safe_get(row, "oi_change_rate_1h", default=0.0)
        range_pos = _safe_get(row, "position_in_range_24h", default=0.5)

        # ── CRISIS: 流动性崩塌 OR 大规模去杠杆 ──────────────────────────────
        # 物理原理: 去杠杆是不可逆的（保证金追缴 = 必须平仓），Alpha 规则在此失效
        if spread > SPREAD_CRISIS:
            return CRISIS
        if oi_1h is not None and oi_1h < OI_DELEVER and amp > AMP_VOLATILE:
            return CRISIS

        # ── VOL_EXPANSION: 波动率爆发 + 成交量极端放大 ──────────────────────
        # 物理原理: 大量仓位在清算，方向不明，LONG 随时被死猫反弹消灭
        if amp > AMP_CRISIS and vol > VOL_EXTREME:
            return VOL_EXPANSION
        if amp > AMP_VOLATILE and vol > VOL_SPIKE and spread > SPREAD_WIDE:
            return VOL_EXPANSION

        # ── RANGE_BOUND: 价格在 24h 区间中部 + 低波动 ───────────────────────
        # 物理原理: 区间内没有趋势动量，均值回归 Alpha 最有效
        if (RANGE_CENTER_LOW <= (range_pos or 0.5) <= RANGE_CENTER_HIGH
                and amp < AMP_VOLATILE):
            return RANGE_BOUND

        # ── VOLATILE_TREND: 活跃波动（有方向但不极端）──────────────────────
        if amp > AMP_VOLATILE:
            return VOLATILE_TREND

        # ── QUIET_TREND: 默认（低波动，趋势或震荡均可）─────────────────────
        return QUIET_TREND

    @staticmethod
    def _check_regime_filter(
        regime: str, phase: str, direction: str, confidence: int
    ) -> Optional[str]:
        """
        返回拒绝原因，None 表示允许通过。
        """
        if regime == CRISIS:
            # 危机期：禁止所有 P2 Alpha（纯统计在危机中无效）
            if phase == "P2":
                return "CRISIS: P2 Alpha statistical rules invalid"
            # P1 事件型信号允许通过（物理机制更强）

        elif regime == VOL_EXPANSION:
            # 波动爆发：禁止 LONG（死猫反弹），SHORT 需要 HIGH 置信度
            if direction == "long" and phase == "P2":
                return "VOL_EXPANSION: P2 LONG blocked (dead-cat bounce risk)"
            if direction == "short" and confidence < 3:
                return f"VOL_EXPANSION: SHORT requires HIGH confidence, got={confidence}"

        elif regime == VOLATILE_TREND:
            # 高波动：仅允许 MEDIUM+ 置信度
            if confidence < 2:
                return f"VOLATILE_TREND: minimum MEDIUM confidence required, got={confidence}(LOW)"

        # RANGE_BOUND / QUIET_TREND: 所有信号通过
        return None


def _safe_get(row: pd.Series, col: str, default=None):
    """安全获取特征值，NaN 视为缺失。"""
    if col not in row.index:
        return default
    val = row[col]
    if pd.isna(val):
        return default
    return float(val)
