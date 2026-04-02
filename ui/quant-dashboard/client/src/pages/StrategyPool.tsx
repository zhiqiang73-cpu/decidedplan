import QuantLayout from "@/components/QuantLayout";
import React, { useState } from "react";
import { SkeletonCard, SkeletonRow } from "@/components/LoadingSkeleton";
import { trpc } from "@/lib/trpc";
import { toast } from "sonner";
import {
  TrendingUp, TrendingDown, Search, Filter, Play, Pause,
  ChevronDown, ChevronUp, BarChart2, RefreshCw, ArrowUpDown,
  CheckCircle, AlertCircle, XCircle, Clock, Zap
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell
} from "recharts";
import { Streamdown } from "streamdown";

type SortKey = "oosWinRate" | "oosAvgReturn" | "pnl7d" | "totalPnl" | "triggerCount7d" | "confidenceScore";
type SortDir = "asc" | "desc";

export default function StrategyPool() {
  const [search, setSearch] = useState("");
   const [typeFilter, setTypeFilter] = useState<"" | "P1" | "P2" | "ALPHA">("")
  const [statusFilter, setStatusFilter] = useState("")
  const [dirFilter, setDirFilter] = useState<"" | "LONG" | "SHORT" | "BOTH">("");
  const [sortKey, setSortKey] = useState<SortKey>("oosWinRate");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [llmReport, setLlmReport] = useState<Record<string, string>>({});

  const { data: strategies, refetch, isLoading: strategiesLoading } = trpc.strategies.list.useQuery({
    type: typeFilter || undefined,
    status: statusFilter || undefined,
    search: search || undefined,
  }, { refetchInterval: 15000 });

  // Client-side direction filter
  const dirFiltered = (strategies ?? []).filter(s => !dirFilter || s.direction === dirFilter);

  const updateStatus = trpc.strategies.updateStatus.useMutation({
    onSuccess: () => { toast.success("策略状态已更新"); refetch(); },
  });
  const triggerBacktest = trpc.strategies.triggerBacktest.useMutation({
    onSuccess: (r) => { toast.success(r.message); refetch(); },
  });
  const analyzeStrategy = trpc.llmAnalysis.analyzeStrategy.useMutation({
    onSuccess: (r, vars) => {
      if (r.success) setLlmReport(prev => ({ ...prev, [vars.strategyId]: String(r.report ?? "") }));
    },
  });

  const sorted = [...dirFiltered].sort((a, b) => {
    const av = parseFloat(String((a as any)[sortKey] ?? 0));
    const bv = parseFloat(String((b as any)[sortKey] ?? 0));
    return sortDir === "desc" ? bv - av : av - bv;
  });

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === "desc" ? "asc" : "desc");
    else { setSortKey(key); setSortDir("desc"); }
  };

  const stats = {
    total: strategies?.length ?? 0,
    active: strategies?.filter(s => s.status === "active").length ?? 0,
    avgWinRate: strategies?.length ? (strategies.reduce((s, x) => s + (x.oosWinRate ?? 0), 0) / strategies.length).toFixed(1) : "0",
    totalPnl7d: strategies?.reduce((s, x) => s + parseFloat(x.pnl7d ?? "0"), 0).toFixed(2) ?? "0",
  };

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>策略池</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>管理、排序和回测所有量化策略</p>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {strategiesLoading ? (
            <><SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard /></>
          ) : (
            <>
              {[
                { label: "策略总数", value: stats.total, color: "#eaecef" },
                { label: "活跃策略", value: stats.active, color: "#0ecb81" },
                { label: "平均胜率", value: `${stats.avgWinRate}%`, color: "#f0b90b" },
                { label: "7日总盈亏", value: `${parseFloat(stats.totalPnl7d) >= 0 ? "+" : ""}$${parseFloat(stats.totalPnl7d).toFixed(2)}`, color: parseFloat(stats.totalPnl7d) >= 0 ? "#0ecb81" : "#f6465d" },
              ].map(s => (
                <div key={s.label} className="card-q p-4">
                  <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{s.label}</div>
                  <div className="text-xl font-bold font-num" style={{ color: s.color }}>{s.value}</div>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Win Rate Distribution Chart */}
        {strategies && strategies.length > 0 && (
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>胜率分布直方图</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>OOS验证胜率区间分布</span>
            </div>
            <ResponsiveContainer width="100%" height={100}>
              <BarChart
                data={(() => {
                  const buckets = [
                    { range: "50-55%", min: 0.50, max: 0.55, count: 0 },
                    { range: "55-60%", min: 0.55, max: 0.60, count: 0 },
                    { range: "60-65%", min: 0.60, max: 0.65, count: 0 },
                    { range: "65-70%", min: 0.65, max: 0.70, count: 0 },
                    { range: "70-75%", min: 0.70, max: 0.75, count: 0 },
                    { range: "75%+",  min: 0.75, max: 1.00, count: 0 },
                  ];
                  strategies.forEach(s => {
                    const wr = s.oosWinRate ?? 0;
                    const b = buckets.find(b => wr >= b.min && wr < b.max);
                    if (b) b.count++;
                  });
                  return buckets;
                })()}
                margin={{ top: 4, right: 4, left: -24, bottom: 0 }}
              >
                <XAxis dataKey="range" tick={{ fill: "#848e9c", fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#848e9c", fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1e2329", border: "1px solid #2b3139", borderRadius: 6 }}
                  labelStyle={{ color: "#848e9c" }}
                  itemStyle={{ color: "#f0b90b" }}
                  formatter={(v: number) => [v, "策略数"]}
                />
                <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                  {["#2b3139", "#3d4b5a", "#f0a500", "#0ecb81", "#00c9a7", "#00e5b0"].map((color, i) => (
                    <Cell key={i} fill={color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-wrap gap-2">
          <div className="relative flex-1 min-w-48">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#848e9c" }} />
            <Input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索策略名称或ID..."
              className="pl-9 text-sm"
              style={{ backgroundColor: "#1e2329", borderColor: "#2b3139", color: "#eaecef" }}
            />
          </div>
          {(["", "P1", "P2", "ALPHA"] as const).map(t => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor: typeFilter === t ? "#f0b90b" : "#1e2329",
                color: typeFilter === t ? "#0b0e11" : "#848e9c",
                border: "1px solid #2b3139"
              }}
            >
              {t || "全部"}
            </button>
          ))}
          {(["", "LONG", "SHORT", "BOTH"] as const).map(d => (
            <button
              key={d}
              onClick={() => setDirFilter(d)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor: dirFilter === d ? (d === "LONG" ? "rgba(14,203,129,0.2)" : d === "SHORT" ? "rgba(246,70,93,0.2)" : "#2b3139") : "transparent",
                color: dirFilter === d ? (d === "LONG" ? "#0ecb81" : d === "SHORT" ? "#f6465d" : "#eaecef") : "#848e9c",
                border: "1px solid #2b3139"
              }}
            >
              {d === "" ? "全部方向" : d === "LONG" ? "↑ 多" : d === "SHORT" ? "↓ 空" : "双向"}
            </button>
          ))}
          {(["", "active", "paused", "degraded"] as const).map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                backgroundColor: statusFilter === s ? "#2b3139" : "transparent",
                color: statusFilter === s ? "#eaecef" : "#848e9c",
                border: "1px solid #2b3139"
              }}
            >
              {s === "" ? "全部状态" : s === "active" ? "活跃" : s === "paused" ? "暂停" : "降级"}
            </button>
          ))}
        </div>

        {/* Strategy Table */}
        <div className="card-q overflow-hidden">
          {/* Table Header */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #2b3139" }}>
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>策略</th>
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>状态</th>
                  <SortTh label="OOS胜率" sortKey="oosWinRate" current={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortTh label="平均收益" sortKey="oosAvgReturn" current={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortTh label="7日盈亏" sortKey="pnl7d" current={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortTh label="置信度" sortKey="confidenceScore" current={sortKey} dir={sortDir} onSort={toggleSort} />
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>回测</th>
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(s => (
                  <React.Fragment key={s.strategyId}>
                    <tr
                      className="cursor-pointer transition-colors"
                      style={{ borderBottom: "1px solid #1e2329" }}
                      onClick={() => setExpandedId(expandedId === s.strategyId ? null : s.strategyId)}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {expandedId === s.strategyId ? <ChevronUp size={14} style={{ color: "#848e9c" }} /> : <ChevronDown size={14} style={{ color: "#848e9c" }} />}
                          <div>
                            <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{s.name}</div>
                            <div className="text-xs" style={{ color: "#848e9c" }}>{s.strategyId} · {s.symbol} · {s.direction}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge-${s.status}`}>{
                          s.status === "active" ? "活跃" : s.status === "paused" ? "暂停" : s.status === "degraded" ? "降级" : "退役"
                        }</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <span className={`text-sm font-num font-bold ${(s.oosWinRate ?? 0) >= 65 ? "text-profit" : (s.oosWinRate ?? 0) >= 55 ? "text-warning-q" : "text-loss"}`}>
                            {s.oosWinRate?.toFixed(1)}%
                          </span>
                        </div>
                        <div className="text-xs" style={{ color: "#848e9c" }}>n={s.oosSampleSize}</div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm font-num text-profit">+{((s.oosAvgReturn ?? 0) * 100).toFixed(3)}%</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-sm font-num font-medium ${parseFloat(s.pnl7d ?? "0") >= 0 ? "text-profit" : "text-loss"}`}>
                          {parseFloat(s.pnl7d ?? "0") >= 0 ? "+" : ""}${parseFloat(s.pnl7d ?? "0").toFixed(2)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <div className="progress-q w-16">
                            <div className="progress-q-fill" style={{ width: `${s.confidenceScore ?? 0}%` }} />
                          </div>
                          <span className="text-xs font-num" style={{ color: "#848e9c" }}>{s.confidenceScore?.toFixed(0)}%</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <BacktestStatus status={s.backtestStatus ?? "idle"} />
                      </td>
                      <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-1.5">
                          <Button
                            size="sm"
                            onClick={() => triggerBacktest.mutate({ strategyId: s.strategyId })}
                            disabled={s.backtestStatus === "running"}
                            className="text-xs h-7 px-2"
                            style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.3)" }}
                          >
                            <Play size={10} className="mr-1" />
                            回测
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => updateStatus.mutate({
                              strategyId: s.strategyId,
                              status: s.status === "active" ? "paused" : "active"
                            })}
                            className="text-xs h-7 px-2"
                            style={{ backgroundColor: "#1e2329", color: "#848e9c", border: "1px solid #2b3139" }}
                          >
                            {s.status === "active" ? <Pause size={10} /> : <Play size={10} />}
                          </Button>
                        </div>
                      </td>
                    </tr>
                    {/* Expanded Row */}
                    {expandedId === s.strategyId && (
                      <tr key={`${s.strategyId}-expanded`} style={{ backgroundColor: "#161a1e" }}>
                        <td colSpan={8} className="px-4 py-4">
                          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                            {/* Entry Condition */}
                            <div>
                              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>入场条件</div>
                              <div className="p-3 rounded-lg font-mono text-xs" style={{ backgroundColor: "#0b0e11", color: "#f0b90b", border: "1px solid #2b3139" }}>
                                {s.entryCondition}
                              </div>
                              <div className="text-xs font-medium mt-3 mb-2" style={{ color: "#848e9c" }}>Top-3 出场条件</div>
                              <div className="space-y-1.5">
                                {((s.exitConditionTop3 as any[]) ?? []).map((c: any, i: number) => (
                                  <div key={i} className="flex items-center gap-2 p-2 rounded text-xs" style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139" }}>
                                    <span className="w-4 h-4 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0" style={{ backgroundColor: "#2b3139", color: "#f0b90b" }}>{i + 1}</span>
                                    <span style={{ color: "#eaecef" }}>{c.label}</span>
                                  </div>
                                ))}
                              </div>
                            </div>

                            {/* Backtest Chart */}
                            <div>
                              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>回测权益曲线</div>
                              {s.backtestResult && (s.backtestResult as any).equity_curve ? (
                                <>
                                  <ResponsiveContainer width="100%" height={120}>
                                    <LineChart data={(s.backtestResult as any).equity_curve.map((v: number, i: number) => ({ i, v }))}>
                                      <XAxis dataKey="i" hide />
                                      <YAxis hide domain={["auto", "auto"]} />
                                      <Tooltip
                                        contentStyle={{ backgroundColor: "#1e2329", border: "1px solid #2b3139", borderRadius: 6, fontSize: 11 }}
                                        formatter={(v: number) => [`${v.toFixed(1)}`, "净值"]}
                                      />
                                      <Line type="monotone" dataKey="v" stroke="#0ecb81" strokeWidth={2} dot={false} />
                                    </LineChart>
                                  </ResponsiveContainer>
                                  <div className="grid grid-cols-3 gap-2 mt-2">
                                    <div className="text-center p-2 rounded" style={{ backgroundColor: "#0b0e11" }}>
                                      <div className="text-xs font-num text-profit">{(s.backtestResult as any).sharpe?.toFixed(2)}</div>
                                      <div className="text-xs" style={{ color: "#848e9c" }}>夏普</div>
                                    </div>
                                    <div className="text-center p-2 rounded" style={{ backgroundColor: "#0b0e11" }}>
                                      <div className="text-xs font-num text-loss">{(s.backtestResult as any).max_drawdown?.toFixed(2)}%</div>
                                      <div className="text-xs" style={{ color: "#848e9c" }}>最大回撤</div>
                                    </div>
                                    <div className="text-center p-2 rounded" style={{ backgroundColor: "#0b0e11" }}>
                                      <div className="text-xs font-num text-profit">{s.oosWinRate?.toFixed(1)}%</div>
                                      <div className="text-xs" style={{ color: "#848e9c" }}>OOS胜率</div>
                                    </div>
                                  </div>
                                </>
                              ) : (
                                <div className="flex items-center justify-center h-24 text-xs" style={{ color: "#848e9c" }}>
                                  点击"回测"按钮运行回测
                                </div>
                              )}
                            </div>

                            {/* LLM Analysis */}
                            <div>
                              <div className="flex items-center justify-between mb-2">
                                <div className="text-xs font-medium" style={{ color: "#848e9c" }}>AI策略分析</div>
                                <Button
                                  size="sm"
                                  onClick={() => analyzeStrategy.mutate({ strategyId: s.strategyId })}
                                  disabled={analyzeStrategy.isPending}
                                  className="text-xs h-6 px-2"
                                  style={{ backgroundColor: "rgba(139,92,246,0.15)", color: "#8b5cf6", border: "1px solid rgba(139,92,246,0.3)" }}
                                >
                                  <Zap size={10} className="mr-1" />
                                  {analyzeStrategy.isPending ? "分析中..." : "AI分析"}
                                </Button>
                              </div>
                              {llmReport[s.strategyId] ? (
                                <div className="p-3 rounded-lg text-xs overflow-y-auto max-h-40" style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139", color: "#eaecef" }}>
                                  <Streamdown>{llmReport[s.strategyId]}</Streamdown>
                                </div>
                              ) : (
                                <div className="flex items-center justify-center h-24 text-xs rounded-lg" style={{ backgroundColor: "#0b0e11", color: "#848e9c", border: "1px solid #2b3139" }}>
                                  点击"AI分析"获取策略优化建议
                                </div>
                              )}
                              <div className="mt-2 grid grid-cols-2 gap-2">
                                <div className="p-2 rounded text-center" style={{ backgroundColor: "#0b0e11" }}>
                                  <div className="text-xs font-num" style={{ color: "#848e9c" }}>过拟合分数</div>
                                  <div className={`text-sm font-num ${(s.overfitScore ?? 0) < 0.2 ? "text-profit" : "text-warning-q"}`}>
                                    {((s.overfitScore ?? 0) * 100).toFixed(0)}%
                                  </div>
                                </div>
                                <div className="p-2 rounded text-center" style={{ backgroundColor: "#0b0e11" }}>
                                  <div className="text-xs font-num" style={{ color: "#848e9c" }}>特征多样性</div>
                                  <div className="text-sm font-num text-profit">
                                    {((s.featureDiversityScore ?? 0) * 100).toFixed(0)}%
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
                {sorted.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-12 text-center">
                      <div className="flex flex-col items-center gap-2" style={{ color: "#848e9c" }}>
                        <TrendingUp size={32} style={{ opacity: 0.3 }} />
                        <p className="text-sm">暂无策略数据</p>
                        <p className="text-xs">调整筛选条件或等待Alpha引擎发现新策略</p>
                      </div>
                    </td>
                  </tr>
                )}
              {strategiesLoading && Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} cols={8} />)}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function SortTh({ label, sortKey, current, dir, onSort }: {
  label: string; sortKey: SortKey; current: SortKey; dir: SortDir;
  onSort: (k: SortKey) => void;
}) {
  const active = current === sortKey;
  return (
    <th
      className="px-4 py-3 text-left text-xs font-medium cursor-pointer select-none"
      style={{ color: active ? "#f0b90b" : "#848e9c" }}
      onClick={() => onSort(sortKey)}
    >
      <div className="flex items-center gap-1">
        {label}
        <ArrowUpDown size={10} style={{ opacity: active ? 1 : 0.4 }} />
      </div>
    </th>
  );
}

function BacktestStatus({ status }: { status: string }) {
  if (status === "completed") return <span className="flex items-center gap-1 text-xs text-profit"><CheckCircle size={12} />完成</span>;
  if (status === "running") return <span className="flex items-center gap-1 text-xs text-info-q"><RefreshCw size={12} className="animate-spin" />运行中</span>;
  if (status === "failed") return <span className="flex items-center gap-1 text-xs text-loss"><XCircle size={12} />失败</span>;
  return <span className="flex items-center gap-1 text-xs text-neutral-q"><Clock size={12} />待运行</span>;
}
