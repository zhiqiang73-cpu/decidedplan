import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { Activity, CheckCircle2, Clock3, Database, FolderOpen, HardDrive } from "lucide-react";

type StorageHealth = "healthy" | "stale" | "missing";
type StorageCadence = "realtime" | "historical";

type PairStatus = "idle" | "scanning" | "completed" | "error";

export default function TradingPairs() {
  const { data: pairs = [] } = trpc.tradingPairs.list.useQuery(undefined, { refetchInterval: 10_000 });
  const { data: dataOverview } = trpc.dataStorage.getOverview.useQuery(undefined, { refetchInterval: 30_000 });

  const trackedPairs = pairs.filter((pair) => pair.isTracked);
  const datasets = dataOverview?.datasets ?? [];

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div>
          <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>{"交易对状态"}</h1>
          <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
            {"当前主链仅支持 BTCUSDT，此页只做实时状态与数据存储展示。"}
          </p>
        </div>

        <div
          className="card-q p-4"
          style={{ border: "1px solid rgba(240,185,11,0.24)", backgroundColor: "rgba(240,185,11,0.08)" }}
        >
          <div className="flex items-start gap-3">
            <Activity size={16} style={{ color: "#f0b90b", marginTop: 2 }} />
            <div className="space-y-1">
              <div className="text-sm font-semibold" style={{ color: "#f0b90b" }}>{"主链口径说明"}</div>
              <div className="text-sm" style={{ color: "#c9d1d9" }}>
                {"前端不再提供新增、下线或强行扫描交易对的按钮，因为后端实际只有 BTCUSDT 这条 live 主链。"}
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="card-q overflow-hidden xl:col-span-2">
            <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"主链交易对"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${trackedPairs.length} ${"条"}`}</span>
            </div>
            <div className="divide-y" style={{ borderColor: "#1e2329" }}>
              {trackedPairs.map((pair) => (
                <div key={pair.symbol} className="px-4 py-4 flex items-center gap-4">
                  <div
                    className="w-12 h-12 rounded-xl flex items-center justify-center font-bold text-sm flex-shrink-0"
                    style={{ backgroundColor: "#1e2329", color: "#f0b90b" }}
                  >
                    {pair.symbol.slice(0, 3)}
                  </div>
                  <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <div className="text-sm font-semibold" style={{ color: "#eaecef" }}>{pair.symbol}</div>
                      <PairStatusBadge status={(pair.alphaEngineStatus ?? "idle") as PairStatus} />
                    </div>
                    <div className="text-xs" style={{ color: "#848e9c" }}>
                      {"最后数据更新："} {pair.lastDataUpdate ? formatDateTime(pair.lastDataUpdate) : "--"}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-right flex-shrink-0">
                    <PairMetric label={"当前价格"} value={formatPrice(pair.currentPrice)} />
                    <PairMetric label={"数据进度"} value={`${pair.dataDownloadProgress ?? 0}%`} />
                    <PairMetric label={"数据质量"} value={`${pair.dataQualityScore ?? 0}%`} />
                    <PairMetric label={"约 1m K线"} value={formatCount(pair.totalKlines ?? 0)} />
                  </div>
                </div>
              ))}
              {trackedPairs.length === 0 && (
                <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"暂无交易对状态"}</div>
              )}
            </div>
          </div>

          <div className="card-q p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Database size={15} style={{ color: "#f0b90b" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"数据存储总览"}</h3>
            </div>
            <SummaryRow label={"交易对"} value={dataOverview?.symbol ?? "BTCUSDT"} />
            <SummaryRow label={"根目录"} value={dataOverview?.rootPath ?? "data/storage"} mono />
            <SummaryRow label={"健康数据集"} value={`${dataOverview?.healthyDatasets ?? 0}/${dataOverview?.totalDatasets ?? 0}`} />
            <SummaryRow label={"文件总数"} value={formatCount(dataOverview?.totalFiles ?? 0)} />
            <SummaryRow label={"占用空间"} value={formatBytes(dataOverview?.totalBytes ?? 0)} />
            <SummaryRow label={"约 1m K线"} value={formatCount(dataOverview?.approxKlines ?? 0)} />
            <SummaryRow label={"最后更新"} value={dataOverview?.lastUpdatedAt ? formatDateTime(dataOverview.lastUpdatedAt) : "--"} />
          </div>
        </div>

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2">
              <Database size={14} style={{ color: "#f0b90b" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"数据存储明细"}</h3>
            </div>
            <span className="text-xs" style={{ color: "#848e9c" }}>{dataOverview?.symbol ?? "BTCUSDT"}</span>
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
                    const health = getStorageHealthMeta(dataset.health as StorageHealth, dataset.cadence as StorageCadence);
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
                        <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>
                          {dataset.latestModifiedAt ? formatDateTime(dataset.latestModifiedAt) : "--"}
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ backgroundColor: health.bg, color: health.fg }}>
                            {health.label}
                          </span>
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

function PairStatusBadge({ status }: { status: PairStatus }) {
  const map: Record<PairStatus, { label: string; bg: string; fg: string; icon: typeof CheckCircle2 }> = {
    idle: { label: "待运行", bg: "rgba(132,142,156,0.12)", fg: "#848e9c", icon: Clock3 },
    scanning: { label: "扫描中", bg: "rgba(240,185,11,0.12)", fg: "#f0b90b", icon: Activity },
    completed: { label: "已完成", bg: "rgba(14,203,129,0.12)", fg: "#0ecb81", icon: CheckCircle2 },
    error: { label: "异常", bg: "rgba(246,70,93,0.12)", fg: "#f6465d", icon: Clock3 },
  };
  const meta = map[status] ?? map.idle;
  const Icon = meta.icon;
  return (
    <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium" style={{ backgroundColor: meta.bg, color: meta.fg }}>
      <Icon size={11} />
      {meta.label}
    </span>
  );
}

function PairMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[88px]">
      <div className="text-[11px] mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-sm font-num font-medium" style={{ color: "#eaecef" }}>{value}</div>
    </div>
  );
}

function SummaryRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 text-sm">
      <span style={{ color: "#848e9c" }}>{label}</span>
      <span className={mono ? "font-mono text-right break-all" : "font-num text-right"} style={{ color: "#eaecef" }}>{value}</span>
    </div>
  );
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

function formatCount(value: number) {
  return Number.isFinite(value) ? value.toLocaleString("en-US") : "0";
}

function formatPrice(value: string | number | null | undefined) {
  const numeric = typeof value === "string" ? Number(value) : value ?? 0;
  if (!Number.isFinite(numeric)) return "--";
  return `$${numeric.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatDateTime(value: string | Date) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date);
}
