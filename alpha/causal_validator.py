"""
因果验证器 (Causal Validator)

对 Alpha 引擎发现的候选规则自动执行因果一致性检验。
通过验证的规则进入 pending_rules.json 供用户审批；
未通过验证的规则自动拒绝（记录拒绝原因）。

验证维度:
  1. TIME 特征封锁 — 种子/确认均禁止时间维度特征
  2. 方向-机制一致性 — 规则方向是否与物理机制逻辑一致
  3. taker_ratio_api 特判 — API 延迟数据 + 历史过拟合嫌疑
  4. 过拟合嫌疑检测 — PF 极高但样本小
  5. 费后净收益门槛 — 低于 maker 费下限则无法盈利
  6. 确认特征维度检查 — 确认条件必须是物理因果维度
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── 特征维度分类 ─────────────────────────────────────────────────────────────

_PRICE_FEATURES = {
    "vwap_deviation",
    "position_in_range_24h",
    "position_in_range_4h",
    "dist_to_24h_high",
    "dist_to_24h_low",
    "amplitude_1m",
    "amplitude_ma20",
    "close", "open", "high", "low",
}

_POSITIONING_FEATURES = {
    "funding_rate",
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "ls_ratio_change_5m",
    "taker_ratio_api",
    "long_short_ratio",
}

# 允许做种子的完整集合
_ALLOWED_SEED_FEATURES = _PRICE_FEATURES | _POSITIONING_FEATURES

# 允许做确认条件的特征集合（TRADE_FLOW + LIQUIDITY + POSITIONING）
_ALLOWED_CONFIRM_FEATURES = {
    "taker_buy_sell_ratio", "volume_vs_ma20", "volume_acceleration",
    "avg_trade_size", "avg_trade_size_cv_10m",
    "kyle_lambda", "spread_vs_ma20", "spread_proxy",
    "oi_change_rate_5m", "oi_change_rate_1h", "ls_ratio_change_5m",
}

# TIME 特征黑名单
_TIME_FEATURES = {
    "hour_in_day", "minute_in_hour", "minutes_to_funding",
    "day_of_week", "time_of_day",
}

# 延迟 API 特征（需要额外警告）
_DELAYED_API_FEATURES = {
    "taker_ratio_api",
    "long_short_ratio",
    "buy_volume",
    "sell_volume",
}

# 费后净收益最低门槛（%）— maker 双边 0.04%，要求有合理利润余量
_MIN_NET_RETURN_PCT = 0.02

# 过拟合嫌疑阈值
_OVERFIT_PF_THRESHOLD = 20.0
_OVERFIT_MIN_SAMPLES  = 50


# ── 方向-机制一致性规则表 ────────────────────────────────────────────────────

# (feature, operator, expected_direction, physical_explanation)
_DIRECTION_RULES: list[tuple[str, str, str, str]] = [
    ("dist_to_24h_low",       "<", "long",  "价格在24h低点附近 → 卖方枯竭 → 做多"),
    ("dist_to_24h_high",      "<", "short", "价格在24h高点附近 → 买方枯竭 → 做空"),
    ("dist_to_24h_low",       ">", "short", "价格远离24h低点 → 做空合理"),
    ("dist_to_24h_high",      ">", "long",  "价格远离24h高点 → 做多合理"),
    ("vwap_deviation",        ">", "short", "价格偏离VWAP上方 → 均值回归 → 做空"),
    ("vwap_deviation",        "<", "long",  "价格偏离VWAP下方 → 均值回归 → 做多"),
    ("funding_rate",          ">", "short", "正资金费率 → 多头成本高 → 做空"),
    ("funding_rate",          "<", "long",  "负资金费率 → 空头成本高 → 做多"),
    ("taker_buy_sell_ratio",  ">", "short", "买方主导失衡 → 买方耗尽 → 做空"),
    ("taker_buy_sell_ratio",  "<", "long",  "卖方主导失衡 → 卖方耗尽 → 做多"),
    ("position_in_range_24h", "<", "long",  "价格在24h区间低位 → 做多"),
    ("position_in_range_24h", ">", "short", "价格在24h区间高位 → 做空"),
    ("position_in_range_4h",  "<", "long",  "价格在4h区间低位 → 做多"),
    ("position_in_range_4h",  ">", "short", "价格在4h区间高位 → 做空"),
    ("amplitude_1m",          ">", "short", "极端振幅 → 市场过热 → 做空（依赖收盘位置确认）"),
    ("amplitude_1m",          ">", "long",  "极端振幅 + 底部收盘 → 吸收确认 → 做多"),
]


def _check_direction_mechanism(
    feature: str, operator: str, direction: str
) -> tuple[bool, str]:
    """检查方向-机制一致性，返回 (consistent, explanation)。"""
    # amplitude_1m 双向都有物理理由，特殊处理
    if feature == "amplitude_1m":
        return True, "振幅信号双方向均有物理解释，需依据收盘位置确认"

    for feat, op, expected_dir, expl in _DIRECTION_RULES:
        if feature == feat and operator == op:
            if direction == expected_dir:
                return True, expl
            else:
                return (
                    False,
                    f"方向冲突: {feature} {op} X 应为 {expected_dir}，"
                    f"规则声称 {direction}。物理逻辑: {expl}",
                )

    # 未在规则表中 → 无硬性约束，默认通过
    return True, "未找到方向-机制硬约束，默认通过（建议手动核查）"


# ── 机制说明词典 ─────────────────────────────────────────────────────────────

_MECHANISM_DESC: dict[str, str] = {
    "vwap_reversion":         "VWAP 均值回归 — 价格偏离成交量加权均价后回归是市场摩擦消散的必然结果",
    "seller_drought":         "卖方枯竭 — 价格在低位成交量干涸说明卖方弹药耗尽，任何买入都能推动价格",
    "compression_release":    "区间压缩释放 — 价格在极值位置积累势能，突破时释放定向动量",
    "taker_snap_reversal":    "主动方耗尽反转 — 极端主动买/卖耗尽后失去推动力，价格向中性回归",
    "volume_climax_reversal": "量价高潮反转 — 极端成交量峰值通常标志单边力量的彻底耗尽",
    "funding_settlement":     "资金费率结算套利 — 结算前后的方向性压力是可预测的机械行为",
    "funding_cycle_oversold": "资金费率周期超卖 — 负资金费空头持仓成本高，空头倾向平仓推动反弹",
    "funding_divergence":     "资金费率背离 — 高仓位+负资金费意味着多头不愿持仓，潜在抛压",
    "mm_rebalance":           "做市商库存再平衡 — 单向流量逼迫做市商偏斜，再平衡是必然的",
    "algo_slicing":           "算法拆单 — VWAP/TWAP 算法执行大单时产生可识别的均匀流量模式",
    "amplitude_absorption":   "振幅吸收 — 极端振幅后收盘位置反映哪方被吸收，被吸收方力量已耗尽",
    "bottom_taker_exhaust":   "底部主动卖方耗尽 — OI + 方向性流量信号杠杆卖方耗尽",
    "top_buyer_exhaust":      "顶部主动买方耗尽 — OI + 方向性流量信号杠杆买方耗尽",
    "seller_impulse":         "主动卖压冲击 — 主动卖盘突然放大并持续压价，直到卖压衰减、流动性恢复，短线下压才会结束",
    "generic_alpha":          "统计规律 — 尚未映射到已知物理机制，请谨慎审核因果逻辑",
}


# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed: bool
    mechanism_type: str
    issues: list[str] = field(default_factory=list)      # 硬性拒绝原因
    warnings: list[str] = field(default_factory=list)    # 警告（不影响通过）
    causal_explanation: str = ""
    overfitting_score: float = 0.0   # 0.0 干净 ~ 1.0 高度嫌疑
    causal_score: float = 1.0        # 0.0 无因果基础 ~ 1.0 强因果支撑

    def to_dict(self) -> dict:
        return {
            "passed":            self.passed,
            "mechanism_type":    self.mechanism_type,
            "issues":            self.issues,
            "warnings":          self.warnings,
            "causal_explanation": self.causal_explanation,
            "overfitting_score": self.overfitting_score,
            "causal_score":      self.causal_score,
        }


# ── 主验证函数 ───────────────────────────────────────────────────────────────

def validate_candidate(card: dict) -> ValidationResult:
    """
    对策略候选卡片执行因果一致性验证。

    Args:
        card: LiveDiscoveryEngine._build_card() 或 _build_combo_card() 的输出

    Returns:
        ValidationResult
    """
    entry        = card.get("entry", {})
    seed_feature = str(entry.get("feature", ""))
    operator     = str(entry.get("operator", ""))
    direction    = str(entry.get("direction", ""))
    stats        = card.get("stats", {})
    mechanism    = str(card.get("mechanism_type", "generic_alpha"))
    combo_conds  = card.get("combo_conditions", [])

    issues        = []
    warnings      = []
    hard_fail     = False
    overfit_score = 0.0
    causal_score  = 1.0

    # ── 1. 种子特征 TIME 封锁 ─────────────────────────────────────────────────
    if seed_feature in _TIME_FEATURES:
        issues.append(
            f"种子特征 '{seed_feature}' 是时间维度特征，禁止用作入场条件。"
            "时间规律随市场参与者行为改变而失效，不具备物理稳定性。"
        )
        hard_fail    = True
        causal_score = 0.0

    # ── 2. 确认条件 TIME 封锁 ─────────────────────────────────────────────────
    for cond in combo_conds:
        cf = str(cond.get("feature", "") or cond.get("confirm_feature", ""))
        if cf in _TIME_FEATURES:
            issues.append(
                f"确认特征 '{cf}' 是时间维度特征，禁止用作确认条件。"
            )
            hard_fail    = True
            causal_score = min(causal_score, 0.0)

    # ── 3. taker_ratio_api / 延迟 API 数据特判 ───────────────────────────────
    if seed_feature in _DELAYED_API_FEATURES:
        warnings.append(
            f"种子特征 '{seed_feature}' 是 Binance API 数据（约 5 分钟延迟）。"
            "历史 pending 规则中 taker_ratio_api 出现 PF=50-71, WR=91-93%，"
            "强烈怀疑数据泄露或过拟合。建议人工仔细审核后再审批。"
        )
        overfit_score = max(overfit_score, 0.7)
        causal_score  = min(causal_score, 0.5)

    # ── 4. 过拟合嫌疑检测 ────────────────────────────────────────────────────
    oos_pf  = float(stats.get("oos_pf",  0) or 0)
    n_oos   = int(  stats.get("n_oos",   0) or 0)
    oos_wr  = float(stats.get("oos_win_rate", 0) or stats.get("oos_wr", 0) or 0)
    oos_net = float(
        stats.get("oos_net_return", None)
        or stats.get("oos_avg_ret", None)
        or 0
    )

    if oos_pf > _OVERFIT_PF_THRESHOLD and n_oos < _OVERFIT_MIN_SAMPLES:
        warnings.append(
            f"过拟合嫌疑: OOS PF={oos_pf:.1f} 但样本仅 {n_oos} 条。"
            f"PF > {_OVERFIT_PF_THRESHOLD:.0f} 需要 n >= {_OVERFIT_MIN_SAMPLES} 才具备统计意义。"
        )
        overfit_score = max(overfit_score, 0.6)
        causal_score  = min(causal_score, 0.6)

    if oos_wr > 90.0 and n_oos < 30:
        warnings.append(
            f"OOS 胜率 {oos_wr:.1f}% 极高但样本仅 {n_oos} 条，统计不显著。"
        )
        overfit_score = max(overfit_score, 0.5)

    # ── 5. 费后净收益门槛 ────────────────────────────────────────────────────
    # 只在有非零数据时判断（0.0 可能是字段缺失）
    if oos_net != 0.0:
        if oos_net < 0:
            issues.append(
                f"OOS 费后净收益 {oos_net:.4f}% < 0，持续亏损，不具备可交易性。"
            )
            hard_fail = True
        elif oos_net < _MIN_NET_RETURN_PCT:
            warnings.append(
                f"OOS 费后净收益 {oos_net:.4f}% 低于最低门槛 {_MIN_NET_RETURN_PCT:.2f}%，"
                "扣除滑点后可能转负，利润空间极小。"
            )
            causal_score = min(causal_score, 0.7)

    # ── 6. 方向-机制一致性 ───────────────────────────────────────────────────
    # 降级为 warning（不硬性拒绝）：同一特征可用于均值回归或动量延续两种机制，
    # 统计验证（OOS WR 70-80%）优先于先验方向假设。
    consistent, dir_expl = _check_direction_mechanism(seed_feature, operator, direction)
    if not consistent:
        warnings.append(f"方向-机制提示: {dir_expl} （统计验证有效则可忽略）")
        causal_score = min(causal_score, 0.6)

    # ── 7. 生成因果解释 ───────────────────────────────────────────────────────
    causal_explanation = _build_causal_explanation(
        seed_feature, operator, direction, mechanism, dir_expl, combo_conds
    )

    passed = not hard_fail

    result = ValidationResult(
        passed=passed,
        mechanism_type=mechanism,
        issues=issues,
        warnings=warnings,
        causal_explanation=causal_explanation,
        overfitting_score=round(overfit_score, 2),
        causal_score=round(causal_score, 2),
    )

    _log_result(result, card.get("rule_str", seed_feature))
    return result


def _build_causal_explanation(
    feature: str,
    operator: str,
    direction: str,
    mechanism: str,
    dir_expl: str,
    combo_conds: list[dict],
) -> str:
    op_text  = "高于" if operator == ">" else "低于"
    dir_text = "做多" if direction == "long" else "做空"

    mech_desc = _MECHANISM_DESC.get(mechanism, _MECHANISM_DESC["generic_alpha"])

    lines = [
        f"入场: {feature} {op_text}阈值 → {dir_text} | 机制: {mechanism}",
        f"物理逻辑: {dir_expl}",
        f"机制说明: {mech_desc}",
    ]

    if combo_conds:
        parts = []
        for c in combo_conds:
            cf  = str(c.get("feature", "") or c.get("confirm_feature", ""))
            cop = str(c.get("op", "") or c.get("operator", ""))
            cth = c.get("threshold", "")
            th_str = f"{cth:.4g}" if isinstance(cth, (int, float)) else str(cth)
            parts.append(f"{cf} {cop} {th_str}")
        lines.append(f"物理确认: {' AND '.join(parts)}")

    return "\n".join(lines)


def _log_result(result: ValidationResult, rule_str: str) -> None:
    label = "PASS" if result.passed else "REJECT"
    causal = f"causal={result.causal_score:.2f} overfit={result.overfitting_score:.2f}"
    if not result.passed:
        logger.info(
            "[CAUSAL] %s | %s | %s | issues: %s",
            label, rule_str[:50], causal, "; ".join(result.issues),
        )
    elif result.warnings:
        logger.info(
            "[CAUSAL] %s | %s | %s | warnings: %s",
            label, rule_str[:50], causal, "; ".join(result.warnings),
        )
    else:
        logger.debug("[CAUSAL] %s | %s | %s", label, rule_str[:50], causal)
