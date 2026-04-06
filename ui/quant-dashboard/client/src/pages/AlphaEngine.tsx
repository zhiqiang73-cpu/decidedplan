import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import {
  Zap, Play, CheckCircle, XCircle, Clock, ChevronDown, ChevronUp,
  TrendingUp, TrendingDown, BarChart2, Activity, RefreshCw,
  ThumbsUp, ThumbsDown, Settings, AlertCircle, ArrowRight,
  Brain, BookOpen, Cpu, RotateCcw
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

const MECHANISM_ZH: Record<string, string> = {
  funding_settlement: "资金费率结算",
  funding_divergence: "资金费率背离",
  funding_cycle_oversold: "资金费率过卖",
  seller_drought: "卖方枯竭",
  vwap_reversion: "VWAP回归",
  compression_release: "仓位压缩释放",
  bottom_taker_exhaust: "底部卖方耗尽",
  near_high_distribution: "高位分发",
  oi_divergence: "OI背离",
  mm_rebalance: "做市商再平衡",
  algo_slicing: "算法拆单",
  regime_transition: "状态转换",
  generic_alpha: "通用Alpha",
  taker_snap_reversal: "吃单反转",
  amplitude_absorption: "振幅吸收",
  volume_climax_reversal: "量能高潮反转",
};

const ENGINE_PHASES = [
  { key: "data_download", label: "数据下载", desc: "获取历史K线数据" },
  { key: "ic_scan", label: "IC扫描", desc: "52+特征信息系数计算" },
  { key: "atom_mining", label: "原子挖掘", desc: "单条件入场规则搜索" },
  { key: "combo_scan", label: "组合扫描", desc: "种子+确认条件组合" },
  { key: "walk_forward", label: "滚动验证", desc: "样本外滚动验证" },
  { key: "causal_validate", label: "因果验证", desc: "方向-机制一致性+过拟合检测" },
  { key: "exit_mining", label: "出场挖掘", desc: "机制衰竭(A优先)+MFE兜底(B)" },
  { key: "completed", label: "完成", desc: "结果汇总" },
];

export default function AlphaEngine() {
  const { alphaProgress, connected } = useWebSocket();
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(null);
  const [showParams, setShowParams] = useState(false);
  const [params, setParams] = useState({
    icThreshold: 0.05,
    oosWinRateMin: 0.60,
    maxConditions: 3,
    lookbackDays: 180,
  });

  const { data: candidates, refetch: refetchCandidates } = trpc.alphaEngine.getCandidates.useQuery(undefined, { refetchInterval: 10000 });
  const { data: runs, refetch: refetchRuns } = trpc.alphaEngine.getRuns.useQuery({ limit: 20 }, { refetchInterval: 5000 });
  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, { refetchInterval: 15000 });
  const { data: globalStatus, refetch: refetchGlobalStatus } = trpc.alphaEngine.getGlobalStatus.useQuery(undefined, { refetchInterval: 3000 });

  const startGlobal = trpc.alphaEngine.startGlobal.useMutation({
    onSuccess: (r) => {
      if (r.success) toast.success(r.message ?? "Alpha引擎已全局启动");
      else toast.warning(r.message ?? "引擎已在运行中");
      refetchGlobalStatus(); refetchRuns();
    },
    onError: () => toast.error("启动失败"),
  });
  const stopGlobal = trpc.alphaEngine.stopGlobal.useMutation({
    onSuccess: (r) => { toast.info(r.message ?? "Alpha引擎已停止"); refetchGlobalStatus(); },
    onError: () => toast.error("停止失败"),
  });
  const approveCandidate = trpc.alphaEngine.approveCandidate.useMutation({
    onSuccess: () => { toast.success("候选策略已批准并激活"); refetchCandidates(); },
  });
  const rejectCandidate = trpc.alphaEngine.rejectCandidate.useMutation({
    onSuccess: () => { toast.success("候选策略已驳回"); refetchCandidates(); },
  });
  const triggerBacktest = trpc.alphaEngine.triggerBacktest.useMutation({
    onSuccess: () => { toast.success("回测已启动"); refetchCandidates(); },
  });

  // LLM Promoter Engine
  const { data: llmState, refetch: refetchLLMState } = trpc.alphaEngine.getLLMEngineState.useQuery(undefined, { refetchInterval: 8000 });
  const { data: reviewQueue, refetch: refetchReview } = trpc.alphaEngine.getReviewQueue.useQuery(undefined, { refetchInterval: 10000 });
  const [showLLMConfig, setShowLLMConfig] = useState(false);
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmModel, setLlmModel] = useState("kimi-k2.5");
  const [llmBaseUrl, setLlmBaseUrl] = useState("https://coding.dashscope.aliyuncs.com/v1");
  const [autoApproveThr, setAutoApproveThr] = useState(0.92);
  const [reviewThr, setReviewThr] = useState(0.70);
  const [expandedReviewId, setExpandedReviewId] = useState<string | null>(null);

  const promoterApprove = trpc.alphaEngine.promoterApprove.useMutation({
    onSuccess: () => { toast.success("已批准并写入策略库"); refetchReview(); refetchLLMState(); refetchCandidates(); },
  });
  const promoterReject = trpc.alphaEngine.promoterReject.useMutation({
    onSuccess: () => { toast.info("已拒绝"); refetchReview(); refetchLLMState(); },
  });
  const saveLLMConfig = trpc.alphaEngine.saveLLMConfig.useMutation({
    onSuccess: () => { toast.success("LLM 配置已保存"); setShowLLMConfig(false); },
  });

  const pendingCandidates = candidates?.filter(c => c.status === "pending") ?? [];
  const approvedCandidates = candidates?.filter(c => c.status === "approved") ?? [];
  const isRunning = globalStatus?.status === "running";
  const latestRun = runs?.[0];

  // Real-time log stream
  const [logs, setLogs] = useState<Array<{ ts: string; level: "info" | "warn" | "success" | "error"; msg: string }>>([]);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!alphaProgress) return;
    const entry = {
      ts: new Date().toISOString().slice(11, 23),
      level: alphaProgress.phase === "completed" ? "success" as const
        : alphaProgress.phase === "walk_forward" ? "warn" as const
        : "info" as const,
      msg: `[${alphaProgress.symbol}] ${alphaProgress.phase} ${alphaProgress.progress}% - ${alphaProgress.message}`,
    };
    setLogs(prev => [...prev.slice(-99), entry]);
  }, [alphaProgress]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Zap size={20} style={{ color: "#f0b90b" }} />
              <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>Alpha 引擎</h1>
              <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.3)" }}>
                核心系统
              </span>
            </div>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>自动挖掘、验证、推荐量化策略 · 全流程自动化</p>
          </div>
        </div>

        {/* System Health Overview */}
        {health && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <HealthCard label="数据层" status={health.layers.data.status} detail={`${health.layers.data.websocket.connected}/4 实时流`} />
            <HealthCard label="特征层" status={health.layers.features.status} detail={`${health.layers.features.computed}/52 特征 · ${health.layers.features.latencyMs}ms`} />
            <HealthCard label="信号层" status={health.layers.signals.status} detail={`${health.layers.signals.p1Running} P1 + ${health.layers.signals.p2Running} P2`} />
            <HealthCard
              label="执行层"
              status={health.layers.execution.status}
              detail={`成交率 ${(health.layers.execution.fillRate * 100).toFixed(0)}%`}
              warn={health.layers.execution.status === "warning"}
            />
          </div>
        )}

        {/* Engine Control + Run Status */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Global Control Panel */}
          <div className="card-q p-5">
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>引擎控制</h3>
              <button
                onClick={() => setShowParams(!showParams)}
                className="flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors"
                style={{ backgroundColor: showParams ? "rgba(240,185,11,0.15)" : "#2b3139", color: showParams ? "#f0b90b" : "#848e9c", border: `1px solid ${showParams ? "rgba(240,185,11,0.3)" : "#2b3139"}` }}
              >
                <Settings size={12} />
                参数调整
              </button>
            </div>

            {/* Global Status Banner */}
            <div className="mb-5 p-4 rounded-xl flex items-center gap-4" style={{
              backgroundColor: isRunning ? "rgba(14,203,129,0.08)" : "rgba(43,49,57,0.6)",
              border: `1px solid ${isRunning ? "rgba(14,203,129,0.25)" : "#2b3139"}`
            }}>
              <div className="flex-shrink-0">
                {isRunning ? (
                  <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ backgroundColor: "rgba(14,203,129,0.15)" }}>
                    <span className="live-dot" style={{ width: 12, height: 12 }} />
                  </div>
                ) : (
                  <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ backgroundColor: "#2b3139" }}>
                    <Zap size={18} style={{ color: "#848e9c" }} />
                  </div>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold" style={{ color: isRunning ? "#0ecb81" : "#eaecef" }}>
                  {isRunning ? "引擎运行中" : "引擎已停止"}
                </div>
                <div className="text-xs mt-0.5" style={{ color: "#848e9c" }}>
                  {isRunning
                    ? `正在处理 ${globalStatus?.currentPairs?.length ?? 0} 个交易对 · 运行 ${Math.floor((globalStatus?.uptimeSeconds ?? 0) / 60)}分${(globalStatus?.uptimeSeconds ?? 0) % 60}秒`
                    : `第 ${globalStatus?.totalRuns ?? 0} 次运行 · 等待启动`
                  }
                </div>
                {isRunning && globalStatus?.currentPairs && globalStatus.currentPairs.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {globalStatus.currentPairs.map(sym => (
                      <span key={sym} className="text-xs px-1.5 py-0.5 rounded font-mono" style={{ backgroundColor: "rgba(14,203,129,0.1)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.2)" }}>
                        {sym}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Params */}
            {showParams && (
              <div className="mb-5 p-4 rounded-xl space-y-3" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
                <div className="text-xs font-semibold mb-3" style={{ color: "#f0b90b" }}>⚙ 引擎参数微调（下次启动生效）</div>
                <ParamSlider
                  label="IC阈值"
                  value={params.icThreshold}
                  min={0.02} max={0.15} step={0.01}
                  format={v => v.toFixed(2)}
                  onChange={v => setParams(p => ({ ...p, icThreshold: v }))}
                />
                <ParamSlider
                  label="样本外最低胜率"
                  value={params.oosWinRateMin}
                  min={0.50} max={0.80} step={0.01}
                  format={v => `${(v * 100).toFixed(0)}%`}
                  onChange={v => setParams(p => ({ ...p, oosWinRateMin: v }))}
                />
                <ParamSlider
                  label="最大条件数"
                  value={params.maxConditions}
                  min={1} max={5} step={1}
                  format={v => `${v}个`}
                  onChange={v => setParams(p => ({ ...p, maxConditions: v }))}
                />
                <ParamSlider
                  label="回溯天数"
                  value={params.lookbackDays}
                  min={60} max={365} step={30}
                  format={v => `${v}天`}
                  onChange={v => setParams(p => ({ ...p, lookbackDays: v }))}
                />
              </div>
            )}

            {/* Start / Stop Buttons */}
            <div className="flex gap-3">
              <Button
                onClick={() => startGlobal.mutate({ params })}
                disabled={startGlobal.isPending || isRunning}
                className="flex-1 h-11 font-semibold text-sm"
                style={{
                  backgroundColor: isRunning ? "rgba(240,185,11,0.1)" : "#f0b90b",
                  color: isRunning ? "#f0b90b" : "#0b0e11",
                  border: isRunning ? "1px solid rgba(240,185,11,0.3)" : "none",
                  opacity: isRunning ? 0.6 : 1,
                }}
              >
                <Zap size={16} className="mr-2" />
                {startGlobal.isPending ? "启动中..." : isRunning ? "运行中" : "启动 Alpha 引擎"}
              </Button>
              <Button
                onClick={() => stopGlobal.mutate()}
                disabled={stopGlobal.isPending || !isRunning}
                className="flex-1 h-11 font-semibold text-sm"
                style={{
                  backgroundColor: !isRunning ? "rgba(246,70,93,0.05)" : "rgba(246,70,93,0.15)",
                  color: !isRunning ? "#5e6673" : "#f6465d",
                  border: `1px solid ${!isRunning ? "#2b3139" : "rgba(246,70,93,0.3)"}`,
                }}
              >
                <XCircle size={16} className="mr-2" />
                {stopGlobal.isPending ? "停止中..." : "停止引擎"}
              </Button>
            </div>
          </div>

          {/* Run Status */}
          <div className="card-q p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>最新运行状态</h3>
              {latestRun && (
                <span className={`text-xs px-2 py-0.5 rounded ${latestRun.status === "running" ? "badge-pending" : latestRun.status === "completed" ? "badge-active" : "badge-retired"}`}>
                  {latestRun.status === "running" ? "运行中" : latestRun.status === "completed" ? "已完成" : latestRun.status}
                </span>
              )}
            </div>
            {latestRun ? (
              <div className="space-y-3">
                <div className="text-xs" style={{ color: "#848e9c" }}>
                  {latestRun.symbol} · {new Date(latestRun.startedAt!).toUTCString()}
                </div>
                {/* Phase Progress */}
                <div className="space-y-2">
                  {ENGINE_PHASES.map((phase, idx) => {
                    const currentPhaseIdx = ENGINE_PHASES.findIndex(p => p.key === latestRun.phase);
                    const isDone = idx < currentPhaseIdx || latestRun.status === "completed";
                    const isCurrent = idx === currentPhaseIdx && latestRun.status === "running";
                    return (
                      <div key={phase.key} className="flex items-center gap-2">
                        <div className="flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center">
                          {isDone ? (
                            <CheckCircle size={14} style={{ color: "#0ecb81" }} />
                          ) : isCurrent ? (
                            <div className="w-3 h-3 rounded-full live-dot" />
                          ) : (
                            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: "#2b3139" }} />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between">
                            <span className={`text-xs font-medium ${isDone ? "text-profit" : isCurrent ? "" : ""}`} style={{ color: isDone ? "#0ecb81" : isCurrent ? "#eaecef" : "#5e6673" }}>
                              {phase.label}
                            </span>
                            {isCurrent && <span className="text-xs font-num" style={{ color: "#f0b90b" }}>{latestRun.progress}%</span>}
                          </div>
                          {isCurrent && (
                            <div className="progress-q mt-1">
                              <div className="progress-q-fill" style={{ width: `${latestRun.progress}%` }} />
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {latestRun.status === "completed" && (
                  <div className="p-3 rounded-lg" style={{ backgroundColor: "rgba(14,203,129,0.1)", border: "1px solid rgba(14,203,129,0.2)" }}>
                    <div className="text-xs text-profit font-medium">运行完成</div>
                    <div className="text-xs mt-1" style={{ color: "#848e9c" }}>
                      扫描特征: {latestRun.featuresScanned} · 发现候选: {latestRun.candidatesFound}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-8" style={{ color: "#848e9c" }}>
                <Zap size={32} style={{ opacity: 0.2 }} />
                <p className="text-sm mt-2">尚未运行</p>
                <p className="text-xs mt-1">点击“启动 Alpha 引擎”开始挖掘</p>
              </div>
            )}
          </div>
        </div>

        {/* Real-time Engine Log Stream */}
        <div className="card-q overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${isRunning ? "live-dot" : ""}`} style={{ backgroundColor: isRunning ? undefined : "#2b3139" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>引擎实时日志</h3>
            </div>
            <div className="flex items-center gap-2">
              {isRunning && <span className="text-xs" style={{ color: "#0ecb81" }}>实时输出</span>}
              <button
                onClick={() => setLogs([])}
                className="text-xs px-2 py-0.5 rounded transition-colors"
                style={{ backgroundColor: "#2b3139", color: "#848e9c" }}
              >
                清空
              </button>
            </div>
          </div>
          <div
            ref={logRef}
            className="font-mono text-xs p-3 space-y-0.5 overflow-y-auto"
            style={{ backgroundColor: "#0d1117", height: 180, color: "#848e9c" }}
          >
            {logs.length === 0 ? (
              <div className="flex items-center justify-center h-full" style={{ color: "#2b3139" }}>
                等待引擎启动...
              </div>
            ) : (
              logs.map((log, i) => (
                <div key={i} className="flex gap-2">
                  <span style={{ color: "#5e6673", flexShrink: 0 }}>{log.ts}</span>
                  <span style={{ color: log.level === "success" ? "#0ecb81" : log.level === "warn" ? "#f0a500" : log.level === "error" ? "#f6465d" : "#848e9c", flexShrink: 0 }}>
                    [{log.level.toUpperCase()}]
                  </span>
                  <span style={{ color: log.level === "success" ? "#c8f7e4" : log.level === "warn" ? "#fdefc0" : "#848e9c" }}>{log.msg}</span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Pending Candidates */}
        <div className="card-q overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>待确认候选策略</h3>
              {pendingCandidates.length > 0 && (
                <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b" }}>
                  {pendingCandidates.length}
                </span>
              )}
            </div>
            <span className="text-xs" style={{ color: "#848e9c" }}>样本外验证通过 · 等待人工确认</span>
          </div>

          {pendingCandidates.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12" style={{ color: "#848e9c" }}>
              <CheckCircle size={40} style={{ opacity: 0.2 }} />
              <p className="text-sm mt-2">暂无待确认候选</p>
            </div>
          ) : (
            <div className="divide-y" style={{ borderColor: "#1e2329" }}>
              {pendingCandidates.map(c => (
                <div key={c.candidateId}>
                  {/* Candidate Header */}
                  <div
                    className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-[#161a1e] transition-colors"
                    onClick={() => setExpandedCandidateId(expandedCandidateId === c.candidateId ? null : c.candidateId)}
                  >
                    <div className="flex-shrink-0">
                      {expandedCandidateId === c.candidateId ? <ChevronUp size={14} style={{ color: "#848e9c" }} /> : <ChevronDown size={14} style={{ color: "#848e9c" }} />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium font-mono" style={{ color: "#eaecef" }}>{c.candidateId}</span>
                        <span className="text-xs" style={{ color: "#848e9c" }}>{c.symbol}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded ${c.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>
                          {c.direction}
                        </span>
                        {(c as any).mechanismType && (
                          <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: "rgba(240,185,11,0.1)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.2)" }}>
                            {MECHANISM_ZH[(c as any).mechanismType] ?? (c as any).mechanismType}
                          </span>
                        )}
                        {(c as any).causalScore != null && (
                          <span className={`text-xs px-1.5 py-0.5 rounded`} style={{
                            backgroundColor: (c as any).causalScore >= 0.8 ? "rgba(14,203,129,0.1)" : (c as any).causalScore >= 0.5 ? "rgba(240,185,11,0.1)" : "rgba(246,70,93,0.1)",
                            color:           (c as any).causalScore >= 0.8 ? "#0ecb81"               : (c as any).causalScore >= 0.5 ? "#f0b90b"               : "#f6465d",
                          }}>
                            因果{((c as any).causalScore * 100).toFixed(0)}
                          </span>
                        )}
                        {(c as any).causalIssues?.length > 0 && (
                          <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: "rgba(246,70,93,0.1)", color: "#f6465d" }}>⚠ 自动拒绝</span>
                        )}
                      </div>
                      <div className="text-xs font-mono mt-0.5 truncate" style={{ color: "#848e9c" }}>{c.fullExpression}</div>
                    </div>
                    <div className="flex items-center gap-4 flex-shrink-0">
                      <div className="text-center">
                        <div className={`text-sm font-bold font-num ${(c.oosWinRate ?? 0) >= 65 ? "text-profit" : "text-warning-q"}`}>{c.oosWinRate?.toFixed(1)}%</div>
                        <div className="text-xs" style={{ color: "#848e9c" }}>样本外胜率</div>
                      </div>
                      <div className="text-center">
                        <div className="text-sm font-bold font-num text-profit">+{((c.oosAvgReturn ?? 0) * 100).toFixed(3)}%</div>
                        <div className="text-xs" style={{ color: "#848e9c" }}>平均收益</div>
                      </div>
                      <div className="flex gap-1.5" onClick={e => e.stopPropagation()}>
                        <Button
                          size="sm"
                          onClick={() => approveCandidate.mutate({ candidateId: c.candidateId })}
                          disabled={approveCandidate.isPending}
                          className="h-7 px-3 text-xs font-medium"
                          style={{ backgroundColor: "rgba(14,203,129,0.15)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.3)" }}
                        >
                          <ThumbsUp size={11} className="mr-1" />批准
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => rejectCandidate.mutate({ candidateId: c.candidateId })}
                          disabled={rejectCandidate.isPending}
                          className="h-7 px-3 text-xs font-medium"
                          style={{ backgroundColor: "rgba(246,70,93,0.15)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.3)" }}
                        >
                          <ThumbsDown size={11} className="mr-1" />驳回
                        </Button>
                      </div>
                    </div>
                  </div>

                  {/* Expanded Detail */}
                  {expandedCandidateId === c.candidateId && (
                    <div className="px-4 py-4" style={{ backgroundColor: "#161a1e", borderTop: "1px solid #2b3139" }}>
                      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                        {/* Conditions */}
                        <div>
                          <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>策略条件</div>
                          <div className="p-3 rounded-lg font-mono text-xs mb-3" style={{ backgroundColor: "#0b0e11", color: "#f0b90b", border: "1px solid #2b3139" }}>
                            {c.fullExpression}
                          </div>
                          <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>前三出场条件</div>
                          <div className="space-y-1.5">
                            {((c.exitConditionTop3 as any[]) ?? []).map((cond: any, i: number) => (
                              <div key={i} className="flex items-center gap-2 p-2 rounded text-xs" style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139" }}>
                                <span className="w-4 h-4 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0" style={{ backgroundColor: "#2b3139", color: "#f0b90b" }}>{i + 1}</span>
                                <span style={{ color: "#eaecef" }}>{cond.label}</span>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Backtest Chart */}
                        <div>
                          <div className="flex items-center justify-between mb-2">
                            <div className="text-xs font-medium" style={{ color: "#848e9c" }}>回测结果</div>
                            {c.backtestStatus !== "completed" && (
                              <Button
                                size="sm"
                                onClick={() => triggerBacktest.mutate({ candidateId: c.candidateId })}
                                className="h-6 px-2 text-xs"
                                style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.3)" }}
                              >
                                <Play size={10} className="mr-1" />运行回测
                              </Button>
                            )}
                          </div>
                          {c.backtestResult && (c.backtestResult as any).equity_curve ? (
                            <>
                              <ResponsiveContainer width="100%" height={120}>
                                <LineChart data={(c.backtestResult as any).equity_curve.map((v: number, i: number) => ({ i, v }))}>
                                  <XAxis dataKey="i" hide />
                                  <YAxis hide domain={["auto", "auto"]} />
                                  <Tooltip
                                    contentStyle={{ backgroundColor: "#1e2329", border: "1px solid #2b3139", borderRadius: 6, fontSize: 11 }}
                                    formatter={(v: number) => [`${v.toFixed(1)}`, "净值"]}
                                  />
                                  <Line type="monotone" dataKey="v" stroke="#0ecb81" strokeWidth={2} dot={false} />
                                </LineChart>
                              </ResponsiveContainer>
                              <div className="grid grid-cols-3 gap-2 mt-2">
                                <MetricBox label="夏普" value={(c.backtestResult as any).sharpe?.toFixed(2)} color="text-profit" />
                                <MetricBox label="最大回撤" value={`${(c.backtestResult as any).max_drawdown?.toFixed(2)}%`} color="text-loss" />
                                <MetricBox label="样本量" value={`n=${c.sampleSize}`} color="text-neutral-q" />
                              </div>
                            </>
                          ) : (
                            <div className="flex items-center justify-center h-24 text-xs rounded" style={{ backgroundColor: "#0b0e11", color: "#848e9c", border: "1px solid #2b3139" }}>
                              {c.backtestStatus === "running" ? "回测运行中..." : "点击运行回测"}
                            </div>
                          )}
                        </div>

                        {/* Metadata */}
                        <div>
                          <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>策略元数据</div>
                          <div className="space-y-2">
                            {[
                              { label: "IC分数", value: c.icScore?.toFixed(3), color: "text-profit" },
                              { label: "置信度", value: `${c.confidenceScore?.toFixed(1)}%`, color: "text-profit" },
                              { label: "过拟合分数", value: `${((c.overfitScore ?? 0) * 100).toFixed(0)}%`, color: (c.overfitScore ?? 0) < 0.2 ? "text-profit" : "text-warning-q" },
                              { label: "日均触发", value: `~${c.estimatedDailyTriggers?.toFixed(1)}次`, color: "text-neutral-q" },
                              { label: "发现时间", value: c.discoveredAt ? new Date(c.discoveredAt).toUTCString().slice(0, 20) : "-", color: "text-neutral-q" },
                            ].map(m => (
                              <div key={m.label} className="flex items-center justify-between py-1.5 px-2 rounded" style={{ backgroundColor: "#0b0e11" }}>
                                <span className="text-xs" style={{ color: "#848e9c" }}>{m.label}</span>
                                <span className={`text-xs font-num font-medium ${m.color}`}>{m.value}</span>
                              </div>
                            ))}
                          </div>
                          {(c as any).causalExplanation && (
                            <div className="mt-3">
                              <div className="text-xs font-medium mb-1" style={{ color: "#848e9c" }}>因果解释</div>
                              <div className="p-2 rounded text-xs whitespace-pre-wrap" style={{ backgroundColor: "#0b0e11", color: "#b7bdc6", border: "1px solid #2b3139", lineHeight: "1.6" }}>
                                {(c as any).causalExplanation}
                              </div>
                            </div>
                          )}
                          {(c as any).causalIssues?.length > 0 && (
                            <div className="mt-3">
                              <div className="text-xs font-medium mb-1" style={{ color: "#f6465d" }}>拒绝原因</div>
                              <div className="space-y-1">
                                {((c as any).causalIssues as string[]).map((issue, i) => (
                                  <div key={i} className="p-2 rounded text-xs" style={{ backgroundColor: "rgba(246,70,93,0.07)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.2)" }}>
                                    {issue}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          {(c as any).causalWarnings?.length > 0 && (
                            <div className="mt-3">
                              <div className="text-xs font-medium mb-1" style={{ color: "#f0b90b" }}>注意事项</div>
                              <div className="space-y-1">
                                {((c as any).causalWarnings as string[]).map((w, i) => (
                                  <div key={i} className="p-2 rounded text-xs" style={{ backgroundColor: "rgba(240,185,11,0.07)", color: "#f0b90b", border: "1px solid rgba(240,185,11,0.2)" }}>
                                    {w}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── LLM 物理验证引擎 ───────────────────────────────────────────────── */}
        <div className="card-q overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
            <div className="flex items-center gap-2">
              <Brain size={14} style={{ color: "#bc8cff" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>LLM 物理验证引擎</h3>
              <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ backgroundColor: "rgba(188,140,255,0.12)", color: "#bc8cff", border: "1px solid rgba(188,140,255,0.3)" }}>
                kimi-k2.5
              </span>
              {llmState && (
                <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded ${llmState.status === "running" ? "badge-pending" : llmState.status === "error" ? "badge-retired" : "badge-active"}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${llmState.status === "running" ? "live-dot" : ""}`}
                    style={{ backgroundColor: llmState.status === "running" ? undefined : llmState.status === "error" ? "#f6465d" : "#0ecb81" }} />
                  {llmState.status === "running" ? "验证中" : llmState.status === "error" ? "错误" : "待机"}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button onClick={() => { refetchLLMState(); refetchReview(); }} className="text-xs px-2 py-0.5 rounded transition-colors" style={{ backgroundColor: "#2b3139", color: "#848e9c" }}>
                <RefreshCw size={10} className="inline mr-1" />刷新
              </button>
              <button onClick={() => setShowLLMConfig(!showLLMConfig)} className="flex items-center gap-1 text-xs px-2 py-0.5 rounded transition-colors"
                style={{ backgroundColor: showLLMConfig ? "rgba(188,140,255,0.15)" : "#2b3139", color: showLLMConfig ? "#bc8cff" : "#848e9c" }}>
                <Settings size={10} />配置
              </button>
            </div>
          </div>

          {/* LLM Config Panel */}
          {showLLMConfig && (
            <div className="px-4 py-4" style={{ backgroundColor: "#161a1e", borderBottom: "1px solid #2b3139" }}>
              <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                <div>
                  <div className="text-xs mb-1" style={{ color: "#848e9c" }}>接口密钥</div>
                  <input
                    type="password"
                    value={llmApiKey}
                    onChange={e => setLlmApiKey(e.target.value)}
                    placeholder={llmState?.llm_config?.api_key_hint ?? "sk-..."}
                    className="w-full px-3 py-1.5 rounded text-xs font-mono"
                    style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139", color: "#eaecef", outline: "none" }}
                  />
                </div>
                <div>
                  <div className="text-xs mb-1" style={{ color: "#848e9c" }}>模型</div>
                  <input
                    value={llmModel}
                    onChange={e => setLlmModel(e.target.value)}
                    className="w-full px-3 py-1.5 rounded text-xs"
                    style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139", color: "#eaecef", outline: "none" }}
                  />
                </div>
                <div>
                  <div className="text-xs mb-1" style={{ color: "#848e9c" }}>Base URL</div>
                  <input
                    value={llmBaseUrl}
                    onChange={e => setLlmBaseUrl(e.target.value)}
                    className="w-full px-3 py-1.5 rounded text-xs font-mono"
                    style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139", color: "#eaecef", outline: "none" }}
                  />
                </div>
                <div>
                  <div className="text-xs mb-1" style={{ color: "#848e9c" }}>自动批准阈值 / 审查阈值</div>
                  <div className="flex gap-2">
                    <input type="number" min={0} max={1} step={0.01} value={autoApproveThr}
                      onChange={e => setAutoApproveThr(parseFloat(e.target.value))}
                      className="w-20 px-2 py-1.5 rounded text-xs"
                      style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139", color: "#0ecb81", outline: "none" }}
                    />
                    <input type="number" min={0} max={1} step={0.01} value={reviewThr}
                      onChange={e => setReviewThr(parseFloat(e.target.value))}
                      className="w-20 px-2 py-1.5 rounded text-xs"
                      style={{ backgroundColor: "#0b0e11", border: "1px solid #2b3139", color: "#f0b90b", outline: "none" }}
                    />
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 mt-3">
                <Button size="sm" onClick={() => saveLLMConfig.mutate({ apiKey: llmApiKey || undefined, model: llmModel || undefined, baseUrl: llmBaseUrl || undefined, autoApprove: autoApproveThr, reviewQueue: reviewThr })}
                  disabled={saveLLMConfig.isPending}
                  className="h-7 px-3 text-xs"
                  style={{ backgroundColor: "rgba(188,140,255,0.15)", color: "#bc8cff", border: "1px solid rgba(188,140,255,0.3)" }}>
                  保存配置
                </Button>
                <span className="text-xs" style={{ color: "#848e9c" }}>
                  当前模型: <span style={{ color: "#bc8cff" }}>{llmState?.llm_config?.model ?? "—"}</span>
                  &nbsp;·&nbsp;密钥: <span style={{ color: "#848e9c" }}>{llmState?.llm_config?.api_key_hint ?? "—"}</span>
                </span>
              </div>
            </div>
          )}

          {/* Engine Stats */}
          {llmState?.stats && (
            <div className="grid grid-cols-4 divide-x" style={{ borderBottom: "1px solid #2b3139", borderColor: "#2b3139" }}>
              {[
                { label: "待验证", value: llmState.stats.pending_count ?? 0, color: "#f0b90b" },
                { label: "审查队列", value: llmState.stats.review_count ?? 0, color: "#58a6ff" },
                { label: "已批准", value: llmState.stats.approved_count ?? 0, color: "#0ecb81" },
                { label: "本次会话批准", value: llmState.stats.total_approved_this_session ?? 0, color: "#bc8cff" },
              ].map(m => (
                <div key={m.label} className="py-3 text-center" style={{ borderColor: "#2b3139" }}>
                  <div className="text-lg font-bold font-num" style={{ color: m.color }}>{m.value}</div>
                  <div className="text-xs" style={{ color: "#848e9c" }}>{m.label}</div>
                </div>
              ))}
            </div>
          )}

          {/* Review Queue */}
          {reviewQueue && reviewQueue.length > 0 ? (
            <div>
              <div className="px-4 py-2 flex items-center gap-2" style={{ borderBottom: "1px solid #1e2329" }}>
                <AlertCircle size={12} style={{ color: "#f0b90b" }} />
                <span className="text-xs font-medium" style={{ color: "#f0b90b" }}>人工审查队列：智能审查置信度在 70% 到 92% 之间，请确认物理逻辑后再决定</span>
                <span className="text-xs px-1.5 py-0.5 rounded-full ml-auto" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b" }}>{reviewQueue.length}</span>
              </div>
              <div className="divide-y" style={{ borderColor: "#1e2329" }}>
                {reviewQueue.map(c => {
                  const llmR = c.llmResult as any;
                  const conf = llmR?.confidence ?? 0;
                  const confColor = conf >= 0.92 ? "#0ecb81" : conf >= 0.70 ? "#f0b90b" : "#f6465d";
                  const isExpanded = expandedReviewId === c.candidateId;
                  return (
                    <div key={c.candidateId}>
                      <div className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-[#161a1e] transition-colors"
                        onClick={() => setExpandedReviewId(isExpanded ? null : c.candidateId)}>
                        {isExpanded ? <ChevronUp size={13} style={{ color: "#848e9c" }} /> : <ChevronDown size={13} style={{ color: "#848e9c" }} />}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-xs font-mono font-medium" style={{ color: "#eaecef" }}>{c.fullExpression}</span>
                            <span className={`text-xs px-1.5 py-0.5 rounded ${c.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>{c.direction === "LONG" ? "\u505a\u591a" : "\u505a\u7a7a"}</span>
                            {llmR?.mechanism_display_name && (
                              <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: "rgba(188,140,255,0.1)", color: "#bc8cff" }}>{llmR.mechanism_display_name}</span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-4 flex-shrink-0">
                          <div className="text-center">
                            <div className="text-sm font-bold font-num text-profit">{c.oosWinRate?.toFixed(1)}%</div>
                            <div className="text-xs" style={{ color: "#848e9c" }}>样本外胜率</div>
                          </div>
                          <div className="text-center">
                            <div className="text-sm font-bold font-num" style={{ color: confColor }}>{(conf * 100).toFixed(0)}%</div>
                            <div className="text-xs" style={{ color: "#848e9c" }}>LLM置信</div>
                          </div>
                          <div className="flex gap-1.5" onClick={e => e.stopPropagation()}>
                            <Button size="sm" onClick={() => promoterApprove.mutate({ candidateId: c.candidateId })} disabled={promoterApprove.isPending}
                              className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(14,203,129,0.15)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.3)" }}>
                              <ThumbsUp size={10} className="mr-1" />批准
                            </Button>
                            <Button size="sm" onClick={() => promoterReject.mutate({ candidateId: c.candidateId })} disabled={promoterReject.isPending}
                              className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(246,70,93,0.15)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.3)" }}>
                              <XCircle size={10} className="mr-1" />拒绝
                            </Button>
                          </div>
                        </div>
                      </div>

                      {/* Expanded LLM reasoning */}
                      {isExpanded && llmR && (
                        <div className="px-4 py-4" style={{ backgroundColor: "#0b0e11", borderTop: "1px solid #1e2329" }}>
                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            <div className="space-y-2">
                              {llmR.physics?.essence && <LLMField label="力的本质" value={llmR.physics.essence} />}
                              {llmR.physics?.why_temporary && <LLMField label="为何短暂" value={llmR.physics.why_temporary} />}
                              {llmR.entry_narrative && <LLMField label="入场叙事" value={llmR.entry_narrative} />}
                            </div>
                            <div className="space-y-2">
                              {llmR.primary_decay?.narrative && <LLMField label="力的衰竭" value={llmR.primary_decay.narrative} color="#f0b90b" />}
                              {llmR.primary_decay?.condition && <LLMField label="衰竭条件" value={llmR.primary_decay.condition} mono />}
                              {llmR.rejection_reason && <LLMField label="疑虑" value={llmR.rejection_reason} color="#f6465d" />}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-8" style={{ color: "#848e9c" }}>
              <Brain size={32} style={{ opacity: 0.15, color: "#bc8cff" }} />
              <p className="text-sm mt-2">审查队列为空</p>
              <p className="text-xs mt-1" style={{ color: "#5e6673" }}>智能审查通过率大于等于 92% 时直接批准，小于 70% 时自动拒绝</p>
            </div>
          )}

          {/* Recent Decisions */}
          {llmState?.recent_decisions && llmState.recent_decisions.length > 0 && (
            <div style={{ borderTop: "1px solid #2b3139" }}>
              <div className="px-4 py-2 flex items-center gap-2">
                <RotateCcw size={11} style={{ color: "#848e9c" }} />
                <span className="text-xs" style={{ color: "#848e9c" }}>最近决策历史</span>
              </div>
              <div className="px-4 pb-3 space-y-1">
                {[...llmState.recent_decisions].reverse().slice(0, 8).map((d, i) => {
                  const decColor = d.decision === "AUTO_APPROVED" || d.decision === "HUMAN_APPROVED" ? "#0ecb81"
                    : d.decision === "REVIEW_QUEUE" ? "#f0b90b" : "#f6465d";
                  const decLabel = { AUTO_APPROVED: "自动批准", REVIEW_QUEUE: "进审查", AUTO_REJECTED: "自动拒绝", HUMAN_APPROVED: "人工批准", HUMAN_REJECTED: "人工拒绝" }[d.decision] ?? d.decision;
                  return (
                    <div key={i} className="flex items-center gap-3 text-xs py-1" style={{ borderBottom: "1px solid #1e2329" }}>
                      <span className="min-w-[72px] text-center text-xs px-1.5 py-0.5 rounded font-medium" style={{ backgroundColor: `${decColor}18`, color: decColor }}>{decLabel}</span>
                      <span className="flex-1 truncate font-mono" style={{ color: "#848e9c" }}>{d.rule_str}</span>
                      {d.confidence != null && <span style={{ color: decColor, minWidth: 38, textAlign: "right" }}>{(d.confidence * 100).toFixed(0)}%</span>}
                      <span style={{ color: "#3d444d", minWidth: 50, textAlign: "right" }}>{d.decided_at ? new Date(d.decided_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : ""}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Force Library Summary */}
          {llmState?.force_library_summary && llmState.force_library_summary.length > 0 && (
            <div style={{ borderTop: "1px solid #2b3139" }}>
              <div className="px-4 py-2 flex items-center gap-2">
                <BookOpen size={11} style={{ color: "#848e9c" }} />
                <span className="text-xs" style={{ color: "#848e9c" }}>力库 · {llmState.force_library_summary.length} 个已验证机制</span>
              </div>
              <div className="px-4 pb-4 grid grid-cols-2 lg:grid-cols-3 gap-2">
                {llmState.force_library_summary.map(m => (
                  <div key={m.mechanism_type} className="p-2 rounded" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
                    <div className="text-xs font-medium" style={{ color: "#ffa657" }}>{m.display_name}</div>
                    <div className="text-xs mt-0.5" style={{ color: "#5e6673" }}>{m.category_name}</div>
                    {m.validated_by?.length > 0 && (
                      <div className="text-xs mt-1" style={{ color: "#0ecb81" }}>实证: {m.validated_by.join(", ")}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Microstructure Analysis Panel */}
        <div className="card-q p-5">
          <div className="flex items-center gap-2 mb-4">
            <Activity size={14} style={{ color: "#848e9c" }} />
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>微观结构分析</h3>
            <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "rgba(240,185,11,0.1)", color: "#f0b90b" }}>实时</span>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <MicroCard label="资金费率" value="0.0100%" sub="每8h" trend="+" />
            <MicroCard label="清算金额" value="$2.4M" sub="24h内" trend="+" />
            <MicroCard label="成交量" value="18,432" sub="BTC/1h" trend="+" />
            <MicroCard label="弹均比" value="1.24" sub="弹均比例" trend="-" />
          </div>
          <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-3">
            <div className="p-3 rounded-lg" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>订单流分析</div>
              <div className="space-y-1.5">
                {[{label:"主动买入",pct:58,color:"#0ecb81"},{label:"主动卖出",pct:42,color:"#f6465d"}].map(item => (
                  <div key={item.label}>
                    <div className="flex justify-between text-xs mb-1">
                      <span style={{ color: "#848e9c" }}>{item.label}</span>
                      <span style={{ color: item.color }}>{item.pct}%</span>
                    </div>
                    <div className="progress-q"><div className="progress-q-fill" style={{ width: `${item.pct}%`, backgroundColor: item.color }} /></div>
                  </div>
                ))}
              </div>
            </div>
            <div className="p-3 rounded-lg" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>资金费率趋势</div>
              <div className="space-y-1">
                {[{t:"08:00",v:"0.0100%"},{t:"16:00",v:"0.0100%"},{t:"00:00",v:"-0.0050%"}].map(r => (
                  <div key={r.t} className="flex justify-between text-xs">
                    <span style={{ color: "#848e9c" }}>UTC {r.t}</span>
                    <span className={r.v.startsWith("-") ? "text-loss" : "text-profit"}>{r.v}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="p-3 rounded-lg" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>大额清算监控</div>
              <div className="space-y-1">
                {[{sym:"BTCUSDT",val:"$12.4M",dir:"LONG"},{sym:"ETHUSDT",val:"$3.2M",dir:"SHORT"},{sym:"SOLUSDT",val:"$0.8M",dir:"LONG"}].map(l => (
                  <div key={l.sym} className="flex justify-between text-xs">
                    <span style={{ color: "#848e9c" }}>{l.sym}</span>
                    <span className={l.dir === "LONG" ? "text-profit" : "text-loss"}>{l.val} {l.dir}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Approved Strategies Timeline */}
        {approvedCandidates.length > 0 && (
          <div className="card-q overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
              <div className="flex items-center gap-2">
                <CheckCircle size={14} style={{ color: "#0ecb81" }} />
                <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>已批准策略时间线</h3>
              </div>
              <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "rgba(14,203,129,0.1)", color: "#0ecb81" }}>{approvedCandidates.length} 个活跃</span>
            </div>
            <div className="p-4 space-y-3">
              {approvedCandidates.map((c, i) => (
                <div key={c.candidateId} className="flex items-start gap-3">
                  <div className="flex flex-col items-center">
                    <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: "#0ecb81", marginTop: 2 }} />
                    {i < approvedCandidates.length - 1 && <div className="w-px flex-1 mt-1" style={{ backgroundColor: "#2b3139", minHeight: 24 }} />}
                  </div>
                  <div className="flex-1 min-w-0 pb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium font-mono" style={{ color: "#eaecef" }}>{c.candidateId}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${c.direction === "LONG" ? "text-profit bg-profit-subtle" : "text-loss bg-loss-subtle"}`}>{c.direction === "LONG" ? "\u505a\u591a" : "\u505a\u7a7a"}</span>
                      <span className="text-xs" style={{ color: "#848e9c" }}>{c.symbol}</span>
                    </div>
                    <div className="flex items-center gap-4 mt-1">
                      <span className="text-xs font-num text-profit">{c.oosWinRate?.toFixed(1)}% 样本外胜率</span>
                      <span className="text-xs" style={{ color: "#848e9c" }}>IC={c.icScore?.toFixed(3)}</span>
                      <span className="text-xs" style={{ color: "#5e6673" }}>{c.discoveredAt ? new Date(c.discoveredAt).toUTCString().slice(0, 20) : ""}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Run History */}
        {runs && runs.length > 0 && (
          <div className="card-q overflow-hidden">
            <div className="px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>运行历史</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr style={{ borderBottom: "1px solid #2b3139" }}>
                    {["运行ID", "交易对", "状态", "进度", "特征扫描", "发现候选", "开始时间"].map(h => (
                      <th key={h} className="px-4 py-2.5 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {runs.map(r => (
                    <tr key={r.runId} style={{ borderBottom: "1px solid #1e2329" }}>
                      <td className="px-4 py-2.5 text-xs font-mono" style={{ color: "#848e9c" }}>{r.runId}</td>
                      <td className="px-4 py-2.5 text-sm font-medium" style={{ color: "#eaecef" }}>{r.symbol}</td>
                      <td className="px-4 py-2.5">
                        <span className={`badge-${r.status === "completed" ? "active" : r.status === "running" ? "pending" : "retired"}`}>
                          {r.status === "completed" ? "完成" : r.status === "running" ? "运行中" : r.status}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <div className="progress-q w-20">
                            <div className="progress-q-fill" style={{ width: `${r.progress ?? 0}%` }} />
                          </div>
                          <span className="text-xs font-num" style={{ color: "#848e9c" }}>{r.progress}%</span>
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-sm font-num" style={{ color: "#eaecef" }}>{r.featuresScanned ?? 0}</td>
                      <td className="px-4 py-2.5 text-sm font-num" style={{ color: r.candidatesFound ? "#0ecb81" : "#848e9c" }}>{r.candidatesFound ?? 0}</td>
                      <td className="px-4 py-2.5 text-xs font-num" style={{ color: "#848e9c" }}>
                        {r.startedAt ? new Date(r.startedAt).toUTCString().slice(0, 20) : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </QuantLayout>
  );
}

function MicroCard({ label, value, sub, trend }: { label: string; value: string; sub: string; trend: "+" | "-" }) {
  return (
    <div className="p-3 rounded-lg" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className={`text-lg font-bold font-num ${trend === "+" ? "text-profit" : "text-loss"}`}>{value}</div>
      <div className="text-xs" style={{ color: "#5e6673" }}>{sub}</div>
    </div>
  );
}

function HealthCard({ label, status, detail, warn }: { label: string; status: string; detail: string; warn?: boolean }) {
  const ok = status === "healthy";
  return (
    <div className="card-q p-3">
      <div className="flex items-center gap-2 mb-1">
        <span className={ok ? (warn ? "live-dot-warning" : "live-dot") : "live-dot-error"} />
        <span className="text-xs font-medium" style={{ color: "#eaecef" }}>{label}</span>
      </div>
      <div className="text-xs" style={{ color: warn ? "#f0a500" : ok ? "#848e9c" : "#f6465d" }}>{detail}</div>
    </div>
  );
}

function MetricBox({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="p-2 rounded text-center" style={{ backgroundColor: "#0b0e11" }}>
      <div className={`text-sm font-num font-medium ${color}`}>{value}</div>
      <div className="text-xs" style={{ color: "#848e9c" }}>{label}</div>
    </div>
  );
}

function LLMField({ label, value, color, mono }: { label: string; value: string; color?: string; mono?: boolean }) {
  return (
    <div className="p-2 rounded" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className={`text-xs leading-relaxed ${mono ? "font-mono" : ""}`} style={{ color: color ?? "#b7bdc6" }}>{value}</div>
    </div>
  );
}

function ParamSlider({ label, value, min, max, step, format, onChange }: {
  label: string; value: number; min: number; max: number; step: number;
  format: (v: number) => string; onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs" style={{ color: "#848e9c" }}>{label}</span>
        <span className="text-xs font-num" style={{ color: "#f0b90b" }}>{format(value)}</span>
      </div>
      <Slider
        value={[value]}
        min={min} max={max} step={step}
        onValueChange={([v]) => onChange(v)}
        className="w-full"
      />
    </div>
  );
}
