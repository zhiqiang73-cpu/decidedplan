"""Shared exit-contract validation helpers for live and approved strategies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

ALLOWED_EXIT_CONTRACT_TOKENS = frozenset({"vs_entry", "mechanism_decay"})


def normalize_exit_contract_tokens(payload: object) -> tuple[str, ...]:
    """Normalize declared exit-contract tokens.

    The contract is intentionally small: a strategy must explicitly declare
    whether it exits because the entry force has changed versus entry
    (``vs_entry``) and/or because the mechanism lifecycle tracker can mark the
    force as exhausted (``mechanism_decay``).
    """

    if isinstance(payload, str):
        raw_items: list[object] = [part.strip() for part in payload.split(",")]
    elif isinstance(payload, Iterable):
        raw_items = list(payload)
    else:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip().lower().replace("-", "_")
        if token in {"vsentry", "entry_snapshot", "snapshot_decay"}:
            token = "vs_entry"
        elif token in {"decay", "mechanism"}:
            token = "mechanism_decay"
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return tuple(normalized)


def has_physical_exit_contract(tokens: object) -> bool:
    normalized = set(normalize_exit_contract_tokens(tokens))
    return bool(normalized & ALLOWED_EXIT_CONTRACT_TOKENS)


def extract_card_exit_contract_tokens(card: Mapping[str, Any]) -> tuple[str, ...]:
    """Infer exit-contract tokens from an approved card payload."""

    tokens: list[str] = []
    exit_payload = card.get("exit")
    strategy_blueprint = card.get("strategy_blueprint")

    exit_method = ""
    if isinstance(exit_payload, Mapping):
        exit_method = str(exit_payload.get("exit_method") or "").strip().lower()
    if "vs_entry" in exit_method:
        tokens.append("vs_entry")
    if "mechanism_decay" in exit_method:
        tokens.append("mechanism_decay")

    def _scan_condition_groups(payload: object) -> None:
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            return
        for group in payload:
            if isinstance(group, Mapping):
                conditions = group.get("conditions")
                if isinstance(conditions, Sequence) and not isinstance(conditions, (str, bytes)):
                    _scan_condition_groups(conditions)
                    continue
            if not isinstance(group, Mapping):
                continue
            feature = str(group.get("feature") or "").strip().lower()
            source = str(group.get("source") or "").strip().lower()
            role = str(group.get("role") or "").strip().lower()
            if feature.endswith("_vs_entry") or source in {"force_decay", "thesis_invalidation"}:
                tokens.append("vs_entry")
            if "mechanism_decay" in source or "mechanism_decay" in role:
                tokens.append("mechanism_decay")

    if isinstance(exit_payload, Mapping):
        _scan_condition_groups([exit_payload])
        for key in ("top3", "invalidation"):
            _scan_condition_groups(exit_payload.get(key))
    if isinstance(strategy_blueprint, Mapping):
        for key in ("force_decay_exit", "thesis_invalidation"):
            _scan_condition_groups(strategy_blueprint.get(key))

    return normalize_exit_contract_tokens(tokens)


def validate_live_specs(specs: Iterable[Any]) -> list[str]:
    issues: list[str] = []
    for spec in specs:
        family = str(getattr(spec, "family", "") or "").strip() or "<unknown>"
        tokens = normalize_exit_contract_tokens(getattr(spec, "exit_contract", ()))
        if not tokens:
            issues.append(f"{family}: missing exit_contract declaration")
            continue
        if not has_physical_exit_contract(tokens):
            issues.append(
                f"{family}: exit_contract must include vs_entry or mechanism_decay, got {tokens}"
            )
    return issues
