import { COOKIE_NAME } from "@shared/const";
import { getSessionCookieOptions } from "./_core/cookies";
import { systemRouter } from "./_core/systemRouter";
import { protectedProcedure, publicProcedure, router } from "./_core/trpc";
import { z } from "zod";
import {
  getApiConfig, upsertApiConfig,
  getTradingPairs, getTradingPair, upsertTradingPair, updateTradingPairStatus,
  getStrategies, getStrategy, updateStrategyStatus, updateStrategyBacktest, insertStrategy,
  getAlphaCandidates, getAlphaCandidate, updateAlphaCandidateStatus, insertAlphaCandidate,
  getAlphaEngineRuns, getLatestEngineRun, insertEngineRun, updateEngineRun,
  getTrades, insertTrade, updateTrade,
  getSystemEvents, insertSystemEvent,
  getDevTasks, updateDevTaskStatus, insertDevTask,
  getLatestWalletSnapshot, insertWalletSnapshot,
} from "./db";
import {
  MOCK_TRADING_PAIRS, MOCK_STRATEGIES, MOCK_ALPHA_CANDIDATES,
  MOCK_TRADES, MOCK_SYSTEM_EVENTS, MOCK_DEV_TASKS
} from "./mockData";
import { nanoid } from "nanoid";
import { invokeLLM } from "./_core/llm";

// ─── Global Alpha Engine State ─────────────────────────────────────────────
let globalEngineState: {
  status: "stopped" | "running" | "paused";
  startedAt: Date | null;
  stoppedAt: Date | null;
  currentPairs: string[];
  totalRuns: number;
  params: { icThreshold: number; oosWinRateMin: number; maxConditions: number; lookbackDays: number };
} = {
  status: "stopped",
  startedAt: null,
  stoppedAt: null,
  currentPairs: [],
  totalRuns: 0,
  params: { icThreshold: 0.05, oosWinRateMin: 0.60, maxConditions: 3, lookbackDays: 180 },
};

// ─── Seed helper (run once on first load) ────────────────────────────────────
let seeded = false;
async function seedIfNeeded() {
  if (seeded) return;
  seeded = true;
  try {
    // Seed trading pairs
    for (const pair of MOCK_TRADING_PAIRS) {
      await upsertTradingPair(pair as any);
    }
    // Seed strategies
    for (const s of MOCK_STRATEGIES) {
      await insertStrategy(s as any);
    }
    // Seed alpha candidates
    for (const c of MOCK_ALPHA_CANDIDATES) {
      await insertAlphaCandidate(c as any);
    }
    // Seed trades
    for (const t of MOCK_TRADES) {
      await insertTrade(t as any);
    }
    // Seed system events
    for (const e of MOCK_SYSTEM_EVENTS) {
      await insertSystemEvent(e as any);
    }
    // Seed dev tasks
    for (const task of MOCK_DEV_TASKS) {
      await insertDevTask(task as any);
    }
    // Seed wallet snapshot
    await insertWalletSnapshot({
      totalEquity: "52847.30",
      availableBalance: "38234.50",
      usedMargin: "12450.00",
      unrealizedPnl: "2162.80",
      assets: [
        { asset: "USDT", balance: "38234.50", unrealizedPnl: "0" },
        { asset: "BTC", balance: "0.15", unrealizedPnl: "2162.80" },
        { asset: "ETH", balance: "2.5", unrealizedPnl: "0" },
      ]
    });
    console.log("[Seed] Mock data seeded successfully");
  } catch (err) {
    console.warn("[Seed] Seeding skipped (already exists or error):", err);
  }
}

// ─── App Router ───────────────────────────────────────────────────────────────
export const appRouter = router({
  system: systemRouter,

  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return { success: true } as const;
    }),
  }),

  // ─── API Config ─────────────────────────────────────────────────────────
  apiConfig: router({
    get: publicProcedure.query(async ({ ctx }) => {
      await seedIfNeeded();
      const userId = ctx.user?.id ?? 1;
      const config = await getApiConfig(userId);
      if (!config) return null;
      // Mask the secret
      return { ...config, apiSecret: config.apiSecret ? "••••••••••••••••" : null };
    }),
    save: publicProcedure.input(z.object({
      apiKey: z.string().min(1),
      apiSecret: z.string().min(1),
      isTestnet: z.boolean().default(false),
    })).mutation(async ({ ctx, input }) => {
      const userId = ctx.user?.id ?? 1;
      await upsertApiConfig(userId, {
        apiKey: input.apiKey,
        apiSecret: input.apiSecret,
        isTestnet: input.isTestnet,
        isActive: false,
        lastTestStatus: "pending",
      });
      return { success: true };
    }),
    testConnection: publicProcedure.mutation(async ({ ctx }) => {
      const userId = ctx.user?.id ?? 1;
      const config = await getApiConfig(userId);
      if (!config?.apiKey) return { success: false, message: "未配置API Key" };
      // Simulate connection test
      await new Promise(r => setTimeout(r, 800));
      const success = Math.random() > 0.2;
      await upsertApiConfig(userId, {
        isActive: success,
        lastTestedAt: new Date(),
        lastTestStatus: success ? "success" : "failed",
      });
      return {
        success,
        message: success ? "连接成功！账户权限验证通过" : "连接失败：API Key无效或网络超时",
        latency: Math.floor(Math.random() * 100) + 50,
      };
    }),
  }),

  // ─── Wallet ──────────────────────────────────────────────────────────────
  wallet: router({
    getSnapshot: publicProcedure.query(async () => {
      await seedIfNeeded();
      return getLatestWalletSnapshot();
    }),
  }),

  // ─── Trading Pairs ────────────────────────────────────────────────────────
  tradingPairs: router({
    list: publicProcedure.query(async () => {
      await seedIfNeeded();
      return getTradingPairs();
    }),
    get: publicProcedure.input(z.object({ symbol: z.string() })).query(async ({ input }) => {
      return getTradingPair(input.symbol);
    }),
    add: publicProcedure.input(z.object({
      symbol: z.string().min(3).toUpperCase(),
    })).mutation(async ({ input }) => {
      const symbol = input.symbol.toUpperCase();
      const base = symbol.replace(/USDT$|BUSD$|BTC$|ETH$/, "");
      const quote = symbol.slice(base.length);
      await upsertTradingPair({
        symbol,
        baseAsset: base,
        quoteAsset: quote,
        isTracked: true,
        dataCollectionStatus: "downloading",
        dataDownloadProgress: 0,
        alphaEngineStatus: "idle",
      });
      // Log system event
      await insertSystemEvent({
        eventType: "engine_started",
        symbol,
        severity: "info",
        title: `新增交易对: ${symbol}`,
        message: `Alpha引擎已开始为 ${symbol} 收集数据，预计完成时间: 2-4小时`,
        occurredAt: new Date(),
      });
      return { success: true, symbol };
    }),
    updateStatus: publicProcedure.input(z.object({
      symbol: z.string(),
      dataDownloadProgress: z.number().optional(),
      alphaEngineStatus: z.enum(["idle", "scanning", "mining", "completed", "error"]).optional(),
      dataCollectionStatus: z.enum(["pending", "downloading", "completed", "failed"]).optional(),
    })).mutation(async ({ input }) => {
      await updateTradingPairStatus(input.symbol, input as any);
      return { success: true };
    }),
  }),

  // ─── Strategies ───────────────────────────────────────────────────────────
  strategies: router({
    list: publicProcedure.input(z.object({
      type: z.enum(["P1", "P2", "ALPHA"]).optional(),
      status: z.string().optional(),
      symbol: z.string().optional(),
      search: z.string().optional(),
    }).optional()).query(async ({ input }) => {
      await seedIfNeeded();
      return getStrategies(input ?? {});
    }),
    get: publicProcedure.input(z.object({ strategyId: z.string() })).query(async ({ input }) => {
      return getStrategy(input.strategyId);
    }),
    updateStatus: publicProcedure.input(z.object({
      strategyId: z.string(),
      status: z.enum(["active", "paused", "degraded", "retired"]),
    })).mutation(async ({ input }) => {
      await updateStrategyStatus(input.strategyId, input.status);
      return { success: true };
    }),
    triggerBacktest: publicProcedure.input(z.object({
      strategyId: z.string(),
    })).mutation(async ({ input }) => {
      await updateStrategyBacktest(input.strategyId, {
        backtestStatus: "running",
        lastBacktestAt: new Date(),
      });
      // Simulate async backtest
      setTimeout(async () => {
        const winRate = 60 + Math.random() * 20;
        const avgReturn = 0.015 + Math.random() * 0.025;
        const sharpe = 1.2 + Math.random() * 1.5;
        const maxDD = -(2 + Math.random() * 6);
        const equityCurve = Array.from({ length: 20 }, (_, i) => 100 + i * (winRate - 50) * 0.1 + (Math.random() - 0.5) * 3);
        await updateStrategyBacktest(input.strategyId, {
          backtestStatus: "completed",
          backtestResult: { equity_curve: equityCurve, sharpe, max_drawdown: maxDD, oos_win_rate: winRate, avg_return: avgReturn },
          lastBacktestAt: new Date(),
        });
        await insertSystemEvent({
          eventType: "backtest_completed",
          strategyId: input.strategyId,
          severity: "info",
          title: `回测完成: ${input.strategyId}`,
          message: `OOS胜率: ${winRate.toFixed(1)}%，夏普比率: ${sharpe.toFixed(2)}，最大回撤: ${maxDD.toFixed(2)}%`,
          metadata: { oos_win_rate: winRate, sharpe, max_drawdown: maxDD },
          occurredAt: new Date(),
        });
      }, 3000 + Math.random() * 5000);
      return { success: true, message: "回测已启动，预计3-8秒完成" };
    }),
    updateParams: publicProcedure.input(z.object({
      strategyId: z.string(),
      params: z.record(z.string(), z.unknown()),
    })).mutation(async ({ input }) => {
      const db_module = await import("./db");
      const db = await db_module.getDb();
      if (!db) return { success: false };
      const { strategies } = await import("../drizzle/schema");
      const { eq } = await import("drizzle-orm");
      await db.update(strategies).set({ params: input.params as any }).where(eq(strategies.strategyId, input.strategyId));
      return { success: true };
    }),
  }),

  // ─── Alpha Engine ─────────────────────────────────────────────────────────
  alphaEngine: router({
    getCandidates: publicProcedure.input(z.object({
      status: z.enum(["pending", "approved", "rejected", "expired"]).optional(),
    }).optional()).query(async ({ input }) => {
      await seedIfNeeded();
      return getAlphaCandidates(input?.status);
    }),
    getCandidate: publicProcedure.input(z.object({ candidateId: z.string() })).query(async ({ input }) => {
      return getAlphaCandidate(input.candidateId);
    }),
    approveCandidate: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(async ({ input }) => {
      await updateAlphaCandidateStatus(input.candidateId, "approved");
      const candidate = await getAlphaCandidate(input.candidateId);
      if (candidate) {
        // Auto-create strategy from approved candidate
        await insertStrategy({
          strategyId: `ALPHA-${input.candidateId.slice(-8)}`,
          name: `Alpha策略 ${input.candidateId.slice(-6)}`,
          type: "ALPHA",
          direction: candidate.direction,
          symbol: candidate.symbol,
          entryCondition: candidate.fullExpression ?? "",
          exitConditionTop3: candidate.exitConditionTop3,
          oosWinRate: candidate.oosWinRate,
          oosAvgReturn: candidate.oosAvgReturn,
          oosSampleSize: candidate.sampleSize ?? 0,
          confidenceScore: candidate.confidenceScore,
          overfitScore: candidate.overfitScore,
          featureDiversityScore: 0.7,
          status: "active",
          approvedAt: new Date(),
        } as any);
        await insertSystemEvent({
          eventType: "alpha_approved",
          symbol: candidate.symbol,
          severity: "info",
          title: `Alpha策略已批准: ${input.candidateId}`,
          message: `OOS胜率: ${candidate.oosWinRate.toFixed(1)}%，已自动激活为活跃策略`,
          metadata: { candidate_id: input.candidateId },
          occurredAt: new Date(),
        });
      }
      return { success: true };
    }),
    rejectCandidate: publicProcedure.input(z.object({
      candidateId: z.string(),
      reason: z.string().optional(),
    })).mutation(async ({ input }) => {
      await updateAlphaCandidateStatus(input.candidateId, "rejected", input.reason);
      await insertSystemEvent({
        eventType: "alpha_rejected",
        severity: "info",
        title: `Alpha候选已驳回: ${input.candidateId}`,
        message: input.reason ?? "手动驳回",
        occurredAt: new Date(),
      });
      return { success: true };
    }),
    getRuns: publicProcedure.input(z.object({
      symbol: z.string().optional(),
      limit: z.number().default(20),
    }).optional()).query(async ({ input }) => {
      await seedIfNeeded();
      return getAlphaEngineRuns(input?.symbol, input?.limit);
    }),
    getGlobalStatus: publicProcedure.query(() => {
      return {
        ...globalEngineState,
        uptimeSeconds: globalEngineState.startedAt
          ? Math.floor((Date.now() - globalEngineState.startedAt.getTime()) / 1000)
          : 0,
      };
    }),
    startGlobal: publicProcedure.input(z.object({
      params: z.object({
        icThreshold: z.number().default(0.05),
        oosWinRateMin: z.number().default(0.60),
        maxConditions: z.number().default(3),
        lookbackDays: z.number().default(180),
      }).optional(),
    }).optional()).mutation(async ({ input }) => {
      if (globalEngineState.status === "running") {
        return { success: false, message: "引擎已在运行中" };
      }
      const pairs = await getTradingPairs();
      const trackedPairs = pairs.filter((p: any) => p.isTracked).map((p: any) => p.symbol);
      globalEngineState = {
        status: "running",
        startedAt: new Date(),
        stoppedAt: null,
        currentPairs: trackedPairs,
        totalRuns: globalEngineState.totalRuns + 1,
        params: input?.params ?? globalEngineState.params,
      };
      // Kick off a run for each tracked pair
      for (const symbol of trackedPairs) {
        const runId = `RUN-${nanoid(8)}`;
        await insertEngineRun({
          runId,
          symbol,
          status: "running",
          phase: "data_download",
          progress: 0,
          params: globalEngineState.params,
          startedAt: new Date(),
        });
        // Simulate async progression per pair
        const phases: Array<"data_download" | "ic_scan" | "atom_mining" | "combo_scan" | "walk_forward" | "exit_mining" | "completed"> = [
          "data_download", "ic_scan", "atom_mining", "combo_scan", "walk_forward", "exit_mining", "completed"
        ];
        let phaseIdx = 0;
        const delay = trackedPairs.indexOf(symbol) * 1500; // stagger starts
        const advance = async () => {
          if (globalEngineState.status !== "running") return;
          if (phaseIdx >= phases.length - 1) {
            await updateEngineRun(runId, {
              status: "completed", phase: "completed", progress: 100,
              featuresScanned: 52,
              candidatesFound: Math.floor(Math.random() * 4) + 1,
              completedAt: new Date(),
            });
            await insertSystemEvent({
              eventType: "engine_stopped", symbol, severity: "info",
              title: `Alpha引擎完成: ${symbol}`,
              message: `已扫描52个特征，发现候选策略`,
              occurredAt: new Date(),
            });
            // Check if all pairs done
            const runs = await getAlphaEngineRuns(undefined, 100);
            const thisRoundRuns = runs.filter((r: any) => r.startedAt && r.startedAt >= globalEngineState.startedAt!);
            const allDone = thisRoundRuns.every((r: any) => r.status === "completed" || r.status === "failed");
            if (allDone) {
              globalEngineState.status = "stopped";
              globalEngineState.stoppedAt = new Date();
            }
            return;
          }
          phaseIdx++;
          await updateEngineRun(runId, {
            phase: phases[phaseIdx],
            progress: Math.floor((phaseIdx / (phases.length - 1)) * 100),
            featuresScanned: phaseIdx >= 2 ? 52 : phaseIdx * 26,
          });
          setTimeout(advance, 3500 + Math.random() * 2500);
        };
        setTimeout(advance, delay + 1500);
      }
      await insertSystemEvent({
        eventType: "engine_started", severity: "info",
        title: `Alpha引擎全局启动`,
        message: `正在处理 ${trackedPairs.length} 个交易对: ${trackedPairs.join(", ")}`,
        occurredAt: new Date(),
      });
      return { success: true, pairs: trackedPairs, message: `引擎已启动，处理 ${trackedPairs.length} 个交易对` };
    }),
    stopGlobal: publicProcedure.mutation(async () => {
      if (globalEngineState.status !== "running") {
        return { success: false, message: "引擎未在运行" };
      }
      globalEngineState.status = "stopped";
      globalEngineState.stoppedAt = new Date();
      await insertSystemEvent({
        eventType: "engine_stopped", severity: "info",
        title: "Alpha引擎已手动停止",
        message: `已处理 ${globalEngineState.currentPairs.length} 个交易对`,
        occurredAt: new Date(),
      });
      return { success: true, message: "引擎已停止" };
    }),
    startRun: publicProcedure.input(z.object({
      symbol: z.string(),
      params: z.object({
        icThreshold: z.number().default(0.05),
        oosWinRateMin: z.number().default(0.60),
        maxConditions: z.number().default(3),
        lookbackDays: z.number().default(180),
      }).optional(),
    })).mutation(async ({ input }) => {
      const runId = `RUN-${nanoid(8)}`;
      await insertEngineRun({
        runId,
        symbol: input.symbol,
        status: "running",
        phase: "data_download",
        progress: 0,
        params: input.params ?? {},
        startedAt: new Date(),
      });
      // Simulate engine run phases
      const phases: Array<"data_download" | "ic_scan" | "atom_mining" | "combo_scan" | "walk_forward" | "exit_mining" | "completed"> = [
        "data_download", "ic_scan", "atom_mining", "combo_scan", "walk_forward", "exit_mining", "completed"
      ];
      let phaseIdx = 0;
      const advance = async () => {
        if (phaseIdx >= phases.length - 1) {
          await updateEngineRun(runId, {
            status: "completed",
            phase: "completed",
            progress: 100,
            featuresScanned: 52,
            candidatesFound: Math.floor(Math.random() * 5) + 1,
            candidatesApproved: 0,
            completedAt: new Date(),
          });
          await insertSystemEvent({
            eventType: "engine_stopped",
            symbol: input.symbol,
            severity: "info",
            title: `Alpha引擎运行完成: ${input.symbol}`,
            message: `已扫描52个特征，发现候选策略`,
            occurredAt: new Date(),
          });
          return;
        }
        phaseIdx++;
        const progress = Math.floor((phaseIdx / (phases.length - 1)) * 100);
        await updateEngineRun(runId, {
          phase: phases[phaseIdx],
          progress,
          featuresScanned: phaseIdx >= 2 ? 52 : phaseIdx * 26,
        });
        setTimeout(advance, 4000 + Math.random() * 3000);
      };
      setTimeout(advance, 2000);
      return { success: true, runId };
    }),
    triggerBacktest: publicProcedure.input(z.object({
      candidateId: z.string(),
    })).mutation(async ({ input }) => {
      const db_module = await import("./db");
      const db = await db_module.getDb();
      if (!db) return { success: false };
      const { alphaCandidates } = await import("../drizzle/schema");
      const { eq } = await import("drizzle-orm");
      await db.update(alphaCandidates).set({ backtestStatus: "running" }).where(eq(alphaCandidates.candidateId, input.candidateId));
      setTimeout(async () => {
        const winRate = 60 + Math.random() * 20;
        const avgReturn = 0.015 + Math.random() * 0.025;
        await db.update(alphaCandidates).set({
          backtestStatus: "completed",
          backtestResult: { oos_win_rate: winRate, avg_return: avgReturn, sharpe: 1.5 + Math.random() },
          oosWinRate: winRate,
          oosAvgReturn: avgReturn,
        } as any).where(eq(alphaCandidates.candidateId, input.candidateId));
      }, 3000 + Math.random() * 4000);
      return { success: true };
    }),
    getSystemHealth: publicProcedure.query(async () => {
      return {
        overall: 87,
        status: "warning",
        layers: {
          data: {
            status: "healthy",
            websocket: { connected: 4, total: 4, streams: ["liquidations", "book_ticker", "agg_trades", "mark_price"] },
            dataIntegrity: { status: "healthy", missingPct: 0.02 },
          },
          features: {
            status: "healthy",
            computed: 52, total: 52, nanRate: 0.02,
            dimensions: ["PRICE", "TRADE_FLOW", "LIQUIDITY", "POSITIONING", "MICROSTRUCTURE", "MARK_PRICE"],
            latencyMs: 8,
          },
          signals: {
            status: "healthy",
            p1Running: 8, p1Total: 8,
            p2Running: 3, p2Total: 3,
            fatigue: false,
          },
          execution: {
            status: "warning",
            engineActive: true,
            fillRate: 0.62,
            fillRateTarget: 0.70,
            exitTracking: true,
            warning: "限价单成交率62% < 目标70%，建议调整ENTRY_OFFSET参数",
          },
        },
        issues: [
          { severity: "warning", message: "限价单成交率 62% < 70%，实盘可行性待验证", action: "调整ENTRY_OFFSET或使用混合策略" },
        ],
        lastUpdated: new Date().toISOString(),
      };
    }),
  }),

  // ─── Trades ───────────────────────────────────────────────────────────────
  trades: router({
    list: publicProcedure.input(z.object({
      symbol: z.string().optional(),
      strategyId: z.string().optional(),
      status: z.enum(["open", "closed", "cancelled"]).optional(),
      direction: z.enum(["LONG", "SHORT"]).optional(),
      limit: z.number().default(50),
    }).optional()).query(async ({ input }) => {
      await seedIfNeeded();
      return getTrades(input ?? {});
    }),
    getStats: publicProcedure.query(async () => {
      await seedIfNeeded();
      const allTrades = await getTrades({ status: "closed", limit: 1000 });
      const totalPnl = allTrades.reduce((s, t) => s + parseFloat(t.pnl ?? "0"), 0);
      const wins = allTrades.filter(t => parseFloat(t.pnl ?? "0") > 0).length;
      const winRate = allTrades.length > 0 ? (wins / allTrades.length) * 100 : 0;
      const avgReturn = allTrades.length > 0 ? totalPnl / allTrades.length : 0;
      const pnlValues = allTrades.map(t => parseFloat(t.pnl ?? "0"));
      let maxDD = 0, peak = 0, cumPnl = 0;
      for (const p of pnlValues) {
        cumPnl += p;
        if (cumPnl > peak) peak = cumPnl;
        const dd = peak > 0 ? (cumPnl - peak) / peak * 100 : 0;
        if (dd < maxDD) maxDD = dd;
      }
      const openTrades = await getTrades({ status: "open", limit: 100 });
      return {
        totalTrades: allTrades.length,
        openTrades: openTrades.length,
        totalPnl: totalPnl.toFixed(2),
        winRate: winRate.toFixed(1),
        avgReturn: avgReturn.toFixed(2),
        maxDrawdown: maxDD.toFixed(2),
        todayPnl: allTrades.filter(t => t.exitAt && t.exitAt > new Date(Date.now() - 86400000)).reduce((s, t) => s + parseFloat(t.pnl ?? "0"), 0).toFixed(2),
      };
    }),
    close: publicProcedure.input(z.object({
      tradeId: z.string(),
      exitPrice: z.string(),
      exitReason: z.string().default("manual"),
    })).mutation(async ({ input }) => {
      await updateTrade(input.tradeId, {
        exitPrice: input.exitPrice,
        exitReason: input.exitReason,
        exitAt: new Date(),
        status: "closed",
      });
      return { success: true };
    }),
  }),

  // ─── System Events ─────────────────────────────────────────────────────────
  systemEvents: router({
    list: publicProcedure.input(z.object({
      limit: z.number().default(50),
      severity: z.string().optional(),
    }).optional()).query(async ({ input }) => {
      await seedIfNeeded();
      return getSystemEvents(input?.limit ?? 50, input?.severity);
    }),
  }),

  // ─── Dev Progress ─────────────────────────────────────────────────────────
  devProgress: router({
    getTasks: publicProcedure.query(async () => {
      await seedIfNeeded();
      return getDevTasks();
    }),
    updateStatus: publicProcedure.input(z.object({
      id: z.number(),
      status: z.enum(["completed", "in_progress", "pending", "blocked"]),
    })).mutation(async ({ input }) => {
      await updateDevTaskStatus(input.id, input.status);
      return { success: true };
    }),
    addTask: publicProcedure.input(z.object({
      category: z.string(),
      title: z.string(),
      description: z.string().optional(),
      priority: z.enum(["critical", "high", "medium", "low"]).default("medium"),
      layer: z.string().optional(),
    })).mutation(async ({ input }) => {
      await insertDevTask({ ...input, status: "pending", sortOrder: 99 });
      return { success: true };
    }),
  }),

  // ─── LLM Analysis ─────────────────────────────────────────────────────────
  llmAnalysis: router({
    analyzeStrategy: publicProcedure.input(z.object({
      strategyId: z.string(),
    })).mutation(async ({ input }) => {
      const strategy = await getStrategy(input.strategyId);
      if (!strategy) return { success: false, report: "" };
      const prompt = `你是一个专业的量化交易策略分析师。请分析以下交易策略并提供优化建议：

策略ID: ${strategy.strategyId}
策略名称: ${strategy.name}
方向: ${strategy.direction}
入场条件: ${strategy.entryCondition}
OOS胜率: ${strategy.oosWinRate}%
平均收益: ${strategy.oosAvgReturn}%
近7日P&L: ${strategy.pnl7d} USDT
状态: ${strategy.status}

请从以下角度分析：
1. 策略优势和潜在风险
2. 市场适用性（趋势市/震荡市）
3. 参数优化建议
4. 出场条件改进方向
5. 与其他策略的协同可能性

请用中文回答，保持专业简洁。`;

      const response = await invokeLLM({
        messages: [
          { role: "system", content: "你是专业的量化交易策略分析师，擅长分析加密货币市场的量化策略。" },
          { role: "user", content: prompt }
        ]
      });
      const report = response.choices?.[0]?.message?.content ?? "分析生成失败";
      return { success: true, report };
    }),
    generateMarketInsight: publicProcedure.mutation(async () => {
      const response = await invokeLLM({
        messages: [
          { role: "system", content: "你是专业的加密货币市场分析师。" },
          { role: "user", content: "请基于当前BTC市场微观结构（资金费率、持仓量变化、清算数据），生成一份简短的市场洞察报告，重点关注量化策略的执行时机。用中文回答，200字以内。" }
        ]
      });
      const insight = response.choices?.[0]?.message?.content ?? "洞察生成失败";
      return { success: true, insight };
    }),
  }),
});

export type AppRouter = typeof appRouter;
