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
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        try {
          resolve({ ok: res.statusCode === 200, status: res.statusCode ?? 0, data: JSON.parse(body) });
        } catch {
          resolve({ ok: false, status: res.statusCode ?? 0, data: body });
        }
      });
    });
    req.on("error", (err) => resolve({ ok: false, status: 0, data: err.message }));
    req.setTimeout(8000, () => {
      req.destroy();
      resolve({ ok: false, status: 0, data: "timeout" });
    });
  });
}

export async function testConnection(apiKey: string, apiSecret: string) {
  if (!apiKey || !apiSecret) {
    return { success: false, message: "未配置 API Key", latency: 0 };
  }

  const startedAt = Date.now();
  const timestamp = Date.now();
  const queryString = `timestamp=${timestamp}`;
  const signature = sign(queryString, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v2/account?${queryString}&signature=${signature}`;

  try {
    const response = await httpsGet(url, apiKey);
    const latency = Date.now() - startedAt;

    if (response.ok) {
      const data = response.data as Record<string, unknown>;
      return {
        success: true,
        message: `连接成功，总权益 ${parseFloat(String(data.totalWalletBalance ?? "0")).toFixed(2)} USDT`,
        latency,
        accountInfo: {
          totalWalletBalance: String(data.totalWalletBalance ?? "0"),
          availableBalance: String(data.availableBalance ?? "0"),
          totalUnrealizedProfit: String(data.totalUnrealizedProfit ?? "0"),
        },
      };
    }

    const errorData = response.data as Record<string, unknown>;
    const message = String(errorData.msg ?? "连接失败");
    return { success: false, message: `连接失败: ${message}`, latency };
  } catch (error) {
    return { success: false, message: `连接异常: ${String(error)}`, latency: Date.now() - startedAt };
  }
}

export async function getAccountBalance(apiKey: string, apiSecret: string) {
  const timestamp = Date.now();
  const queryString = `timestamp=${timestamp}`;
  const signature = sign(queryString, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v2/account?${queryString}&signature=${signature}`;

  try {
    const response = await httpsGet(url, apiKey);
    if (!response.ok) return null;

    const data = response.data as Record<string, unknown>;
    const walletBalance = Number(data.totalWalletBalance ?? "0");
    const unrealizedPnl = Number(data.totalUnrealizedProfit ?? "0");
    return {
      totalEquity: String(walletBalance + unrealizedPnl),
      availableBalance: String(data.availableBalance ?? "0"),
      usedMargin: String(data.totalInitialMargin ?? "0"),
      unrealizedPnl: String(unrealizedPnl),
      assets: [{ asset: "USDT", balance: String(walletBalance), unrealizedPnl: String(unrealizedPnl) }],
    };
  } catch {
    return null;
  }
}

export async function getOrderHistory(symbol: string, apiKey: string, apiSecret: string, limit = 50) {
  const timestamp = Date.now();
  const queryString = `symbol=${symbol}&limit=${limit}&timestamp=${timestamp}`;
  const signature = sign(queryString, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v1/allOrders?${queryString}&signature=${signature}`;

  try {
    const response = await httpsGet(url, apiKey);
    if (!response.ok || !Array.isArray(response.data)) return [];
    return (response.data as Array<Record<string, unknown>>).map((order) => ({
      orderId: String(order.orderId ?? ""),
      symbol: String(order.symbol ?? ""),
      side: String(order.side ?? ""),
      type: String(order.type ?? ""),
      status: String(order.status ?? ""),
      price: String(order.price ?? "0"),
      avgPrice: String(order.avgPrice ?? "0"),
      origQty: String(order.origQty ?? "0"),
      executedQty: String(order.executedQty ?? "0"),
      time: Number(order.time ?? 0),
      updateTime: Number(order.updateTime ?? 0),
    }));
  } catch {
    return [];
  }
}

export async function getOpenPositions(apiKey: string, apiSecret: string, symbol = "BTCUSDT") {
  const timestamp = Date.now();
  const queryString = `timestamp=${timestamp}`;
  const signature = sign(queryString, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v2/account?${queryString}&signature=${signature}`;

  try {
    const response = await httpsGet(url, apiKey);
    if (!response.ok) return [];
    const data = response.data as { positions?: Array<Record<string, unknown>> };
    const positions = Array.isArray(data.positions) ? data.positions : [];
    return positions
      .filter((position) => String(position.symbol ?? "") === symbol)
      .map((position) => {
        const amount = Number(position.positionAmt ?? 0);
        const entryPrice = Number(position.entryPrice ?? 0);
        if (!Number.isFinite(amount) || Math.abs(amount) <= 1e-12 || !Number.isFinite(entryPrice) || entryPrice <= 0) {
          return null;
        }
        return {
          symbol,
          direction: amount > 0 ? "LONG" : "SHORT",
          quantity: Math.abs(amount),
          entryPrice,
          unrealizedPnl: Number(position.unRealizedProfit ?? 0),
        };
      })
      .filter(
        (
          value,
        ): value is { symbol: string; direction: "LONG" | "SHORT"; quantity: number; entryPrice: number; unrealizedPnl: number } =>
          value !== null,
      );
  } catch {
    return [];
  }
}

export async function getOpenOrders(symbol: string, apiKey: string, apiSecret: string) {
  const timestamp = Date.now();
  const queryString = `symbol=${symbol}&timestamp=${timestamp}`;
  const signature = sign(queryString, apiSecret);
  const url = `${TESTNET_BASE}/fapi/v1/openOrders?${queryString}&signature=${signature}`;

  try {
    const response = await httpsGet(url, apiKey);
    if (!response.ok || !Array.isArray(response.data)) return [];
    return (response.data as Array<Record<string, unknown>>).map((order) => ({
      orderId: String(order.orderId ?? ""),
      symbol: String(order.symbol ?? symbol),
      side: String(order.side ?? ""),
      type: String(order.type ?? ""),
      price: Number(order.price ?? 0),
      origQty: Number(order.origQty ?? 0),
      executedQty: Number(order.executedQty ?? 0),
      updateTime: Number(order.updateTime ?? 0),
      status: String(order.status ?? "NEW"),
    }));
  } catch {
    return [];
  }
}
