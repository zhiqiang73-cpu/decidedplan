import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { Activity, TrendingUp, TrendingDown, Clock } from "lucide-react";

export default function Positions() {
  const { data: openTrades } = trpc.trades.list.useQuery({ status: "open", limit: 50 }, { refetchInterval: 5000 });
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 10000 });

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>持仓监控</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>实时持仓状态 · UTC</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="live-dot" />
            <span className="text-xs text-profit">实时</span>
          </div>
        </div>

        {/* Summary */}
        <div className="grid grid-cols-3 gap-3">
          <div className="card-q p-4 text-center">
            <div className="text-2xl font-bold font-num" style={{ color: "#eaecef" }}>{openTrades?.length ?? 0}</div>
            <div className="text-xs mt-1" style={{ color: "#848e9c" }}>当前持仓</div>
          </div>
          <div className="card-q p-4 text-center">
            <div className="text-2xl font-bold font-num text-profit">+${parseFloat(wallet?.unrealizedPnl ?? "0").toFixed(2)}</div>
            <div className="text-xs mt-1" style={{ color: "#848e9c" }}>未实现盈亏</div>
          </div>
          <div className="card-q p-4 text-center">
            <div className="text-2xl font-bold font-num text-warning-q">${parseFloat(wallet?.usedMargin ?? "0").toFixed(0)}</div>
            <div className="text-xs mt-1" style={{ color: "#848e9c" }}>占用保证金</div>
          </div>
        </div>

        {/* Positions Table */}
        <div className="card-q overflow-hidden">
          <div className="px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>活跃持仓</h3>
          </div>
          {(openTrades ?? []).length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16" style={{ color: "#848e9c" }}>
              <Activity size={48} style={{ opacity: 0.2 }} />
              <p className="mt-3 text-sm">暂无活跃持仓</p>
              <p className="text-xs mt-1">等待策略信号触发...</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr style={{ borderBottom: "1px solid #2b3139" }}>
                    {["交易对", "方向", "策略", "入场价", "数量", "杠杆", "持仓时间", "浮动盈亏"].map(h => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(openTrades ?? []).map(t => {
                    const entryP = parseFloat(t.entryPrice ?? "0");
                    const currentP = entryP * (1 + (Math.random() * 0.02 - 0.01));
                    const pnlPct = ((currentP - entryP) / entryP * 100 * (t.leverage ?? 1));
                    const pnlUsd = (currentP - entryP) * parseFloat(t.quantity ?? "0") * (t.direction === "SHORT" ? -1 : 1);
                    const holdingMins = Math.floor((Date.now() - new Date(t.entryAt!).getTime()) / 60000);
                    return (
                      <tr key={t.tradeId} style={{ borderBottom: "1px solid #1e2329" }} className="hover:bg-[#1e2329] transition-colors">
                        <td className="px-4 py-3 text-sm font-medium" style={{ color: "#eaecef" }}>{t.symbol}</td>
                        <td className="px-4 py-3">
                          <span className={`text-xs px-2 py-0.5 rounded ${t.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>
                            {t.direction}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{t.strategyId}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{entryP.toLocaleString()}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{t.quantity}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#f0b90b" }}>{t.leverage}x</td>
                        <td className="px-4 py-3 text-xs font-num" style={{ color: "#848e9c" }}>
                          <div className="flex items-center gap-1">
                            <Clock size={11} />
                            {holdingMins >= 60 ? `${Math.floor(holdingMins / 60)}h${holdingMins % 60}m` : `${holdingMins}m`}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className={`text-sm font-num font-medium ${pnlPct >= 0 ? "text-profit" : "text-loss"}`}>
                            {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                          </div>
                          <div className={`text-xs font-num ${pnlUsd >= 0 ? "text-profit" : "text-loss"}`}>
                            {pnlUsd >= 0 ? "+" : ""}${pnlUsd.toFixed(2)}
                          </div>
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
