import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { type ReactNode, useMemo } from "react";
import { CheckCircle2, Clock3, GitBranch, Layers3, Lock, LoaderCircle } from "lucide-react";

const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  completed: { label: "已完成", color: "#0ecb81", bg: "rgba(14,203,129,0.10)" },
  in_progress: { label: "进行中", color: "#1890ff", bg: "rgba(24,144,255,0.10)" },
  pending: { label: "待处理", color: "#f0b90b", bg: "rgba(240,185,11,0.10)" },
  blocked: { label: "阻塞", color: "#f6465d", bg: "rgba(246,70,93,0.10)" },
};

const PRIORITY_META: Record<string, string> = {
  critical: "关键",
  high: "高",
  medium: "中",
  low: "低",
};

const LAYER_META: Record<string, { label: string; description: string }> = {
  backtest: { label: "回测层", description: "验证信号是否真能在历史里兑现为费后收益。" },
  execution: { label: "执行层", description: "把信号真正变成挂单、成交和出场。" },
  alpha: { label: "Alpha 层", description: "发现新规则、审核候选和比较出场逻辑。" },
  ui: { label: "界面层", description: "让控制台只讲真话，不演示不存在的能力。" },
  report: { label: "报告层", description: "把阶段性结论沉淀成能继续决策的文档。" },
  data: { label: "数据层", description: "补齐 OI、LSR 等关键数据，不让样本缺口误导结论。" },
};

const TASK_COPY: Record<number, { title: string; description: string }> = {
  1: { title: "搭建 run_pipeline_backtest.py 并完成首轮全链路回测", description: "用逐 bar 重放验证 SignalRunner 的真实盈亏，同时补上 MFE 和 MAE 的观察。" },
  2: { title: "确认管道回测结论，只保留 Maker 路径", description: "已经确认 Taker 路径期望值偏差过大，主链执行应继续坚持限价成交。" },
  3: { title: "补跑 P1-6 的完整回测样本", description: "把底部量能枯竭这一支补进回测结论，避免当前统计缺口。" },
  4: { title: "开发限价单执行引擎的模拟版本", description: "实现被动挂单、超时、IOC 补单和二次尝试，先在模拟盘验证。" },
  5: { title: "把成交率从 62% 往 70% 以上推", description: "继续统计 filled 与 not_filled 的差异，并校准 ENTRY_OFFSET 这类执行参数。" },
  6: { title: "把 Smart Exit 接进实盘执行", description: "把 MFE、MAE、shadow hard-stop 和 profit protect 统一接入 outcome tracker。" },
  7: { title: "审计固定持有与智能出场的差异", description: "用 shadow audit 证明固定 hold bars 是否真在拖后腿。" },
  8: { title: "继续审核新的 Alpha 候选", description: "重点盯高 OOS 胜率候选，确认它们是否真的符合主链哲学。" },
  9: { title: "把 UI 全面接到 Python 真实数据", description: "移除假数据来源，统一读取 system_state、trades、alerts 和各类输出文件。" },
  10: { title: "核对 Binance 测试网订单和本地交易历史", description: "双向核对 trades.csv 与 allOrders，避免历史页漏单或重复。" },
  11: { title: "输出阶段性结论报告", description: "沉淀回测、执行、候选审核和下一步优先级，方便继续收敛主链。" },
  12: { title: "补齐 OI 和 LSR 的历史覆盖", description: "当前样本长度不足，很多持仓结构特征还谈不上充分验证。" },
};

function getStatusMeta(status: string) {
  return STATUS_META[status] ?? { label: status, color: "#848e9c", bg: "rgba(132,142,156,0.10)" };
}

function getLayerMeta(layer?: string) {
  if (!layer) {
    return { label: "未分层", description: "当前任务还没有归到明确层级。" };
  }
  return LAYER_META[layer] ?? { label: layer, description: "当前层级暂无补充说明。" };
}

export default function DevProgress() {
  const { data } = trpc.devProgress.getTasks.useQuery(undefined, { refetchInterval: 30000 });

  const tasks = useMemo(() => {
    const rows = data ?? [];
    return rows
      .slice()
      .sort((left, right) => Number(left.sortOrder ?? 999) - Number(right.sortOrder ?? 999) || left.id - right.id)
      .map((task) => ({
        ...task,
        displayTitle: TASK_COPY[task.id]?.title ?? `任务 ${task.id}`,
        displayDescription: TASK_COPY[task.id]?.description ?? "等待补充任务说明。",
      }));
  }, [data]);

  const total = tasks.length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const inProgress = tasks.filter((task) => task.status === "in_progress").length;
  const pending = tasks.filter((task) => task.status === "pending").length;
  const blocked = tasks.filter((task) => task.status === "blocked").length;
  const progressPct = total > 0 ? Math.round((completed / total) * 100) : 0;

  const groups = useMemo(() => {
    const bucket = new Map<string, typeof tasks>();
    tasks.forEach((task) => {
      const key = task.layer ?? "unknown";
      const rows = bucket.get(key) ?? [];
      rows.push(task);
      bucket.set(key, rows);
    });
    return Array.from(bucket.entries());
  }, [tasks]);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 max-w-6xl space-y-5">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <GitBranch size={18} style={{ color: "#f0b90b" }} />
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>开发进度</h1>
          </div>
          <p className="text-sm" style={{ color: "#848e9c" }}>
            页面已经改成只读后端真实任务，不再用前端手写模块假装进度。
          </p>
        </div>

        <div className="card-q p-5 space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <div className="text-sm font-semibold" style={{ color: "#eaecef" }}>总体进度</div>
              <div className="text-xs mt-1" style={{ color: "#848e9c" }}>数据源: data/dev_tasks.json</div>
            </div>
            <div className="text-2xl font-bold font-num" style={{ color: "#f0b90b" }}>{progressPct}%</div>
          </div>
          <div className="progress-q" style={{ height: 8 }}>
            <div className="progress-q-fill" style={{ width: `${progressPct}%`, height: "100%" }} />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatCard label="已完成" value={`${completed}`} valueColor="#0ecb81" />
            <StatCard label="进行中" value={`${inProgress}`} valueColor="#1890ff" />
            <StatCard label="待处理" value={`${pending}`} valueColor="#f0b90b" />
            <StatCard label="阻塞" value={`${blocked}`} valueColor="#f6465d" />
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.2fr_0.8fr] gap-5">
          <div className="card-q p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Layers3 size={16} style={{ color: "#f0b90b" }} />
              <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>分层看板</h2>
            </div>

            {groups.length === 0 ? (
              <div className="rounded-xl p-6 text-sm text-center" style={{ backgroundColor: "#161a1e", color: "#848e9c" }}>
                当前没有任务
              </div>
            ) : (
              groups.map(([layer, rows]) => {
                const layerMeta = getLayerMeta(layer);
                return (
                  <div key={layer} className="rounded-xl overflow-hidden" style={{ border: "1px solid #2b3139" }}>
                    <div className="px-4 py-3" style={{ backgroundColor: "#161a1e", borderBottom: "1px solid #2b3139" }}>
                      <div className="flex items-center justify-between gap-3 flex-wrap">
                        <div>
                          <div className="text-sm font-semibold" style={{ color: "#eaecef" }}>{layerMeta.label}</div>
                          <div className="text-xs mt-1" style={{ color: "#848e9c" }}>{layerMeta.description}</div>
                        </div>
                        <div className="text-xs font-num" style={{ color: "#848e9c" }}>{rows.length} 项</div>
                      </div>
                    </div>

                    <div className="divide-y" style={{ borderColor: "#1e2329" }}>
                      {rows.map((task) => {
                        const statusMeta = getStatusMeta(task.status);
                        const priorityLabel = PRIORITY_META[task.priority] ?? task.priority;
                        return (
                          <div key={task.id} className="px-4 py-3 space-y-2" style={{ backgroundColor: "#101417" }}>
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{task.displayTitle}</div>
                                <div className="text-xs mt-1 leading-6" style={{ color: "#848e9c" }}>{task.displayDescription}</div>
                              </div>
                              <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
                                <span className="px-2 py-1 rounded-lg text-xs" style={{ backgroundColor: statusMeta.bg, color: statusMeta.color }}>
                                  {statusMeta.label}
                                </span>
                                <span className="px-2 py-1 rounded-lg text-xs" style={{ backgroundColor: "#1a1d21", color: "#f0b90b" }}>
                                  {priorityLabel}
                                </span>
                              </div>
                            </div>
                            <div className="text-[11px]" style={{ color: "#5e6673" }}>
                              任务 ID: {task.id}
                              {task.completedAt ? ` · 完成时间 ${task.completedAt.slice(0, 10)}` : ""}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <div className="space-y-4">
            <div className="card-q p-5 space-y-3">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={16} style={{ color: "#0ecb81" }} />
                <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>执行状态解读</h2>
              </div>
              <Legend icon={<CheckCircle2 size={14} style={{ color: "#0ecb81" }} />} label="已完成" text="已经落地，后端任务状态为 completed。" />
              <Legend icon={<LoaderCircle size={14} style={{ color: "#1890ff" }} />} label="进行中" text="已经开工，但还没有收尾。" />
              <Legend icon={<Clock3 size={14} style={{ color: "#f0b90b" }} />} label="待处理" text="方向明确，但还没有开始实作。" />
              <Legend icon={<Lock size={14} style={{ color: "#f6465d" }} />} label="阻塞" text="受数据或依赖限制，暂时推进不了。" />
            </div>

            <div className="card-q p-5 space-y-3">
              <div className="text-sm font-semibold" style={{ color: "#eaecef" }}>当前工程判断</div>
              <div className="text-sm leading-7" style={{ color: "#848e9c" }}>
                这页现在终于讲的是后端真实任务，而不是前端自己编的模块清单。进度条、层级和状态都跟同一份任务源同步，少了很多“看起来很满，实际没接线”的花活。
              </div>
            </div>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function StatCard({ label, value, valueColor = "#eaecef" }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="rounded-lg px-3 py-3" style={{ backgroundColor: "#161a1e" }}>
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-2xl font-bold font-num" style={{ color: valueColor }}>{value}</div>
    </div>
  );
}

function Legend({ icon, label, text }: { icon: ReactNode; label: string; text: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="pt-0.5">{icon}</div>
      <div>
        <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{label}</div>
        <div className="text-xs mt-1 leading-5" style={{ color: "#848e9c" }}>{text}</div>
      </div>
    </div>
  );
}
