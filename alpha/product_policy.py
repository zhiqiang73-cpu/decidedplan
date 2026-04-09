from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from monitor.exit_policy_config import ExitParams, get_exit_params_for_signal
from monitor.live_catalog import LIVE_STRATEGIES, get_live_strategy_spec
from utils.file_io import read_json_file, write_json_atomic

_OUTPUT_DIR = Path("alpha/output")
_APPROVED_PATH = _OUTPUT_DIR / "approved_rules.json"
_CANDIDATE_PATH = _OUTPUT_DIR / "candidate_rules.json"
_PENDING_PATH = _OUTPUT_DIR / "pending_rules.json"
_REVIEW_PATH = _OUTPUT_DIR / "review_queue.json"
_REJECTED_PATH = _OUTPUT_DIR / "rejected_rules.json"
_TRADES_PATH = Path("execution/logs/trades.csv")

_PRODUCT_SPECS = tuple(
    spec for spec in LIVE_STRATEGIES if spec.phase == "P2" and spec.uses_card_exit
)
_PRODUCT_FAMILIES = tuple(spec.family for spec in _PRODUCT_SPECS)
_PRODUCT_FAMILY_SET = set(_PRODUCT_FAMILIES)

_FAMILY_SIGNATURES: dict[tuple[str, str, str], str] = {
    ("dist_to_24h_high", "oi_change_rate_5m", "short"): "A2-26",
    ("dist_to_24h_high", "spread_vs_ma20", "short"): "A2-29",
    ("dist_to_24h_high", "oi_change_rate_1h", "short"): "A3-OI",
    ("position_in_range_4h", "oi_change_rate_1h", "short"): "A4-PIR",
}

_NEUTRAL_FEATURE_VALUES: dict[str, float] = {
    "dist_to_24h_high": 0.0,
    "position_in_range_4h": 0.5,
    "oi_change_rate_5m": 0.0,
    "oi_change_rate_1h": 0.0,
    "spread_vs_ma20": 1.0,
}
_FEATURE_MIN_DELTAS: dict[str, float] = {
    "dist_to_24h_high": 0.002,
    "position_in_range_4h": 0.04,
    "oi_change_rate_5m": 0.003,
    "oi_change_rate_1h": 0.003,
    "spread_vs_ma20": 0.03,
}
_FORCE_DECAY_RATIO = 0.55
_FORCE_INVALIDATION_RATIO = 0.30
_VS_ENTRY_TAG = "_vs_entry"


def product_alpha_families() -> tuple[str, ...]:
    return _PRODUCT_FAMILIES


def is_product_alpha_family(family: str) -> bool:
    return str(family or "").strip() in _PRODUCT_FAMILY_SET


def infer_product_family(card: dict[str, Any]) -> str:
    family = str(card.get("family") or "").strip()
    if family in _PRODUCT_FAMILY_SET:
        return family

    entry = card.get("entry") if isinstance(card.get("entry"), dict) else {}
    entry_feature = str(entry.get("feature") or "").strip()
    direction = str(entry.get("direction") or "").strip().lower()
    confirm_feature = ""
    for condition in card.get("combo_conditions") or []:
        if isinstance(condition, dict) and condition.get("feature"):
            confirm_feature = str(condition.get("feature") or "").strip()
            break
    return _FAMILY_SIGNATURES.get((entry_feature, confirm_feature, direction), "")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _feature_neutral_value(feature: str, reference: float = 0.0) -> float:
    if feature in _NEUTRAL_FEATURE_VALUES:
        return _NEUTRAL_FEATURE_VALUES[feature]
    if "position_in_range" in feature:
        return 0.5
    if feature.endswith("_ratio"):
        return 1.0
    return reference * 0.0


def _feature_min_delta(feature: str, reference: float) -> float:
    return max(_FEATURE_MIN_DELTAS.get(feature, 0.0), abs(reference) * 0.15, 1e-4)


def _signed_decay_gap(feature: str, entry_op: str, entry_threshold: float) -> tuple[float, float, float]:
    neutral = _feature_neutral_value(feature, entry_threshold)
    raw_gap = neutral - entry_threshold
    if abs(raw_gap) < 1e-8:
        fallback = _feature_min_delta(feature, entry_threshold)
        raw_gap = fallback if entry_op == "<" else -fallback
    base_gap = max(abs(raw_gap), _feature_min_delta(feature, entry_threshold))
    return neutral, raw_gap, base_gap


def _build_vs_entry_condition(
    feature: str,
    delta: float,
    *,
    source: str,
    role: str,
    neutral_value: float,
) -> dict[str, Any]:
    return {
        "feature": f"{feature}{_VS_ENTRY_TAG}",
        "operator": ">" if delta > 0 else "<",
        "threshold": round(float(delta), 8),
        "source": source,
        "role": role,
        "neutral_value": round(float(neutral_value), 8),
    }


def _build_absolute_condition(
    feature: str,
    target_value: float,
    *,
    operator: str,
    source: str,
    role: str,
    neutral_value: float,
) -> dict[str, Any]:
    return {
        "feature": feature,
        "operator": operator,
        "threshold": round(float(target_value), 8),
        "source": source,
        "role": role,
        "neutral_value": round(float(neutral_value), 8),
    }


def _build_force_decay_condition(feature: str, entry_op: str, entry_threshold: float) -> tuple[dict[str, Any], dict[str, Any]]:
    neutral, raw_gap, base_gap = _signed_decay_gap(feature, entry_op, entry_threshold)
    decay_delta = (
        (1.0 if raw_gap >= 0 else -1.0)
        * max(base_gap * _FORCE_DECAY_RATIO, _feature_min_delta(feature, entry_threshold))
    )
    repair_target = entry_threshold + decay_delta
    return (
        _build_vs_entry_condition(
            feature,
            decay_delta,
            source="force_decay",
            role="decay",
            neutral_value=neutral,
        ),
        _build_absolute_condition(
            feature,
            repair_target,
            operator=">" if decay_delta > 0 else "<",
            source="force_decay",
            role="repair_target",
            neutral_value=neutral,
        ),
    )


def _build_invalidation_condition(feature: str, entry_op: str, entry_threshold: float) -> dict[str, Any]:
    neutral, raw_gap, base_gap = _signed_decay_gap(feature, entry_op, entry_threshold)
    invalid_delta = (
        (-1.0 if raw_gap >= 0 else 1.0)
        * max(base_gap * _FORCE_INVALIDATION_RATIO, _feature_min_delta(feature, entry_threshold))
    )
    return _build_vs_entry_condition(
        feature,
        invalid_delta,
        source="thesis_invalidation",
        role="thesis_invalidated",
        neutral_value=neutral,
    )


def _dedupe_combo_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, str, float], ...]] = set()
    deduped: list[dict[str, Any]] = []
    for entry in entries:
        conditions = entry.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            continue
        key_parts: list[tuple[str, str, float]] = []
        valid = True
        for cond in conditions:
            if not isinstance(cond, dict):
                valid = False
                break
            feature = str(cond.get("feature", "")).strip()
            operator = str(cond.get("operator") or cond.get("op") or "").strip()
            if not feature or operator not in {"<", ">"}:
                valid = False
                break
            try:
                threshold = float(cond.get("threshold"))
            except (TypeError, ValueError):
                valid = False
                break
            key_parts.append((feature, operator, round(threshold, 8)))
        if not valid:
            continue
        key = tuple(key_parts)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def build_force_decay_exit(
    *,
    entry_feature: str,
    entry_op: str,
    entry_threshold: float,
    combo_conditions: list[dict[str, Any]] | None,
    mechanism_type: str,
    existing_exit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary_decay, primary_abs = _build_force_decay_condition(
        entry_feature, entry_op, entry_threshold
    )
    primary_invalidation = _build_invalidation_condition(
        entry_feature, entry_op, entry_threshold
    )

    combos: list[dict[str, Any]] = [
        {
            "conditions": [primary_decay],
            "combo_label": "C1",
            "description": f"{entry_feature} relative to entry has repaired enough",
        },
        {
            "conditions": [primary_decay, primary_abs],
            "combo_label": "C2",
            "description": f"{entry_feature} repaired versus entry and absolute state",
        },
    ]
    invalidation: list[dict[str, Any]] = [
        {
            "conditions": [primary_invalidation],
            "combo_label": "I1",
            "description": f"{entry_feature} worsened versus entry; thesis broken",
        }
    ]

    first_confirm: dict[str, Any] | None = None
    for condition in combo_conditions or []:
        if isinstance(condition, dict) and condition.get("feature"):
            first_confirm = condition
            break
    if first_confirm is not None:
        confirm_feature = str(first_confirm.get("feature") or "").strip()
        confirm_op = str(first_confirm.get("op") or first_confirm.get("operator") or "").strip()
        confirm_threshold = _safe_float(first_confirm.get("threshold"))
        confirm_decay, confirm_abs = _build_force_decay_condition(
            confirm_feature, confirm_op, confirm_threshold
        )
        confirm_invalidation = _build_invalidation_condition(
            confirm_feature, confirm_op, confirm_threshold
        )
        combos.extend(
            [
                {
                    "conditions": [primary_decay, confirm_decay],
                    "combo_label": "C3",
                    "description": "Entry force and confirm force both repaired versus entry",
                },
                {
                    "conditions": [primary_abs, confirm_abs],
                    "combo_label": "C4",
                    "description": "Primary and confirm states both returned toward neutral",
                },
            ]
        )
        invalidation.append(
            {
                "conditions": [primary_invalidation, confirm_invalidation],
                "combo_label": "I2",
                "description": "Primary and confirm force both worsened versus entry",
            }
        )

    rebuilt = {
        "top3": _dedupe_combo_entries(combos)[:3],
        "invalidation": _dedupe_combo_entries(invalidation),
        "exit_method": "force_decay_vs_entry",
        "snapshot_required": True,
        "mechanism_type": mechanism_type,
        "force_features": [entry_feature]
        + [
            str(item.get("feature", ""))
            for item in (combo_conditions or [])
            if isinstance(item, dict) and item.get("feature")
        ],
    }
    if isinstance(existing_exit, dict):
        for key, value in existing_exit.items():
            if key in {"top3", "invalidation", "exit_method", "snapshot_required", "mechanism_type", "force_features"}:
                continue
            rebuilt[key] = value
    return rebuilt


def build_stop_logic(mechanism_type: str, exit_params: dict[str, Any] | ExitParams, *, direction: str) -> dict[str, Any]:
    stop_pct = (
        _safe_float(exit_params.get("stop_pct"))
        if isinstance(exit_params, dict)
        else _safe_float(getattr(exit_params, "stop_pct", 0.0))
    )
    return {
        "type": "mechanism_hard_stop",
        "mechanism_type": mechanism_type,
        "direction": direction,
        "stop_pct": round(stop_pct, 4),
        "reason": "thesis_invalidated_or_force_stalled",
    }


def build_live_trade_stats(trades_path: Path = _TRADES_PATH) -> dict[str, dict[str, Any]]:
    stats = {
        family: {
            "trades": 0,
            "filled": 0,
            "total_net_return_pct": 0.0,
            "filled_avg_net_return_pct": None,
            "_filled_sum": 0.0,
        }
        for family in _PRODUCT_FAMILIES
    }
    if not trades_path.exists():
        return stats

    with trades_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            family = str(row.get("strategy_id") or "").strip()
            if family not in stats:
                continue
            entry = stats[family]
            entry["trades"] += 1
            net_return = _safe_float(row.get("net_return_pct"))
            entry["total_net_return_pct"] += net_return
            exit_reason = str(row.get("exit_reason") or "").strip().lower()
            if exit_reason == "not_filled":
                continue
            entry["filled"] += 1
            entry["_filled_sum"] += net_return

    for family, entry in stats.items():
        filled = int(entry["filled"])
        entry["total_net_return_pct"] = round(float(entry["total_net_return_pct"]), 4)
        if filled > 0:
            entry["filled_avg_net_return_pct"] = round(float(entry["_filled_sum"]) / filled, 4)
        else:
            entry["filled_avg_net_return_pct"] = None
        entry.pop("_filled_sum", None)
    return stats


def _normalize_combo_conditions(card: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for condition in card.get("combo_conditions") or []:
        if not isinstance(condition, dict):
            continue
        feature = str(condition.get("feature") or "").strip()
        operator = str(condition.get("op") or condition.get("operator") or "").strip()
        if not feature or operator not in {"<", ">"}:
            continue
        normalized.append(
            {
                "feature": feature,
                "op": operator,
                "threshold": _safe_float(condition.get("threshold")),
            }
        )
    return normalized


def enrich_product_card(
    card: dict[str, Any],
    *,
    live_stats: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    family = infer_product_family(card)
    if not family:
        return None

    spec = get_live_strategy_spec(family)
    if spec is None:
        return None

    entry = dict(card.get("entry") or {})
    combo_conditions = _normalize_combo_conditions(card)
    direction = str(entry.get("direction") or "").strip().lower()
    mechanism_type = str(card.get("mechanism_type") or spec.mechanism_type or "").strip()
    exit_params_obj = get_exit_params_for_signal(family, direction)
    exit_params = dict(card.get("exit_params") or {}) if isinstance(card.get("exit_params"), dict) else {}
    if not exit_params:
        exit_params = exit_params_obj.to_dict()
    elif "stop_pct" not in exit_params:
        exit_params = {**exit_params_obj.to_dict(), **exit_params}

    existing_exit = card.get("exit") if isinstance(card.get("exit"), dict) else {}
    rebuilt_exit = build_force_decay_exit(
        entry_feature=str(entry.get("feature") or ""),
        entry_op=str(entry.get("operator") or ""),
        entry_threshold=_safe_float(entry.get("threshold")),
        combo_conditions=combo_conditions,
        mechanism_type=mechanism_type,
        existing_exit=existing_exit,
    )

    enriched = dict(card)
    enriched["family"] = family
    enriched["mechanism_type"] = mechanism_type
    enriched["combo_conditions"] = combo_conditions
    enriched["exit"] = rebuilt_exit
    enriched["exit_params"] = exit_params
    enriched["stop_pct"] = _safe_float(
        enriched.get("stop_pct"),
        _safe_float(exit_params.get("stop_pct"), _safe_float(getattr(exit_params_obj, "stop_pct", 0.0))),
    )
    enriched["stop_logic"] = build_stop_logic(mechanism_type, exit_params, direction=direction)
    enriched["strategy_blueprint"] = {
        "snapshot_required": True,
        "force_decay_exit": list(rebuilt_exit.get("top3") or []),
        "thesis_invalidation": list(rebuilt_exit.get("invalidation") or []),
    }
    enriched["product_candidate"] = True
    enriched["trade_ready"] = True
    enriched["production_requirements"] = {
        "explicit_physical_mechanism": bool(mechanism_type and mechanism_type != "generic_alpha"),
        "fee_positive_oos": _safe_float((card.get("stats") or {}).get("oos_net_return")) > 0,
        "enough_oos_samples": _safe_int((card.get("stats") or {}).get("n_oos")) >= 30,
        "snapshot_required": True,
        "thesis_invalidation": True,
        "mechanism_hard_stop": True,
        "live_family_ready": True,
    }
    if live_stats is not None:
        enriched["live_trade_stats"] = dict(live_stats.get(family) or {})
    return enriched


def _approved_status_rank(card: dict[str, Any]) -> tuple[int, int]:
    status = str(card.get("status") or "").strip().lower()
    approved_by = str(card.get("approved_by") or "").strip()
    return (
        0 if status in {"approved", "live", "enabled"} else 1,
        0 if approved_by else 1,
    )


def _filter_product_backlog(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        family = infer_product_family(card)
        if not family:
            continue
        clone = dict(card)
        clone["family"] = family
        filtered.append(clone)
    return filtered


def build_product_candidate_board(approved_cards: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    approved_cards = approved_cards if isinstance(approved_cards, list) else read_json_file(_APPROVED_PATH, [])
    selected: dict[str, dict[str, Any]] = {}
    for raw_card in approved_cards:
        if not isinstance(raw_card, dict):
            continue
        family = infer_product_family(raw_card)
        if not family:
            continue
        status = str(raw_card.get("status") or "").strip().lower()
        if status in {"flagged", "rejected", "retired", "disabled", "pending"}:
            continue
        approved_by = str(raw_card.get("approved_by") or "").strip()
        if not approved_by:
            continue
        card = dict(raw_card)
        card["family"] = family
        current = selected.get(family)
        if current is None or _approved_status_rank(card) < _approved_status_rank(current):
            selected[family] = card

    live_stats = build_live_trade_stats()
    board: list[dict[str, Any]] = []
    for family in _PRODUCT_FAMILIES:
        card = selected.get(family)
        if card is None:
            continue
        enriched = enrich_product_card(card, live_stats=live_stats)
        if enriched is not None:
            board.append(enriched)
    return board


def sync_product_candidate_pool() -> list[dict[str, Any]]:
    approved_cards = read_json_file(_APPROVED_PATH, [])
    if not isinstance(approved_cards, list):
        approved_cards = []

    board = build_product_candidate_board(approved_cards)
    write_json_atomic(_APPROVED_PATH, board, ensure_ascii=False, indent=2)
    write_json_atomic(_CANDIDATE_PATH, board, ensure_ascii=False, indent=2)

    for path in (_PENDING_PATH, _REVIEW_PATH):
        cards = read_json_file(path, [])
        filtered = _filter_product_backlog(cards if isinstance(cards, list) else [])
        write_json_atomic(path, filtered, ensure_ascii=False, indent=2)

    rejected = read_json_file(_REJECTED_PATH, [])
    filtered_rejected = _filter_product_backlog(rejected if isinstance(rejected, list) else [])
    write_json_atomic(_REJECTED_PATH, filtered_rejected, ensure_ascii=False, indent=2)
    return board
