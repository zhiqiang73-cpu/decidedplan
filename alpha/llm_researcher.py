"""
Kimi 驱动的量化研究员模块。

职责:
  - 提出物理机制假设
  - 结合扫描结果决定是否继续
  - 生成 sandbox 可执行的入场/出场代码
  - 基于触发统计、WF 结果、回测报告做多轮评审
"""

from __future__ import annotations

import ast
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from alpha.auto_explain import FEATURE_META as _PROJECT_FEATURE_META
except Exception as exc:  # pragma: no cover - 依赖缺失时兜底
    logger.warning("[KimiResearcher] 导入 FEATURE_META 失败: %s", exc)
    _PROJECT_FEATURE_META: dict[str, dict[str, Any]] = {}

try:
    from alpha.scanner import FEATURE_DIM as _PROJECT_FEATURE_DIM
except Exception as exc:  # pragma: no cover - 依赖缺失时兜底
    logger.warning("[KimiResearcher] 导入 FEATURE_DIM 失败: %s", exc)
    _PROJECT_FEATURE_DIM: dict[str, str] = {}


FEATURE_META: dict[str, dict[str, Any]] = dict(_PROJECT_FEATURE_META)
FEATURE_DIM: dict[str, str] = dict(_PROJECT_FEATURE_DIM)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_FILE = _REPO_ROOT / "alpha" / "output" / "promoter_config.json"
_METHODOLOGY_FILE = _REPO_ROOT / "docs" / "P_SERIES_METHODOLOGY.md"

_DEFAULT_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
_DEFAULT_MODEL = "kimi-k2.5"
_DEFAULT_TIMEOUT = 60
_DEFAULT_MAX_RETRIES = 2

_JSON_TEMPERATURE = 0.10
_CODE_TEMPERATURE = 0.20
_JSON_MAX_TOKENS = 8000
_CODE_MAX_TOKENS = 6000
_TEXT_LIMIT = 10000

_DEFAULT_STOP_GRID = [0.30, 0.50, 0.70, 1.00, 1.50]
_DEFAULT_PROTECT_GRID = [0.05, 0.08, 0.12, 0.18, 0.25]

_EXTRA_TIME_FEATURES = {
    "funding_countdown_m",
    "minute_of_day",
    "day_of_week",
    "session_open",
}

_JSON_FENCE_PATTERNS = (
    r"```json\s*([\s\S]*?)\s*```",
    r"```python\s*([\s\S]*?)\s*```",
    r"```\s*([\s\S]*?)\s*```",
)


@dataclass
class Hypothesis:
    mechanism_name: str
    force_description: str
    why_temporary: str
    direction: str
    entry_features: list[str]
    persistence_requirement: str
    predicted_trigger_rate: float
    predicted_win_rate: float
    horizon: int

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        fallback_name: str,
    ) -> "Hypothesis":
        return cls(
            mechanism_name=str(payload.get("mechanism_name") or fallback_name).strip(),
            force_description=str(payload.get("force_description") or "").strip(),
            why_temporary=str(payload.get("why_temporary") or "").strip(),
            direction=_normalize_direction(payload.get("direction")),
            entry_features=_ensure_string_list(payload.get("entry_features")),
            persistence_requirement=str(
                payload.get("persistence_requirement") or ""
            ).strip(),
            predicted_trigger_rate=_safe_float(
                payload.get("predicted_trigger_rate"), default=0.0
            ),
            predicted_win_rate=_safe_float(
                payload.get("predicted_win_rate"), default=0.0
            ),
            horizon=max(1, int(_safe_float(payload.get("horizon"), default=10.0))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchSession:
    session_id: str
    hypotheses: list[Hypothesis]
    conversation_history: list[dict]
    active_hypothesis_idx: int
    phase: str
    artifacts: dict
    started_at: str
    iteration_count: int = 0
    max_iterations: int = 3

    def current_hypothesis(self) -> Hypothesis | None:
        if 0 <= self.active_hypothesis_idx < len(self.hypotheses):
            return self.hypotheses[self.active_hypothesis_idx]
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_direction(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"short", "做空", "空", "sell", "bear", "看空"}:
        return "short"
    if text in {"both", "双向", "long/short", "long_short"}:
        return "both"
    if text in {"long", "做多", "多", "buy", "bull", "看多"}:
        return "long"
    return "long"


def _ensure_string_list(value: Any) -> list[str]:
    """将各种格式的特征列表统一为 list[str]。

    处理 Kimi 可能返回的多种格式:
      - ["feat1", "feat2"]  -> 标准列表
      - "feat1, feat2"      -> 逗号分隔字符串
      - {"seed": "feat1", "confirm": ["feat2", "feat3"]}  -> 嵌套 dict
      - [{"seed": ..., "confirm": ...}]  -> dict 列表
    """
    if value is None:
        return []
    # 处理嵌套 dict: {"seed": "x", "confirm": ["y", "z"]} -> ["x", "y", "z"]
    if isinstance(value, dict):
        result: list[str] = []
        for v in value.values():
            result.extend(_ensure_string_list(v))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            if isinstance(item, dict):
                # 递归展开嵌套 dict
                result.extend(_ensure_string_list(item))
            elif isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(text)
            elif isinstance(item, (list, tuple)):
                result.extend(_ensure_string_list(item))
            else:
                text = str(item).strip()
                if text:
                    result.append(text)
        return result
    if isinstance(value, str):
        parts = re.split(r"[,,、/]+", value)
        return [part.strip() for part in parts if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    text = text.replace("%", "")
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    truthy = {
        "true",
        "yes",
        "y",
        "1",
        "accept",
        "proceed",
        "approve",
        "是",
        "继续",
        "通过",
        "批准",
        "接受",
    }
    falsy = {
        "false",
        "no",
        "n",
        "0",
        "reject",
        "skip",
        "abandon",
        "否",
        "拒绝",
        "跳过",
        "放弃",
    }
    if text in truthy:
        return True
    if text in falsy:
        return False
    return default


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:
            pass
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _pretty_json(payload: Any, *, limit: int = _TEXT_LIMIT) -> str:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
    except Exception:
        text = str(payload)
    return _trim_text(text, limit=limit)


def _trim_text(text: Any, *, limit: int = _TEXT_LIMIT) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n...<已截断>..."


def _read_text_with_fallback(path: Path) -> str:
    encodings = ("utf-8", "utf-8-sig", "gbk", "cp936")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except Exception as exc:
            last_error = exc
    raise OSError(f"读取文件失败: {path}") from last_error


def _balanced_segment(text: str, opening: str, closing: str) -> str:
    start = text.find(opening)
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == opening:
            depth += 1
            continue
        if char == closing:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return ""


def _compact_stat_value(value: Any) -> str:
    if isinstance(value, dict):
        preferred = [
            "n",
            "count",
            "sample_count",
            "coverage",
            "availability",
            "ic",
            "IC",
            "icir",
            "ICIR",
            "mean",
            "std",
            "min",
            "p05",
            "p25",
            "median",
            "p50",
            "p75",
            "p95",
            "max",
        ]
        keys = [key for key in preferred if key in value]
        if not keys:
            keys = list(value.keys())[:8]
        parts = []
        for key in keys:
            parts.append(f"{key}={_compact_stat_value(value[key])}")
        return ", ".join(parts)
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in list(value)[:8]]
        return "[" + ", ".join(parts) + "]"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _normalize_grid_values(value: Any, fallback: list[float]) -> list[float]:
    raw_values = value
    if isinstance(value, dict):
        for key in ("grid", "candidates", "values", "levels"):
            if key in value:
                raw_values = value[key]
                break

    if not isinstance(raw_values, (list, tuple, set)):
        raw_values = []

    parsed: list[float] = []
    for item in raw_values:
        number = _safe_float(item, default=0.0)
        if number > 0:
            parsed.append(round(number, 4))

    parsed = sorted(set(parsed))
    if parsed:
        return parsed
    return list(fallback)


class KimiResearcher:
    MAX_MODIFICATION_LOOPS = 3
    MAX_CONVERSATION_TURNS = 20

    def __init__(self, config: dict | None = None):
        self._config = config if config is not None else self._load_config()
        llm_cfg = self._config.get("llm", {})
        self._api_key: str = str(llm_cfg.get("api_key") or "").strip()
        self._base_url: str = str(llm_cfg.get("base_url") or _DEFAULT_BASE_URL).strip()
        self._model: str = str(llm_cfg.get("model") or _DEFAULT_MODEL).strip()
        self._timeout: int = int(llm_cfg.get("timeout") or _DEFAULT_TIMEOUT)
        self._max_retries: int = int(llm_cfg.get("max_retries") or _DEFAULT_MAX_RETRIES)
        self._methodology_text: str = self._load_methodology_text()
        self._client = None
        self._client_error = ""
        self._time_feature_set = self._build_time_feature_set()

    def _load_config(self) -> dict:
        if _CONFIG_FILE.exists():
            try:
                return json.loads(_read_text_with_fallback(_CONFIG_FILE))
            except Exception as exc:
                logger.warning("[KimiResearcher] 读取配置失败: %s", exc)

        return {
            "llm": {
                "api_key": "",
                "base_url": _DEFAULT_BASE_URL,
                "model": _DEFAULT_MODEL,
                "timeout": _DEFAULT_TIMEOUT,
                "max_retries": _DEFAULT_MAX_RETRIES,
            }
        }

    def _load_methodology_text(self) -> str:
        if not _METHODOLOGY_FILE.exists():
            logger.warning("[KimiResearcher] 方法论文件不存在: %s", _METHODOLOGY_FILE)
            return "方法论文件缺失。"
        try:
            return _read_text_with_fallback(_METHODOLOGY_FILE)
        except Exception as exc:
            logger.warning("[KimiResearcher] 读取方法论失败: %s", exc)
            return f"方法论文件读取失败: {exc}"

    def _ensure_client(self) -> None:
        if self._client is not None or self._client_error:
            return
        if not self._api_key:
            self._client_error = "API key 未配置。"
            return

        try:
            from openai import OpenAI
        except ImportError as exc:
            self._client_error = f"openai 包导入失败: {exc}"
            logger.warning("[KimiResearcher] %s", self._client_error)
            return

        try:
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        except Exception as exc:
            self._client_error = f"OpenAI 客户端初始化失败: {exc}"
            logger.warning("[KimiResearcher] %s", self._client_error)

    def _get_client(self):
        self._ensure_client()
        if self._client is not None:
            return self._client
        raise RuntimeError(self._client_error or "LLM client unavailable")

    def _build_time_feature_set(self) -> set[str]:
        time_features = {
            feature
            for feature, dim in FEATURE_DIM.items()
            if str(dim).upper() == "TIME"
        }
        time_features.update(
            feature
            for feature, meta in FEATURE_META.items()
            if str(meta.get("category", "")).upper() == "TIME"
        )
        time_features.update(_EXTRA_TIME_FEATURES)
        return time_features

    def _extract_available_features(
        self,
        feature_stats: Any,
        data_availability: Any,
    ) -> tuple[set[str], set[str]]:
        available: set[str] = set()
        unavailable: set[str] = set()

        if isinstance(feature_stats, dict):
            mapping = feature_stats.get("features")
            if isinstance(mapping, dict):
                for feature in mapping:
                    available.add(str(feature))
            else:
                for feature, value in feature_stats.items():
                    if feature in FEATURE_META or feature in FEATURE_DIM:
                        if value is not None:
                            available.add(str(feature))
        elif isinstance(feature_stats, list):
            for item in feature_stats:
                if isinstance(item, dict):
                    feature = item.get("feature") or item.get("name")
                    if feature:
                        available.add(str(feature))

        if isinstance(data_availability, dict):
            for key in ("feature_availability", "features"):
                mapping = data_availability.get(key)
                if isinstance(mapping, dict):
                    for feature, status in mapping.items():
                        is_available = (
                            status.get("available")
                            if isinstance(status, dict)
                            else status
                        )
                        if _coerce_bool(is_available, default=True):
                            available.add(str(feature))
                        else:
                            unavailable.add(str(feature))

            direct_feature_keys = [
                key
                for key in data_availability
                if key in FEATURE_META or key in FEATURE_DIM
            ]
            for feature in direct_feature_keys:
                status = data_availability.get(feature)
                if _coerce_bool(
                    status.get("available") if isinstance(status, dict) else status,
                    default=True,
                ):
                    available.add(str(feature))
                else:
                    unavailable.add(str(feature))

            available_list = data_availability.get("available_features")
            if isinstance(available_list, (list, tuple, set)):
                for feature in available_list:
                    available.add(str(feature))

            unavailable_list = data_availability.get("unavailable_features")
            if isinstance(unavailable_list, (list, tuple, set)):
                for feature in unavailable_list:
                    unavailable.add(str(feature))

        return available, unavailable

    def _format_feature_stats(self, feature_stats: Any) -> str:
        if feature_stats is None:
            return "未提供动态统计。"

        lines: list[str] = []
        mapping: dict[str, Any] | None = None

        if isinstance(feature_stats, dict):
            if isinstance(feature_stats.get("features"), dict):
                mapping = {
                    str(feature): stats
                    for feature, stats in feature_stats["features"].items()
                }
            else:
                candidate_keys = [
                    str(feature)
                    for feature in feature_stats
                    if feature in FEATURE_META or feature in FEATURE_DIM
                ]
                if candidate_keys:
                    mapping = {feature: feature_stats[feature] for feature in candidate_keys}

        if mapping:
            for feature in sorted(mapping):
                meta = FEATURE_META.get(feature, {})
                dim = FEATURE_DIM.get(feature, meta.get("category", "OTHER"))
                desc = meta.get("desc", "未收录描述")
                stats_text = _compact_stat_value(mapping.get(feature))
                lines.append(
                    f"- {feature} [{dim}] {desc} | 统计: {stats_text}"
                )
            return "\n".join(lines) if lines else "未提供可识别的特征统计。"

        if isinstance(feature_stats, list):
            return _pretty_json(feature_stats, limit=4000)

        return _pretty_json(feature_stats, limit=4000)

    def _format_data_availability(self, data_availability: Any) -> str:
        if data_availability is None:
            return "未提供数据可用性说明。"
        return _pretty_json(data_availability, limit=4000)

    def _build_feature_catalog(
        self,
        available_features: set[str],
        unavailable_features: set[str],
    ) -> str:
        catalog_features = sorted(set(FEATURE_META) | set(FEATURE_DIM) | available_features)
        lines: list[str] = []

        for feature in catalog_features:
            meta = FEATURE_META.get(feature, {})
            dim = FEATURE_DIM.get(feature, meta.get("category", "OTHER"))
            desc = meta.get("desc", "未收录描述")
            high = meta.get("high", "")
            low = meta.get("low", "")

            if feature in unavailable_features:
                status = "缺失"
            elif feature in available_features:
                status = "可用"
            else:
                status = "未知"

            if feature in self._time_feature_set or str(dim).upper() == "TIME":
                status = "禁用(TIME)"

            lines.append(
                f"- {feature} | 维度={dim} | 状态={status} | 含义={desc} | 高值含义={high} | 低值含义={low}"
            )

        return "\n".join(lines)

    def _build_system_prompt(self, feature_stats: Any, data_availability: Any) -> str:
        available_features, unavailable_features = self._extract_available_features(
            feature_stats, data_availability
        )
        feature_stats_text = self._format_feature_stats(feature_stats)
        data_availability_text = self._format_data_availability(data_availability)
        feature_catalog_text = self._build_feature_catalog(
            available_features,
            unavailable_features,
        )

        allowed_hint = (
            ", ".join(sorted(available_features))
            if available_features
            else "未显式声明,默认只能使用在动态统计或特征目录中存在且未被标记缺失的特征"
        )
        unavailable_hint = (
            ", ".join(sorted(unavailable_features))
            if unavailable_features
            else "无"
        )
        time_hint = ", ".join(sorted(self._time_feature_set))

        parts = []
        parts.append("你是 BTC 永续合约 Alpha 发现引擎的量化研究员。你掌握从假设到验证的完整方法论,能自主完成策略发现。")
        parts.append("")
        parts.append("## 核心哲学")
        parts.append("- 入场 = 捕捉一股暂时失衡的力（微观结构失衡）")
        parts.append("- 出场 = 判断这股力是否已释放/衰竭,用 vs_entry（当前值 - 入场值）")
        parts.append("- 你赚的是'失衡到回归'这段距离")
        parts.append("- 不接受纯价格形态、纯时间窗口、拍脑袋模式")
        parts.append("")
        parts.append("## 你掌握的参数优化方法论")
        parts.append("")
        parts.append("### 入场: 你告诉引擎扫什么特征、什么分位数、什么方向")
        parts.append("- 用 p1/p2/p3/p5（LONG 入场）或 p95/p97/p98/p99（SHORT 入场）")
        parts.append("- 双特征组合: 种子特征 + 确认特征（不同维度交叉）")
        parts.append("- crossing detection + 60 bar cooldown")
        parts.append("- 目标触发率 0.5-3%,n_oos >= 30")
        parts.append("")
        parts.append("### 出场: 你指定 vs_entry 阈值扫描范围,引擎帮你跑")
        parts.append("- 种子特征 vs_entry 变化量: 扫 [0.005, 0.008, 0.010, 0.015, 0.020, 0.030]")
        parts.append("- 辅助特征 vs_entry: 扫 [-0.05, -0.10, -0.15, -0.20, -0.30]")
        parts.append("- 出场阈值不能太小（太激进提前出场）也不能太大（等太久）")
        parts.append("")
        parts.append("### 止损/保护: 你指定扫描范围")
        parts.append("- 止损 stop_pct: [0.30, 0.50, 0.70, 1.00, 1.50]")
        parts.append("- 保护 protect_start_pct: [0.04, 0.08, 0.12, 0.15, 0.20]")
        parts.append("")
        parts.append("### 最终门槛")
        parts.append("- OOS WR >= 65%（扣 Maker 0.04% 后）")
        parts.append("- OOS n >= 30")
        parts.append("- OOS 净收益 >= 0.03%（每笔至少 3 个基点）")
        parts.append("- PF >= 1.2")
        parts.append("- 必须 LONG 和 SHORT 方向都产出策略")
        parts.append("")
        parts.append("### 迭代（最多 3 轮）")
        parts.append("1. 收紧入场阈值（p95到p98,减少噪声）")
        parts.append("2. 放宽出场阈值（让利润跑更远）")
        parts.append("3. 加宽止损（0.5%到1.0%,减少被震出）")
        parts.append("")
        parts.append("## 硬性约束")
        parts.append("1. entry_features 必须是精确列名字符串列表")
        parts.append("2. 入场必须有 persistence 或 multi-bar 确认")
        parts.append(f"3. 禁止 TIME 维度特征: {time_hint}")
        parts.append("4. 代码只能用 np/pd/math/statistics")
        parts.append("5. 中文注释和结论,JSON 或代码格式输出")
        parts.append("")
        parts.append(f"当前已知可用特征:\n{allowed_hint}")
        parts.append(f"\n当前明确不可用特征:\n{unavailable_hint}")
        parts.append(f"\n动态数据可用性:\n{data_availability_text}")
        parts.append(f"\n动态特征统计:\n{feature_stats_text}")
        parts.append(f"\n特征目录:\n{feature_catalog_text}")
        parts.append(f"\n===== P_SERIES_METHODOLOGY.md BEGIN =====\n{self._methodology_text}\n===== P_SERIES_METHODOLOGY.md END =====")
        return "\n".join(parts)

    def _trim_history(self, history: list[dict]) -> list[dict]:
        if len(history) <= self.MAX_CONVERSATION_TURNS:
            return history

        system_messages = [msg for msg in history if msg.get("role") == "system"]
        non_system = [msg for msg in history if msg.get("role") != "system"]
        keep_non_system = max(self.MAX_CONVERSATION_TURNS - len(system_messages), 0)
        trimmed = system_messages[:1] + non_system[-keep_non_system:]
        return trimmed[-self.MAX_CONVERSATION_TURNS :]

    def _call_kimi(self, session: ResearchSession, user_message: str) -> str:
        try:
            if not session.conversation_history:
                system_prompt = str(
                    session.artifacts.get("system_prompt")
                    or self._build_system_prompt(
                        session.artifacts.get("feature_stats"),
                        session.artifacts.get("data_availability"),
                    )
                )
                session.conversation_history.append(
                    {"role": "system", "content": system_prompt}
                )

            session.conversation_history.append({"role": "user", "content": user_message})
            session.conversation_history = self._trim_history(
                session.conversation_history
            )

            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=session.conversation_history,
                temperature=0.3,
            )
            assistant_content = (
                response.choices[0].message.content if response.choices else ""
            ) or ""
            session.conversation_history.append(
                {"role": "assistant", "content": assistant_content}
            )
            session.conversation_history = self._trim_history(
                session.conversation_history
            )
            return assistant_content
        except Exception as exc:
            logger.warning("[KimiResearcher] Kimi 调用失败: %s", exc)
            return json.dumps(
                {"error": f"Kimi 调用失败: {exc}"},
                ensure_ascii=False,
            )

    def _parse_json_response(self, raw: str) -> dict[str, Any] | list[Any]:
        text = str(raw or "").strip()
        if not text:
            return {"error": "空回复"}

        candidates: list[str] = [text]
        for pattern in _JSON_FENCE_PATTERNS:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            for item in matches:
                cleaned = item.strip()
                if cleaned:
                    candidates.append(cleaned)

        for opening, closing in (("{", "}"), ("[", "]")):
            segment = _balanced_segment(text, opening, closing)
            if segment:
                candidates.append(segment)

        seen: set[str] = set()
        unique_candidates: list[str] = []
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_candidates.append(normalized)

        for candidate in unique_candidates:
            try:
                return json.loads(candidate)
            except Exception:
                pass

            try:
                repaired = (
                    candidate.replace("true", "True")
                    .replace("false", "False")
                    .replace("null", "None")
                )
                parsed = ast.literal_eval(repaired)
                if isinstance(parsed, (dict, list)):
                    return parsed
            except Exception:
                pass

        return {"error": "JSON 解析失败", "raw": _trim_text(text, limit=1200)}

    def _extract_python_code(self, raw: str, *, expected_def: str) -> str:
        text = str(raw or "")
        for pattern in _JSON_FENCE_PATTERNS[1:]:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            for match in matches:
                code = match.strip()
                if expected_def in code:
                    return code

        if expected_def in text:
            start = text.find(expected_def)
            return text[start:].strip()
        return ""

    def _current_hypothesis_payload(self, session: ResearchSession) -> dict[str, Any]:
        hypothesis = session.current_hypothesis()
        return hypothesis.to_dict() if hypothesis is not None else {}

    def _bump_iteration(self, session: ResearchSession, *, accepted: bool) -> None:
        if accepted:
            return
        session.iteration_count += 1
        if session.iteration_count > session.max_iterations:
            session.iteration_count = session.max_iterations

    def start_research_session(
        self,
        feature_stats: Any,
        data_availability: Any,
    ) -> ResearchSession:
        session = ResearchSession(
            session_id=(
                "kimi_research_"
                f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_"
                f"{uuid.uuid4().hex[:8]}"
            ),
            hypotheses=[],
            conversation_history=[],
            active_hypothesis_idx=0,
            phase="hypothesis",
            artifacts={
                "feature_stats": feature_stats,
                "data_availability": data_availability,
                "system_prompt": self._build_system_prompt(
                    feature_stats,
                    data_availability,
                ),
            },
            started_at=_now_iso(),
            iteration_count=0,
            max_iterations=self.MAX_MODIFICATION_LOOPS,
        )

        user_prompt = f"""你的任务: 发现 BTC 永续合约新策略,LONG 和 SHORT 方向都要有。

## 已被占用的力（避开这些,P 系列已在跑）
- vwap_deviation + vol_drought（P1-8）
- position_in_range_24h + 缩量（P1-9）
- dist_to_24h_low + taker_buy_sell_ratio（P1-10）
- position_in_range_4h + funding_rate（P1-11）

## 你要做的
提出 6 个新假设（至少 3 个 LONG + 3 个 SHORT）,每个假设直接指定引擎要扫描的参数。

## 数据可用性
{_pretty_json(data_availability, limit=2000)}

## 特征统计（p5/p95 帮你选阈值范围）
{_pretty_json(feature_stats, limit=4000)}

## 输出格式（严格 JSON 数组,不要嵌套 dict）
```json
[
  {{
    "mechanism_name": "简短英文ID",
    "force_description": "什么力在失衡",
    "why_temporary": "为什么会消退",
    "direction": "long",
    "entry_features": ["feature_a", "feature_b"],
    "seed_feature": "feature_a",
    "seed_operator": "<",
    "seed_percentiles": [1, 2, 3, 5],
    "confirm_feature": "feature_b",
    "confirm_operator": ">",
    "confirm_percentiles": [95, 97, 98, 99],
    "persistence_bars": 3,
    "cooldown_bars": 60,
    "exit_seed_vs_entry_range": [0.005, 0.008, 0.010, 0.015, 0.020],
    "exit_confirm_vs_entry_range": [-0.05, -0.10, -0.15, -0.20],
    "stop_grid": [0.30, 0.50, 0.70, 1.00, 1.50],
    "horizon": 15,
    "predicted_trigger_rate": 1.0,
    "predicted_win_rate": 70
  }}
]
```

关键:
- seed_feature 和 seed_operator/seed_percentiles: 引擎会在 IS 数据上计算这些分位数的实际值作为阈值
- confirm_feature: 第二个维度的确认条件（不同维度交叉）
- persistence_bars: 种子条件需要连续满足的 bar 数（0=不要求持续性,用 crossing detection）
- exit_seed_vs_entry_range: 出场时种子特征 vs_entry 变化量的扫描范围
- 6 个假设,至少 3 LONG + 3 SHORT,entry_features 是纯字符串列表
"""

        raw = self._call_kimi(session, user_prompt)
        parsed = self._parse_json_response(raw)
        session.artifacts["phase1_raw"] = raw
        session.artifacts["phase1_parsed"] = parsed

        hypotheses_payload: list[dict[str, Any]] = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    hypotheses_payload.append(item)
        elif isinstance(parsed, dict) and isinstance(parsed.get("hypotheses"), list):
            for item in parsed["hypotheses"]:
                if isinstance(item, dict):
                    hypotheses_payload.append(item)
        elif isinstance(parsed, dict) and "error" in parsed:
            session.artifacts["error"] = parsed["error"]

        # Fallback: 如果标准解析失败,尝试从 raw 中找所有完整 JSON 数组
        if not hypotheses_payload and raw:
            import json as _json
            # 找所有 [...] 段,取最长的（最可能是完整 hypotheses 数组）
            bracket_segments = []
            for m in re.finditer(r'\[', raw):
                seg = _balanced_segment(raw[m.start():], '[', ']')
                if seg and len(seg) > 50:  # 至少 50 字符才可能是有效 JSON
                    bracket_segments.append(seg)
            bracket_segments.sort(key=len, reverse=True)
            for seg in bracket_segments:
                try:
                    arr = _json.loads(seg)
                    if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                        hypotheses_payload = [item for item in arr if isinstance(item, dict)]
                        logger.info("[KimiResearcher] Fallback JSON 解析成功: %d 个假设", len(hypotheses_payload))
                        break
                except Exception:
                    continue

        session.hypotheses = [
            Hypothesis.from_dict(payload, fallback_name=f"hypothesis_{idx}")
            for idx, payload in enumerate(hypotheses_payload, start=1)
        ]
        return session

    def feed_scan_results(
        self,
        session: ResearchSession,
        hypothesis_idx: int,
        scan_results: Any,
    ) -> dict[str, Any]:
        session.active_hypothesis_idx = max(0, hypothesis_idx)
        session.phase = "scanning"
        session.artifacts.setdefault("scan_results", {})[hypothesis_idx] = scan_results

        hypothesis = self._current_hypothesis_payload(session)
        user_prompt = f"""以下是 IC 扫描结果。Phase 2 只做一个判断: 特征在数据中是否有预测力（|ICIR| > 0.1 且 n_days >= 10）。

重要: IC 的正负号不等于入场方向！比如 P1-10 LONG 用 dist_to_24h_low 做多,IC 是负的（低值预测跌）,但入场逻辑是"低值 = 卖方耗尽 = 反弹"。IC 只说明特征有预测力,方向由物理机制决定,不由 IC 符号决定。

同样: 特征已被 P 系列使用不是拒绝理由。同一个特征可以用于不同的物理机制组合。

判断标准（只要满足一个就 proceed=true）:
- 任一特征 |ICIR| > 0.1
- 任一特征 |t_stat| > 1.5
- 特征组合在物理上说得通（即使单个 IC 弱）

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## 扫描结果
{_pretty_json(scan_results, limit=6000)}

只输出 JSON:
{{
  "proceed": true/false,
  "assessment": "一句话"
}}
"""

        raw = self._call_kimi(session, user_prompt)
        parsed = self._parse_json_response(raw)
        session.artifacts.setdefault("scan_assessments", {})[hypothesis_idx] = {
            "raw": raw,
            "parsed": parsed,
        }

        if isinstance(parsed, dict) and "error" not in parsed:
            proceed = _coerce_bool(parsed.get("proceed"), default=False)
            assessment = str(parsed.get("assessment") or "").strip()
        else:
            proceed = False
            assessment = (
                str(parsed.get("error"))
                if isinstance(parsed, dict)
                else "扫描评估解析失败"
            )

        if proceed:
            session.phase = "entry"
        return {"proceed": proceed, "assessment": assessment}

    def request_entry_code(self, session: ResearchSession) -> str:
        session.phase = "entry"
        hypothesis = self._current_hypothesis_payload(session)
        scan_results = session.artifacts.get("scan_results", {}).get(
            session.active_hypothesis_idx,
            {},
        )

        user_prompt = f"""基于扫描结果,请写一个入场检测函数。

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## 扫描结果
{_pretty_json(scan_results, limit=6000)}

## 要求
1. 函数签名: def detect_entry(df: pd.DataFrame) -> pd.Series
2. 返回 bool Series,True = 入场信号
3. 必须包含持续性检查（不是单 bar 阈值穿越）
4. 使用 crossing detection with cooldown（首次穿越+冷却期）
5. 目标触发率: 0.5-3%
6. 只能用 numpy (np) 和 pandas (pd),不能 import

只输出函数代码,用 ```python ``` 包裹。
"""

        raw = self._call_kimi(session, user_prompt)
        code = self._extract_python_code(raw, expected_def="def detect_entry")
        session.artifacts["entry_code_raw"] = raw
        session.artifacts["entry_code"] = code
        return code

    def feed_entry_statistics(
        self,
        session: ResearchSession,
        entry_stats: Any,
    ) -> dict[str, Any]:
        session.phase = "entry"
        session.artifacts["entry_stats"] = entry_stats

        hypothesis = self._current_hypothesis_payload(session)
        user_prompt = f"""以下是入场检测函数的触发统计,请判断是否满意。

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## 入场统计
{_pretty_json(entry_stats, limit=5000)}

请只输出 JSON:
{{
  "accept": true/false,
  "feedback": "中文反馈。若不接受,明确指出该如何修改持续性、冷却期或阈值逻辑"
}}
"""

        raw = self._call_kimi(session, user_prompt)
        parsed = self._parse_json_response(raw)
        session.artifacts["entry_feedback"] = {"raw": raw, "parsed": parsed}

        if isinstance(parsed, dict) and "error" not in parsed:
            accept = _coerce_bool(parsed.get("accept"), default=False)
            feedback = str(parsed.get("feedback") or "").strip()
        else:
            accept = False
            feedback = (
                str(parsed.get("error"))
                if isinstance(parsed, dict)
                else "入场反馈解析失败"
            )

        self._bump_iteration(session, accepted=accept)
        if accept:
            session.phase = "wf"
        return {"accept": accept, "feedback": feedback}

    def feed_walk_forward_results(
        self,
        session: ResearchSession,
        wf_results: Any,
    ) -> dict[str, Any]:
        session.phase = "wf"
        session.artifacts["wf_results"] = wf_results

        hypothesis = self._current_hypothesis_payload(session)
        user_prompt = f"""以下是 Walk-Forward 验证结果,请判断是否继续进入出场挖掘阶段。

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## Walk-Forward 结果
{_pretty_json(wf_results, limit=6000)}

请只输出 JSON:
{{
  "proceed": true/false,
  "decision": "中文结论。说明是否继续做出场挖掘,或者建议修改什么"
}}
"""

        raw = self._call_kimi(session, user_prompt)
        parsed = self._parse_json_response(raw)
        session.artifacts["wf_feedback"] = {"raw": raw, "parsed": parsed}

        if isinstance(parsed, dict) and "error" not in parsed:
            proceed = _coerce_bool(parsed.get("proceed"), default=False)
            decision = str(parsed.get("decision") or "").strip()
        else:
            proceed = False
            decision = (
                str(parsed.get("error"))
                if isinstance(parsed, dict)
                else "Walk-Forward 反馈解析失败"
            )

        self._bump_iteration(session, accepted=proceed)
        if proceed:
            session.phase = "exit"
        return {"proceed": proceed, "decision": decision}

    def request_exit_mining_code(
        self,
        session: ResearchSession,
        mfe_data: Any,
    ) -> str:
        session.phase = "exit"
        session.artifacts["mfe_data"] = mfe_data
        hypothesis = self._current_hypothesis_payload(session)

        user_prompt = f"""以下是入场后的 MFE 峰值数据,请写出场条件挖掘代码。

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## MFE 数据
{_pretty_json(mfe_data, limit=7000)}

## 要求
遵循 P 系列 MFE 峰值方法论:
1. 对每个入场点,找 MFE 峰值 bar
2. 在峰值附近收集 vs_entry 特征值（当前值 - 入场值）
3. 找区分"好出场"/"坏出场"的特征组合
4. 输出 Top-3 出场条件

函数签名:
def mine_exit_conditions(df, entry_positions, direction, close) -> dict

返回格式:
{{
  "top3": [{{"conditions": [{{"feature": "feat_vs_entry", "operator": ">", "threshold": 0.01}}], "combo_label": "C1", "description": "..."}}],
  "invalidation": [{{"conditions": [], "combo_label": "I1", "description": "论文失效条件"}}],
  "stop_grid": {{"values": [0.2, 0.3, 0.4, 0.5, 0.7, 1.0]}},
  "protect_grid": {{"values": [0.03, 0.05, 0.08, 0.10, 0.12]}}
}}

只能用 numpy (np) 和 pandas (pd)。只输出函数代码。
"""

        raw = self._call_kimi(session, user_prompt)
        code = self._extract_python_code(
            raw,
            expected_def="def mine_exit_conditions",
        )
        session.artifacts["exit_code_raw"] = raw
        session.artifacts["exit_code"] = code
        return code

    def request_stop_grid_spec(self, session: ResearchSession) -> dict[str, Any]:
        session.phase = "exit"
        hypothesis = self._current_hypothesis_payload(session)
        mfe_data = session.artifacts.get("mfe_data", {})

        user_prompt = f"""请根据当前研究上下文,给出止损网格与利润保护网格。

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## MFE 摘要
{_pretty_json(mfe_data, limit=5000)}

请只输出 JSON:
{{
  "stop_grid": [0.2, 0.3, 0.4, 0.5, 0.7, 1.0],
  "protect_grid": [0.03, 0.05, 0.08, 0.10, 0.12]
}}
"""

        raw = self._call_kimi(session, user_prompt)
        parsed = self._parse_json_response(raw)

        if isinstance(parsed, dict) and "error" not in parsed:
            stop_grid = _normalize_grid_values(
                parsed.get("stop_grid"),
                _DEFAULT_STOP_GRID,
            )
            protect_grid = _normalize_grid_values(
                parsed.get("protect_grid"),
                _DEFAULT_PROTECT_GRID,
            )
        else:
            stop_grid = list(_DEFAULT_STOP_GRID)
            protect_grid = list(_DEFAULT_PROTECT_GRID)

        result = {"stop_grid": stop_grid, "protect_grid": protect_grid}
        session.artifacts["stop_grid_spec"] = {
            "raw": raw,
            "parsed": parsed,
            "normalized": result,
        }
        return result

    def final_review(
        self,
        session: ResearchSession,
        backtest_report: Any,
    ) -> dict[str, Any]:
        session.phase = "review"
        session.artifacts["backtest_report"] = backtest_report
        hypothesis = self._current_hypothesis_payload(session)

        user_prompt = f"""以下是完整回测报告,请做最终审核。

## 当前假设
{_pretty_json(hypothesis, limit=3000)}

## 完整回测报告
{_pretty_json(backtest_report, limit=9000)}

请只输出 JSON:
{{
  "decision": "approve/reject/modify",
  "confidence": 0.0,
  "mechanism_type": "机制类型",
  "frozen_thresholds": {{}},
  "rejection_reason": "",
  "modification_instructions": ""
}}
"""

        raw = self._call_kimi(session, user_prompt)
        parsed = self._parse_json_response(raw)

        if isinstance(parsed, dict) and "error" not in parsed:
            decision = str(parsed.get("decision") or "reject").strip().lower()
            if decision not in {"approve", "reject", "modify"}:
                decision = "reject"
            confidence = max(
                0.0,
                min(1.0, _safe_float(parsed.get("confidence"), default=0.0)),
            )
            mechanism_type = str(parsed.get("mechanism_type") or "").strip()
            frozen_thresholds = (
                parsed.get("frozen_thresholds")
                if isinstance(parsed.get("frozen_thresholds"), dict)
                else {}
            )
            rejection_reason = str(parsed.get("rejection_reason") or "").strip()
            modification_instructions = str(
                parsed.get("modification_instructions") or ""
            ).strip()
        else:
            decision = "reject"
            confidence = 0.0
            mechanism_type = ""
            frozen_thresholds = {}
            rejection_reason = (
                str(parsed.get("error"))
                if isinstance(parsed, dict)
                else "最终审核解析失败"
            )
            modification_instructions = ""

        result = {
            "decision": decision,
            "confidence": confidence,
            "mechanism_type": mechanism_type,
            "frozen_thresholds": frozen_thresholds,
            "rejection_reason": rejection_reason,
            "modification_instructions": modification_instructions,
        }
        session.artifacts["final_review"] = {
            "raw": raw,
            "parsed": parsed,
            "normalized": result,
        }
        return result
