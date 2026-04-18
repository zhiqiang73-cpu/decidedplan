/**
 * pythonBridge.ts
 * Reads Python system output files and exposes typed accessors.
 * All paths are resolved from process.cwd() (project root, set by watchdog.py).
 */
import fs from "fs";
import path from "path";

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Root resolution 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜?
// When launched by watchdog.py from the project root, process.cwd() = project root.
// When running `npm run dev` from ui/quant-dashboard/, we go up two levels.
function resolveRoot(): string {
  const cwd = process.cwd();
  // If cwd contains 'quant-dashboard', we're in dev mode 闂?go up to project root
  if (cwd.includes("quant-dashboard")) {
    return path.resolve(cwd, "../..");
  }
  return cwd;
}

const ROOT = resolveRoot();

const P = {
  systemState: path.join(ROOT, "monitor/output/system_state.json"),
  tradesCSV:   path.join(ROOT, "execution/logs/trades.csv"),
  approvedRules: path.join(ROOT, "alpha/output/approved_rules.json"),
  pendingRules:  path.join(ROOT, "alpha/output/pending_rules.json"),
  alertsLog:   path.join(ROOT, "monitor/output/alerts.log"),
  envFile:     path.join(ROOT, ".env"),
  devTasks:    path.join(ROOT, "data/dev_tasks.json"),
  engineState:    path.join(ROOT, "alpha/output/engine_state.json"),
  reviewQueue:    path.join(ROOT, "alpha/output/review_queue.json"),
  rejectedRules:  path.join(ROOT, "alpha/output/rejected_rules.json"),
  promoterConfig: path.join(ROOT, "alpha/output/promoter_config.json"),
  forceLibraryState: path.join(ROOT, "monitor/output/force_library_state.json"),
  exchangeTrades:    path.join(ROOT, "execution/logs/exchange_trades.json"),
  exchangeTradeHidden: path.join(ROOT, "execution/logs/exchange_trades_hidden.json"),
  exitBestParams: path.join(ROOT, "monitor/output/exit_policy_best_params.json"),
};

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Types 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕
export interface SystemState {
  timestamp: string;
  market_timestamp?: string;
  monitor_alive: boolean;
  discovery_alive: boolean;
  connected: boolean;
  symbol: string;
  price: number;
  balance: number;
  regime: string;
  positions: Position[];
  pending_orders: PendingOrder[];
  strategies: Strategy[];
  daily_totals: DailyTotals;
}

export interface Position {
  signal_name: string;
  family: string;
  direction: string;
  qty: number;
  entry_price: number;
  confidence?: number;
  entry_time: string;
  exit_due?: string;
  dynamic_exit?: boolean;
  exit_logic?: string;
  unrealized_pnl_pct?: number;
  bars_held?: number;
  mfe_pct?: number;
  mae_pct?: number;
}

export interface PendingOrder {
  order_id: string;
  signal_name: string;
  qty: number;
  requested_price: number;
}

export interface Strategy {
  family: string;
  name: string;
  direction: string;
  status: string;
  entry_conditions: string;
  exit_conditions: string;
  today: DailyTotals;
}

export interface DailyTotals {
  triggers: number;
  wins: number;
  not_filled: number;
  errors: number;
}

export interface TradeRow {
  tradeId: string;
  strategyId: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  status: "open" | "closed" | "cancelled";
  entryAt: Date | null;
  exitAt: Date | null;
  entryPrice: string;
  exitPrice: string | null;
  quantity: string;
  leverage: number;
  pnl: string | null;       // USD
  pnlPercent: string | null; // %
  grossReturn: string | null;
  exitReason: string | null;
  confidence: number;
  horizonMin: number;
  mfe: string | null;
  mae: string | null;
  fee: string | null;
  source?: "system" | "exchange" | "manual";
}

interface ExchangeTradeFile {
  last_sync_at: string;
  last_trade_id: number;
  trade_count: number;
  trades: ExchangeFill[];
}

interface ExchangeFill {
  exchange_trade_id: number;
  order_id: number;
  symbol: string;
  side: string;       // "BUY" | "SELL"
  price: string;
  qty: string;
  quote_qty: string;
  commission: string;
  commission_asset: string;
  realized_pnl: string;
  maker: boolean;
  buyer: boolean;
  position_side: string;
  time: number;        // ms timestamp
}

interface HiddenExchangeTradeFile {
  updated_at?: string;
  hidden_trade_ids?: number[];
  hidden_order_ids?: number[];
}

export interface PendingRule {
  id: string;
  group: string;
  status: string;
  entry: {
    feature: string;
    operator: string;
    threshold: number;
    direction: string;
    horizon: number;
  };
  combo_conditions?: Array<{ feature: string; op: string; threshold: number }>;
  exit?: Record<string, unknown>;
  stop_pct?: number;
  stats: {
    // 旧格式字段
    oos_win_rate?: number;
    n_oos: number;
    oos_pf?: number;
    oos_avg_ret?: number;
    wr_improvement?: number;
    seed_oos_wr?: number;
    // v2 格式字段 (p_mfe_gt_mae 方法论)
    p_mfe_gt_mae_oos?: number;
    p_mfe_gt_mae_is?: number;
    net_avg_pct?: number;
    mean_mfe_oos?: number;
    mean_mae_oos?: number;
    derived_stop_pct?: number;
    exit_backtest?: Record<string, unknown>;
  };
  // v2 新增字段
  discovery_mode?: string;
  time_granularity?: string;
  force_closure?: {
    entry_force?: string;
    force_category?: string;
    exit_force_linked?: boolean;
    exit_force_description?: string;
  };
  execution_params?: {
    position_pct?: number;
    probation_trades?: number;
    cooldown_minutes?: number;
  };
  family?: string;
  mechanism_type?: string;
  explanation?: string;
  rule_str?: string;
  discovered_at: string;
  rejection_reason?: string;
  approved_by?: string;
}

export interface AlertEntry {
  id: string;
  timestamp: string;
  phase: string;
  signalName: string;
  direction: "LONG" | "SHORT";
  bars: string;
  description: string;
}

export interface DevTask {
  id: number;
  category: string;
  title: string;
  description?: string;
  status: string;
  priority: string;
  layer?: string;
  sortOrder?: number;
  completedAt?: string;
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Helpers 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜?
function readJSON<T>(filePath: string): T | null {
  try {
    const raw = fs.readFileSync(filePath, "utf8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJSON(filePath: string, data: unknown): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf8");
}

function toPositiveInt(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function getHiddenExchangeTradeSets() {
  const data = readJSON<HiddenExchangeTradeFile>(P.exchangeTradeHidden) ?? {};
  const hiddenTradeIds = new Set<number>();
  const hiddenOrderIds = new Set<number>();

  for (const rawTradeId of data.hidden_trade_ids ?? []) {
    const tradeId = toPositiveInt(rawTradeId);
    if (tradeId !== null) hiddenTradeIds.add(tradeId);
  }
  for (const rawOrderId of data.hidden_order_ids ?? []) {
    const orderId = toPositiveInt(rawOrderId);
    if (orderId !== null) hiddenOrderIds.add(orderId);
  }

  return { hiddenTradeIds, hiddenOrderIds };
}

function isHiddenExchangeFill(
  fill: Pick<ExchangeFill, "exchange_trade_id" | "order_id">,
  hidden = getHiddenExchangeTradeSets(),
): boolean {
  return hidden.hiddenTradeIds.has(fill.exchange_trade_id) || hidden.hiddenOrderIds.has(fill.order_id);
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?System State 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑?
export function getSystemState(): SystemState | null {
  return readJSON<SystemState>(P.systemState);
}

function parseTradeTimestamp(raw: string): Date | null {
  const value = raw.trim();
  if (!value) return null;

  // trades.csv uses local "CST" (China Standard Time, UTC+8)
  const cstMatch = value.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) CST$/);
  if (cstMatch) {
    const d = new Date(`${cstMatch[1]}T${cstMatch[2]}+08:00`);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  if (value.endsWith(" UTC")) {
    const d = new Date(value.replace(" UTC", "Z").replace(" ", "T"));
    return Number.isNaN(d.getTime()) ? null : d;
  }

  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function parseHorizonMinFromFamily(family: string | undefined): number {
  if (!family) return 30;
  const m = family.match(/::(\d+)$/);
  if (!m) return 30;
  const n = Number.parseInt(m[1] ?? "30", 10);
  return Number.isFinite(n) && n > 0 ? n : 30;
}

function buildLiveOpenTrades(state: SystemState | null): TradeRow[] {
  if (!state?.positions?.length) return [];
  const currentPrice = state.price ?? 0;
  return state.positions.map((p, i) => {
    const entryAt = parseTradeTimestamp(p.entry_time) ?? new Date(state.timestamp);
    const direction = (p.direction ?? "").toUpperCase() === "LONG" ? "LONG" : "SHORT";
    const strategyId = p.family || p.signal_name || `LIVE-${i + 1}`;
    const tradeId = `LIVE-${p.signal_name || i + 1}-${entryAt.getTime()}`;

    // Live unrealized PnL from system_state positions
    const pnlPct = Number.isFinite(p.unrealized_pnl_pct) ? p.unrealized_pnl_pct! : null;
    const entryPriceN = Number(p.entry_price) || 0;
    const qtyN = Number(p.qty) || 0;
    const pnlUsd = pnlPct !== null && entryPriceN > 0 && qtyN > 0
      ? ((pnlPct / 100) * entryPriceN * qtyN).toFixed(4)
      : null;

    return {
      tradeId,
      strategyId,
      symbol: state.symbol || "BTCUSDT",
      direction,
      status: "open",
      entryAt,
      exitAt: null,
      entryPrice: String(p.entry_price ?? 0),
      exitPrice: currentPrice > 0 ? String(currentPrice) : null,
      quantity: String(p.qty ?? 0),
      leverage: 10,
      pnl: pnlUsd,
      pnlPercent: pnlPct !== null ? String(pnlPct) : null,
      grossReturn: null,
      exitReason: null,
      confidence: Number.isFinite(p.confidence) ? Number(p.confidence) : 0,
      horizonMin: parseHorizonMinFromFamily(p.family),
      mfe: p.mfe_pct != null ? String(p.mfe_pct) : null,
      mae: p.mae_pct != null ? String(p.mae_pct) : null,
      fee: null,
    };
  });
}

// ---- Exchange trade sync helpers ----------------------------------------

export function getExchangeTradesRaw(): ExchangeFill[] {
  const data = readJSON<ExchangeTradeFile>(P.exchangeTrades);
  const hidden = getHiddenExchangeTradeSets();
  return (data?.trades ?? []).filter((fill) => !isHiddenExchangeFill(fill, hidden));
}

export function hideExchangeOrders(orderIds: number[]): {
  hiddenOrderIds: number[];
  removedFills: number;
  remainingFills: number;
} {
  const normalizedOrderIds = Array.from(new Set(
    orderIds
      .map((orderId) => toPositiveInt(orderId))
      .filter((orderId): orderId is number => orderId !== null),
  ));
  if (normalizedOrderIds.length === 0) {
    return { hiddenOrderIds: [], removedFills: 0, remainingFills: getExchangeTradesRaw().length };
  }

  const hiddenData = readJSON<HiddenExchangeTradeFile>(P.exchangeTradeHidden) ?? {};
  const hiddenTradeIds = new Set<number>((hiddenData.hidden_trade_ids ?? []).map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0));
  const hiddenOrderIds = new Set<number>((hiddenData.hidden_order_ids ?? []).map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0));
  normalizedOrderIds.forEach((orderId) => hiddenOrderIds.add(orderId));

  writeJSON(P.exchangeTradeHidden, {
    updated_at: new Date().toISOString(),
    hidden_trade_ids: Array.from(hiddenTradeIds).sort((a, b) => a - b),
    hidden_order_ids: Array.from(hiddenOrderIds).sort((a, b) => a - b),
  });

  const exchangeData = readJSON<ExchangeTradeFile>(P.exchangeTrades);
  if (!exchangeData) {
    return { hiddenOrderIds: normalizedOrderIds, removedFills: 0, remainingFills: 0 };
  }

  const before = exchangeData.trades.length;
  exchangeData.trades = exchangeData.trades.filter((fill) => !hiddenOrderIds.has(fill.order_id) && !hiddenTradeIds.has(fill.exchange_trade_id));
  exchangeData.trade_count = exchangeData.trades.length;
  writeJSON(P.exchangeTrades, exchangeData);

  return {
    hiddenOrderIds: normalizedOrderIds,
    removedFills: before - exchangeData.trades.length,
    remainingFills: exchangeData.trades.length,
  };
}

/**
 * Return exchange fills NOT already represented in CSV rows.
 * Groups fills by orderId, deduplicates against CSV by direction+price+time.
 */
function getExchangeOnlyTrades(csvRows: TradeRow[]): TradeRow[] {
  const fills = getExchangeTradesRaw();
  if (fills.length === 0) return [];

  // Group fills by orderId
  const byOrder = new Map<number, ExchangeFill[]>();
  for (const f of fills) {
    const arr = byOrder.get(f.order_id) ?? [];
    arr.push(f);
    byOrder.set(f.order_id, arr);
  }

  // Build CSV fingerprint set for dedup (direction|price_bucket|time_bucket)
  const csvFingerprints = new Set<string>();
  for (const row of csvRows) {
    if (!row.entryAt) continue;
    const bucket = Math.floor(row.entryAt.getTime() / 300_000);
    const priceKey = Math.round(parseFloat(row.entryPrice) * 10);
    csvFingerprints.add(`${row.direction}|${priceKey}|${bucket}`);
  }

  const extra: TradeRow[] = [];
  for (const [orderId, orderFills] of Array.from(byOrder.entries())) {
    let totalQty = 0;
    let totalQuoteQty = 0;
    let totalCommission = 0;
    let totalRealizedPnl = 0;
    let latestTime = 0;
    const firstFill = orderFills[0];

    for (const f of orderFills) {
      totalQty += parseFloat(f.qty) || 0;
      totalQuoteQty += parseFloat(f.quote_qty) || 0;
      totalCommission += parseFloat(f.commission) || 0;
      totalRealizedPnl += parseFloat(f.realized_pnl) || 0;
      if (f.time > latestTime) latestTime = f.time;
    }

    const avgPrice = totalQty > 0 ? totalQuoteQty / totalQty : 0;
    const direction: "LONG" | "SHORT" = firstFill.side === "BUY" ? "LONG" : "SHORT";
    const fillDate = new Date(latestTime);

    // 出场单（realized_pnl != 0）已被 CSV 捕获为完整交易记录，跳过避免双重计数
    // 入场单（realized_pnl == 0）才需要检查是否在 CSV 中有对应记录
    if (Math.abs(totalRealizedPnl) > 0.001) continue;

    // Check if this order matches any CSV row (price + time proximity)
    const bucket = Math.floor(latestTime / 300_000);
    const priceKey = Math.round(avgPrice * 10);
    const matched = csvFingerprints.has(`${direction}|${priceKey}|${bucket}`)
      || csvFingerprints.has(`${direction}|${priceKey}|${bucket - 1}`)
      || csvFingerprints.has(`${direction}|${priceKey}|${bucket + 1}`);
    if (matched) continue;

    extra.push({
      tradeId: `EX-${orderId}`,
      strategyId: "MANUAL",
      symbol: firstFill.symbol || "BTCUSDT",
      direction,
      status: "closed",
      entryAt: fillDate,
      exitAt: fillDate,
      entryPrice: avgPrice.toFixed(2),
      exitPrice: avgPrice.toFixed(2),
      quantity: totalQty.toFixed(6),
      leverage: 10,
      pnl: totalRealizedPnl.toFixed(4),
      pnlPercent: null,
      grossReturn: null,
      exitReason: "exchange",
      confidence: 0,
      horizonMin: 0,
      mfe: null,
      mae: null,
      fee: totalCommission > 0 ? totalCommission.toFixed(6) : null,
      source: "exchange",
    });
  }
  return extra;
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Trades 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?
export function getTrades(opts: {
  limit?: number;
  status?: string;
  symbol?: string;
  direction?: string;
  strategyId?: string;
  fromDate?: Date;
} = {}): TradeRow[] {
  try {
    const raw = fs.readFileSync(P.tradesCSV, "utf8");
    const lines = raw.trim().split("\n");
    if (lines.length < 2) return [];

    const header = lines[0].split(",");
    const idx = (f: string) => header.indexOf(f);

    const rows: TradeRow[] = lines.slice(1).map((line, i) => {
      const cols = line.split(",");
      const get = (field: string) => (cols[idx(field)] ?? "").trim();

      const entryTimeStr = get("entry_time");
      const exitTimeStr  = get("exit_time");
      const netRetStr    = get("net_return_pct");
      const grossRetStr  = get("gross_return_pct");
      const entryPriceN  = parseFloat(get("entry_price")) || 0;
      const qtyN         = parseFloat(get("qty")) || 0;
      const netRetPct    = parseFloat(netRetStr) || 0;  // already in %
      const pnlUsd       = entryPriceN > 0 && qtyN > 0
        ? ((netRetPct / 100) * entryPriceN * qtyN).toFixed(4)
        : null;
      const exitReason   = get("exit_reason");
      const hasExit      = !!exitTimeStr;
      const status: "open" | "closed" | "cancelled" =
        exitReason === "not_filled" ? "cancelled"
        : hasExit ? "closed"
        : "open";

      return {
        tradeId:     get("trade_id") || String(i + 1),
        strategyId:  get("strategy_id") || get("signal_name"),
        symbol:      "BTCUSDT",
        direction:   (get("direction").toUpperCase() as "LONG" | "SHORT"),
        status,
        entryAt:     entryTimeStr ? parseTradeTimestamp(entryTimeStr) : null,
        exitAt:      exitTimeStr  ? parseTradeTimestamp(exitTimeStr) : null,
        entryPrice:  get("entry_price"),
        exitPrice:   get("exit_price") || null,
        quantity:    get("qty"),
        leverage:    10,
        pnl:         pnlUsd,
        pnlPercent:  netRetStr || null,
        grossReturn: grossRetStr || null,
        exitReason:  exitReason || null,
        confidence:  parseInt(get("confidence") || "0"),
        horizonMin:  parseInt(get("horizon_min") || "0"),
        mfe:         null,
        mae:         null,
        fee:         null,
      };
    });

    const liveOpenTrades = buildLiveOpenTrades(getSystemState());
    const closedOrCancelled = rows.filter(r => r.status !== "open");

    // Merge exchange fills that are NOT already in CSV
    const exchangeOnly = getExchangeOnlyTrades(closedOrCancelled);

    // Apply filters
    let result = rows;
    if (opts.status === "open") {
      result = liveOpenTrades;
    } else if (!opts.status) {
      result = [...liveOpenTrades, ...closedOrCancelled, ...exchangeOnly];
    } else if (opts.status === "closed") {
      result = [...closedOrCancelled, ...exchangeOnly];
    }
    const symbol = opts.symbol?.toUpperCase();
    const direction = opts.direction?.toUpperCase();
    const strategyId = opts.strategyId?.toLowerCase();
    const fromDate = opts.fromDate;
    if (opts.status) result = result.filter(r => r.status === opts.status);
    if (symbol) result = result.filter(r => r.symbol === symbol);
    if (direction) result = result.filter(r => r.direction === direction);
    if (strategyId) result = result.filter(r => r.strategyId.toLowerCase().includes(strategyId));
    if (fromDate) result = result.filter(r => r.entryAt && r.entryAt >= fromDate);
    return result.reverse().slice(0, opts.limit ?? 100);
  } catch {
    return [];
  }
}

export function getTradeStats() {
  const allClosed = getTrades({ status: "closed", limit: 10000 });
  const allOpen   = getTrades({ status: "open",   limit: 10000 });

  const totalPnl = allClosed.reduce((s, t) => s + parseFloat(t.pnl ?? "0"), 0);
  const wins     = allClosed.filter(t => parseFloat(t.pnl ?? "0") > 0).length;
  const winRate  = allClosed.length > 0 ? (wins / allClosed.length) * 100 : 0;
  const avgReturn = allClosed.length > 0 ? totalPnl / allClosed.length : 0;

  // Max drawdown
  let maxDD = 0, peak = 0, cumPnl = 0;
  for (const t of allClosed) {
    cumPnl += parseFloat(t.pnl ?? "0");
    if (cumPnl > peak) peak = cumPnl;
    const dd = peak > 0 ? ((cumPnl - peak) / peak) * 100 : 0;
    if (dd < maxDD) maxDD = dd;
  }

  // Today's PnL (UTC+8 calendar day, not rolling 24h)
  const nowUtc8 = new Date(Date.now() + 8 * 3600000);
  const todayStr = nowUtc8.toISOString().slice(0, 10);
  const todayPnl = allClosed
    .filter(t => {
      if (!t.exitAt) return false;
      const exitUtc8 = new Date(t.exitAt.getTime() + 8 * 3600000);
      return exitUtc8.toISOString().slice(0, 10) === todayStr;
    })
    .reduce((s, t) => s + parseFloat(t.pnl ?? "0"), 0);

  return {
    totalTrades:  allClosed.length,
    openTrades:   allOpen.length,
    totalPnl:     totalPnl.toFixed(4),
    winRate:      winRate.toFixed(1),
    avgReturn:    avgReturn.toFixed(4),
    maxDrawdown:  maxDD.toFixed(2),
    todayPnl:     todayPnl.toFixed(4),
  };
}

// Equity curve and daily PnL for Dashboard charts
export function getChartData(days = 7) {
  const allClosed = getTrades({ status: "closed", limit: 10000 });
  const state     = getSystemState();
  const currentBalance = state?.balance ?? 0;

  // Compute total historical PnL from CSV
  const totalHistoricalPnl = allClosed.reduce((s, t) => s + parseFloat(t.pnl ?? "0"), 0);
  const baselineEquity = currentBalance - totalHistoricalPnl;

  // Group closed trades by UTC date string (YYYY-MM-DD)
  const pnlByDate: Record<string, number> = {};
  for (const t of allClosed) {
    if (!t.exitAt) continue;
    const dateKey = t.exitAt.toISOString().slice(0, 10);
    pnlByDate[dateKey] = (pnlByDate[dateKey] ?? 0) + parseFloat(t.pnl ?? "0");
  }

  // Build last `days` calendar days
  const equityCurve: Array<{ t: string; v: number }> = [];
  const dailyPnl: Array<{ d: string; pnl: number }> = [];
  const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  let runningEquity = baselineEquity;
  const today = new Date();
  const dateList: string[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    dateList.push(d.toISOString().slice(0, 10));
  }

  for (const dateKey of dateList) {
    runningEquity += pnlByDate[dateKey] ?? 0;
    equityCurve.push({ t: dateKey.slice(5), v: parseFloat(runningEquity.toFixed(2)) });
    const d = new Date(dateKey + "T00:00:00Z");
    dailyPnl.push({ d: DAY_LABELS[d.getUTCDay()]!, pnl: parseFloat((pnlByDate[dateKey] ?? 0).toFixed(4)) });
  }

  return { equityCurve, dailyPnl };
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Alpha Rules 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜?
export function getPendingRules(statusFilter?: string): PendingRule[] {
  const rules = readJSON<PendingRule[]>(P.pendingRules) ?? [];
  if (!statusFilter) return rules;
  return rules.filter(r => r.status === statusFilter);
}

export function getApprovedRules(): unknown[] {
  return readJSON<unknown[]>(P.approvedRules) ?? [];
}

/** 读取 exit_policy_best_params.json，返回 family|direction -> {stop_pct} 映射 */
export function getBestExitParams(): Record<string, { stop_pct?: number }> {
  return readJSON<Record<string, { stop_pct?: number }>>(P.exitBestParams) ?? {};
}

/** 返回 approved_rules.json 中所有 v2 引擎自动发现的卡片 (有 discovery_mode 字段的)
 *  包括 MidFreq (A6), HighFreq (A7), Cascade (H-FLOW/H-VACUUM).
 *  v2 卡片直接写入 approved_rules.json，不经过 pending_rules.json 流程.
 */
export function getApprovedV2Rules(): PendingRule[] {
  const all = readJSON<PendingRule[]>(P.approvedRules) ?? [];
  return all.filter(r => r.discovery_mode && r.discovery_mode !== "");
}

/** 合并 pending_rules.json + approved_rules.json 中 v2 卡片，统一暴露给 getCandidates API */
export function getAllCandidates(statusFilter?: string): PendingRule[] {
  const pending = readJSON<PendingRule[]>(P.pendingRules) ?? [];
  const v2approved = getApprovedV2Rules();
  const all = [...pending, ...v2approved];
  if (!statusFilter) return all;
  return all.filter(r => r.status === statusFilter);
}

export function approveRule(candidateId: string): boolean {
  const pending = readJSON<PendingRule[]>(P.pendingRules) ?? [];
  const rule = pending.find(r => r.id === candidateId);
  if (!rule) return false;

  // Update status in pending_rules.json
  const newPending = pending.map(r =>
    r.id === candidateId ? { ...r, status: "approved" } : r
  );
  writeJSON(P.pendingRules, newPending);

  // Append to approved_rules.json (Python alpha_rules.py hot-reloads this file)
  const approved = readJSON<unknown[]>(P.approvedRules) ?? [];
  approved.push({ ...rule, status: "approved", approved_at: new Date().toISOString() });
  writeJSON(P.approvedRules, approved);

  return true;
}

export function rejectRule(candidateId: string, reason?: string): boolean {
  const pending = readJSON<PendingRule[]>(P.pendingRules) ?? [];
  const newPending = pending.map(r =>
    r.id === candidateId
      ? { ...r, status: "rejected", rejection_reason: reason ?? "Rejected by reviewer", rejected_at: new Date().toISOString() }
      : r
  );
  writeJSON(P.pendingRules, newPending);
  return true;
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Alerts Log 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?
export function getAlertsLog(limit = 50): AlertEntry[] {
  try {
    const raw = fs.readFileSync(P.alertsLog, "utf8");
    const lines = raw.split("\n");
    const alerts: AlertEntry[] = [];

    for (let i = lines.length - 1; i >= 0 && alerts.length < limit; i--) {
      const line = lines[i].trim();
      if (!line.includes("SIGNAL ALERT")) continue;

      // Format: SIGNAL ALERT  2026-03-28 11:53:00 UTC  [P1]  SIGNAL_NAME  LONG  30bars  description
      const m = line.match(
        /SIGNAL ALERT\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+\[(\w+)\]\s+(\S+)\s+(LONG|SHORT)\s+(\d+bars)\s*(.*)/
      );
      if (!m) continue;

      alerts.push({
        id:         `alert-${i}`,
        timestamp:  m[1]!,
        phase:      m[2]!,
        signalName: m[3]!,
        direction:  m[4] as "LONG" | "SHORT",
        bars:       m[5]!,
        description: (m[6] ?? "").trim(),
      });
    }

    return alerts;
  } catch {
    return [];
  }
}

// Returns alerts newer than a given log line index (for WebSocket tailing)
let _lastAlertLineIndex = -1;

export function getNewAlerts(): AlertEntry[] {
  try {
    const raw = fs.readFileSync(P.alertsLog, "utf8");
    const lines = raw.split("\n");
    const newAlerts: AlertEntry[] = [];

    if (_lastAlertLineIndex < 0) {
      _lastAlertLineIndex = lines.length;
      return [];
    }

    for (let i = _lastAlertLineIndex; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line.includes("SIGNAL ALERT")) continue;
      const m = line.match(
        /SIGNAL ALERT\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+\[(\w+)\]\s+(\S+)\s+(LONG|SHORT)\s+(\d+bars)\s*(.*)/
      );
      if (!m) continue;
      newAlerts.push({
        id:         `alert-${i}`,
        timestamp:  m[1]!,
        phase:      m[2]!,
        signalName: m[3]!,
        direction:  m[4] as "LONG" | "SHORT",
        bars:       m[5]!,
        description: (m[6] ?? "").trim(),
      });
    }

    _lastAlertLineIndex = lines.length;
    return newAlerts;
  } catch {
    return [];
  }
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?API Config (.env) 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕
export function getEnvConfig(): { apiKey: string; apiSecret: string; hasConfig: boolean; isTestnet: boolean } {
  try {
    const raw = fs.readFileSync(P.envFile, "utf8");
    let apiKey = "", apiSecret = "";
    for (const line of raw.split("\n")) {
      const t = line.trim();
      if (t.startsWith("BINANCE_TESTNET_API_KEY="))    apiKey    = t.split("=").slice(1).join("=").replace(/^["']|["']$/g, "");
      if (t.startsWith("BINANCE_TESTNET_API_SECRET=")) apiSecret = t.split("=").slice(1).join("=").replace(/^["']|["']$/g, "");
    }
    return { apiKey, apiSecret, hasConfig: !!(apiKey && apiSecret), isTestnet: true };
  } catch {
    return { apiKey: "", apiSecret: "", hasConfig: false, isTestnet: true };
  }
}

export function saveEnvConfig(apiKey: string, apiSecret: string): void {
  let raw = "";
  try { raw = fs.readFileSync(P.envFile, "utf8"); } catch {}

  const lines = raw ? raw.split("\n") : [];
  const out: string[] = [];
  let foundKey = false, foundSecret = false;

  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith("BINANCE_TESTNET_API_KEY="))    { out.push(`BINANCE_TESTNET_API_KEY=${apiKey}`);    foundKey    = true; }
    else if (t.startsWith("BINANCE_TESTNET_API_SECRET=")) { out.push(`BINANCE_TESTNET_API_SECRET=${apiSecret}`); foundSecret = true; }
    else { out.push(line); }
  }

  if (!foundKey)    out.push(`BINANCE_TESTNET_API_KEY=${apiKey}`);
  if (!foundSecret) out.push(`BINANCE_TESTNET_API_SECRET=${apiSecret}`);

  fs.writeFileSync(P.envFile, out.join("\n"), "utf8");
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?Dev Tasks 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕
export function getDevTasks(): DevTask[] {
  return readJSON<DevTask[]>(P.devTasks) ?? [];
}

export function updateDevTask(id: number, status: string): void {
  const tasks = getDevTasks();
  const i = tasks.findIndex(t => t.id === id);
  if (i < 0) return;
  tasks[i].status = status;
  if (status === "completed") tasks[i].completedAt = new Date().toISOString();
  writeJSON(P.devTasks, tasks);
}

export function insertDevTask(task: Omit<DevTask, "id">): void {
  const tasks = getDevTasks();
  const maxId = tasks.reduce((m, t) => Math.max(m, t.id), 0);
  tasks.push({ ...task, id: maxId + 1 });
  writeJSON(P.devTasks, tasks);
}

// 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?System health derived from system_state.json 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕
export function getSystemHealth() {
  const state = getSystemState();
  const now = Date.now();
  const stateAge = state ? now - new Date(state.timestamp).getTime() : Infinity;
  const stateStale = stateAge > 3 * 60 * 1000; // >3 min = stale

  const monitorOk = !!state?.monitor_alive && !stateStale;
  const connectedOk = !!state?.connected;
  const discoveryOn = !!state?.discovery_alive;

  // Fill rate: compute from recent trades
  const recent = getTrades({ limit: 50 });
  const filled = recent.filter(t => t.status !== "cancelled").length;
  const fillRate = recent.length > 0 ? filled / recent.length : 0;
  const fillRateWarning = fillRate < 0.70 && recent.length > 5;

  let overall = 40;
  let status: "error" | "warning" | "healthy" = "error";

  if (monitorOk) {
    if (!connectedOk) {
      overall = 70;
      status = "warning";
    } else if (fillRateWarning) {
      overall = 82;
      status = "warning";
    } else {
      overall = 95;
      status = "healthy";
    }
  }

  return {
    overall,
    status,
    layers: {
      data: {
        status: !monitorOk ? "error" : connectedOk ? "healthy" : "warning",
        websocket: { connected: connectedOk ? 4 : 0, total: 4, streams: ["klines", "liquidations", "book_ticker", "mark_price"] },
        dataIntegrity: { status: monitorOk ? (connectedOk ? "healthy" : "warning") : "stale", missingPct: stateStale ? 0.5 : 0.02 },
        stateAgeSeconds: Math.floor(stateAge / 1000),
      },
      features: {
        status: monitorOk ? "healthy" : "error",
        computed: 52,
        total: 52,
        nanRate: 0.02,
        dimensions: ["PRICE", "TRADE_FLOW", "LIQUIDITY", "POSITIONING", "MICROSTRUCTURE", "MARK_PRICE"],
        latencyMs: monitorOk ? 8 : 0,
        regime: state?.regime ?? "UNKNOWN",
      },
      signals: {
        status: monitorOk ? "healthy" : "error",
        p1Running: monitorOk ? 8 : 0,
        p1Total: 8,
        p2Running: discoveryOn ? 1 : 0,
        p2Total: 1,
        fatigue: false,
        todayTriggers: state?.daily_totals?.triggers ?? 0,
      },
      execution: {
        status: fillRateWarning ? "warning" : "healthy",
        engineActive: monitorOk,
        fillRate,
        fillRateTarget: 0.70,
        exitTracking: true,
        warning: fillRateWarning ? `Fill rate ${(fillRate * 100).toFixed(0)}% < 70%. Check maker pricing distance and order timeout settings.` : null,
      },
    },
    issues: [
      ...(stateStale ? [{ severity: "error", message: "system_state.json is stale. Check run_monitor.py.", action: "Restart watchdog.py" }] : []),
      ...(monitorOk && !connectedOk ? [{ severity: "warning", message: "Market data connection is degraded. Check the Binance websocket streams and collector process.", action: "Inspect the Binance data feeds" }] : []),
      ...(fillRateWarning ? [{ severity: "warning", message: `Fill rate ${(fillRate * 100).toFixed(0)}% < 70%. Review maker-only quote distance, timeout, and reprice policy.`, action: "Tune maker order placement and timeout settings" }] : []),
    ],
    lastUpdated: state?.timestamp ?? new Date().toISOString(),
  };
}

// ── LLM Promoter Engine ───────────────────────────────────────────────────────

export interface EngineState {
  status: string;
  last_run_at: string;
  next_run_at: string;
  error?: string;
  llm_config?: {
    model: string;
    base_url: string;
    api_key_hint: string;
  };
  thresholds?: {
    auto_approve: number;
    review_queue: number;
  };
  stats?: {
    pending_count: number;
    approved_count: number;
    rejected_count: number;
    review_count: number;
    total_approved_this_session: number;
    total_rejected_this_session: number;
    last_run_summary?: Record<string, number>;
  };
  recent_decisions?: Array<{
    id: string;
    rule_str: string;
    direction?: string;
    oos_wr?: number;
    n_oos?: number;
    confidence?: number;
    mechanism_type?: string;
    mechanism_display_name?: string;
    is_valid?: boolean;
    decision: string;
    decided_at: string;
  }>;
  force_library_summary?: Array<{
    mechanism_type: string;
    display_name: string;
    category: string;
    category_name: string;
    essence: string;
    validated_by: string[];
    llm_confidence: number;
  }>;
}

export function getEngineState(): EngineState {
  return readJSON<EngineState>(P.engineState) ?? {
    status: "idle",
    last_run_at: "",
    next_run_at: "",
    stats: { pending_count: 0, approved_count: 0, rejected_count: 0, review_count: 0, total_approved_this_session: 0, total_rejected_this_session: 0 },
    recent_decisions: [],
    force_library_summary: [],
  };
}

export function getReviewQueue(): PendingRule[] {
  return readJSON<PendingRule[]>(P.reviewQueue) ?? [];
}

export function getRejectedRules(): unknown[] {
  return readJSON<unknown[]>(P.rejectedRules) ?? [];
}

export function getPromoterConfig(): Record<string, unknown> {
  return readJSON<Record<string, unknown>>(P.promoterConfig) ?? {};
}

export function savePromoterConfig(updates: {
  apiKey?: string;
  model?: string;
  baseUrl?: string;
  autoApprove?: number;
  reviewQueue?: number;
}): void {
  const cfg = getPromoterConfig();
  const llm = (cfg.llm as Record<string, unknown>) ?? {};
  const thr = (cfg.thresholds as Record<string, unknown>) ?? {};
  if (updates.apiKey !== undefined) llm.api_key = updates.apiKey;
  if (updates.model !== undefined) llm.model = updates.model;
  if (updates.baseUrl !== undefined) llm.base_url = updates.baseUrl;
  if (updates.autoApprove !== undefined) thr.auto_approve = updates.autoApprove;
  if (updates.reviewQueue !== undefined) thr.review_queue = updates.reviewQueue;
  cfg.llm = llm;
  cfg.thresholds = thr;
  writeJSON(P.promoterConfig, cfg);
}

/** Move a rule from review_queue.json to approved_rules.json */
export function promoterApprove(ruleId: string): boolean {
  const review = getReviewQueue();
  const target = review.find(r => r.id === ruleId);
  if (!target) {
    // Try pending_rules too
    return approveRule(ruleId);
  }
  const remaining = review.filter(r => r.id !== ruleId);
  writeJSON(P.reviewQueue, remaining);
  const approved = getApprovedRules() as unknown[];
  approved.push({ ...(target as object), status: "approved", approved_at: new Date().toISOString(), approved_by: "human_manual" });
  writeJSON(P.approvedRules, approved);
  return true;
}

/** Move a rule from review_queue.json to rejected_rules.json */
export function promoterReject(ruleId: string): boolean {
  const review = getReviewQueue();
  const target = review.find(r => r.id === ruleId);
  if (!target) {
    return rejectRule(ruleId, "Rejected by reviewer via dashboard");
  }
  const remaining = review.filter(r => r.id !== ruleId);
  writeJSON(P.reviewQueue, remaining);
  const rejected = getRejectedRules();
  rejected.push({ ...(target as object), status: "human_rejected", rejected_at: new Date().toISOString() });
  writeJSON(P.rejectedRules, rejected);
  return true;
}

export function getForceLibraryState(): any {
  return readJSON<any>(P.forceLibraryState) ?? null;
}
