import { and, desc, eq, gte, like, or } from "drizzle-orm";
import { drizzle } from "drizzle-orm/mysql2";
import {
  alphaCandidates,
  alphaEngineRuns,
  apiConfigs,
  devTasks,
  InsertUser,
  strategies,
  systemEvents,
  trades,
  tradingPairs,
  users,
  walletSnapshots,
} from "../drizzle/schema";
import { ENV } from "./_core/env";

let _db: ReturnType<typeof drizzle> | null = null;

export async function getDb() {
  if (!_db && process.env.DATABASE_URL) {
    try {
      _db = drizzle(process.env.DATABASE_URL);
    } catch (error) {
      console.warn("[Database] Failed to connect:", error);
      _db = null;
    }
  }
  return _db;
}

// ─── Users ───────────────────────────────────────────────────────────────────
export async function upsertUser(user: InsertUser): Promise<void> {
  if (!user.openId) throw new Error("User openId is required for upsert");
  const db = await getDb();
  if (!db) { console.warn("[Database] Cannot upsert user: database not available"); return; }
  try {
    const values: InsertUser = { openId: user.openId };
    const updateSet: Record<string, unknown> = {};
    const textFields = ["name", "email", "loginMethod"] as const;
    type TextField = (typeof textFields)[number];
    const assignNullable = (field: TextField) => {
      const value = user[field];
      if (value === undefined) return;
      const normalized = value ?? null;
      values[field] = normalized;
      updateSet[field] = normalized;
    };
    textFields.forEach(assignNullable);
    if (user.lastSignedIn !== undefined) { values.lastSignedIn = user.lastSignedIn; updateSet.lastSignedIn = user.lastSignedIn; }
    if (user.role !== undefined) { values.role = user.role; updateSet.role = user.role; }
    else if (user.openId === ENV.ownerOpenId) { values.role = "admin"; updateSet.role = "admin"; }
    if (!values.lastSignedIn) values.lastSignedIn = new Date();
    if (Object.keys(updateSet).length === 0) updateSet.lastSignedIn = new Date();
    await db.insert(users).values(values).onDuplicateKeyUpdate({ set: updateSet });
  } catch (error) { console.error("[Database] Failed to upsert user:", error); throw error; }
}

export async function getUserByOpenId(openId: string) {
  const db = await getDb();
  if (!db) return undefined;
  const result = await db.select().from(users).where(eq(users.openId, openId)).limit(1);
  return result.length > 0 ? result[0] : undefined;
}

// ─── API Config ───────────────────────────────────────────────────────────────
export async function getApiConfig(userId: number) {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(apiConfigs).where(eq(apiConfigs.userId, userId)).limit(1);
  return result[0] ?? null;
}

export async function upsertApiConfig(userId: number, data: {
  apiKey?: string; apiSecret?: string; isTestnet?: boolean; isActive?: boolean;
  lastTestedAt?: Date; lastTestStatus?: "success" | "failed" | "pending";
}) {
  const db = await getDb();
  if (!db) return;
  const existing = await getApiConfig(userId);
  if (existing) {
    await db.update(apiConfigs).set(data).where(eq(apiConfigs.userId, userId));
  } else {
    await db.insert(apiConfigs).values({ userId, ...data });
  }
}

// ─── Trading Pairs ────────────────────────────────────────────────────────────
export async function getTradingPairs() {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(tradingPairs).orderBy(desc(tradingPairs.isTracked), tradingPairs.symbol);
}

export async function getTradingPair(symbol: string) {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(tradingPairs).where(eq(tradingPairs.symbol, symbol)).limit(1);
  return result[0] ?? null;
}

export async function upsertTradingPair(data: {
  symbol: string; baseAsset: string; quoteAsset: string;
  isTracked?: boolean; dataCollectionStatus?: "pending" | "downloading" | "completed" | "failed";
  dataDownloadProgress?: number; alphaEngineStatus?: "idle" | "scanning" | "mining" | "completed" | "error";
  currentPrice?: string; priceChange24h?: number; volume24h?: string;
  lastDataUpdate?: Date; totalKlines?: number; dataQualityScore?: number;
}) {
  const db = await getDb();
  if (!db) return;
  await db.insert(tradingPairs).values(data).onDuplicateKeyUpdate({ set: data });
}

export async function updateTradingPairStatus(symbol: string, updates: Partial<{
  dataCollectionStatus: "pending" | "downloading" | "completed" | "failed";
  dataDownloadProgress: number;
  alphaEngineStatus: "idle" | "scanning" | "mining" | "completed" | "error";
  lastDataUpdate: Date; totalKlines: number; dataQualityScore: number;
  currentPrice: string; priceChange24h: number; volume24h: string;
}>) {
  const db = await getDb();
  if (!db) return;
  await db.update(tradingPairs).set(updates).where(eq(tradingPairs.symbol, symbol));
}

// ─── Strategies ───────────────────────────────────────────────────────────────
export async function getStrategies(filters?: {
  type?: "P1" | "P2" | "ALPHA"; status?: string; symbol?: string; search?: string;
}) {
  const db = await getDb();
  if (!db) return [];
  let query = db.select().from(strategies).$dynamic();
  const conditions = [];
  if (filters?.type) conditions.push(eq(strategies.type, filters.type));
  if (filters?.status) conditions.push(eq(strategies.status, filters.status as any));
  if (filters?.symbol) conditions.push(eq(strategies.symbol, filters.symbol));
  if (filters?.search) conditions.push(or(like(strategies.name, `%${filters.search}%`), like(strategies.strategyId, `%${filters.search}%`)));
  if (conditions.length > 0) query = query.where(and(...conditions));
  return query.orderBy(desc(strategies.oosWinRate));
}

export async function getStrategy(strategyId: string) {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(strategies).where(eq(strategies.strategyId, strategyId)).limit(1);
  return result[0] ?? null;
}

export async function updateStrategyStatus(strategyId: string, status: "active" | "paused" | "degraded" | "retired") {
  const db = await getDb();
  if (!db) return;
  await db.update(strategies).set({ status }).where(eq(strategies.strategyId, strategyId));
}

export async function updateStrategyBacktest(strategyId: string, data: {
  backtestStatus: "idle" | "running" | "completed" | "failed";
  backtestResult?: unknown; lastBacktestAt?: Date;
}) {
  const db = await getDb();
  if (!db) return;
  await db.update(strategies).set(data as any).where(eq(strategies.strategyId, strategyId));
}

export async function insertStrategy(data: typeof strategies.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(strategies).values(data).onDuplicateKeyUpdate({ set: { updatedAt: new Date() } });
}

// ─── Alpha Candidates ─────────────────────────────────────────────────────────
export async function getAlphaCandidates(status?: "pending" | "approved" | "rejected" | "expired") {
  const db = await getDb();
  if (!db) return [];
  if (status) return db.select().from(alphaCandidates).where(eq(alphaCandidates.status, status)).orderBy(desc(alphaCandidates.discoveredAt));
  return db.select().from(alphaCandidates).orderBy(desc(alphaCandidates.discoveredAt));
}

export async function getAlphaCandidate(candidateId: string) {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(alphaCandidates).where(eq(alphaCandidates.candidateId, candidateId)).limit(1);
  return result[0] ?? null;
}

export async function updateAlphaCandidateStatus(candidateId: string, status: "approved" | "rejected", reason?: string) {
  const db = await getDb();
  if (!db) return;
  const now = new Date();
  if (status === "approved") {
    await db.update(alphaCandidates).set({ status, approvedAt: now }).where(eq(alphaCandidates.candidateId, candidateId));
  } else {
    await db.update(alphaCandidates).set({ status, rejectedAt: now, rejectionReason: reason }).where(eq(alphaCandidates.candidateId, candidateId));
  }
}

export async function insertAlphaCandidate(data: typeof alphaCandidates.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(alphaCandidates).values(data).onDuplicateKeyUpdate({ set: { updatedAt: new Date() } });
}

// ─── Alpha Engine Runs ────────────────────────────────────────────────────────
export async function getAlphaEngineRuns(symbol?: string, limit = 20) {
  const db = await getDb();
  if (!db) return [];
  if (symbol) return db.select().from(alphaEngineRuns).where(eq(alphaEngineRuns.symbol, symbol)).orderBy(desc(alphaEngineRuns.startedAt)).limit(limit);
  return db.select().from(alphaEngineRuns).orderBy(desc(alphaEngineRuns.startedAt)).limit(limit);
}

export async function getLatestEngineRun(symbol: string) {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(alphaEngineRuns).where(eq(alphaEngineRuns.symbol, symbol)).orderBy(desc(alphaEngineRuns.startedAt)).limit(1);
  return result[0] ?? null;
}

export async function insertEngineRun(data: typeof alphaEngineRuns.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(alphaEngineRuns).values(data);
}

export async function updateEngineRun(runId: string, data: Partial<typeof alphaEngineRuns.$inferInsert>) {
  const db = await getDb();
  if (!db) return;
  await db.update(alphaEngineRuns).set(data as any).where(eq(alphaEngineRuns.runId, runId));
}

// ─── Trades ───────────────────────────────────────────────────────────────────
export async function getTrades(filters?: {
  symbol?: string; strategyId?: string; status?: string;
  direction?: string; fromDate?: Date; limit?: number;
}) {
  const db = await getDb();
  if (!db) return [];
  const conditions = [];
  if (filters?.symbol) conditions.push(eq(trades.symbol, filters.symbol));
  if (filters?.strategyId) conditions.push(eq(trades.strategyId, filters.strategyId));
  if (filters?.status) conditions.push(eq(trades.status, filters.status as any));
  if (filters?.direction) conditions.push(eq(trades.direction, filters.direction as any));
  if (filters?.fromDate) conditions.push(gte(trades.entryAt, filters.fromDate));
  let query = db.select().from(trades).$dynamic();
  if (conditions.length > 0) query = query.where(and(...conditions));
  return query.orderBy(desc(trades.entryAt)).limit(filters?.limit ?? 100);
}

export async function insertTrade(data: typeof trades.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(trades).values(data).onDuplicateKeyUpdate({ set: { updatedAt: new Date() } });
}

export async function updateTrade(tradeId: string, data: Partial<typeof trades.$inferInsert>) {
  const db = await getDb();
  if (!db) return;
  await db.update(trades).set(data as any).where(eq(trades.tradeId, tradeId));
}

// ─── System Events ────────────────────────────────────────────────────────────
export async function getSystemEvents(limit = 50, severity?: string) {
  const db = await getDb();
  if (!db) return [];
  if (severity) return db.select().from(systemEvents).where(eq(systemEvents.severity, severity as any)).orderBy(desc(systemEvents.occurredAt)).limit(limit);
  return db.select().from(systemEvents).orderBy(desc(systemEvents.occurredAt)).limit(limit);
}

export async function insertSystemEvent(data: typeof systemEvents.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(systemEvents).values(data);
}

// ─── Dev Tasks ────────────────────────────────────────────────────────────────
export async function getDevTasks() {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(devTasks).orderBy(devTasks.sortOrder, devTasks.category);
}

export async function updateDevTaskStatus(id: number, status: "completed" | "in_progress" | "pending" | "blocked") {
  const db = await getDb();
  if (!db) return;
  const completedAt = status === "completed" ? new Date() : undefined;
  await db.update(devTasks).set({ status, ...(completedAt ? { completedAt } : {}) }).where(eq(devTasks.id, id));
}

export async function insertDevTask(data: typeof devTasks.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(devTasks).values(data);
}

// ─── Wallet ───────────────────────────────────────────────────────────────────
export async function getLatestWalletSnapshot() {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(walletSnapshots).orderBy(desc(walletSnapshots.snapshotAt)).limit(1);
  return result[0] ?? null;
}

export async function insertWalletSnapshot(data: typeof walletSnapshots.$inferInsert) {
  const db = await getDb();
  if (!db) return;
  await db.insert(walletSnapshots).values(data);
}
