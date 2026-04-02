"""Live alpha rule loading and physical-confirm filtering."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
from utils.file_io import read_json_file

logger = logging.getLogger(__name__)

_APPROVED_FILE = Path(__file__).parent.parent / "alpha" / "output" / "approved_rules.json"
_approved_mtime: float = 0.0


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_exit_condition(payload: dict | None) -> dict | None:
    """Normalize a single flat exit condition dict. Backward compat only."""
    if not isinstance(payload, dict):
        return None

    feature = str(payload.get("feature", "")).strip()
    operator = str(payload.get("operator") or payload.get("op") or "").strip()
    threshold = _safe_float(payload.get("threshold"))
    if not feature or operator not in {"<", ">"} or threshold is None:
        return None

    normalized = {
        "feature": feature,
        "operator": operator,
        "threshold": threshold,
    }
    for key in (
        "expected_hold_bars",
        "net_return_with_exit",
        "net_return_fixed_hold",
        "improvement",
        "n_samples",
    ):
        value = _safe_float(payload.get(key))
        if value is not None:
            normalized[key] = value
    return normalized


def _extract_top3_exit_combos(exit_payload: dict | None) -> list[list[dict]]:
    """Extract Top-3 exit condition combos from ExitConditionMiner output.

    Returns list of combos. Each combo is a list of condition dicts
    (feature/operator/threshold). Combos use AND logic internally;
    the earliest-triggered combo triggers exit (OR between combos).
    """
    if not isinstance(exit_payload, dict):
        return []

    top3 = exit_payload.get("top3")
    if not isinstance(top3, list) or not top3:
        # Fallback: try backward-compat single condition
        single = _normalize_exit_condition(exit_payload)
        if single:
            return [[single]]
        return []

    combos: list[list[dict]] = []
    for entry in top3:
        conditions = entry.get("conditions", [])
        if not isinstance(conditions, list) or not conditions:
            continue
        combo: list[dict] = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            feature = str(cond.get("feature", "")).strip()
            operator = str(cond.get("operator") or cond.get("op") or "").strip()
            threshold = _safe_float(cond.get("threshold"))
            if feature and operator in {"<", ">"} and threshold is not None:
                combo.append({
                    "feature": feature,
                    "operator": operator,
                    "threshold": threshold,
                })
        if combo:
            combos.append(combo)

    if not combos:
        # Final fallback to single condition
        single = _normalize_exit_condition(exit_payload)
        if single:
            return [[single]]

    return combos


def _is_enabled_approved_card(card: dict) -> bool:
    """Only load cards that are explicitly approved for live execution."""
    if not isinstance(card, dict):
        return False

    status = str(card.get("status", "") or "").strip().lower()
    if status in {"approved", "live", "enabled"}:
        return True
    if status in {"pending", "rejected", "flagged", "disabled", "retired"}:
        return False

    enabled = card.get("enabled")
    if isinstance(enabled, bool):
        return enabled

    # Legacy fallback: if status is absent, trust explicit APPROVE validation.
    validation = card.get("validation")
    if isinstance(validation, dict):
        conclusion = str(validation.get("conclusion", "") or "").strip().upper()
        if conclusion:
            return conclusion == "APPROVE"

    # No explicit approval marker -> do not load.
    return False


def _build_alpha_rules_from_approved(approved: list[dict]) -> list[dict]:
    rules: list[dict] = []
    for card in approved:
        if not _is_enabled_approved_card(card):
            continue
        try:
            entry = card["entry"]
            group = str(card.get("group") or card.get("rule_str") or card.get("id", entry["feature"]))
            combo_conditions = []
            for condition in card.get("combo_conditions", []):
                combo_conditions.append(
                    {
                        "feature": condition["feature"],
                        "op": condition["op"],
                        "threshold": float(condition["threshold"]),
                    }
                )

            # Extract Top-3 exit combos from ExitConditionMiner output
            exit_combos = _extract_top3_exit_combos(card.get("exit"))
            # Backward compat: single flat condition for legacy code paths
            exit_condition = _normalize_exit_condition(card.get("exit"))

            # Stop loss from card or from stop optimization result
            stop_pct = _safe_float(card.get("stop_pct"))

            family = (
                str(card.get("family") or "").strip()
                or f"ALPHA::{group}::{entry['direction']}::{int(entry['horizon'])}"
            )
            has_exit = bool(exit_combos) or exit_condition is not None
            rules.append(
                {
                    "name": card.get("id", entry["feature"]),
                    "family": family,
                    "group": group,
                    "feature": entry["feature"],
                    "op": entry["operator"],
                    "threshold": float(entry["threshold"]),
                    "direction": entry["direction"],
                    "horizon": int(entry["horizon"]),
                    "combo_conditions": combo_conditions,
                    "exit": exit_condition,
                    "exit_combos": exit_combos,
                    "stop_pct": stop_pct,
                    "rule_str": str(card.get("rule_str", "") or ""),
                    "card_id": str(card.get("id", entry["feature"])),
                    "trade_ready": has_exit,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("[AlphaRules] skip malformed approved rule: %s", exc)
    return rules


def _load_approved_rules() -> list[dict]:
    global _approved_mtime
    if not _APPROVED_FILE.exists():
        return []
    try:
        approved = read_json_file(_APPROVED_FILE, [])
        _approved_mtime = _APPROVED_FILE.stat().st_mtime
        rules = _build_alpha_rules_from_approved(approved)
        if rules:
            logger.info("[AlphaRules] loaded %d approved rules", len(rules))
        return rules
    except Exception as exc:
        logger.warning("[AlphaRules] failed to load approved_rules.json: %s", exc)
        return []


ALPHA_RULES: list[dict] = _load_approved_rules()


class AlphaRuleChecker:
    """Evaluate approved alpha rules on the latest feature row."""

    def __init__(self, cooldown_bars: int = 1):
        self.cooldown_bars = max(1, int(cooldown_bars))
        self._last_trigger: dict[str, int] = {}
        self._bar_count = 0

    def tick(self) -> None:
        self._bar_count += 1
        if self._bar_count % 60 == 0:
            self._maybe_reload()

    def _maybe_reload(self) -> None:
        global ALPHA_RULES, _approved_mtime
        if not _APPROVED_FILE.exists():
            return
        try:
            mtime = _APPROVED_FILE.stat().st_mtime
            if mtime > _approved_mtime:
                ALPHA_RULES = _load_approved_rules()
                logger.info("[AlphaRules] hot reloaded %d rules", len(ALPHA_RULES))
        except Exception as exc:
            logger.warning("[AlphaRules] reload check failed: %s", exc)

    def check(self, row: pd.Series, timestamp_ms: Optional[int] = None) -> list[dict]:
        self.tick()
        if not ALPHA_RULES:
            return []

        triggered: list[dict] = []
        confirm_cache: dict[str, list[str]] = {}

        def _get_confirms(direction: str) -> list[str]:
            if direction not in confirm_cache:
                confirm_cache[direction] = self._check_physical_confirms(row, direction)
            return confirm_cache[direction]

        for rule in ALPHA_RULES:
            name = str(rule.get("name", ""))
            feature = str(rule.get("feature", ""))
            op = str(rule.get("op", ""))
            threshold = float(rule.get("threshold", 0.0))
            direction = str(rule.get("direction", "")).lower()
            horizon = int(rule.get("horizon", 1))
            group = str(rule.get("group", name))

            value = _safe_get(row, feature, default=None)
            if value is None:
                continue

            matched = value < threshold if op == "<" else value > threshold
            if not matched:
                continue

            combo_ok = True
            for condition in rule.get("combo_conditions", []):
                cc_feature = condition["feature"]
                cc_op = condition["op"]
                cc_threshold = float(condition["threshold"])
                cc_value = _safe_get(row, cc_feature, default=None)
                if cc_value is None:
                    combo_ok = False
                    break
                if cc_op == "<" and not (cc_value < cc_threshold):
                    combo_ok = False
                    break
                if cc_op == ">" and not (cc_value > cc_threshold):
                    combo_ok = False
                    break
            if not combo_ok:
                continue

            last = self._last_trigger.get(name, -10**9)
            if self._bar_count - last < self.cooldown_bars:
                continue

            confirms = _get_confirms(direction)
            confidence = 1 + len(confirms)
            if confidence < 2:
                self._last_trigger[name] = self._bar_count
                logger.debug("[Alpha] %s skipped: confidence=%d < 2", name, confidence)
                continue

            label = "HIGH" if confidence >= 3 else "MEDIUM"
            self._last_trigger[name] = self._bar_count
            confirms_str = ", ".join(confirms) if confirms else "none"

            alert = {
                "name": name,
                "family": str(rule.get("family") or f"ALPHA::{group}::{direction}::{horizon}"),
                "group": group,
                "feature": feature,
                "feature_value": round(float(value), 6),
                "threshold": threshold,
                "op": op,
                "direction": direction,
                "horizon": horizon,
                "timestamp_ms": timestamp_ms,
                "physical_confirms": list(confirms),
                "confidence": confidence,
                "confidence_label": label,
                "alpha_exit_conditions": [rule["exit"]] if rule.get("exit") else [],
                "alpha_exit_combos": list(rule.get("exit_combos") or []),
                "stop_pct": rule.get("stop_pct"),
                "rule_str": rule.get("rule_str", ""),
                "card_id": rule.get("card_id", name),
                "trade_ready": bool(rule.get("trade_ready")),
                "desc": (
                    f"[{name}] {feature}={value:.5f} {op} {threshold:.5f} "
                    f"| {direction.upper()} {horizon}bars | conf={confidence}({label})"
                ),
            }
            triggered.append(alert)
            logger.info(
                "[Alpha SIGNAL] %s | %s=%.5f %s %.5f | %s %dbars | confidence=%d(%s) | physical: [%s]",
                name,
                feature,
                value,
                op,
                threshold,
                direction.upper(),
                horizon,
                confidence,
                label,
                confirms_str,
            )

        return triggered

    def _check_physical_confirms(self, row: pd.Series, direction: str) -> list[str]:
        confirms: list[str] = []
        volume_ma = _safe_get(row, "volume_vs_ma20", default=0.0)
        taker_ratio = _safe_get(row, "taker_buy_sell_ratio", default=1.0)
        oi_change = _safe_get(row, "oi_change_rate_5m", default=None)

        if direction == "short":
            if volume_ma >= 1.0 and taker_ratio < 1.0:
                confirms.append("volume_confirm")
            if oi_change is not None and oi_change > 0:
                confirms.append("oi_confirm")
        elif direction == "long":
            if volume_ma >= 1.0 and taker_ratio > 1.0:
                confirms.append("volume_confirm")
            if oi_change is not None and oi_change > 0:
                confirms.append("oi_confirm")

        return confirms


def _safe_get(row: pd.Series, col: str, default=None):
    if col not in row.index:
        return default
    value = row[col]
    if pd.isna(value):
        return default
    return float(value)
