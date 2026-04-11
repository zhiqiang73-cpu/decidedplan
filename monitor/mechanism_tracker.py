"""
机制生命周期追踪器。

核心目标不是看统计止盈止损，而是判断入场时捕捉到的那股“力”还在不在。
如果主因已经衰竭，再由次级迹象一起确认，就把持仓从“继续拿着”逐步切到
“收紧保护”或“直接退出”。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)
_FORCE_REGISTRY_PATH = Path(__file__).parent.parent / "alpha" / "output" / "force_registry.json"
_APPROVED_RULES_PATH = Path(__file__).parent.parent / "alpha" / "output" / "approved_rules.json"
_dynamic_family_mechanism_map: dict[str, str] = {}
_dynamic_family_map_mtimes: tuple[float, float] = (-1.0, -1.0)


@dataclass
class DecayCondition:
    """单个衰竭条件。"""

    feature: str
    op: str
    threshold: float | tuple[float, float] | None
    description: str


@dataclass
class MechanismConfig:
    """
    机制生命周期配置（四层结构）。

    第一层: category — 大类，按力的物理本质归组
    第二层: physics — 三个核心问题（本质/短暂原因/边缘来源）+ entry_fingerprint
    第三层: relations — 力与力之间的关系（增强/冲突/常见时序）
    第四层: validated_by — 哪些已验证策略是这个力的实证来源

    primary / confirms / description 是原有运行时衰竭判断字段，保持不变。
    """

    mechanism_type: str
    primary: DecayCondition
    confirms: list[DecayCondition]
    description: str
    # ── 四层扩展字段（带默认值，向后完全兼容）────────────────────────────────
    category: str = "generic"
    display_name: str = ""
    physics: dict = field(default_factory=dict)
    entry_fingerprint: list = field(default_factory=list)
    validated_by: list = field(default_factory=list)
    relations: dict = field(default_factory=dict)
    llm_confidence: float = 0.0
    last_reviewed: str = ""


@dataclass
class DecayResult:
    """机制衰竭评估结果。"""

    decay_score: float
    primary_fired: bool
    confirms_fired: list[str]
    recommended_action: str
    reason: str


def _safe_get(source, key, default=None):
    """从 pd.Series 或 dict 里取值，并把 NaN 视为缺失。"""
    if isinstance(source, dict):
        val = source.get(key, default)
    elif hasattr(source, "index") and key in source.index:
        val = source[key]
    else:
        return default

    if val is None:
        return default

    try:
        if pd.isna(val):
            return default
    except Exception:
        pass

    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _neutral_value_for_feature(feature: str) -> float:
    """返回某个特征的中性参考点。"""
    if feature in {"taker_buy_sell_ratio", "volume_vs_ma20", "spread_vs_ma20"}:
        return 1.0
    if feature in {"vwap_deviation", "funding_rate", "volume_acceleration"}:
        return 0.0
    if feature.startswith("position_in_range_"):
        return 0.5
    return 0.0


def _resolve_entry_feature(entry_snapshot: dict) -> tuple[str, float | None]:
    """解析 generic alpha 用的入场特征名和入场值。"""
    feature = str(
        entry_snapshot.get("entry_feature")
        or entry_snapshot.get("feature")
        or ""
    ).strip()

    value = _safe_get(entry_snapshot, "entry_feature_value")
    if value is None:
        value = _safe_get(entry_snapshot, "feature_value")
    if value is None and feature:
        value = _safe_get(entry_snapshot, feature)
    return feature, value


def _load_dynamic_family_mechanism_map() -> dict[str, str]:
    """Load approved/live Alpha family -> mechanism mappings with mtime caching."""
    global _dynamic_family_mechanism_map, _dynamic_family_map_mtimes

    current_mtimes: list[float] = []
    for path in (_FORCE_REGISTRY_PATH, _APPROVED_RULES_PATH):
        try:
            current_mtimes.append(path.stat().st_mtime)
        except OSError:
            current_mtimes.append(-1.0)
    mtime_key = tuple(current_mtimes)
    if mtime_key == _dynamic_family_map_mtimes:
        return _dynamic_family_mechanism_map

    mapping: dict[str, str] = {}

    try:
        payload = json.loads(_FORCE_REGISTRY_PATH.read_text(encoding="utf-8"))
        for force_payload in payload.get("forces", {}).values():
            strategies = force_payload.get("strategies", {}) if isinstance(force_payload, dict) else {}
            if not isinstance(strategies, dict):
                continue
            for strategy_payload in strategies.values():
                if not isinstance(strategy_payload, dict):
                    continue
                family = str(strategy_payload.get("family") or "").strip()
                mechanism_type = str(strategy_payload.get("mechanism_type") or "").strip()
                if family and mechanism_type:
                    mapping[family] = mechanism_type
    except Exception:
        pass

    try:
        approved_payload = json.loads(_APPROVED_RULES_PATH.read_text(encoding="utf-8"))
        if isinstance(approved_payload, list):
            for card in approved_payload:
                if not isinstance(card, dict):
                    continue
                family = str(card.get("family") or "").strip()
                mechanism_type = str(card.get("mechanism_type") or "").strip()
                if family and mechanism_type:
                    mapping[family] = mechanism_type
    except Exception:
        pass

    _dynamic_family_mechanism_map = mapping
    _dynamic_family_map_mtimes = mtime_key
    return _dynamic_family_mechanism_map


# ── 大类描述词典 ─────────────────────────────────────────────────────────────
MECHANISM_CATEGORIES: dict[str, str] = {
    "leverage_cost_imbalance": (
        "杠杆成本失衡 — 多空双方持仓成本不对称，成本高的一方被迫平仓或离场"
    ),
    "liquidity_vacuum": (
        "流动性真空 — 价格偏离后支撑它的成交量消失，回归均衡是必然"
    ),
    "unilateral_exhaustion": (
        "单边力量耗尽 — 一方主动攻击弹药打完，对手方开始反攻"
    ),
    "algorithmic_trace": (
        "算法执行痕迹 — 大资金通过算法拆单，留下可识别的节律和均匀特征"
    ),
    "potential_energy_release": (
        "势能积累释放 — 价格在极值附近长时间压缩积累动能，突破时定向释放"
    ),
    "distribution_pattern": (
        "高位分发形态 — 价格虚高但无新买方入场，聪明钱在悄悄退出"
    ),
    "open_interest_divergence": (
        "持仓量背离 — 价格与持仓量走势分叉，代表资金方向而非真实共识"
    ),
    "inventory_rebalance": (
        "做市商库存再平衡 — 单向流量偏斜后做市商被迫回补，产生可预测的反向压力"
    ),
    "regime_change": (
        "市场状态转换 — 振幅/成交量/价差同时结构性变化的可识别制度切换"
    ),
    "generic": (
        "通用规律 — 尚未映射到已知物理机制，需谨慎使用并补充物理因果验证"
    ),
}


SIGNAL_MECHANISM_MAP: dict[str, str] = {
    # ── P1 系列 live 策略（按 detector.name 精确匹配）──────────────────────
    "P0-2_funding_rate": "funding_settlement",
    "P0-2_资金费率套利": "funding_settlement",
    "P1-1_market_maker": "mm_rebalance",
    "P1-2_vwap_twap": "algo_slicing",
    "P1-2_VWAP/TWAP拆单": "algo_slicing",
    "P1-3_volume_climax": "volume_climax_reversal",
    "P1-4_taker_exhaustion": "taker_snap_reversal",
    "P1-5_amplitude_reversal": "amplitude_absorption",
    "P1-6_bottom_volume_drought": "seller_drought",
    "P1-8_vwap_vol_drought": "vwap_reversion",
    "P1-9_position_compression": "compression_release",
    "P1-10_taker_exhaustion_low": "bottom_taker_exhaust",
    "P1-11_high_pos_funding": "funding_divergence",
    "C1_funding_cycle_oversold_long": "funding_cycle_oversold",
    # ── Alpha 卡片（按 family 前缀匹配）─────────────────────────────────────
    # 原 generic_alpha → 修正为具名机制
    "A2-26": "near_high_distribution",
    "A2-29": "near_high_distribution",
    "A3-OI": "oi_divergence",
    "A4-PIR": "oi_divergence",
    # ── 制度转换策略 ──────────────────────────────────────────────────────────
    "RT-1": "regime_transition",
    "RT-1_regime_transition_long": "regime_transition",
    "OA-1": "oi_accumulation_long",
    "OA-1_oi_accumulation_long": "oi_accumulation_long",
}


def resolve_mechanism_type(signal_name: str, direction: str = "", family: str = "") -> str:
    """把信号名解析成机制类型，先 family 匹配，再 signal_name 匹配。

    Alpha 卡片的 signal_name 是时间戳格式（如 20260330_053917_dist_to__spread_v），
    但 SIGNAL_MECHANISM_MAP 用 family 名（如 A2-29）。所以 family 优先查。
    P1 系列的 signal_name 直接匹配（如 P1-8_vwap_vol_drought）。
    """
    # P1-10 SHORT 特判
    if signal_name.startswith("P1-10") and str(direction).lower() == "short":
        return "top_buyer_exhaust"

    # 优先用 family 查（Alpha 卡片的正确路径）
    if family:
        mechanism = SIGNAL_MECHANISM_MAP.get(family, "")
        if mechanism:
            return mechanism
        for key, mech in SIGNAL_MECHANISM_MAP.items():
            if family.startswith(key) or key.startswith(family):
                return mech
        dynamic_mechanism = _load_dynamic_family_mechanism_map().get(family, "")
        if dynamic_mechanism:
            return dynamic_mechanism

    # signal_name 也尝试动态映射（A5-xxx 直接以 signal_name 形式传入时）
    dynamic_by_sname = _load_dynamic_family_mechanism_map().get(signal_name, "")
    if dynamic_by_sname:
        return dynamic_by_sname

    # signal_name 精确匹配（P1 系列走这条路径）
    mechanism = SIGNAL_MECHANISM_MAP.get(signal_name, "")
    if mechanism:
        return mechanism

    # 前缀匹配（兜底：信号名可能是 "P1-8_vwap_vol_drought|variant_A" 等变体）
    for key, mech in SIGNAL_MECHANISM_MAP.items():
        if signal_name.startswith(key) or key.startswith(signal_name):
            return mech

    # 最后一步: 从力注册表 force_registry.json 里查 mechanism_type
    # 这样 Alpha 管道新晋升的策略不会永远卡在 generic_alpha 衰竭上限 0.4
    try:
        import json as _json
        _fr_path = Path(__file__).parent.parent / "alpha" / "output" / "force_registry.json"
        if _fr_path.exists():
            _fr = _json.loads(_fr_path.read_text(encoding="utf-8"))
            for _force_cat, _cat_entry in _fr.get("forces", {}).items():
                _strat = _cat_entry.get("strategies", {})
                for _fam, _info in _strat.items():
                    if family and (family == _fam or family.startswith(_fam) or _fam.startswith(family)):
                        mtype = str(_info.get("mechanism_type") or "")
                        if mtype and mtype not in ("generic_alpha", "generic", ""):
                            logger.debug(
                                "[MECHANISM] force_registry lookup: %s -> %s", family, mtype,
                            )
                            return mtype
    except Exception as _exc:
        logger.debug("[MECHANISM] force_registry lookup failed: %s", _exc)

    logger.warning(
        "[MECHANISM] No mapping for signal=%s family=%s, falling back to generic_alpha",
        signal_name, family,
    )
    return "generic_alpha"


MECHANISM_CATALOG: dict[str, MechanismConfig] = {
    # ══════════════════════════════════════════════════════════════════════════
    # 大类：leverage_cost_imbalance — 杠杆成本失衡
    # ══════════════════════════════════════════════════════════════════════════
    "funding_settlement": MechanismConfig(
        mechanism_type="funding_settlement",
        primary=DecayCondition(
            feature="minutes_to_funding",
            op="revert_to_neutral",
            threshold=None,
            description="资金费率结算窗口走完并重新进入下一轮周期",
        ),
        confirms=[
            DecayCondition(
                feature="taker_buy_sell_ratio",
                op="between",
                threshold=(0.45, 0.55),
                description="主动买卖重新回到中性",
            ),
            DecayCondition(
                feature="funding_rate",
                op="between",
                threshold=(-0.00005, 0.00005),
                description="资金费率几乎回到零轴",
            ),
        ],
        description="围绕资金费率结算窗口的价差扭曲与回补",
        category="leverage_cost_imbalance",
        display_name="资金结算窗口套利",
        physics={
            "essence": (
                "资金费率每8小时结算一次。结算前多空双方有强烈的成本动机平仓或对冲，"
                "造成方向性价格压力。这是合约交易所的机制设计直接导致的可预测行为。"
            ),
            "why_temporary": (
                "结算事件是一次性的：结算完成后费率归零，压力释放，价格回归均衡。"
                "下一轮结算前30分钟才会重新积累。"
            ),
            "edge_source": "FUNDING_COST — 资金结算时间表是公开且固定的，创造了可预测的时间窗口",
        },
        entry_fingerprint=[
            {"feature": "minutes_to_funding", "condition": "< 30", "why": "进入结算窗口"},
            {"feature": "funding_rate", "condition": "extreme (> 0.01% or < -0.01%)", "why": "方向性成本压力足够大"},
            {"feature": "taker_buy_sell_ratio", "condition": "偏向与费率方向一致", "why": "市场参与者已经在行动"},
        ],
        validated_by=["P0-2"],
        relations={
            "reinforces": ["funding_cycle_oversold", "funding_divergence"],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),
    "funding_divergence": MechanismConfig(
        mechanism_type="funding_divergence",
        primary=DecayCondition(
            feature="funding_rate",
            op=">",
            threshold=0.0,
            description="原本偏负的资金费率重新翻正，背离结束",
        ),
        confirms=[
            DecayCondition(
                feature="position_in_range_4h",
                op="<",
                threshold=0.8,
                description="价格离开 4 小时极端区域",
            ),
            DecayCondition(
                feature="oi_change_rate_5m",
                op="<",
                threshold=-0.002,
                description="持仓流出，挤压结构松动",
            ),
        ],
        description="高位负资金费率背离一旦修复，做空因果基础就明显减弱",
        category="leverage_cost_imbalance",
        display_name="资金费率-持仓背离",
        physics={
            "essence": (
                "价格处于高位，但资金费率为负（空方付多方）——多头在高位托举，"
                "空方在成本递增的情况下仍不愿离场。这是一个内部矛盾的杠杆结构。"
            ),
            "why_temporary": (
                "空方持仓成本随时间递增。价格要么因多头离场回落，要么空方被成本压力"
                "逼平仓，两种结局都导致结构瓦解。矛盾无法长期持续。"
            ),
            "edge_source": "FUNDING_COST — 杠杆成本不对称是可测量的持续压力，不依赖方向预测",
        },
        entry_fingerprint=[
            {"feature": "position_in_range_4h", "condition": "> 0.95", "why": "价格在4小时高位，多头结构明显"},
            {"feature": "funding_rate", "condition": "< -0.00003", "why": "资金费率为负，空方在高位付费，背离明显"},
        ],
        validated_by=["P1-11"],
        relations={
            "reinforces": ["near_high_distribution"],
            "conflicts_with": ["funding_cycle_oversold"],
            "often_follows": ["compression_release"],
        },
    ),
    "funding_cycle_oversold": MechanismConfig(
        mechanism_type="funding_cycle_oversold",
        primary=DecayCondition(
            feature="funding_rate",
            op=">",
            threshold=0.0,
            description="负资金费率窗口已经修复到正值",
        ),
        confirms=[
            DecayCondition(
                feature="position_in_range_24h",
                op=">",
                threshold=0.5,
                description="价格已经回到日内区间中上部",
            ),
            DecayCondition(
                feature="taker_buy_sell_ratio",
                op="<",
                threshold=0.8,
                description="新的卖压开始出现",
            ),
        ],
        description="资金周期造成的超卖反弹，随着费率修复和价格回升而自然衰竭",
        category="leverage_cost_imbalance",
        display_name="资金周期超卖反弹",
        physics={
            "essence": (
                "资金费率持续为负时，空方持续付费给多方，空方成本递增。"
                "当价格同时在低位（超卖），空方被迫平仓的概率极高，做多具备均值回归优势。"
            ),
            "why_temporary": (
                "随着价格上涨，费率自然向正值修复，空方成本压力消失。"
                "超卖本身也会随价格回归而解除。双重压力消失后反弹动力耗尽。"
            ),
            "edge_source": "FUNDING_COST — 空方累积成本是可计算的压力，超卖只是触发时机",
        },
        entry_fingerprint=[
            {"feature": "funding_rate", "condition": "< 0 (负值)", "why": "空方在付费，成本压力存在"},
            {"feature": "position_in_range_24h", "condition": "< 0.15", "why": "价格在日内区间低位，超卖叠加"},
            {"feature": "vwap_deviation", "condition": "< -0.003", "why": "价格偏离VWAP下方，确认超卖"},
        ],
        validated_by=["C1"],
        relations={
            "reinforces": ["seller_drought", "bottom_taker_exhaust"],
            "conflicts_with": ["funding_divergence"],
            "often_follows": [],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：liquidity_vacuum — 流动性真空
    # ══════════════════════════════════════════════════════════════════════════
    "seller_drought": MechanismConfig(
        mechanism_type="seller_drought",
        primary=DecayCondition(
            feature="volume_vs_ma20+taker_buy_sell_ratio",
            op="revert_to_neutral",
            threshold=None,
            description="成交量回来了，而且回来的主要是卖压",
        ),
        confirms=[
            DecayCondition(
                feature="dist_to_24h_low",
                op="<",
                threshold=None,
                description="价格重新向 24h 低点下沉，底部干涸不再成立",
            ),
            DecayCondition(
                feature="oi_change_rate_5m",
                op="<",
                threshold=-0.003,
                description="持仓明显流失，原本的支撑结构在松动",
            ),
        ],
        description="卖盘枯竭带来的下跌衰减，一旦卖量回归就意味着机制衰竭",
        category="liquidity_vacuum",
        display_name="底部卖方枯竭",
        physics={
            "essence": (
                "价格跌至近24小时低点附近，但成交量极度萎缩——卖方弹药已经耗尽，"
                "没有新的卖压来源。此时任何买入都能推动价格，反弹是最小阻力方向。"
            ),
            "why_temporary": (
                "卖方干涸是暂时的：价格上涨后套牢盘解套会带来新的卖压，"
                "或者外部消息可以重新激活卖方。成交量回归是机制消亡的最直接信号。"
            ),
            "edge_source": "FLOW_EXHAUSTION — 成交量干涸是可直接测量的卖方弹药状态",
        },
        entry_fingerprint=[
            {"feature": "dist_to_24h_low", "condition": "< 0.0011", "why": "价格贴近24小时低点"},
            {"feature": "volume_vs_ma20", "condition": "< 0.25", "why": "成交量极度萎缩，卖方力量消失"},
        ],
        validated_by=["P1-6"],
        relations={
            "reinforces": ["bottom_taker_exhaust", "funding_cycle_oversold"],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),
    "vwap_reversion": MechanismConfig(
        mechanism_type="vwap_reversion",
        primary=DecayCondition(
            feature="vwap_deviation",
            op="revert_to_neutral",
            threshold=None,
            description="价格相对 VWAP 的偏离已经回补过半",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op=">",
                threshold=1.5,
                description="成交重新放大，均值回归过程完成得更充分",
            ),
            DecayCondition(
                feature="position_in_range_24h",
                op="between",
                threshold=(0.3, 0.7),
                description="价格回到日内区间中部",
            ),
        ],
        description="价格偏离 VWAP 后向均衡位置回归，偏离越收敛，机制越接近完成",
        category="liquidity_vacuum",
        display_name="VWAP均值回归",
        physics={
            "essence": (
                "VWAP是当日所有成交的量加权均价，代表市场的公允价值重心。"
                "价格大幅偏离VWAP且成交量同步萎缩，说明偏离是在无支撑下发生的——"
                "没有新资金愿意在这个价位成交，回归是必然。"
            ),
            "why_temporary": (
                "成交量加权均价是一种引力：越偏离越有回归压力。"
                "当偏离超过2%且成交量枯竭，说明价格是被少量市价单推出去的，"
                "真实的买卖双方都还在VWAP附近等待。"
            ),
            "edge_source": "FLOW_EXHAUSTION + MARKET_MAKER — 无量偏离是流动性真空，做市商会推价格回来",
        },
        entry_fingerprint=[
            {"feature": "vwap_deviation", "condition": "> 2% or < -2.4%", "why": "价格偏离VWAP超过极端阈值"},
            {"feature": "volume_vs_ma20", "condition": "连续3-4个区间 < 0.3", "why": "持续无量，偏离无支撑"},
        ],
        validated_by=["P1-8"],
        relations={
            "reinforces": ["seller_drought", "compression_release"],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),
    "mm_rebalance": MechanismConfig(
        mechanism_type="mm_rebalance",
        primary=DecayCondition(
            feature="taker_buy_sell_ratio",
            op="between",
            threshold=(0.8, 1.2),
            description="做市商再平衡完成，主动方向回归常态",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.0,
                description="额外流量消退",
            ),
            DecayCondition(
                feature="spread_vs_ma20",
                op="<",
                threshold=1.2,
                description="点差回到常规水平",
            ),
        ],
        description="做市商被动偏仓后回补库存，随后重新均衡",
        category="inventory_rebalance",
        display_name="做市商库存再平衡",
        physics={
            "essence": (
                "做市商被动承接大量单边订单后，库存严重偏向一侧。"
                "为了控制风险，他们必须通过主动交易来恢复平衡——"
                "这种必然的再平衡行为创造了方向性压力。"
            ),
            "why_temporary": (
                "库存再平衡是一次性的：平衡恢复后，做市商重新中性报价，"
                "方向性压力消失。点差收窄和吃单比回归中性是最直接的信号。"
            ),
            "edge_source": "MARKET_MAKER — 做市商有义务维持双边报价，偏仓必须修复",
        },
        entry_fingerprint=[
            {"feature": "taker_buy_sell_ratio", "condition": "extreme (< 0.5 or > 2.0)", "why": "做市商被逼到极端单边"},
            {"feature": "spread_vs_ma20", "condition": "> 1.5", "why": "做市商扩大报价保护自己，偏仓严重"},
        ],
        validated_by=["P1-1"],
        relations={
            "reinforces": [],
            "conflicts_with": [],
            "often_follows": ["taker_snap_reversal"],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：unilateral_exhaustion — 单边力量耗尽
    # ══════════════════════════════════════════════════════════════════════════
    "bottom_taker_exhaust": MechanismConfig(
        mechanism_type="bottom_taker_exhaust",
        primary=DecayCondition(
            feature="taker_buy_sell_ratio",
            op=">",
            threshold=0.5,
            description="极端卖盘已经不再极端，主动卖出重新恢复",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20+taker_buy_sell_ratio",
                op="revert_to_neutral",
                threshold=None,
                description="有明显成交回流，而且回流里带着卖压",
            ),
            DecayCondition(
                feature="oi_change_rate_5m",
                op="<",
                threshold=-0.002,
                description="持仓回落，承接反弹的力量在减弱",
            ),
        ],
        description="底部主动卖出耗尽后产生的反抽，一旦卖压重新回归就该防守",
        category="unilateral_exhaustion",
        display_name="底部吃单卖方耗尽",
        physics={
            "essence": (
                "价格在低位时，主动卖出（吃单）占比极低——卖方弹药几乎耗尽。"
                "极端的吃单比不对称（卖方主导但比例极低）说明最后一批卖家在离场，"
                "之后任何中性的买卖力量都能推价格上涨。"
            ),
            "why_temporary": (
                "卖方耗尽是一种暂时性的流量状态。随着价格上涨，"
                "解套盘和空头对冲会带来新的卖压，耗尽状态自然结束。"
            ),
            "edge_source": "FLOW_EXHAUSTION — 极端吃单比是杠杆卖方弹药状态的直接测量",
        },
        entry_fingerprint=[
            {"feature": "dist_to_24h_low", "condition": "< 0.0011", "why": "价格在24小时低位"},
            {"feature": "taker_buy_sell_ratio", "condition": "< 0.21", "why": "主动卖出比例极端，卖方已到极限"},
        ],
        validated_by=["P1-10"],
        relations={
            "reinforces": ["seller_drought", "funding_cycle_oversold"],
            "conflicts_with": ["top_buyer_exhaust"],
            "often_follows": [],
        },
    ),
    "top_buyer_exhaust": MechanismConfig(
        mechanism_type="top_buyer_exhaust",
        primary=DecayCondition(
            feature="vwap_deviation",
            op="revert_to_neutral",
            threshold=None,
            description="价格偏离已经向均值回补过半",
        ),
        confirms=[
            DecayCondition(
                feature="taker_buy_sell_ratio",
                op=">",
                threshold=0.5,
                description="主动买盘重新回流，高位耗尽结构开始失效",
            ),
        ],
        description="顶部追涨买盘耗尽后形成的回落，回补越充分越接近退出窗口",
        category="unilateral_exhaustion",
        display_name="顶部追涨买方耗尽",
        physics={
            "essence": (
                "价格在高位时，5分钟内吃单买入量急剧减少——追涨买方弹药耗尽。"
                "没有新的买单支撑的高位会因为重力（正常套利卖压）而回落。"
            ),
            "why_temporary": (
                "高位买方耗尽后，价格下跌会吸引新的买方承接。"
                "当VWAP偏离回补过半，说明这次下跌已经完成了主要修复。"
            ),
            "edge_source": "FLOW_EXHAUSTION — 高位吃单量变化率是买方动能衰减的直接测量",
        },
        entry_fingerprint=[
            {"feature": "vwap_deviation", "condition": "> 2%", "why": "价格高于VWAP，买方在推价"},
            {"feature": "taker_ratio_delta5", "condition": "< -1.09", "why": "5分钟内吃单买入量急剧减少"},
        ],
        validated_by=["P1-10"],
        relations={
            "reinforces": ["near_high_distribution", "vwap_reversion"],
            "conflicts_with": ["bottom_taker_exhaust"],
            "often_follows": [],
        },
    ),
    "taker_snap_reversal": MechanismConfig(
        mechanism_type="taker_snap_reversal",
        primary=DecayCondition(
            feature="taker_buy_sell_ratio",
            op="between",
            threshold=(0.4, 0.6),
            description="主动买卖失衡已经明显收敛",
        ),
        confirms=[
            DecayCondition(
                feature="volume_acceleration",
                op="<",
                threshold=0.0,
                description="成交爆发继续减速",
            ),
            DecayCondition(
                feature="amplitude_1m",
                op="<",
                threshold=None,
                description="瞬时振幅回落到入场前的常态振幅",
            ),
        ],
        description="瞬时 taker 冲击被市场吸收，失衡状态快速回归",
        category="unilateral_exhaustion",
        display_name="瞬时吃单冲击反转",
        physics={
            "essence": (
                "短时间内极端的主动买入或卖出冲击，快速耗尽了同方向的后续弹药。"
                "市场流动性深度有限，极端方向的吃单消耗完挂单后，对手方反弹是必然的。"
            ),
            "why_temporary": (
                "吃单冲击是短暂的——弹药耗尽后方向性压力消失，"
                "价格自然向中性回归。吃单比收敛是机制完成的直接信号。"
            ),
            "edge_source": "FLOW_EXHAUSTION — 极端吃单比是流量冲击强度的直接测量",
        },
        entry_fingerprint=[
            {"feature": "taker_buy_sell_ratio", "condition": "extreme percentile (> p80 or < p20)", "why": "主动方向极端失衡"},
            {"feature": "volume_acceleration", "condition": "< 0 (加速度为负)", "why": "冲击力量开始衰减"},
        ],
        validated_by=["P1-4"],
        relations={
            "reinforces": [],
            "conflicts_with": [],
            "often_follows": ["mm_rebalance"],
        },
    ),
    "seller_impulse": MechanismConfig(
        mechanism_type="seller_impulse",
        primary=DecayCondition(
            feature="taker_buy_sell_ratio",
            op=">",
            threshold=1.0,
            description="主动卖压已经不再主导，买卖比重新回到买方一侧",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.2,
                description="放量卖压开始退潮，成交回到常态附近",
            ),
            DecayCondition(
                feature="spread_vs_ma20",
                op="<",
                threshold=1.2,
                description="点差重新收敛，恐慌式砸盘阶段结束",
            ),
            DecayCondition(
                feature="volume_acceleration",
                op=">",
                threshold=0.0,
                description="成交不再继续向下压榨式放大，卖压冲击开始衰减",
            ),
        ],
        description="主动卖盘突然集中涌出，靠吃掉流动性推动价格下压；当卖压收敛、点差回落，短线冲击也就走完",
        category="unilateral_exhaustion",
        display_name="主动卖盘冲击",
        physics={
            "essence": (
                "大量主动卖单集中在短时间内涌出，通过吃掉买方挂单强行压低价格。"
                "点差扩大是做市商在保护自己：他们知道自己在被单边流量利用。"
            ),
            "why_temporary": (
                "大规模主动卖出是一次性事件：卖方弹药有限，"
                "当卖压减弱、点差收窄，说明冲击完成，价格回归。"
            ),
            "edge_source": "FLOW_EXHAUSTION — 方向性吃单流量是直接可测的冲击强度",
        },
        entry_fingerprint=[
            {"feature": "taker_buy_sell_ratio", "condition": "极端低值（< p10）", "why": "卖方主导极端"},
            {"feature": "spread_vs_ma20", "condition": "> 1.5", "why": "做市商扩价差保护，冲击激烈"},
        ],
        validated_by=[],
        relations={
            "reinforces": ["bottom_taker_exhaust"],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),

    # buyer_impulse: LONG mirror of seller_impulse.
    # Physics: aggressive buyers flood the order book, eating through asks.
    # When buying pressure fades (taker ratio drops, sell share rises), exit.
    "buyer_impulse": MechanismConfig(
        mechanism_type="buyer_impulse",
        primary=DecayCondition(
            feature="taker_buy_sell_ratio",
            op="<",
            threshold=1.0,
            description="主动买压已消退，买卖比重新回到卖方一侧",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.2,
                description="放量买入开始退潮，成交回到常态附近",
            ),
            DecayCondition(
                feature="spread_vs_ma20",
                op="<",
                threshold=1.2,
                description="点差重新收敛，FOMO式追涨阶段结束",
            ),
            DecayCondition(
                feature="direction_net_1m",
                op="<",
                threshold=0.0,
                description="成交方向转为卖方主导，买压冲击结束",
            ),
        ],
        description="主动买盘突然集中涌出，靠吃掉卖方挂单推动价格上涨；当买压收敛、点差回落，短线冲击也就走完",
        category="unilateral_exhaustion",
        display_name="主动买盘冲击",
        physics={
            "essence": (
                "大量主动买单集中在短时间内涌出，通过吃掉卖方挂单强行推高价格。"
                "点差扩大是做市商在保护自己：他们知道自己在被单边流量利用。"
            ),
            "why_temporary": (
                "大规模主动买入是一次性事件：买方弹药有限，"
                "当买压减弱、点差收窄，说明冲击完成，价格回调。"
            ),
            "edge_source": "FLOW_EXHAUSTION — 方向性吃单流量是直接可测的冲击强度",
        },
        entry_fingerprint=[
            {"feature": "taker_buy_sell_ratio", "condition": "极端高值（> p90）", "why": "买方主导极端"},
            {"feature": "spread_vs_ma20", "condition": "> 1.5", "why": "做市商扩价差保护，冲击激烈"},
        ],
        validated_by=[],
        relations={
            "reinforces": ["bottom_taker_exhaust"],  # both are LONG forces
            "conflicts_with": ["seller_impulse", "top_buyer_exhaust"],  # opposing forces
            "often_follows": ["bottom_taker_exhaust"],  # buyer impulse can follow seller exhaustion
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：algorithmic_trace — 算法执行痕迹
    # ══════════════════════════════════════════════════════════════════════════
    "algo_slicing": MechanismConfig(
        mechanism_type="algo_slicing",
        primary=DecayCondition(
            feature="volume_autocorr_lag5",
            op="<",
            threshold=0.3,
            description="算法拆单的节拍感消失",
        ),
        confirms=[
            DecayCondition(
                feature="avg_trade_size_cv_10m",
                op=">",
                threshold=0.5,
                description="成交尺寸不再整齐划一",
            ),
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.0,
                description="放量回落到日常水平",
            ),
        ],
        description="有节奏的小单拆分停止，推进力量耗尽",
        category="algorithmic_trace",
        display_name="机构VWAP/TWAP算法执行",
        physics={
            "essence": (
                "大型机构用VWAP/TWAP算法分批建仓时，会在每个固定时间节点放量，"
                "成交额高度均匀，成交量自相关性强——这是算法控制执行节奏留下的痕迹。"
                "跟随机构的建仓方向有明显的正期望。"
            ),
            "why_temporary": (
                "算法执行是有时限的：订单完成后节律消失，成交量回归随机，"
                "推动力量耗尽。成交自相关降低是直接信号。"
            ),
            "edge_source": "ALGO_EXECUTION — 算法执行的节律是可识别的微结构痕迹，非随机噪音",
        },
        entry_fingerprint=[
            {"feature": "volume_autocorr_lag5", "condition": "> 0.55", "why": "成交量节律强，算法在执行"},
            {"feature": "avg_trade_size_cv_10m", "condition": "< 0.30", "why": "单笔成交额均匀，TWAP特征明显"},
            {"feature": "minute_in_hour", "condition": "= 0/15/30/45", "why": "整刻钟放量，VWAP时间节点"},
        ],
        validated_by=["P1-2"],
        relations={
            "reinforces": [],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：potential_energy_release — 势能积累释放
    # ══════════════════════════════════════════════════════════════════════════
    "compression_release": MechanismConfig(
        mechanism_type="compression_release",
        primary=DecayCondition(
            feature="amplitude_1m",
            op="<",
            threshold=None,
            description="压缩后的能量已经释放完，波动恢复到日常背景",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.0,
                description="跟随机会的成交开始退潮",
            ),
            DecayCondition(
                feature="spread_vs_ma20",
                op="<",
                threshold=1.5,
                description="微观结构恢复平稳",
            ),
        ],
        description="仓位压缩带来的释放行情走完后，市场会重新冷却",
        category="potential_energy_release",
        display_name="区间压缩势能释放",
        physics={
            "essence": (
                "价格在极值位置长时间窄幅震荡（压缩），说明多空双方在积累仓位，"
                "做市商在收集对手盘。压缩越久，积累的定向势能越大，突破后动量越强。"
            ),
            "why_temporary": (
                "压缩释放是一次性的：势能释放完后，波动回归日常背景，"
                "单分钟振幅回到入场前的均值水平就是释放完成的信号。"
            ),
            "edge_source": "OI_STRUCTURE — 区间压缩是做市商收集筹码的可观测行为",
        },
        entry_fingerprint=[
            {"feature": "position_in_range_24h", "condition": "> 0.93 or < 0.06", "why": "价格在极端区域"},
            {"feature": "连续N个区间振幅收窄", "condition": ">= 6-8个压缩区间", "why": "势能积累时间足够"},
        ],
        validated_by=["P1-9"],
        relations={
            "reinforces": ["vwap_reversion"],
            "conflicts_with": [],
            "often_follows": ["near_high_distribution"],
        },
    ),
    "volume_climax_reversal": MechanismConfig(
        mechanism_type="volume_climax_reversal",
        primary=DecayCondition(
            feature="volume_acceleration",
            op=">",
            threshold=0.0,
            description="成交加速度重新抬头，原先的衰减结束",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.5,
                description="极端放量不再持续",
            ),
            DecayCondition(
                feature="taker_buy_sell_ratio",
                op="revert_to_neutral",
                threshold=None,
                description="主动方向重新穿回中性轴",
            ),
        ],
        description="情绪冲顶或砸盘见底后的反抽/反杀动力被消化",
        category="potential_energy_release",
        display_name="成交量高潮反转",
        physics={
            "essence": (
                "极端成交量峰值通常标志着单边力量的彻底耗尽——最后的追涨者或恐慌者"
                "在高潮时入场，之后没有后续资金接棒，价格必然反向。"
            ),
            "why_temporary": (
                "成交量高潮是情绪爆发点，之后成交加速度变负（量减速），"
                "说明冲击力量在衰减。当加速度重新转正，说明新的力量在进场，"
                "原来的反转动力耗尽。"
            ),
            "edge_source": "VOLATILITY_SHOCK — 成交量高潮是市场情绪极值的可测量标志",
        },
        entry_fingerprint=[
            {"feature": "volume_vs_ma20", "condition": "> p90 (极端放量)", "why": "成交量高潮"},
            {"feature": "volume_acceleration", "condition": "< 0 (开始减速)", "why": "冲击力量衰减"},
        ],
        validated_by=["P1-3"],
        relations={
            "reinforces": ["taker_snap_reversal"],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),
    "amplitude_absorption": MechanismConfig(
        mechanism_type="amplitude_absorption",
        primary=DecayCondition(
            feature="amplitude_1m",
            op="<",
            threshold=None,
            description="单分钟振幅已经收回到背景波动附近",
        ),
        confirms=[
            DecayCondition(
                feature="spread_vs_ma20",
                op="<",
                threshold=1.5,
                description="点差重新变窄，吸收过程完成",
            ),
        ],
        description="大振幅被动承接后，市场重新恢复可交易的平衡形态",
        category="potential_energy_release",
        display_name="极端振幅被动吸收",
        physics={
            "essence": (
                "单分钟极端振幅后，收盘位置反映哪方被吸收：在底部收盘说明卖方被吸收，"
                "买方占优；在顶部收盘说明买方被吸收。被吸收方的力量已经耗尽。"
            ),
            "why_temporary": (
                "极端振幅后市场需要重新寻找平衡。振幅回落到均值水平，"
                "点差收窄，说明平衡已经恢复，信号的优势窗口关闭。"
            ),
            "edge_source": "VOLATILITY_SHOCK — 极端振幅+收盘位置是吸收方向的组合信号",
        },
        entry_fingerprint=[
            {"feature": "amplitude_1m", "condition": "> p90 (极端振幅)", "why": "发生了极端的单向冲击"},
            {"feature": "收盘位置", "condition": "< 35% (底部) or > 65% (顶部)", "why": "确认哪方被吸收"},
        ],
        validated_by=["P1-5"],
        relations={
            "reinforces": ["volume_climax_reversal"],
            "conflicts_with": [],
            "often_follows": [],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：distribution_pattern — 高位分发形态（新增）
    # ══════════════════════════════════════════════════════════════════════════
    "near_high_distribution": MechanismConfig(
        mechanism_type="near_high_distribution",
        primary=DecayCondition(
            feature="dist_to_24h_high",
            op="<",
            threshold=-0.015,
            description="价格已经跌离24小时高点超过1.5%，分发完成，机制结束",
        ),
        confirms=[
            DecayCondition(
                feature="spread_vs_ma20",
                op="<",
                threshold=1.2,
                description="价差恢复正常，做市商重新入场，流动性恢复",
            ),
            DecayCondition(
                feature="oi_change_rate_5m",
                op=">",
                threshold=0.0001,
                description="持仓量重新增长，新买方开始入场",
            ),
        ],
        description="价格贴近24小时高点但无新买方入场（OI停滞或价差扩大），聪明钱在悄悄退出",
        category="distribution_pattern",
        display_name="高位无买方分发",
        physics={
            "essence": (
                "价格虽然接近24小时高点，但两个信号之一出现：\n"
                "①持仓量停止增长甚至下降（A2-26）——没有新多头在这个高位建仓；\n"
                "②买卖价差扩大至均值1.7倍（A2-29）——做市商在撤退保护自己。\n"
                "两者都说明高位缺乏真实的买方支撑，价格是被少量资金推出来的虚高。"
            ),
            "why_temporary": (
                "虚高位无法持续：没有新买方接盘，持仓老多头会陆续获利离场，"
                "价格在重力下自然回落。跌离高位1.5%是分发完成的经验阈值。"
            ),
            "edge_source": "OI_STRUCTURE + MARKET_MAKER — OI停滞和价差扩大是聪明钱退出的可测量痕迹",
        },
        entry_fingerprint=[
            {"feature": "dist_to_24h_high", "condition": "> -0.0097", "why": "价格非常接近24小时高点（贴近高点）"},
            {
                "feature": "oi_change_rate_5m or spread_vs_ma20",
                "condition": "OI增速 < 0.0000145 (A2-26) 或 价差 > 1.688 (A2-29)",
                "why": "无新买方建仓支撑（A2-26），或做市商撤退（A2-29）",
            },
        ],
        validated_by=["A2-26", "A2-29"],
        relations={
            "reinforces": ["funding_divergence", "top_buyer_exhaust", "oi_divergence"],
            "conflicts_with": ["seller_drought", "bottom_taker_exhaust"],
            "often_follows": ["compression_release"],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：open_interest_divergence — 持仓量背离（新增）
    # ══════════════════════════════════════════════════════════════════════════
    "oi_divergence": MechanismConfig(
        mechanism_type="oi_divergence",
        primary=DecayCondition(
            feature="oi_change_rate_1h",
            op=">",
            threshold=0.005,
            description="OI 重新正增长 (>0.5%/h)：新资金流入，去杠杆/停滞论点失效",
            # was -0.003; that fires instantly for A4-PIR (enters at ~0.00007)
        ),
        confirms=[
            DecayCondition(
                feature="dist_to_24h_high",
                op="<",
                threshold=-0.01,
                description="价格已经从高位回落超过1%，背离已经兑现",
            ),
        ],
        description="价格接近24小时高点但1小时持仓量持续流出，聪明钱在悄悄减仓，价格无支撑",
        category="open_interest_divergence",
        display_name="价格-持仓量背离（顶部去杠杆）",
        physics={
            "essence": (
                "价格在高位，但持仓量（OI）在过去1小时持续下降（增速 < -1%/小时）。"
                "正常的高位应该是新多头持续建仓推价——OI上升才对。"
                "OI下降说明多头在获利了结或被迫减仓，价格是在存量筹码互搏中被推高的，"
                "没有真实新资金流入。"
            ),
            "why_temporary": (
                "持仓量下降是一个强烈的资金流出信号。OI持续流出的高位价格无法自持，"
                "因为支撑价格的杠杆多头在减少。当去杠杆压力消失（OI降速放缓），"
                "说明减仓接近尾声，价格要么已经修复，要么等待新的催化剂。"
            ),
            "edge_source": "OI_STRUCTURE — OI与价格的方向背离是资金意图的直接测量，非价格噪音",
        },
        entry_fingerprint=[
            {"feature": "dist_to_24h_high", "condition": "> -0.005", "why": "价格非常接近24小时高点"},
            {"feature": "oi_change_rate_1h", "condition": "< -0.01", "why": "过去1小时OI持续下降超1%，去杠杆在进行"},
        ],
        validated_by=["A3-OI"],
        relations={
            "reinforces": ["near_high_distribution", "funding_divergence"],
            "conflicts_with": ["seller_drought", "funding_cycle_oversold"],
            "often_follows": [],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 大类：regime_change — 市场状态转换（新增）
    # ══════════════════════════════════════════════════════════════════════════
    "regime_transition": MechanismConfig(
        mechanism_type="regime_transition",
        primary=DecayCondition(
            feature="amplitude_ma20",
            op=">",
            threshold=None,
            description="20根K线均振幅扩大到入场时的1.5倍以上，安静趋势状态结束",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op=">",
                threshold=1.5,
                description="成交量重新放大，市场状态已经不是安静趋势",
            ),
            DecayCondition(
                feature="spread_vs_ma20",
                op=">",
                threshold=1.3,
                description="价差扩大，波动率在上升，制度特征改变",
            ),
        ],
        description="市场从区间震荡切换到安静趋势，早期趋势跟随窗口的动量效应",
        category="regime_change",
        display_name="震荡转趋势制度切换",
        physics={
            "essence": (
                "市场从RANGE_BOUND（区间震荡）切换到QUIET_TREND（安静趋势）时，"
                "振幅缩小但方向一致性增强——这是趋势初期的微结构特征。"
                "早期趋势跟随相比趋势成熟期有更好的风险收益比。"
            ),
            "why_temporary": (
                "制度切换带来的趋势初期效应是有时限的：趋势会加速（切入VOLATILE_TREND）"
                "或失败（回到RANGE_BOUND）。振幅扩大或成交量放大都说明QUIET_TREND状态结束。"
            ),
            "edge_source": "REGIME_SHIFT — 振幅/成交量/价差的结构性同步变化是制度切换的可测量特征",
        },
        entry_fingerprint=[
            {"feature": "regime", "condition": "RANGE_BOUND -> QUIET_TREND 切换", "why": "制度转换本身是触发条件"},
            {"feature": "amplitude_ma20", "condition": "下降（震荡减弱）", "why": "QUIET_TREND的典型特征"},
        ],
        validated_by=["RT-1"],
        relations={
            "reinforces": [],
            "conflicts_with": ["volume_climax_reversal", "amplitude_absorption"],
            "often_follows": ["compression_release"],
        },
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 兜底：generic_alpha — 通用规律（所有未映射的新发现规则）
    # ══════════════════════════════════════════════════════════════════════════
    "generic_alpha": MechanismConfig(
        mechanism_type="generic_alpha",
        primary=DecayCondition(
            feature="entry_feature",
            op="revert_to_neutral",
            threshold=None,
            description="入场主特征已经向中性位置回吐过半",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=1.0,
                description="成交确认开始回归常态，或主动方向也回到中性",
            ),
        ],
        description="未知或新发现 alpha 先按最通用的因果回归逻辑处理",
        category="generic",
        display_name="待归类通用规律",
        physics={
            "essence": "尚未归类到已知物理机制，作为兜底使用",
            "why_temporary": "入场特征向中性回归是所有均值回归类力量的共同衰竭信号",
            "edge_source": "STATISTICAL — 纯统计规律，需要补充物理因果验证后才能归入具名机制",
        },
        entry_fingerprint=[],
        validated_by=[],
        relations={},
    ),

    # OA-1: OI accumulation LONG (mirror of A3-OI distribution SHORT)
    "oi_accumulation_long": MechanismConfig(
        mechanism_type="oi_accumulation_long",
        primary=DecayCondition(
            feature="oi_change_rate_5m",
            op="revert_to_neutral",
            threshold=None,
            description="OI growth rate reverts to below 50pct of entry value (MA5 smoothed) = new-money inflow fading",
        ),
        confirms=[
            DecayCondition(
                feature="volume_vs_ma20",
                op="<",
                threshold=0.8,
                description="Volume contraction = accumulation momentum fading",
            ),
        ],
        description="TREND_UP with sustained OI growth = new long capital accumulating. Mirror of A3-OI distribution (SHORT).",
        category="open_interest_divergence",
        display_name="OI Accumulation LONG",
        physics={
            "essence": "In TREND_UP, rising OI means new long positions are being opened, not just existing longs holding. New capital inflow sustains the trend.",
            "why_temporary": "OI accumulation fades when new buyers stop entering. MA5-smoothed revert-to-neutral detects this without reacting to single-bar noise.",
            "edge_source": "OI_STRUCTURE -- sustained OI growth in uptrend is measurable new capital commitment",
        },
        entry_fingerprint=[
            {"feature": "oi_change_rate_5m", "condition": "> 0.003", "why": "OI growing at meaningful rate"},
            {"feature": "taker_buy_sell_ratio", "condition": "> 0.95", "why": "buyers dominant"},
            {"feature": "volume_vs_ma20", "condition": "> 1.2", "why": "above-average volume confirms conviction"},
        ],
        validated_by=["OA-1"],
        relations={
            "reinforces": ["buyer_impulse"],
            "conflicts_with": ["oi_divergence", "seller_impulse"],
            "often_follows": [],
        },
    ),
}



# ─── Force Relationship Helpers ──────────────────────────────────────

_FAMILY_TO_MECHANISM: dict[str, str] = {
    "P0-2": "funding_settlement",
    "P1-1": "mm_rebalance",
    "P1-2": "algo_slicing",
    "P1-6": "seller_drought",
    "P1-8": "vwap_reversion",
    "P1-9": "compression_release",
    "P1-10": "bottom_taker_exhaust",
    "P1-11": "funding_divergence",
    "C1": "funding_cycle_oversold",
    "RT-1": "regime_transition",
    "A2-26": "near_high_distribution",
    "A2-29": "near_high_distribution",
    "A3-OI": "oi_divergence",
    "A4-PIR": "oi_divergence",
    "OA-1": "oi_accumulation_long",
}


def get_mechanism_for_family(family: str) -> str:
    """Map strategy family to its primary mechanism ID. Falls back to 'generic_alpha'."""
    family_text = str(family or "").strip()
    if not family_text:
        return "generic_alpha"
    if family_text in _FAMILY_TO_MECHANISM:
        return _FAMILY_TO_MECHANISM[family_text]
    dynamic_mechanism = _load_dynamic_family_mechanism_map().get(family_text, "")
    if dynamic_mechanism:
        return dynamic_mechanism
    return "generic_alpha"


def get_force_category(mechanism: str) -> str:
    """Return the force category for a mechanism (e.g., 'leverage_cost_imbalance').

    Looks up the mechanism in MECHANISM_CATALOG and returns its .category attribute.
    Falls back to 'generic' for unknown mechanisms.
    """
    cfg = MECHANISM_CATALOG.get(mechanism)
    if cfg is None:
        return "generic"
    return cfg.category


def check_conflicts(mech_a: str, mech_b: str) -> bool:
    """Return True if two mechanisms conflict with each other."""
    cfg_a = MECHANISM_CATALOG.get(mech_a)
    cfg_b = MECHANISM_CATALOG.get(mech_b)
    if not cfg_a or not cfg_b:
        return False
    return (mech_b in cfg_a.relations.get("conflicts_with", [])) or            (mech_a in cfg_b.relations.get("conflicts_with", []))


def check_reinforces(mech_a: str, mech_b: str) -> bool:
    """Return True if the two mechanisms reinforce each other."""
    cfg_a = MECHANISM_CATALOG.get(mech_a)
    cfg_b = MECHANISM_CATALOG.get(mech_b)
    if not cfg_a or not cfg_b:
        return False
    return (mech_b in cfg_a.relations.get("reinforces", [])) or            (mech_a in cfg_b.relations.get("reinforces", []))


def get_chain_precedents(mechanism: str) -> list[str]:
    """Return mechanisms that typically precede this one (often_follows)."""
    cfg = MECHANISM_CATALOG.get(mechanism)
    if not cfg:
        return []
    return list(cfg.relations.get("often_follows", []))


class MechanismTracker:
    """追踪每个持仓背后因果机制的生命周期。"""

    def evaluate_decay(
        self,
        mechanism_type: str,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
        entry_regime: str | None = None,
        current_regime: str | None = None,
    ) -> DecayResult:
        """评估当前持仓所依赖机制是否已经开始衰竭。"""
        config = MECHANISM_CATALOG.get(mechanism_type)
        if config is None:
            logger.debug("未知机制 %s，回退到 generic_alpha", mechanism_type)
            config = MECHANISM_CATALOG["generic_alpha"]
            mechanism_type = "generic_alpha"

        if current_regime == "CRISIS":
            return DecayResult(
                decay_score=1.0,
                primary_fired=True,
                confirms_fired=["crisis_regime"],
                recommended_action="exit",
                reason="regime_shift_crisis",
            )

        primary_fired = self._check_condition(
            config.primary,
            entry_snapshot,
            current_features,
            mechanism_type=mechanism_type,
        )

        confirms_fired: list[str] = []
        for confirm in config.confirms:
            if self._check_condition(
                confirm,
                entry_snapshot,
                current_features,
                mechanism_type=mechanism_type,
            ):
                confirms_fired.append(confirm.feature)

        primary_score = 1.0 if primary_fired else 0.0
        confirm_score = (
            len(confirms_fired) / len(config.confirms)
            if config.confirms
            else 0.0
        )
        decay_score = 0.6 * primary_score + 0.4 * confirm_score

        if decay_score >= 0.6:
            action = "exit"
            reason = f"mechanism_decay_{mechanism_type}"
        elif decay_score >= 0.3:
            action = "tighten"
            reason = f"mechanism_weakening_{mechanism_type}"
        else:
            action = "hold"
            reason = "mechanism_alive"

        if entry_regime and current_regime and entry_regime != current_regime:
            if current_regime in ("VOL_EXPANSION", "CRISIS"):
                decay_score = max(decay_score, 0.6)
                action = "exit"
                reason = f"regime_shift_{entry_regime}_to_{current_regime}"

        return DecayResult(
            decay_score=float(decay_score),
            primary_fired=primary_fired,
            confirms_fired=confirms_fired,
            recommended_action=action,
            reason=reason,
        )

    def _check_condition(
        self,
        condition: DecayCondition,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
        mechanism_type: str = "",
    ) -> bool:
        """检查单个衰竭条件是否触发。"""
        feature = condition.feature

        if feature == "minutes_to_funding" and condition.op == "revert_to_neutral":
            return self._check_funding_window_reset(entry_snapshot, current_features)

        if feature == "volume_vs_ma20+taker_buy_sell_ratio":
            return self._check_compound_flow_return(
                mechanism_type,
                current_features,
            )

        if feature == "dist_to_24h_low" and condition.op == "<" and condition.threshold is None:
            current_dist = _safe_get(current_features, "dist_to_24h_low")
            entry_dist = _safe_get(entry_snapshot, "dist_to_24h_low")
            if current_dist is None or entry_dist is None:
                return False
            return current_dist < entry_dist * 0.5

        if feature == "amplitude_1m" and condition.op == "<" and condition.threshold is None:
            current_amp = _safe_get(current_features, "amplitude_1m")
            entry_amp_ma20 = _safe_get(entry_snapshot, "amplitude_ma20")
            if current_amp is None or entry_amp_ma20 is None:
                return False
            return current_amp < entry_amp_ma20

        if feature == "entry_feature" and condition.op == "revert_to_neutral":
            return self._check_generic_feature_revert(entry_snapshot, current_features)

        if (
            mechanism_type == "volume_climax_reversal"
            and feature == "taker_buy_sell_ratio"
            and condition.op == "revert_to_neutral"
        ):
            return self._check_directional_taker_revert(entry_snapshot, current_features)

        if (
            mechanism_type == "generic_alpha"
            and feature == "volume_vs_ma20"
            and condition.op == "<"
            and condition.threshold == 1.0
        ):
            return self._check_generic_volume_confirm(current_features)

        current_value = _safe_get(current_features, feature)
        if current_value is None:
            return False

        if condition.op == ">":
            threshold = condition.threshold
            if threshold is None:
                return False
            return current_value > float(threshold)

        if condition.op == "<":
            threshold = condition.threshold
            if threshold is None:
                return False
            return current_value < float(threshold)

        if condition.op == "between":
            threshold = condition.threshold
            if not isinstance(threshold, tuple) or len(threshold) != 2:
                return False
            low, high = threshold
            return float(low) <= current_value <= float(high)

        if (
            mechanism_type == "oi_accumulation_long"
            and condition.op == "revert_to_neutral"
        ):
            return self._check_revert_to_neutral_smoothed(
                feature=condition.feature,
                entry_snapshot=entry_snapshot,
                current_features=current_features,
            )

        if condition.op == "revert_to_neutral":
            return self._check_revert_to_neutral(
                feature=feature,
                entry_snapshot=entry_snapshot,
                current_features=current_features,
            )

        logger.debug("未识别的衰竭操作: %s", condition.op)
        return False

    def _check_funding_window_reset(
        self,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
    ) -> bool:
        """检查资金费率结算窗口是否已经走完并重置。"""
        current_minutes = _safe_get(current_features, "minutes_to_funding")
        entry_minutes = _safe_get(entry_snapshot, "minutes_to_funding")
        if current_minutes is None:
            return False
        if current_minutes > 30.0:
            return True
        if entry_minutes is None:
            return False
        return entry_minutes < 30.0 and current_minutes > entry_minutes

    def _check_compound_flow_return(
        self,
        mechanism_type: str,
        current_features: pd.Series | dict,
    ) -> bool:
        """检查复合成交条件。"""
        volume = _safe_get(current_features, "volume_vs_ma20")
        taker_ratio = _safe_get(current_features, "taker_buy_sell_ratio")
        if volume is None or taker_ratio is None:
            return False

        if mechanism_type == "seller_drought":
            return volume > 1.5 and taker_ratio < 0.8
        if mechanism_type == "bottom_taker_exhaust":
            return volume > 1.5 and taker_ratio < 1.0
        return False

    def _check_generic_feature_revert(
        self,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
    ) -> bool:
        """检查 generic alpha 的主特征是否已经回吐过半。"""
        feature, entry_value = _resolve_entry_feature(entry_snapshot)
        if not feature or entry_value is None:
            return False
        return self._check_revert_to_neutral(
            feature=feature,
            entry_snapshot=entry_snapshot,
            current_features=current_features,
            entry_value=entry_value,
        )

    def _check_directional_taker_revert(
        self,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
    ) -> bool:
        """按入场方向检查 taker 比例是否重新穿回中性轴。"""
        current_ratio = _safe_get(current_features, "taker_buy_sell_ratio")
        entry_ratio = _safe_get(entry_snapshot, "taker_buy_sell_ratio")
        direction = str(
            entry_snapshot.get("direction")
            or entry_snapshot.get("side")
            or ""
        ).lower()

        if current_ratio is None:
            return False
        if direction == "long":
            return current_ratio > 1.0
        if direction == "short":
            return current_ratio < 1.0
        if entry_ratio is not None:
            if entry_ratio < 1.0:
                return current_ratio > 1.0
            if entry_ratio > 1.0:
                return current_ratio < 1.0
        return False

    def _check_generic_volume_confirm(
        self,
        current_features: pd.Series | dict,
    ) -> bool:
        """检查 generic alpha 的量能确认是否已经回到常态。"""
        volume = _safe_get(current_features, "volume_vs_ma20")
        taker_ratio = _safe_get(current_features, "taker_buy_sell_ratio")

        volume_reverted = volume is not None and volume < 0.8
        taker_neutral = (
            taker_ratio is not None and 0.90 <= taker_ratio <= 1.10
        )
        return volume_reverted and taker_neutral

    def _check_revert_to_neutral(
        self,
        feature: str,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
        entry_value: float | None = None,
    ) -> bool:
        """检查当前值是否已经向中性位置回吐超过一半。"""
        current_value = _safe_get(current_features, feature)
        if current_value is None:
            return False

        if entry_value is None:
            entry_value = _safe_get(entry_snapshot, feature)
        if entry_value is None:
            return False

        neutral_value = _neutral_value_for_feature(feature)
        entry_distance = abs(entry_value - neutral_value)
        if entry_distance <= 0.0:
            return False

        current_distance = abs(current_value - neutral_value)
        return current_distance <= entry_distance * 0.5

    def _check_revert_to_neutral_smoothed(
        self,
        feature: str,
        entry_snapshot: dict,
        current_features: pd.Series | dict,
    ) -> bool:
        """MA5 smoothed revert-to-neutral for slow-accumulation LONG mechanisms.

        Uses the pre-computed {feature}_ma5 column if available, otherwise
        falls back to the raw feature. Prevents single-bar oscillation from
        triggering premature exits on slow OI accumulation moves.
        """
        ma5_key = f"{feature}_ma5"
        current_ma5 = _safe_get(current_features, ma5_key)
        if current_ma5 is None:
            current_ma5 = _safe_get(current_features, feature)
        if current_ma5 is None:
            return False

        entry_value = _safe_get(entry_snapshot, feature)
        if entry_value is None:
            return False

        neutral = _neutral_value_for_feature(feature)
        entry_dist = abs(entry_value - neutral)
        if entry_dist <= 0:
            return False
        current_dist = abs(current_ma5 - neutral)
        return current_dist <= entry_dist * 0.5


# ─── Dynamic Mechanism Registration (LLM-discovered forces) ──────────

_CUSTOM_MECHANISMS_PATH = Path("monitor/output/custom_mechanisms.json")

# Category aliases: map LLM's free-form category names to canonical IDs
_CATEGORY_ALIASES: dict[str, str] = {
    "leverage_cost": "leverage_cost_imbalance",
    "liquidity": "liquidity_vacuum",
    "exhaustion": "unilateral_exhaustion",
    "algorithmic": "algorithmic_trace",
    "potential_energy": "potential_energy_release",
    "distribution": "distribution_pattern",
    "oi_divergence": "open_interest_divergence",
    "oi": "open_interest_divergence",
    "inventory": "inventory_rebalance",
    "regime": "regime_change",
}


def _resolve_category(raw_category: str) -> str:
    """Normalize a category string to a canonical MECHANISM_CATEGORIES key."""
    raw = raw_category.strip().lower().replace(" ", "_")
    if raw in MECHANISM_CATEGORIES:
        return raw
    if raw in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[raw]
    # Fuzzy match: check if raw is a substring of any canonical category
    for key in MECHANISM_CATEGORIES:
        if raw in key or key in raw:
            return key
    return "generic"


def register_mechanism(
    mechanism_type: str,
    family: str,
    direction: str,
    category: str,
    display_name: str = "",
    physics: dict | None = None,
    primary_decay_feature: str = "",
    primary_decay_condition: str = "",
    decay_narrative: str = "",
) -> bool:
    """Register a new LLM-discovered mechanism into the live force library.

    Creates a MechanismConfig, inserts into MECHANISM_CATALOG,
    registers the category if new, maps the family, and persists to disk.

    Returns True if a NEW mechanism was registered, False if it already existed.
    """
    mechanism_type = mechanism_type.strip()
    if not mechanism_type or mechanism_type in ("generic", "generic_alpha"):
        return False

    # Already known — just ensure family mapping exists
    if mechanism_type in MECHANISM_CATALOG:
        if family and family not in _FAMILY_TO_MECHANISM:
            _FAMILY_TO_MECHANISM[family] = mechanism_type
            _save_custom_mechanisms()
        return False

    # Parse primary decay condition from LLM string like "oi_change_rate_1h > 0.001"
    primary = _parse_decay_condition(primary_decay_feature, primary_decay_condition)

    resolved_cat = _resolve_category(category)
    physics = physics or {}

    config = MechanismConfig(
        mechanism_type=mechanism_type,
        primary=primary,
        confirms=[],
        description=decay_narrative or display_name or mechanism_type,
        category=resolved_cat,
        display_name=display_name,
        physics=physics,
        entry_fingerprint=[primary_decay_feature] if primary_decay_feature else [],
        relations={},
        validated_by=[family] if family else [],
    )

    # Register into live data structures
    config._is_custom = True  # type: ignore[attr-defined]
    MECHANISM_CATALOG[mechanism_type] = config

    if resolved_cat not in MECHANISM_CATEGORIES:
        MECHANISM_CATEGORIES[resolved_cat] = display_name or resolved_cat

    if family:
        _FAMILY_TO_MECHANISM[family] = mechanism_type
        SIGNAL_MECHANISM_MAP[family] = mechanism_type

    _save_custom_mechanisms()

    logger.info(
        "[FORCE_REGISTRY] New mechanism registered: %s (category=%s, family=%s)",
        mechanism_type, resolved_cat, family,
    )
    return True


def _parse_decay_condition(feature: str, condition_str: str) -> DecayCondition:
    """Parse LLM decay condition string like 'oi_change_rate_1h > 0.001'."""
    import re as _re
    feature = feature.strip()
    condition_str = condition_str.strip()

    # Try to parse "feature op threshold" from condition string
    m = _re.match(r"(\w+)\s*([<>]=?)\s*([-\d.eE+]+)", condition_str)
    if m:
        feature = m.group(1)
        op = m.group(2).replace(">=", ">").replace("<=", "<")
        threshold = float(m.group(3))
    else:
        op = ">"
        threshold = 0.0

    return DecayCondition(
        feature=feature or "generic",
        op=op,
        threshold=threshold,
        description=condition_str or "LLM-derived decay condition",
    )


def _save_custom_mechanisms() -> None:
    """Persist LLM-discovered mechanisms to JSON for restart survival."""
    import json as _json

    custom: list[dict] = []
    for mtype, cfg in MECHANISM_CATALOG.items():
        # Only save mechanisms not in the original hardcoded catalog
        if not hasattr(cfg, "_is_custom"):
            continue
        custom.append({
            "mechanism_type": cfg.mechanism_type,
            "category": cfg.category,
            "display_name": cfg.display_name,
            "description": cfg.description,
            "physics": cfg.physics,
            "primary": {
                "feature": cfg.primary.feature,
                "op": cfg.primary.op,
                "threshold": cfg.primary.threshold,
                "description": cfg.primary.description,
            },
            "entry_fingerprint": cfg.entry_fingerprint,
            "relations": cfg.relations,
            "validated_by": cfg.validated_by,
        })

    # Also save custom family mappings
    families: dict[str, str] = {}
    for fam, mech in _FAMILY_TO_MECHANISM.items():
        if mech in {c["mechanism_type"] for c in custom}:
            families[fam] = mech

    data = {"mechanisms": custom, "family_mappings": families}
    try:
        _CUSTOM_MECHANISMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CUSTOM_MECHANISMS_PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CUSTOM_MECHANISMS_PATH)
    except Exception as exc:
        logger.warning("[FORCE_REGISTRY] Failed to save custom mechanisms: %s", exc)


def _load_custom_mechanisms() -> None:
    """Load LLM-discovered mechanisms from JSON on startup."""
    import json as _json

    if not _CUSTOM_MECHANISMS_PATH.exists():
        return
    try:
        data = _json.loads(_CUSTOM_MECHANISMS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[FORCE_REGISTRY] Failed to load custom mechanisms: %s", exc)
        return

    for entry in data.get("mechanisms", []):
        mtype = entry.get("mechanism_type", "")
        if not mtype or mtype in MECHANISM_CATALOG:
            continue

        primary_data = entry.get("primary", {})
        primary = DecayCondition(
            feature=primary_data.get("feature", "generic"),
            op=primary_data.get("op", ">"),
            threshold=primary_data.get("threshold", 0.0),
            description=primary_data.get("description", ""),
        )

        cfg = MechanismConfig(
            mechanism_type=mtype,
            primary=primary,
            confirms=[],
            description=entry.get("description", ""),
            category=entry.get("category", "generic"),
            display_name=entry.get("display_name", ""),
            physics=entry.get("physics", {}),
            entry_fingerprint=entry.get("entry_fingerprint", []),
            relations=entry.get("relations", {}),
            validated_by=entry.get("validated_by", []),
        )
        cfg._is_custom = True  # type: ignore[attr-defined]
        MECHANISM_CATALOG[mtype] = cfg

        cat = entry.get("category", "generic")
        if cat and cat not in MECHANISM_CATEGORIES:
            MECHANISM_CATEGORIES[cat] = entry.get("display_name", cat)

    for fam, mech in data.get("family_mappings", {}).items():
        if fam not in _FAMILY_TO_MECHANISM:
            _FAMILY_TO_MECHANISM[fam] = mech
            SIGNAL_MECHANISM_MAP[fam] = mech

    count = len(data.get("mechanisms", []))
    if count:
        logger.info("[FORCE_REGISTRY] Loaded %d custom mechanisms from disk", count)


# Load custom mechanisms on module import
_load_custom_mechanisms()

# FAT-FIX: 增加 approved_rules/force_registry 动态家族映射，确保 A5-xxx 能解析到真实 mechanism_type。


__all__ = [
    "DecayCondition",
    "MechanismConfig",
    "DecayResult",
    "MechanismTracker",
    "MECHANISM_CATALOG",
    "MECHANISM_CATEGORIES",
    "SIGNAL_MECHANISM_MAP",
    "_safe_get",
    "resolve_mechanism_type",
    # Force relationship helpers
    "_FAMILY_TO_MECHANISM",
    "get_mechanism_for_family",
    "get_force_category",
    "check_conflicts",
    "check_reinforces",
    "get_chain_precedents",
    # Dynamic registration
    "register_mechanism",
]
