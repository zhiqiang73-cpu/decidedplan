import type { ReactNode } from "react";
import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import { Activity, AlertCircle, BarChart2, DollarSign, Target, Wifi, WifiOff } from "lucide-react";
import { formatDateTimeUTC8 } from "@/lib/time";
import { formatDirection, formatForceCategory, formatRegime } from "@/lib/labels";

export default function Dashboard() {
  const { connected } = useWebSocket();
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 30000 });
  const { data: tradeStats } = trpc.trades.getStats.useQuery(undefined, { refetchInterval: 30000 });
  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, { refetchInterval: 30000 });
  const { data: strategies } = trpc.strategies.list.useQuery({ status: "active" });
  const { data: liveSnapshot } = trpc.execution.getLiveSnapshot.useQuery(undefined, { refetchInterval: 5000 });
  const { data: tradingPairs } = trpc.tradingPairs.list.useQuery(undefined, { refetchInterval: 30000 });
  const { data: regimeData } = trpc.alphaEngine.getRegimeStatus.useQuery(undefined, { refetchInterval: 10000 });
  const { data: winRates } = trpc.alphaEngine.getSignalWinRates.useQuery(undefined, { refetchInterval: 30000 });
  const { data: forceData } = trpc.alphaEngine.getForceConcentration.useQuery(undefined, { refetchInterval: 10000 });

  const totalEquity = parseFloat(wallet?.totalEquity ?? "0");
  const unrealizedPnl = parseFloat(wallet?.unrealizedPnl ?? "0");
  const todayPnl = parseFloat(tradeStats?.todayPnl ?? "0");
  const winRate = parseFloat(tradeStats?.winRate ?? "0");

  const positions = liveSnapshot?.positions ?? [];
  const pendingOrders = liveSnapshot?.pendingOrders ?? [];
  const markPrice = liveSnapshot?.price ?? null;

  const totalNotional = positions.reduce((sum, p) => sum + (p.entryPrice ?? 0) * (p.quantity ?? 0), 0);

  const REGIME_COLOR: Record<string, string> = {
    QUIET_TREND: "#0ecb81",
    VOLATILE_TREND: "#f0b90b",
    RANGE_BOUND: "#1890ff",
    VOL_EXPANSION: "#f6a600",
    CRISIS: "#f6465d",
    UNKNOWN: "#848e9c",
  };
  const currentRegime = regimeData?.regime ?? "UNKNOWN";
  const regimeColor = REGIME_COLOR[currentRegime] ?? "#848e9c";
  const regimeLabel = formatRegime(currentRegime);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>系统仪表盘</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
              实时监控 · UTC+8 {formatDateTimeUTC8(new Date())}
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

        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <KpiCard
            title="账户权益"
            value={`$${totalEquity.toLocaleString("en-US", { minimumFractionDigits: 2 })}`}
            sub="总资产估值"
            icon={<DollarSign size={16} />}
          />
          <KpiCard
            title="今日盈亏"
            value={`${todayPnl >= 0 ? "+" : ""}$${todayPnl.toFixed(2)}`}
            sub="UTC+8 今日"
            valueColor={todayPnl >= 0 ? "#0ecb81" : "#f6465d"}
            icon={<BarChart2 size={16} />}
          />
          <KpiCard
            title="未实现盈亏"
            value={`${unrealizedPnl >= 0 ? "+" : ""}$${unrealizedPnl.toFixed(2)}`}
            sub={`${positions.length} 笔持仓`}
            valueColor={unrealizedPnl >= 0 ? "#0ecb81" : "#f6465d"}
            icon={<Activity size={16} />}
          />
          <KpiCard
            title="综合胜率"
            value={`${winRate.toFixed(1)}%`}
            sub={`${tradeStats?.totalTrades ?? 0} 笔历史`}
            valueColor={winRate >= 60 ? "#0ecb81" : "#f0a500"}
            icon={<Target size={16} />}
          />
          <KpiCard
            title="市场状态"
            value={regimeLabel}
            sub="BTCUSDT · 实时"
            valueColor={regimeColor}
            icon={<BarChart2 size={16} />}
          />
        </div>

        {/* Regime + Win Rates + Force Concentration */}
        <div className="px-4 pb-3 space-y-2">
          {/* Regime */}
          <div className="flex items-center gap-2 text-xs">
            <span style={{ color: "#848e9c" }}>市场状态:</span>
            <span
              className="px-2 py-0.5 rounded font-medium"
              style={{
                backgroundColor: "rgba(0,0,0,0.3)",
                color:
                  regimeData?.regime === "QUIET_TREND"
                    ? "#0ecb81"
                    : regimeData?.regime === "VOLATILE_TREND"
                      ? "#f0b90b"
                      : regimeData?.regime === "RANGE_BOUND"
                        ? "#58a6ff"
                        : ["VOL_EXPANSION", "CRISIS"].includes(regimeData?.regime ?? "")
                          ? "#f6465d"
                          : "#848e9c",
              }}
            >
              {regimeLabel}
            </span>
          </div>

          {/* Win Rate Bars */}
          {winRates && winRates.length > 0 && (
            <div>
              <div className="text-xs mb-1" style={{ color: "#848e9c" }}>策略验证胜率</div>
              <div className="flex flex-wrap gap-1.5">
                {winRates.filter((r) => r.oosWinRate != null).map((r) => (
                  <div
                    key={r.family}
                    className="flex items-center gap-1 px-2 py-1 rounded text-xs"
                    style={{ backgroundColor: "#1a1d21", border: "1px solid #2b3139" }}
                  >
                    <span style={{ color: "#848e9c" }}>{r.family}</span>
                    <span
                      className="font-bold font-num"
                      style={{ color: r.oosWinRate >= 80 ? "#0ecb81" : r.oosWinRate >= 60 ? "#f0b90b" : "#f6465d" }}
                    >
                      {r.oosWinRate.toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Force Concentration */}
          {forceData?.concentration && Object.keys(forceData.concentration).length > 0 && (
            <div className="flex items-center gap-2 flex-wrap text-xs">
              <span style={{ color: "#848e9c" }}>力集中度:</span>
              {Object.entries(forceData.concentration)
                .filter(([, value]) => Number(value) > 0)
                .map(([key, value]) => (
                  <span
                    key={key}
                    className="px-2 py-0.5 rounded"
                    style={{
                      backgroundColor: "rgba(0,0,0,0.3)",
                      color: Number(value) >= 2 ? "#f0b90b" : "#848e9c",
                      border: `1px solid ${Number(value) >= 2 ? "#f0b90b" : "#2b3139"}`,
                    }}
                  >
                    {formatForceCategory(key)}: {Number(value)}
                    
                  </span>
                ))}
            </div>
          )}
        </div>

        {/* Signal Win Rate Bar Strip */}
        <div className="card-q p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>策略验证正确率（白名单准入）</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>样本外滚动验证</span>
          </div>
          {(!winRates || winRates.length === 0) ? (
            <div className="text-xs" style={{ color: "#848e9c" }}>暂无数据</div>
          ) : (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              {winRates.map((s) => {
                const wr = s.oosWinRate;
                const barColor = wr == null ? "#2b3139" : wr >= 80 ? "#0ecb81" : wr >= 60 ? "#f0b90b" : "#f6465d";
                const textColor = wr == null ? "#848e9c" : wr >= 80 ? "#0ecb81" : wr >= 60 ? "#f0b90b" : "#f6465d";
                return (
                  <div key={s.family} className="p-2 rounded" style={{ backgroundColor: "#161a1e" }}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs truncate" style={{ color: "#848e9c", maxWidth: "70%" }}>{s.name || s.family}</span>
                      <span className="text-xs font-num font-bold" style={{ color: textColor }}>
                        {wr != null ? `${wr.toFixed(0)}%` : "—"}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: "#2b3139" }}>
                      <div className="h-full rounded-full transition-all" style={{ width: `${wr ?? 0}%`, backgroundColor: barColor }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="card-q p-4">
            <div className="flex items-center gap-2 mb-3">
              <AlertCircle size={14} style={{ color: "#f0a500" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>系统健康</h3>
            </div>
            <div className="space-y-2 text-sm">
              <Row label="综合得分" value={`${health?.overall ?? 0}%`} valueColor={(health?.overall ?? 0) >= 80 ? "#0ecb81" : "#f0a500"} />
              <Row label="执行层成交率" value={`${((health?.layers?.execution?.fillRate ?? 0) * 100).toFixed(0)}%`} valueColor="#eaecef" />
              <Row label="持仓名义价值" value={`$${totalNotional.toFixed(2)}`} valueColor="#eaecef" />
              <Row label="当前挂单数" value={`${pendingOrders.length}`} valueColor="#eaecef" />
            </div>
            {(health?.issues ?? []).slice(0, 2).map((issue, i) => (
              <div key={i} className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: "rgba(240,165,0,0.1)", color: "#f0a500", border: "1px solid rgba(240,165,0,0.2)" }}>
                {issue.message}
              </div>
            ))}
          </div>

          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>活跃策略</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{strategies?.length ?? 0} 个</span>
            </div>
            <div className="space-y-2">
              {(strategies ?? []).slice(0, 6).map((s) => (
                <div key={s.strategyId} className="py-2 px-2 rounded" style={{ backgroundColor: "#161a1e" }}>
                  <div className="text-xs font-medium" style={{ color: "#eaecef" }}>{s.name}</div>
                  <div className="text-xs" style={{ color: "#848e9c" }}>{s.symbol} / {formatDirection(s.direction)}</div>
                </div>
              ))}
            </div>
          {/* Force Concentration Zone */}
          {forceData?.concentration && Object.keys(forceData.concentration).length > 0 && (
            <div className="mt-3 pt-3" style={{ borderTop: "1px solid #2b3139" }}>
              <div className="flex items-center gap-1.5 mb-2">
                <AlertCircle size={12} style={{ color: "#f0a500" }} />
                <span className="text-xs font-medium" style={{ color: "#848e9c" }}>力量集中度</span>
              </div>
              <div className="space-y-1">
                {Object.entries(forceData.concentration).map(([category, count]) => (
                  <div key={category} className="flex items-center justify-between text-xs">
                    <span style={{ color: "#848e9c" }}>{formatForceCategory(category)}</span>
                    <span className="font-num font-medium" style={{ color: (count as number) >= 2 ? "#f6465d" : "#eaecef" }}>
                      {count as number} 仓
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          </div>

          <div className="card-q p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>当前持仓</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{positions.length} 笔</span>
            </div>
            {positions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8" style={{ color: "#848e9c" }}>
                <Activity size={32} style={{ opacity: 0.3 }} />
                <p className="text-sm mt-2">暂无持仓</p>
              </div>
            ) : (
              <div className="space-y-2">
                {positions.map((p) => {
                  const entryP = p.entryPrice ?? 0;
                  const currentP = markPrice ?? entryP;
                  const signedMove = p.direction === "SHORT" ? entryP - currentP : currentP - entryP;
                  const pnlPct = entryP > 0 ? (signedMove / entryP) * 100 : 0;
                  return (
                    <div key={p.positionId} className="flex items-center justify-between py-2 px-2 rounded" style={{ backgroundColor: "#161a1e" }}>
                      <div>
                        <div className="text-xs font-medium" style={{ color: "#eaecef" }}>{p.symbol} / {formatDirection(p.direction)}</div>
                        <div className="text-xs font-num" style={{ color: "#848e9c" }}>@{entryP.toLocaleString()}</div>
                      </div>
                      <div className={`text-sm font-num font-medium ${pnlPct >= 0 ? "text-profit" : "text-loss"}`}>
                        {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="card-q overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>数据状态</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>BTCUSDT</span>
          </div>
          <div className="px-4 py-3 text-sm" style={{ color: "#848e9c" }}>
            价格: <span style={{ color: "#eaecef" }}>${(liveSnapshot?.price ?? 0).toLocaleString()}</span>
            <span className="mx-3">|</span>
            最后更新: <span style={{ color: "#eaecef" }}>{formatDateTimeUTC8(liveSnapshot?.timestamp ?? null)}</span>
            <span className="mx-3">|</span>
            交易对数量: <span style={{ color: "#eaecef" }}>{tradingPairs?.length ?? 0}</span>
          </div>
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
  icon: ReactNode;
  valueColor?: string;
}) {
  return (
    <div className="card-q p-4">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs" style={{ color: "#848e9c" }}>{title}</div>
        <div style={{ color: "#848e9c" }}>{icon}</div>
      </div>
      <div className="text-xl font-bold font-num" style={{ color: valueColor }}>{value}</div>
      <div className="text-xs mt-1" style={{ color: "#5e6673" }}>{sub}</div>
    </div>
  );
}

function Row({ label, value, valueColor }: { label: string; value: string; valueColor: string }) {
  return (
    <div className="flex items-center justify-between">
      <span style={{ color: "#848e9c" }}>{label}</span>
      <span className="font-num" style={{ color: valueColor }}>{value}</span>
    </div>
  );
}

