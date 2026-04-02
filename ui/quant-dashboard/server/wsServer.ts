import { Server as SocketIOServer } from "socket.io";
import type { Server as HttpServer } from "http";
import * as bridge from "./pythonBridge";

let io: SocketIOServer | null = null;

export function initWebSocket(httpServer: HttpServer) {
  io = new SocketIOServer(httpServer, {
    cors: { origin: "*", methods: ["GET", "POST"] },
    path: "/api/ws",
  });

  io.on("connection", (socket) => {
    console.log(`[WS] Client connected: ${socket.id}`);
    socket.on("subscribe:pair",   (symbol: string) => socket.join(`pair:${symbol}`));
    socket.on("unsubscribe:pair", (symbol: string) => socket.leave(`pair:${symbol}`));
    socket.on("disconnect", () => console.log(`[WS] Client disconnected: ${socket.id}`));

    // Send initial state on connect
    const state = bridge.getSystemState();
    if (state) {
      socket.emit("price:update", {
        symbol: "BTCUSDT",
        price: state.price,
        change24h: 0,
        timestamp: new Date().toISOString(),
      });
    }
  });

  startRealDataPoller();
  return io;
}

export function getIO() { return io; }

// ─── Emit helpers ─────────────────────────────────────────────────────────────

export function emitPriceUpdate(data: { symbol: string; price: number; change24h: number }) {
  io?.to(`pair:${data.symbol}`).emit("price:update", { ...data, timestamp: new Date().toISOString() });
  io?.emit("price:update", { ...data, timestamp: new Date().toISOString() });
}

export function emitSignalTriggered(signal: {
  symbol: string; strategyId: string; direction: "LONG" | "SHORT"; price: number; confidence: number;
}) {
  io?.emit("signal:triggered", { ...signal, timestamp: new Date().toISOString() });
}

export function emitAlphaProgress(data: { symbol: string; phase: string; progress: number; message: string }) {
  io?.emit("alpha:progress", { ...data, timestamp: new Date().toISOString() });
}

export function emitTradeUpdate(trade: { tradeId: string; symbol: string; status: string; pnl?: string }) {
  io?.emit("trade:update", { ...trade, timestamp: new Date().toISOString() });
}

export function emitSystemEvent(event: { type: string; severity: "info" | "warning" | "error"; title: string; message: string }) {
  io?.emit("system:event", { ...event, timestamp: new Date().toISOString() });
}

// ─── Real data poller (replaces simulated emitter) ────────────────────────────

function startRealDataPoller() {
  let lastPrice            = 0;
  let lastStateTimestamp   = "";
  let lastMonitorAlive     = true;
  let lastDiscoveryAlive   = false;

  // Price update every 2 seconds from system_state.json
  setInterval(() => {
    if (!io) return;
    const state = bridge.getSystemState();
    if (!state) return;

    if (state.price !== lastPrice) {
      emitPriceUpdate({ symbol: "BTCUSDT", price: state.price, change24h: 0 });
      lastPrice = state.price;
    }
  }, 2000);

  // New signal alerts from alerts.log every 5 seconds
  setInterval(() => {
    if (!io) return;
    const state = bridge.getSystemState();
    const price = state?.price ?? 0;

    const newAlerts = bridge.getNewAlerts();
    for (const alert of newAlerts) {
      emitSignalTriggered({
        symbol:     "BTCUSDT",
        strategyId: alert.signalName,
        direction:  alert.direction,
        price,
        confidence: 0.75,
      });
      emitSystemEvent({
        type:     "signal_triggered",
        severity: "info",
        title:    `[${alert.phase}] ${alert.signalName}`,
        message:  alert.description || `${alert.signalName} → ${alert.direction} ${alert.bars}`,
      });
    }
  }, 5000);

  // System health / alpha progress every 10 seconds
  setInterval(() => {
    if (!io) return;
    const state = bridge.getSystemState();
    if (!state) return;

    // Emit system health changes
    if (state.timestamp !== lastStateTimestamp) {
      lastStateTimestamp = state.timestamp;

      // Monitor alive change
      if (state.monitor_alive !== lastMonitorAlive) {
        lastMonitorAlive = state.monitor_alive;
        emitSystemEvent({
          type:     state.monitor_alive ? "monitor_started" : "monitor_stopped",
          severity: state.monitor_alive ? "info" : "warning",
          title:    state.monitor_alive ? "监控引擎已启动" : "监控引擎已停止",
          message:  state.monitor_alive ? "run_monitor.py 正在运行，实时信号检测中" : "run_monitor.py 已退出，请检查 watchdog 日志",
        });
      }

      // Discovery alive change
      if (state.discovery_alive !== lastDiscoveryAlive) {
        lastDiscoveryAlive = state.discovery_alive;
        emitSystemEvent({
          type:     state.discovery_alive ? "discovery_started" : "discovery_stopped",
          severity: "info",
          title:    state.discovery_alive ? "Alpha发现引擎已启动" : "Alpha发现引擎已停止",
          message:  state.discovery_alive ? "run_live_discovery.py 正在运行" : "本轮发现任务已完成",
        });
      }

      // Alpha progress (if discovery running)
      if (state.discovery_alive) {
        const pending = bridge.getPendingRules("pending");
        emitAlphaProgress({
          symbol:   "BTCUSDT",
          phase:    "combo_scan",
          progress: 50,
          message:  `发现 ${pending.length} 个待审批候选策略`,
        });
      }
    }

    // Regime change notification (regime varies, emit periodically)
    if (state.regime) {
      const REGIME_LABELS: Record<string, string> = {
        QUIET_TREND:     "安静趋势",
        VOLATILE_TREND:  "波动趋势",
        RANGE_BOUND:     "震荡区间",
        VOL_EXPANSION:   "波动扩张",
        CRISIS:          "危机模式",
      };
      // Emit regime state every 30s (handled by 10s poll, emit every 3rd call)
    }
  }, 10000);
}
