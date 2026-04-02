import QuantLayout from "@/components/QuantLayout";
import React, { useState, useMemo } from "react";
import { trpc } from "@/lib/trpc";

import { ChevronDown, ChevronUp, Download, TrendingUp, TrendingDown, BarChart2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export default function TradeHistory() {
   const [statusFilter, setStatusFilter] = useState<"" | "open" | "closed" | "cancelled">("")
  const [symbolFilter, setSymbolFilter] = useState("");
  const [directionFilter, setDirectionFilter] = useState<"" | "LONG" | "SHORT">("")
  const [strategyFilter, setStrategyFilter] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<"" | "today" | "week" | "month">("");

  const { data: trades } = trpc.trades.list.useQuery({
    status: statusFilter === "" ? undefined : statusFilter,
    symbol: symbolFilter || undefined,
    limit: 200,
  }, { refetchInterval: 15000 });

  const { data: stats } = trpc.trades.getStats.useQuery(undefined, { refetchInterval: 30000 });

  // Client-side filtering for direction, strategy, and time range
  const filteredTrades = useMemo(() => {
    let result = trades ?? [];
    if (directionFilter) result = result.filter(t => t.direction === directionFilter);
    if (strategyFilter) result = result.filter(t => t.strategyId?.toLowerCase().includes(strategyFilter.toLowerCase()));
    if (timeRange) {
      const now = Date.now();
      const cutoff = timeRange === "today" ? now - 86400000
        : timeRange === "week" ? now - 7 * 86400000
        : now - 30 * 86400000;
      result = result.filter(t => t.entryAt && new Date(t.entryAt).getTime() >= cutoff);
    }
    return result;
  }, [trades, directionFilter, strategyFilter, timeRange]);

  // PnL distribution data
  const pnlDistData = useMemo(() => {
    const closed = filteredTrades.filter(t => t.status === "closed" && t.pnl !== null);
    const buckets: Record<string, number> = {};
    closed.forEach(t => {
      const pnl = parseFloat(String(t.pnl ?? "0"));
      const bucket = pnl >= 200 ? ">200" : pnl >= 100 ? "100-200" : pnl >= 50 ? "50-100" : pnl >= 0 ? "0-50" : pnl >= -50 ? "-50-0" : pnl >= -100 ? "-100-50" : "<-100";
      buckets[bucket] = (buckets[bucket] ?? 0) + 1;
    });
    const order = ["<-100", "-100-50", "-50-0", "0-50", "50-100", "100-200", ">200"];
    return order.map(k => ({ range: k, count: buckets[k] ?? 0, positive: !k.startsWith("-") && k !== "<-100" }));
  }, [filteredTrades]);

  const todayPnl = parseFloat(String(stats?.todayPnl ?? "0"));
  const totalPnl = parseFloat(String(stats?.totalPnl ?? "0"));

  // CSV Export
  const exportCSV = () => {
    const header = "TradeID,Symbol,Direction,Strategy,EntryPrice,ExitPrice,Quantity,Leverage,PnL,PnL%,EntryAt,ExitAt,ExitReason";
    const rows = filteredTrades.map(t =>
      [t.tradeId, t.symbol, t.direction, t.strategyId ?? "", t.entryPrice ?? "", t.exitPrice ?? "",
       t.quantity ?? "", t.leverage ?? "", t.pnl ?? "", t.pnlPercent ?? "",
       t.entryAt ? new Date(t.entryAt).toISOString() : "",
       t.exitAt ? new Date(t.exitAt).toISOString() : "",
       t.exitReason ?? ""].join(",")
    );
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `trades_${Date.now()}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>交易记录</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>完整交易历史 · UTC时区</p>
          </div>
          <button
            onClick={exportCSV}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ backgroundColor: "#1e2329", color: "#848e9c", border: "1px solid #2b3139" }}
          >
            <Download size={14} /> 导出CSV
          </button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="card-q p-4">
            <div className="text-xs mb-1" style={{ color: "#848e9c" }}>总交易次数</div>
            <div className="text-2xl font-bold font-num" style={{ color: "#eaecef" }}>{stats?.totalTrades ?? 0}</div>
          </div>
          <div className="card-q p-4">
            <div className="text-xs mb-1" style={{ color: "#848e9c" }}>综合胜率</div>
            <div className="text-2xl font-bold font-num text-profit">{stats?.winRate ?? "0"}%</div>
          </div>
          <div className="card-q p-4">
            <div className="text-xs mb-1" style={{ color: "#848e9c" }}>今日盈亏</div>
            <div className={`text-2xl font-bold font-num ${todayPnl >= 0 ? "text-profit" : "text-loss"}`}>
              {todayPnl >= 0 ? "+" : ""}${todayPnl.toFixed(2)}
            </div>
          </div>
          <div className="card-q p-4">
            <div className="text-xs mb-1" style={{ color: "#848e9c" }}>总盈亏</div>
            <div className={`text-2xl font-bold font-num ${totalPnl >= 0 ? "text-profit" : "text-loss"}`}>
              {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}
            </div>
          </div>
        </div>

        {/* PnL Distribution Chart */}
        <div className="card-q p-4">
          <div className="flex items-center gap-2 mb-4">
            <BarChart2 size={14} style={{ color: "#848e9c" }} />
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>盈亏分布</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>（已平仓交易）</span>
          </div>
          <ResponsiveContainer width="100%" height={120}>
            <BarChart data={pnlDistData} barSize={28}>
              <XAxis dataKey="range" tick={{ fill: "#848e9c", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "#848e9c", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ backgroundColor: "#1e2329", border: "1px solid #2b3139", borderRadius: 8 }}
                itemStyle={{ color: "#eaecef" }}
                formatter={(v: number) => [v, "笔数"]}
              />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {pnlDistData.map((entry, i) => (
                  <Cell key={i} fill={entry.positive ? "#0ecb81" : "#f6465d"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Quick Time Filters */}
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: "#848e9c" }}>快速筛选：</span>
          {([["", "全部"], ["today", "今日"], ["week", "本周"], ["month", "本月"]] as const).map(([val, label]) => (
            <button
              key={val}
              onClick={() => setTimeRange(val)}
              className="px-3 py-1 rounded text-xs font-medium transition-colors"
              style={{
                backgroundColor: timeRange === val ? "#f0b90b" : "#1e2329",
                color: timeRange === val ? "#0b0e11" : "#848e9c",
                border: "1px solid #2b3139",
              }}
            >
              {label}
            </button>
          ))}
          <span className="text-xs ml-2" style={{ color: "#5e6673" }}>共 {filteredTrades.length} 笔</span>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 items-center">
          <Input
            value={symbolFilter}
            onChange={e => setSymbolFilter(e.target.value.toUpperCase())}
            placeholder="交易对..."
            className="w-32 text-sm h-8"
            style={{ backgroundColor: "#1e2329", borderColor: "#2b3139", color: "#eaecef" }}
          />
          <Input
            value={strategyFilter}
            onChange={e => setStrategyFilter(e.target.value)}
            placeholder="策略ID..."
            className="w-36 text-sm h-8"
            style={{ backgroundColor: "#1e2329", borderColor: "#2b3139", color: "#eaecef" }}
          />
          {/* Status filter */}
          {(["", "open", "closed", "cancelled"] as const).map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor: statusFilter === s ? "#f0b90b" : "#1e2329",
                color: statusFilter === s ? "#0b0e11" : "#848e9c",
                border: "1px solid #2b3139"
              }}
            >
              {s === "" ? "全部" : s === "open" ? "持仓中" : s === "closed" ? "已平仓" : "已取消"}
            </button>
          ))}
          {/* Direction filter */}
          {(["", "LONG", "SHORT"] as const).map(d => (
            <button
              key={d}
              onClick={() => setDirectionFilter(d)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor: directionFilter === d ? (d === "LONG" ? "#0ecb81" : d === "SHORT" ? "#f6465d" : "#f0b90b") : "#1e2329",
                color: directionFilter === d ? "#0b0e11" : "#848e9c",
                border: "1px solid #2b3139"
              }}
            >
              {d === "" ? "多空" : d === "LONG" ? "↑ 多" : "↓ 空"}
            </button>
          ))}
          <span className="text-xs ml-auto" style={{ color: "#848e9c" }}>共 {filteredTrades.length} 条</span>
        </div>

        {/* Trade Table */}
        <div className="card-q overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #2b3139" }}>
                  {["", "交易对", "方向", "策略", "入场价", "出场价", "数量", "杠杆", "盈亏", "入场时间(UTC)", "出场时间(UTC)"].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredTrades.map(t => {
                  const entryP = parseFloat(t.entryPrice ?? "0");
                  const exitP = parseFloat(t.exitPrice ?? "0");
                  const pnl = parseFloat(String(t.pnl ?? "0"));
                  const pnlPct = parseFloat(String(t.pnlPercent ?? "0"));
                  const hasPnl = t.status === "closed" && t.pnl !== null;
                  return (
                    <React.Fragment key={t.tradeId}>
                      <tr
                        className="cursor-pointer hover:bg-[#161a1e] transition-colors"
                        style={{ borderBottom: "1px solid #1e2329" }}
                        onClick={() => setExpandedId(expandedId === t.tradeId ? null : t.tradeId)}
                      >
                        <td className="px-3 py-3">
                          {expandedId === t.tradeId ? <ChevronUp size={12} style={{ color: "#848e9c" }} /> : <ChevronDown size={12} style={{ color: "#848e9c" }} />}
                        </td>
                        <td className="px-4 py-3 text-sm font-medium" style={{ color: "#eaecef" }}>{t.symbol}</td>
                        <td className="px-4 py-3">
                          <span className={`text-xs px-2 py-0.5 rounded ${t.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>
                            {t.direction === "LONG" ? "↑ 多" : "↓ 空"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs font-mono" style={{ color: "#848e9c" }}>{t.strategyId ?? "—"}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{entryP.toLocaleString()}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: exitP ? "#eaecef" : "#5e6673" }}>
                          {exitP ? exitP.toLocaleString() : "—"}
                        </td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{t.quantity}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#f0b90b" }}>{t.leverage}x</td>
                        <td className="px-4 py-3">
                          {hasPnl ? (
                            <div>
                              <div className={`text-sm font-num font-medium ${pnl >= 0 ? "text-profit" : "text-loss"}`}>
                                {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                              </div>
                              <div className={`text-xs font-num ${pnlPct >= 0 ? "text-profit" : "text-loss"}`}>
                                {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                              </div>
                            </div>
                          ) : (
                            <span className="badge-pending text-xs">持仓中</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>
                          {t.entryAt ? new Date(t.entryAt).toISOString().slice(0, 16).replace("T", " ") : "—"}
                        </td>
                        <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>
                          {t.exitAt ? new Date(t.exitAt).toISOString().slice(0, 16).replace("T", " ") : "—"}
                        </td>
                      </tr>
                      {expandedId === t.tradeId && (
                        <tr style={{ backgroundColor: "#161a1e" }}>
                          <td colSpan={11} className="px-4 py-4" style={{ borderTop: "1px solid #2b3139" }}>
                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                              <InfoBox label="交易ID" value={t.tradeId} mono />
                              <InfoBox label="策略ID" value={t.strategyId ?? "—"} mono />
                              <InfoBox label="出场原因" value={t.exitReason ?? "—"} />
                              <InfoBox label="交易方向" value={t.direction} />
                              <InfoBox label="入场价格" value={`$${entryP.toLocaleString()}`} />
                              <InfoBox label="出场价格" value={exitP ? `$${exitP.toLocaleString()}` : "持仓中"} />
                              <InfoBox label="实现盈亏" value={hasPnl ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)} (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%)` : "—"} />
                              <InfoBox label="持仓时长" value={t.entryAt && t.exitAt ? formatDuration(new Date(t.entryAt), new Date(t.exitAt)) : "持仓中"} />
                              <InfoBox label="最大有利偏移(MFE)" value={t.mfe ? `+$${parseFloat(String(t.mfe)).toFixed(2)}` : "—"} />
                              <InfoBox label="最大不利偏移(MAE)" value={t.mae ? `-$${Math.abs(parseFloat(String(t.mae))).toFixed(2)}` : "—"} />
                              <InfoBox label="手续费" value={t.fee ? `$${parseFloat(String(t.fee)).toFixed(4)}` : "—"} />
                              <InfoBox label="入场时间(UTC)" value={t.entryAt ? new Date(t.entryAt).toISOString().replace("T", " ").slice(0, 19) : "—"} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
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

function InfoBox({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="p-2 rounded" style={{ backgroundColor: "#0b0e11" }}>
      <div className="text-xs mb-0.5" style={{ color: "#848e9c" }}>{label}</div>
      <div className={`text-xs ${mono ? "font-mono" : ""}`} style={{ color: "#eaecef" }}>{value}</div>
    </div>
  );
}

function formatDuration(start: Date, end: Date): string {
  const ms = end.getTime() - start.getTime();
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (h > 24) return `${Math.floor(h / 24)}天${h % 24}小时`;
  return `${h}小时${m}分`;
}
