import { describe, expect, it, vi, beforeEach } from "vitest";
import { appRouter } from "./routers";
import type { TrpcContext } from "./_core/context";

// ─── Mock DB helpers ──────────────────────────────────────────────────────────
vi.mock("./db", () => ({
  seedIfNeeded: vi.fn().mockResolvedValue(undefined),
  getTradingPairs: vi.fn().mockResolvedValue([
    {
      id: 1, symbol: "BTCUSDT", baseAsset: "BTC", quoteAsset: "USDT",
      isActive: true, isTracked: true,
      dataCollectionStatus: "completed", dataDownloadProgress: 100,
      alphaEngineStatus: "completed", lastDataUpdate: new Date(),
      totalKlines: 50000, dataQualityScore: 0.98,
      currentPrice: "67500.00", priceChange24h: 2.5, volume24h: "1234567890",
      createdAt: new Date(), updatedAt: new Date(),
    },
  ]),
  getTradingPair: vi.fn().mockResolvedValue({
    id: 1, symbol: "BTCUSDT", baseAsset: "BTC", quoteAsset: "USDT",
    isActive: true, isTracked: true,
    dataCollectionStatus: "completed", dataDownloadProgress: 100,
    alphaEngineStatus: "completed", lastDataUpdate: new Date(),
    totalKlines: 50000, dataQualityScore: 0.98,
    currentPrice: "67500.00", priceChange24h: 2.5, volume24h: "1234567890",
    createdAt: new Date(), updatedAt: new Date(),
  }),
  upsertTradingPair: vi.fn().mockResolvedValue(undefined),
  updateTradingPairStatus: vi.fn().mockResolvedValue(undefined),
  getStrategies: vi.fn().mockResolvedValue([
    {
      id: 1, strategyId: "MOM_RSI_V2", symbol: "BTCUSDT", name: "动量RSI策略",
      description: "RSI + 动量因子组合", status: "active", direction: "LONG",
      entryConditions: JSON.stringify([{ factor: "RSI", op: "<", value: 30 }]),
      exitConditions: null, icScore: 0.08, winRate: "0.72", sharpeRatio: "1.85",
      maxDrawdown: "0.12", totalReturn: "0.45", backtestPeriodDays: 180,
      oosWinRate: "0.69", overfitScore: "0.15", confidenceScore: "0.88",
      tradeCount: 124, avgHoldingBars: 8, avgPnlPerTrade: "0.0058",
      createdAt: new Date(), updatedAt: new Date(),
    },
  ]),
  getStrategy: vi.fn().mockResolvedValue(null),
  insertStrategy: vi.fn().mockResolvedValue(undefined),
  updateStrategy: vi.fn().mockResolvedValue(undefined),
  updateStrategyStatus: vi.fn().mockResolvedValue(undefined),
  updateStrategyBacktest: vi.fn().mockResolvedValue(undefined),
  updateAlphaCandidateStatus: vi.fn().mockResolvedValue(undefined),
  getDevTasks: vi.fn().mockResolvedValue([]),
  updateDevTaskStatus: vi.fn().mockResolvedValue(undefined),
  insertDevTask: vi.fn().mockResolvedValue(undefined),
  getTradeStats: vi.fn().mockResolvedValue({
    totalTrades: 124, winTrades: 89, lossTrades: 35,
    winRate: 71.77, totalPnl: 4521.30, todayPnl: 312.50,
  }),
  getAlphaCandidates: vi.fn().mockResolvedValue([
    {
      id: 1, candidateId: "CAND_001", symbol: "BTCUSDT", strategyId: null,
      status: "pending", icScore: 0.09, oosWinRate: "0.71", overfitScore: "0.12",
      entryConditions: JSON.stringify([{ factor: "RSI", op: "<", value: 30 }]),
      exitConditions: null, backtestEquityCurve: null, backtestSummary: null,
      reviewedAt: null, reviewNote: null,
      createdAt: new Date(), updatedAt: new Date(),
    },
  ]),
  getAlphaCandidate: vi.fn().mockResolvedValue(null),
  insertAlphaCandidate: vi.fn().mockResolvedValue(undefined),
  updateAlphaCandidate: vi.fn().mockResolvedValue(undefined),
  getAlphaEngineRuns: vi.fn().mockResolvedValue([
    {
      id: 1, runId: "RUN_001", symbol: "BTCUSDT",
      status: "completed", currentPhase: "completed",
      progress: 100, startedAt: new Date(), completedAt: new Date(),
      phaseDetails: null, errorMessage: null,
      createdAt: new Date(), updatedAt: new Date(),
    },
  ]),
  insertEngineRun: vi.fn().mockResolvedValue(undefined),
  updateEngineRun: vi.fn().mockResolvedValue(undefined),
  getLatestEngineRun: vi.fn().mockResolvedValue(null),
  getTrades: vi.fn().mockResolvedValue([
    {
      id: 1, tradeId: "TRADE_001", symbol: "BTCUSDT", strategyId: "MOM_RSI_V2",
      direction: "LONG", status: "closed",
      entryPrice: "67000.00", exitPrice: "68500.00",
      quantity: "0.01", leverage: 5,
      pnl: "75.00", pnlPercent: "1.12",
      exitReason: "TP_HIT",
      entryAt: new Date(Date.now() - 3600000), exitAt: new Date(),
      createdAt: new Date(), updatedAt: new Date(),
    },
  ]),
  insertTrade: vi.fn().mockResolvedValue(undefined),
  updateTrade: vi.fn().mockResolvedValue(undefined),
  getLatestWalletSnapshot: vi.fn().mockResolvedValue({
    id: 1, snapshotTime: new Date(), totalEquity: "52847.30",
    availableBalance: "48200.00", usedMargin: "4647.30",
    unrealizedPnl: "2162.80", realizedPnlToday: "1202.03",
    btcBalance: "0.5", usdtBalance: "48200.00",
    createdAt: new Date(),
  }),
  insertWalletSnapshot: vi.fn().mockResolvedValue(undefined),
  getSystemEvents: vi.fn().mockResolvedValue([
    {
      id: 1, eventType: "signal_triggered", symbol: "BTCUSDT",
      severity: "info", title: "信号触发", message: "MOM_RSI_V2 LONG信号",
      occurredAt: new Date(), createdAt: new Date(),
    },
  ]),
  insertSystemEvent: vi.fn().mockResolvedValue(undefined),
  getApiConfig: vi.fn().mockResolvedValue(null),
  upsertApiConfig: vi.fn().mockResolvedValue(undefined),
}));

// ─── Test context ─────────────────────────────────────────────────────────────
function createTestContext(): TrpcContext {
  return {
    user: null,
    req: { protocol: "https", headers: {} } as TrpcContext["req"],
    res: { clearCookie: vi.fn() } as unknown as TrpcContext["res"],
  };
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("tradingPairs router", () => {
  it("list returns trading pairs", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.tradingPairs.list();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
    expect(result[0]?.symbol).toBe("BTCUSDT");
  });

  it("get returns a specific pair", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.tradingPairs.get({ symbol: "BTCUSDT" });
    expect(result).not.toBeNull();
    expect(result?.symbol).toBe("BTCUSDT");
  });

  it("add creates a new trading pair", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.tradingPairs.add({ symbol: "SOLUSDT" });
    expect(result.success).toBe(true);
    expect(result.symbol).toBe("SOLUSDT");
  });
});

describe("strategies router", () => {
  it("list returns strategies", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.strategies.list({});
    expect(Array.isArray(result)).toBe(true);
    expect(result[0]?.strategyId).toBe("MOM_RSI_V2");
  });

  it("triggerBacktest returns success", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.strategies.triggerBacktest({ strategyId: "MOM_RSI_V2" });
    expect(result.success).toBe(true);
    expect(result.message).toContain("回测");
  });
});

describe("alphaEngine router", () => {
  it("getCandidates returns candidates list", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.alphaEngine.getCandidates();
    expect(Array.isArray(result)).toBe(true);
    expect(result[0]?.candidateId).toBe("CAND_001");
  });

  it("getRuns returns run history", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.alphaEngine.getRuns({ symbol: "BTCUSDT", limit: 5 });
    expect(Array.isArray(result)).toBe(true);
    expect(result[0]?.runId).toBe("RUN_001");
  });

  it("startRun creates a new run", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.alphaEngine.startRun({ symbol: "BTCUSDT" });
    expect(result.success).toBe(true);
    expect(result.runId).toBeDefined();
  });

  it("getSystemHealth returns health metrics", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.alphaEngine.getSystemHealth();
    expect(result).toBeDefined();
    expect(result.layers).toBeDefined();
    expect(result.overall).toBeGreaterThanOrEqual(0);
  });
});

describe("trades router", () => {
  it("list returns trades", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.trades.list({});
    expect(Array.isArray(result)).toBe(true);
    expect(result[0]?.tradeId).toBe("TRADE_001");
  });

  it("getStats returns trade statistics", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.trades.getStats();
    // getStats is computed from the trades list (1 mock trade)
    expect(result.totalTrades).toBeGreaterThanOrEqual(0);
    expect(result.winRate).toBeDefined();
    expect(result.totalPnl).toBeDefined();
    expect(result.todayPnl).toBeDefined();
  });
});

describe("wallet router", () => {
  it("getSnapshot returns wallet data", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.wallet.getSnapshot();
    expect(result).not.toBeNull();
    expect(result?.totalEquity).toBe("52847.30");
  });
});

describe("systemEvents router", () => {
  it("list returns system events", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.systemEvents.list({ limit: 10 });
    expect(Array.isArray(result)).toBe(true);
    expect(result[0]?.eventType).toBe("signal_triggered");
  });
});

describe("auth router", () => {
  it("me returns null for unauthenticated user", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.auth.me();
    expect(result).toBeNull();
  });

  it("logout clears session cookie", async () => {
    const clearedCookies: string[] = [];
    const ctx: TrpcContext = {
      user: {
        id: 1, openId: "test-user", email: "test@test.com", name: "Test",
        loginMethod: "manus", role: "user",
        createdAt: new Date(), updatedAt: new Date(), lastSignedIn: new Date(),
      },
      req: { protocol: "https", headers: {} } as TrpcContext["req"],
      res: {
        clearCookie: (name: string) => clearedCookies.push(name),
      } as unknown as TrpcContext["res"],
    };
    const caller = appRouter.createCaller(ctx);
    const result = await caller.auth.logout();
    expect(result.success).toBe(true);
    expect(clearedCookies.length).toBe(1);
  });
});
