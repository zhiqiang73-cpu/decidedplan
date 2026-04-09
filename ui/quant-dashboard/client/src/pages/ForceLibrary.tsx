import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Layers3, Shield, Zap } from "lucide-react";

const CATEGORY_META: Record<string, { label: string; description: string }> = {
  leverage_cost_imbalance: { label: "杠杆成本失衡", description: "多空两边的持仓成本失去平衡，贵的一方会被时间和费用逼着离场。" },
  liquidity_vacuum: { label: "流动性真空", description: "价格被推离均值以后，支撑它的成交量不见了，回归就成了最省力的方向。" },
  unilateral_exhaustion: { label: "单边力量耗尽", description: "某一边连续进攻以后弹药打空，另一边开始自然接管。" },
  algorithmic_trace: { label: "算法执行痕迹", description: "大资金拆单执行会留下稳定节奏和密度特征，这些痕迹本身就是信号。" },
  potential_energy_release: { label: "势能释放", description: "价格在极值附近长期压缩，等到势能释放时会出现定向冲击。" },
  distribution_pattern: { label: "派发结构", description: "高位承接开始变差，筹码从强手向弱手转移，回落的概率随之上升。" },
  open_interest_divergence: { label: "持仓量背离", description: "价格和持仓量不再同频，说明趋势背后的跟随力量在衰减。" },
  inventory_rebalance: { label: "库存再平衡", description: "做市商或流动性提供方需要把偏掉的库存拉回中性位置。" },
  regime_change: { label: "状态切换", description: "市场从一种波动或流动性状态切到另一种状态，旧条件不再成立。" },
  generic: { label: "通用规则", description: "保底类机制，不属于某个特定物理家族。" },
};

const MECHANISM_META: Record<string, { label: string; description: string }> = {
  funding_settlement: { label: "资金费率结算窗口", description: "利用资金费率结算前后的强制调仓动机。" },
  funding_divergence: { label: "资金费率背离", description: "价格位置和资金成本矛盾，说明一边在硬扛。" },
  funding_cycle_oversold: { label: "资金周期超卖", description: "持续负费率叠加低位，容易触发被迫回补。" },
  seller_drought: { label: "卖方枯竭", description: "跌到低位但成交量干涸，卖压来源在消失。" },
  vwap_reversion: { label: "VWAP 回归", description: "无量偏离均值以后，价格通常会向成交重心回归。" },
  bottom_taker_exhaust: { label: "底部吃单卖方耗尽", description: "低位主动卖出缩到极致，说明最后一批卖家快退场了。" },
  top_buyer_exhaust: { label: "顶部追涨买方耗尽", description: "高位主动买入放缓，追价力量开始枯竭。" },
  taker_snap_reversal: { label: "瞬时吃单反转", description: "极端吃单冲击过后，缺少续航的一边会被反向修复。" },
  seller_impulse: { label: "主动卖盘冲击", description: "短时间内的大量主动卖出把价格砸离均衡，之后观察修复。" },
  algo_slicing: { label: "机构拆单执行", description: "VWAP 或 TWAP 拆单留下稳定的执行痕迹。" },
  compression_release: { label: "压缩释放", description: "长时间窄幅积累的势能在突破时集中释放。" },
  volume_climax_reversal: { label: "成交高潮反转", description: "极端放量常常对应情绪尾声，之后更容易反向。" },
  amplitude_absorption: { label: "振幅吸收", description: "大振幅没有继续扩大，说明冲击被市场吸收。" },
  near_high_distribution: { label: "高位派发", description: "价格贴近高位但跟随力量衰退，常见于顶部出货。" },
  oi_divergence: { label: "持仓量背离", description: "价格继续走，但持仓量已经不跟，这种背离通常难持久。" },
  mm_rebalance: { label: "做市库存回补", description: "做市商把偏掉的库存往中性拉，容易推动均值回归。" },
  regime_transition: { label: "状态切换", description: "波动、流动性或趋势状态在切换，旧信号要重估。" },
  generic_alpha: { label: "通用 Alpha", description: "尚未收进主物理家族的通用规则。" },
};

function getCategoryLabel(id: string) {
  return CATEGORY_META[id]?.label ?? id;
}

function getCategoryDescription(id: string) {
  return CATEGORY_META[id]?.description ?? "该类别暂无补充说明。";
}

function getMechanismLabel(id: string) {
  return MECHANISM_META[id]?.label ?? id;
}

function getMechanismDescription(id: string) {
  return MECHANISM_META[id]?.description ?? "该机制暂无补充说明。";
}

function formatWinRate(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "--";
  return `${value.toFixed(1)}%`;
}

function winRateColor(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "#848e9c";
  if (value >= 80) return "#0ecb81";
  if (value >= 60) return "#f0b90b";
  return "#f6465d";
}

export default function ForceLibrary() {
  const { data, isLoading } = trpc.alphaEngine.getForceLibrary.useQuery(undefined, { refetchInterval: 30000 });
  const [activeCategoryId, setActiveCategoryId] = useState<string | null>(null);

  const categories = data?.categories ?? [];
  const concentration = (data?.concentration ?? {}) as Record<string, number>;

  useEffect(() => {
    if (!categories.length) {
      setActiveCategoryId(null);
      return;
    }
    if (!activeCategoryId || !categories.some((category: any) => category.id === activeCategoryId)) {
      setActiveCategoryId(categories[0].id);
    }
  }, [activeCategoryId, categories]);

  const activeCategory = categories.find((category: any) => category.id === activeCategoryId) ?? null;

  const allMechanisms = useMemo(() => {
    return categories.flatMap((category: any) =>
      (category.mechanisms ?? []).map((mechanism: any) => ({ ...mechanism, categoryId: category.id })),
    );
  }, [categories]);

  const totalStrategies = useMemo(() => {
    const names = new Set<string>();
    allMechanisms.forEach((mechanism: any) => {
      (mechanism.strategies ?? []).forEach((strategy: string) => names.add(strategy));
    });
    return names.size;
  }, [allMechanisms]);

  const concentrationAlerts = Object.values(concentration).filter((value) => value >= 2).length;

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Shield size={18} style={{ color: "#f0b90b" }} />
              <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>力库</h1>
            </div>
            <p className="text-sm" style={{ color: "#848e9c" }}>
              页面只展示后端真实机制结构，不再直接把乱码字段照单全收。
            </p>
          </div>
          {isLoading && (
            <span className="px-2.5 py-1 rounded-lg text-xs" style={{ backgroundColor: "#1a1d21", color: "#848e9c" }}>
              正在刷新
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
          <SummaryCard label="力类别" value={`${categories.length}`} />
          <SummaryCard label="机制数" value={`${allMechanisms.length}`} />
          <SummaryCard label="绑定策略" value={`${totalStrategies}`} valueColor="#0ecb81" />
          <SummaryCard label="集中告警" value={`${concentrationAlerts}`} valueColor={concentrationAlerts > 0 ? "#f0b90b" : "#eaecef"} />
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
          {categories.map((category: any) => {
            const mechanisms = category.mechanisms ?? [];
            const wrRows = mechanisms.filter((mechanism: any) => mechanism.oos_win_rate != null);
            const avgWinRate = wrRows.length
              ? wrRows.reduce((sum: number, mechanism: any) => sum + Number(mechanism.oos_win_rate), 0) / wrRows.length
              : null;
            const active = category.id === activeCategory?.id;
            return (
              <button
                key={category.id}
                type="button"
                onClick={() => setActiveCategoryId(category.id)}
                className="rounded-xl p-3 text-left transition-colors"
                style={{
                  backgroundColor: "#1a1d21",
                  border: `1px solid ${active ? "#f0b90b" : "#2b3139"}`,
                }}
              >
                <div className="text-sm font-medium" style={{ color: active ? "#f0b90b" : "#eaecef" }}>
                  {getCategoryLabel(category.id)}
                </div>
                <div className="text-xs mt-1" style={{ color: "#848e9c" }}>{mechanisms.length} 个机制</div>
                <div className="text-xs font-num mt-2" style={{ color: winRateColor(avgWinRate) }}>
                  平均 OOS 胜率 {formatWinRate(avgWinRate)}
                </div>
              </button>
            );
          })}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.25fr_0.75fr] gap-5">
          <div className="card-q p-5 space-y-4">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Layers3 size={16} style={{ color: "#f0b90b" }} />
                <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>
                  {activeCategory ? getCategoryLabel(activeCategory.id) : "暂无机制类别"}
                </h2>
              </div>
              <p className="text-sm leading-6" style={{ color: "#848e9c" }}>
                {activeCategory ? getCategoryDescription(activeCategory.id) : "等待后端返回机制结构。"}
              </p>
            </div>

            {(activeCategory?.mechanisms ?? []).length === 0 ? (
              <div className="rounded-xl p-6 text-sm text-center" style={{ backgroundColor: "#161a1e", color: "#848e9c" }}>
                当前类别还没有机制明细
              </div>
            ) : (
              (activeCategory?.mechanisms ?? []).map((mechanism: any) => (
                <div key={mechanism.id} className="rounded-xl p-4 space-y-3" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <Zap size={14} style={{ color: "#f0b90b" }} />
                        <span className="text-sm font-semibold" style={{ color: "#eaecef" }}>{getMechanismLabel(mechanism.id)}</span>
                      </div>
                      <div className="text-xs mt-1 leading-5" style={{ color: "#848e9c" }}>
                        {getMechanismDescription(mechanism.id)}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-xs" style={{ color: "#848e9c" }}>OOS 胜率</div>
                      <div className="text-sm font-bold font-num" style={{ color: winRateColor(mechanism.oos_win_rate) }}>
                        {formatWinRate(mechanism.oos_win_rate)}
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                    <InfoBlock title="绑定策略" content={(mechanism.strategies ?? []).length ? (mechanism.strategies ?? []).join(" / ") : "暂无绑定"} />
                    <InfoBlock
                      title="增强关系"
                      content={(mechanism.relations?.reinforces ?? []).length
                        ? (mechanism.relations?.reinforces ?? []).map((id: string) => getMechanismLabel(id)).join(" / ")
                        : "无"}
                    />
                    <InfoBlock
                      title="冲突关系"
                      content={(mechanism.relations?.conflicts_with ?? []).length
                        ? (mechanism.relations?.conflicts_with ?? []).map((id: string) => getMechanismLabel(id)).join(" / ")
                        : "无"}
                    />
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="card-q p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Activity size={16} style={{ color: "#f0b90b" }} />
              <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>力集中度</h2>
            </div>

            {Object.keys(concentration).length === 0 ? (
              <div className="rounded-xl p-6 text-sm text-center" style={{ backgroundColor: "#161a1e", color: "#848e9c" }}>
                当前没有集中度数据
              </div>
            ) : (
              <div className="space-y-2">
                {Object.entries(concentration)
                  .sort(([, left], [, right]) => Number(right) - Number(left))
                  .map(([categoryId, count]) => {
                    const flagged = Number(count) >= 2;
                    return (
                      <div key={categoryId} className="rounded-lg px-3 py-2.5 flex items-center justify-between" style={{ backgroundColor: "#161a1e" }}>
                        <div className="flex items-center gap-2">
                          {flagged ? <AlertTriangle size={12} style={{ color: "#f0b90b" }} /> : <span className="w-2 h-2 rounded-full" style={{ backgroundColor: "#2b3139" }} />}
                          <span className="text-sm" style={{ color: "#eaecef" }}>{getCategoryLabel(categoryId)}</span>
                        </div>
                        <span className="text-sm font-num" style={{ color: flagged ? "#f0b90b" : "#848e9c" }}>{count}</span>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        </div>

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>全机制总览</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ borderBottom: "1px solid #2b3139" }}>
                  {["机制", "类别", "OOS 胜率", "绑定策略", "增强数", "冲突数"].map((header) => (
                    <th key={header} className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{header}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {allMechanisms
                  .slice()
                  .sort((left: any, right: any) => Number(right.oos_win_rate ?? -1) - Number(left.oos_win_rate ?? -1))
                  .map((mechanism: any) => (
                    <tr key={`${mechanism.categoryId}-${mechanism.id}`} style={{ borderBottom: "1px solid #1e2329" }}>
                      <td className="px-4 py-3" style={{ color: "#eaecef" }}>{getMechanismLabel(mechanism.id)}</td>
                      <td className="px-4 py-3" style={{ color: "#848e9c" }}>{getCategoryLabel(mechanism.categoryId)}</td>
                      <td className="px-4 py-3 font-num" style={{ color: winRateColor(mechanism.oos_win_rate) }}>{formatWinRate(mechanism.oos_win_rate)}</td>
                      <td className="px-4 py-3" style={{ color: "#848e9c" }}>{(mechanism.strategies ?? []).join(" / ") || "--"}</td>
                      <td className="px-4 py-3 font-num" style={{ color: "#0ecb81" }}>{(mechanism.relations?.reinforces ?? []).length}</td>
                      <td className="px-4 py-3 font-num" style={{ color: "#f6465d" }}>{(mechanism.relations?.conflicts_with ?? []).length}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function SummaryCard({ label, value, valueColor = "#eaecef" }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="card-q p-4">
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-2xl font-bold font-num" style={{ color: valueColor }}>{value}</div>
    </div>
  );
}

function InfoBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="rounded-lg p-3" style={{ backgroundColor: "#0f1318" }}>
      <div className="text-[11px] mb-1" style={{ color: "#848e9c" }}>{title}</div>
      <div className="text-xs leading-5" style={{ color: "#eaecef" }}>{content}</div>
    </div>
  );
}
