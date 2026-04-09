import QuantLayout from "@/components/QuantLayout";
import { useMemo, useState } from "react";
import { trpc } from "@/lib/trpc";
import { Download } from "lucide-react";
import { Input } from "@/components/ui/input";
import { formatDateTimeUTC8 } from "@/lib/time";
import { formatDirection, formatTradeStatus, formatExitReason } from "@/lib/labels";

export default function TradeHistory() {
  const [statusFilter, setStatusFilter] = useState<"" | "open" | "closed" | "cancelled">("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [directionFilter, setDirectionFilter] = useState<"" | "LONG" | "SHORT">("");
  const [strategyFilter, setStrategyFilter] = useState("");

  const { data: trades } = trpc.trades.list.useQuery(
    {
      status: statusFilter || undefined,
      symbol: symbolFilter || undefined,
      direction: directionFilter || undefined,
      limit: 300,
    },
    { refetchInterval: 15000 },
  );

  const { data: stats } = trpc.trades.getStats.useQuery(undefined, { refetchInterval: 30000 });

  const filteredTrades = useMemo(() => {
    const rows = trades ?? [];
    if (!strategyFilter.trim()) return rows;
    const kw = strategyFilter.trim().toLowerCase();
    return rows.filter((t) => (t.strategyId ?? "").toLowerCase().includes(kw));
  }, [trades, strategyFilter]);

  const exportCSV = () => {
    const header = "TradeID,Symbol,Direction,Status,Strategy,EntryPrice,ExitPrice,Quantity,PnL,PnL%,EntryAt(UTC+8),ExitAt(UTC+8),ExitReason";
    const rows = filteredTrades.map((t) => [
      t.tradeId,
      t.symbol,
      formatDirection(t.direction),
      formatTradeStatus(t.status),
      t.strategyId ?? "",
      t.entryPrice ?? "",
      t.exitPrice ?? "",
      t.quantity ?? "",
      t.pnl ?? "",
      t.pnlPercent ?? "",
      t.entryAt ? formatDateTimeUTC8(t.entryAt) : "",
      t.exitAt ? formatDateTimeUTC8(t.exitAt) : "",
      t.exitReason ?? "",
    ].join(","));

    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trades_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const todayPnl = parseFloat(String(stats?.todayPnl ?? "0"));
  const totalPnl = parseFloat(String(stats?.totalPnl ?? "0"));

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>交易记录</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>完整交易历史 · UTC+8</p>
          </div>
          <button
            onClick={exportCSV}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ backgroundColor: "#1e2329", color: "#848e9c", border: "1px solid #2b3139" }}
          >
            <Download size={14} /> 导出CSV
          </button>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard label="总交易笔数" value={`${stats?.totalTrades ?? 0}`} />
          <StatCard label="综合胜率" value={`${stats?.winRate ?? "0"}%`} valueColor="#0ecb81" />
          <StatCard label="今日盈亏" value={`${todayPnl >= 0 ? "+" : ""}$${todayPnl.toFixed(2)}`} valueColor={todayPnl >= 0 ? "#0ecb81" : "#f6465d"} />
          <StatCard label="总盈亏" value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`} valueColor={totalPnl >= 0 ? "#0ecb81" : "#f6465d"} />
        </div>

        <div className="flex flex-wrap gap-2 items-center">
          <Input
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
            placeholder="交易对..."
            className="w-32 text-sm h-8"
            style={{ backgroundColor: "#1e2329", borderColor: "#2b3139", color: "#eaecef" }}
          />
          <Input
            value={strategyFilter}
            onChange={(e) => setStrategyFilter(e.target.value)}
            placeholder="策略ID..."
            className="w-40 text-sm h-8"
            style={{ backgroundColor: "#1e2329", borderColor: "#2b3139", color: "#eaecef" }}
          />

          {(["", "open", "closed", "cancelled"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor: statusFilter === s ? "#f0b90b" : "#1e2329",
                color: statusFilter === s ? "#0b0e11" : "#848e9c",
                border: "1px solid #2b3139",
              }}
            >
              {s === "" ? "全部" : s === "open" ? "持仓中" : s === "closed" ? "已平仓" : "已撤单"}
            </button>
          ))}

          {(["", "LONG", "SHORT"] as const).map((d) => (
            <button
              key={d}
              onClick={() => setDirectionFilter(d)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor:
                  directionFilter === d ? (d === "LONG" ? "#0ecb81" : d === "SHORT" ? "#f6465d" : "#f0b90b") : "#1e2329",
                color: directionFilter === d ? "#0b0e11" : "#848e9c",
                border: "1px solid #2b3139",
              }}
            >
              {d === "" ? "全部方向" : formatDirection(d)}
            </button>
          ))}

          <span className="text-xs ml-auto" style={{ color: "#848e9c" }}>共 {filteredTrades.length} 条</span>
        </div>

        <div className="card-q overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #2b3139" }}>
                  {["交易对", "方向", "状态", "策略", "入场价", "出场价", "数量", "盈亏", "入场时间(UTC+8)", "出场时间(UTC+8)", "出场原因"].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredTrades.map((t) => {
                  const pnl = parseFloat(String(t.pnl ?? "0"));
                  const pnlPct = parseFloat(String(t.pnlPercent ?? "0"));
                  return (
                    <tr key={t.tradeId} style={{ borderBottom: "1px solid #1e2329" }} className="hover:bg-[#161a1e]">
                      <td className="px-4 py-3 text-sm" style={{ color: "#eaecef" }}>{t.symbol}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded ${t.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>
                          {formatDirection(t.direction)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{formatTradeStatus(t.status)}</td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{t.strategyId}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{t.entryPrice}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{t.exitPrice ?? "-"}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{t.quantity}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: pnl >= 0 ? "#0ecb81" : "#f6465d" }}>
                        {t.status === "closed" ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)} (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%)` : "-"}
                      </td>
                      <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>{t.entryAt ? formatDateTimeUTC8(t.entryAt) : "-"}</td>
                      <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>{t.exitAt ? formatDateTimeUTC8(t.exitAt) : "-"}</td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{formatExitReason(t.exitReason)}</td>
                    </tr>
                  );
                })}
                {filteredTrades.length === 0 && (
                  <tr>
                    <td colSpan={11} className="px-4 py-12 text-center text-sm" style={{ color: "#848e9c" }}>
                      暂无符合条件的交易记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function StatCard({ label, value, valueColor = "#eaecef" }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="card-q p-4">
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-2xl font-bold font-num" style={{ color: valueColor }}>{value}</div>
    </div>
  );
}
