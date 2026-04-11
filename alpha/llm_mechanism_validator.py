"""
LLM 机制验证器。

职责划分:
  - 代码做数学: IC scan / walk-forward / MFE/MAE (live_discovery.py 已完成)
  - LLM 做物理: 因果链验证、机制命名、衰竭条件生成、力库匹配

调用流程:
  1. 加载 MECHANISM_CATALOG (力库背景知识)
  2. 构建 system prompt: 教 LLM 理解"入场=捕捉一股力/出场=判断力用完"哲学
  3. 构建 user prompt: 输入候选规则统计数据 + 入场特征
  4. 调用 kimi-k2.5 via OpenAI SDK (DashScope base_url)
  5. 解析 JSON 输出: is_valid / confidence / mechanism_type / physics / decay_conditions
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path("alpha/output/promoter_config.json")
_CATALOG_MODULE = "monitor.mechanism_tracker"


# ── 结果数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class LLMValidationResult:
    """LLM 验证结果。"""

    is_valid: bool
    confidence: float                       # 0.0 ~ 1.0
    mechanism_type: str                     # 匹配到的力库机制 ID
    mechanism_display_name: str = ""        # 人类可读名称
    physics_essence: str = ""              # 一句话：这股力的本质是什么
    physics_why_temporary: str = ""        # 为什么这股力会消退
    physics_edge_source: str = ""          # 交易优势从哪里来
    primary_decay_feature: str = ""        # 主因衰竭特征
    primary_decay_condition: str = ""      # 衰竭条件描述
    decay_narrative: str = ""              # 完整衰竭叙事
    entry_narrative: str = ""              # 入场物理叙事
    confirms: list[str] = field(default_factory=list)  # 次级确认特征
    rejection_reason: str = ""             # 若 is_valid=False 的原因
    raw_response: str = ""                 # 原始 LLM 输出（调试用）
    llm_model: str = ""
    # 6 个物理机制评估字段（新增）
    force_description: str = ""            # 信号捕捉的"力"的描述和物理来源
    force_duration: str = ""               # 力的持续时长和消失原因
    confirm_directional: bool = True       # 确认因子是否有方向偏见（false=无效）
    daily_frequency: str = ""              # 每天重复出现频率估计
    trend_safe: bool = True                # 在单边趋势中是否不会反复误触发（false=无效）
    exit_captures_decay: bool = True       # vs_entry 出场是否能捕捉力的消失
    transient_failure: bool = False        # 网络/API/依赖失败，不应被当成策略无效

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "confidence": self.confidence,
            "mechanism_type": self.mechanism_type,
            "mechanism_display_name": self.mechanism_display_name,
            "physics": {
                "essence": self.physics_essence,
                "why_temporary": self.physics_why_temporary,
                "edge_source": self.physics_edge_source,
            },
            "primary_decay": {
                "feature": self.primary_decay_feature,
                "condition": self.primary_decay_condition,
                "narrative": self.decay_narrative,
            },
            "entry_narrative": self.entry_narrative,
            "confirms": self.confirms,
            "rejection_reason": self.rejection_reason,
            "llm_model": self.llm_model,
            # 6 个物理机制评估字段
            "force_description": self.force_description,
            "force_duration": self.force_duration,
            "confirm_directional": self.confirm_directional,
            "daily_frequency": self.daily_frequency,
            "trend_safe": self.trend_safe,
            "exit_captures_decay": self.exit_captures_decay,
            "transient_failure": self.transient_failure,
        }



# ── 力库摘要（从 MECHANISM_CATALOG 动态提取） ─────────────────────────────────


def _build_catalog_summary() -> str:
    """
    从 monitor.mechanism_tracker.MECHANISM_CATALOG 提取力库摘要，
    注入 LLM system prompt，让 LLM 理解已验证的机制。
    """
    try:
        from monitor.mechanism_tracker import MECHANISM_CATALOG
    except ImportError:
        logger.warning("[LLMValidator] 无法导入 MECHANISM_CATALOG，使用空摘要")
        return "(力库暂时不可用)"

    lines: list[str] = []
    for mtype, cfg in MECHANISM_CATALOG.items():
        display = cfg.display_name or mtype
        cat = cfg.category
        essence = cfg.physics.get("essence", "")
        validated = ", ".join(cfg.validated_by) if cfg.validated_by else "无"
        primary = f"{cfg.primary.feature} {cfg.primary.op} {cfg.primary.threshold}"
        lines.append(
            f"- [{mtype}] ({cat}) {display}\n"
            f"  本质: {essence}\n"
            f"  主因衰竭: {primary}\n"
            f"  实证策略: {validated}"
        )
    return "\n".join(lines)


# ── System Prompt ─────────────────────────────────────────────────────────────


_SYSTEM_PROMPT_TEMPLATE = """你是 BTC 永续合约微结构分析师。请按以下框架评估候选策略，用 JSON 格式回答。

## 核心哲学
**入场 = 捕捉一股力（微观结构失衡）**
**出场 = 判断这股力用完了**

力的来源必须是微观结构失衡（资金费率/持仓量/流动性/成交流），不是纯价格模式。

## 已验证的力库（参考背景）
{catalog_summary}

## 评估框架（6 个核心问题）
你必须在 JSON 中回答以下 6 个问题：

1. force_description: 这个信号捕捉的是什么"力"？力的物理来源？
2. force_duration: 这股力是暂时的吗？预计持续多少分钟？为什么会消失？
3. confirm_directional: 确认因子是否有方向偏见？
   - 如果确认因子涨跌都会触发（如价差扩大、成交量放大），回答 false
   - 只有在特定方向才会出现才回答 true
4. daily_frequency: 这个力在一天中会重复出现几次（大约）？
5. trend_safe: 在单边趋势中，这个信号会不会反复误触发？
   - 如果在连续上涨/下跌中会频繁触发（如价格持续高于VWAP），回答 false
6. exit_captures_decay: vs_entry 出场条件能否捕捉到力的消失？

## 关键判断规则
- confirm_directional=false 或 trend_safe=false 直接导致 is_valid=false，拒绝
- 力的来源必须是微观结构失衡，不是纯价格模式
- confidence >= 0.92: 物理机制清晰，因果链完整 → 自动批准
- 0.70 <= confidence < 0.92: 机制可信但有不确定性 → 进入人工审查队列
- confidence < 0.70: 纯统计噪音、方向错误、机制不成立 → 自动拒绝

## 输出格式
必须返回严格的 JSON，不要有多余文字：

```json
{{
  "is_valid": true/false,
  "confidence": 0.00-1.00,
  "mechanism_type": "机制ID（从力库中选或提出新的）",
  "mechanism_display_name": "简短的中文名称",
  "force_description": "这个信号捕捉的是什么力？力的物理来源是什么？",
  "force_duration": "这股力预计持续多少分钟？为什么会消失？",
  "confirm_directional": true/false,
  "daily_frequency": "每天大约出现几次？",
  "trend_safe": true/false,
  "exit_captures_decay": true/false,
  "physics_essence": "一句话：这股力的本质是什么（具体，不要空洞）",
  "physics_why_temporary": "为什么这股力会在N根K线内消退",
  "physics_edge_source": "交易优势从哪里来（具体的结构性原因）",
  "primary_decay_feature": "主因衰竭应该看哪个特征",
  "primary_decay_condition": "具体的衰竭阈值条件，如 dist_to_24h_high < -0.015",
  "decay_narrative": "完整的衰竭叙事：当...发生时，意味着那股力消退了",
  "entry_narrative": "入场叙事：为什么此刻入场捕捉到的是这股力",
  "confirms": ["次级确认特征1", "次级确认特征2"],
  "rejection_reason": "如果 is_valid=false，解释原因（confirm_directional=false 或 trend_safe=false 时必填）"
}}
```

**重要**: 高胜率/高盈亏比本身不是批准理由。必须有清晰的物理因果解释。
"""

_USER_PROMPT_TEMPLATE = """请验证以下候选交易规则：

## 规则基本信息
- 规则ID: {rule_id}
- 入场方向: {direction}
- 观测窗口: {horizon} bars
- 入场条件: {rule_str}

## 统计数据（OOS 样本外验证）
- 胜率: {oos_wr:.1f}%
- 样本数: {n_oos}
- 盈亏比 (PF): {oos_pf:.3f}
- 平均收益: {oos_avg_ret:+.4f}%
- 种子单独胜率: {seed_oos_wr:.1f}%
- 组合提升: +{wr_improvement:.1f}%

## 入场特征详情
- 主特征: {entry_feature} {entry_op} {entry_threshold}
  含义: {entry_feature_desc}
- 确认特征: {confirm_feature} {confirm_op} {confirm_threshold}
  含义: {confirm_feature_desc}

## 已推断的机制类型
当前系统推断为: {inferred_mechanism}

## 你的任务
1. 判断这个规则是否捕捉了真实的物理力
2. 给出置信度评分
3. 说明当这股力消退时应该平仓的信号
4. 如果认为无效，清楚解释原因

请严格返回 JSON 格式。"""


# ── 特征描述表（辅助 LLM 理解特征物理含义） ─────────────────────────────────


_FEATURE_DESCRIPTIONS: dict[str, str] = {
    "dist_to_24h_high": "当前价格距24小时最高价的距离（负值=低于最高价，越接近0越靠近最高点）",
    "dist_to_24h_low": "当前价格距24小时最低价的距离（正值=高于最低价）",
    "vwap_deviation": "当前价格偏离VWAP的程度（正=高于VWAP，负=低于VWAP）",
    "position_in_range_24h": "在24小时高低价范围内的位置（0=最低，1=最高）",
    "position_in_range_4h": "在4小时高低价范围内的位置",
    "oi_change_rate_5m": "5分钟未平仓合约变化率（正=OI增加/加杠杆，负=OI减少/去杠杆）",
    "oi_change_rate_1h": "1小时未平仓合约变化率",
    "spread_vs_ma20": "当前买卖价差相对20周期均值的倍数（>1=价差扩大/流动性恶化）",
    "taker_buy_sell_ratio": "主动买入与主动卖出成交量比值（>1=主动买多，<1=主动卖多）",
    "volume_vs_ma20": "当前成交量相对20周期均值的倍数",
    "volume_acceleration": "成交量加速度（正=量在快速放大，负=量在萎缩）",
    "kyle_lambda": "价格冲击系数（高=市场深度浅，每单位成交量对价格冲击大）",
    "funding_rate": "资金费率（正=多头付空头，负=空头付多头）",
    "amplitude_1m": "1分钟K线振幅",
    "large_trade_buy_ratio": "大单买入占比",
    "direction_net_1m": "1分钟方向净值",
    "sell_notional_share_1m": "1分钟卖方名义价值占比",
    "trade_burst_index": "交易爆发指数",
    "avg_trade_size": "平均成交笔数大小",
}


def _get_feature_desc(feature: str) -> str:
    return _FEATURE_DESCRIPTIONS.get(feature, f"特征 {feature}（无描述）")


# ── 配置加载 ──────────────────────────────────────────────────────────────────


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
            "timeout": 60,
            "max_retries": 2,
        }
    }


# ── 主验证器 ──────────────────────────────────────────────────────────────────


class LLMMechanismValidator:
    """
    使用 LLM 对候选规则进行物理机制验证。

    设计原则:
    - LLM 不做统计判断（IC / WR 代码已做）
    - LLM 只做物理因果判断：这股力是否真实存在、何时消退
    - 输出结构化 JSON，由 auto_promoter 决定是否批准
    """

    def __init__(self, config: dict | None = None):
        if config is None:
            config = _load_config()
        llm_cfg = config.get("llm", {})
        self._api_key: str = llm_cfg.get("api_key", "")
        self._base_url: str = llm_cfg.get("base_url", "https://coding.dashscope.aliyuncs.com/v1")
        self._model: str = llm_cfg.get("model", "kimi-k2.5")
        self._timeout: int = int(llm_cfg.get("timeout", 60))
        self._max_retries: int = int(llm_cfg.get("max_retries", 2))
        self._client = None
        self._catalog_summary: str | None = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                import sys
                logger.error(
                    "[LLMValidator] openai import failed: %s\nsys.path=%s",
                    exc, sys.path[:5],
                )
                raise RuntimeError(
                    f"openai 包导入失败: {exc}"
                ) from exc
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._client

    def _get_catalog_summary(self) -> str:
        if self._catalog_summary is None:
            self._catalog_summary = _build_catalog_summary()
        return self._catalog_summary

    def validate(self, candidate: dict) -> LLMValidationResult:
        """
        验证单个候选规则。

        Args:
            candidate: pending_rules.json 中的一条规则 dict

        Returns:
            LLMValidationResult
        """
        if not self._api_key:
            logger.warning("[LLMValidator] API key 未配置，跳过 LLM 验证")
            return LLMValidationResult(
                is_valid=False,
                confidence=0.0,
                mechanism_type="generic",
                rejection_reason="API key 未配置",
                transient_failure=True,
            )

        # 构建 prompt
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            catalog_summary=self._get_catalog_summary()
        )
        user_prompt = self._build_user_prompt(candidate)

        logger.info(
            "[LLMValidator] 验证规则: %s  方向=%s  OOS_WR=%.1f%%  n=%s",
            candidate.get("id", "?")[:24],
            candidate.get("entry", {}).get("direction", "?"),
            candidate.get("stats", {}).get("oos_win_rate", 0),
            candidate.get("stats", {}).get("n_oos", 0),
        )

        raw = ""
        for attempt in range(1, self._max_retries + 2):
            try:
                client = self._get_client()
                response = client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=1024,
                )
                raw = response.choices[0].message.content or ""
                break
            except Exception as exc:
                logger.warning(
                    "[LLMValidator] 第 %d 次调用失败: %s", attempt, exc
                )
                if attempt >= self._max_retries + 1:
                    return LLMValidationResult(
                        is_valid=False,
                        confidence=0.0,
                        mechanism_type="generic",
                        rejection_reason=f"LLM 调用失败: {exc}",
                        raw_response=raw,
                        transient_failure=True,
                    )

        return self._parse_response(raw, candidate)

    def _build_user_prompt(self, candidate: dict) -> str:
        entry = candidate.get("entry", {})
        stats = candidate.get("stats", {})
        combo = candidate.get("combo_conditions", [{}])
        confirm = combo[0] if combo else {}

        return _USER_PROMPT_TEMPLATE.format(
            rule_id=candidate.get("id", "unknown")[:32],
            direction=entry.get("direction", "?"),
            horizon=entry.get("horizon", 60),
            rule_str=candidate.get("rule_str", "N/A"),
            oos_wr=float(stats.get("oos_win_rate", 0)),
            n_oos=int(stats.get("n_oos", 0)),
            oos_pf=float(stats.get("oos_pf", 0)),
            oos_avg_ret=float(stats.get("oos_avg_ret", stats.get("oos_net_return", 0))),
            seed_oos_wr=float(stats.get("seed_oos_wr", stats.get("oos_win_rate", 0))),
            wr_improvement=float(stats.get("wr_improvement", 0)),
            entry_feature=entry.get("feature", "?"),
            entry_op=entry.get("operator", "?"),
            entry_threshold=entry.get("threshold", 0),
            entry_feature_desc=_get_feature_desc(entry.get("feature", "")),
            confirm_feature=confirm.get("feature", "无"),
            confirm_op=confirm.get("op", ""),
            confirm_threshold=confirm.get("threshold", ""),
            confirm_feature_desc=_get_feature_desc(confirm.get("feature", "")),
            inferred_mechanism=candidate.get("mechanism_type", "generic_alpha"),
        )

    def _parse_response(
        self, raw: str, candidate: dict
    ) -> LLMValidationResult:
        """解析 LLM 返回的 JSON，容错处理。"""
        # 提取 JSON 块
        json_str = raw.strip()
        # 去除 markdown 代码块
        for pattern in [r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"]:
            m = re.search(pattern, json_str)
            if m:
                json_str = m.group(1)
                break

        data: dict[str, Any] = {}
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(
                "[LLMValidator] JSON 解析失败，原始输出: %s", raw[:300]
            )
            # 尝试提取关键字段
            is_valid_match = re.search(r'"is_valid"\s*:\s*(true|false)', raw, re.I)
            conf_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
            return LLMValidationResult(
                is_valid=is_valid_match.group(1).lower() == "true" if is_valid_match else False,
                confidence=float(conf_match.group(1)) if conf_match else 0.0,
                mechanism_type=candidate.get("mechanism_type", "generic"),
                rejection_reason="LLM 输出 JSON 解析失败",
                raw_response=raw[:2000],
                llm_model=self._model,
            )

        # 提取 6 个物理机制评估字段
        confirm_directional = bool(data.get("confirm_directional", True))
        trend_safe = bool(data.get("trend_safe", True))

        # 关键判断规则：confirm_directional=false 或 trend_safe=false → is_valid=false
        is_valid_raw = bool(data.get("is_valid", False))
        rejection_reason_raw = str(data.get("rejection_reason", ""))
        if not confirm_directional:
            is_valid_raw = False
            if not rejection_reason_raw:
                rejection_reason_raw = "confirm_directional=false: 确认因子无方向偏见，涨跌都会触发"
        if not trend_safe:
            is_valid_raw = False
            if not rejection_reason_raw:
                rejection_reason_raw = "trend_safe=false: 在单边趋势中会反复误触发"

        return LLMValidationResult(
            is_valid=is_valid_raw,
            confidence=float(data.get("confidence", 0.0)),
            mechanism_type=str(data.get("mechanism_type", candidate.get("mechanism_type", "generic"))),
            mechanism_display_name=str(data.get("mechanism_display_name", "")),
            physics_essence=str(data.get("physics_essence", "")),
            physics_why_temporary=str(data.get("physics_why_temporary", "")),
            physics_edge_source=str(data.get("physics_edge_source", "")),
            primary_decay_feature=str(data.get("primary_decay_feature", "")),
            primary_decay_condition=str(data.get("primary_decay_condition", "")),
            decay_narrative=str(data.get("decay_narrative", "")),
            entry_narrative=str(data.get("entry_narrative", "")),
            confirms=list(data.get("confirms", [])),
            rejection_reason=rejection_reason_raw,
            raw_response=raw[:2000],
            llm_model=self._model,
            force_description=str(data.get("force_description", "")),
            force_duration=str(data.get("force_duration", "")),
            confirm_directional=confirm_directional,
            daily_frequency=str(data.get("daily_frequency", "")),
            trend_safe=trend_safe,
            exit_captures_decay=bool(data.get("exit_captures_decay", True)),
        )
