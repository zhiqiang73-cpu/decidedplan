import { COOKIE_NAME } from "@shared/const";
import { systemRouter } from "./_core/systemRouter";
import { getSessionCookieOptions } from "./_core/cookies";
import { publicProcedure, router } from "./_core/trpc";
import { z } from "zod";
import { nanoid } from "nanoid";
import { invokeLLM } from "./_core/llm";
import * as bridge from "./pythonBridge";
import * as binance from "./binanceTestnet";
import { spawn, type ChildProcess } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

// --- Alpha discovery process handle ---
let _discoveryProcess: ChildProcess | null = null;
const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const SYSTEM_STATE_PATH = path.join(PROJECT_ROOT, "monitor/output/system_state.json");
const DATA_SYNC_CONTROL_PATH = path.join(PROJECT_ROOT, "monitor/output/data_sync_control.json");

function getDataSyncStatus() {
  try {
    const raw = fs.readFileSync(DATA_SYNC_CONTROL_PATH, "utf8");
    const parsed = JSON.parse(raw) as { enabled?: boolean } | null;
    const stats = fs.statSync(DATA_SYNC_CONTROL_PATH);
    const ageMs = Math.max(0, Date.now() - stats.mtimeMs);
    const requestedEnabled = parsed?.enabled === true;
    return {
      enabled: requestedEnabled,
      requestedEnabled,
      stalePause: false,
      updatedAt: new Date(stats.mtimeMs).toISOString(),
      ageSeconds: Math.floor(ageMs / 1000),
    };
  } catch {
    return {
      enabled: false,
      requestedEnabled: false,
      stalePause: false,
      updatedAt: null,
      ageSeconds: null,
    };
  }
}

function writeDataSyncControl(enabled: boolean) {
  fs.mkdirSync(path.dirname(DATA_SYNC_CONTROL_PATH), { recursive: true });
  fs.writeFileSync(
    DATA_SYNC_CONTROL_PATH,
    JSON.stringify({
      enabled,
      updated_at: new Date().toISOString(),
      source: "ui",
    }, null, 2),
  );
}

type LiveSnapshotPosition = {
  positionId: string;
  signalName: string;
  strategyFamily: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  entryPrice: number;
  entryAt: Date | null;
  confidence: number;
  exitLogic: string | null;
  source: "system" | "exchange";
  isExternal: boolean;
  leverage: number;
  markPrice: number | null;
  notional: number;
  usedMargin: number;
  unrealizedPnl: number | null;
  unrealizedPnlPct: number | null;
};

type LiveSnapshotPendingOrder = {
  orderId: string;
  signalName: string;
  quantity: number;
  requestedPrice: number;
  source: "system" | "exchange";
};

function isExternalSystemPosition(position: bridge.Position): boolean {
  const family = String(position.family ?? "").toLowerCase();
  const signalName = String(position.signal_name ?? "").toLowerCase();
  return family === "external" || signalName === "external_position";
}

function mapSystemPositions(state: ReturnType<typeof bridge.getSystemState>): LiveSnapshotPosition[] {
  const markPrice = Number(state?.price ?? 0);
  return (state?.positions ?? []).map((p, idx) => {
    const entryPrice = Number(p.entry_price ?? 0);
    const quantity = Number(p.qty ?? 0);
    const leverage = 10;
    const notional = Math.abs(entryPrice * quantity);
    const usedMargin = leverage > 0 ? notional / leverage : notional;
    const priceMovePct = Number.isFinite(p.unrealized_pnl_pct) ? Number(p.unrealized_pnl_pct) : null;
    const unrealizedPnl = priceMovePct !== null ? (priceMovePct / 100) * notional : null;
    const unrealizedPnlPct = unrealizedPnl !== null && usedMargin > 0
      ? (unrealizedPnl / usedMargin) * 100
      : null;
    const isExternal = isExternalSystemPosition(p);

    return {
      positionId: `LIVE-${p.signal_name || idx + 1}-${p.entry_time || idx + 1}`,
      signalName: p.signal_name,
      strategyFamily: isExternal ? "EXCHANGE" : p.family,
      symbol: state?.symbol ?? "BTCUSDT",
      direction: (p.direction ?? "").toUpperCase() === "LONG" ? "LONG" : "SHORT",
      quantity,
      entryPrice,
      entryAt: p.entry_time ? new Date(p.entry_time) : null,
      confidence: p.confidence ?? 0,
      exitLogic: p.exit_logic ?? null,
      source: isExternal ? "exchange" : "system",
      isExternal,
      leverage,
      markPrice: Number.isFinite(markPrice) && markPrice > 0 ? markPrice : null,
      notional,
      usedMargin,
      unrealizedPnl,
      unrealizedPnlPct,
    };
  });
}

function mapSystemPendingOrders(state: ReturnType<typeof bridge.getSystemState>): LiveSnapshotPendingOrder[] {
  return (state?.pending_orders ?? []).map((o, idx) => ({
    orderId: o.order_id || `PENDING-${idx + 1}`,
    signalName: o.signal_name,
    quantity: o.qty,
    requestedPrice: o.requested_price,
    source: "system",
  }));
}

function isSamePosition(
  left: LiveSnapshotPosition,
  right: { symbol: string; direction: "LONG" | "SHORT"; quantity: number; entryPrice: number },
): boolean {
  const sameSymbol = left.symbol === right.symbol;
  const sameDirection = left.direction === right.direction;
  const qtyGap = Math.abs((left.quantity ?? 0) - (right.quantity ?? 0));
  const entryGap = Math.abs((left.entryPrice ?? 0) - (right.entryPrice ?? 0));
  return sameSymbol && sameDirection && qtyGap <= 1e-4 && entryGap <= 5;
}

function mergeExchangePositions(
  localPositions: LiveSnapshotPosition[],
  exchangePositions: Array<{
    symbol: string;
    direction: "LONG" | "SHORT";
    quantity: number;
    entryPrice: number;
    markPrice: number | null;
    leverage: number;
    notional: number;
    usedMargin: number;
    unrealizedPnl: number;
    unrealizedPnlPct: number | null;
  }>,
): LiveSnapshotPosition[] {
  if (!exchangePositions.length) {
    return localPositions;
  }

  const merged = [...localPositions];
  exchangePositions.forEach((position, idx) => {
    const existingIndex = merged.findIndex((existing) => isSamePosition(existing, position));
    if (existingIndex >= 0) {
      const existing = merged[existingIndex];
      merged[existingIndex] = {
        ...existing,
        leverage: position.leverage || existing.leverage,
        markPrice: position.markPrice ?? existing.markPrice,
        notional: position.notional || existing.notional,
        usedMargin: position.usedMargin || existing.usedMargin,
        unrealizedPnl: position.unrealizedPnl,
        unrealizedPnlPct: position.unrealizedPnlPct,
      };
      return;
    }
    merged.push({
      positionId: `EXCHANGE-${idx + 1}-${position.direction}-${position.entryPrice}`,
      signalName: "exchange_sync",
      strategyFamily: "EXCHANGE",
      symbol: position.symbol,
      direction: position.direction,
      quantity: position.quantity,
      entryPrice: position.entryPrice,
      entryAt: null,
      confidence: 0,
      exitLogic: null,
      source: "exchange",
      isExternal: true,
      leverage: position.leverage,
      markPrice: position.markPrice,
      notional: position.notional,
      usedMargin: position.usedMargin,
      unrealizedPnl: position.unrealizedPnl,
      unrealizedPnlPct: position.unrealizedPnlPct,
    });
  });
  return merged;
}

function mergeExchangePendingOrders(
  localOrders: LiveSnapshotPendingOrder[],
  exchangeOrders: Array<{
    orderId: string;
    symbol: string;
    side: string;
    type: string;
    price: number;
    origQty: number;
  }>,
): LiveSnapshotPendingOrder[] {
  if (!exchangeOrders.length) {
    return localOrders;
  }

  const existingIds = new Set(localOrders.map((order) => String(order.orderId)));
  const merged = [...localOrders];
  exchangeOrders.forEach((order) => {
    if (existingIds.has(String(order.orderId))) {
      return;
    }
    merged.push({
      orderId: String(order.orderId),
      signalName: `${order.side} ${order.type}`,
      quantity: order.origQty,
      requestedPrice: order.price,
      source: "exchange",
    });
  });
  return merged;
}

function _patchDiscoveryAlive(alive: boolean) {
  try {
    const raw = fs.existsSync(SYSTEM_STATE_PATH) ? fs.readFileSync(SYSTEM_STATE_PATH, "utf8") : "{}";
    const obj = JSON.parse(raw);
    obj.discovery_alive = alive;
    fs.writeFileSync(SYSTEM_STATE_PATH, JSON.stringify(obj, null, 2));
  } catch { /* ignore */ }
}

// 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?In-memory alpha engine state (reflects Python discovery process) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂?
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

// Strategy status overrides (paused/retired per user action, persisted in memory for now)
const strategyStatusOverrides: Record<string, "active" | "paused" | "degraded" | "retired"> = {};

const LIVE_ACTIVE_STRATEGY_STATUSES = new Set([
  "trade_ready",
  "partial_trade_ready",
  "warming_up",
  "warmup",
  "running",
  "enabled",
  "live",
]);
type StorageCadence = "realtime" | "historical";
type StorageHealth = "healthy" | "stale" | "missing";

type DataStorageDataset = {
  key: string;
  label: string;
  description: string;
  path: string;
  fileCount: number;
  totalBytes: number;
  latestModifiedAt: Date | null;
  health: StorageHealth;
  cadence: StorageCadence;
};

type DataStorageOverview = {
  symbol: string;
  rootPath: string;
  healthyDatasets: number;
  totalDatasets: number;
  totalFiles: number;
  totalBytes: number;
  approxKlines: number;
  lastUpdatedAt: Date | null;
  datasets: DataStorageDataset[];
};

const DATA_STORAGE_ROOT = path.join(PROJECT_ROOT, "data/storage");
const DATA_STORAGE_CACHE_TTL_MS = 15_000;

const DATASET_DEFINITIONS: Array<{
  key: string;
  label: string;
  description: string;
  cadence: StorageCadence;
  approxRowsPerFile?: number;
}> = [
  { key: "klines", label: "\u4e00\u5206 K\u7ebf", description: "1m K\u7ebf\u5386\u53f2\u4e3b\u6570\u636e", cadence: "historical", approxRowsPerFile: 1440 },
  { key: "agg_trades", label: "\u9010\u7b14\u6210\u4ea4", description: "\u6210\u4ea4\u5fae\u89c2\u7ed3\u6784\u4e0e\u4e3b\u52a8\u6027\u7279\u5f81", cadence: "realtime" },
  { key: "book_ticker", label: "\u76d8\u53e3", description: "\u4e70\u4e00\u5356\u4e00\u4e0e\u6df1\u5ea6\u5fae\u7ed3\u6784", cadence: "realtime" },
  { key: "mark_price", label: "\u6807\u8bb0\u4ef7", description: "\u8d44\u91d1\u8d39\u7387\u3001basis \u4e0e\u5012\u8ba1\u65f6", cadence: "realtime" },
  { key: "liquidations", label: "\u7206\u4ed3", description: "\u5f3a\u5e73\u538b\u529b\u4e0e\u7206\u4ed3\u5bc6\u5ea6", cadence: "realtime" },
  { key: "funding_rate", label: "\u8d44\u91d1\u8d39\u7387", description: "\u5386\u53f2 funding rate \u5e8f\u5217", cadence: "historical" },
  { key: "long_short_ratio", label: "\u591a\u7a7a\u6bd4", description: "\u8d26\u6237\u4fa7 long/short ratio", cadence: "historical" },
  { key: "open_interest", label: "\u6301\u4ed3\u91cf", description: "\u5386\u53f2 OI \u66f2\u7ebf", cadence: "historical" },
  { key: "taker_ratio", label: "taker \u6bd4\u7387", description: "\u4e3b\u52a8\u4e70\u5356\u529b\u6bd4\u7387", cadence: "historical" },
];

let dataStorageOverviewCache:
  | { expiresAt: number; value: DataStorageOverview }
  | null = null;

function toProjectRelativePath(targetPath: string) {
  return path.relative(PROJECT_ROOT, targetPath).split(path.sep).join("/") || ".";
}

function scanStorageTree(rootPath: string) {
  if (!fs.existsSync(rootPath)) {
    return { fileCount: 0, totalBytes: 0, latestModifiedAt: null as Date | null };
  }

  const stack = [rootPath];
  let fileCount = 0;
  let totalBytes = 0;
  let latestModifiedAt: Date | null = null;

  while (stack.length > 0) {
    const currentPath = stack.pop()!;
    for (const entry of fs.readdirSync(currentPath, { withFileTypes: true })) {
      const fullPath = path.join(currentPath, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
        continue;
      }
      if (!entry.isFile()) continue;
      const stats = fs.statSync(fullPath);
      fileCount += 1;
      totalBytes += stats.size;
      if (!latestModifiedAt || stats.mtime > latestModifiedAt) {
        latestModifiedAt = stats.mtime;
      }
    }
  }

  return { fileCount, totalBytes, latestModifiedAt };
}

function classifyStorageHealth(cadence: StorageCadence, latestModifiedAt: Date | null, fileCount: number): StorageHealth {
  if (fileCount === 0 || !latestModifiedAt) return "missing";
  const ageMs = Date.now() - latestModifiedAt.getTime();
  const healthyThresholdMs = cadence === "realtime" ? 30 * 60 * 1000 : 48 * 60 * 60 * 1000;
  return ageMs <= healthyThresholdMs ? "healthy" : "stale";
}

function buildDataStorageOverview(): DataStorageOverview {
  if (dataStorageOverviewCache && dataStorageOverviewCache.expiresAt > Date.now()) {
    return dataStorageOverviewCache.value;
  }

  const datasets: DataStorageDataset[] = DATASET_DEFINITIONS.map((dataset) => {
    const datasetPath = path.join(DATA_STORAGE_ROOT, dataset.key);
    const stats = scanStorageTree(datasetPath);
    return {
      key: dataset.key,
      label: dataset.label,
      description: dataset.description,
      path: toProjectRelativePath(datasetPath),
      fileCount: stats.fileCount,
      totalBytes: stats.totalBytes,
      latestModifiedAt: stats.latestModifiedAt,
      health: classifyStorageHealth(dataset.cadence, stats.latestModifiedAt, stats.fileCount),
      cadence: dataset.cadence,
    };
  });

  const totalFiles = datasets.reduce((sum, dataset) => sum + dataset.fileCount, 0);
  const totalBytes = datasets.reduce((sum, dataset) => sum + dataset.totalBytes, 0);
  const klinesDef = DATASET_DEFINITIONS.find((dataset) => dataset.key === "klines");
  const approxKlines = datasets
    .filter((dataset) => dataset.key === "klines")
    .reduce((sum, dataset) => sum + dataset.fileCount * (klinesDef?.approxRowsPerFile ?? 0), 0);
  const healthyDatasets = datasets.filter((dataset) => dataset.health === "healthy").length;
  const lastUpdatedAt = datasets.reduce<Date | null>((latest, dataset) => {
    if (!dataset.latestModifiedAt) return latest;
    if (!latest || dataset.latestModifiedAt > latest) return dataset.latestModifiedAt;
    return latest;
  }, null);

  const overview: DataStorageOverview = {
    symbol: bridge.getSystemState()?.symbol ?? "BTCUSDT",
    rootPath: toProjectRelativePath(DATA_STORAGE_ROOT),
    healthyDatasets,
    totalDatasets: datasets.length,
    totalFiles,
    totalBytes,
    approxKlines,
    lastUpdatedAt,
    datasets,
  };

  dataStorageOverviewCache = {
    expiresAt: Date.now() + DATA_STORAGE_CACHE_TTL_MS,
    value: overview,
  };

  return overview;
}

// 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?App Router 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
const P1_KNOWN_HORIZONS: Record<string, number> = {
  "P0-2": 30, "P1-2": 30, "P1-6": 30, "P1-9": 30,
  "P1-10": 20, "P1-11": 20, "P1-12": 30, "P1-13": 30, "P1-14": 60,
  "C1": 30, "OA-1": 30, "RT-1": 30,
  "T1-1": 3, "T1-2": 5, "T1-3": 10,
  "A3-OI": 30, "A4-PIR": 30,
};

const P1_DEFAULT_STOP_PCT: Record<string, number> = {
  "P0-2": 1.5, "P1-2": 0.3, "P1-6": 0.7, "P1-9": 0.3,
  "P1-10": 0.7, "P1-11": 1.5, "P1-12": 0.5, "P1-13": 0.3, "P1-14": 1.5,
  "C1": 0.35, "OA-1": 0.7, "RT-1": 0.4,
  "T1-1": 0.05, "T1-2": 0.05, "T1-3": 0.05,
  "A3-OI": 0.7, "A4-PIR": 0.5,
};

export const appRouter = router({
  system: systemRouter,

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Auth (local 闂?no OAuth) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return { success: true } as const;
    }),
  }),
  apiConfig: router({
    get: publicProcedure.query(() => {
      const cfg = bridge.getEnvConfig();
      return {
        id: 1,
        userId: 1,
        exchange: "binance",
        isTestnet: true,
        isActive: cfg.hasConfig,
        apiKey: cfg.apiKey || null,
        apiSecret: cfg.hasConfig ? "****************" : null,
        lastTestStatus: cfg.hasConfig ? "success" : "pending",
        lastTestedAt: null,
      };
    }),
    save: publicProcedure.input(z.object({
      apiKey: z.string().min(1),
      apiSecret: z.string().min(1),
      isTestnet: z.boolean().default(true),
    })).mutation(({ input }) => {
      bridge.saveEnvConfig(input.apiKey, input.apiSecret);
      return { success: true };
    }),
    testConnection: publicProcedure.mutation(async () => {
      const cfg = bridge.getEnvConfig();
      if (!cfg.hasConfig) return { success: false, message: "\u8bf7\u5148\u914d\u7f6e Binance API Key \u548c Secret", latency: 0 };
      const result = await binance.testConnection(cfg.apiKey, cfg.apiSecret);
      return result;
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Wallet 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
  wallet: router({
    getSnapshot: publicProcedure.query(async () => {
      const state = bridge.getSystemState();

      const cfg = bridge.getEnvConfig();
      if (cfg.hasConfig) {
        const acct = await binance.getAccountBalance(cfg.apiKey, cfg.apiSecret);
        if (acct) {
          return {
            id: 1,
            snapshotAt: new Date(),
            source: "exchange" as const,
            sourceLabel: "交易所实时快照",
            ...acct,
          };
        }
      }
      if (state) {
        const positions = mapSystemPositions(state);
        const usedMargin = positions.reduce((sum, position) => sum + (position.usedMargin ?? 0), 0);
        const unrealizedPnl = positions.reduce((sum, position) => sum + (position.unrealizedPnl ?? 0), 0);
        const totalEquity = state.balance + unrealizedPnl;
        const availableBalance = Math.max(state.balance - usedMargin, 0);
        return {
          id: 1,
          snapshotAt: new Date(state.timestamp),
          source: "system_state" as const,
          sourceLabel: "本地 system_state 回退",
          totalEquity: totalEquity.toFixed(4),
          availableBalance: availableBalance.toFixed(4),
          usedMargin: usedMargin.toFixed(4),
          unrealizedPnl: unrealizedPnl.toFixed(4),
          assets: [
            { asset: "USDT", balance: state.balance.toFixed(4), unrealizedPnl: unrealizedPnl.toFixed(4) },
          ],
        };
      }
      return null;
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Trading Pairs 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂?
  execution: router({
    getLiveSnapshot: publicProcedure.query(async () => {
      const state = bridge.getSystemState();

      const base = {
        timestamp: state?.timestamp ? new Date(state.timestamp) : null,
        marketTimestamp: state?.market_timestamp ? new Date(state.market_timestamp) : null,
        symbol: state?.symbol ?? "BTCUSDT",
        price: state?.price ?? 0,
      };

      let positions = mapSystemPositions(state);
      let pendingOrders = mapSystemPendingOrders(state);

      const cfg = bridge.getEnvConfig();
      if (cfg.hasConfig && state?.symbol) {
        const [exPositions, exOrders] = await Promise.all([
          binance.getOpenPositions(cfg.apiKey, cfg.apiSecret, state.symbol),
          binance.getOpenOrders(state.symbol, cfg.apiKey, cfg.apiSecret),
        ]);
        positions = mergeExchangePositions(positions, exPositions);
        pendingOrders = mergeExchangePendingOrders(pendingOrders, exOrders);
      }

      return {
        ...base,
        positions,
        pendingOrders,
      };
    }),
  }),

  tradingPairs: router({
    list: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      return [{
        id: 1,
        symbol: "BTCUSDT",
        baseAsset: "BTC",
        quoteAsset: "USDT",
        isTracked: true,
        dataCollectionStatus: "completed",
        dataDownloadProgress: 100,
        alphaEngineStatus: state?.discovery_alive ? "scanning" : "idle",
        currentPrice:     String(state?.price ?? 0),
        priceChange24h:   0,
        volume24h:        null,
        totalKlines:      504000,  // ~18 months of 1m data
        dataQualityScore: 99,
        lastDataUpdate:   state ? new Date(state.timestamp) : null,
      }];
    }),
    get: publicProcedure.input(z.object({ symbol: z.string() })).query(({ input }) => {
      if (input.symbol !== "BTCUSDT") return null;
      const state = bridge.getSystemState();
      return {
        id: 1, symbol: "BTCUSDT", baseAsset: "BTC", quoteAsset: "USDT",
        isTracked: true, dataCollectionStatus: "completed", dataDownloadProgress: 100,
        alphaEngineStatus: state?.discovery_alive ? "scanning" : "idle",
        currentPrice: String(state?.price ?? 0), priceChange24h: 0, volume24h: null,
      };
    }),
    add: publicProcedure.input(z.object({ symbol: z.string() })).mutation(({ input }) => {
      return {
        success: false,
        symbol: input.symbol,
        message: "\u5f53\u524d\u6267\u884c\u4e3b\u94fe\u4ec5\u652f\u6301 BTCUSDT\uff0c\u4ea4\u6613\u5bf9\u7ef4\u62a4\u5165\u53e3\u672a\u63a5\u5165 live \u4e3b\u94fe\u3002",
      };
    }),
    updateStatus: publicProcedure.input(z.object({
      symbol: z.string(),
      dataDownloadProgress: z.number().optional(),
      alphaEngineStatus: z.string().optional(),
      dataCollectionStatus: z.string().optional(),
    })).mutation(() => ({
      success: false,
      message: "\u5f53\u524d\u9875\u9762\u4e3a\u53ea\u8bfb\u5c55\u793a\uff0c\u72b6\u6001\u7531\u4e3b\u94fe\u8fdb\u7a0b\u81ea\u52a8\u7ef4\u62a4\u3002",
    })),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Strategies 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷?
  strategies: router({
    list: publicProcedure.input(z.object({
      type:   z.enum(["P1", "P2", "ALPHA"]).optional(),
      status: z.string().optional(),
      symbol: z.string().optional(),
      search: z.string().optional(),
    }).nullish()).query(({ input }) => {
      const state = bridge.getSystemState();
      const bestExitParams = bridge.getBestExitParams();

      /** 查找某 family+direction 组合的止损比例，先查 best_params.json，再查默认表 */
      function resolveStopPct(family: string, direction: string): { pct: number | null; source: "optimized" | "default" | null } {
        const dirKey = `${family}|${direction.toLowerCase()}`;
        const fromBest = bestExitParams[dirKey]?.stop_pct ?? bestExitParams[family]?.stop_pct;
        if (fromBest != null) return { pct: fromBest, source: "optimized" };
        const fromDefault = P1_DEFAULT_STOP_PCT[family];
        if (fromDefault != null) return { pct: fromDefault, source: "default" };
        return { pct: null, source: null };
      }

      const trades = bridge.getTrades({ limit: 2000 });

      // Build win rate per signal family from trades
      const familyStats: Record<string, { wins: number; total: number; pnl7d: number; netReturnPctSum: number }> = {};
      const cutoff7d = Date.now() - 7 * 86400000;
      for (const t of trades) {
        if (t.status !== "closed") continue;
        const f = t.strategyId.split("_")[0] ?? t.strategyId;
        if (!familyStats[f]) familyStats[f] = { wins: 0, total: 0, pnl7d: 0, netReturnPctSum: 0 };
        familyStats[f].total++;
        if (parseFloat(t.pnl ?? "0") > 0) familyStats[f].wins++;
        familyStats[f].netReturnPctSum += parseFloat(t.pnlPercent ?? "0") || 0;
        if (t.exitAt && t.exitAt.getTime() > cutoff7d) {
          familyStats[f].pnl7d += parseFloat(t.pnl ?? "0");
        }
      }

      // Map strategies from system_state.json
      const p1strategies = (state?.strategies ?? []).map((s: any, i) => {
        const typePrefix = s.family.startsWith("P0") ? "P1" : s.family.startsWith("C") ? "P1" : "P1";
        const stats = familyStats[s.family] ?? { wins: 0, total: 0, pnl7d: 0, netReturnPctSum: 0 };
        const liveWR = stats.total > 0 ? (stats.wins / stats.total) * 100 : null;
        const liveAvgReturnPct = stats.total > 0 ? stats.netReturnPctSum / stats.total : null;
        const validationWR = s.oos_win_rate ?? null;
        const override = strategyStatusOverrides[s.family];
        const rawLiveStatus = (s.status ?? "").toLowerCase();
        const status = override ?? (LIVE_ACTIVE_STRATEGY_STATUSES.has(rawLiveStatus) ? "active" : "paused");
        return {
          strategyId:   s.family,
          name:         s.name,
          type:         typePrefix as "P1",
          direction:    s.direction.toUpperCase() as "LONG" | "SHORT" | "BOTH",
          symbol:       "BTCUSDT",
          entryCondition: s.entry_conditions,
          exitConditionTop3: [{ label: s.exit_conditions }] as Array<{ label: string }>,
          liveWinRate:  liveWR,
          validationWinRate: validationWR,
          liveAvgReturnPct,
          closedSampleSize: stats.total,
          oosWinRate:   validationWR,
          oosAvgReturn: null,
          mechanismType: null,
          oosSampleSize: 0,
          confidenceScore: s.status === "trade_ready" ? 0.8 : 0.5,
          overfitScore:  0.2,
          featureDiversityScore: 0.7,
          status,
          todayTriggers: s.today.triggers,
          todayWins:    s.today.wins,
          notFilled:    s.today.not_filled,
          pnl7d:        stats.pnl7d.toFixed(4),
          backtestStatus: "idle",
          backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null,
          lastBacktestAt: null,
          approvedAt:   null,
          params:       null,
          stopPct:      resolveStopPct(s.family, s.direction.toLowerCase()).pct,
          stopSource:   resolveStopPct(s.family, s.direction.toLowerCase()).source,
          horizon:      P1_KNOWN_HORIZONS[s.family] ?? null,
          updatedAt:    new Date(),
        };
      });

      // Approved alpha rules
      const approved = bridge.getApprovedRules() as bridge.PendingRule[];
      // Lookup table: family -> Chinese name from system_state strategies
      const zhNameMap: Record<string, string> = {};
      for (const s of (state?.strategies ?? [])) {
        if (s.family && s.name && s.name !== s.family) zhNameMap[s.family] = s.name;
      }

      const alphaStrategies = approved.filter(r => r.status === "approved").map(r => {
        const alphaFamily = String((r as any).family ?? "").trim();
        const strategyId = `ALPHA-${r.id}`;
        const stats = familyStats[strategyId] ?? familyStats[alphaFamily] ?? { wins: 0, total: 0, pnl7d: 0, netReturnPctSum: 0 };
        const liveWR = stats.total > 0 ? (stats.wins / stats.total) * 100 : null;
        const liveAvgReturnPct = stats.total > 0 ? stats.netReturnPctSum / stats.total : null;
        const override = strategyStatusOverrides[strategyId];
        return {
          strategyId,
          name:         zhNameMap[alphaFamily] ?? r.group ?? r.rule_str ?? r.id,
          type:         "ALPHA" as const,
          direction:    (r.entry.direction.toUpperCase() as "LONG" | "SHORT"),
          symbol:       "BTCUSDT",
          entryCondition: r.rule_str ?? "",
          exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })),
          liveWinRate:  liveWR,
          validationWinRate: r.stats.oos_win_rate,
          liveAvgReturnPct,
          closedSampleSize: stats.total,
          oosWinRate:   r.stats.oos_win_rate,
          oosAvgReturn: r.stats.oos_avg_ret,
          mechanismType: (r as any).mechanism_type ?? null,
          oosSampleSize: r.stats.n_oos,
          confidenceScore: (r.stats.oos_win_rate ?? 0) / 100,
          overfitScore:  r.stats.wr_improvement ? Math.max(0, 1 - r.stats.wr_improvement / 100) : 0.3,
          featureDiversityScore: 0.7,
          status:       override ?? "active",
          todayTriggers: 0,
          todayWins:    0,
          notFilled:    0,
          pnl7d:        stats.pnl7d.toFixed(4),
          backtestStatus: "idle",
          backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null,
          lastBacktestAt: null,
          approvedAt:   r.discovered_at ? new Date(r.discovered_at) : null,
          params:       null,
          stopPct:      (r as any).stop_pct ?? (r as any).exit_params?.stop_pct ?? null,
          stopSource:   ((r as any).stop_pct != null || (r as any).exit_params?.stop_pct != null) ? "optimized" as const : null,
          horizon:      (r as any).entry?.horizon ?? null,
          updatedAt:    new Date(),
        };
      });

      // Deduplicate: if an alpha card's family already exists in p1strategies,
      // skip it to avoid showing the same strategy twice.
      const p1Families = new Set(p1strategies.map(s => s.strategyId));
      const dedupedAlpha = alphaStrategies.filter(a => {
        const alphaFamily = String((a as any).strategyId ?? "").replace(/^ALPHA-.*/, "");
        // Check if family from approved_rules.json already appeared in system_state strategies
        const fam = approved.find(r => `ALPHA-${r.id}` === a.strategyId);
        const family = fam ? String((fam as any).family ?? "").trim() : "";
        return !family || !p1Families.has(family);
      });
      let all = [...p1strategies, ...dedupedAlpha];

      // Filters
      const symbol = input?.symbol?.toUpperCase();
      const search = input?.search?.toLowerCase();
      if (input?.type) all = all.filter(s => s.type === input.type);
      if (input?.status) all = all.filter(s => s.status === input.status);
      if (symbol) all = all.filter(s => s.symbol === symbol);
      if (search) all = all.filter(s =>
        s.name.toLowerCase().includes(search) ||
        s.strategyId.toLowerCase().includes(search)
      );

      return all;
    }),

    get: publicProcedure.input(z.object({ strategyId: z.string() })).query(({ input }) => {
      const state = bridge.getSystemState();
      const s = state?.strategies.find(s => s.family === input.strategyId);
      if (!s) return null;
      return {
        strategyId: s.family, name: s.name, type: "P1", direction: s.direction.toUpperCase(),
        symbol: "BTCUSDT", entryCondition: s.entry_conditions, exitConditionTop3: [{ label: s.exit_conditions }] as Array<{ label: string }>,
        liveWinRate: null, validationWinRate: (s as any).oos_win_rate ?? null, liveAvgReturnPct: null, closedSampleSize: 0,
        oosWinRate: (s as any).oos_win_rate ?? null, oosAvgReturn: null, oosSampleSize: 0, confidenceScore: 0.7,
        overfitScore: 0.2, featureDiversityScore: 0.7,
        status: strategyStatusOverrides[s.family] ?? "active",
        backtestStatus: "idle", backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null, lastBacktestAt: null,
        approvedAt: null, params: null, updatedAt: new Date(),
        todayTriggers: s.today.triggers, pnl7d: "0.0000",
      };
    }),

    updateStatus: publicProcedure.input(z.object({
      strategyId: z.string(),
      status: z.enum(["active", "paused", "degraded", "retired"]),
    })).mutation(({ input }) => {
      strategyStatusOverrides[input.strategyId] = input.status;
      return { success: true };
    }),

    triggerBacktest: publicProcedure.input(z.object({ strategyId: z.string() })).mutation(() => {
      return { success: true, message: "Backtest execution is delegated to run_pipeline_backtest.py. Please check the dev progress page for the current workflow." };
    }),

    updateParams: publicProcedure.input(z.object({
      strategyId: z.string(),
      params: z.record(z.string(), z.unknown()),
    })).mutation(() => ({
      success: false,
      message: "\u53c2\u6570\u7f16\u8f91\u5c1a\u672a\u63a5\u5165 live \u4e3b\u94fe\uff0c\u76ee\u524d\u4ec5\u4fdd\u7559\u53ea\u8bfb\u89c6\u56fe\u3002",
    })),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Alpha Engine 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂?
  alphaEngine: router({
    getCandidates: publicProcedure.input(z.object({
      status: z.enum(["pending", "approved", "rejected", "expired"]).optional(),
    }).nullish()).query(({ input }) => {
      // 合并 pending_rules.json + approved_rules.json 中 v2 自动发现卡片
      const rules = bridge.getAllCandidates(input?.status);
      return rules.map(r => {
        // v2 卡片用 p_mfe_gt_mae_oos (0-1 fraction)，旧卡片用 oos_win_rate (0-100)
        const isV2 = !!(r.stats.p_mfe_gt_mae_oos !== undefined);
        const pMfeMae = isV2
          ? (r.stats.p_mfe_gt_mae_oos ?? 0)
          : ((r.stats.oos_win_rate ?? 0) / 100);
        const oosWinRate = isV2
          ? (pMfeMae * 100)
          : (r.stats.oos_win_rate ?? 0);
        return {
          id:          r.id,
          candidateId: r.id,
          symbol:      "BTCUSDT",
          direction:   r.entry.direction.toUpperCase() as "LONG" | "SHORT",
          fullExpression: r.rule_str ?? `${r.entry.feature} ${r.entry.operator} ${r.entry.threshold}`,
          seedCondition: `${r.entry.feature} ${r.entry.operator} ${r.entry.threshold}`,
          confirmConditions: (r.combo_conditions ?? []).map(c => `${c.feature} ${c.op} ${c.threshold}`),
          // 统一字段: 旧卡片用 oos_win_rate, v2 卡片用 p_mfe_gt_mae_oos * 100
          oosWinRate,
          oosAvgReturn:  r.stats.oos_avg_ret ?? r.stats.net_avg_pct ?? null,
          sampleSize:    r.stats.n_oos,
          icScore:       null as number | null,
          confidenceScore: pMfeMae,
          overfitScore:  (r as any).validation?.overfitting_score ?? 0.3,
          status:        r.status as "pending" | "approved" | "rejected" | "expired",
          discoveredAt:  new Date(r.discovered_at),
          approvedAt:    null,
          rejectedAt:    null,
          rejectionReason: r.rejection_reason ?? null,
          exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })),
          backtestStatus: "idle",
          backtestResult: null as { equity_curve: number[]; sharpe?: number | null; max_drawdown?: number | null } | null,
          explanation:   r.explanation ?? null,
          featureDimensions: [] as string[],
          estimatedDailyTriggers: null as number | null,
          mechanismType:   r.mechanism_type ?? (r as any).mechanism_type ?? null,
          causalScore:     (r as any).validation?.causal_score ?? null,
          causalIssues:    ((r as any).validation?.issues ?? []) as string[],
          causalWarnings:  ((r as any).validation?.warnings ?? []) as string[],
          causalExplanation: (r as any).validation?.causal_explanation ?? (r.explanation ?? null),
          // v2 专属字段
          isV2,
          discoveryMode:   r.discovery_mode ?? null,
          timeGranularity: r.time_granularity ?? "1m",
          pMfeMae:         isV2 ? pMfeMae : null,
          netAvgPct:       r.stats.net_avg_pct ?? null,
          forceClosure:    r.force_closure ?? null,
          executionParams: r.execution_params ?? null,
          family:          r.family ?? null,
          approvedBy:      r.approved_by ?? null,
        };
      });
    }),

    getCandidate: publicProcedure.input(z.object({ candidateId: z.string() })).query(({ input }) => {
      const rules = bridge.getPendingRules();
      const r = rules.find(x => x.id === input.candidateId);
      if (!r) return null;
      return {
        candidateId: r.id, symbol: "BTCUSDT",
        direction: r.entry.direction.toUpperCase(),
        fullExpression: r.rule_str ?? "",
        oosWinRate: r.stats.oos_win_rate,
        oosAvgReturn: r.stats.oos_avg_ret,
        sampleSize: r.stats.n_oos,
        confidenceScore: Math.min((r.stats.oos_win_rate ?? 0) / 100, 1),
        overfitScore: 0.3, status: r.status,
        discoveredAt: new Date(r.discovered_at),
        explanation: r.explanation ?? null,
        exitConditionTop3: Object.entries(r.exit ?? {}).map(([key, value]) => ({ label: `${key}: ${String(value)}` })), 
      };
    }),

    approveCandidate: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(({ input }) => {
      const ok = bridge.approveRule(input.candidateId);
      return { success: ok, message: ok ? "Rule approved and written to approved_rules.json. alpha_rules.py will reload it automatically." : "Candidate rule was not found." };
    }),

    rejectCandidate: publicProcedure.input(z.object({
      candidateId: z.string(),
      reason: z.string().optional(),
    })).mutation(({ input }) => {
      const ok = bridge.rejectRule(input.candidateId, input.reason);
      return { success: ok };
    }),

    getRuns: publicProcedure.input(z.object({
      symbol: z.string().optional(),
      limit: z.number().default(20),
    }).nullish()).query(() => {
      // Derive synthetic runs from pending rules discovery timestamps
      const pending = bridge.getPendingRules();
      if (pending.length === 0) return [];

      // Group by date prefix of discovered_at
      const groups: Record<string, bridge.PendingRule[]> = {};
      for (const r of pending) {
        const day = r.discovered_at.slice(0, 10);
        groups[day] = groups[day] ?? [];
        groups[day].push(r);
      }

      return Object.entries(groups)
        .sort(([a], [b]) => b.localeCompare(a))
        .slice(0, 20)
        .map(([day, rules]) => ({
          runId:           `RUN-${day}`,
          symbol:          "BTCUSDT",
          status:          "completed",
          phase:           "completed",
          progress:        100,
          featuresScanned: 52,
          candidatesFound: rules.length,
          candidatesApproved: rules.filter(r => r.status === "approved").length,
          params:          globalEngineState.params,
          startedAt:       new Date(day + "T00:00:00Z"),
          completedAt:     new Date(rules[rules.length - 1]!.discovered_at),
        }));
    }),

    getGlobalStatus: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      const discoveryRunning = state?.discovery_alive ?? false;
      // Sync in-memory state with real discovery state
      if (discoveryRunning && globalEngineState.status === "stopped") {
        globalEngineState.status = "running";
        globalEngineState.startedAt = globalEngineState.startedAt ?? new Date();
        globalEngineState.currentPairs = ["BTCUSDT"];
      } else if (!discoveryRunning && globalEngineState.status === "running") {
        globalEngineState.status = "stopped";
        globalEngineState.stoppedAt = new Date();
      }
      return {
        ...globalEngineState,
        uptimeSeconds: globalEngineState.startedAt
          ? Math.floor((Date.now() - globalEngineState.startedAt.getTime()) / 1000)
          : 0,
      };
    }),

    getHeartbeat: publicProcedure.query(() => {
      const hbPath = path.join(PROJECT_ROOT, "monitor/output/discovery_heartbeat.json");
      try {
        if (!fs.existsSync(hbPath)) return { alive: false, pid: 0, updatedAt: null, ageSeconds: 99999 };
        const raw = JSON.parse(fs.readFileSync(hbPath, "utf8"));
        const updatedAt = raw.updated ? new Date(raw.updated * 1000) : null;
        const ageSeconds = updatedAt ? Math.round((Date.now() - updatedAt.getTime()) / 1000) : 99999;
        return { alive: raw.alive ?? false, pid: raw.pid ?? 0, updatedAt, ageSeconds };
      } catch { return { alive: false, pid: 0, updatedAt: null, ageSeconds: 99999 }; }
    }),

    getLLMConfig: publicProcedure.query(() => {
      const cfgPath = path.join(PROJECT_ROOT, "alpha/output/promoter_config.json");
      try {
        if (!fs.existsSync(cfgPath)) return { model: "kimi-k2.5", baseUrl: "https://coding.dashscope.aliyuncs.com/v1", apiKeyMasked: "****" };
        const cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
        const llm = cfg.llm ?? {};
        const key = String(llm.api_key ?? "");
        const masked = key.length > 8 ? key.slice(0, 4) + "****" + key.slice(-4) : "****";
        return { model: llm.model ?? "kimi-k2.5", baseUrl: llm.base_url ?? "", apiKeyMasked: masked };
      } catch { return { model: "unknown", baseUrl: "", apiKeyMasked: "****" }; }
    }),

    getDiscoveryLog: publicProcedure.input(z.object({
      lines: z.number().default(50),
    }).nullish()).query(({ input }) => {
      const logPath = path.join(PROJECT_ROOT, "alpha/output/discovery.log");
      try {
        if (!fs.existsSync(logPath)) return [];
        const raw = fs.readFileSync(logPath, "utf8");
        const allLines = raw.trim().split("\n").filter(Boolean);
        const n = input?.lines ?? 50;
        return allLines.slice(-n);
      } catch {
        return [];
      }
    }),

    startGlobal: publicProcedure.input(z.object({
      params: z.object({
        icThreshold:  z.number().default(0.05),
        oosWinRateMin: z.number().default(0.60),
        maxConditions: z.number().default(3),
        lookbackDays:  z.number().default(180),
      }).optional(),
    }).nullish()).mutation(({ input }) => {
      // Discovery process is managed by watchdog.py 閳?this button reflects UI intent only.
      // discovery_alive is written by run_live_discovery.py itself via _set_discovery_alive().
      globalEngineState = {
        status: "running",
        startedAt: new Date(),
        stoppedAt: null,
        currentPairs: ["BTCUSDT"],
        totalRuns: globalEngineState.totalRuns + 1,
        params: input?.params ?? globalEngineState.params,
      };
      return { success: true, pairs: ["BTCUSDT"], message: "Alpha engine started. Managed by watchdog.py." };
    }),

    stopGlobal: publicProcedure.mutation(() => {
      globalEngineState.status = "stopped";
      globalEngineState.stoppedAt = new Date();
      return { success: true, message: "Alpha engine stopped. Watchdog will restart it automatically." };
    }),

    startRun: publicProcedure.input(z.object({
      symbol: z.string(),
      params: z.object({
        icThreshold:  z.number().default(0.05),
        oosWinRateMin: z.number().default(0.60),
        maxConditions: z.number().default(3),
        lookbackDays:  z.number().default(180),
      }).optional(),
    })).mutation(() => {
      return { success: true, runId: `RUN-${nanoid(8)}`, message: "Discovery run queued. The watchdog process will handle the live execution loop." };
    }),

    triggerBacktest: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(() => {
      return { success: true, message: "Backtest execution is delegated to run_pipeline_backtest.py." };
    }),

    getSystemHealth: publicProcedure.query(() => {
      return bridge.getSystemHealth();
    }),

    // 閳光偓閳光偓 LLM Promoter Engine 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
    getLLMEngineState: publicProcedure.query(() => {
      return bridge.getEngineState();
    }),

    getReviewQueue: publicProcedure.query(() => {
      return bridge.getReviewQueue().map(r => ({
        id: r.id,
        candidateId: r.id,
        symbol: "BTCUSDT",
        direction: ((r.entry?.direction ?? "LONG").toUpperCase()) as "LONG" | "SHORT",
        fullExpression: r.rule_str ?? `${r.entry?.feature ?? ""} ${r.entry?.operator ?? ""} ${r.entry?.threshold ?? ""}`,
        oosWinRate: r.stats?.oos_win_rate ?? 0,
        oosAvgReturn: r.stats?.oos_avg_ret ?? 0,
        sampleSize: r.stats?.n_oos ?? 0,
        oosPf: r.stats?.oos_pf ?? null,
        status: r.status,
        discoveredAt: new Date(r.discovered_at),
        mechanismType: (r as any).mechanism_type ?? null,
        llmResult: (r as any).llm_result ?? null,
        llmValidated: (r as any).llm_validated ?? false,
        llmValidatedAt: (r as any).llm_validated_at ?? null,
      }));
    }),

    promoterApprove: publicProcedure.input(z.object({ candidateId: z.string() })).mutation(({ input }) => {
      const ok = bridge.promoterApprove(input.candidateId);
      return { success: ok, message: ok ? "Rule approved." : "Rule not found." };
    }),

    promoterReject: publicProcedure.input(z.object({
      candidateId: z.string(),
      reason: z.string().optional(),
    })).mutation(({ input }) => {
      const ok = bridge.promoterReject(input.candidateId);
      return { success: ok };
    }),

    saveLLMConfig: publicProcedure.input(z.object({
      apiKey: z.string().optional(),
      model: z.string().optional(),
      baseUrl: z.string().optional(),
      autoApprove: z.number().min(0).max(1).optional(),
      reviewQueue: z.number().min(0).max(1).optional(),
    })).mutation(({ input }) => {
      bridge.savePromoterConfig(input);
      return { success: true };
    }),

    getForceLibrary: publicProcedure.query(() => {
      return bridge.getForceLibraryState();
    }),

    getRegimeStatus: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      return {
        regime: state?.regime ?? "UNKNOWN",
        price: state?.price ?? 0,
        symbol: state?.symbol ?? "BTCUSDT",
      };
    }),

    getSignalWinRates: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      if (!state?.strategies) return [];
      return state.strategies.map((s: any) => ({
        family: s.family,
        name: s.name ?? s.family,
        direction: s.direction ?? "both",
        oosWinRate: s.oos_win_rate ?? null,
        mechanismType: s.mechanism_type ?? null,
        status: s.status ?? "unknown",
      }));
    }),

    getForceConcentration: publicProcedure.query(() => {
      const state = bridge.getSystemState();
      const positions = state?.positions ?? [];
      const forceLib = bridge.getForceLibraryState();
      const concentration: Record<string, number> = forceLib?.concentration ?? {};
      return {
        concentration,
        positionCount: positions.length,
      };
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Trades 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮?
  trades: router({
    list: publicProcedure.input(z.object({
      symbol:     z.string().optional(),
      strategyId: z.string().optional(),
      status:     z.enum(["open", "closed", "cancelled"]).optional(),
      direction:  z.enum(["LONG", "SHORT"]).optional(),
      limit:      z.number().default(50),
    }).nullish()).query(({ input }) => {
      return bridge.getTrades({
        symbol:     input?.symbol,
        strategyId: input?.strategyId,
        status:     input?.status,
        direction:  input?.direction,
        limit:      input?.limit ?? 50,
      });
    }),

    getStats: publicProcedure.query(() => {
      return bridge.getTradeStats();
    }),

    getChartData: publicProcedure.query(() => {
      return bridge.getChartData(7);
    }),

    close: publicProcedure.input(z.object({
      tradeId:    z.string(),
      exitPrice:  z.string(),
      exitReason: z.string().default("manual"),
    })).mutation(() => {
      return { success: false, message: "Manual close is handled by execution_engine. The UI currently exposes this as a read-only action." };
    }),

    exchangeRaw: publicProcedure.query(() => {
      return bridge.getExchangeTradesRaw();
    }),

    hideExchangeOrders: publicProcedure.input(z.object({
      orderIds: z.array(z.number().int().positive()).min(1),
    })).mutation(({ input }) => {
      return { success: true, ...bridge.hideExchangeOrders(input.orderIds) };
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?System Events (from alerts.log) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€?
  systemEvents: router({
    list: publicProcedure.input(z.object({
      limit:    z.number().default(50),
      severity: z.string().optional(),
    }).nullish()).query(({ input }) => {
      const alerts = bridge.getAlertsLog(input?.limit ?? 50);
      return alerts.map((a, i) => ({
        id:          i + 1,
        eventType:   "signal_triggered",
        symbol:      "BTCUSDT",
        severity:    "info" as const,
        title:       `[${a.phase}] ${a.signalName} ${a.direction}`,
        message:     a.description || `${a.signalName} \u89e6\u53d1 ${a.direction === "LONG" ? "\u505a\u591a" : "\u505a\u7a7a"}\uff0c\u6301\u7eed ${a.bars}`,
        strategyId:  a.signalName,
        metadata:    { bars: a.bars, phase: a.phase },
        occurredAt:  new Date(a.timestamp.replace(" UTC", "Z")),
      }));
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?Dev Progress (real tasks from data/dev_tasks.json) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣搴ゎ潐濞叉﹢宕归崸妤冨祦婵☆垵鍋愮壕鍏间繆椤栨粌甯堕悽顖涱殜濮婄粯鎷呮笟顖滃姼缂備胶绮崝娆掓濡炪倖鐗楃粙鎾汇€呴幓鎹ㄦ棃鏁愰崨顓熸闂佹娊鏀遍崹鍧楀蓟濞戙垹绠涙い鎾跺仧缁佺兘鏌ｉ姀鈺佺仜闁告梹鍨垮璇测槈濮橈絽浜鹃柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閿涘嫪娌柣锝呯潡瑜忛埀顒冾潐濞叉﹢銆冮崱妤婂殫闁告洦鍓涚弧鈧繛杈剧到婢瑰﹤螞濠婂牊鈷掗柛灞捐壘閳ь剟顥撶划鍫熺瑹閳ь剙鐣烽鐐查敜婵°倐鍋撻柛灞诲妽缁绘繃绻濋崒婊冾暫缂佺偓鍎抽…鐑藉蓟閻旂厧绀堢憸蹇曟暜濞戙垺鐓熼柟鎯у暱閺嗭綁鏌＄仦鍓ь灱缂佺姵鐩獮娆撳礃閳诡剨闄勭换娑氣偓娑欘焽閻帞绱掗悩宕囧ⅹ妞ゎ偄绻愮叅妞ゅ繐瀚ˇ銊╂⒑閸︻厾甯涢悽顖涘笚濞煎繘宕ㄧ€涙ǚ鎷?

  dataStorage: router({
    getOverview: publicProcedure.query(() => buildDataStorageOverview()),
    list: publicProcedure.query(() => buildDataStorageOverview().datasets),
  }),

  dataSync: router({
    getStatus: publicProcedure.query(() => {
      return getDataSyncStatus();
    }),
    start: publicProcedure.mutation(() => {
      writeDataSyncControl(true);
      return { success: true, ...getDataSyncStatus() };
    }),
    stop: publicProcedure.mutation(() => {
      writeDataSyncControl(false);
      return { success: true, ...getDataSyncStatus() };
    }),
  }),

  devProgress: router({
    getTasks: publicProcedure.query(() => bridge.getDevTasks()),
    updateStatus: publicProcedure.input(z.object({
      id:     z.number(),
      status: z.enum(["completed", "in_progress", "pending", "blocked"]),
    })).mutation(({ input }) => {
      bridge.updateDevTask(input.id, input.status);
      return { success: true };
    }),
    addTask: publicProcedure.input(z.object({
      category:    z.string(),
      title:       z.string(),
      description: z.string().optional(),
      priority:    z.enum(["critical", "high", "medium", "low"]).default("medium"),
      layer:       z.string().optional(),
    })).mutation(({ input }) => {
      bridge.insertDevTask({ ...input, status: "pending", sortOrder: 99 });
      return { success: true };
    }),
  }),

  // 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷?LLM Analysis (optional 闂?works if BUILT_IN_FORGE_API_KEY is set) 闂傚倸鍊搁崐鎼佸磹妞嬪海鐭嗗〒姘ｅ亾鐎规洦鍨跺畷绋课旈埀顒勫磼閵婏妇绡€濠电姴鍊绘晶鏇犵棯閹岀吋闁哄瞼鍠栧畷婊嗩槾閻㈩垱鐩弻锝夊箻閸愬弶娈婚梺鍝勬湰缁嬫牜绮诲☉銏犵闁告劏鏁╅敂鐣岀閻庢稒顭囬惌鎺旂磼閻樺磭澧い顐㈢箰鐓ゆい蹇撳椤︺劑姊洪崷顓犲笡閻㈩垱甯楀蹇涘川鐎涙ǚ鎷虹紓浣割儐椤戞瑩宕曢幇鐗堢厵闁荤喓澧楅崰妯尖偓娈垮枦椤曆囶敇閸忕厧绶炲┑鐘插濡差垰鈹戦悩顔肩伇婵炲鐩弫鍐晲閸℃瑧褰鹃梺鍝勬储閸ㄦ椽鍩涢幒鎳ㄥ綊鏁愰崶鍓佸姼闂佸搫妫濇禍鍫曞蓟濞戞鐔兼嚒閵堝洨鍘滈柣?
  llmAnalysis: router({
    analyzeStrategy: publicProcedure.input(z.object({
      strategyId: z.string(),
    })).mutation(async ({ input }) => {
      const state = bridge.getSystemState();
      const s = state?.strategies.find(x => x.family === input.strategyId);
      if (!s) return { success: false, report: "Strategy not found." };

      const prompt = [
        "Analyze the following BTC strategy and return a concise assessment.",
        `Strategy ID: ${s.family}` ,
        `Name: ${s.name}` ,
        `Direction: ${s.direction.toUpperCase()}` ,
        `Entry: ${s.entry_conditions}` ,
        `Exit: ${s.exit_conditions}` ,
        `Today triggers: ${s.today.triggers}` ,
        `Today wins: ${s.today.wins}` ,
      ].join("\\n");

      try {
        const response = await invokeLLM({
          messages: [
            { role: "system", content: "You are a quantitative trading analyst. Review the strategy and suggest practical improvements." },
            { role: "user", content: prompt },
          ],
        });
        const report = response.choices?.[0]?.message?.content ?? "No analysis returned.";
        return { success: true, report };
      } catch {
        return { success: false, report: "LLM request failed. Check BUILT_IN_FORGE_API_KEY." };
      }
    }),

    generateMarketInsight: publicProcedure.mutation(async () => {
      const state = bridge.getSystemState();
      const prompt = [
        "Summarize the current BTC market state in a concise operator-facing note.",
        `Price: ${state?.price ?? "unknown"} USDT`,
        `Regime: ${state?.regime ?? "unknown"}`,
      ].join("\\n");

      try {
        const response = await invokeLLM({
          messages: [
            { role: "system", content: "You are a market analyst producing short actionable trading insights." },
            { role: "user", content: prompt },
          ],
        });
        const insight = response.choices?.[0]?.message?.content ?? "No market insight returned.";
        return { success: true, insight };
      } catch {
        return { success: false, insight: "LLM request failed. Check BUILT_IN_FORGE_API_KEY." };
      }
    }),
  }),
});

export type AppRouter = typeof appRouter;





