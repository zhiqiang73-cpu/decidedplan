from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# 进入 pending 的自动筛选闸门:
# 1. 入场要能赚钱，而且 OOS 次数不能太少
# 2. 出场要能说明“力在消退”，而且触发次数不能只是个位数
MIN_REVIEW_OOS_WR = 65.0
MIN_REVIEW_OOS_N = 30
MIN_REVIEW_EDGE_PCT = 0.02
MIN_EXIT_SAMPLES = 30
MIN_EXIT_PF = 1.0
MIN_EXIT_TRIGGER_PCT = 25.0
MIN_EXIT_TRIGGER_COUNT = 10

_DELAYED_API_FEATURES = {
    "taker_ratio_api",
    "long_short_ratio",
    "buy_volume",
    "sell_volume",
}

_DIRECTION_RULES: dict[tuple[str, str], tuple[str, str]] = {
    ("dist_to_24h_low", "<"): ("long", "靠近 24 小时低点更像反弹做多"),
    ("dist_to_24h_high", "<"): ("short", "靠近 24 小时高点更像高位回落做空"),
    ("dist_to_24h_low", ">"): ("short", "远离低点更像下跌延续"),
    ("dist_to_24h_high", ">"): ("long", "远离高点压制更像释放后反弹"),
    ("vwap_deviation", ">"): ("short", "价格明显高于成交均价，更像回归做空"),
    ("vwap_deviation", "<"): ("long", "价格明显低于成交均价，更像回归做多"),
    ("funding_rate", ">"): ("short", "正资金费率说明多头持仓更吃力"),
    ("funding_rate", "<"): ("long", "负资金费率说明空头持仓更吃力"),
    ("taker_buy_sell_ratio", ">"): ("short", "主动买盘过热更像买方用力过猛"),
    ("taker_buy_sell_ratio", "<"): ("long", "主动卖盘过热更像卖方用力过猛"),
    ("position_in_range_24h", "<"): ("long", "日内区间低位更像做多"),
    ("position_in_range_24h", ">"): ("short", "日内区间高位更像做空"),
    ("position_in_range_4h", "<"): ("long", "4 小时区间低位更像做多"),
    ("position_in_range_4h", ">"): ("short", "4 小时区间高位更像做空"),
}


@dataclass(frozen=True)
class ReviewDecision:
    keep_pending: bool
    reasons: list[str]


def _rule_key(card: dict[str, Any]) -> str:
    return str(card.get("rule_str", "") or card.get("id", ""))


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _has_force_decay_exit(exit_info: dict[str, Any]) -> bool:
    exit_method = str(exit_info.get("exit_method", "") or "")
    if exit_method == "causal":
        return True

    top3 = exit_info.get("top3")
    if not isinstance(top3, list):
        return False

    for combo in top3:
        if not isinstance(combo, dict):
            continue
        conditions = combo.get("conditions")
        if not isinstance(conditions, list):
            continue
        for cond in conditions:
            if isinstance(cond, dict) and cond.get("source") == "causal":
                return True
    return False


def _direction_reason(card: dict[str, Any]) -> str | None:
    entry = card.get("entry", {})
    feature = str(entry.get("feature", "") or "")
    operator = str(entry.get("operator", "") or "")
    direction = str(entry.get("direction", "") or "")
    mechanism_type = str(card.get("mechanism_type", "") or "")

    if (
        mechanism_type == "seller_impulse"
        and feature == "taker_buy_sell_ratio"
        and operator == "<"
        and direction == "short"
    ):
        return None

    expected = _DIRECTION_RULES.get((feature, operator))
    if expected is None:
        return None
    expected_direction, story = expected
    if direction == expected_direction:
        return None
    direction_text = "做多" if direction == "long" else "做空"
    expected_text = "做多" if expected_direction == "long" else "做空"
    return f"方向和故事打架: {feature} {operator} 更像 {expected_text}，现在却是 {direction_text}；{story}"


def _entry_reasons(card: dict[str, Any], reasons: list[str]) -> None:
    validation = card.get("validation", {})
    if card.get("status") == "auto_rejected" or validation.get("passed") is False:
        issues = list(validation.get("issues") or [])
        if issues:
            reasons.extend(str(issue) for issue in issues)
        else:
            reasons.append("因果校验没有通过")

    mechanism_type = str(card.get("mechanism_type", "") or "")
    if mechanism_type == "generic_alpha":
        reasons.append("还没落到明确物理机制，仍然是 generic_alpha")

    direction_reason = _direction_reason(card)
    if direction_reason:
        reasons.append(direction_reason)

    entry = card.get("entry", {})
    seed_feature = str(entry.get("feature", "") or "")
    if seed_feature in _DELAYED_API_FEATURES:
        reasons.append(f"入场依赖延迟数据源: {seed_feature}")

    stats = card.get("stats", {})
    oos_wr = _safe_float(stats.get("oos_win_rate"))
    n_oos = _safe_int(stats.get("n_oos"))
    edge = _safe_float(stats.get("oos_net_return"))
    if edge == 0.0:
        edge = _safe_float(stats.get("oos_avg_ret"))

    if oos_wr < MIN_REVIEW_OOS_WR:
        reasons.append(f"OOS 胜率不够: {oos_wr:.2f}% < {MIN_REVIEW_OOS_WR:.0f}%")
    if n_oos < MIN_REVIEW_OOS_N:
        reasons.append(f"OOS 次数太少: {n_oos} < {MIN_REVIEW_OOS_N}")
    if edge <= 0:
        reasons.append(f"OOS 费后边际不赚钱: {edge:.4f}%")
    elif edge < MIN_REVIEW_EDGE_PCT:
        reasons.append(f"OOS 费后边际太薄: {edge:.4f}% < {MIN_REVIEW_EDGE_PCT:.2f}%")


def _exit_reasons(card: dict[str, Any], reasons: list[str]) -> None:
    exit_info = card.get("exit")
    if not isinstance(exit_info, dict) or not exit_info:
        reasons.append("没有完整出场方案")
        return

    if not _has_force_decay_exit(exit_info):
        reasons.append("出场还没有落到明确的力消退条件")

    exit_pf = _safe_float(
        exit_info.get("earliest_pf")
        or exit_info.get("pf")
        or exit_info.get("profit_factor")
    )
    if exit_pf <= MIN_EXIT_PF:
        reasons.append(f"出场利润因子不够: {exit_pf:.3f} <= {MIN_EXIT_PF:.1f}")

    exit_net = _safe_float(
        exit_info.get("net_return_with_exit")
        or exit_info.get("avg_net_pct")
        or exit_info.get("avg_net")
    )
    if exit_net <= 0:
        reasons.append(f"出场后净收益不赚钱: {exit_net:.4f}%")

    improvement = _safe_float(exit_info.get("improvement"))
    if improvement <= 0:
        reasons.append(f"出场没有优于傻拿着: improvement={improvement:.4f}%")

    exit_samples = _safe_int(
        exit_info.get("n_samples")
        or exit_info.get("n_trades")
        or card.get("stats", {}).get("n_oos")
    )
    if exit_samples < MIN_EXIT_SAMPLES:
        reasons.append(f"出场样本太少: {exit_samples} < {MIN_EXIT_SAMPLES}")

    triggered_exit_pct = _safe_float(exit_info.get("triggered_exit_pct"))
    if triggered_exit_pct <= 0:
        reasons.append("缺少出场触发频次证据")
    else:
        triggered_count = int(round(exit_samples * triggered_exit_pct / 100.0))
        if triggered_exit_pct < MIN_EXIT_TRIGGER_PCT:
            reasons.append(
                f"出场触发占比太低: {triggered_exit_pct:.2f}% < {MIN_EXIT_TRIGGER_PCT:.0f}%"
            )
        if triggered_count < MIN_EXIT_TRIGGER_COUNT:
            reasons.append(
                f"真正触发出场的次数太少: {triggered_count} < {MIN_EXIT_TRIGGER_COUNT}"
            )

    if exit_info.get("complementarity_passed") is False:
        reasons.append("组合出场不比单条出场更好")

    top3 = exit_info.get("top3")
    if not isinstance(top3, list) or not top3:
        reasons.append("缺少出场候选组合 top3")


def review_card(card: dict[str, Any]) -> ReviewDecision:
    reasons: list[str] = []
    _entry_reasons(card, reasons)
    _exit_reasons(card, reasons)
    return ReviewDecision(keep_pending=not reasons, reasons=reasons)


def mark_pending(card: dict[str, Any], *, reviewed_at: str | None = None) -> dict[str, Any]:
    reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    out = dict(card)
    out["status"] = "pending"
    out["review"] = {
        "reviewed_at": reviewed_at,
        "reviewer": "system",
        "verdict": "keep_pending",
        "reason": "保留待复核: 入场和出场能讲成一组，费后赚钱，而且次数够多",
    }
    return out


def mark_flagged(
    card: dict[str, Any],
    reasons: list[str],
    *,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    out = dict(card)
    out["status"] = "flagged"
    out["rejection_reason"] = "; ".join(reasons)
    out["review"] = {
        "reviewed_at": reviewed_at,
        "reviewer": "system",
        "verdict": "flagged_before_pending",
        "reasons": list(reasons),
    }
    return out


def split_review_candidates(cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    reviewed_at = datetime.now(timezone.utc).isoformat()
    pending: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()

    for card in cards:
        decision = review_card(card)
        if decision.keep_pending:
            pending.append(mark_pending(card, reviewed_at=reviewed_at))
            continue
        flagged.append(mark_flagged(card, decision.reasons, reviewed_at=reviewed_at))
        reason_counts.update(decision.reasons)

    return pending, flagged, dict(reason_counts)


def merge_pending_rules(
    existing: list[dict[str, Any]],
    new_pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for card in existing:
        key = _rule_key(card)
        if key:
            merged[key] = card
    for card in new_pending:
        key = _rule_key(card)
        if key:
            merged[key] = card
    return list(merged.values())


def merge_flagged_rules(
    existing: list[dict[str, Any]],
    new_flags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for card in existing:
        key = _rule_key(card)
        if key:
            merged[key] = card
    for card in new_flags:
        key = _rule_key(card)
        if key:
            merged[key] = card
    return list(merged.values())
