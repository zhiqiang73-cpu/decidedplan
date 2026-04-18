"""Exit parameter loading and lookup helpers.

``base_horizon`` remains the research/observation window estimated during
discovery. It is *not* a promise to exit after N bars; the runtime only uses it
to size the final safety cap when all dynamic exit logic stays silent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict


CORE_EXIT_FAMILIES = (
    "P0-2",
    "P1-2",
    "P1-6",
    "P1-8",
    "P1-9",
    "P1-10",
    "P1-11",
    "P1-12",
    "P1-13",
    "P1-14",
    "C1",
    "OA-1",
    "RT-1",
    # T1 系列: Tick 30s 微结构信号
    "T1-1",
    "T1-2",
    "T1-3",
)

FAMILY_MIN_HOLD_CAPS = {
    "P0-2": 6,
    "P1-2": 12,
    "P1-6": 20,
    "P1-8": 24,
    "P1-9": 24,
    "P1-10": 20,
    "P1-11": 24,
    "P1-12": 60,   # safety_cap = 60 根 1m K 线（avg hold 22 bar，上限 1 小时）
    "P1-13": 60,   # safety_cap = 60 根 1m K 线（avg hold 21 bar，上限 1 小时）
    "P1-14": 60,   # safety_cap = 60 根 1m K 线（avg hold 52 bar，上限 1 小时）
    "C1": 30,
    "OA-1": 3,
    "RT-1": 60,   # safety_cap 锚点: 60 根 1m K 线（约 1 小时），制度转换持仓上限
    # T1 系列: 18 根 30s bar = 9分钟最大持仓
    "T1-1": 18,
    "T1-2": 18,
    "T1-3": 18,
}

# T1 系列出场参数（智能三阶段，紧止损+快速保本）
# 验证最优: SL=0.05%, 保本触发=0.04%, 追踪激活=0.08%, 追踪回撤=0.04%
_T1_EXIT_PARAMS_BASE = dict(
    stop_pct=0.05,           # 硬止损 0.05%
    protect_start_pct=0.04,  # 浮盈0.04%时移SL到入场价 (覆盖手续费0.036%)
    protect_gap_ratio=0.50,
    protect_floor_pct=0.01,
    min_hold_bars=1,         # tick信号无最小持仓限制
    max_hold_factor=6,       # base_horizon=3, 6x=18 bar max
    exit_confirm_bars=1,
    mfe_ratchet_threshold=0.08,  # 追踪激活: 浮盈达0.08%
    mfe_ratchet_ratio=0.50,      # 追踪: 锁住峰值50%利润
    decay_exit_threshold=0.95,   # 机制衰竭门槛高 (tick信号衰竭快)
    decay_tighten_threshold=0.7,
)

BEST_PARAMS_PATH = Path("monitor/output/exit_policy_best_params.json")


@dataclass(frozen=True)
class ExitParams:
    take_profit_pct: float = 0.0
    stop_pct: float = 0.70
    protect_start_pct: float = 0.12
    protect_gap_ratio: float = 0.50
    protect_floor_pct: float = 0.03
    min_hold_bars: int = 3
    max_hold_factor: int = 4
    exit_confirm_bars: int = 2
    decay_exit_threshold: float = 0.85
    decay_tighten_threshold: float = 0.5
    tighten_gap_ratio: float = 0.30
    # Adaptive stop multipliers
    confidence_stop_multipliers: dict = field(default_factory=lambda: {1: 0.7, 2: 1.0, 3: 1.3})
    regime_stop_multipliers: dict = field(default_factory=lambda: {
        "QUIET_TREND": 0.8,
        "RANGE_BOUND": 1.0,
        "VOLATILE_TREND": 1.5,
        "VOL_EXPANSION": 1.5,
        "CRISIS": 0.5,
    })
    # SHORT 鏂瑰悜鐙珛 regime 涔樻暟
    # 鍥炴祴楠岃瘉: 澶氭暟 SHORT 绛栫暐鍦?QUIET_TREND 涓?regime_mult=0.6 鏈€浼?
    # (P1-11 SHORT 渚嬪, 鏈€浼?1.3, 閫氳繃 best_params.json 鍗曠嫭瑕嗙洊)
    regime_stop_multipliers_short: dict = field(default_factory=lambda: {
        "QUIET_TREND": 0.6,
        "RANGE_BOUND": 1.0,
        "VOLATILE_TREND": 1.5,
        "VOL_EXPANSION": 1.5,
        "CRISIS": 0.5,
    })
    # MFE 棘轮: MFE 超过此阈值后, 止损被压到 MFE * ratio
    # 0.15 对 BTC 太低(1根K线就能波动0.15%), 提到 0.25 防止噪声触发
    mfe_ratchet_threshold: float = 0.25
    mfe_ratchet_ratio: float = 0.4

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _coerce_float(value: object, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_multiplier_map(
    payload: object,
    *,
    base: dict,
    int_keys: bool = False,
) -> dict:
    if not isinstance(payload, dict):
        return dict(base)

    normalized: dict = {}
    for raw_key, raw_value in payload.items():
        try:
            key = int(float(raw_key)) if int_keys else str(raw_key)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        normalized[key] = value
    return normalized or dict(base)


def build_exit_params(payload: Any, base: ExitParams | None = None) -> ExitParams | None:
    if not isinstance(payload, dict):
        return None

    base = base or ExitParams()
    try:
        return ExitParams(
            take_profit_pct=_coerce_float(payload.get("take_profit_pct"), base.take_profit_pct),
            stop_pct=_coerce_float(payload.get("stop_pct"), base.stop_pct),
            protect_start_pct=_coerce_float(payload.get("protect_start_pct"), base.protect_start_pct),
            protect_gap_ratio=_coerce_float(payload.get("protect_gap_ratio"), base.protect_gap_ratio),
            protect_floor_pct=_coerce_float(payload.get("protect_floor_pct"), base.protect_floor_pct),
            min_hold_bars=_coerce_int(payload.get("min_hold_bars"), base.min_hold_bars),
            max_hold_factor=_coerce_int(payload.get("max_hold_factor"), base.max_hold_factor),
            exit_confirm_bars=_coerce_int(payload.get("exit_confirm_bars"), base.exit_confirm_bars),
            decay_exit_threshold=_coerce_float(payload.get("decay_exit_threshold"), base.decay_exit_threshold),
            decay_tighten_threshold=_coerce_float(payload.get("decay_tighten_threshold"), base.decay_tighten_threshold),
            tighten_gap_ratio=_coerce_float(payload.get("tighten_gap_ratio"), base.tighten_gap_ratio),
            confidence_stop_multipliers=_coerce_multiplier_map(
                payload.get("confidence_stop_multipliers"),
                base=base.confidence_stop_multipliers,
                int_keys=True,
            ),
            regime_stop_multipliers=_coerce_multiplier_map(
                payload.get("regime_stop_multipliers"),
                base=base.regime_stop_multipliers,
            ),
            regime_stop_multipliers_short=_coerce_multiplier_map(
                payload.get("regime_stop_multipliers_short"),
                base=base.regime_stop_multipliers_short,
            ),
            mfe_ratchet_threshold=_coerce_float(payload.get("mfe_ratchet_threshold"), base.mfe_ratchet_threshold),
            mfe_ratchet_ratio=_coerce_float(payload.get("mfe_ratchet_ratio"), base.mfe_ratchet_ratio),
        )
    except Exception:
        return None


DEFAULT_EXIT_PARAMS: Dict[str, ExitParams] = {
    family: ExitParams() for family in CORE_EXIT_FAMILIES
}

# 覆盖 T1 系列的默认参数（紧止损+快速保本）
_t1_params = ExitParams(**_T1_EXIT_PARAMS_BASE)
for _t1_fam in ("T1-1", "T1-2", "T1-3"):
    DEFAULT_EXIT_PARAMS[_t1_fam] = _t1_params

# C1 专属出场参数（资金周期超卖做多策略）
# 默认参数 stop_pct=0.70% 过松，safety_cap=120根K线过长
# 物理依据：C1 的均值回归窗口短（30根K线），不应容忍长时间逆势浮亏
DEFAULT_EXIT_PARAMS["C1"] = ExitParams(
    stop_pct=0.35,              # 硬止损从 0.70% 收紧到 0.35%
    protect_start_pct=0.08,     # 浮盈 0.08% 即启动保本线（原 0.12%）
    protect_gap_ratio=0.50,
    protect_floor_pct=0.02,
    min_hold_bars=3,
    max_hold_factor=2,          # safety_cap = 30×2 = 60根K线（从 120 降到 60）
    exit_confirm_bars=2,
    mfe_ratchet_threshold=0.08, # 追踪止损激活点从 0.15% 降到 0.08%
    mfe_ratchet_ratio=0.50,     # 锁住峰值 50% 利润（原 40%）
)


# P1-11 专属出场参数（高仓位资金费做空）
# 回测优化 (2026-04-17): no_protect PnL=+2.57% vs current +0.73%
# 原因: 利润保护截断利润, 98/157笔被保护提前锁利; 去掉后 logic_complete 33->94 笔
DEFAULT_EXIT_PARAMS["P1-11"] = ExitParams(
    stop_pct=1.50,               # 保持宽止损 (高位做空振幅大)
    protect_start_pct=99.0,      # 禁用利润保护
    protect_gap_ratio=0.40,
    protect_floor_pct=0.05,
    min_hold_bars=5,
    max_hold_factor=4,            # safety_cap = 20*4 = 80 bars
    exit_confirm_bars=1,
    mfe_ratchet_threshold=0.25,
    mfe_ratchet_ratio=0.40,
)


# P1-12 专属出场参数（趋势下跌区间顶部做空）
# 回测优化 (2026-04-17): wider+relax PnL=+5.96% vs current +1.31%
# 原因: thesis_invalidated(0.02)太敏感, 43/78笔被误判; 放宽到0.05后WR 43.6%->73.1%
DEFAULT_EXIT_PARAMS["P1-12"] = ExitParams(
    stop_pct=0.50,               # 回测最优: 0.50% (wider, 减少止损触发)
    protect_start_pct=99.0,      # 禁用利润保护 (回测验证: 加保护反而亏)
    protect_gap_ratio=0.50,
    protect_floor_pct=0.03,
    min_hold_bars=3,
    max_hold_factor=3,            # base=30 bar * 3 = 90 bar safety_cap (wider+relax)
    exit_confirm_bars=2,
    mfe_ratchet_threshold=0.25,
    mfe_ratchet_ratio=0.50,
)


# P1-13 专属出场参数（趋势下跌 + VWAP 双确认做空）
# 回测优化 (2026-04-17): no_protect PnL=+1.43% vs current -0.15%
# 原因: thesis_invalidated(r4h+0.02)太敏感, 45%交易被误判失效
# 最优: 去掉利润保护, 放宽 invalidation 阈值(在 smart_exit_policy.py 中改)
DEFAULT_EXIT_PARAMS["P1-13"] = ExitParams(
    stop_pct=0.30,               # 保持（MAE p90=0.22%）
    protect_start_pct=99.0,      # 禁用利润保护（回测验证: 去掉后 PnL 从 -0.15% 到 +1.43%）
    protect_gap_ratio=0.50,
    protect_floor_pct=0.02,
    min_hold_bars=3,
    max_hold_factor=2,            # base=30 bar * 2 = 60 bar safety_cap
    exit_confirm_bars=2,
    mfe_ratchet_threshold=0.25,
    mfe_ratchet_ratio=0.50,
)


# P1-14 专属出场参数（趋势下跌 + 日内支撑确认反弹做多）
# 回测优化 (2026-04-17): no_protect 配置 PnL=+74.56% vs current +26.23%
# 原因: 利润保护在 0.20% 过早锁利, 截断了 87/101 笔交易的上行空间
# 最优: 去掉利润保护, 让 vs_entry logic_complete 和 safety_cap 自然出场
DEFAULT_EXIT_PARAMS["P1-14"] = ExitParams(
    stop_pct=1.50,               # 保持宽止损（逆势振幅大, MAE p90=1.36%）
    protect_start_pct=99.0,      # 禁用利润保护（回测验证: 去掉后 PnL x2.8）
    protect_gap_ratio=0.50,
    protect_floor_pct=0.05,
    min_hold_bars=5,
    max_hold_factor=2,            # base=60 bar * 2 = 120 bar safety_cap
    exit_confirm_bars=2,
    mfe_ratchet_threshold=0.50,  # 保持（MFE 平均 1.375%, 0.50% 阈值合理）
    mfe_ratchet_ratio=0.50,
)


# P1-8 SHORT 专属出场参数（VWAP 偏离+量能枯竭做空方向）
# 实盘问题（2026-04-16）：live WR ~50%，-$31.89；赢均+0.172% vs 亏均-0.321%，盈亏不对称
# 根因：默认 stop_pct=0.70% 过松，导致单笔亏损远大于单笔盈利
# 修复：收紧止损到 0.50%，移除固定 TP（靠 vs_entry 力消失出场）
DEFAULT_EXIT_PARAMS["P1-8|short"] = ExitParams(
    take_profit_pct=0.0,          # 移除固定 TP，靠 vs_entry 智能出场
    stop_pct=0.50,                 # 从默认 0.70% 收紧到 0.50%（亏均-0.321%，对应 0.50% 止损合理）
    protect_start_pct=0.10,        # 浮盈 0.10% 即启动保本线
    protect_gap_ratio=0.50,
    protect_floor_pct=0.02,
    min_hold_bars=3,
    max_hold_factor=3,             # safety_cap = 24×3 = 72根K线
    exit_confirm_bars=2,
    mfe_ratchet_threshold=0.25,    # 0.12%太低(BTC噪声级), 提到0.25%
    mfe_ratchet_ratio=0.50,        # 锁住峰值 50% 利润
)



# RT-1 专属出场参数（制度转换做多策略）
# 制度转换需要足够时间让新制度确认，允许较宽止损（0.40%）
# 禁用利润保护（CLAUDE.md 红线），禁止固定止盈（CLAUDE.md 红线）
# safety_cap = 60 * 2 = 120 根 1m K 线（约 2 小时兜底）
DEFAULT_EXIT_PARAMS["RT-1"] = ExitParams(
    stop_pct=0.40,              # 制度转换策略允许较宽止损
    take_profit_pct=0.0,        # 禁止固定止盈
    protect_start_pct=99.0,     # 禁用利润保护
    protect_gap_ratio=0.50,
    protect_floor_pct=0.05,
    min_hold_bars=5,            # 让制度确认有时间
    max_hold_factor=2,          # safety_cap = 60 * 2 = 120 bars 兜底
    exit_confirm_bars=2,        # 出场防抖
    mfe_ratchet_threshold=0.25,
    mfe_ratchet_ratio=0.40,
)

def resolve_safety_cap_bars(family: str, base_horizon: int, params: ExitParams) -> int:
    family_cap = FAMILY_MIN_HOLD_CAPS.get(family, max(6, base_horizon * 2))
    scaled_cap = max(base_horizon * params.max_hold_factor, params.min_hold_bars + 1)
    return max(family_cap, scaled_cap)


def _load_raw_params() -> dict:
    if not BEST_PARAMS_PATH.exists():
        return {}
    try:
        return json.loads(BEST_PARAMS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def load_best_exit_params() -> Dict[str, ExitParams]:
    raw = _load_raw_params()
    if not raw:
        return dict(DEFAULT_EXIT_PARAMS)

    result = dict(DEFAULT_EXIT_PARAMS)
    for key, payload in raw.items():
        params = build_exit_params(payload, base=result.get(key, ExitParams()))
        if params is not None:
            result[key] = params
    return result


def resolve_exit_params_key(family: str, direction: str | None = None) -> str:
    direction_text = str(direction or "").lower()
    if direction_text in {"long", "short"}:
        return f"{family}|{direction_text}"
    return family


def get_exit_params_for_signal(
    family: str,
    direction: str | None = None,
    params_map: Dict[str, ExitParams] | None = None,
) -> ExitParams:
    params_map = params_map or load_best_exit_params()
    direction_key = resolve_exit_params_key(family, direction)
    if direction_key in params_map:
        return params_map[direction_key]
    if family in params_map:
        return params_map[family]
    return ExitParams()


def has_explicit_exit_params(family: str, direction: str | None = None) -> bool:
    direction_key = resolve_exit_params_key(family, direction)
    raw = _load_raw_params()
    if direction_key in raw or family in raw:
        return True
    return direction_key in DEFAULT_EXIT_PARAMS or family in DEFAULT_EXIT_PARAMS


def save_exit_params(key: str, params: ExitParams) -> None:
    """Merge a single family|direction entry into best_params.json (atomic write)."""
    raw = _load_raw_params()
    raw[key] = params.to_dict()
    BEST_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BEST_PARAMS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=4), encoding="utf-8")
    tmp.replace(BEST_PARAMS_PATH)
