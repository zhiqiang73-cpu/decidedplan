"""
观察式离场策略表(影子模式)

服务 8 个核心策略家族 + Alpha 卡片:
P0-2 / P1-2 / P1-6 / P1-8 / P1-9 / P1-10 / P1-11 / C1 / ALPHA

目标不是直接替代真实执行，而是在影子模式里回答三件事：
1. 原始入场逻辑是否还在
2. 逻辑是不是开始减弱，应该启动利润保护
3. 逻辑是否已经失效，应该立即离场

输出统一为：
  - hold     继续持有
  - protect  启动/收紧利润保护
  - exit     立即离场
"""


from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from monitor.exit_policy_config import ExitParams, resolve_max_hold_bars

SNAPSHOT_COLUMNS = (
    "vwap_deviation",
    "volume_vs_ma20",
    "position_in_range_24h",
    "position_in_range_4h",
    "dist_to_24h_low",
    "dist_to_24h_high",
    "funding_rate",
    "minutes_to_funding",
    "taker_buy_sell_ratio",
    "taker_buy_pct",
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "spread_vs_ma20",
    "volume_autocorr_lag5",
    "avg_trade_size_cv_10m",
    "amplitude_ma20",
    "amplitude_1m",
    "kyle_lambda",
    "minute_in_hour",
)

_EXIT_REASON_SET = {"reverse_structure", "logic_failed", "logic_complete", "regime_shift"}
LOGIC_COMPLETE_MIN_RETURN_PCT = 0.0


def _coerce_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _normalize_alpha_exit_conditions(payload: object) -> list[dict[str, float | str]]:
    if isinstance(payload, dict):
        raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        return []

    normalized: list[dict[str, float | str]] = []
    seen: set[tuple[str, str, float]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        feature = str(item.get("feature", "")).strip()
        operator = str(item.get("operator") or item.get("op") or "").strip()
        threshold = _coerce_float(item.get("threshold"))
        if not feature or operator not in {"<", ">"} or threshold is None:
            continue
        key = (feature, operator, threshold)
        if key in seen:
            continue
        seen.add(key)

        condition: dict[str, float | str] = {
            "feature": feature,
            "operator": operator,
            "threshold": threshold,
        }
        for extra_key in ("expected_hold_bars", "net_return_with_exit", "improvement", "n_samples"):
            extra_value = _coerce_float(item.get(extra_key))
            if extra_value is not None:
                condition[extra_key] = extra_value
        normalized.append(condition)
    return normalized


def _normalize_alpha_exit_params(payload: object) -> dict[str, float | int] | None:
    if not isinstance(payload, dict):
        return None

    normalized: dict[str, float | int] = {}
    float_keys = (
        "take_profit_pct",
        "stop_pct",
        "protect_start_pct",
        "protect_gap_ratio",
        "protect_floor_pct",
        "decay_exit_threshold",
        "decay_tighten_threshold",
        "tighten_gap_ratio",
    )
    int_keys = ("min_hold_bars", "max_hold_factor", "exit_confirm_bars")

    for key in float_keys:
        value = _coerce_float(payload.get(key))
        if value is not None:
            normalized[key] = value

    for key in int_keys:
        value = _coerce_float(payload.get(key))
        if value is not None:
            normalized[key] = int(value)

    return normalized or None


def _build_alpha_exit_summary(
    exit_conditions: list[dict[str, float | str]],
    stop_pct: Optional[float],
    fallback_horizon: object,
) -> str:
    parts: list[str] = []
    if stop_pct is not None and stop_pct > 0:
        parts.append(f"Stop {stop_pct:.2f}%")

    if exit_conditions:
        main = exit_conditions[0]
        parts.append(
            f"Card exit: {main['feature']} {main['operator']} {float(main['threshold']):.6f}"
        )
        hold = _coerce_float(main.get("expected_hold_bars"))
        if hold is not None and hold > 0:
            parts.append(f"Expected hold ~{hold:.0f} bars")
        if len(exit_conditions) > 1:
            parts.append(f"{len(exit_conditions)} exit features armed")
    else:
        try:
            horizon = int(fallback_horizon or 0)
        except Exception:
            horizon = 0
        if horizon > 0:
            parts.append(f"No dedicated card exit; fallback hold {horizon} bars")
        else:
            parts.append("No dedicated card exit")

    return " | ".join(parts)


def build_entry_snapshot(alert: Dict, features: Optional[pd.Series]) -> Dict[str, object]:
    alpha_exit_conditions = _normalize_alpha_exit_conditions(
        alert.get("alpha_exit_conditions") or alert.get("exit")
    )
    # Top-3 combos from ExitConditionMiner (preferred over flat conditions)
    alpha_exit_combos = alert.get("alpha_exit_combos") or []
    alpha_exit_params = _normalize_alpha_exit_params(alert.get("alpha_exit_params"))

    stop_pct = _coerce_float(alert.get("stop_pct"))
    snapshot: Dict[str, object] = {
        "family": alert.get("family") or normalize_family(alert.get("name", "")),
        "desc": alert.get("desc", ""),
        "feature": alert.get("feature", ""),
        "feature_value": alert.get("feature_value"),
        "exit_summary": _build_alpha_exit_summary(
            alpha_exit_conditions,
            stop_pct,
            alert.get("horizon"),
        ),
    }
    if alpha_exit_conditions:
        snapshot["alpha_exit_conditions"] = alpha_exit_conditions
    if alpha_exit_combos:
        snapshot["alpha_exit_combos"] = alpha_exit_combos
    if alpha_exit_params:
        snapshot["alpha_exit_params"] = alpha_exit_params
    if stop_pct is not None:
        snapshot["alpha_stop_pct"] = stop_pct
    if features is None:
        return snapshot

    for col in SNAPSHOT_COLUMNS:
        snapshot[col] = _safe_get(features, col)
    return snapshot


def normalize_family(rule_name: str) -> str:
    if not rule_name:
        return ""
    for prefix in (
        "C1_",
        "P0-2_",
        "P1-2_",
        "P1-6_",
        "P1-8_",
        "P1-9_",
        "P1-10_",
        "P1-11_",
    ):
        if rule_name.startswith(prefix):
            return prefix[:-1]
    return rule_name


def evaluate_exit_state(
    position: Dict,
    close: float,
    features: Optional[pd.Series],
) -> Dict[str, object]:
    if features is None:
        return {"action": "hold", "reason": "no_features", "health": 0.0}

    family = str(position.get("family") or normalize_family(position.get("rule", "")))
    current_return = _current_return(position, close)
    snapshot = position.get("entry_snapshot", {}) or {}

    if snapshot.get("alpha_exit_combos") or _normalize_alpha_exit_conditions(snapshot.get("alpha_exit_conditions")):
        return _eval_alpha_card(position, features, snapshot, current_return)

    if family == "P1-8":
        return _eval_p1_8(position, features, snapshot, current_return)
    if family == "P1-11":
        return _eval_p1_11(position, features, snapshot, current_return)
    if family == "P1-6":
        return _eval_p1_6(position, features, snapshot, current_return)
    if family == "P0-2":
        return _eval_p0_2(position, features, snapshot, current_return)
    if family == "P1-10":
        return _eval_p1_10(position, features, snapshot, current_return)
    if family == "P1-9":
        return _eval_p1_9(position, features, snapshot, current_return)
    if family == "P1-2":
        return _eval_p1_2(position, features, snapshot, current_return)

    if family == "C1":
        return _eval_c1(position, features, snapshot, current_return)

    return _eval_generic(position, features, snapshot, current_return)


def _eval_alpha_card(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """Evaluate alpha card exit conditions using Top-3 earliest-trigger strategy.

    Each combo is a list of conditions (AND logic within a combo).
    Any combo fully matched = exit (OR between combos).
    Supports _vs_entry features by computing current - entry from snapshot.
    """
    _VS_ENTRY_TAG = "_vs_entry"

    # Prefer Top-3 combos; fall back to flat conditions
    exit_combos = snapshot.get("alpha_exit_combos")
    if isinstance(exit_combos, list) and exit_combos:
        # Top-3 combo evaluation: AND within combo, OR between combos
        for combo_idx, combo in enumerate(exit_combos):
            if not isinstance(combo, list) or not combo:
                continue
            all_conditions_met = True
            matched_parts: list[str] = []
            for condition in combo:
                if not isinstance(condition, dict):
                    all_conditions_met = False
                    break
                feature = str(condition.get("feature", "")).strip()
                operator = str(condition.get("operator") or condition.get("op") or "").strip()
                try:
                    threshold = float(condition.get("threshold"))
                except (TypeError, ValueError):
                    all_conditions_met = False
                    break

                # Resolve value: _vs_entry features need delta computation
                if feature.endswith(_VS_ENTRY_TAG):
                    base_col = feature[: -len(_VS_ENTRY_TAG)]
                    value = _vs_entry_val(row, snapshot, base_col)
                else:
                    value = _safe_get(row, feature)

                if value is None:
                    all_conditions_met = False
                    break

                is_match = (
                    (operator == ">" and value > threshold)
                    or (operator == "<" and value < threshold)
                )
                if not is_match:
                    all_conditions_met = False
                    break
                matched_parts.append(f"{feature} {operator} {threshold:.6f}")

            if all_conditions_met and matched_parts:
                return {
                    "action": "exit",
                    "reason": "logic_complete",
                    "health": 1.0,
                    "matched_exit": f"C{combo_idx + 1}: {' AND '.join(matched_parts)}",
                }
        return {"action": "hold", "reason": "hold", "health": 0.0}

    # Fallback: legacy flat conditions (each independent, OR logic)
    matched_conditions: list[str] = []
    for condition in _normalize_alpha_exit_conditions(snapshot.get("alpha_exit_conditions")):
        feature = str(condition["feature"])
        operator = str(condition["operator"])
        threshold = float(condition["threshold"])

        if feature.endswith(_VS_ENTRY_TAG):
            base_col = feature[: -len(_VS_ENTRY_TAG)]
            value = _vs_entry_val(row, snapshot, base_col)
        else:
            value = _safe_get(row, feature)

        if value is None:
            continue

        is_match = (operator == ">" and value > threshold) or (operator == "<" and value < threshold)
        if not is_match:
            continue
        matched_conditions.append(f"{feature} {operator} {threshold:.6f}")

    if matched_conditions:
        return {
            "action": "exit",
            "reason": "logic_complete",
            "health": 1.0,
            "matched_exit": matched_conditions[0],
        }
    return {"action": "hold", "reason": "hold", "health": 0.0}


def _eval_p1_8(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2, no TIME features).

    LONG top3 (earliest trigger):
      C1: vwap_deviation_vs_entry > 0.008898 AND dist_to_24h_low_vs_entry > 0.007402 AND vwap_deviation > -0.018165
      C2: position_in_range_24h_vs_entry > 0.090718 AND vwap_deviation_vs_entry > 0.008898 AND vwap_deviation > -0.018165
      C3: dist_to_24h_low_vs_entry > 0.007402 AND dist_to_24h_high_vs_entry > 0.008439 AND vwap_deviation > -0.018165
    Stop: -1.50%

    SHORT top3 (earliest trigger):
      C1: oi_change_rate_5m < 0.805410 AND volume_autocorr_lag5 < -0.013628
      C2: oi_change_rate_5m < 0.805410 AND volume_autocorr_lag5 < -0.013628 AND amplitude_1m < 0.239284
      C3: oi_change_rate_1h < -0.066308
    Stop: -1.00%
    """
    direction = position.get("direction", "").lower()

    if direction == "long":
        vwap = _safe_get(row, "vwap_deviation")
        vwap_vs_entry = _vs_entry_val(row, snapshot, "vwap_deviation")
        dist_low_vs_entry = _vs_entry_val(row, snapshot, "dist_to_24h_low")
        dist_high_vs_entry = _vs_entry_val(row, snapshot, "dist_to_24h_high")
        r24h_vs_entry = _vs_entry_val(row, snapshot, "position_in_range_24h")

        if vwap is not None and vwap > -0.018165:
            c1 = (vwap_vs_entry is not None and vwap_vs_entry > 0.008898
                  and dist_low_vs_entry is not None and dist_low_vs_entry > 0.007402)
            c2 = (r24h_vs_entry is not None and r24h_vs_entry > 0.090718
                  and vwap_vs_entry is not None and vwap_vs_entry > 0.008898)
            c3 = (dist_low_vs_entry is not None and dist_low_vs_entry > 0.007402
                  and dist_high_vs_entry is not None and dist_high_vs_entry > 0.008439)
            if c1 or c2 or c3:
                return {"action": "exit", "reason": "logic_complete", "health": 1.0}
        return {"action": "hold", "reason": "hold", "health": 0.0}

    else:  # short
        oi_5m = _safe_get(row, "oi_change_rate_5m")
        vol_autocorr = _safe_get(row, "volume_autocorr_lag5")
        oi_1h = _safe_get(row, "oi_change_rate_1h")

        c1 = (oi_5m is not None and oi_5m < 0.805410
              and vol_autocorr is not None and vol_autocorr < -0.013628)
        c3 = (oi_1h is not None and oi_1h < -0.066308)
        if c1 or c3:
            return {"action": "exit", "reason": "logic_complete", "health": 1.0}
        return {"action": "hold", "reason": "hold", "health": 0.0}


def _eval_p1_11(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2, no TIME features).

    SHORT top3 (earliest trigger = C2, most general):
      C1: taker_buy_pct_vs_entry < -0.003398 AND volume_vs_ma20_vs_entry < -0.003582 AND volume_autocorr_lag5 < -0.013463
      C2: taker_buy_pct_vs_entry < -0.003398 AND volume_autocorr_lag5 < -0.013463
      C3: amplitude_1m < 0.568360 AND taker_buy_pct_vs_entry < -0.003398 AND volume_autocorr_lag5 < -0.013463
    Earliest: C2 (contained in C1 and C3)
    Stop: -1.50%
    """
    taker_pct_vs_entry = _vs_entry_val(row, snapshot, "taker_buy_pct")
    vol_autocorr = _safe_get(row, "volume_autocorr_lag5")

    if (taker_pct_vs_entry is not None and taker_pct_vs_entry < -0.003398
            and vol_autocorr is not None and vol_autocorr < -0.013463):
        return {"action": "exit", "reason": "logic_complete", "health": 1.0}
    return {"action": "hold", "reason": "hold", "health": 0.0}


def _eval_p1_6(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2).

    LONG top3 (earliest trigger):
      C1: position_in_range_24h > 0.216730 AND vwap_deviation_vs_entry > 0.003131 AND dist_to_24h_high_vs_entry > 0.002680
      C2: position_in_range_24h > 0.216730 AND vwap_deviation_vs_entry > 0.003131
      C3: position_in_range_24h_vs_entry > 0.163031 AND position_in_range_24h > 0.216730
    Earliest: r24h > 0.217 AND (vwap_vs_entry > 0.003131 OR r24h_vs_entry > 0.163031)
    Stop: -0.70%
    """
    r24h = _safe_get(row, "position_in_range_24h")
    vwap_vs_entry = _vs_entry_val(row, snapshot, "vwap_deviation")
    r24h_vs_entry = _vs_entry_val(row, snapshot, "position_in_range_24h")

    if r24h is not None and r24h > 0.216730:
        if ((vwap_vs_entry is not None and vwap_vs_entry > 0.003131)
                or (r24h_vs_entry is not None and r24h_vs_entry > 0.163031)):
            return {"action": "exit", "reason": "logic_complete", "health": 1.0}
    return {"action": "hold", "reason": "hold", "health": 0.0}


def _eval_p0_2(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2).

    SHORT top3 (earliest trigger = C2, most general):
      C1: position_in_range_24h_vs_entry < -0.089729 AND vwap_deviation_vs_entry < -0.003921 AND dist_to_24h_low_vs_entry < -0.003140
      C2: position_in_range_24h_vs_entry < -0.089729 AND vwap_deviation_vs_entry < -0.003921
      C3: dist_to_24h_high_vs_entry < -0.003624 AND position_in_range_24h_vs_entry < -0.089729 AND vwap_deviation_vs_entry < -0.003921
    Earliest: C2
    Stop: -1.50%
    """
    r24h_vs_entry = _vs_entry_val(row, snapshot, "position_in_range_24h")
    vwap_vs_entry = _vs_entry_val(row, snapshot, "vwap_deviation")

    if (r24h_vs_entry is not None and r24h_vs_entry < -0.089729
            and vwap_vs_entry is not None and vwap_vs_entry < -0.003921):
        return {"action": "exit", "reason": "logic_complete", "health": 1.0}
    return {"action": "hold", "reason": "hold", "health": 0.0}


def _eval_p1_10(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2, no TIME features).

    LONG top3 (earliest trigger):
      C1: position_in_range_4h > 0.556422 AND vwap_deviation_vs_entry > 0.003512
      C2: position_in_range_4h_vs_entry > 0.492069 AND vwap_deviation_vs_entry > 0.003512
      C3: C1 AND C2 combined
    Stop: -1.00%
    """
    direction = position.get("direction", "").lower()

    if direction == "long":
        r4h = _safe_get(row, "position_in_range_4h")
        vwap_vs_entry = _vs_entry_val(row, snapshot, "vwap_deviation")
        r4h_vs_entry = _vs_entry_val(row, snapshot, "position_in_range_4h")

        c1 = (r4h is not None and r4h > 0.556422
              and vwap_vs_entry is not None and vwap_vs_entry > 0.003512)
        c2 = (r4h_vs_entry is not None and r4h_vs_entry > 0.492069
              and vwap_vs_entry is not None and vwap_vs_entry > 0.003512)

        if c1 or c2:
            return {"action": "exit", "reason": "logic_complete", "health": 1.0}
        return {"action": "hold", "reason": "hold", "health": 0.0}

    return _eval_generic(position, row, snapshot, ret)


def _eval_p1_9(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2).

    LONG top3 (earliest trigger = C1, most general):
      C1: taker_buy_sell_ratio_vs_entry > 0.004978 AND volume_autocorr_lag5 > 0.004775
      C2: taker_buy_pct_vs_entry > -0.009193 AND taker_buy_sell_ratio_vs_entry > 0.004978 AND volume_autocorr_lag5 > 0.004775
      C3: taker_buy_sell_ratio_vs_entry > 0.004978 AND kyle_lambda > 0.004060 AND volume_autocorr_lag5 > 0.004775
    Earliest: C1 (contained in C2 and C3)
    Stop: -0.30%
    """
    direction = position.get("direction", "").lower()

    if direction == "long":
        taker_ratio_vs_entry = _vs_entry_val(row, snapshot, "taker_buy_sell_ratio")
        vol_autocorr = _safe_get(row, "volume_autocorr_lag5")

        if (taker_ratio_vs_entry is not None and taker_ratio_vs_entry > 0.004978
                and vol_autocorr is not None and vol_autocorr > 0.004775):
            return {"action": "exit", "reason": "logic_complete", "health": 1.0}
        return {"action": "hold", "reason": "hold", "health": 0.0}

    return _eval_generic(position, row, snapshot, ret)


def _eval_p1_2(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """MFE-peak exit conditions (remine v2).

    LONG top3 (earliest trigger = any of 2 independent conditions):
      C1: position_in_range_4h > 0.776553
      C2: oi_change_rate_5m > 0.000388
      C3: position_in_range_4h > 0.776553 AND oi_change_rate_5m > 0.000388
    Earliest: C1 OR C2
    Stop: -0.30%
    """
    direction = position.get("direction", "").lower()

    if direction == "long":
        r4h = _safe_get(row, "position_in_range_4h")
        oi_5m = _safe_get(row, "oi_change_rate_5m")

        if ((r4h is not None and r4h > 0.776553)
                or (oi_5m is not None and oi_5m > 0.000388)):
            return {"action": "exit", "reason": "logic_complete", "health": 1.0}
        return {"action": "hold", "reason": "hold", "health": 0.0}

    return _eval_generic(position, row, snapshot, ret)


def _eval_c1(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    """C1 funding_cycle_oversold_long exit.

    Exit conditions (OR, earliest trigger wins):
      A: taker_buy_sell_ratio_vs_entry > -0.019968
         AND volume_vs_ma20_vs_entry > 0.009667
         AND spread_vs_ma20 > 0.999939
      B: amplitude_ma20 > 0.291184  (20bar avg amplitude spike, unit: %)
      C: amplitude_1m > 0.591679    (single bar amplitude spike, unit: %)
    Stop: -0.70%
    Physical: A = market state restored (taker/vol normalized, spread tight);
              B/C = volatility released, momentum exhausted
    """
    taker_vs_entry = _vs_entry_val(row, snapshot, "taker_buy_sell_ratio")
    vol_vs_entry = _vs_entry_val(row, snapshot, "volume_vs_ma20")
    spread_ratio = _safe_get(row, "spread_vs_ma20")
    amp_ma20 = _safe_get(row, "amplitude_ma20")
    amp_1m = _safe_get(row, "amplitude_1m")

    cond_a = (
        taker_vs_entry is not None and taker_vs_entry > -0.019968
        and vol_vs_entry is not None and vol_vs_entry > 0.009667
        and spread_ratio is not None and spread_ratio > 0.999939
    )
    cond_b = amp_ma20 is not None and amp_ma20 > 0.291184
    cond_c = amp_1m is not None and amp_1m > 0.591679

    if cond_a or cond_b or cond_c:
        return {"action": "exit", "reason": "logic_complete", "health": 1.0}
    return {"action": "hold", "reason": "hold", "health": 0.0}


def _eval_generic(position: Dict, row: pd.Series, snapshot: Dict, ret: float) -> Dict[str, object]:
    direction = position.get("direction", "").lower()
    taker = _safe_get(row, "taker_buy_sell_ratio")
    spread_ratio = _safe_get(row, "spread_vs_ma20")
    oi_5m = _safe_get(row, "oi_change_rate_5m")

    score = 0.0
    if taker is not None:
        if direction == "short":
            if taker < 0.90:
                score += 1.0
            elif taker > 1.15:
                score -= 1.0
        elif direction == "long":
            if taker > 1.10:
                score += 1.0
            elif taker < 0.85:
                score -= 1.0

    if spread_ratio is not None:
        if spread_ratio > 2.0:
            score -= 1.0
        elif spread_ratio < 1.0:
            score += 0.4

    if oi_5m is not None:
        if oi_5m > 0.005:
            score += 0.4
        elif oi_5m < -0.02:
            score -= 0.7

    if score <= -2.0:
        return {"action": "exit", "reason": "regime_shift", "health": round(score, 3)}
    if score <= -0.8 and ret > 0.06:
        return {"action": "protect", "reason": "logic_weaken", "health": round(score, 3)}
    return {"action": "hold", "reason": "hold", "health": round(score, 3)}


def _resolve_decision(
    *,
    score: float,
    current_return: float,
    logic_complete: bool,
    logic_failed: bool,
    reverse_structure: bool,
) -> Dict[str, object]:
    score = round(score, 3)
    if reverse_structure:
        return {"action": "exit", "reason": "reverse_structure", "health": score}
    if logic_failed:
        return {"action": "exit", "reason": "logic_failed", "health": score}
    if logic_complete:
        if current_return <= LOGIC_COMPLETE_MIN_RETURN_PCT:
            return {"action": "hold", "reason": "logic_complete_wait_profit", "health": score}
        if current_return > 0.06:
            return {"action": "protect", "reason": "logic_complete", "health": score}
        return {"action": "exit", "reason": "logic_complete", "health": score}
    if score <= -0.8 and current_return > 0.06:
        return {"action": "protect", "reason": "logic_weaken", "health": score}
    return {"action": "hold", "reason": "hold", "health": score}


def _repaired_enough(entry_value: Optional[float], current_value: Optional[float]) -> bool:
    if entry_value is None or current_value is None:
        return False
    try:
        if entry_value == 0:
            return False
        if entry_value * current_value < 0:
            return True
        return abs(current_value) <= abs(entry_value) * 0.35
    except Exception:
        return False


def _repair_ratio(entry_value: Optional[float], current_value: Optional[float]) -> float:
    if entry_value is None or current_value is None:
        return 0.0
    try:
        if entry_value == 0:
            return 0.0
        if entry_value * current_value < 0:
            return 1.0
        repaired = 1.0 - (abs(current_value) / abs(entry_value))
        return max(0.0, min(1.0, repaired))
    except Exception:
        return 0.0


def _vs_entry_val(row: Optional[pd.Series], snapshot: Dict, base_col: str) -> Optional[float]:
    """Compute current_value - entry_value (for _vs_entry delta features)."""
    current = _safe_get(row, base_col)
    entry = _safe_get_dict(snapshot, base_col)
    if current is None or entry is None:
        return None
    return current - entry


def _same_side(entry_value: Optional[float], current_value: Optional[float]) -> bool:
    if entry_value is None or current_value is None:
        return False
    return entry_value == 0 or current_value == 0 or entry_value * current_value >= 0


def _current_return(position: Dict, close: float) -> float:
    entry = float(position.get("entry_price", 0.0) or 0.0)
    if entry <= 0:
        return 0.0
    direction = str(position.get("direction", "")).lower()
    if direction == "short":
        return (entry - close) / entry * 100
    if direction == "long":
        return (close - entry) / entry * 100
    return 0.0


def _safe_get(row: Optional[pd.Series], col: str, default: Optional[float] = None) -> Optional[float]:
    if row is None:
        return default
    if isinstance(row, dict):
        if col not in row:
            return default
        val = row[col]
    else:
        if col not in row.index:
            return default
        val = row[col]
    if pd.isna(val):
        return default
    try:
        return float(val)
    except Exception:
        return default


def _safe_get_dict(data: Dict, col: str, default: Optional[float] = None) -> Optional[float]:
    if col not in data:
        return default
    val = data[col]
    if val is None:
        return default
    try:
        return float(val)
    except Exception:
        return default


def update_mfe_mae(runtime_state: Dict[str, object], position: Dict, close: float) -> None:
    entry = float(position.get("entry_price", 0.0) or 0.0)
    if entry <= 0:
        return

    direction = str(position.get("direction", "")).lower()
    if direction == "short":
        favorable = (entry - close) / entry * 100
        adverse = (close - entry) / entry * 100
    else:
        favorable = (close - entry) / entry * 100
        adverse = (entry - close) / entry * 100

    runtime_state["mfe_pct"] = max(float(runtime_state.get("mfe_pct", 0.0) or 0.0), favorable)
    runtime_state["mae_pct"] = max(float(runtime_state.get("mae_pct", 0.0) or 0.0), adverse)


def evaluate_exit_action(
    position: Dict,
    close: float,
    features: Optional[pd.Series | Dict[str, object]],
    runtime_state: Dict[str, object],
    params: ExitParams,
) -> Dict[str, object]:
    family = str(position.get("family") or normalize_family(str(position.get("rule", ""))))
    bars_held = int(runtime_state.get("bars_held", 0) or 0)
    current_return = _current_return(position, close)
    adverse = _adverse_pct(position, close)

    # --- Adaptive hard stop ---
    base_stop = params.stop_pct
    _confidence = int(runtime_state.get("confidence", 2) or 2)
    _regime = str(runtime_state.get("entry_regime", "RANGE_BOUND") or "RANGE_BOUND")
    _mfe = float(runtime_state.get("mfe_pct", 0.0) or 0.0)

    _conf_mult = params.confidence_stop_multipliers.get(_confidence, 1.0)
    _regime_mult = params.regime_stop_multipliers.get(_regime, 1.0)
    effective_stop = base_stop * _conf_mult * _regime_mult

    if _mfe > params.mfe_ratchet_threshold:
        ratcheted_stop = _mfe * params.mfe_ratchet_ratio
        effective_stop = min(effective_stop, ratcheted_stop)

    if adverse >= effective_stop:
        return {
            "action": "exit",
            "reason": "hard_stop",
            "health": -9.0,
            "current_return": current_return,
        }

    if params.take_profit_pct > 0 and current_return >= params.take_profit_pct:
        return {
            "action": "exit",
            "reason": "take_profit",
            "health": float(runtime_state.get("last_health", 0.0) or 0.0),
            "current_return": current_return,
        }

    if family == "P1-8" and str(position.get("direction", "")).lower() == "short" and params.take_profit_pct > 0:
        max_hold_bars = resolve_max_hold_bars(
            family=family,
            base_horizon=max(1, int(position.get("hold_bars", 1) or 1)),
            params=params,
        )
        if bars_held >= max_hold_bars:
            return {
                "action": "exit",
                "reason": "time_cap",
                "health": float(runtime_state.get("last_health", 0.0) or 0.0),
                "current_return": current_return,
            }
        return {
            "action": "hold",
            "reason": "tp_sl_mode",
            "health": float(runtime_state.get("last_health", 0.0) or 0.0),
            "current_return": current_return,
        }

    _refresh_runtime_protect(runtime_state, params)
    if runtime_state.get("protect_armed"):
        protect_floor = float(runtime_state.get("protect_floor_pct", float("-inf")))
        if current_return <= protect_floor:
            return {
                "action": "exit",
                "reason": "profit_protect",
                "health": float(runtime_state.get("last_health", 0.0) or 0.0),
                "current_return": current_return,
            }

    max_hold_bars = resolve_max_hold_bars(
        family=family,
        base_horizon=max(1, int(position.get("hold_bars", 1) or 1)),
        params=params,
    )
    if bars_held >= max_hold_bars:
        return {
            "action": "exit",
            "reason": "time_cap",
            "health": float(runtime_state.get("last_health", 0.0) or 0.0),
            "current_return": current_return,
        }

    if bars_held < params.min_hold_bars:
        return {
            "action": "hold",
            "reason": "min_hold",
            "health": float(runtime_state.get("last_health", 0.0) or 0.0),
            "current_return": current_return,
        }

    # ── 机制生命周期退出（优先于统计退出）──────────────────────────────────
    decay_score = float(runtime_state.get("decay_score", 0.0) or 0.0)
    decay_action = str(runtime_state.get("decay_action", "hold") or "hold")
    decay_reason = str(runtime_state.get("decay_reason", "") or "")

    if decay_action == "exit" and decay_score >= params.decay_exit_threshold:
        return {
            "action": "exit",
            "reason": decay_reason or "mechanism_decay",
            "health": -decay_score,
            "current_return": current_return,
        }

    if decay_action == "tighten" and decay_score >= params.decay_tighten_threshold:
        # 机制正在衰竭 → 收紧 trailing stop（缩小 gap ratio）
        tightened = ExitParams(
            take_profit_pct=params.take_profit_pct,
            stop_pct=params.stop_pct,
            protect_start_pct=max(0.04, params.protect_start_pct * 0.5),
            protect_gap_ratio=params.tighten_gap_ratio,
            protect_floor_pct=params.protect_floor_pct,
            min_hold_bars=params.min_hold_bars,
            max_hold_factor=params.max_hold_factor,
            exit_confirm_bars=params.exit_confirm_bars,
            decay_exit_threshold=params.decay_exit_threshold,
            decay_tighten_threshold=params.decay_tighten_threshold,
            tighten_gap_ratio=params.tighten_gap_ratio,
            confidence_stop_multipliers=params.confidence_stop_multipliers,
            regime_stop_multipliers=params.regime_stop_multipliers,
            mfe_ratchet_threshold=params.mfe_ratchet_threshold,
            mfe_ratchet_ratio=params.mfe_ratchet_ratio,
        )
        _refresh_runtime_protect(runtime_state, tightened)
        if runtime_state.get("protect_armed"):
            protect_floor = float(runtime_state.get("protect_floor_pct", float("-inf")))
            if current_return <= protect_floor:
                return {
                    "action": "exit",
                    "reason": f"tightened_protect|{decay_reason}",
                    "health": -decay_score,
                    "current_return": current_return,
                }

    decision = evaluate_exit_state(position, close, features)
    runtime_state["last_health"] = float(decision.get("health", 0.0) or 0.0)

    action = str(decision.get("action", "hold"))
    reason = str(decision.get("reason", "hold"))

    if reason == "logic_complete" and current_return <= LOGIC_COMPLETE_MIN_RETURN_PCT:
        runtime_state["pending_exit_reason"] = ""
        runtime_state["pending_exit_count"] = 0
        return {
            "action": "hold",
            "reason": "logic_complete_wait_profit",
            "health": runtime_state["last_health"],
            "current_return": current_return,
        }

    if action == "protect" and float(runtime_state.get("mfe_pct", 0.0) or 0.0) >= params.protect_start_pct:
        runtime_state["protect_armed"] = True
        runtime_state["protect_reason"] = reason
        _refresh_runtime_protect(runtime_state, params)
        return {
            "action": "protect",
            "reason": reason,
            "health": runtime_state["last_health"],
            "current_return": current_return,
        }

    if action == "exit" and reason in _EXIT_REASON_SET:
        # Guard: dynamic exit only when profitable 鈥?if losing, wait for MFE or stop
        if reason == "logic_complete" and current_return < 0:
            runtime_state["pending_exit_reason"] = ""
            runtime_state["pending_exit_count"] = 0
            return {
                "action": "hold",
                "reason": "logic_complete_but_losing",
                "health": runtime_state["last_health"],
                "current_return": current_return,
            }

        prev_reason = str(runtime_state.get("pending_exit_reason", ""))
        if prev_reason == reason:
            runtime_state["pending_exit_count"] = int(runtime_state.get("pending_exit_count", 0) or 0) + 1
        else:
            runtime_state["pending_exit_reason"] = reason
            runtime_state["pending_exit_count"] = 1

        if int(runtime_state["pending_exit_count"]) >= params.exit_confirm_bars:
            return {
                "action": "exit",
                "reason": reason,
                "health": runtime_state["last_health"],
                "current_return": current_return,
            }
        return {
            "action": "hold",
            "reason": f"confirm_{reason}",
            "health": runtime_state["last_health"],
            "current_return": current_return,
        }

    runtime_state["pending_exit_reason"] = ""
    runtime_state["pending_exit_count"] = 0
    return {
        "action": action,
        "reason": reason,
        "health": runtime_state["last_health"],
        "current_return": current_return,
    }


def build_runtime_state() -> Dict[str, object]:
    return {
        "mfe_pct": 0.0,
        "mae_pct": 0.0,
        "protect_armed": False,
        "protect_reason": "",
        "protect_floor_pct": float("-inf"),
        "pending_exit_reason": "",
        "pending_exit_count": 0,
        "last_health": 0.0,
        "bars_held": 0,
    }


def _refresh_runtime_protect(runtime_state: Dict[str, object], params: ExitParams) -> None:
    mfe = float(runtime_state.get("mfe_pct", 0.0) or 0.0)
    if mfe < params.protect_start_pct:
        return

    gap = max(params.protect_floor_pct, mfe * params.protect_gap_ratio)
    floor = max(params.protect_floor_pct, mfe - gap)
    if floor > float(runtime_state.get("protect_floor_pct", float("-inf"))):
        runtime_state["protect_floor_pct"] = floor


def _adverse_pct(position: Dict, close: float) -> float:
    entry = float(position.get("entry_price", 0.0) or 0.0)
    if entry <= 0:
        return 0.0
    direction = str(position.get("direction", "")).lower()
    if direction == "short":
        return max(0.0, (close - entry) / entry * 100)
    return max(0.0, (entry - close) / entry * 100)

