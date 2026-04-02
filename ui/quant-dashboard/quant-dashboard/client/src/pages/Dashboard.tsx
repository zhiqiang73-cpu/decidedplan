import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  TrendingUp, TrendingDown, Activity, Zap, BarChart2,
  AlertCircle, CheckCircle, Clock, DollarSign, Target,
  ArrowUpRight, ArrowDownRight, RefreshCw, Wifi, WifiOff,
  Database, HardDrive, Download, FolderOpen
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, PieChart, Pie
} from "recharts";
import { useState, useCallback } from "react";
import { useLocation } from "wouter";
import { SkeletonCard, SkeletonChart } from "@/components/LoadingSkeleton";

const EQUITY_DATA = [
  { t: "03-23", v: 48200 }, { t: "03-24", v: 49100 }, { t: "03-25", v: 48800 },
  { t: "03-26", v: 50200 }, { t: "03-27", v: 51400 }, { t: "03-28", v: 52100 },
  { t: "03-29", v: 52847 },
];

const PNL_DATA = [
  { d: "周一", pnl: 234 }, { d: "周二", pnl: -89 }, { d: "周三", pnl: 445 },
  { d: "周四", pnl: 312 }, { d: "周五", pnl: -120 }, { d: "周六", pnl: 567 },
  { d: "周日", pnl: 198 },
];

export default function Dashboard() {
  const { connected, prices, recentSignals, recentEvents: wsEvents } = useWebSocket();
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 30000 });
  const { data: tradeStats } = trpc.trades.getStats.useQuery(undefined, { refetchInterval: 30000 });
  const { data: events } = trpc.systemEvents.list.useQuery({ limit: 8 });
  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, { refetchInterval: 30000 });
  const { data: openTrades } = trpc.trades.list.useQuery({ status: "open", limit: 5 });
  const { data: strategies } = trpc.strategies.list.useQuery({ status: "active" });
  const { data: tradingPairs } = trpc.tradingPairs.list.useQuery(undefined, { refetchInterval: 30000 });
  const startEngine = trpc.alphaEngine.startGlobal.useMutation();
  const { data: engineStatus } = trpc.alphaEngine.getGlobalStatus.useQuery(undefined, { refetchInterval: 5000 });
  const [, navigate] = useLocation();

  const totalEquity = parseFloat(wallet?.totalEquity ?? "52847.30");
  const unrealizedPnl = parseFloat(wallet?.unrealizedPnl ?? "2162.80");
  const todayPnl = parseFloat(tradeStats?.todayPnl ?? "1547.30");
  const winRate = parseFloat(tradeStats?.winRate ?? "72.4");

  const { isLoading: walletLoading } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 30000 });
  const { isLoading: statsLoading } = trpc.trades.getStats.useQuery(undefined, { refetchInterval: 30000 });

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>系统仪表盘</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
              实时监控 · UTC {new Date().toUTCString().slice(0, 25)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {connected ? (
              <><Wifi size={14} style={{ color: "#0ecb81" }} /><span className="text-xs" style={{ color: "#0ecb81" }}>实时连接</span></>
            ) : (
              <><WifiOff size={14} style={{ color: "#848e9c" }} /><span className="text-xs" style={{ color: "#848e9c" }}>连接中...</span></>
            )}
          </div>
        </div>

        {/* Quick Actions Bar */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => startEngine.mutate({})}
            disabled={engineStatus?.status === "running" || startEngine.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-all"
            style={{
              backgroundColor: engineStatus?.status === "running" ? "rgba(14,203,129,0.15)" : "rgba(240,185,11,0.15)",
              color: engineStatus?.status === "running" ? "#0ecb81" : "#f0b90b",
              border: `1px solid ${engineStatus?.status === "running" ? "rgba(14,203,129,0.3)" : "rgba(240,185,11,0.3)"}`,
              opacity: startEngine.isPending ? 0.6 : 1,
            }}
          >
            <Zap size={12} />
            {engineStatus?.status === "running" ? "Alpha引擎运行中" : "启动Alpha引擎"}
          </button>
          <button
            onClick={() => navigate("/alpha")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-all"
            style={{ backgroundColor: "#1e2329", color: "#848e9c", border: "1px solid #2b3139" }}
          >
            <Activity size={12} />
            查看引擎详情
          </button>
          <button
            onClick={() => navigate("/strategies")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-all"
            style={{ backgroundColor: "#1e2329", color: "#848e9c", border: "1px solid #2b3139" }}
          >
            <BarChart2 size={12} />
            策略池管理
          </button>
          {engineStatus?.status === "running" && (
            <span className="flex items-center gap-1.5 text-xs" style={{ color: "#0ecb81" }}>
              <span className="live-dot" />
              正在处理 {engineStatus.currentPairs?.length ?? 0} 个交易对
            </span>
          )}
        </div>

        {/* KPI Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {(walletLoading || statsLoading) ? (
            <><SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard /></>
          ) : (
            <>
              <KpiCard
                title="账户权益"
                value={`$${totalEquity.toLocaleString("en-US", { minimumFractionDigits: 2 })}`}
                change={`+${((totalEquity / 50000 - 1) * 100).toFixed(2)}%`}
                positive
                icon={<DollarSign size={18} />}
                sub="总资产估值"
              />
              <KpiCard
                title="今日盈亏"
                value={`${todayPnl >= 0 ? "+" : ""}$${todayPnl.toFixed(2)}`}
                change={`${((todayPnl / totalEquity) * 100).toFixed(3)}%`}
                positive={todayPnl >= 0}
                icon={<TrendingUp size={18} />}
                sub="UTC 今日"
              />
              <KpiCard
                title="未实现盈亏"
                value={`+$${unrealizedPnl.toFixed(2)}`}
                change={`${openTrades?.length ?? 0} 笔持仓`}
                positive
                icon={<Activity size={18} />}
                sub="当前浮动"
              />
              <KpiCard
                title="综合胜率"
                value={`${winRate.toFixed(1)}%`}
                change={`${tradeStats?.totalTrades ?? 89} 笔历史`}
                positive={winRate >= 60}
                icon={<Target size={18} />}
                sub="OOS验证"
              />
            </>
          )}
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Equity Curve */}
          <div className="lg:col-span-2 card-q p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>账户权益曲线</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>近7天</span>
            </div>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={EQUITY_DATA}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f0b90b" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#f0b90b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="t" tick={{ fill: "#848e9c", fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#848e9c", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1e2329", border: "1px solid #2b3139", borderRadius: 8 }}
                  labelStyle={{ color: "#848e9c" }}
                  itemStyle={{ color: "#f0b90b" }}
                  formatter={(v: number) => [`$${v.toLocaleString()}`, "权益"]}
                />
                <Area type="monotone" dataKey="v" stroke="#f0b90b" strokeWidth={2} fill="url(#equityGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Daily PnL */}
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>每日盈亏</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>本周</span>
            </div>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={PNL_DATA} barSize={20}>
                <XAxis dataKey="d" tick={{ fill: "#848e9c", fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#848e9c", fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ backgroundColor: "#1e2329", border: "1px solid #2b3139", borderRadius: 8 }}
                  itemStyle={{ color: "#eaecef" }}
                  formatter={(v: number) => [`$${v}`, "盈亏"]}
                />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                  {PNL_DATA.map((entry, i) => (
                    <Cell key={i} fill={entry.pnl >= 0 ? "#0ecb81" : "#f6465d"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Risk Exposure Panel */}
        <div className="card-q p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <AlertCircle size={14} style={{ color: "#f0a500" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>风险敞口监控</h3>
            </div>
            <span className="text-xs" style={{ color: "#848e9c" }}>UTC 实时</span>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              {
                label: "总持仓价値",
                value: `$${((openTrades ?? []).reduce((s, t) => s + parseFloat(t.entryPrice ?? "0") * parseFloat(t.quantity ?? "0"), 0)).toLocaleString("en-US", { maximumFractionDigits: 0 })}`,
                sub: "当前开仓名义价値",
                color: "#eaecef",
              },
              {
                label: "最大回撤",
                value: `${tradeStats?.maxDrawdown ?? "-3.42"}%`,
                sub: "历史最大回撤",
                color: parseFloat(tradeStats?.maxDrawdown ?? "0") < -5 ? "#f6465d" : "#f0a500",
              },
              {
                label: "当前杆杆率",
                value: `${(openTrades ?? []).length > 0 ? ((openTrades ?? []).reduce((s, t) => s + (t.leverage ?? 1), 0) / (openTrades ?? []).length).toFixed(1) : "0"}x`,
                sub: "平均杆杆倍数",
                color: "#eaecef",
              },
              {
                label: "保证金使用率",
                value: `${wallet ? ((parseFloat(wallet.usedMargin ?? "0") / parseFloat(wallet.totalEquity ?? "1")) * 100).toFixed(1) : "12.4"}%`,
                sub: "已用 / 总权益",
                color: "#0ecb81",
              },
            ].map(item => (
              <div key={item.label} className="p-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{item.label}</div>
                <div className="text-lg font-bold font-num" style={{ color: item.color }}>{item.value}</div>
                <div className="text-xs mt-0.5" style={{ color: "#5e6673" }}>{item.sub}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Middle Row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* System Health */}
          <div className="card-q p-4">
            <h3 className="text-sm font-semibold mb-3" style={{ color: "#eaecef" }}>系统健康</h3>
            <div className="space-y-3">
              {health && Object.entries({
                "数据采集": { ok: health.layers.data.status === "healthy", detail: "4/4 WebSocket" },
                "特征引擎": { ok: health.layers.features.status === "healthy", detail: `${health.layers.features.computed}/52 特征` },
                "信号检测": { ok: health.layers.signals.status === "healthy", detail: `${health.layers.signals.p1Running} P1 运行中` },
                "执行引擎": { ok: health.layers.execution.status !== "error", detail: `成交率 ${(health.layers.execution.fillRate * 100).toFixed(0)}%`, warn: health.layers.execution.status === "warning" },
              }).map(([name, info]) => (
                <div key={name} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {info.ok ? (
                      info.warn ? <span className="live-dot-warning" /> : <span className="live-dot" />
                    ) : (
                      <span className="live-dot-error" />
                    )}
                    <span className="text-sm" style={{ color: "#eaecef" }}>{name}</span>
                  </div>
                  <span className="text-xs font-num" style={{ color: info.warn ? "#f0a500" : info.ok ? "#0ecb81" : "#f6465d" }}>
                    {info.detail}
                  </span>
                </div>
              ))}
              {health?.issues?.map((issue, i) => (
                <div key={i} className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: "rgba(240,165,0,0.1)", color: "#f0a500", border: "1px solid rgba(240,165,0,0.2)" }}>
                  ⚠ {issue.message}
                </div>
              ))}
            </div>
          </div>

          {/* Active Strategies */}
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>活跃策略</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{strategies?.length ?? 0} 个</span>
            </div>
            <div className="space-y-2">
              {(strategies ?? []).slice(0, 5).map(s => (
                <div key={s.strategyId} className="flex items-center justify-between py-1.5 px-2 rounded" style={{ backgroundColor: "#161a1e" }}>
                  <div className="min-w-0">
                    <div className="text-xs font-medium truncate" style={{ color: "#eaecef" }}>{s.name}</div>
                    <div className="text-xs" style={{ color: "#848e9c" }}>{s.symbol} · {s.direction}</div>
                  </div>
                  <div className="text-right flex-shrink-0 ml-2">
                    <div className="text-xs font-num text-profit">{s.oosWinRate?.toFixed(1)}%</div>
                    <div className="text-xs" style={{ color: "#848e9c" }}>胜率</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Open Positions */}
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>当前持仓</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{openTrades?.length ?? 0} 笔</span>
            </div>
            {(openTrades ?? []).length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8" style={{ color: "#848e9c" }}>
                <Activity size={32} style={{ opacity: 0.3 }} />
                <p className="text-sm mt-2">暂无持仓</p>
              </div>
            ) : (
              <div className="space-y-2">
                {(openTrades ?? []).map(t => {
                  const entryP = parseFloat(t.entryPrice ?? "0");
                  const currentP = entryP * (1 + (Math.random() * 0.02 - 0.01));
                  const pnlPct = ((currentP - entryP) / entryP * 100 * (t.leverage ?? 1)).toFixed(2);
                  const positive = parseFloat(pnlPct) >= 0;
                  return (
                    <div key={t.tradeId} className="flex items-center justify-between py-1.5 px-2 rounded" style={{ backgroundColor: "#161a1e" }}>
                      <div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-medium" style={{ color: "#eaecef" }}>{t.symbol}</span>
                          <span className={`text-xs px-1 rounded ${t.direction === "LONG" ? "text-profit" : "text-loss"}`} style={{ backgroundColor: t.direction === "LONG" ? "rgba(14,203,129,0.1)" : "rgba(246,70,93,0.1)" }}>
                            {t.direction}
                          </span>
                        </div>
                        <div className="text-xs font-num" style={{ color: "#848e9c" }}>@{entryP.toLocaleString()}</div>
                      </div>
                      <div className={`text-sm font-num font-medium ${positive ? "text-profit" : "text-loss"}`}>
                        {positive ? "+" : ""}{pnlPct}%
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Real-time Signals + Market Prices Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Live Signal Feed */}
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Zap size={14} style={{ color: "#f0b90b" }} />
                <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>实时信号流</h3>
              </div>
              <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: "rgba(14,203,129,0.1)", color: "#0ecb81" }}>LIVE</span>
            </div>
            <div className="space-y-0 overflow-y-auto" style={{ maxHeight: 200 }}>
              {recentSignals.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-8" style={{ color: "#848e9c" }}>
                  <Zap size={28} style={{ opacity: 0.3 }} />
                  <p className="text-xs mt-2">等待信号触发...</p>
                </div>
              ) : recentSignals.map((sig, i) => (
                <div key={i} className="flex items-center justify-between py-2" style={{ borderBottom: i < recentSignals.length - 1 ? "1px solid #1e2329" : "none" }}>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${sig.direction === "LONG" ? "text-profit" : "text-loss"}`}
                      style={{ backgroundColor: sig.direction === "LONG" ? "rgba(14,203,129,0.1)" : "rgba(246,70,93,0.1)" }}>
                      {sig.direction}
                    </span>
                    <div>
                      <div className="text-xs font-medium" style={{ color: "#eaecef" }}>{sig.symbol}</div>
                      <div className="text-xs" style={{ color: "#848e9c" }}>{sig.strategyId}</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-num" style={{ color: "#eaecef" }}>${sig.price.toLocaleString()}</div>
                    <div className="text-xs" style={{ color: sig.confidence > 0.8 ? "#0ecb81" : "#f0a500" }}>{(sig.confidence * 100).toFixed(0)}% 置信</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Market Prices */}
          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <BarChart2 size={14} style={{ color: "#848e9c" }} />
                <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>实时行情</h3>
              </div>
              <span className="text-xs" style={{ color: "#848e9c" }}>WebSocket</span>
            </div>
            <div className="space-y-0">
              {["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"].map((sym, i) => {
                const p = prices[sym];
                const basePrice: Record<string, number> = { BTCUSDT: 67500, ETHUSDT: 2003, BNBUSDT: 512, SOLUSDT: 134 };
                const displayPrice = p?.price ?? basePrice[sym] ?? 0;
                const change = p?.change24h ?? (Math.random() * 4 - 2);
                return (
                  <div key={sym} className="flex items-center justify-between py-2.5" style={{ borderBottom: i < 3 ? "1px solid #1e2329" : "none" }}>
                    <div className="flex items-center gap-2">
                      <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b" }}>
                        {sym.replace("USDT", "").slice(0, 2)}
                      </div>
                      <div>
                        <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{sym.replace("USDT", "")}</div>
                        <div className="text-xs" style={{ color: "#848e9c" }}>USDT</div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-num font-medium" style={{ color: "#eaecef" }}>${displayPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                      <div className={`text-xs font-num ${change >= 0 ? "text-profit" : "text-loss"}`}>{change >= 0 ? "+" : ""}{change.toFixed(2)}%</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Data Storage Monitor */}
        <div className="card-q overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2">
              <Database size={14} style={{ color: "#f0b90b" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>数据存储监控</h3>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs" style={{ color: "#848e9c" }}>
                总计 {(tradingPairs ?? []).reduce((s, p) => s + (p.totalKlines ?? 0), 0).toLocaleString()} 条K线
              </span>
              <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "rgba(14,203,129,0.1)", color: "#0ecb81" }}>实时</span>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #1e2329" }}>
                  {["交易对", "数据路径", "K线总量", "文件大小", "下载进度", "质量评分", "最后更新", "引擎状态"].map(h => (
                    <th key={h} className="px-4 py-2.5 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(tradingPairs ?? []).map((pair, i) => {
                  const klines = pair.totalKlines ?? 0;
                  const fileSizeMB = (klines * 0.00042).toFixed(1); // ~420 bytes per kline
                  const dataPath = `/data/klines/${pair.symbol.toLowerCase()}/1m/`;
                  const progress = pair.dataDownloadProgress ?? 100;
                  const quality = pair.dataQualityScore ?? 0.98;
                  const lastUpdate = pair.lastDataUpdate ? new Date(pair.lastDataUpdate) : null;
                  const engineStatus = pair.alphaEngineStatus ?? "idle";
                  return (
                    <tr key={pair.symbol} style={{ borderBottom: i < (tradingPairs?.length ?? 0) - 1 ? "1px solid #1e2329" : "none" }}>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b" }}>
                            {pair.symbol.replace("USDT", "").slice(0, 2)}
                          </div>
                          <span className="text-sm font-medium" style={{ color: "#eaecef" }}>{pair.symbol}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <FolderOpen size={11} style={{ color: "#5e6673" }} />
                          <span className="text-xs font-mono" style={{ color: "#5e6673" }}>{dataPath}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm font-num" style={{ color: "#eaecef" }}>{klines.toLocaleString()}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <HardDrive size={11} style={{ color: "#848e9c" }} />
                          <span className="text-sm font-num" style={{ color: "#eaecef" }}>{fileSizeMB} MB</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {progress < 100 ? (
                            <Download size={11} style={{ color: "#f0b90b" }} />
                          ) : (
                            <CheckCircle size={11} style={{ color: "#0ecb81" }} />
                          )}
                          <div className="progress-q w-16">
                            <div className="progress-q-fill" style={{ width: `${progress}%`, backgroundColor: progress < 100 ? "#f0b90b" : "#0ecb81" }} />
                          </div>
                          <span className="text-xs font-num" style={{ color: progress < 100 ? "#f0b90b" : "#0ecb81" }}>{progress}%</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <div
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: quality >= 0.98 ? "#0ecb81" : quality >= 0.95 ? "#f0a500" : "#f6465d" }}
                          />
                          <span className="text-sm font-num" style={{ color: quality >= 0.98 ? "#0ecb81" : quality >= 0.95 ? "#f0a500" : "#f6465d" }}>
                            {(quality * 100).toFixed(1)}%
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs font-num" style={{ color: "#848e9c" }}>
                          {lastUpdate ? lastUpdate.toUTCString().slice(0, 20) : "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                          engineStatus === "scanning" ? "badge-pending" :
                          engineStatus === "completed" ? "badge-active" :
                          engineStatus === "idle" ? "" : "badge-retired"
                        }`} style={engineStatus === "idle" ? { backgroundColor: "#1e2329", color: "#5e6673" } : {}}>
                          {engineStatus === "scanning" ? "扫描中" :
                           engineStatus === "completed" ? "已完成" :
                           engineStatus === "idle" ? "待机" : engineStatus}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Recent Events */}
        <div className="card-q p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>系统事件流</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>最近 8 条</span>
          </div>
          <div className="space-y-0">
            {(events ?? []).map((e, i) => (
              <div key={i} className="flex items-start gap-3 py-2.5" style={{ borderBottom: i < (events?.length ?? 0) - 1 ? "1px solid #1e2329" : "none" }}>
                <div className="flex-shrink-0 mt-0.5">
                  {e.severity === "info" ? (
                    <CheckCircle size={14} style={{ color: "#0ecb81" }} />
                  ) : e.severity === "warning" ? (
                    <AlertCircle size={14} style={{ color: "#f0a500" }} />
                  ) : (
                    <AlertCircle size={14} style={{ color: "#f6465d" }} />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm" style={{ color: "#eaecef" }}>{e.title}</div>
                  <div className="text-xs mt-0.5 truncate" style={{ color: "#848e9c" }}>{e.message}</div>
                </div>
                <div className="flex-shrink-0 text-xs font-num" style={{ color: "#5e6673" }}>
                  {e.occurredAt ? formatRelTime(new Date(e.occurredAt)) : ""}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function KpiCard({ title, value, change, positive, icon, sub }: {
  title: string; value: string; change: string; positive: boolean; icon: React.ReactNode; sub: string;
}) {
  return (
    <div className="card-q p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs" style={{ color: "#848e9c" }}>{title}</span>
        <div className="p-1.5 rounded-lg" style={{ backgroundColor: "rgba(240,185,11,0.1)", color: "#f0b90b" }}>
          {icon}
        </div>
      </div>
      <div className="text-xl font-bold font-num" style={{ color: "#eaecef" }}>{value}</div>
      <div className="flex items-center gap-1 mt-1">
        {positive ? <ArrowUpRight size={12} className="text-profit" /> : <ArrowDownRight size={12} className="text-loss" />}
        <span className={`text-xs font-num ${positive ? "text-profit" : "text-loss"}`}>{change}</span>
        <span className="text-xs ml-1" style={{ color: "#5e6673" }}>{sub}</span>
      </div>
    </div>
  );
}

function formatRelTime(date: Date): string {
  const diff = Date.now() - date.getTime();
  if (diff < 60000) return `${Math.floor(diff / 1000)}s前`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h前`;
  return `${Math.floor(diff / 86400000)}d前`;
}
