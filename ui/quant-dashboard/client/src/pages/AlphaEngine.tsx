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
      toast.success("已从复核队列移出");
      refetchReview();
    },
  });

  // -- 新增: 心跳 + 大模型配置 + 发现日志 --
  const { data: heartbeat } = trpc.alphaEngine.getHeartbeat.useQuery(undefined, { refetchInterval: 15_000 });
  const { data: llmConfig } = trpc.alphaEngine.getLLMConfig.useQuery(undefined, { refetchInterval: 60_000 });
  const { data: discoveryLogLines = [] } = trpc.alphaEngine.getDiscoveryLog.useQuery({ lines: 30 }, { refetchInterval: 30_000 });

  const candidates = rawCandidates as CandidateRow[];
  const reviewQueue = rawReviewQueue as ReviewRow[];
  const pendingCandidates = candidates.filter((item) => item.status === "pending");
  const approvedCandidates = candidates.filter((item) => item.status === "approved");
  const latestRun = runs[0];
  const isRunning = globalStatus?.status === "running";

  // 心跳状态: Python端每60秒写一次heartbeat（含休眠期），超过120秒无心跳=异常
  const hbAge = heartbeat?.ageSeconds ?? 99999;
  const hbColor = hbAge < 120 ? "#0ecb81" : hbAge < 300 ? "#f0b90b" : "#f6465d";
  const hbLabel = hbAge < 120 ? "引擎心跳正常" : hbAge < 300 ? "引擎响应慢" : "引擎离线";

  // 今日候选 (UTC+8)
  const todayStr = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const todayCandidates = candidates.filter((c) => {
    const disc = (c as any).discoveredAt;
    if (!disc) return false;
    const d = new Date(disc);
    const utc8 = new Date(d.getTime() + 8 * 3600000);
    return utc8.toISOString().slice(0, 10) === todayStr;
  });

  useEffect(() => {
    if (!alphaProgress) return;
    setLogs((current) => {
      const level: "info" | "success" | "warn" =
        alphaProgress.phase === "completed" ? "success" : alphaProgress.phase === "walk_forward" ? "warn" : "info";
      const next = [
        ...current.slice(-79),
        {
          ts: new Date().toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }),
          message: `[${alphaProgress.symbol}] ${formatProgressPhase(alphaProgress.phase)} ${alphaProgress.progress}% - ${translateVisibleText(alphaProgress.message)}`,
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
              <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>{"阿尔法引擎"}</h1>
            </div>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
              {"统一展示发现状态、候选规则、复核队列和运行记录。"}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg" style={{ backgroundColor: "#1e2329", color: hbColor }}>
              <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: hbColor, boxShadow: hbAge < 120 ? `0 0 6px ${hbColor}` : "none", animation: hbAge < 120 ? "pulse 2s infinite" : "none" }} />
              <span>{hbLabel}</span>
              {llmConfig && <span style={{ color: "#848e9c", marginLeft: 4 }}>{`| 大模型：${llmConfig.model}`}</span>}
            </div>
            <div className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg" style={{ backgroundColor: "#1e2329", color: connected ? "#0ecb81" : "#848e9c" }}>
              {connected ? <Activity size={14} /> : <Clock3 size={14} />}
              <span>{connected ? "实时连接已建立" : "实时连接重连中"}</span>
            </div>
          </div>
        </div>

        {health && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <HealthCard label={"数据层"} status={health.layers.data.status} detail={`${health.layers.data.websocket.connected}/4 实时流`} />
            <HealthCard label={"特征层"} status={health.layers.features.status} detail={`${health.layers.features.computed}/52 特征`} />
            <HealthCard label={"信号层"} status={health.layers.signals.status} detail={`${health.layers.signals.p1Running} P1 + ${health.layers.signals.p2Running} P2`} />
            <HealthCard label={"执行层"} status={health.layers.execution.status} detail={`成交率 ${(health.layers.execution.fillRate * 100).toFixed(0)}%`} />
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
              <ParamField label="相关性阈值" value={params.icThreshold} step={0.01} onChange={(value) => setParams((current) => ({ ...current, icThreshold: value }))} />
              <ParamField label="样本外最低胜率" value={params.oosWinRateMin} step={0.01} onChange={(value) => setParams((current) => ({ ...current, oosWinRateMin: value }))} />
              <ParamField label="最多条件数" value={params.maxConditions} step={1} onChange={(value) => setParams((current) => ({ ...current, maxConditions: Math.max(1, Math.round(value)) }))} />
              <ParamField label="回看天数" value={params.lookbackDays} step={1} onChange={(value) => setParams((current) => ({ ...current, lookbackDays: Math.max(30, Math.round(value)) }))} />
            </div>

            <div className="rounded-xl p-4" style={{ backgroundColor: "#161a1e", border: "1px solid #2b3139" }}>
              <div className="text-xs font-medium mb-2" style={{ color: "#848e9c" }}>{"运行摘要"}</div>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
                <SummaryCell label={"引擎状态"} value={isRunning ? "运行中" : "已停止"} valueColor={isRunning ? "#0ecb81" : "#848e9c"} />
                <SummaryCell label={"总运行次数"} value={String(globalStatus?.totalRuns ?? 0)} />
                <SummaryCell label={"最新运行编号"} value={latestRun?.runId ?? "--"} />
                <SummaryCell label={"最新候选"} value={String(pendingCandidates.length)} />
              </div>
            </div>
          </div>

          <div className="card-q p-5 space-y-3">
            <div className="flex items-center gap-2">
              <Brain size={15} style={{ color: "#f0b90b" }} />
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"复核队列"}</h3>
            </div>
            <SummaryCell label={"待复核"} value={String(reviewQueue.length)} />
            <SummaryCell label={"已批准候选"} value={String(approvedCandidates.length)} />
            <SummaryCell label={"当前连接"} value={connected ? "实时连接正常" : "实时连接重连中"} valueColor={connected ? "#0ecb81" : "#848e9c"} />
            {llmConfig && (
              <div className="rounded-lg p-3 text-xs leading-6" style={{ backgroundColor: "#161a1e" }}>
                <div style={{ color: "#bc8cff" }}>{"大模型审核引擎"}</div>
                <div style={{ color: "#eaecef" }}>{`模型: ${llmConfig.model}`}</div>
                <div style={{ color: "#848e9c" }}>{`接口: ${llmConfig.baseUrl || "--"}`}</div>
                <div style={{ color: "#848e9c" }}>{`密钥: ${llmConfig.apiKeyMasked}`}</div>
              </div>
            )}
          </div>
        </div>

        <div className="card-q overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"今日发现"}</h3>
            <span className="text-xs" style={{ color: "#848e9c" }}>{`${todayStr} (UTC+8) | ${todayCandidates.length} 条`}</span>
          </div>
          {todayCandidates.length === 0 ? (
            <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"今日暂无新发现，引擎每小时自动扫描，新候选将在此出现。"}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr style={{ borderBottom: "1px solid #1e2329" }}>
                    {["规则", "方向", "样本外胜率", "样本数", "大模型评估", "状态", "操作"].map((heading) => (
                      <th key={heading} className="px-4 py-2.5 text-left text-xs font-medium" style={{ color: "#848e9c" }}>{heading}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {todayCandidates.map((candidate, index) => {
                    const llmR = (candidate as any).llmResult as any;
                    const statusColor = candidate.status === "approved" ? "#0ecb81" : candidate.status === "rejected" ? "#f6465d" : "#f0b90b";
                    const statusLabel = candidate.status === "approved" ? "已批准" : candidate.status === "rejected" ? "已驳回" : "待审";
                    return (
                    <tr key={candidate.candidateId} style={{ borderBottom: index < todayCandidates.length - 1 ? "1px solid #1e2329" : "none" }}>
                      <td className="px-4 py-3">
                        <div className="text-xs" style={{ color: "#f0b90b" }}>{formatRuleExpression(candidate.fullExpression)}</div>
                      </td>
                      <td className="px-4 py-3 text-sm" style={{ color: candidate.direction === "LONG" ? "#0ecb81" : "#f6465d" }}>{candidate.direction === "LONG" ? "做多" : "做空"}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: scoreColor(candidate.oosWinRate) }}>{formatPercent(candidate.oosWinRate)}</td>
                      <td className="px-4 py-3 text-sm font-num" style={{ color: "#eaecef" }}>{String(candidate.sampleSize ?? 0)}</td>
                      <td className="px-4 py-3">
                        {llmR ? (
                          <div>
                            <div className="text-xs" style={{ color: "#bc8cff" }}>{formatMechanismType(llmR.mechanism_display_name ?? llmR.mechanism_type)}</div>
                            {!llmR.is_valid && llmR.rejection_reason && (
                              <div className="text-xs mt-0.5" style={{ color: "#f6465d" }}>{llmR.rejection_reason}</div>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs" style={{ color: "#848e9c" }}>{"--"}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: `${statusColor}20`, color: statusColor }}>{statusLabel}</span>
                      </td>
                      <td className="px-4 py-3">
                        {candidate.status === "pending" && (
                        <div className="flex items-center gap-1.5">
                          <Button size="sm" onClick={() => approveCandidate.mutate({ candidateId: candidate.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(14,203,129,0.15)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.3)" }}>
                            <CheckCircle2 size={11} className="mr-1" />
                            {"批准"}
                          </Button>
                          <Button size="sm" onClick={() => rejectCandidate.mutate({ candidateId: candidate.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(246,70,93,0.12)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.25)" }}>
                            {"驳回"}
                          </Button>
                        </div>
                        )}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <div className="card-q overflow-hidden">
            <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #2b3139" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>{"复核队列"}</h3>
              <span className="text-xs" style={{ color: "#848e9c" }}>{`${reviewQueue.length} 条`}</span>
            </div>
            {reviewQueue.length === 0 ? (
              <div className="px-4 py-6 text-sm" style={{ color: "#848e9c" }}>{"暂无复核队列条目。"}</div>
            ) : (
              <div className="divide-y" style={{ borderColor: "#1e2329" }}>
                {reviewQueue.map((item) => (
                  <div key={item.candidateId} className="px-4 py-3 flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm" style={{ color: "#eaecef" }}>{formatRuleExpression(item.fullExpression ?? item.candidateId)}</div>
                      <div className="text-xs mt-1" style={{ color: "#848e9c" }}>
                        {`${formatDirection(item.direction)} / ${formatMechanismType(item.mechanismType)} / ${formatPercent(item.oosWinRate)}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Button size="sm" onClick={() => promoterApprove.mutate({ candidateId: item.candidateId })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(14,203,129,0.15)", color: "#0ecb81", border: "1px solid rgba(14,203,129,0.3)" }}>
                        {"写入"}
                      </Button>
                      <Button size="sm" onClick={() => promoterReject.mutate({ candidateId: item.candidateId, reason: "人工复核移出" })} className="h-7 px-2 text-xs" style={{ backgroundColor: "rgba(246,70,93,0.12)", color: "#f6465d", border: "1px solid rgba(246,70,93,0.25)" }}>
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
              {logs.length === 0 && discoveryLogLines.length === 0 ? (
                <div className="text-sm" style={{ color: "#848e9c" }}>{"等待引擎运行..."}</div>
              ) : (
                [...(discoveryLogLines as string[]).map((line, i) => ({
                  ts: line.slice(0, 8),
                  message: translateVisibleText(line.slice(10)),
                  level: (line.includes("ERROR") || line.includes("WARNING") ? "warn" : line.includes("合格") ? "success" : "info") as "info" | "success" | "warn",
                  key: `dl-${i}`,
                })), ...logs.map((l, i) => ({ ...l, key: `ws-${i}` }))].slice(-50).map((log) => (
                  <div key={(log as any).key ?? log.ts} className="rounded-lg p-3 text-xs font-mono" style={{ backgroundColor: "#161a1e", color: log.level === "success" ? "#0ecb81" : log.level === "warn" ? "#f0b90b" : "#c9d1d9" }}>
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
          {formatHealthStatus(status)}
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

function formatHealthStatus(status: string) {
  const map: Record<string, string> = {
    healthy: "正常",
    warning: "预警",
    error: "异常",
    offline: "离线",
    running: "运行中",
    idle: "待机",
  };
  return map[status] ?? translateVisibleText(status);
}

function formatProgressPhase(phase?: string | null) {
  const map: Record<string, string> = {
    loading: "加载数据",
    feature_engineering: "计算特征",
    scanning: "扫描信号",
    scan: "扫描信号",
    seed_mining: "挖掘种子",
    walk_forward: "滚动验证",
    validation: "验证候选",
    promotion: "审核晋升",
    completed: "已完成",
    error: "异常",
  };
  return map[String(phase ?? "")] ?? translateVisibleText(phase);
}

function formatMechanismType(value?: string | null) {
  const map: Record<string, string> = {
    compression_release: "压缩释放",
    vwap_reversion: "均价回归",
    seller_impulse: "卖压冲击",
    buyer_impulse: "买方冲击",
    liquidation_cascade: "清算级联",
    funding_divergence: "资金费率背离",
    oi_divergence: "持仓量背离",
    generic: "通用机制",
    generic_alpha: "通用阿尔法",
  };
  if (!value) return "未识别机制";
  return map[value] ?? translateVisibleText(value);
}

function formatRuleExpression(value?: string | null) {
  if (!value) return "未配置规则";
  const replacements: Array<[RegExp, string]> = [
    [/price_compression_blocks_5m/g, "五分钟价格压缩块数"],
    [/price_compression_blocks_10m/g, "十分钟价格压缩块数"],
    [/vol_drought_blocks_5m/g, "五分钟量能枯竭块数"],
    [/vol_drought_blocks_10m/g, "十分钟量能枯竭块数"],
    [/spread_vs_ma20/g, "价差相对二十均值"],
    [/volume_acceleration/g, "成交量加速度"],
    [/vwap_deviation/g, "成交均价偏离"],
    [/taker_buy_sell_ratio/g, "主动买卖比"],
    [/volume_vs_ma20/g, "成交量相对二十均值"],
    [/btc_liq_net_pressure/g, "清算净压力"],
    [/direction_net_1m/g, "一分钟主动方向净值"],
    [/large_trade_buy_ratio/g, "大额成交买入占比"],
    [/trade_burst_index/g, "成交突增指数"],
    [/quote_imbalance/g, "盘口报价失衡"],
    [/bid_depth_ratio/g, "买盘深度占比"],
    [/mark_basis/g, "标记价基差"],
    [/funding_rate/g, "资金费率"],
    [/oi_change_rate_5m/g, "五分钟持仓变化率"],
    [/oi_change_rate_1h/g, "一小时持仓变化率"],
    [/dist_to_24h_high/g, "距二十四小时高点"],
    [/dist_to_24h_low/g, "距二十四小时低点"],
    [/position_in_range_24h/g, "二十四小时区间位置"],
    [/position_in_range_4h/g, "四小时区间位置"],
    [/\bAND\b/g, "且"],
    [/->\s*long\s*(\d+)bars/gi, "，方向：做多，周期：$1 根"],
    [/->\s*short\s*(\d+)bars/gi, "，方向：做空，周期：$1 根"],
    [/\blong\b/gi, "做多"],
    [/\bshort\b/gi, "做空"],
  ];
  return replacements.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), value);
}

function translateVisibleText(value?: string | null) {
  if (!value) return "";
  const replacements: Array<[RegExp, string]> = [
    [/Review Queue/gi, "复核队列"],
    [/review queue/gi, "复核队列"],
    [/Discovery/gi, "发现流程"],
    [/discovery/gi, "发现流程"],
    [/Promoter/gi, "晋升器"],
    [/promoter/gi, "晋升器"],
    [/Candidate/gi, "候选"],
    [/candidate/gi, "候选"],
    [/Pending/gi, "待审"],
    [/pending/gi, "待审"],
    [/Approved/gi, "已批准"],
    [/approved/gi, "已批准"],
    [/Rejected/gi, "已拒绝"],
    [/rejected/gi, "已拒绝"],
    [/running/gi, "运行中"],
    [/completed/gi, "已完成"],
    [/warning/gi, "预警"],
    [/error/gi, "错误"],
    [/healthy/gi, "正常"],
    [/offline/gi, "离线"],
    [/online/gi, "在线"],
    [/WebSocket/gi, "实时连接"],
    [/LLM/gi, "大模型"],
    [/OOS WR/gi, "样本外胜率"],
    [/WR/gi, "胜率"],
    [/PF/gi, "利润因子"],
    [/DEFER/gi, "延期"],
    [/AUTO_APPROVE/gi, "自动批准"],
    [/AUTO_REJECT/gi, "自动拒绝"],
    [/Connection error/gi, "连接失败"],
    [/generic_alpha/gi, "通用阿尔法"],
    [/generic/gi, "通用机制"],
    [/compression_release/gi, "压缩释放"],
    [/vwap_reversion/gi, "均价回归"],
    [/seller_impulse/gi, "卖压冲击"],
    [/buyer_impulse/gi, "买方冲击"],
  ];
  return replacements.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), value);
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
