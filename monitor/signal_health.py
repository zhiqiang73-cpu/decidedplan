"""信号健康度监控与生命周期管理。"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SignalHealth:
    """按 card_id 跟踪滚动健康度，并管理生命周期状态。"""

    def __init__(
        self,
        outcomes_path: str | Path = "data/signal_outcomes.jsonl",
        state_path: str | Path = "data/signal_health_state.json",
        rolling_window_days: int = 30,
    ):
        """初始化结果落盘路径、状态路径和滚动窗口。"""
        self.outcomes_path = Path(outcomes_path)
        self.state_path = Path(state_path)
        self.rolling_window_days = max(1, int(rolling_window_days))
        self._lock = threading.Lock()

        self.outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def record_outcome(
        self,
        card_id: str,
        direction: str,
        net_return_pct: float,
        flow_type: str = "",
        regime: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        """追加写入一条交易结果到 JSONL 文件。"""
        ts = self._normalize_datetime(timestamp)
        payload = {
            "card_id": str(card_id),
            "direction": str(direction),
            "net_return_pct": float(net_return_pct),
            "flow_type": str(flow_type),
            "regime": str(regime),
            "ts": ts.isoformat(),
        }

        try:
            self.outcomes_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self.outcomes_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("写入信号结果失败: %s", exc)

    def get_state(self, card_id: str) -> str:
        """获取某个 card_id 当前的生命周期状态。"""
        state = self._load_state()
        entry = state.get(card_id, {})
        return self._normalize_state(entry.get("state"))

    def get_rolling_stats(self, card_id: str) -> dict:
        """获取某个 card_id 在滚动窗口内的汇总统计。"""
        cutoff = self._utc_now() - timedelta(days=self.rolling_window_days)
        records = [
            record
            for record in self._load_outcomes(cutoff=cutoff)
            if record["card_id"] == card_id
        ]
        stats = self._compute_stats(records)
        stats["state"] = self.get_state(card_id)
        return stats

    def get_stats_by_flow(self, card_id: str) -> dict[str, dict]:
        """按 flow_type 返回某个 card_id 在滚动窗口内的统计结果。"""
        cutoff = self._utc_now() - timedelta(days=self.rolling_window_days)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for record in self._load_outcomes(cutoff=cutoff):
            if record["card_id"] != card_id:
                continue
            grouped[str(record.get("flow_type", ""))].append(record)

        return {
            flow_type: self._compute_stats(records)
            for flow_type, records in grouped.items()
        }

    def update_states(self) -> dict[str, str]:
        """按最近完成日的日度 PF 序列更新生命周期状态。"""
        now = self._utc_now()
        now_iso = now.isoformat()
        reference_day = now.date() - timedelta(days=1)
        state = self._load_state()
        cutoff = now - timedelta(days=self.rolling_window_days)
        recent_outcomes = self._load_outcomes(cutoff=cutoff)

        outcomes_by_card: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in recent_outcomes:
            outcomes_by_card[record["card_id"]].append(record)

        cards_to_check = set(outcomes_by_card)
        cards_to_check.update(
            card_id
            for card_id, payload in state.items()
            if self._normalize_state(payload.get("state")) == "degraded"
        )

        transitions: dict[str, str] = {}
        for card_id in cards_to_check:
            entry = self._normalize_state_entry(state.get(card_id))
            current_state = entry["state"]
            daily_pf = self._build_daily_pf_map(outcomes_by_card.get(card_id, []))
            new_state = current_state

            if current_state == "active":
                if self._has_consecutive_days(
                    daily_pf,
                    end_day=reference_day,
                    days=7,
                    predicate=lambda value: value < 0.8,
                ):
                    new_state = "degraded"
                    entry["degraded_since"] = now_iso
            elif current_state == "degraded":
                if self._has_consecutive_days(
                    daily_pf,
                    end_day=reference_day,
                    days=3,
                    predicate=lambda value: value > 1.2,
                ):
                    new_state = "active"
                    entry["degraded_since"] = None
                else:
                    degraded_since = self._parse_timestamp(entry.get("degraded_since"))
                    if degraded_since is None:
                        degraded_since = now
                        entry["degraded_since"] = now_iso
                    if (now.date() - degraded_since.date()).days >= 14:
                        new_state = "retired"
            else:
                new_state = "retired"

            if new_state != current_state:
                entry["state"] = new_state
                transitions[card_id] = new_state

            entry["last_checked"] = now_iso
            state[card_id] = entry

        if cards_to_check or state:
            self._save_state(state)

        return transitions

    def _load_state(self) -> dict[str, dict]:
        """从状态文件读取并规范化 card_id 状态字典。"""
        if not self.state_path.exists():
            return {}

        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            logger.warning("读取信号健康状态失败，已回退默认状态: %s", exc)
            return {}

        if not isinstance(payload, dict):
            logger.warning("信号健康状态文件格式异常，已回退默认状态")
            return {}

        normalized: dict[str, dict] = {}
        for card_id, raw_entry in payload.items():
            normalized[str(card_id)] = self._normalize_state_entry(raw_entry)
        return normalized

    def _save_state(self, state: dict) -> None:
        """将状态字典保存到 JSON 文件。"""
        safe_state: dict[str, dict[str, Any]] = {}
        for card_id, entry in state.items():
            safe_state[str(card_id)] = self._normalize_state_entry(entry)

        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self.state_path.open("w", encoding="utf-8") as fh:
                    json.dump(safe_state, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("保存信号健康状态失败: %s", exc)

    def _load_outcomes(
        self,
        cutoff: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """读取并规范化结果文件中的记录，可按时间截断。"""
        if not self.outcomes_path.exists():
            return []

        records: list[dict[str, Any]] = []
        try:
            with self.outcomes_path.open("r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "信号结果文件第 %s 行 JSON 损坏，已跳过: %s",
                            line_no,
                            exc,
                        )
                        continue

                    record = self._normalize_outcome(payload, line_no=line_no)
                    if record is None:
                        continue
                    if cutoff is not None and record["ts"] < cutoff:
                        continue
                    records.append(record)
        except Exception as exc:
            logger.warning("读取信号结果失败，已回退空结果: %s", exc)
            return []

        return records

    def _normalize_outcome(
        self,
        payload: Any,
        line_no: int | None = None,
    ) -> dict[str, Any] | None:
        """将单条结果记录整理成统一结构。"""
        if not isinstance(payload, dict):
            logger.warning("信号结果记录不是对象，已跳过: line=%s", line_no)
            return None

        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            logger.warning("信号结果记录缺少 card_id，已跳过: line=%s", line_no)
            return None

        ts = self._parse_timestamp(payload.get("ts"))
        if ts is None:
            logger.warning("信号结果记录时间戳异常，已跳过: line=%s", line_no)
            return None

        try:
            net_return_pct = float(payload.get("net_return_pct", 0.0))
        except (TypeError, ValueError):
            logger.warning("信号结果记录收益率异常，已跳过: line=%s", line_no)
            return None

        return {
            "card_id": card_id,
            "direction": str(payload.get("direction", "")),
            "net_return_pct": net_return_pct,
            "flow_type": str(payload.get("flow_type", "")),
            "regime": str(payload.get("regime", "")),
            "ts": ts,
        }

    def _normalize_state_entry(self, entry: Any) -> dict[str, Any]:
        """清洗状态文件中的单个 card_id 条目。"""
        if not isinstance(entry, dict):
            entry = {}

        degraded_since = entry.get("degraded_since")
        if self._parse_timestamp(degraded_since) is None:
            degraded_since = None

        last_checked = entry.get("last_checked")
        if self._parse_timestamp(last_checked) is None:
            last_checked = None

        return {
            "state": self._normalize_state(entry.get("state")),
            "degraded_since": degraded_since,
            "last_checked": last_checked,
        }

    def _normalize_state(self, value: Any) -> str:
        """将状态值规范成 active、degraded 或 retired。"""
        state = str(value or "active").strip().lower()
        if state in {"active", "degraded", "retired"}:
            return state
        return "active"

    def _compute_stats(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """从结果列表计算胜率、利润因子、平均收益和样本数。"""
        sample_count = len(records)
        if sample_count == 0:
            return {
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_return": 0.0,
                "sample_count": 0,
            }

        returns = [float(record["net_return_pct"]) for record in records]
        wins = sum(1 for value in returns if value > 0)
        positive_sum = sum(value for value in returns if value > 0)
        negative_sum = sum(value for value in returns if value < 0)

        if negative_sum == 0:
            profit_factor = float("inf")
        else:
            profit_factor = positive_sum / abs(negative_sum)

        return {
            "win_rate": wins / sample_count,
            "profit_factor": profit_factor,
            "avg_return": sum(returns) / sample_count,
            "sample_count": sample_count,
        }

    def _build_daily_pf_map(
        self,
        records: list[dict[str, Any]],
    ) -> dict[date, float]:
        """把结果列表汇总成按 UTC 自然日计算的利润因子。"""
        grouped: dict[date, list[float]] = defaultdict(list)
        for record in records:
            grouped[record["ts"].date()].append(float(record["net_return_pct"]))

        daily_pf: dict[date, float] = {}
        for day, returns in grouped.items():
            positive_sum = sum(value for value in returns if value > 0)
            negative_sum = sum(value for value in returns if value < 0)

            if negative_sum == 0:
                if positive_sum > 0:
                    daily_pf[day] = float("inf")
                else:
                    daily_pf[day] = 0.0
            else:
                daily_pf[day] = positive_sum / abs(negative_sum)

        return daily_pf

    def _has_consecutive_days(
        self,
        daily_pf: dict[date, float],
        end_day: date,
        days: int,
        predicate: Callable[[float], bool],
    ) -> bool:
        """检查在有交易记录的日子中，截止某天是否有连续 N 个交易日满足 PF 条件。

        修复：跳过无交易日（PF 为 None），只在有交易记录的日子中做连续计数。
        稀少触发策略（如 P1-11 每周 1-2 次）不会因无交易日中断连续判断。
        """
        if days <= 0:
            return False

        # 收集 end_day 之前（含当天）所有有交易日，按日期降序排列
        trading_days = sorted(
            (d for d in daily_pf if d <= end_day),
            reverse=True,
        )

        # 从最近的有交易日往前数，检查连续 N 个有交易日是否全满足条件
        consecutive = 0
        for d in trading_days:
            value = daily_pf.get(d)
            if value is not None and predicate(value):
                consecutive += 1
                if consecutive >= days:
                    return True
            else:
                # 当前有交易日不满足条件，连续链断开
                break
        return False

    def _parse_timestamp(self, value: Any) -> datetime | None:
        """解析 ISO8601 时间，统一转成带 UTC 时区的 datetime。"""
        if not value:
            return None

        if isinstance(value, datetime):
            return self._normalize_datetime(value)

        if not isinstance(value, str):
            return None

        text = value.strip()
        if not text:
            return None

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return self._normalize_datetime(parsed)

    def _normalize_datetime(self, value: datetime | None) -> datetime:
        """把时间对象规范成带 UTC 时区的 datetime。"""
        if value is None:
            return self._utc_now()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _utc_now(self) -> datetime:
        """返回当前 UTC 时间。"""
        return datetime.now(timezone.utc)
