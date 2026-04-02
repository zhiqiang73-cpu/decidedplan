import { Server as SocketIOServer } from "socket.io";
import type { Server as HttpServer } from "http";

let io: SocketIOServer | null = null;

export function initWebSocket(httpServer: HttpServer) {
  io = new SocketIOServer(httpServer, {
    cors: { origin: "*", methods: ["GET", "POST"] },
    path: "/api/ws",
  });

  io.on("connection", (socket) => {
    console.log(`[WS] Client connected: ${socket.id}`);

    // Client subscribes to a trading pair
    socket.on("subscribe:pair", (symbol: string) => {
      socket.join(`pair:${symbol}`);
    });

    socket.on("unsubscribe:pair", (symbol: string) => {
      socket.leave(`pair:${symbol}`);
    });

    socket.on("disconnect", () => {
      console.log(`[WS] Client disconnected: ${socket.id}`);
    });
  });

  // Start simulated real-time data emission
  startSimulatedEmitter();

  return io;
}

export function getIO() {
  return io;
}

// ─── Emit helpers ─────────────────────────────────────────────────────────────

export function emitSignalTriggered(signal: {
  symbol: string;
  strategyId: string;
  direction: "LONG" | "SHORT";
  price: number;
  confidence: number;
}) {
  io?.emit("signal:triggered", { ...signal, timestamp: new Date().toISOString() });
}

export function emitAlphaProgress(data: {
  symbol: string;
  phase: string;
  progress: number;
  message: string;
}) {
  io?.emit("alpha:progress", { ...data, timestamp: new Date().toISOString() });
}

export function emitTradeUpdate(trade: {
  tradeId: string;
  symbol: string;
  status: string;
  pnl?: string;
}) {
  io?.emit("trade:update", { ...trade, timestamp: new Date().toISOString() });
}

export function emitSystemEvent(event: {
  type: string;
  severity: "info" | "warning" | "error";
  title: string;
  message: string;
}) {
  io?.emit("system:event", { ...event, timestamp: new Date().toISOString() });
}

export function emitPriceUpdate(data: {
  symbol: string;
  price: number;
  change24h: number;
}) {
  io?.to(`pair:${data.symbol}`).emit("price:update", { ...data, timestamp: new Date().toISOString() });
  io?.emit("price:update", { ...data, timestamp: new Date().toISOString() });
}

// ─── Simulated emitter for demo ───────────────────────────────────────────────

const DEMO_PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"];
const DEMO_PRICES: Record<string, number> = {
  BTCUSDT: 67500, ETHUSDT: 3420, BNBUSDT: 512, SOLUSDT: 185, XRPUSDT: 0.62,
};

function startSimulatedEmitter() {
  // Price updates every 2 seconds
  setInterval(() => {
    DEMO_PAIRS.forEach(sym => {
      const base = DEMO_PRICES[sym]!;
      const change = (Math.random() - 0.5) * 0.002;
      DEMO_PRICES[sym] = base * (1 + change);
      emitPriceUpdate({
        symbol: sym,
        price: parseFloat(DEMO_PRICES[sym]!.toFixed(4)),
        change24h: parseFloat(((Math.random() - 0.3) * 8).toFixed(2)),
      });
    });
  }, 2000);

  // Alpha engine progress every 5 seconds
  let alphaProgress = 0;
  const alphaPhases = ["数据下载", "特征工程", "IC扫描", "策略挖掘", "OOS验证", "回测验证"];
  setInterval(() => {
    alphaProgress = (alphaProgress + Math.floor(Math.random() * 8) + 2) % 101;
    const phaseIdx = Math.floor((alphaProgress / 100) * alphaPhases.length);
    emitAlphaProgress({
      symbol: "BTCUSDT",
      phase: alphaPhases[Math.min(phaseIdx, alphaPhases.length - 1)]!,
      progress: alphaProgress,
      message: `正在处理 ${alphaProgress}% · 发现 ${Math.floor(alphaProgress / 10)} 个候选策略`,
    });
  }, 5000);

  // Random signal every 30 seconds
  setInterval(() => {
    const sym = DEMO_PAIRS[Math.floor(Math.random() * DEMO_PAIRS.length)]!;
    const strategies = ["MOM_RSI_V2", "VOL_BREAKOUT", "MACD_CROSS", "BB_SQUEEZE", "VWAP_REVERT"];
    emitSignalTriggered({
      symbol: sym,
      strategyId: strategies[Math.floor(Math.random() * strategies.length)]!,
      direction: Math.random() > 0.5 ? "LONG" : "SHORT",
      price: DEMO_PRICES[sym] ?? 0,
      confidence: parseFloat((0.6 + Math.random() * 0.35).toFixed(2)),
    });
  }, 30000);

  // System events every 60 seconds
  setInterval(() => {
    const events = [
      { type: "data_sync", severity: "info" as const, title: "数据同步完成", message: "BTCUSDT 15m K线数据已更新至最新" },
      { type: "strategy_found", severity: "info" as const, title: "新策略发现", message: "Alpha引擎发现1个高置信度策略，正在进行OOS验证" },
      { type: "network_check", severity: "info" as const, title: "网络延迟检测", message: `当前延迟: ${Math.floor(Math.random() * 50 + 10)}ms` },
    ];
    const evt = events[Math.floor(Math.random() * events.length)]!;
    emitSystemEvent(evt);
  }, 60000);
}
