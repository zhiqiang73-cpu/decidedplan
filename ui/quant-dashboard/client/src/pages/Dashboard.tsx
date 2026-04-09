import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import { formatDateTimeUTC8 } from "@/lib/time";
import { formatDirection, formatForceCategory, formatRegime } from "@/lib/labels";
import { Activity, AlertCircle, BarChart3, Database, DollarSign, FolderOpen, HardDrive, Target, Wifi, WifiOff } from "lucide-react";

type StorageHealth = "healthy" | "stale" | "missing";
type StorageCadence = "realtime" | "historical";

type PositionRow = {
  positionId: string;
  signalName: string;
  strategyFamily: string;
  symbol: string;
  direction: string;
  quantity: number;
  entryPrice: number;
  entryAt: Date | string | null;
};

type PendingOrderRow = {
  orderId: string;
  signalName: string;
  quantity: number;
  requestedPrice: number;
};

export default function Dashboard() {
  const { connected } = useWebSocket();
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 30_000 });
  const { data: tradeStats } = trpc.trades.getStats.useQuery(undefined, { refetchInterval: 30_000 });
  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, { refetchInterval: 30_000 });
  const { data: strategies = [] } = trpc.strategies.list.useQuery({ status: "active" }, { refetchInterval: 15_000 });
  const { data: liveSnapshot } = trpc.execution.getLiveSnapshot.useQuery(undefined, { refetchInterval: 5_000 });
  const { data: dataOverview } = trpc.dataStorage.getOverview.useQuery(undefined, { refetchInterval: 30_000 });
  const { data: regimeData } = trpc.alphaEngine.getRegimeStatus.useQuery(undefined, { refetchInterval: 10_000 });
  const { data: winRates = [] } = trpc.alphaEngine.getSignalWinRates.useQuery(undefined, { refetchInterval: 30_000 });
  const { data: forceData } = trpc.alphaEngine.getForceConcentration.useQuery(undefined, { refetchInterval: 10_000 });

  const totalEquity = Number(wallet?.totalEquity ?? 0);
  const unrealizedPnl = Number(wallet?.unrealizedPnl ?? 0);
  const todayPnl = Number(tradeStats?.todayPnl ?? 0);
  const winRate = Number(tradeStats?.winRate ?? 0);
  const positions = (liveSnapshot?.positions ?? []) as PositionRow[];
  const pendingOrders = (liveSnapshot?.pendingOrders ?? []) as PendingOrderRow[];
  const datasets = dataOverview?.datasets ?? [];
  const activeStrategies = strategies.slice(0, 6);
  const regimeLabel = formatRegime(regimeData?.regime ?? "UNKNOWN");
  const regimeColor = getRegimeColor(regimeData?.regime ?? "UNKNOWN");
  const totalNotional = positions.reduce((sum, position) => sum + (position.entryPrice ?? 0) * (position.quantity ?? 0), 0);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>{"系统仪表盘"}</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
              {"实时监控 / UTC+8 "}{formatDateTimeUTC8(new Date())}
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg" style={{ backgroundColor: "#1e2329", color: connected ? "#0ecb81" : "#848e9c" }}>
            {connected ? <Wifi size={14} /> : <WifiOff size={14} />}
            <span>{connected ? "4/4 实时流" : "实时流重连中"}</span>
          </div>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <KpiCard title={"账户权益"} value={formatMoney(totalEquity)} sub={"总资产估值"} icon={<DollarSign size={16} />} />
          <KpiCard title={"今日盈亏"} value={formatMoney(todayPnl)} sub={"UTC+8 今日"} valueColor={todayPnl >= 0 ? "#0ecb81" : "#f6465d"} icon={<BarChart3 size={16} />} />
          <KpiCard title={"浮动盈亏"} value={formatMoney(unrealizedPnl)} sub={`${positions.length} 笔持仓`} valueColor={unrealizedPnl >= 0 ? "#0ecb81" : "#f6465d"} icon={<Activity size={16} />} />
          <KpiCard title={"综合胜率"} value={`${winRate.toFixed(1)}%`} sub={`${tradeStats?.totalTrades ?? 0} 笔闭单`} valueColor={winRate >= 60 ? "#0ecb81" : "#f0a500"} icon={<Target size={16} />} />
          <KpiCard title={"市场状态"} value={regimeLabel} sub="BTCUSDT" valueColor={regimeColor} icon={<BarChart3 size={16} />} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="card-q p-4">
            <div className="flex items-center gap-2 mb-3">
              <AlertCircle size={14} style={{ color: "#f0a500" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"系统健康"}</h3>
            </div>
            <div className="space-y-2 text-sm">
              <Row label={"综合得分"} value={`${health?.overall ?? 0}%`} valueColor={(health?.overall ?? 0) >= 80 ? "#0ecb81" : "#f0a500"} />
              <Row label={"执行层成交率"} value={`${(((health?.layers?.execution?.fillRate ?? 0) as number) * 100).toFixed(0)}%`} valueColor="#eaecef" />
              <Row label={"持仓名义价值"} value={formatMoney(totalNotional)} valueColor="#eaecef" />
              <Row label={"当前挂单数"} value={String(pendingOrders.length)} valueColor="#eaecef" />
            </div>
            {(health?.issues ?? []).slice(0, 2).map((issue, index) => (
              <div key={`${issue.message}-${index}`} className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: "rgba(240,165,0,0.1)", color: "#f0a500", border: "1px solid rgba(240,165,0,0.2)" }}>
                {issue.message}
              </div>
            ))}
          </div>

          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"活跃策略"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${strategies.length} 条`}</span>
            </div>
            <div className="space-y-2">
              {activeStrategies.map((strategy) => (
                <div key={strategy.strategyId} className="py-2 px-2 rounded" style={{ backgroundColor: "#161a1e" }}>
                  <div className="text-xs font-medium" style={{ color: "#eaecef" }}>{strategy.name}</div>
                  <div className="text-xs" style={{ color: "#848e9c" }}>{`${strategy.symbol} / ${formatDirection(strategy.direction)}`}</div>
                </div>
              ))}
              {activeStrategies.length === 0 && <div className="text-sm" style={{ color: "#848e9c" }}>{"暂无活跃策略"}</div>}
            </div>
            {forceData?.concentration && Object.keys(forceData.concentration).length > 0 && (
              <div className="mt-3 pt-3" style={{ borderTop: "1px solid #2b3139" }}>
                <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>{"力量集中度"}</div>
                <div className="space-y-1">
                  {Object.entries(forceData.concentration).map(([category, count]) => (
                    <div key={category} className="flex items-center justify-between text-xs">
                      <span style={{ color: "#848e9c" }}>{formatForceCategory(category)}</span>
                      <span className="font-num" style={{ color: Number(count) >= 2 ? "#f0b90b" : "#eaecef" }}>{String(count)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"当前持仓"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${positions.length} 笔`}</span>
            </div>
            <div className="space-y-2">
              {positions.map((position) => (
                <div key={position.positionId} className="rounded-lg p-3" style={{ backgroundColor: "#161a1e" }}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{position.signalName || position.strategyFamily}</div>
                      <div className="text-xs" style={{ color: "#848e9c" }}>{`${position.symbol} / ${formatDirection(position.direction)}`}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-num" style={{ color: "#eaecef" }}>{Number(position.quantity ?? 0).toFixed(4)}</div>
                      <div className="text-xs font-num" style={{ color: "#848e9c" }}>{`@ ${Number(position.entryPrice ?? 0).toFixed(2)}`}</div>
                    </div>
                  </div>
                </div>
              ))}
              {positions.length === 0 && <div className="text-sm" style={{ color: "#848e9c" }}>{"当前无持仓"}</div>}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"待成交订单"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${pendingOrders.length} 笔`}</span>
            </div>
            <div className="space-y-2">
              {pendingOrders.map((order) => (
                <div key={order.orderId} className="rounded-lg p-3" style={{ backgroundColor: "#161a1e" }}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{order.signalName}</div>
                      <div className="text-xs font-num" style={{ color: "#848e9c" }}>{`qty ${Number(order.quantity ?? 0).toFixed(4)}`}</div>
                    </div>
                    <div className="text-sm font-num" style={{ color: "#f0b90b" }}>{`@ ${Number(order.requestedPrice ?? 0).toFixed(2)}`}</div>
                  </div>
                </div>
              ))}
              {pendingOrders.length === 0 && <div className="text-sm" style={{ color: "#848e9c" }}>{"暂无待成交订单"}</div>}
            </div>
          </div>

          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"策略 OOS 验证胜率"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${winRates.length} 组`}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {winRates.map((item) => {
                const value = Number(item.oosWinRate ?? 0);
                const color = value >= 80 ? "#0ecb81" : value >= 60 ? "#f0b90b" : "#f6465d";
                return (
                  <div key={item.family} className="rounded-lg p-3" style={{ backgroundColor: "#161a1e" }}>
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="text-xs" style={{ color: "#848e9c" }}>{item.family}</span>
                      <span className="text-xs font-num font-bold" style={{ color }}>{`${value.toFixed(1)}%`}</span>
                    </div>
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: "#2b3139" }}>
                      <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, value))}%`, backgroundColor: color }} />
                    </div>
                  </div>
                );
              })}
              {winRates.length === 0 && <div className="text-sm" style={{ color: "#848e9c" }}>{"暂无 OOS 胜率数据"}</div>}
            </div>
          </div>
        </div>

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2">
              <Database size={14} style={{ color: "#f0b90b" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"数据存储概览"}</h3>
            </div>
            <span className="text-xs" style={{ color: "#848e9c" }}>{dataOverview?.symbol ?? "BTCUSDT"}</span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-3 p-4" style={{ borderBottom: "1px solid #2b3139" }}>
            <StorageMetric label={"根目录"} value={dataOverview?.rootPath ?? "data/storage"} mono />
            <StorageMetric label={"健康度"} value={`${dataOverview?.healthyDatasets ?? 0}/${dataOverview?.totalDatasets ?? 0}`} />
            <StorageMetric label={"文件数量"} value={formatCount(dataOverview?.totalFiles ?? 0)} />
            <StorageMetric label={"总大小"} value={formatBytes(dataOverview?.totalBytes ?? 0)} />
          </div>
          {datasets.length === 0 ? (
            <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"暂无数据"}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr style={{ borderBottom: "1px solid #1e2329" }}>
                    {[
                      "类型",
                      "描述",
                      "路径",
                      "文件数量",
                      "大小",
                      "最后更新",
                      "健康",
                    ].map((heading) => (
                      <th key={heading} className="px-4 py-2.5 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{heading}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((dataset, index) => {
                    const meta = getStorageHealthMeta(dataset.health as StorageHealth, dataset.cadence as StorageCadence);
                    return (
                      <tr key={dataset.key} style={{ borderBottom: index < datasets.length - 1 ? "1px solid #1e2329" : "none" }}>
                        <td className="px-4 py-3 text-sm font-medium" style={{ color: "#eaecef" }}>{dataset.label}</td>
                        <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{dataset.description}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <FolderOpen size={11} style={{ color: "#5e6673" }} />
                            <span className="text-xs font-mono break-all" style={{ color: "#5e6673" }}>{dataset.path}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{formatCount(dataset.fileCount)}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <HardDrive size={11} style={{ color: "#848e9c" }} />
                            <span className="text-sm font-num" style={{ color: "#eaecef" }}>{formatBytes(dataset.totalBytes)}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>{dataset.latestModifiedAt ? formatDateTimeUTC8(new Date(dataset.latestModifiedAt)) : "--"}</td>
                        <td className="px-4 py-3">
                          <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ backgroundColor: meta.bg, color: meta.fg }}>{meta.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </QuantLayout>
  );
}

function KpiCard({
  title,
  value,
  sub,
  icon,
  valueColor = "#eaecef",
}: {
  title: string;
  value: string;
  sub: string;
  icon: React.ReactNode;
  valueColor?: string;
}) {
  return (
    <div className="card-q p-4">
      <div className="flex items-center justify-between gap-3 mb-2">
        <span className="text-xs" style={{ color: "#848e9c" }}>{title}</span>
        <span style={{ color: "#848e9c" }}>{icon}</span>
      </div>
      <div className="text-xl font-bold font-num" style={{ color: valueColor }}>{value}</div>
      <div className="text-xs mt-1" style={{ color: "#848e9c" }}>{sub}</div>
    </div>
  );
}

function Row({ label, value, valueColor }: { label: string; value: string; valueColor: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span style={{ color: "#848e9c" }}>{label}</span>
      <span className="font-num" style={{ color: valueColor }}>{value}</span>
    </div>
  );
}

function StorageMetric({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className={mono ? "text-sm font-mono break-all" : "text-sm font-num"} style={{ color: "#eaecef" }}>{value}</div>
    </div>
  );
}

function formatMoney(value: number) {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatCount(value: number) {
  return Number.isFinite(value) ? value.toLocaleString("en-US") : "0";
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 100 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function getRegimeColor(regime: string) {
  const colorMap: Record<string, string> = {
    QUIET_TREND: "#0ecb81",
    VOLATILE_TREND: "#f0b90b",
    RANGE_BOUND: "#1890ff",
    VOL_EXPANSION: "#f6a600",
    CRISIS: "#f6465d",
    UNKNOWN: "#848e9c",
  };
  return colorMap[regime] ?? "#848e9c";
}

function getStorageHealthMeta(health: StorageHealth, cadence: StorageCadence) {
  if (health === "healthy") {
    return {
      label: cadence === "realtime" ? "实时" : "最新",
      bg: "rgba(14,203,129,0.12)",
      fg: "#0ecb81",
    };
  }
  if (health === "stale") {
    return {
      label: cadence === "realtime" ? "延迟" : "过期",
      bg: "rgba(240,165,0,0.12)",
      fg: "#f0a500",
    };
  }
  return { label: "缺失", bg: "rgba(246,70,93,0.12)", fg: "#f6465d" };
}
