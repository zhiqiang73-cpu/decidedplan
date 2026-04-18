"""v2 卡片自动晋升器

独立于旧 auto_promoter, 专门处理 mid_freq_scanner_v2 和 high_freq_scanner_v2 产出的卡片.

核心规则:
  - 统计门槛通过 (在 scanner 里已经过滤)
  - 力闭环验证通过 (exit_force_linked == True)
  - 出场参数符合新 4 层瀑布 (take_profit=0, protect_start=99)
  - 自动标记为 probation (前 10 笔观察期)
  - 仓位 4% (半仓)

写入:
  - alpha/output/approved_rules.json (追加新卡片, 保留旧)
  - alpha/output/backups/ (自动备份)
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from alpha.force_closure_validator import validate_card_force_closure

logger = logging.getLogger(__name__)


_APPROVED_FILE = Path("alpha/output/approved_rules.json")
_BACKUP_DIR = Path("alpha/output/backups")
_MAX_BACKUPS = 20


def _read_approved() -> list:
    if not _APPROVED_FILE.exists():
        return []
    try:
        return json.loads(_APPROVED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _backup_approved() -> None:
    """写入前自动备份."""
    if not _APPROVED_FILE.exists():
        return
    try:
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = _BACKUP_DIR / f"approved_rules_{ts}.json"
        shutil.copy2(str(_APPROVED_FILE), str(backup))
        backups = sorted(_BACKUP_DIR.glob("approved_rules_*.json"))
        for old in backups[:-_MAX_BACKUPS]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("[V2Promoter] backup failed: %s", exc)


def _write_approved(cards: list) -> None:
    _backup_approved()
    _APPROVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _APPROVED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_APPROVED_FILE)


def _entry_sig(card: dict) -> tuple:
    """计算卡片的入场条件签名 (feature, threshold_rounded, direction).

    用于去重检查: 相同入场条件的卡片只保留一张.
    """
    e = card.get('entry', {})
    thr = e.get('threshold')
    try:
        thr_r = round(float(thr), 6) if thr is not None else None
    except (TypeError, ValueError):
        thr_r = None
    return (str(e.get('feature', '')), thr_r, str(e.get('direction', '')))


def _card_to_v1_format(card: dict) -> dict:
    """把 v2 卡片转成 live 系统能识别的格式.

    live 系统 (alpha_rules.py) 读取 approved_rules.json 的 schema:
      {
        "name": str, "status": "approved",
        "entry": {"feature", "operator", "threshold", "direction", "horizon"},
        "combo_conditions": [{"feature", "op", "threshold"}],
        "exit": {...}, "exit_params": {...},
        "family": str, "mechanism_type": str,
        "stats": {...},
      }
    """
    entry = card["entry"]
    fc = card.get("force_closure", {})
    stats = card.get("stats", {})
    execution = card.get("execution", {})

    # family 格式必须匹配 alpha_rules.py 正则 ^A\d+-\d+$
    # A6 = v2 MidFreq 自动发现; A7 = v2 HighFreq 自动发现
    # 用 UUID hex 的整数值取模保证唯一
    prefix = "A7" if card.get("discovery_mode") == "high_freq_scanner_v2" else "A6"
    hex_part = card["id"].split("-", 1)[-1][:8]
    try:
        fam_num = int(hex_part, 16) % 999999 + 1
    except ValueError:
        fam_num = abs(hash(card["id"])) % 999999 + 1
    family = f"{prefix}-{fam_num:06d}"

    # 从 exit_params 取数据推导止损 (顶级 stop_pct 供 alpha_rules.py 直接读取)
    stop_pct = card.get("exit_params", {}).get("stop_pct", None)

    return {
        # 基础字段
        "id": card["id"],
        "name": card["id"],
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": "v2_stats_auto",
        "family": family,
        "mechanism_type": fc.get("force_category", "unclassified"),
        "stop_pct": stop_pct,
        # 入场
        "entry": {
            "feature": entry["feature"],
            "operator": entry["operator"],
            "threshold": entry["threshold"],
            "direction": entry["direction"],
            "horizon": entry["horizon"],
        },
        "feature": entry["feature"],
        "op": entry["operator"],
        "threshold": entry["threshold"],
        "direction": entry["direction"],
        "horizon": entry["horizon"],
        "rule_str": entry["rule_str"],
        "combo_conditions": [],
        # 出场
        "exit": card["exit"],
        "exit_params": card["exit_params"],
        # 统计
        "stats": stats,
        # 力闭环
        "force_closure": fc,
        # 执行参数 (半仓 + 观察期)
        "execution_params": {
            "position_pct": execution.get("position_pct", 0.04),
            "probation_trades": execution.get("probation_trades", 10),
            "cooldown_minutes": execution.get("cooldown_minutes", 5),
        },
        # 元数据
        "discovered_at": card.get("discovered_at"),
        "discovery_mode": card["discovery_mode"],
        "time_granularity": card.get("time_granularity", "1m"),
    }


def promote_v2_cards(v2_cards: list[dict]) -> dict:
    """晋升 v2 卡片批次. 返回统计结果.

    Args:
        v2_cards: mid_freq_scanner / high_freq_scanner 产出的卡片列表

    Returns:
        {"approved": int, "rejected": int, "reasons": {str: int}}
    """
    if not v2_cards:
        return {"approved": 0, "rejected": 0, "reasons": {}}

    approved_list = _read_approved()
    existing_ids = {str(c.get("id", "")) for c in approved_list}
    # 预计算已有入场条件签名集合，用于去重
    existing_sigs: set = {_entry_sig(c) for c in approved_list}

    new_approved = []
    rejected_count = 0
    rejection_reasons: dict[str, int] = {}

    for card in v2_cards:
        cid = card.get("id", "?")

        # 1. 去重: ID 已在 approved_rules 中
        if cid in existing_ids:
            rejected_count += 1
            rejection_reasons["duplicate_id"] = rejection_reasons.get("duplicate_id", 0) + 1
            continue

        # 1b. 去重: 相同入场条件 (feature, threshold, direction)
        sig = _entry_sig(card)
        if sig in existing_sigs:
            rejected_count += 1
            rejection_reasons["duplicate_condition"] = (
                rejection_reasons.get("duplicate_condition", 0) + 1
            )
            logger.info(
                "[V2Promoter] REJECT %s: duplicate entry condition %s %s %s",
                cid, sig[0], sig[2], sig[1],
            )
            continue

        # 2. 力闭环验证
        ok, msg = validate_card_force_closure(card)
        if not ok:
            rejected_count += 1
            rejection_reasons[f"force_closure: {msg[:40]}"] = (
                rejection_reasons.get(f"force_closure: {msg[:40]}", 0) + 1
            )
            logger.info("[V2Promoter] REJECT %s: %s", cid, msg)
            continue

        # 3. 出场 Top-3 组合必须非空
        if not card.get("exit", {}).get("top3"):
            rejected_count += 1
            rejection_reasons["empty_exit_top3"] = rejection_reasons.get("empty_exit_top3", 0) + 1
            continue

        # 4. 转成 v1 格式并追加
        v1_card = _card_to_v1_format(card)
        new_approved.append(v1_card)
        existing_sigs.add(_entry_sig(card))  # 防止批次内重复
        logger.info(
            "[V2Promoter] APPROVE %s (%s) P(MFE>MAE)=%.3f n_oos=%d",
            cid,
            card.get("discovery_mode", ""),
            card.get("stats", {}).get("p_mfe_gt_mae_oos", 0),
            card.get("stats", {}).get("n_oos", 0),
        )

    if new_approved:
        _write_approved(approved_list + new_approved)
        logger.info("[V2Promoter] wrote %d new cards to approved_rules.json", len(new_approved))

    return {
        "approved": len(new_approved),
        "rejected": rejected_count,
        "reasons": rejection_reasons,
    }
