import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import { formatDirection } from "@/lib/labels";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Activity, AlertCircle, Brain, CheckCircle2, Clock3, Play, RefreshCw, Square, Zap } from "lucide-react";

type CandidateRow = {
  candidateId: string;
  symbol: string;
  direction: string;
  fullExpression: string;
  oosWinRate: number | null;
  sampleSize: number | null;
  confidenceScore: number | null;
  status: string;
  explanation?: string | null;
  backtestStatus?: string | null;
};

type ReviewRow = {
  candidateId: string;
  direction: string;
  fullExpression?: string | null;
  oosWinRate?: number | null;
  sampleSize?: number | null;
  mechanismType?: string | null;
};

const PARAM_INPUT = "w-full rounded-lg border px-3 py-2 text-sm outline-none";

export default function AlphaEngine() {
  const { connected, alphaProgress } = useWebSocket();
  const [params, setParams] = useState({
    icThreshold: 0.05,
    oosWinRateMin: 0.6,
    maxConditions: 3,
    lookbackDays: 180,
  });
  const [logs, setLogs] = useState<Array<{ ts: string; message: string; level: "info" | "success" | "warn" }>>([]);

  const { data: rawCandidates = [], refetch: refetchCandidates } = trpc.alphaEngine.getCandidates.useQuery(undefined, { refetchInterval: 10_000 });
  const { data: runs = [], refetch: refetchRuns } = trpc.alphaEngine.getRuns.useQuery({ limit: 20 }, { refetchInterval: 5_000 });
  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, { refetchInterval: 15_000 });
  const { data: globalStatus, refetch: refetchGlobalStatus } = trpc.alphaEngine.getGlobalStatus.useQuery(undefined, { refetchInterval: 3_000 });
  const { data: rawReviewQueue = [], refetch: refetchReview } = trpc.alphaEngine.getReviewQueue.useQuery(undefined, { refetchInterval: 10_000 });

  const startGlobal = trpc.alphaEngine.startGlobal.useMutation({
    onSuccess: (result) => {
      toast.success(result.message ?? "Alpha 引擎已启动");
      refetchGlobalStatus();
      refetchRuns();
    },
    onError: () => toast.error("启动失败"),
  });
  const stopGlobal = trpc.alphaEngine.stopGlobal.useMutation({
    onSuccess: (result) => {
      toast.success(result.message ?? "Alpha 引擎已停止");
      refetchGlobalStatus();
    },
    onError: () => toast.error("停止失败"),
  });
  const approveCandidate = trpc.alphaEngine.approveCandidate.useMutation({
    onSuccess: (result) => {
      toast.success(result.message ?? "候选规则已批准");
      refetchCandidates();
    },
  });
  const rejectCandidate = trpc.alphaEngine.rejectCandidate.useMutation({
    onSuccess: () => {
      toast.success("候选规则已驳回");
      refetchCandidates();
    },
  });
  const triggerBacktest = trpc.alphaEngine.triggerBacktest.useMutation({
    onSuccess: (result) => {
      toast.success(result.message ?? "已提交回测");
      refetchCandidates();
    },
  });
  const promoterApprove = trpc.alphaEngine.promoterApprove.useMutation({
    onSuccess: (result) => {
      toast.success(result.message ?? "已写入策略池");
      refetchReview();
      refetchCandidates();
    },
  });
  const promoterReject = trpc.alphaEngine.promoterReject.useMutation({
    onSuccess: () => {
      toast.success("已从 review queue 移出");
      refetchReview();
    },
  });

  const candidates = rawCandidates as CandidateRow[];
  const reviewQueue = rawReviewQueue as ReviewRow[];
  const pendingCandidates = candidates.filter((item) => item.status === "pending");
  const approvedCandidates = candidates.filter((item) => item.status === "approved");
  const latestRun = runs[0];
  const isRunning = globalStatus?.status === "running";

  useEffect(() => {
    if (!alphaProgress) return;
    setLogs((current) => {
      const level: "info" | "success" | "warn" =
        alphaProgress.phase === "completed" ? "success" : alphaProgress.phase === "walk_forward" ? "warn" : "info";
      const next = [
        ...current.slice(-79),
        {
          ts: new Date().toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }),
          message: `[${alphaProgress.symbol}] ${alphaProgress.phase} ${alphaProgress.progress}% - ${alphaProgress.message}`,
          level,
        },
      ];
      return next;
    });
  }, [alphaProgress]);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              <Zap size={20} style={{ color: "#f0b90b" }} />
              <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>{"Alpha 引擎"}</h1>
            </div>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
              {"统一展示 discovery 状态、候选规则、review queue 和运行记录。"}
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg" style={{ backgroundColor: "#1e2329", color: connected ? "#0ecb81" : "#848e9c" }}>
            {connected ? <Activity size={14} /> : <Clock3 size={14} />}
            <span>{connected ? "WebSocket 已连接" : "WebSocket 重连中"}</span>
          </div>
        </div>

        {health && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <HealthCard label={"数据层"} status={health.layers.data.status} detail={`${health.layers.data.websocket.connected}/4 实时流`} />
            <HealthCard label={"特征层"} status={health.layers.features.status} detail={`${health.layers.features.computed}/52 特征`} />
            <HealthCard label={"信号层"} status={health.layers.signals.status} detail={`${health.layers.signals.p1Running} P1 + ${health.layers.signals.p2Running} P2`} />
            <HealthCard label={"执行层"} status={health.layers.execution.status} detail={`fill rate ${(health.layers.execution.fillRate * 100).toFixed(0)}%`} />
          </div>
        )}

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="card-q p-5 xl:col-span-2 space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"引擎控制"}</div>
                <div className="text-xs mt-1" style={{ color: "#848e9c" }}>
                  {isRunning
                    ? `当前正在处理 ${globalStatus?.currentPairs?.length ?? 0} 个交易对`
                    : "引擎处于待机状态"}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => startGlobal.mutate({ params })}
                  disabled={startGlobal.isPending || isRunning}
                  style={{ backgroundColor: "#f0b90b", color: "#0b0e11" }}
                >
                  <Play size={14} className="mr-1.5" />
                  {"启动"}
                </Button>
                <Button
                  onClick={() => stopGlobal.mutate()}
                  disabled={stopGlobal.isPending || !isRunning}
                  style={{ backgroundColor: "#1e2329", color: "#eaecef", border: "1px solid #2b3139" }}
                >
                  <Square size={14} className="mr-1.5" />
                  {"停止"}
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
              <ParamField label="IC threshold" value={params.icThreshold} step={0.01} onChange={(value) => setParams((current) => ({ ...current, icThreshold: value }))} />
              <ParamField label="OOS win rate" value={params.oosWinRateMin} step={0.01} onChange={(value) => setParams((current) => ({ ...current, oosWinRateMin: value }))} />
              <ParamField label="Max conditions" value={params.maxConditions} step={1} onChange={(value) => setParams((current) => ({ ...current, maxConditions: Math.max(1, Math.round(value)) }))} />
              <ParamField label="Lookback days" value={params.lookbackDays} step={1} onChange={(value) => setParams((current) => ({ ...current, lookbackDays: Math.max(30, Math.round(value)) }))} />
            </div>

            <div className="rounded-xl p-4" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>{"运行摘要"}</div>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
                <SummaryCell label={"引擎状态"} value={isRunning ? "运行中" : "已停止"} valueColor={isRunning ? "#0ecb81" : "#848e9c"} />
                <SummaryCell label={"总运行次数"} value={String(globalStatus?.totalRuns ?? 0)} />
                <SummaryCell label={"最新 run"} value={latestRun?.runId ?? "--"} />
                <SummaryCell label={"最新候选"} value={String(pendingCandidates.length)} />
              </div>
            </div>
          </div>

          <div className="card-q p-5 space-y-3">
            <div className="flex items-center gap-2">
              <Brain size={15} style={{ color: "#f0b90b" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"Review Queue"}</h3>
            </div>
            <SummaryCell label={"待复核"} value={String(reviewQueue.length)} />
            <SummaryCell label={"已批准候选"} value={String(approvedCandidates.length)} />
            <SummaryCell label={"当前连接"} value={connected ? "WS online" : "WS reconnecting"} valueColor={connected ? "#0ecb81" : "#848e9c"} />
            <div className="rounded-lg p-3 text-xs leading-6" style={{ backgroundColor: "#161a1e", color: "#848e9c" }}>
              {"这里只展示后端真实返回的候选及 review queue，不再做虚假计数或 UI 自造状态。"}
            </div>
          </div>
        </div>

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"待审候选"}</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>{`${pendingCandidates.length} 条`}</span>
          </div>
          {pendingCandidates.length === 0 ? (
            <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"当前没有 pending 候选。"}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr style={{ borderBottom: "1px solid #1e2329" }}>
                    {["规则", "方向", "OOS 胜率", "样本", "置信度", "操作"].map((heading) => (
                      <th key={heading} className="px-4 py-2.5 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{heading}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pendingCandidates.map((candidate, index) => (
                    <tr key={candidate.candidateId} style={{ borderBottom: index < pendingCandidates.length - 1 ? "1px solid #1e2329" : "none" }}>
                      <td className="px-4 py-3">
                        <div className="text-sm" style={{ color: "#eaecef" }}>{candidate.fullExpression}</div>
                        <div className="text-xs mt-1" style={{ color: "#848e9c" }}>{candidate.symbol}</div>
                      </td>
                      <td className="px-4 py-3 text-sm" style={{ color: "#eaecef" }}>{formatDirection(candidate.direction)}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: scoreColor(candidate.oosWinRate) }}>{formatPercent(candidate.oosWinRate)}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{String(candidate.sampleSize ?? 0)}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: confidenceColor(candidate.confidenceScore) }}>{formatConfidence(candidate.confidenceScore)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <Button size="sm" onClick={() => approveCandidate.mutate({ candidateId: candidate.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(14,203,129,0.15)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.3)" }}>
                            <CheckCircle2 size={11} className="mr-1" />
                            {"批准"}
                          </Button>
                          <Button size="sm" onClick={() => rejectCandidate.mutate({ candidateId: candidate.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(246,70,93,0.12)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.25)" }}>
                            {"驳回"}
                          </Button>
                          <Button size="sm" onClick={() => triggerBacktest.mutate({ candidateId: candidate.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.3)" }}>
                            {"回测"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <div className="card-q overflow-hidden">
            <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"Review Queue"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${reviewQueue.length} 条`}</span>
            </div>
            {reviewQueue.length === 0 ? (
              <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"暂无 review queue 条目。"}</div>
            ) : (
              <div className="divide-y" style={{ borderColor: "#1e2329" }}>
                {reviewQueue.map((item) => (
                  <div key={item.candidateId} className="px-4 py-3 flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm" style={{ color: "#eaecef" }}>{item.fullExpression ?? item.candidateId}</div>
                      <div className="text-xs mt-1" style={{ color: "#848e9c" }}>
                        {`${formatDirection(item.direction)} / ${item.mechanismType ?? "generic"} / ${formatPercent(item.oosWinRate)}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Button size="sm" onClick={() => promoterApprove.mutate({ candidateId: item.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(14,203,129,0.15)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.3)" }}>
                        {"写入"}
                      </Button>
                      <Button size="sm" onClick={() => promoterReject.mutate({ candidateId: item.candidateId, reason: "manual_review" })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(246,70,93,0.12)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.25)" }}>
                        {"移出"}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card-q overflow-hidden">
            <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"运行日志"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${logs.length} 条`}</span>
            </div>
            <div className="max-h-80 overflow-y-auto px-4 py-3 space-y-2">
              {logs.length === 0 ? (
                <div className="text-sm" style={{ color: "#848e9c" }}>{"等待 Alpha progress 输入..."}</div>
              ) : (
                logs.map((log, index) => (
                  <div key={`${log.ts}-${index}`} className="rounded-lg p-3 text-xs font-mono" style={{ backgroundColor: "#161a1e", color: log.level === "success" ? "#0ecb81" : log.level === "warn" ? "#f0b90b" : "#c9d1d9" }}>
                    <span style={{ color: "#848e9c" }}>{log.ts}</span>
                    <span>{`  ${log.message}`}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function HealthCard({ label, status, detail }: { label: string; status: string; detail: string }) {
  const color = status === "healthy" ? "#0ecb81" : status === "warning" ? "#f0b90b" : "#f6465d";
  return (
    <div className="card-q p-4">
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-xs" style={{ color: "#848e9c" }}>{label}</span>
        <span className="inline-flex items-center gap-1 text-xs" style={{ color }}>
          <AlertCircle size={12} />
          {status}
        </span>
      </div>
      <div className="text-sm" style={{ color: "#eaecef" }}>{detail}</div>
    </div>
  );
}

function ParamField({ label, value, step, onChange }: { label: string; value: number; step: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <input
        type="number"
        value={value}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className={PARAM_INPUT}
        style={{ backgroundColor: "#11161b", borderColor: "#2b3139", color: "#eaecef" }}
      />
    </label>
  );
}

function SummaryCell({ label, value, valueColor = "#eaecef" }: { label: string; value: string; valueColor?: string }) {
  return (
    <div>
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-sm font-num font-medium" style={{ color: valueColor }}>{value}</div>
    </div>
  );
}

function scoreColor(value: number | null | undefined) {
  if (value == null) return "#848e9c";
  if (value >= 80) return "#0ecb81";
  if (value >= 60) return "#f0b90b";
  return "#f6465d";
}

function confidenceColor(value: number | null | undefined) {
  if (value == null) return "#848e9c";
  if (value >= 0.85) return "#0ecb81";
  if (value >= 0.65) return "#f0b90b";
  return "#f6465d";
}

function formatPercent(value: number | null | undefined) {
  if (value == null) return "--";
  return `${value.toFixed(1)}%`;
}

function formatConfidence(value: number | null | undefined) {
  if (value == null) return "--";
  return `${(value * 100).toFixed(0)}%`;
}
