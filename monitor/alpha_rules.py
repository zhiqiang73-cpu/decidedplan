"""Live alpha rule loading and physical-confirm filtering."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
from monitor.live_catalog import live_strategy_families
from monitor.mechanism_tracker import get_mechanism_for_family
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
    """Only load cards that are explicitly approved for live execution.

    Requires BOTH:
      1. status in {"approved", "live", "enabled"}
      2. approved_by is set (LLM auto-approve or human manual approve)
    Cards without a clear approval trail are rejected to prevent
    unapproved strategies from trading.
    """
    if not isinstance(card, dict):
        return False

    status = str(card.get("status", "") or "").strip().lower()
    if status in {"pending", "rejected", "flagged", "disabled", "retired"}:
        return False
    if status not in {"approved", "live", "enabled"}:
        enabled = card.get("enabled")
        if not (isinstance(enabled, bool) and enabled):
            return False

    # Gate: must have explicit approval record
    approved_by = str(card.get("approved_by") or "").strip()
    if not approved_by or approved_by == "UNKNOWN":
        card_id = str(card.get("id", "?"))[:30]
        logger.warning(
            "[AlphaRules] BLOCKED %s: status=%s but no approved_by record",
            card_id, status,
        )
        return False

    return True


def _collect_approved_card_issues(
    approved: list[dict],
) -> tuple[list[tuple[dict, str, str]], list[str]]:
    live_families = set(live_strategy_families())
    seen_card_ids: dict[str, str] = {}
    seen_families: dict[str, str] = {}
    valid_cards: list[tuple[dict, str, str]] = []
    issues: list[str] = []

    for index, card in enumerate(approved):
        if not _is_enabled_approved_card(card):
            continue

        card_id = str(card.get("id") or f"approved[{index}]").strip() or f"approved[{index}]"
        family = str(card.get("family") or "").strip()
        card_issues: list[str] = []

        if not family:
            card_issues.append("missing family")
        elif family not in live_families:
            card_issues.append(f"unknown family={family}")

        existing_card_id = seen_card_ids.get(card_id)
        if existing_card_id is not None:
            card_issues.append(f"duplicate card_id={card_id}")

        existing_family_owner = seen_families.get(family)
        if family and existing_family_owner is not None:
            card_issues.append(f"duplicate family={family} first_seen_in={existing_family_owner}")

        if card_issues:
            issues.append(f"{card_id}: {'; '.join(card_issues)}")
            continue

        seen_card_ids[card_id] = card_id
        seen_families[family] = card_id
        valid_cards.append((card, card_id, family))

    return valid_cards, issues


def validate_approved_rule_pool(approved: list[dict]) -> list[str]:
    _, issues = _collect_approved_card_issues(approved)
    return issues


def _build_alpha_rules_from_approved(approved: list[dict]) -> list[dict]:
    rules: list[dict] = []
    valid_cards, issues = _collect_approved_card_issues(approved)
    for issue in issues:
        logger.warning("[AlphaRules] blocked approved card: %s", issue)

    for card, card_id, family in valid_cards:
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

            has_exit = bool(exit_combos) or exit_condition is not None
            rules.append(
                {
                    "name": card_id,
                    "family": family,
                    "mechanism_type": get_mechanism_for_family(family),
                    "group": group,
                    "feature": entry["feature"],
                    "op": entry["operator"],
                    "threshold": float(entry["threshold"]),
                    "direction": entry["direction"],
                    "horizon": int(entry["horizon"]),
                    "combo_conditions": combo_conditions,
                    "exit": exit_condition,
                    "exit_combos": exit_combos,
                    "exit_params": dict(card.get("exit_params") or {})
                    if isinstance(card.get("exit_params"), dict)
                    else None,
                    "stop_pct": stop_pct,
                    "rule_str": str(card.get("rule_str", "") or ""),
                    "card_id": card_id,
                    "trade_ready": has_exit,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("[AlphaRules] skip malformed approved rule %s: %s", card_id, exc)
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
            family = str(rule.get("family") or "").strip()
            if not family:
                logger.warning("[AlphaRules] skip live alert without family: %s", name)
                continue

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
                "family": family,
                "mechanism_type": str(rule.get("mechanism_type") or get_mechanism_for_family(family)),
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
                "alpha_exit_params": dict(rule.get("exit_params") or {})
                if isinstance(rule.get("exit_params"), dict)
                else None,
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
            if oi_change is not None and oi_change < 0:
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
