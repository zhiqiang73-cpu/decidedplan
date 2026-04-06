import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useState } from "react";
import { toast } from "sonner";
import { Plus, Zap, Activity, CheckCircle, Clock, AlertCircle, Trash2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function TradingPairs() {
  const [newSymbol, setNewSymbol] = useState("");
  const { data: pairs, refetch } = trpc.tradingPairs.list.useQuery(undefined, { refetchInterval: 10000 });

  const addPair = trpc.tradingPairs.add.useMutation({
    onSuccess: () => {
      toast.success("交易对已添加，Alpha引擎将自动启动");
      setNewSymbol("");
      refetch();
    },
    onError: (e) => toast.error(e.message),
  });
  const updateStatus = trpc.tradingPairs.updateStatus.useMutation({
    onSuccess: () => { toast.success("状态已更新"); refetch(); },
  });

  const handleAdd = () => {
    const sym = newSymbol.trim().toUpperCase();
    if (!sym) { toast.error("请输入交易对"); return; }
    if (!sym.endsWith("USDT") && !sym.endsWith("BTC") && !sym.endsWith("ETH")) {
      toast.error("请输入有效的交易对，如 ETHUSDT");
      return;
    }
    addPair.mutate({ symbol: sym });
  };

  const tracked = pairs?.filter(p => p.isTracked) ?? [];
  const untracked = pairs?.filter(p => !p.isTracked) ?? [];

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div>
          <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>多交易对管理</h1>
          <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>添加新交易对后，Alpha引擎自动启动数据收集与策略挖掘</p>
        </div>

        {/* Add New Pair */}
        <div className="card-q p-5">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "#eaecef" }}>添加新交易对</h3>
          <div className="flex gap-2">
            <Input
              value={newSymbol}
              onChange={e => setNewSymbol(e.target.value.toUpperCase())}
              placeholder="输入交易对，如 SOLUSDT"
              className="flex-1 font-mono text-sm"
              style={{ backgroundColor: "#161a1e", borderColor: "#2b3139", color: "#eaecef" }}
              onKeyDown={e => e.key === "Enter" && handleAdd()}
            />
            <Button
              onClick={handleAdd}
              disabled={addPair.isPending}
              style={{ backgroundColor: "#f0b90b", color: "#0b0e11" }}
            >
              <Plus size={16} className="mr-1.5" />
              {addPair.isPending ? "添加中..." : "添加"}
            </Button>
          </div>
          <div className="mt-3 p-3 rounded-lg text-xs" style={{ backgroundColor: "rgba(24,144,255,0.1)", color: "#848e9c", border: "1px solid rgba(24,144,255,0.2)" }}>
            <span style={{ color: "#1890ff" }}>自动化流程：</span> 添加后 → Alpha引擎自动下载历史数据 → IC扫描 → 策略挖掘 → 样本外验证 → 推荐策略
          </div>
        </div>

        {/* Tracked Pairs */}
        <div className="card-q overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>已追踪交易对</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>{tracked.length} 个</span>
          </div>
          <div className="divide-y" style={{ borderColor: "#1e2329" }}>
            {tracked.map(p => (
              <div key={p.symbol} className="flex items-center gap-4 px-4 py-3 hover:bg-[#161a1e] transition-colors">
                <div className="w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm flex-shrink-0" style={{ backgroundColor: "#2b3139", color: "#f0b90b" }}>
                  {p.symbol.slice(0, 2)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium" style={{ color: "#eaecef" }}>{p.symbol}</span>
                    <AlphaStatusBadge status={p.alphaEngineStatus ?? "idle"} />
                  </div>
                  <div className="text-xs mt-0.5" style={{ color: "#848e9c" }}>
                    最后更新: {p.lastDataUpdate ? new Date(p.lastDataUpdate).toUTCString().slice(0, 20) : "从未"}
                  </div>
                </div>
                {p.dataDownloadProgress !== null && p.dataDownloadProgress !== undefined && p.dataDownloadProgress < 100 && (
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <div className="progress-q w-24">
                      <div className="progress-q-fill progress-q-fill-blue" style={{ width: `${p.dataDownloadProgress}%` }} />
                    </div>
                    <span className="text-xs font-num" style={{ color: "#848e9c" }}>{p.dataDownloadProgress}%</span>
                  </div>
                )}
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <Button
                    size="sm"
                    onClick={() => updateStatus.mutate({ symbol: p.symbol, alphaEngineStatus: "scanning" })}
                    disabled={p.alphaEngineStatus === "scanning"}
                    className="h-7 px-2 text-xs"
                    style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.3)" }}
                  >
                    <Zap size={11} className="mr-1" />扫描
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => updateStatus.mutate({ symbol: p.symbol, isTracked: false } as any)}
                    className="h-7 px-2 text-xs"
                    style={{ backgroundColor: "rgba(246,70,93,0.1)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.2)" }}
                  >
                    <Trash2 size={11} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Available Pairs */}
        {untracked.length > 0 && (
          <div className="card-q overflow-hidden">
            <div className="px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>可用交易对（未追踪）</h3>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 p-4">
              {untracked.map(p => (
                <button
                  key={p.symbol}
                  onClick={() => addPair.mutate({ symbol: p.symbol })}
                  className="flex items-center gap-2 p-2.5 rounded-lg text-sm transition-colors hover:bg-[#2b3139]"
                  style={{ backgroundColor: "#161a1e", color: "#848e9c", border: "1px solid #2b3139" }}
                >
                  <Plus size={14} />
                  {p.symbol}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </QuantLayout>
  );
}

function AlphaStatusBadge({ status }: { status: string }) {
  if (status === "scanning") return (
    <span className="flex items-center gap-1 text-xs badge-pending">
      <RefreshCw size={10} className="animate-spin" />扫描中
    </span>
  );
  if (status === "completed") return (
    <span className="flex items-center gap-1 text-xs badge-active">
      <CheckCircle size={10} />已完成
    </span>
  );
  if (status === "error") return (
    <span className="flex items-center gap-1 text-xs badge-degraded">
      <AlertCircle size={10} />错误
    </span>
  );
  return (
    <span className="flex items-center gap-1 text-xs badge-retired">
      <Clock size={10} />待扫描
    </span>
  );
}
