import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { Activity, Clock } from "lucide-react";

export default function Positions() {
  const { data: liveSnapshot } = trpc.execution.getLiveSnapshot.useQuery(undefined, { refetchInterval: 5000 });
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 10000 });

  const positions = liveSnapshot?.positions ?? [];
  const pendingOrders = liveSnapshot?.pendingOrders ?? [];
  const markPrice = liveSnapshot?.price ?? null;

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>持仓监控</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>实时持仓状态 · UTC+8</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="live-dot" />
            <span className="text-xs text-profit">实时</span>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="card-q p-4 text-center">
            <div className="text-2xl font-bold font-num" style={{ color: "#eaecef" }}>{positions.length}</div>
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

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>活跃持仓</h3>
          </div>
          {positions.length === 0 ? (
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
                  {positions.map((p) => {
                    const entryP = p.entryPrice ?? 0;
                    const qty = p.quantity ?? 0;
                    const currentP = markPrice ?? entryP;
                    const signedMove = p.direction === "SHORT" ? (entryP - currentP) : (currentP - entryP);
                    const pnlPct = entryP > 0 ? (signedMove / entryP) * 100 : 0;
                    const pnlUsd = signedMove * qty;
                    const holdingMins = p.entryAt ? Math.max(0, Math.floor((Date.now() - new Date(p.entryAt).getTime()) / 60000)) : 0;
                    return (
                      <tr key={p.positionId} style={{ borderBottom: "1px solid #1e2329" }} className="hover:bg-[#1e2329] transition-colors">
                        <td className="px-4 py-3 text-sm font-medium" style={{ color: "#eaecef" }}>{p.symbol}</td>
                        <td className="px-4 py-3">
                          <span className={`text-xs px-2 py-0.5 rounded ${p.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>
                            {p.direction}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{p.strategyFamily}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{entryP.toLocaleString()}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{qty.toFixed(4)}</td>
                        <td className="px-4 py-3 text-sm font-num" style={{ color: "#f0b90b" }}>10x</td>
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

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>当前委托</h3>
          </div>
          {pendingOrders.length === 0 ? (
            <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>暂无挂单</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr style={{ borderBottom: "1px solid #2b3139" }}>
                    {["订单ID", "信号", "数量", "委托价"].map(h => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pendingOrders.map((o) => (
                    <tr key={o.orderId} style={{ borderBottom: "1px solid #1e2329" }}>
                      <td className="px-4 py-3 text-xs font-mono" style={{ color: "#eaecef" }}>{o.orderId}</td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#848e9c" }}>{o.signalName}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{o.quantity.toFixed(4)}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{o.requestedPrice.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </QuantLayout>
  );
}
