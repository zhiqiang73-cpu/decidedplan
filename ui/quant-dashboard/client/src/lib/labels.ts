export const REGIME_LABELS: Record<string, string> = {
  QUIET_TREND: "安静趋势",
  VOLATILE_TREND: "波动趋势",
  RANGE_BOUND: "区间震荡",
  VOL_EXPANSION: "波动扩张",
  CRISIS: "危机模式",
  UNKNOWN: "未知",
};

const DIRECTION_LABELS: Record<string, string> = {
  LONG: "做多",
  SHORT: "做空",
  BOTH: "双向",
  long: "做多",
  short: "做空",
  both: "双向",
  unknown: "未配置",
};

const TRADE_STATUS_LABELS: Record<string, string> = {
  open: "持仓中",
  closed: "已平仓",
  cancelled: "已撤单",
};

const STRATEGY_STATUS_LABELS: Record<string, string> = {
  active: "活跃",
  paused: "暂停",
  degraded: "降级",
  retired: "退市",
};

const FORCE_CATEGORY_LABELS: Record<string, string> = {
  leverage_cost_imbalance: "杠杆成本失衡",
  liquidity_vacuum: "流动性真空",
  unilateral_exhaustion: "单边力量耗尽",
  algorithmic_trace: "算法执行痕迹",
  potential_energy_release: "势能释放",
  distribution_pattern: "派发结构",
  open_interest_divergence: "持仓量背离",
  inventory_rebalance: "库存再平衡",
  regime_change: "状态切换",
  generic: "通用规则",
};

const EXIT_REASON_LABELS: Record<string, string> = {
  hard_stop: "硬止损",
  logic_complete: "机制完成",
  time_cap: "安全网上限",
  safety_cap: "安全网上限",
  take_profit: "止盈",
  not_filled: "未成交",
  trailing_stop: "追踪止损",
  mfe_ratchet: "盈利保护",
  counter_signal: "反向信号",
  regime_shift: "制度切换",
  profit_protect: "盈利保护",
};

function fallback(value?: string | null): string {
  return value && value.trim() ? value : "未配置";
}

export function formatDirection(value?: string | null): string {
  if (!value) return "未配置";
  return DIRECTION_LABELS[value] ?? fallback(value);
}

export function formatTradeStatus(value?: string | null): string {
  if (!value) return "未配置";
  return TRADE_STATUS_LABELS[value] ?? fallback(value);
}

export function formatStrategyStatus(value?: string | null): string {
  if (!value) return "未配置";
  return STRATEGY_STATUS_LABELS[value] ?? fallback(value);
}

export function formatRegime(value?: string | null): string {
  if (!value) return REGIME_LABELS.UNKNOWN;
  return REGIME_LABELS[value] ?? fallback(value);
}

export function formatForceCategory(value?: string | null): string {
  if (!value) return FORCE_CATEGORY_LABELS.generic;
  return FORCE_CATEGORY_LABELS[value] ?? fallback(value);
}

export function formatExitReason(value?: string | null): string {
  if (!value) return "-";
  // mechanism_decay_* → "机制衰竭"
  if (value.startsWith("mechanism_decay")) return "机制衰竭";
  return EXIT_REASON_LABELS[value] ?? value;
}
