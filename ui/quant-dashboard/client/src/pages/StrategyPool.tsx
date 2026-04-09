import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { formatDirection, formatStrategyStatus } from "@/lib/labels";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Search, ChevronDown, ChevronUp, Play, Pause, ArrowUpDown, RefreshCw, BarChart3 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

type SortKey = "liveWinRate" | "validationWinRate" | "liveAvgReturnPct" | "pnl7d" | "closedSampleSize";
type SortDir = "asc" | "desc";
type StrategyStatus = "active" | "paused" | "degraded" | "retired";

type StrategyTypeFilter = "" | "P1" | "P2" | "ALPHA";
type DirectionFilter = "" | "LONG" | "SHORT" | "BOTH";

type StrategyRow = {
  strategyId: string;
  name: string;
  type: string;
  direction: string;
  symbol: string;
  status: StrategyStatus;
  entryCondition?: string | null;
  exitConditionTop3?: Array<{ label: string }>;
  liveWinRate?: number | null;
  validationWinRate?: number | null;
  oosWinRate?: number | null;
  liveAvgReturnPct?: number | null;
  closedSampleSize?: number | null;
  pnl7d?: string | null;
  todayTriggers?: number;
  todayWins?: number;
  notFilled?: number;
  oosSampleSize?: number;
  mechanismType?: string | null;
  backtestStatus?: string | null;
};

type WinRateItem = {
  family: string;
  oosWinRate: number | null;
};

export default function StrategyPool() {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<StrategyTypeFilter>("");
  const [statusFilter, setStatusFilter] = useState<"" | StrategyStatus>("");
  const [dirFilter, setDirFilter] = useState<DirectionFilter>("");
  const [sortKey, setSortKey] = useState<SortKey>("pnl7d");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: rawStrategies, isLoading, refetch } = trpc.strategies.list.useQuery(
    {
      type: typeFilter || undefined,
      status: statusFilter || undefined,
      search: search || undefined,
    },
    { refetchInterval: 15_000 },
  );
  const { data: rawWinRates } = trpc.alphaEngine.getSignalWinRates.useQuery(undefined, { refetchInterval: 30_000 });

  const updateStatus = trpc.strategies.updateStatus.useMutation({
    onSuccess: () => {
      toast.success("策略状态已更新");
      refetch();
    },
  });
  const triggerBacktest = trpc.strategies.triggerBacktest.useMutation({
    onSuccess: (result) => {
      toast.success(result.message);
      refetch();
    },
  });

  const strategies = (rawStrategies ?? []) as StrategyRow[];
  const winRates = (rawWinRates ?? []) as WinRateItem[];

  const validationMap = useMemo(() => {
    return Object.fromEntries(winRates.map((item) => [item.family, item.oosWinRate]));
  }, [winRates]);

  const withDerived = useMemo(() => {
    return strategies
      .filter((item) => !dirFilter || item.direction === dirFilter)
      .map((item) => ({
        ...item,
        liveWinRate: item.liveWinRate ?? null,
        validationWinRate:
          item.validationWinRate ??
          validationMap[item.strategyId] ??
          (typeof item.oosWinRate === "number" ? item.oosWinRate : null),
        liveAvgReturnPct: item.liveAvgReturnPct ?? null,
        closedSampleSize: item.closedSampleSize ?? 0,
      }));
  }, [dirFilter, strategies, validationMap]);

  const sorted = useMemo(() => {
    const rows = [...withDerived];
    rows.sort((a, b) => {
      const av = getSortValue(a, sortKey);
      const bv = getSortValue(b, sortKey);
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return rows;
  }, [sortDir, sortKey, withDerived]);

  const summary = useMemo(() => {
    const activeCount = withDerived.filter((item) => item.status === "active").length;
    const liveRows = withDerived.filter((item) => (item.closedSampleSize ?? 0) > 0 && item.liveWinRate != null);
    const avgLiveWinRate = liveRows.length
      ? liveRows.reduce((sum, item) => sum + Number(item.liveWinRate ?? 0), 0) / liveRows.length
      : null;
    const totalPnl7d = withDerived.reduce((sum, item) => sum + Number(item.pnl7d ?? "0"), 0);
    return {
      total: withDerived.length,
      activeCount,
      avgLiveWinRate,
      totalPnl7d,
    };
  }, [withDerived]);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>{"策略池"}</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
              {"统一展示实盘胜率、验证胜率、实盘均笔收益、7日盈亏和闭单样本。"}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard label={"策略总数"} value={String(summary.total)} />
          <StatCard label={"活跃策略"} value={String(summary.activeCount)} valueColor="#0ecb81" />
          <StatCard
            label={"平均实盘胜率"}
            value={summary.avgLiveWinRate == null ? "--" : `${summary.avgLiveWinRate.toFixed(1)}%`}
            valueColor="#f0b90b"
          />
          <StatCard
            label={"7日总盈亏"}
            value={formatMoney(summary.totalPnl7d)}
            valueColor={summary.totalPnl7d >= 0 ? "#0ecb81" : "#f6465d"}
          />
        </div>

        <div className="card-q p-4">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 size={14} style={{ color: "#f0b90b" }} />
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"指标口径"}</h3>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-3 text-sm">
            <MetricExplain title={"实盘胜率"} text={"基于已闭单的 live 交易计算。"} />
            <MetricExplain title={"验证胜率"} text={"后端 OOS 验证结果，不再冒充实盘数据。"} />
            <MetricExplain title={"实盘均笔收益"} text={"只统计已闭单的费后百分比收益。"} />
            <MetricExplain title={"7日盈亏"} text={"近 7 天已实现 PnL 汇总，直接对应交易日志。"} />
            <MetricExplain title={"闭单样本数"} text={"只统计 status=closed 的交易，样本不足时不过度解读。"} />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <div className="relative flex-1 min-w-48">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#848e9c" }} />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={"搜索策略名称或 ID"}
              className="pl-9 text-sm"
              style={{ backgroundColor: "#1e2329", borderColor: "#2b3139", color: "#eaecef" }}
            />
          </div>
          {(["", "P1", "P2", "ALPHA"] as StrategyTypeFilter[]).map((value) => (
            <FilterChip key={value || "all-type"} active={typeFilter === value} onClick={() => setTypeFilter(value)}>
              {value || "全部类型"}
            </FilterChip>
          ))}
          {(["", "LONG", "SHORT", "BOTH"] as DirectionFilter[]).map((value) => (
            <FilterChip key={value || "all-direction"} active={dirFilter === value} onClick={() => setDirFilter(value)}>
              {value ? formatDirection(value) : "全部方向"}
            </FilterChip>
          ))}
          {(["", "active", "paused", "degraded", "retired"] as Array<"" | StrategyStatus>).map((value) => (
            <FilterChip key={value || "all-status"} active={statusFilter === value} onClick={() => setStatusFilter(value)}>
              {value ? formatStrategyStatus(value) : "全部状态"}
            </FilterChip>
          ))}
        </div>

        <div className="card-q overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #2b3139" }}>
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{"策略"}</th>
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{"状态"}</th>
                  <SortHeader label={"实盘胜率"} sortKey="liveWinRate" current={sortKey} dir={sortDir} onSort={setSortState} />
                  <SortHeader label={"验证胜率"} sortKey="validationWinRate" current={sortKey} dir={sortDir} onSort={setSortState} />
                  <SortHeader label={"实盘均笔收益%"} sortKey="liveAvgReturnPct" current={sortKey} dir={sortDir} onSort={setSortState} />
                  <SortHeader label={"7日盈亏$"} sortKey="pnl7d" current={sortKey} dir={sortDir} onSort={setSortState} />
                  <SortHeader label={"闭单样本数"} sortKey="closedSampleSize" current={sortKey} dir={sortDir} onSort={setSortState} />
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{"回测"}</th>
                  <th className="px-4 py-3 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{"操作"}</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((strategy) => {
                  const liveWinRate = strategy.liveWinRate;
                  const validationWinRate = strategy.validationWinRate;
                  const liveAvgReturnPct = strategy.liveAvgReturnPct;
                  const pnl7d = Number(strategy.pnl7d ?? "0");
                  const closedSampleSize = strategy.closedSampleSize ?? 0;
                  const expanded = expandedId === strategy.strategyId;
                  return (
                    <FragmentRow key={strategy.strategyId}>
                      <tr
                        className="cursor-pointer transition-colors hover:bg-[#13171c]"
                        style={{ borderBottom: expanded ? "none" : "1px solid #1e2329" }}
                        onClick={() => setExpandedId(expanded ? null : strategy.strategyId)}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            {expanded ? <ChevronUp size={14} style={{ color: "#848e9c" }} /> : <ChevronDown size={14} style={{ color: "#848e9c" }} />}
                            <div>
                              <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{strategy.name}</div>
                              <div className="text-xs" style={{ color: "#848e9c" }}>{`${strategy.strategyId} / ${strategy.symbol} / ${formatDirection(strategy.direction)}`}</div>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={strategy.status} />
                        </td>
                        <td className="px-4 py-3">
                          <MetricValue value={liveWinRate} suffix="%" positiveThreshold={65} neutralThreshold={55} digits={1} />
                        </td>
                        <td className="px-4 py-3">
                          <MetricValue value={validationWinRate} suffix="%" positiveThreshold={80} neutralThreshold={60} digits={1} />
                        </td>
                        <td className="px-4 py-3">
                          <SignedValue value={liveAvgReturnPct} suffix="%" digits={4} />
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-sm font-num font-medium" style={{ color: pnl7d >= 0 ? "#0ecb81" : "#f6465d" }}>
                            {formatMoney(pnl7d)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-sm font-num font-medium" style={{ color: closedSampleSize > 0 ? "#eaecef" : "#848e9c" }}>
                            {closedSampleSize}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <BacktestBadge status={strategy.backtestStatus ?? "idle"} />
                        </td>
                        <td className="px-4 py-3" onClick={(event) => event.stopPropagation()}>
                          <div className="flex items-center gap-1.5">
                            <Button
                              size="sm"
                              onClick={() => triggerBacktest.mutate({ strategyId: strategy.strategyId })}
                              disabled={triggerBacktest.isPending || strategy.backtestStatus === "running"}
                              className="text-xs h-7 px-2"
                              style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.3)" }}
                            >
                              <Play size={10} className="mr-1" />
                              {"回测"}
                            </Button>
                            <Button
                              size="sm"
                              onClick={() => updateStatus.mutate({
                                strategyId: strategy.strategyId,
                                status: strategy.status === "active" ? "paused" : "active",
                              })}
                              disabled={updateStatus.isPending}
                              className="text-xs h-7 px-2"
                              style={{ backgroundColor: "#1e2329", color: "#848e9c", border: "1px solid #2b3139" }}
                            >
                              {strategy.status === "active" ? <Pause size={10} /> : <Play size={10} />}
                            </Button>
                          </div>
                        </td>
                      </tr>
                      {expanded && (
                        <tr style={{ backgroundColor: "#161a1e", borderBottom: "1px solid #1e2329" }}>
                          <td colSpan={9} className="px-4 py-4">
                            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                              <DetailCard title={"入场逻辑"}>
                                <p className="text-sm leading-6" style={{ color: "#c9d1d9" }}>
                                  {strategy.entryCondition || "--"}
                                </p>
                              </DetailCard>
                              <DetailCard title={"出场逻辑"}>
                                <div className="space-y-2">
                                  {(strategy.exitConditionTop3 ?? []).length === 0 ? (
                                    <div className="text-sm" style={{ color: "#848e9c" }}>--</div>
                                  ) : (
                                    (strategy.exitConditionTop3 ?? []).map((item, index) => (
                                      <div key={`${strategy.strategyId}-exit-${index}`} className="text-sm leading-6" style={{ color: "#c9d1d9" }}>
                                        {item.label}
                                      </div>
                                    ))
                                  )}
                                </div>
                              </DetailCard>
                              <DetailCard title={"执行与验证"}>
                                <div className="space-y-2 text-sm">
                                  <DetailRow label={"今日触发"} value={String(strategy.todayTriggers ?? 0)} />
                                  <DetailRow label={"今日胜单"} value={String(strategy.todayWins ?? 0)} />
                                  <DetailRow label={"未成交"} value={String(strategy.notFilled ?? 0)} />
                                  <DetailRow label={"OOS 样本"} value={String(strategy.oosSampleSize ?? 0)} />
                                  <DetailRow label={"机制标签"} value={strategy.mechanismType || "--"} />
                                </div>
                              </DetailCard>
                            </div>
                          </td>
                        </tr>
                      )}
                    </FragmentRow>
                  );
                })}
                {!isLoading && sorted.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-sm" style={{ color: "#848e9c" }}>
                      {"暂无策略数据"}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {isLoading && (
            <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"正在载入策略池..."}</div>
          )}
        </div>
      </div>
    </QuantLayout>
  );

  function setSortState(nextKey: SortKey) {
    if (sortKey === nextKey) {
      setSortDir((current) => (current === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(nextKey);
    setSortDir("desc");
  }
}

function getSortValue(strategy: StrategyRow, key: SortKey) {
  switch (key) {
    case "liveWinRate":
      return strategy.liveWinRate ?? -1;
    case "validationWinRate":
      return strategy.validationWinRate ?? -1;
    case "liveAvgReturnPct":
      return strategy.liveAvgReturnPct ?? Number.NEGATIVE_INFINITY;
    case "pnl7d":
      return Number(strategy.pnl7d ?? "0");
    case "closedSampleSize":
      return strategy.closedSampleSize ?? 0;
    default:
      return 0;
  }
}

function StatCard({ label, value, valueColor = "#eaecef" }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="card-q p-4">
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-xl font-bold font-num" style={{ color: valueColor }}>{value}</div>
    </div>
  );
}

function MetricExplain({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-lg p-3" style={{ backgroundColor: "#161a1e" }}>
      <div className="text-xs font-medium mb-1" style={{ color: "#f0b90b" }}>{title}</div>
      <div className="text-xs leading-5" style={{ color: "#848e9c" }}>{text}</div>
    </div>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
      style={{
        backgroundColor: active ? "#2b3139" : "transparent",
        color: active ? "#eaecef" : "#848e9c",
        border: "1px solid #2b3139",
      }}
    >
      {children}
    </button>
  );
}

function SortHeader({
  label,
  sortKey,
  current,
  dir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey;
  dir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const active = current === sortKey;
  return (
    <th className="px-4 py-3 text-left text-xs font-medium">
      <button className="inline-flex items-center gap-1" style={{ color: active ? "#eaecef" : "#848e9c" }} onClick={() => onSort(sortKey)}>
        <span>{label}</span>
        {active ? <ArrowUpDown size={12} className={dir === "asc" ? "rotate-180" : ""} /> : <ArrowUpDown size={12} />}
      </button>
    </th>
  );
}

function MetricValue({
  value,
  suffix,
  positiveThreshold,
  neutralThreshold,
  digits,
}: {
  value: number | null | undefined;
  suffix: string;
  positiveThreshold: number;
  neutralThreshold: number;
  digits: number;
}) {
  if (value == null) {
    return <span className="text-sm font-num" style={{ color: "#848e9c" }}>--</span>;
  }
  const color = value >= positiveThreshold ? "#0ecb81" : value >= neutralThreshold ? "#f0b90b" : "#f6465d";
  return (
    <span className="text-sm font-num font-bold" style={{ color }}>
      {`${value.toFixed(digits)}${suffix}`}
    </span>
  );
}

function SignedValue({ value, suffix, digits }: { value: number | null | undefined; suffix: string; digits: number }) {
  if (value == null) {
    return <span className="text-sm font-num" style={{ color: "#848e9c" }}>--</span>;
  }
  const color = value >= 0 ? "#0ecb81" : "#f6465d";
  return (
    <span className="text-sm font-num font-medium" style={{ color }}>
      {`${value >= 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`}
    </span>
  );
}

function StatusBadge({ status }: { status: StrategyStatus }) {
  const colorMap: Record<StrategyStatus, { bg: string; fg: string }> = {
    active: { bg: "rgba(14,203,129,0.12)", fg: "#0ecb81" },
    paused: { bg: "rgba(132,142,156,0.12)", fg: "#848e9c" },
    degraded: { bg: "rgba(240,185,11,0.12)", fg: "#f0b90b" },
    retired: { bg: "rgba(246,70,93,0.12)", fg: "#f6465d" },
  };
  const meta = colorMap[status];
  return (
    <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium" style={{ backgroundColor: meta.bg, color: meta.fg }}>
      {formatStrategyStatus(status)}
    </span>
  );
}

function BacktestBadge({ status }: { status: string }) {
  const meta =
    status === "running"
      ? { label: "运行中", icon: <RefreshCw size={11} className="animate-spin" />, color: "#f0b90b" }
      : status === "completed"
        ? { label: "已完成", icon: <Play size={11} />, color: "#0ecb81" }
        : { label: "待运行", icon: <Play size={11} />, color: "#848e9c" };
  return (
    <span className="inline-flex items-center gap-1 text-xs" style={{ color: meta.color }}>
      {meta.icon}
      {meta.label}
    </span>
  );
}

function DetailCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl p-4" style={{ backgroundColor: "#11161b", border: "1px solid #2b3139" }}>
      <div className="text-xs font-semibold mb-3" style={{ color: "#f0b90b" }}>{title}</div>
      {children}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span style={{ color: "#848e9c" }}>{label}</span>
      <span className="font-num text-right" style={{ color: "#eaecef" }}>{value}</span>
    </div>
  );
}

function FragmentRow({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

function formatMoney(value: number) {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}$${value.toFixed(2)}`;
}
