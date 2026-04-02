import {
  bigint,
  boolean,
  decimal,
  float,
  int,
  json,
  mysqlEnum,
  mysqlTable,
  text,
  timestamp,
  varchar,
} from "drizzle-orm/mysql-core";

// ─── Users (auth) ───────────────────────────────────────────────────────────
export const users = mysqlTable("users", {
  id: int("id").autoincrement().primaryKey(),
  openId: varchar("openId", { length: 64 }).notNull().unique(),
  name: text("name"),
  email: varchar("email", { length: 320 }),
  loginMethod: varchar("loginMethod", { length: 64 }),
  role: mysqlEnum("role", ["user", "admin"]).default("user").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  lastSignedIn: timestamp("lastSignedIn").defaultNow().notNull(),
});
export type User = typeof users.$inferSelect;
export type InsertUser = typeof users.$inferInsert;

// ─── API Configuration ───────────────────────────────────────────────────────
export const apiConfigs = mysqlTable("api_configs", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  exchange: varchar("exchange", { length: 32 }).default("binance").notNull(),
  apiKey: varchar("apiKey", { length: 256 }),
  apiSecret: varchar("apiSecret", { length: 256 }),
  isTestnet: boolean("isTestnet").default(false).notNull(),
  isActive: boolean("isActive").default(false).notNull(),
  lastTestedAt: timestamp("lastTestedAt"),
  lastTestStatus: mysqlEnum("lastTestStatus", ["success", "failed", "pending"]).default("pending"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});
export type ApiConfig = typeof apiConfigs.$inferSelect;

// ─── Trading Pairs ───────────────────────────────────────────────────────────
export const tradingPairs = mysqlTable("trading_pairs", {
  id: int("id").autoincrement().primaryKey(),
  symbol: varchar("symbol", { length: 32 }).notNull().unique(),
  baseAsset: varchar("baseAsset", { length: 16 }).notNull(),
  quoteAsset: varchar("quoteAsset", { length: 16 }).notNull(),
  isActive: boolean("isActive").default(true).notNull(),
  isTracked: boolean("isTracked").default(false).notNull(),
  dataCollectionStatus: mysqlEnum("dataCollectionStatus", [
    "pending", "downloading", "completed", "failed"
  ]).default("pending").notNull(),
  dataDownloadProgress: int("dataDownloadProgress").default(0),
  alphaEngineStatus: mysqlEnum("alphaEngineStatus", [
    "idle", "scanning", "mining", "completed", "error"
  ]).default("idle").notNull(),
  lastDataUpdate: timestamp("lastDataUpdate"),
  totalKlines: bigint("totalKlines", { mode: "number" }).default(0),
  dataQualityScore: float("dataQualityScore").default(0),
  currentPrice: decimal("currentPrice", { precision: 20, scale: 8 }),
  priceChange24h: float("priceChange24h").default(0),
  volume24h: decimal("volume24h", { precision: 30, scale: 8 }),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});
export type TradingPair = typeof tradingPairs.$inferSelect;

// ─── Strategies ──────────────────────────────────────────────────────────────
export const strategies = mysqlTable("strategies", {
  id: int("id").autoincrement().primaryKey(),
  strategyId: varchar("strategyId", { length: 64 }).notNull().unique(),
  name: varchar("name", { length: 128 }).notNull(),
  type: mysqlEnum("type", ["P1", "P2", "ALPHA"]).notNull(),
  direction: mysqlEnum("direction", ["LONG", "SHORT", "BOTH"]).notNull(),
  symbol: varchar("symbol", { length: 32 }).default("BTCUSDT").notNull(),
  entryCondition: text("entryCondition"),
  entryConditionJson: json("entryConditionJson"),
  exitConditionTop3: json("exitConditionTop3"),
  oosWinRate: float("oosWinRate"),
  oosAvgReturn: float("oosAvgReturn"),
  isSampleSize: int("isSampleSize").default(0),
  oosSampleSize: int("oosSampleSize").default(0),
  overfitScore: float("overfitScore").default(0),
  featureDiversityScore: float("featureDiversityScore").default(0),
  confidenceScore: float("confidenceScore").default(0),
  triggerCount7d: int("triggerCount7d").default(0),
  tradeCount7d: int("tradeCount7d").default(0),
  pnl7d: decimal("pnl7d", { precision: 20, scale: 8 }).default("0"),
  totalTrades: int("totalTrades").default(0),
  totalPnl: decimal("totalPnl", { precision: 20, scale: 8 }).default("0"),
  status: mysqlEnum("status", ["active", "paused", "degraded", "retired", "pending"]).default("pending").notNull(),
  discoveredAt: timestamp("discoveredAt").defaultNow(),
  approvedAt: timestamp("approvedAt"),
  lastBacktestAt: timestamp("lastBacktestAt"),
  backtestStatus: mysqlEnum("backtestStatus", ["idle", "running", "completed", "failed"]).default("idle"),
  backtestResult: json("backtestResult"),
  params: json("params"),
  tags: json("tags"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});
export type Strategy = typeof strategies.$inferSelect;

// ─── Alpha Candidates ────────────────────────────────────────────────────────
export const alphaCandidates = mysqlTable("alpha_candidates", {
  id: int("id").autoincrement().primaryKey(),
  candidateId: varchar("candidateId", { length: 64 }).notNull().unique(),
  symbol: varchar("symbol", { length: 32 }).default("BTCUSDT").notNull(),
  direction: mysqlEnum("direction", ["LONG", "SHORT"]).notNull(),
  seedCondition: text("seedCondition"),
  confirmConditions: json("confirmConditions"),
  fullExpression: text("fullExpression"),
  oosWinRate: float("oosWinRate").notNull(),
  oosAvgReturn: float("oosAvgReturn").notNull(),
  sampleSize: int("sampleSize").default(0),
  icScore: float("icScore"),
  featureDimensions: json("featureDimensions"),
  exitConditionTop3: json("exitConditionTop3"),
  estimatedDailyTriggers: float("estimatedDailyTriggers").default(0),
  confidenceScore: float("confidenceScore").default(0),
  overfitScore: float("overfitScore").default(0),
  status: mysqlEnum("status", ["pending", "approved", "rejected", "expired"]).default("pending").notNull(),
  approvedAt: timestamp("approvedAt"),
  rejectedAt: timestamp("rejectedAt"),
  rejectionReason: text("rejectionReason"),
  discoveredAt: timestamp("discoveredAt").defaultNow().notNull(),
  backtestStatus: mysqlEnum("backtestStatus", ["idle", "running", "completed", "failed"]).default("idle"),
  backtestResult: json("backtestResult"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});
export type AlphaCandidate = typeof alphaCandidates.$inferSelect;

// ─── Trades ──────────────────────────────────────────────────────────────────
export const trades = mysqlTable("trades", {
  id: int("id").autoincrement().primaryKey(),
  tradeId: varchar("tradeId", { length: 64 }).notNull().unique(),
  symbol: varchar("symbol", { length: 32 }).notNull(),
  strategyId: varchar("strategyId", { length: 64 }),
  direction: mysqlEnum("direction", ["LONG", "SHORT"]).notNull(),
  entryPrice: decimal("entryPrice", { precision: 20, scale: 8 }).notNull(),
  exitPrice: decimal("exitPrice", { precision: 20, scale: 8 }),
  quantity: decimal("quantity", { precision: 20, scale: 8 }).notNull(),
  leverage: int("leverage").default(1),
  pnl: decimal("pnl", { precision: 20, scale: 8 }),
  pnlPercent: float("pnlPercent"),
  fee: decimal("fee", { precision: 20, scale: 8 }).default("0"),
  mfe: float("mfe"),
  mae: float("mae"),
  exitReason: varchar("exitReason", { length: 128 }),
  exitConditionTriggered: varchar("exitConditionTriggered", { length: 256 }),
  status: mysqlEnum("status", ["open", "closed", "cancelled"]).default("open").notNull(),
  entryOrderId: varchar("entryOrderId", { length: 64 }),
  exitOrderId: varchar("exitOrderId", { length: 64 }),
  entryAt: timestamp("entryAt").notNull(),
  exitAt: timestamp("exitAt"),
  holdingMinutes: int("holdingMinutes"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});
export type Trade = typeof trades.$inferSelect;

// ─── System Events ───────────────────────────────────────────────────────────
export const systemEvents = mysqlTable("system_events", {
  id: int("id").autoincrement().primaryKey(),
  eventType: mysqlEnum("eventType", [
    "signal_triggered", "trade_opened", "trade_closed",
    "alpha_discovered", "alpha_approved", "alpha_rejected",
    "backtest_completed", "system_error", "system_warning",
    "ws_connected", "ws_disconnected", "data_download_progress",
    "engine_started", "engine_stopped"
  ]).notNull(),
  symbol: varchar("symbol", { length: 32 }),
  strategyId: varchar("strategyId", { length: 64 }),
  severity: mysqlEnum("severity", ["info", "warning", "error", "critical"]).default("info").notNull(),
  title: varchar("title", { length: 256 }).notNull(),
  message: text("message"),
  metadata: json("metadata"),
  isRead: boolean("isRead").default(false).notNull(),
  occurredAt: timestamp("occurredAt").defaultNow().notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});
export type SystemEvent = typeof systemEvents.$inferSelect;

// ─── Alpha Engine Runs ───────────────────────────────────────────────────────
export const alphaEngineRuns = mysqlTable("alpha_engine_runs", {
  id: int("id").autoincrement().primaryKey(),
  runId: varchar("runId", { length: 64 }).notNull().unique(),
  symbol: varchar("symbol", { length: 32 }).notNull(),
  status: mysqlEnum("status", ["running", "completed", "failed", "cancelled"]).default("running").notNull(),
  phase: mysqlEnum("phase", [
    "data_download", "ic_scan", "atom_mining", "combo_scan",
    "walk_forward", "exit_mining", "completed"
  ]).default("data_download").notNull(),
  progress: int("progress").default(0),
  featuresScanned: int("featuresScanned").default(0),
  candidatesFound: int("candidatesFound").default(0),
  candidatesApproved: int("candidatesApproved").default(0),
  icScanResults: json("icScanResults"),
  params: json("params"),
  errorMessage: text("errorMessage"),
  startedAt: timestamp("startedAt").defaultNow().notNull(),
  completedAt: timestamp("completedAt"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});
export type AlphaEngineRun = typeof alphaEngineRuns.$inferSelect;

// ─── Development Tasks ───────────────────────────────────────────────────────
export const devTasks = mysqlTable("dev_tasks", {
  id: int("id").autoincrement().primaryKey(),
  category: varchar("category", { length: 64 }).notNull(),
  title: varchar("title", { length: 256 }).notNull(),
  description: text("description"),
  status: mysqlEnum("status", ["completed", "in_progress", "pending", "blocked"]).default("pending").notNull(),
  priority: mysqlEnum("priority", ["critical", "high", "medium", "low"]).default("medium").notNull(),
  layer: varchar("layer", { length: 64 }),
  completedAt: timestamp("completedAt"),
  estimatedHours: float("estimatedHours"),
  actualHours: float("actualHours"),
  notes: text("notes"),
  sortOrder: int("sortOrder").default(0),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});
export type DevTask = typeof devTasks.$inferSelect;

// ─── Wallet Snapshots ────────────────────────────────────────────────────────
export const walletSnapshots = mysqlTable("wallet_snapshots", {
  id: int("id").autoincrement().primaryKey(),
  totalEquity: decimal("totalEquity", { precision: 20, scale: 8 }),
  availableBalance: decimal("availableBalance", { precision: 20, scale: 8 }),
  usedMargin: decimal("usedMargin", { precision: 20, scale: 8 }),
  unrealizedPnl: decimal("unrealizedPnl", { precision: 20, scale: 8 }),
  assets: json("assets"),
  snapshotAt: timestamp("snapshotAt").defaultNow().notNull(),
});
export type WalletSnapshot = typeof walletSnapshots.$inferSelect;
