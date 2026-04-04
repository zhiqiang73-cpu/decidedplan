import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TrpcContext } from "./_core/context";

const mockBridge = vi.hoisted(() => {
  const systemState = {
    timestamp: "2026-04-04T12:00:00Z",
    market_timestamp: "2026-04-04T12:00:00Z",
    monitor_alive: true,
    discovery_alive: false,
    connected: true,
    symbol: "BTCUSDT",
    price: 50_000,
    balance: 5_000,
    regime: "QUIET_TREND",
    positions: [
      {
        signal_name: "P0-2_live",
        family: "P0-2",
        direction: "short",
        qty: 0.02,
        entry_price: 50_000,
        confidence: 3,
        entry_time: "2026-04-04 11:55:00 UTC",
        exit_logic: "dynamic",
      },
    ],
    pending_orders: [],
    strategies: [
      {
        family: "P0-2",
        name: "Funding Rate Arbitrage",
        direction: "short",
        status: "trade_ready",
        entry_conditions: "funding_rate > 0.01%",
        exit_conditions: "exit when funding normalizes",
        today: { triggers: 2, wins: 1, not_filled: 0, errors: 0 },
      },
    ],
    daily_totals: { triggers: 2, wins: 1, not_filled: 0, errors: 0 },
  };

  const trades = [
    {
      tradeId: "TRADE-1",
      strategyId: "P0-2_signal",
      symbol: "BTCUSDT",
      direction: "SHORT",
      status: "closed",
      entryAt: new Date("2026-04-04T10:00:00Z"),
      exitAt: new Date("2026-04-04T10:30:00Z"),
      entryPrice: "50000",
      exitPrice: "49500",
      quantity: "0.02",
      leverage: 10,
      pnl: "10.0000",
      pnlPercent: "1.0000",
      grossReturn: "1.0400",
      exitReason: "logic_complete",
      confidence: 3,
      horizonMin: 30,
      mfe: null,
      mae: null,
      fee: null,
    },
  ];

  const pendingRules = [
    {
      id: "CAND_001",
      group: "position_in_range_4h > 0.7159 -> short 30",
      status: "pending",
      entry: {
        feature: "position_in_range_4h",
        operator: ">",
        threshold: 0.715852,
        direction: "short",
        horizon: 30,
      },
      combo_conditions: [
        { feature: "oi_change_rate_1h", op: "<", threshold: 0.000074 },
      ],
      exit: {
        top3: [
          {
            conditions: [
              { feature: "position_in_range_4h", operator: "<", threshold: 0.50 },
            ],
          },
        ],
      },
      stats: {
        oos_win_rate: 68.75,
        n_oos: 32,
        oos_avg_ret: 0.0453,
      },
      explanation: "High position without fresh OI support.",
      rule_str: "position_in_range_4h > 0.7159 AND oi_change_rate_1h < 7.4e-05",
      discovered_at: "2026-04-04T08:00:00Z",
      mechanism_type: "oi_divergence",
      validation: {
        causal_score: 1,
        issues: [],
        warnings: [],
        causal_explanation: "Price is high while OI growth stalls.",
      },
    },
  ];

  const approvedRules = [
    {
      ...pendingRules[0],
      id: "A4-PIR-001",
      status: "approved",
      discovered_at: "2026-04-03T23:12:47Z",
    },
  ];

  const alerts = [
    {
      id: "alert-1",
      timestamp: "2026-04-04 11:58:00 UTC",
      phase: "P1",
      signalName: "P0-2_live",
      direction: "SHORT",
      bars: "30bars",
      description: "Funding edge still active",
    },
  ];

  return { systemState, trades, pendingRules, approvedRules, alerts };
});

vi.mock("./pythonBridge", () => ({
  getSystemState: vi.fn(() => mockBridge.systemState),
  getTrades: vi.fn((opts?: { status?: string; limit?: number }) => {
    if (opts?.status) {
      return mockBridge.trades.filter((trade) => trade.status === opts.status).slice(0, opts.limit ?? 100);
    }
    return mockBridge.trades.slice(0, opts?.limit ?? 100);
  }),
  getTradeStats: vi.fn(() => ({
    totalTrades: 1,
    openTrades: 0,
    totalPnl: "10.0000",
    winRate: "100.0",
    avgReturn: "10.0000",
    maxDrawdown: "0.00",
    todayPnl: "10.0000",
  })),
  getChartData: vi.fn(() => ({ equityCurve: [], dailyPnl: [] })),
  getApprovedRules: vi.fn(() => mockBridge.approvedRules),
  getPendingRules: vi.fn((status?: string) => {
    if (!status) return mockBridge.pendingRules;
    return mockBridge.pendingRules.filter((rule) => rule.status === status);
  }),
  approveRule: vi.fn(() => true),
  rejectRule: vi.fn(() => true),
  getSystemHealth: vi.fn(() => ({
    overall: 95,
    status: "healthy",
    layers: {},
    issues: [],
    lastUpdated: mockBridge.systemState.timestamp,
  })),
  getEnvConfig: vi.fn(() => ({ apiKey: "", apiSecret: "", hasConfig: false, isTestnet: true })),
  saveEnvConfig: vi.fn(),
  getAlertsLog: vi.fn(() => mockBridge.alerts),
  getDevTasks: vi.fn(() => []),
  updateDevTask: vi.fn(),
  insertDevTask: vi.fn(),
  getEngineState: vi.fn(() => ({
    status: "idle",
    last_run_at: "",
    next_run_at: "",
    stats: {
      pending_count: 1,
      approved_count: 1,
      rejected_count: 0,
      review_count: 0,
      total_approved_this_session: 0,
      total_rejected_this_session: 0,
    },
    recent_decisions: [],
    force_library_summary: [],
  })),
  getReviewQueue: vi.fn(() => []),
  promoterApprove: vi.fn(() => true),
  promoterReject: vi.fn(() => true),
  savePromoterConfig: vi.fn(),
  getForceLibraryState: vi.fn(() => ({ concentration: { positioning: 1 } })),
}));

vi.mock("./binanceTestnet", () => ({
  testConnection: vi.fn(async () => ({ success: true, message: "ok", latency: 0 })),
  getAccountBalance: vi.fn(async () => null),
  getOpenPositions: vi.fn(async () => []),
  getOpenOrders: vi.fn(async () => []),
}));

vi.mock("./_core/llm", () => ({
  invokeLLM: vi.fn(),
}));

import { appRouter } from "./routers";

function createTestContext(): TrpcContext {
  return {
    user: {
      id: 1,
      openId: "local",
      name: "Local Admin",
      email: null,
      role: "admin",
    },
    req: { protocol: "https", headers: {} } as TrpcContext["req"],
    res: { clearCookie: vi.fn() } as unknown as TrpcContext["res"],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("tradingPairs router", () => {
  it("lists the single live trading pair", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.tradingPairs.list();
    expect(result).toHaveLength(1);
    expect(result[0]?.symbol).toBe("BTCUSDT");
    expect(result[0]?.alphaEngineStatus).toBe("idle");
  });

  it("rejects adding unsupported symbols", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.tradingPairs.add({ symbol: "SOLUSDT" });
    expect(result.success).toBe(false);
    expect(result.symbol).toBe("SOLUSDT");
  });
});

describe("strategies router", () => {
  it("returns live strategies plus approved alpha cards", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.strategies.list({});
    expect(result.map((item) => item.strategyId)).toContain("P0-2");
    expect(result.map((item) => item.strategyId)).toContain("ALPHA-A4-PIR-001");
  });

  it("reports delegated backtests", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.strategies.triggerBacktest({ strategyId: "P0-2" });
    expect(result.success).toBe(true);
    expect(result.message).toContain("run_pipeline_backtest.py");
  });
});

describe("alphaEngine router", () => {
  it("returns pending candidate cards", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.alphaEngine.getCandidates();
    expect(result).toHaveLength(1);
    expect(result[0]?.candidateId).toBe("CAND_001");
    expect(result[0]?.mechanismType).toBe("oi_divergence");
  });

  it("derives run history from discovery dates", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.alphaEngine.getRuns({ symbol: "BTCUSDT", limit: 5 });
    expect(result).toHaveLength(1);
    expect(result[0]?.runId).toBe("RUN-2026-04-04");
  });

  it("queues a new discovery run", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.alphaEngine.startRun({ symbol: "BTCUSDT" });
    expect(result.success).toBe(true);
    expect(result.runId.startsWith("RUN-")).toBe(true);
  });

  it("returns live system health", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.alphaEngine.getSystemHealth();
    expect(result.overall).toBe(95);
    expect(result.status).toBe("healthy");
  });
});

describe("trades router", () => {
  it("returns live trade rows", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.trades.list({});
    expect(result).toHaveLength(1);
    expect(result[0]?.tradeId).toBe("TRADE-1");
  });

  it("returns aggregated trade statistics", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.trades.getStats();
    expect(result.totalTrades).toBe(1);
    expect(result.winRate).toBe("100.0");
  });
});

describe("wallet router", () => {
  it("builds the wallet snapshot from live state", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.wallet.getSnapshot();
    expect(result).not.toBeNull();
    expect(result?.availableBalance).toBe("5000.0000");
    expect(result?.totalEquity).toBe("5100.0000");
  });
});

describe("systemEvents router", () => {
  it("maps alert log entries into UI events", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.systemEvents.list({ limit: 10 });
    expect(result).toHaveLength(1);
    expect(result[0]?.eventType).toBe("signal_triggered");
  });
});

describe("auth router", () => {
  it("returns the local operator identity", async () => {
    const caller = appRouter.createCaller(createTestContext());
    const result = await caller.auth.me();
    expect(result?.openId).toBe("local");
    expect(result?.role).toBe("admin");
  });

  it("clears the session cookie on logout", async () => {
    const ctx = createTestContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.auth.logout();
    expect(result.success).toBe(true);
    expect(ctx.res.clearCookie).toHaveBeenCalledTimes(1);
  });
});