import { useState } from "react";
import { Shield, Zap, AlertTriangle, Activity } from "lucide-react";
import { trpc } from "@/lib/trpc";
import QuantLayout from "@/components/QuantLayout";

const CATEGORY_NAMES: Record<string, string> = {
  leverage_cost_imbalance: "杠杆成本失衡",
  liquidity_vacuum: "流动性真空",
  unilateral_exhaustion: "单边耗尽",
  algorithmic_trace: "算法痕迹",
  potential_energy_release: "势能释放",
  distribution_pattern: "分发形态",
  open_interest_divergence: "OI背离",
  regime_change: "状态转换",
  generic: "通用规律",
};

function WrBadge({ wr }: { wr: number | null }) {
  if (wr == null) return <span style={{ color: "#848e9c" }}>—</span>;
  const color = wr >= 80 ? "#0ecb81" : wr >= 60 ? "#f0b90b" : "#f6465d";
  return <span className="font-num font-bold text-xs" style={{ color }}>{wr.toFixed(1)}%</span>;
}

export default function ForceLibrary() {
  const { data, isLoading } = trpc.alphaEngine.getForceLibrary.useQuery(undefined, { refetchInterval: 30000 });
  const [selectedCat, setSelectedCat] = useState<string | null>(null);

  const categories: any[] = data?.categories ?? [];
  const concentration: Record<string, number> = data?.concentration ?? {};
  const activeCat = categories.find((c: any) => c.id === selectedCat) ?? categories[0] ?? null;

  const allMechs = categories.flatMap((c: any) => (c.mechanisms ?? []).map((m: any) => ({ ...m, catId: c.id })));

  return (
    <QuantLayout>
      <div className="p-4 space-y-4" style={{ color: "#eaecef" }}>
        {/* Header */}
        <div className="flex items-center gap-3">
          <Shield size={20} style={{ color: "#f0b90b" }} />
          <div>
            <h1 className="text-lg font-bold">力库</h1>
            <p className="text-xs" style={{ color: "#848e9c" }}>已验证物理机制 · 策略入场力学基础</p>
          </div>
          {isLoading && <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "#1a1d21", color: "#848e9c" }}>加载中...</span>}
        </div>

        {/* Zone 1: Category Cards */}
        <div className="grid grid-cols-3 lg:grid-cols-5 gap-2">
          {categories.map((cat: any) => {
            const mechs: any[] = cat.mechanisms ?? [];
            const avgWr = mechs.filter((m: any) => m.oos_win_rate != null).reduce((a: number, m: any, _: number, arr: any[]) => a + m.oos_win_rate / arr.length, 0) || null;
            const isActive = (activeCat?.id === cat.id);
            return (
              <div key={cat.id} onClick={() => setSelectedCat(cat.id)}
                className="p-3 rounded-lg cursor-pointer transition-all"
                style={{
                  backgroundColor: "#1a1d21",
                  border: `1px solid ${isActive ? "#f0b90b" : "#2b3139"}`,
                }}>
                <div className="text-xs font-medium mb-1" style={{ color: isActive ? "#f0b90b" : "#eaecef" }}>
                  {CATEGORY_NAMES[cat.id] ?? cat.name ?? cat.id}
                </div>
                <div className="text-xs" style={{ color: "#848e9c" }}>{mechs.length} 机制</div>
                {avgWr != null && <WrBadge wr={avgWr} />}
              </div>
            );
          })}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Zone 2: Mechanism Detail */}
          <div className="lg:col-span-2 rounded-lg p-3 space-y-3" style={{ backgroundColor: "#1a1d21", border: "1px solid #2b3139" }}>
            <div className="text-sm font-medium" style={{ color: "#f0b90b" }}>
              {activeCat ? (CATEGORY_NAMES[activeCat.id] ?? activeCat.id) : "选择一个力类别"}
            </div>
            {(activeCat?.mechanisms ?? []).map((m: any) => (
              <div key={m.id} className="p-3 rounded" style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139" }}>
                <div className="flex items-center gap-2 mb-1">
                  <Zap size={12} style={{ color: "#f0b90b" }} />
                  <span className="text-sm font-medium">{m.display_name ?? m.id}</span>
                  <WrBadge wr={m.oos_win_rate} />
                </div>
                <div className="text-xs mb-2" style={{ color: "#848e9c", fontStyle: "italic" }}>{m.essence}</div>
                <div className="flex flex-wrap gap-1">
                  {(m.strategies ?? []).map((s: string) => (
                    <span key={s} className="px-1.5 py-0.5 rounded text-xs" style={{ backgroundColor: "rgba(240,185,11,0.1)", color: "#f0b90b" }}>{s}</span>
                  ))}
                  {(m.relations?.reinforces ?? []).map((r: string) => (
                    <span key={r} className="px-1.5 py-0.5 rounded text-xs" style={{ backgroundColor: "rgba(14,203,129,0.1)", color: "#0ecb81" }}>→{r}</span>
                  ))}
                  {(m.relations?.conflicts_with ?? []).map((r: string) => (
                    <span key={r} className="px-1.5 py-0.5 rounded text-xs" style={{ backgroundColor: "rgba(246,70,93,0.1)", color: "#f6465d" }}>✕{r}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Zone 3: Force Concentration */}
          <div className="rounded-lg p-3 space-y-2" style={{ backgroundColor: "#1a1d21", border: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2 text-sm font-medium">
              <Activity size={14} style={{ color: "#f0b90b" }} />
              <span>力集中度</span>
            </div>
            {Object.entries(concentration).length === 0 && (
              <div className="text-xs" style={{ color: "#848e9c" }}>暂无持仓</div>
            )}
            {Object.entries(concentration).map(([cat, cnt]) => (
              <div key={cat} className="flex items-center justify-between text-xs">
                <span style={{ color: "#848e9c" }}>{CATEGORY_NAMES[cat] ?? cat}</span>
                <span className="flex items-center gap-1" style={{ color: (cnt as number) >= 2 ? "#f0b90b" : "#eaecef" }}>
                  {(cnt as number) >= 2 && <AlertTriangle size={10} />}
                  {cnt as number}仓
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Zone 4: Full Mechanism Table */}
        <div className="rounded-lg overflow-hidden" style={{ border: "1px solid #2b3139" }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ backgroundColor: "#1a1d21", borderBottom: "1px solid #2b3139" }}>
                {["机制","力类别","绑定策略","OOS胜率","增强","冲突"].map(h => (
                  <th key={h} className="px-3 py-2 text-left" style={{ color: "#848e9c" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {allMechs.map((m: any) => (
                <tr key={m.id} style={{ borderBottom: "1px solid #1e2329" }}>
                  <td className="px-3 py-2" style={{ color: "#eaecef" }}>{m.display_name ?? m.id}</td>
                  <td className="px-3 py-2" style={{ color: "#848e9c" }}>{CATEGORY_NAMES[m.catId] ?? m.catId}</td>
                  <td className="px-3 py-2">{(m.strategies ?? []).join(", ") || "—"}</td>
                  <td className="px-3 py-2"><WrBadge wr={m.oos_win_rate} /></td>
                  <td className="px-3 py-2" style={{ color: "#0ecb81" }}>{(m.relations?.reinforces ?? []).join(", ") || "—"}</td>
                  <td className="px-3 py-2" style={{ color: "#f6465d" }}>{(m.relations?.conflicts_with ?? []).join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </QuantLayout>
  );
}
