import { useEffect, useRef, useState, useCallback } from "react";
import { io, Socket } from "socket.io-client";

export interface PriceUpdate {
  symbol: string;
  price: number;
  change24h: number;
  timestamp: string;
}

export interface SignalTriggered {
  symbol: string;
  strategyId: string;
  direction: "LONG" | "SHORT";
  price: number;
  confidence: number;
  timestamp: string;
}

export interface AlphaProgress {
  symbol: string;
  phase: string;
  progress: number;
  message: string;
  timestamp: string;
}

export interface TradeUpdate {
  tradeId: string;
  symbol: string;
  status: string;
  pnl?: string;
  timestamp: string;
}

export interface SystemEvent {
  type: string;
  severity: "info" | "warning" | "error";
  title: string;
  message: string;
  timestamp: string;
}

interface WSState {
  connected: boolean;
  prices: Record<string, PriceUpdate>;
  recentSignals: SignalTriggered[];
  alphaProgress: AlphaProgress | null;
  recentEvents: SystemEvent[];
  recentTrades: TradeUpdate[];
}

let globalSocket: Socket | null = null;
let globalListeners: Array<(state: WSState) => void> = [];
let globalState: WSState = {
  connected: false,
  prices: {},
  recentSignals: [],
  alphaProgress: null,
  recentEvents: [],
  recentTrades: [],
};

function notifyListeners() {
  globalListeners.forEach(fn => fn({ ...globalState }));
}

function getSocket(): Socket {
  if (!globalSocket) {
    globalSocket = io(window.location.origin, {
      path: "/api/ws",
      transports: ["websocket", "polling"],
    });

    globalSocket.on("connect", () => {
      globalState = { ...globalState, connected: true };
      notifyListeners();
    });

    globalSocket.on("disconnect", () => {
      globalState = { ...globalState, connected: false };
      notifyListeners();
    });

    globalSocket.on("price:update", (data: PriceUpdate) => {
      globalState = {
        ...globalState,
        prices: { ...globalState.prices, [data.symbol]: data },
      };
      notifyListeners();
    });

    globalSocket.on("signal:triggered", (data: SignalTriggered) => {
      globalState = {
        ...globalState,
        recentSignals: [data, ...globalState.recentSignals].slice(0, 20),
      };
      notifyListeners();
    });

    globalSocket.on("alpha:progress", (data: AlphaProgress) => {
      globalState = { ...globalState, alphaProgress: data };
      notifyListeners();
    });

    globalSocket.on("system:event", (data: SystemEvent) => {
      globalState = {
        ...globalState,
        recentEvents: [data, ...globalState.recentEvents].slice(0, 50),
      };
      notifyListeners();
    });

    globalSocket.on("trade:update", (data: TradeUpdate) => {
      globalState = {
        ...globalState,
        recentTrades: [data, ...globalState.recentTrades].slice(0, 20),
      };
      notifyListeners();
    });
  }
  return globalSocket;
}

export function useWebSocket() {
  const [state, setState] = useState<WSState>(globalState);

  useEffect(() => {
    const socket = getSocket();

    const listener = (newState: WSState) => setState(newState);
    globalListeners.push(listener);

    // Sync current state
    setState({ ...globalState });

    return () => {
      globalListeners = globalListeners.filter(fn => fn !== listener);
    };
  }, []);

  const subscribePair = useCallback((symbol: string) => {
    globalSocket?.emit("subscribe:pair", symbol);
  }, []);

  const unsubscribePair = useCallback((symbol: string) => {
    globalSocket?.emit("unsubscribe:pair", symbol);
  }, []);

  return { ...state, subscribePair, unsubscribePair };
}
