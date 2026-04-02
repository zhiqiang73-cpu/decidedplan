/**
 * binanceTestnet.ts
 * Direct REST calls to Binance Futures Testnet API.
 * Used for: connection verification, order history cross-reference.
 */
import crypto from "crypto";
import https from "https";

const TESTNET_BASE = "https://testnet.binancefuture.com";

function sign(queryString: string, secret: string): string {
  return crypto.createHmac("sha256", secret).update(queryString).digest("hex");
}

function httpsGet(url: string, apiKey: string): Promise<{ ok: boolean; status: number; data: unknown }> {
  return new Promise((resolve) => {
    const req = https.get(url, { headers: { "X-MBX-APIKEY": apiKey } }, (res) => {
      let body = "";
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try {
          resolve({ ok: res.statusCode === 200, status: res.statusCode ?? 0, data: JSON.parse(body) });
        } catch {
          resolve({ ok: false, status: res.statusCode ?? 0, data: body });
        }
      });
    });
    req.on("error", (err) => resolve({ ok: false, status: 0, data: err.message }));
    req.setTimeout(8000, () => { req.destroy(); resolve({ ok: false, status: 0, data: "timeout" }); });
  });
}

/**
 * Test Binance Testnet connectivity using the account endpoint.
 * Returns { success, message, latency, accountInfo? }
 */
export async function testConnection(apiKey: string, apiSecret: string) {
  if (!apiKey || !apiSecret) {
    return { success: false, message: "未配置API Key", latency: 0 };
  }

  const t0 = Date.now();
  const timestamp = Date.now();
  const qs = `timestamp=${timestamp}`;
  const sig = sign(qs, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v2/account?${qs}&signature=${sig}`;

  try {
    const res = await httpsGet(url, apiKey);
    const latency = Date.now() - t0;

    if (res.ok) {
      const data = res.data as Record<string, unknown>;
      return {
        success: true,
        message: `连接成功！总权益 ${parseFloat(String(data.totalWalletBalance ?? "0")).toFixed(2)} USDT`,
        latency,
        accountInfo: {
          totalWalletBalance: String(data.totalWalletBalance ?? "0"),
          availableBalance:   String(data.availableBalance ?? "0"),
          totalUnrealizedProfit: String(data.totalUnrealizedProfit ?? "0"),
        },
      };
    } else {
      const errData = res.data as Record<string, unknown>;
      const msg = String(errData.msg ?? "连接失败");
      return { success: false, message: `连接失败: ${msg}`, latency };
    }
  } catch (err) {
    return { success: false, message: `连接异常: ${String(err)}`, latency: Date.now() - t0 };
  }
}

/**
 * Fetch account balance snapshot from Binance Testnet.
 * Fallback when system_state.json is unavailable.
 */
export async function getAccountBalance(apiKey: string, apiSecret: string) {
  const timestamp = Date.now();
  const qs = `timestamp=${timestamp}`;
  const sig = sign(qs, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v2/account?${qs}&signature=${sig}`;

  try {
    const res = await httpsGet(url, apiKey);
    if (!res.ok) return null;
    const data = res.data as Record<string, unknown>;
    return {
      totalEquity:           String(data.totalWalletBalance ?? "0"),
      availableBalance:      String(data.availableBalance ?? "0"),
      usedMargin:            String(data.totalInitialMargin ?? "0"),
      unrealizedPnl:         String(data.totalUnrealizedProfit ?? "0"),
      assets: [{ asset: "USDT", balance: String(data.totalWalletBalance ?? "0"), unrealizedPnl: String(data.totalUnrealizedProfit ?? "0") }],
    };
  } catch {
    return null;
  }
}

/**
 * Fetch order history from Binance Testnet for cross-referencing with trades.csv.
 * Returns raw Binance order objects.
 */
export async function getOrderHistory(symbol: string, apiKey: string, apiSecret: string, limit = 50) {
  const timestamp = Date.now();
  const qs = `symbol=${symbol}&limit=${limit}&timestamp=${timestamp}`;
  const sig = sign(qs, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v1/allOrders?${qs}&signature=${sig}`;

  try {
    const res = await httpsGet(url, apiKey);
    if (!res.ok || !Array.isArray(res.data)) return [];
    return (res.data as Array<Record<string, unknown>>).map(o => ({
      orderId:     String(o.orderId ?? ""),
      symbol:      String(o.symbol ?? ""),
      side:        String(o.side ?? ""),
      type:        String(o.type ?? ""),
      status:      String(o.status ?? ""),
      price:       String(o.price ?? "0"),
      avgPrice:    String(o.avgPrice ?? "0"),
      origQty:     String(o.origQty ?? "0"),
      executedQty: String(o.executedQty ?? "0"),
      time:        Number(o.time ?? 0),
      updateTime:  Number(o.updateTime ?? 0),
    }));
  } catch {
    return [];
  }
}

export async function getOpenPositions(apiKey: string, apiSecret: string, symbol = "BTCUSDT") {
  const timestamp = Date.now();
  const qs = `timestamp=${timestamp}`;
  const sig = sign(qs, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v2/account?${qs}&signature=${sig}`;

  try {
    const res = await httpsGet(url, apiKey);
    if (!res.ok) return [];
    const data = res.data as { positions?: Array<Record<string, unknown>> };
    const positions = Array.isArray(data.positions) ? data.positions : [];
    return positions
      .filter((p) => String(p.symbol ?? "") === symbol)
      .map((p) => {
        const amt = Number(p.positionAmt ?? 0);
        const entry = Number(p.entryPrice ?? 0);
        if (!Number.isFinite(amt) || Math.abs(amt) <= 1e-12 || !Number.isFinite(entry) || entry <= 0) return null;
        return {
          symbol,
          direction: amt > 0 ? "LONG" : "SHORT",
          quantity: Math.abs(amt),
          entryPrice: entry,
          unrealizedPnl: Number(p.unRealizedProfit ?? 0),
        };
      })
      .filter((v): v is { symbol: string; direction: "LONG" | "SHORT"; quantity: number; entryPrice: number; unrealizedPnl: number } => v !== null);
  } catch {
    return [];
  }
}

export async function getOpenOrders(symbol: string, apiKey: string, apiSecret: string) {
  const timestamp = Date.now();
  const qs = `symbol=${symbol}&timestamp=${timestamp}`;
  const sig = sign(qs, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v1/openOrders?${qs}&signature=${sig}`;

  try {
    const res = await httpsGet(url, apiKey);
    if (!res.ok || !Array.isArray(res.data)) return [];
    return (res.data as Array<Record<string, unknown>>).map((o) => ({
      orderId: String(o.orderId ?? ""),
      symbol: String(o.symbol ?? symbol),
      side: String(o.side ?? ""),
      type: String(o.type ?? ""),
      price: Number(o.price ?? 0),
      origQty: Number(o.origQty ?? 0),
      executedQty: Number(o.executedQty ?? 0),
      status: String(o.status ?? ""),
    }));
  } catch {
    return [];
  }
}
