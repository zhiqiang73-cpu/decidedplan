"""
Alpha 自动晋升器（每小时循环）。

流程:
  1. 加载 pending_rules.json
  2. 对每条未经 LLM 验证的规则调用 LLMMechanismValidator
  3. confidence >= 0.92  → 自动批准，移入 approved_rules.json
     0.70 <= conf < 0.92 → 进入 review_queue（保留在 pending，标记需人审）
     conf < 0.70         → 自动拒绝，移入 rejected_rules.json
  4. 写 engine_state.json（供 UI dashboard 读取）
  5. 更新 MECHANISM_CATALOG（若 LLM 推断出新机制类型）

运行:
  from alpha.auto_promoter import AutoPromoter
  promoter = AutoPromoter()
  promoter.run_once()          # 单次运行
  promoter.run_loop()          # 阻塞循环（每 interval_hours 运行一次）
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha.product_policy import infer_product_family, sync_product_candidate_pool
from alpha.force_classifier import register_card as _register_force

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path("alpha/output/promoter_config.json")
_ENGINE_STATE_FILE = Path("alpha/output/engine_state.json")
_PENDING_FILE = Path("alpha/output/pending_rules.json")
_APPROVED_FILE = Path("alpha/output/approved_rules.json")
_REJECTED_FILE = Path("alpha/output/rejected_rules.json")
_REVIEW_FILE = Path("alpha/output/review_queue.json")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else []


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _candidate_key(card: dict) -> str:
    return str(card.get("rule_str", "") or card.get("id", "") or "").strip()


def _dedupe_cards(cards: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for card in cards:
        key = _candidate_key(card)
        if key:
            merged[key] = card
    return list(merged.values())


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "llm": {
            "api_key": "",
            "base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "model": "kimi-k2.5",
        },
        "thresholds": {"auto_approve": 0.92, "review_queue": 0.70},
        "loop": {"interval_hours": 1, "max_batch_per_run": 10},
    }


# ── 引擎状态 ──────────────────────────────────────────────────────────────────


def _build_engine_state(
    *,
    status: str,
    last_run_at: str,
    next_run_at: str,
    stats: dict,
    recent_decisions: list[dict],
    config: dict,
    error: str = "",
) -> dict:
    """构建写给 UI 的 engine_state.json。"""
    llm_cfg = config.get("llm", {})
    thresholds = config.get("thresholds", {})
    return {
        "status": status,            # idle / running / error
        "last_run_at": last_run_at,
        "next_run_at": next_run_at,
        "error": error,
        "llm_config": {
            "model": llm_cfg.get("model", ""),
            "base_url": llm_cfg.get("base_url", ""),
            # 不暴露 api_key 明文，只显示前8位掩码
            "api_key_hint": (
                llm_cfg.get("api_key", "")[:8] + "****"
                if llm_cfg.get("api_key") else "(未配置)"
            ),
        },
        "thresholds": thresholds,
        "stats": stats,
        "recent_decisions": recent_decisions[-20:],  # 最近 20 条决策
        "force_library_summary": _get_force_library_summary(),
    }


def _get_force_library_summary() -> list[dict]:
    """提取力库摘要供 UI 展示。"""
    try:
        from monitor.mechanism_tracker import MECHANISM_CATALOG, MECHANISM_CATEGORIES
        rows = []
        for mtype, cfg in MECHANISM_CATALOG.items():
            cat_name = MECHANISM_CATEGORIES.get(cfg.category, {}).get("name", cfg.category)
            rows.append({
                "mechanism_type": mtype,
                "display_name": cfg.display_name or mtype,
                "category": cfg.category,
                "category_name": cat_name,
                "essence": cfg.physics.get("essence", ""),
                "validated_by": cfg.validated_by,
                "llm_confidence": cfg.llm_confidence,
            })
        return rows
    except Exception as exc:
        logger.debug("[AutoPromoter] 力库摘要获取失败: %s", exc)
        return []


# ── 主促进器 ──────────────────────────────────────────────────────────────────


class AutoPromoter:
    """
    每小时扫描 pending_rules.json，用 LLM 判断物理机制是否成立，
    自动批准/拒绝/放入人工审查队列。
    """

    def __init__(self, config: dict | None = None):
        self._config = config or _load_config()
        self._thresholds = self._config.get("thresholds", {})
        self._auto_approve_thr: float = float(
            self._thresholds.get("auto_approve", 0.92)
        )
        self._review_thr: float = float(
            self._thresholds.get("review_queue", 0.70)
        )
        self._max_batch: int = int(
            self._config.get("loop", {}).get("max_batch_per_run", 10)
        )
        self._interval_hours: float = float(
            self._config.get("loop", {}).get("interval_hours", 1)
        )

        from alpha.llm_mechanism_validator import LLMMechanismValidator
        self._validator = LLMMechanismValidator(config=self._config)

        # 累计统计（跨 run 保持）
        self._total_approved = 0
        self._total_rejected = 0
        self._total_review = 0
        self._recent_decisions: list[dict] = []

    # ── 单次运行 ──────────────────────────────────────────────────────────────

    def run_once(self) -> dict:
        """
        执行一次促进循环。

        Returns:
            本次运行摘要 dict
        """
        run_start = datetime.now(timezone.utc).isoformat()
        logger.info("[AutoPromoter] === 开始促进循环 %s ===", run_start)

        self._update_state(status="running", last_run=run_start)

        pending = _read_json(_PENDING_FILE, [])
        approved = _read_json(_APPROVED_FILE, [])
        rejected = _read_json(_REJECTED_FILE, [])
        review = _read_json(_REVIEW_FILE, [])
        if isinstance(pending, list):
            deduped_pending = _dedupe_cards(pending)
            if len(deduped_pending) != len(pending):
                logger.info(
                    "[AutoPromoter] cleaned %d duplicate pending candidate(s)",
                    len(pending) - len(deduped_pending),
                )
                pending = deduped_pending
                _write_json(_PENDING_FILE, pending)

        # Dedup: collect rule_str keys already in approved/review to skip re-validation
        existing_rule_strs: set[str] = set()
        for pool in (approved, review):
            for c in pool:
                rs = str(c.get("rule_str", "") or "").strip()
                if rs:
                    existing_rule_strs.add(rs)

        # 只处理尚未经过 LLM 验证且不重复的条目
        unvalidated = [
            c for c in pending
            if not c.get("llm_validated")
            and str(c.get("rule_str", "") or "").strip() not in existing_rule_strs
        ][:self._max_batch]

        # Remove duplicates from pending silently
        dup_count = sum(
            1 for c in pending
            if not c.get("llm_validated")
            and str(c.get("rule_str", "") or "").strip() in existing_rule_strs
        )
        if dup_count:
            logger.info("[AutoPromoter] 跳过 %d 条重复候选 (已在审查队列/已批准)", dup_count)
            pending = [
                c for c in pending
                if c.get("llm_validated")
                or str(c.get("rule_str", "") or "").strip() not in existing_rule_strs
            ]
            _write_json(_PENDING_FILE, pending)

        if not unvalidated:
            logger.info("[AutoPromoter] 没有需要验证的候选，跳过")
            summary = {"approved": 0, "rejected": 0, "review": 0, "skipped": len(pending)}
            self._flush_state(run_start, summary)
            return summary

        logger.info(
            "[AutoPromoter] 待验证: %d 条（上限 %d）",
            len(unvalidated), self._max_batch,
        )

        approved_ids: set[str] = set()
        rejected_ids: set[str] = set()
        review_ids: set[str] = set()
        deferred_ids: set[str] = set()
        deferred_candidates: dict[str, dict] = {}
        new_approved: list[dict] = []
        new_rejected: list[dict] = []
        new_review: list[dict] = []

        for candidate in unvalidated:
            cid = str(candidate.get("id", "?"))
            ckey = _candidate_key(candidate) or cid

            # 统计硬门槛前置检查，不通过直接丢弃，不送 LLM 审核
            if not self._pass_hard_gates(candidate):
                stats = candidate.get("stats", {})
                oos_ret = float(stats.get("oos_avg_ret") or stats.get("oos_net_return") or 0)
                logger.info(
                    "[AutoPromoter] REJECTED by hard gates: %s (WR=%.1f%%, n=%d, ret=%.4f)",
                    cid[:32],
                    float(stats.get("oos_win_rate") or 0),
                    int(stats.get("n_oos") or 0),
                    oos_ret,
                )
                candidate = dict(candidate)
                candidate["status"] = "gate_rejected"
                new_rejected.append(candidate)
                rejected_ids.add(ckey)
                continue

            # ── 纯统计快速批准通道 ──
            # 当统计指标足够强时，跳过大模型验证，直接批准
            if self._pass_stats_auto_approve(candidate):
                candidate = dict(candidate)
                candidate["family"] = infer_product_family(candidate)
                candidate["llm_validated"] = False
                candidate["status"] = "approved"
                candidate["approved_at"] = datetime.now(timezone.utc).isoformat()
                candidate["approved_by"] = "stats_auto"
                candidate["mechanism_type"] = str(
                    candidate.get("mechanism_type")
                    or candidate.get("entry", {}).get("mechanism_type")
                    or "data_driven"
                )
                new_approved.append(candidate)
                approved_ids.add(ckey)
                self._total_approved += 1
                self._persist_exit_params(candidate)
                try:
                    _register_force(candidate)  # 写入力注册表
                except Exception as _fe:
                    logger.warning("[AutoPromoter] 力库注册失败: %s", _fe)

                stats = candidate.get("stats", {})
                self._recent_decisions.append({
                    "id": cid[:32],
                    "rule_str": candidate.get("rule_str", "?")[:60],
                    "direction": candidate.get("entry", {}).get("direction", "?"),
                    "oos_wr": stats.get("oos_win_rate", 0),
                    "n_oos": stats.get("n_oos", 0),
                    "confidence": 1.0,
                    "mechanism_type": candidate["mechanism_type"],
                    "mechanism_display_name": candidate["mechanism_type"],
                    "is_valid": True,
                    "decision": "STATS_AUTO_APPROVED",
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                })
                self._recent_decisions = self._recent_decisions[-50:]
                logger.info(
                    "[AutoPromoter] STATS_AUTO_APPROVE %s  WR=%.1f%% n=%d PF=%.2f",
                    cid[:24],
                    float(stats.get("oos_win_rate") or 0),
                    int(stats.get("n_oos") or 0),
                    float(stats.get("oos_pf") or stats.get("oos_profit_factor") or 0),
                )
                continue

            try:
                result = self._validator.validate(candidate)
            except Exception as exc:
                logger.error("[AutoPromoter] 验证异常 %s: %s", cid[:24], exc)
                continue

            if getattr(result, "transient_failure", False):
                candidate = dict(candidate)
                candidate["family"] = infer_product_family(candidate)
                candidate["llm_validated"] = False
                candidate["llm_validation_deferred"] = True
                candidate["llm_validation_error"] = result.rejection_reason
                candidate["llm_validation_last_attempt_at"] = datetime.now(timezone.utc).isoformat()
                deferred_ids.add(ckey)
                deferred_candidates[ckey] = candidate
                self._recent_decisions.append(
                    {
                        "id": cid[:32],
                        "rule_str": candidate.get("rule_str", "?")[:60],
                        "direction": candidate.get("entry", {}).get("direction", "?"),
                        "oos_wr": candidate.get("stats", {}).get("oos_win_rate", 0),
                        "n_oos": candidate.get("stats", {}).get("n_oos", 0),
                        "confidence": 0.0,
                        "mechanism_type": result.mechanism_type,
                        "mechanism_display_name": result.mechanism_display_name,
                        "is_valid": False,
                        "decision": "LLM_DEFERRED",
                        "decided_at": datetime.now(timezone.utc).isoformat(),
                        "reason": result.rejection_reason,
                    }
                )
                self._recent_decisions = self._recent_decisions[-50:]
                logger.warning(
                    "[AutoPromoter] DEFER %s due to transient LLM failure: %s",
                    cid[:24], result.rejection_reason[:80],
                )
                continue

            # 标记已验证
            candidate = dict(candidate)
            candidate["family"] = infer_product_family(candidate)
            candidate["llm_validated"] = True
            candidate["llm_result"] = result.to_dict()
            candidate["llm_validated_at"] = datetime.now(timezone.utc).isoformat()

            decision_entry = {
                "id": cid[:32],
                "rule_str": candidate.get("rule_str", "?")[:60],
                "direction": candidate.get("entry", {}).get("direction", "?"),
                "oos_wr": candidate.get("stats", {}).get("oos_win_rate", 0),
                "n_oos": candidate.get("stats", {}).get("n_oos", 0),
                "confidence": result.confidence,
                "mechanism_type": result.mechanism_type,
                "mechanism_display_name": result.mechanism_display_name,
                "is_valid": result.is_valid,
                "decided_at": datetime.now(timezone.utc).isoformat(),
            }

            if result.is_valid and result.confidence >= self._auto_approve_thr:
                candidate["status"] = "approved"
                candidate["approved_at"] = datetime.now(timezone.utc).isoformat()
                candidate["approved_by"] = "llm_auto"
                candidate["mechanism_type"] = result.mechanism_type
                new_approved.append(candidate)
                approved_ids.add(ckey)
                decision_entry["decision"] = "AUTO_APPROVED"
                self._total_approved += 1
                self._persist_exit_params(candidate)
                self._register_mechanism_if_new(candidate, result)
                try:
                    _register_force(candidate)  # 写入力注册表
                except Exception as _fe:
                    logger.warning("[AutoPromoter] 力库注册失败: %s", _fe)
                logger.info(
                    "[AutoPromoter] AUTO_APPROVE conf=%.2f mechanism=%s  %s",
                    result.confidence, result.mechanism_type, cid[:24],
                )

            elif result.confidence >= self._review_thr:
                candidate["status"] = "review_queue"
                new_review.append(candidate)
                review_ids.add(ckey)
                decision_entry["decision"] = "REVIEW_QUEUE"
                self._total_review += 1
                logger.info(
                    "[AutoPromoter] REVIEW_QUEUE conf=%.2f  %s",
                    result.confidence, cid[:24],
                )

            else:
                candidate["status"] = "llm_rejected"
                candidate["rejection_reason"] = result.rejection_reason
                new_rejected.append(candidate)
                rejected_ids.add(ckey)
                decision_entry["decision"] = "AUTO_REJECTED"
                self._total_rejected += 1
                logger.info(
                    "[AutoPromoter] AUTO_REJECT conf=%.2f  %s  reason=%s",
                    result.confidence, cid[:24], result.rejection_reason[:60],
                )

            self._recent_decisions.append(decision_entry)
            self._recent_decisions = self._recent_decisions[-50:]  # 最多保留 50 条

        # 更新文件
        # review_queue 的条目只写入 review_queue.json，不留在 pending_rules.json
        remaining_pending = [
            deferred_candidates.get(_candidate_key(c), c) for c in pending
            if _candidate_key(c) not in approved_ids
            and _candidate_key(c) not in rejected_ids
            and _candidate_key(c) not in review_ids
        ]

        _write_json(_PENDING_FILE, remaining_pending)
        _write_json(_APPROVED_FILE, approved + new_approved)
        _write_json(_REJECTED_FILE, rejected + new_rejected)

        # Dedup review queue by rule_str before writing
        merged_review: dict[str, dict] = {}
        for c in review:
            key = str(c.get("rule_str", "") or c.get("id", ""))
            merged_review[key] = c
        for c in new_review:
            key = str(c.get("rule_str", "") or c.get("id", ""))
            merged_review[key] = c  # newer replaces older
        _write_json(_REVIEW_FILE, list(merged_review.values()))
        sync_product_candidate_pool()

        summary = {
            "approved": len(new_approved),
            "rejected": len(new_rejected),
            "review": len(new_review),
            "deferred": len(deferred_ids),
            "skipped": len(pending) - len(unvalidated),
        }
        logger.info(
            "[AutoPromoter] 本轮完成: 批准=%d 拒绝=%d 审查队列=%d 延期=%d 跳过=%d",
            summary["approved"], summary["rejected"], summary["review"], summary["deferred"], summary["skipped"],
        )
        self._flush_state(run_start, summary)
        return summary

    # ── 统计硬门槛 ────────────────────────────────────────────────────────────

    @staticmethod
    def _pass_stats_auto_approve(card: dict) -> bool:
        """
        纯统计快速批准通道 -- 指标足够强时跳过大模型验证直接批准。

        门槛组合（全部满足才放行）：
          - 样本外胜率 >= 65%（与硬门槛一致，由 PF 提供额外安全）
          - 样本外样本数 >= 12（多条件稀疏种子）或 >= 30（普通种子）
          - 样本外净收益 > 0.02%（必须明确有钱赚）
          - 样本外盈亏比 >= 1.5（不能只靠高胜率低盈亏比）
          - 降级比 >= 0.5（如果有，不比硬门槛更严）
          - MFE 覆盖率 >= 70%（如果有）

        与 _pass_hard_gates 的区别：增加了 PF 要求 + 净收益下限。
        这两个额外条件确保只有真正有钱赚的策略才走快速通道。
        """
        stats = card.get("stats", {})
        wf_stats = card.get("wf_stats", {})
        profile = str(card.get("review_profile") or card.get("discovery_profile") or "")

        # 胜率 >= 65%
        oos_wr = float(stats.get("oos_win_rate") or wf_stats.get("oos_wr") or 0)
        if oos_wr < 65.0:
            return False

        # 样本数: 不低于 30，不妥协（n=12 统计上不可靠）
        n_oos = int(stats.get("n_oos") or wf_stats.get("oos_n") or 0)
        if n_oos < 30:
            return False

        # 净收益 > 0.02%
        oos_ret = float(
            stats.get("oos_avg_ret")
            if stats.get("oos_avg_ret") is not None
            else (stats.get("oos_net_return") or wf_stats.get("oos_net") or 0)
        )
        if oos_ret <= 0.02:
            return False

        # 盈亏比 >= 1.5
        oos_pf = float(
            stats.get("oos_pf")
            or stats.get("oos_profit_factor")
            or wf_stats.get("oos_pf")
            or 0
        )
        if oos_pf < 1.5:
            return False

        # 降级比 >= 0.5（如果有）
        degradation = (
            stats.get("degradation")
            or wf_stats.get("degradation")
        )
        if degradation is not None and float(degradation) < 0.5:
            return False

        # P(MFE > MAE) >= 65%：入场后方向正确概率（核心门槛）
        # 含义：触发入场后，有利幅度 > 不利幅度 的次数占比 >= 65%
        # 做多 = 上涨幅度 > 下跌幅度；做空 = 下跌幅度 > 上涨幅度
        p_mfe_gt_mae = (
            stats.get("p_mfe_gt_mae")
            or wf_stats.get("p_mfe_gt_mae")
        )
        if p_mfe_gt_mae is not None and float(p_mfe_gt_mae) < 0.65:
            return False

        return True

    @staticmethod
    def _pass_hard_gates(card: dict) -> bool:
        """
        统计硬门槛检查 -- 不通过的候选直接丢弃，不送 LLM 审核。

        兼容两种 stats 字段命名：
          - _build_combo_card 产生的: oos_avg_ret
          - _build_card(单条件原子) 产生的: oos_net_return
        """
        stats = card.get("stats", {})

        # OOS 胜率 >= 65%（扣费后）
        if float(stats.get("oos_win_rate") or 0) < 65:
            return False
        # OOS 样本数 >= 30（不可豁免，数量不够用更多历史数据）
        if int(stats.get("n_oos") or 0) < 30:
            return False
        # OOS 净收益 > 0%（兼容两种字段名）
        oos_ret = float(
            stats.get("oos_avg_ret")
            if stats.get("oos_avg_ret") is not None
            else (stats.get("oos_net_return") or 0)
        )
        if oos_ret <= 0:
            return False
        # 降级比 > 0.5（如果有）
        degradation = stats.get("degradation")
        if degradation is not None and float(degradation) < 0.5:
            return False
        return True

    # ── 持续循环 ──────────────────────────────────────────────────────────────

    def run_loop(self) -> None:
        """持续运行，每 interval_hours 执行一次 run_once()。阻塞调用。"""
        logger.info(
            "[AutoPromoter] 启动持续循环，间隔 %.1f 小时",
            self._interval_hours,
        )
        while True:
            try:
                self.run_once()
            except Exception as exc:
                logger.error("[AutoPromoter] run_once 异常: %s", exc, exc_info=True)
                self._update_state(status="error", error=str(exc))

            next_ts = time.time() + self._interval_hours * 3600
            next_str = datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat()
            logger.info("[AutoPromoter] 下次运行: %s", next_str)
            time.sleep(self._interval_hours * 3600)

    # ── 出场参数持久化 ─────────────────────────────────────────────────────────

    @staticmethod
    def _persist_exit_params(card: dict) -> None:
        """Write candidate's ExitParams into best_params.json for runtime pickup.

        This aligns Alpha strategies with P1: once approved, the runtime's
        smart_exit_policy uses the same ExitParams framework via
        has_explicit_exit_params() / get_exit_params_for_signal().
        """
        from monitor.exit_policy_config import (
            ExitParams,
            resolve_exit_params_key,
            save_exit_params,
        )

        exit_params_dict = card.get("exit_params")
        if not isinstance(exit_params_dict, dict):
            return

        card_family = infer_product_family(card)
        entry = card.get("entry", {})
        direction = str(entry.get("direction", "")).lower()
        family = str(card_family or card.get("family") or "").strip()
        if not family:
            group = str(card.get("group") or card.get("id", ""))
            horizon = int(entry.get("horizon", 0))
            family = f"ALPHA::{group}::{direction}::{horizon}"

        key = resolve_exit_params_key(family, direction)
        try:
            valid_fields = {f.name for f in ExitParams.__dataclass_fields__.values()}
            params = ExitParams(**{
                k: v for k, v in exit_params_dict.items()
                if k in valid_fields
            })
            save_exit_params(key, params)
            logger.info("[AutoPromoter] Wrote exit params for %s", key)
        except Exception as exc:
            logger.warning("[AutoPromoter] Failed to write exit params for %s: %s", key, exc)

    # ── 力库自动注册（LLM 发现的新机制） ─────────────────────────────────────

    @staticmethod
    def _register_mechanism_if_new(candidate: dict, result: Any) -> None:
        """Register a new mechanism into the force library if LLM discovered one."""
        from monitor.mechanism_tracker import register_mechanism, MECHANISM_CATALOG

        mtype = getattr(result, "mechanism_type", "") or ""
        if not mtype or mtype in ("generic", "generic_alpha"):
            return
        if mtype in MECHANISM_CATALOG:
            # Already known — just ensure family mapping
            entry = candidate.get("entry", {})
            family = str(candidate.get("family") or "").strip()
            if family:
                from monitor.mechanism_tracker import _FAMILY_TO_MECHANISM
                if family not in _FAMILY_TO_MECHANISM:
                    _FAMILY_TO_MECHANISM[family] = mtype
            return

        entry = candidate.get("entry", {})
        family = str(candidate.get("family") or "").strip()
        direction = str(entry.get("direction", "")).lower()

        # Extract physics from LLM result
        physics = {}
        for attr in ("physics_essence", "physics_why_temporary", "physics_edge_source"):
            val = getattr(result, attr, "")
            if val:
                physics[attr.replace("physics_", "")] = val

        register_mechanism(
            mechanism_type=mtype,
            family=family,
            direction=direction,
            category=mtype.split("_")[0] if "_" in mtype else "generic",
            display_name=getattr(result, "mechanism_display_name", ""),
            physics=physics,
            primary_decay_feature=getattr(result, "primary_decay_feature", ""),
            primary_decay_condition=getattr(result, "primary_decay_condition", ""),
            decay_narrative=getattr(result, "decay_narrative", ""),
        )

    @staticmethod
    def _register_mechanism_from_card(card: dict) -> None:
        """Register mechanism from an already-validated card (manual approve path)."""
        from monitor.mechanism_tracker import register_mechanism, MECHANISM_CATALOG

        llm_result = card.get("llm_result", {})
        mtype = str(card.get("mechanism_type") or llm_result.get("mechanism_type") or "")
        if not mtype or mtype in ("generic", "generic_alpha") or mtype in MECHANISM_CATALOG:
            return

        entry = card.get("entry", {})
        family = str(card.get("family") or "").strip()
        physics = llm_result.get("physics", {})
        primary_decay = llm_result.get("primary_decay", {})

        register_mechanism(
            mechanism_type=mtype,
            family=family,
            direction=str(entry.get("direction", "")).lower(),
            category=mtype.split("_")[0] if "_" in mtype else "generic",
            display_name=llm_result.get("mechanism_display_name", ""),
            physics=physics,
            primary_decay_feature=primary_decay.get("feature", ""),
            primary_decay_condition=primary_decay.get("condition", ""),
            decay_narrative=primary_decay.get("narrative", ""),
        )

    # ── 手动审批接口（供 dashboard API 调用） ─────────────────────────────────

    def manual_approve(self, rule_id: str) -> bool:
        """人工批准一条规则（从 pending 或 review_queue 移入 approved）。"""
        for src_file in [_PENDING_FILE, _REVIEW_FILE]:
            rules = _read_json(src_file, [])
            target = next((c for c in rules if c.get("id") == rule_id), None)
            if target:
                remaining = [c for c in rules if c.get("id") != rule_id]
                _write_json(src_file, remaining)

                approved = _read_json(_APPROVED_FILE, [])
                target = dict(target)
                target["family"] = infer_product_family(target)
                if not target["family"]:
                    logger.warning("[AutoPromoter] manual approve blocked: %s missing canonical family", rule_id[:32])
                    return False
                target["status"] = "approved"
                target["approved_at"] = datetime.now(timezone.utc).isoformat()
                target["approved_by"] = "human_manual"
                approved.append(target)
                _write_json(_APPROVED_FILE, approved)
                self._persist_exit_params(target)
                self._register_mechanism_from_card(target)
                try:
                    _register_force(target)  # 写入力注册表
                except Exception as _fe:
                    logger.warning("[AutoPromoter] 力库注册失败: %s", _fe)
                sync_product_candidate_pool()

                self._recent_decisions.append({
                    "id": rule_id[:32],
                    "rule_str": target.get("rule_str", "?")[:60],
                    "decision": "HUMAN_APPROVED",
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                })
                self._flush_state(None, None)
                logger.info("[AutoPromoter] 人工批准: %s", rule_id[:32])
                return True
        return False

    def manual_reject(self, rule_id: str) -> bool:
        """人工拒绝一条规则。"""
        for src_file in [_PENDING_FILE, _REVIEW_FILE]:
            rules = _read_json(src_file, [])
            target = next((c for c in rules if c.get("id") == rule_id), None)
            if target:
                remaining = [c for c in rules if c.get("id") != rule_id]
                _write_json(src_file, remaining)

                rejected = _read_json(_REJECTED_FILE, [])
                target = dict(target)
                target["status"] = "human_rejected"
                target["rejected_at"] = datetime.now(timezone.utc).isoformat()
                rejected.append(target)
                _write_json(_REJECTED_FILE, rejected)

                self._recent_decisions.append({
                    "id": rule_id[:32],
                    "rule_str": target.get("rule_str", "?")[:60],
                    "decision": "HUMAN_REJECTED",
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                })
                self._flush_state(None, None)
                logger.info("[AutoPromoter] 人工拒绝: %s", rule_id[:32])
                return True
        return False

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _update_state(
        self,
        status: str,
        last_run: str | None = None,
        error: str = "",
    ) -> None:
        existing = _read_json(_ENGINE_STATE_FILE, {})
        existing["status"] = status
        if last_run:
            existing["last_run_at"] = last_run
        if error:
            existing["error"] = error
        _write_json(_ENGINE_STATE_FILE, existing)

    def _flush_state(self, last_run: str | None, summary: dict | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        next_run = datetime.fromtimestamp(
            time.time() + self._interval_hours * 3600, tz=timezone.utc
        ).isoformat()

        pending_count = len(_read_json(_PENDING_FILE, []))
        approved_count = len(_read_json(_APPROVED_FILE, []))
        rejected_count = len(_read_json(_REJECTED_FILE, []))
        review_count = len(_read_json(_REVIEW_FILE, []))

        stats = {
            "pending_count": pending_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "review_count": review_count,
            "total_approved_this_session": self._total_approved,
            "total_rejected_this_session": self._total_rejected,
            "total_review_this_session": self._total_review,
        }
        if summary:
            stats["last_run_summary"] = summary

        state = _build_engine_state(
            status="idle",
            last_run_at=last_run or now,
            next_run_at=next_run,
            stats=stats,
            recent_decisions=self._recent_decisions,
            config=self._config,
        )
        _write_json(_ENGINE_STATE_FILE, state)
