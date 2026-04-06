import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useState } from "react";
import { toast } from "sonner";
import { CheckCircle, Clock, AlertCircle, ChevronDown, ChevronUp, GitBranch, Zap, TrendingUp, Settings, BarChart2, Activity } from "lucide-react";

const MODULES = [
  {
    id: "core-infra",
    name: "核心基础设施",
    icon: Settings,
    color: "#f0b90b",
    features: [
      { name: "项目脚手架 (React + tRPC + Drizzle)", status: "done", priority: "P0" },
      { name: "数据库Schema设计 (10张核心表)", status: "done", priority: "P0" },
      { name: "深色主题UI系统 (币安风格)", status: "done", priority: "P0" },
      { name: "侧边栏导航布局", status: "done", priority: "P0" },
      { name: "实时推送通道", status: "in-progress", priority: "P1" },
      { name: "UTC时区统一处理", status: "done", priority: "P0" },
    ]
  },
  {
    id: "alpha-engine",
    name: "Alpha 引擎",
    icon: Zap,
    color: "#f0b90b",
    features: [
      { name: "引擎运行状态展示", status: "done", priority: "P0" },
      { name: "多阶段进度可视化", status: "done", priority: "P0" },
      { name: "候选策略审批工作流", status: "done", priority: "P0" },
      { name: "参数微调界面 (IC阈值/胜率/条件数)", status: "done", priority: "P0" },
      { name: "前三出场条件展示", status: "done", priority: "P0" },
      { name: "回测结果权益曲线", status: "done", priority: "P0" },
      { name: "真实数据下载进度", status: "in-progress", priority: "P1" },
      { name: "Binance API真实数据接入", status: "planned", priority: "P1" },
      { name: "IC扫描真实计算", status: "planned", priority: "P1" },
      { name: "样本外滚动验证", status: "planned", priority: "P1" },
    ]
  },
  {
    id: "strategy-pool",
    name: "策略池管理",
    icon: TrendingUp,
    color: "#0ecb81",
    features: [
      { name: "策略列表展示 (排序/筛选/搜索)", status: "done", priority: "P0" },
      { name: "策略状态管理 (激活/暂停/降级)", status: "done", priority: "P0" },
      { name: "手动触发回测按钮", status: "done", priority: "P0" },
      { name: "折叠卡片详情展示", status: "done", priority: "P0" },
      { name: "AI策略分析 (LLM)", status: "done", priority: "P1" },
      { name: "回测权益曲线图表", status: "done", priority: "P0" },
      { name: "过拟合分数评估", status: "done", priority: "P1" },
      { name: "真实回测引擎", status: "planned", priority: "P1" },
    ]
  },
  {
    id: "trading",
    name: "交易执行",
    icon: BarChart2,
    color: "#1890ff",
    features: [
      { name: "交易记录完整展示", status: "done", priority: "P0" },
      { name: "持仓实时监控", status: "done", priority: "P0" },
      { name: "盈亏统计图表", status: "done", priority: "P0" },
      { name: "按交易对/策略/时间筛选", status: "done", priority: "P0" },
      { name: "Binance实盘API接入", status: "planned", priority: "P1" },
      { name: "自动下单执行", status: "planned", priority: "P1" },
      { name: "止损止盈自动管理", status: "planned", priority: "P1" },
    ]
  },
  {
    id: "multi-pair",
    name: "多交易对管理",
    icon: Activity,
    color: "#8b5cf6",
    features: [
      { name: "交易对添加/移除界面", status: "done", priority: "P0" },
      { name: "Alpha引擎自动触发", status: "done", priority: "P0" },
      { name: "数据下载进度显示", status: "done", priority: "P0" },
      { name: "每对策略数量统计", status: "done", priority: "P0" },
      { name: "真实Binance数据下载", status: "planned", priority: "P1" },
    ]
  },
  {
    id: "api-config",
    name: "API配置与钱包",
    icon: Settings,
    color: "#f0a500",
    features: [
      { name: "接口密钥录入页面", status: "done", priority: "P0" },
      { name: "测试网/实盘切换", status: "done", priority: "P0" },
      { name: "连接测试功能", status: "done", priority: "P0" },
      { name: "钱包状态展示", status: "done", priority: "P0" },
      { name: "资产明细列表", status: "done", priority: "P0" },
      { name: "真实Binance账户余额", status: "planned", priority: "P1" },
    ]
  },
];

export default function DevProgress() {
  const [expandedModule, setExpandedModule] = useState<string | null>("alpha-engine");

  const allFeatures = MODULES.flatMap(m => m.features);
  const doneCount = allFeatures.filter(f => f.status === "done").length;
  const inProgressCount = allFeatures.filter(f => f.status === "in-progress").length;
  const plannedCount = allFeatures.filter(f => f.status === "planned").length;
  const totalCount = allFeatures.length;
  const overallProgress = Math.round((doneCount / totalCount) * 100);

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5 max-w-4xl">
        {/* Header */}
        <div className="flex items-center gap-2">
          <GitBranch size={20} style={{ color: "#f0b90b" }} />
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>开发进度追踪</h1>
            <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>QuantAlpha 系统开发状态 · 实时更新</p>
          </div>
        </div>

        {/* Overall Progress */}
        <div className="card-q p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold" style={{ color: "#eaecef" }}>整体进度</h3>
            <span className="text-2xl font-bold font-num" style={{ color: "#f0b90b" }}>{overallProgress}%</span>
          </div>
          <div className="progress-q mb-3" style={{ height: 8 }}>
            <div className="progress-q-fill" style={{ width: `${overallProgress}%`, height: "100%" }} />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="text-center p-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
              <div className="text-xl font-bold font-num text-profit">{doneCount}</div>
              <div className="text-xs mt-0.5" style={{ color: "#848e9c" }}>已完成</div>
            </div>
            <div className="text-center p-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
              <div className="text-xl font-bold font-num text-info-q">{inProgressCount}</div>
              <div className="text-xs mt-0.5" style={{ color: "#848e9c" }}>进行中</div>
            </div>
            <div className="text-center p-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
              <div className="text-xl font-bold font-num text-neutral-q">{plannedCount}</div>
              <div className="text-xs mt-0.5" style={{ color: "#848e9c" }}>计划中</div>
            </div>
          </div>
        </div>

        {/* Version Info */}
        <div className="card-q p-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              { label: "当前版本", value: "v0.8.0-alpha" },
              { label: "技术栈", value: "React 19 + tRPC" },
              { label: "数据库", value: "MySQL (Drizzle ORM)" },
              { label: "最后更新", value: new Date().toUTCString().slice(0, 16) },
            ].map(item => (
              <div key={item.label} className="p-2.5 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                <div className="text-xs" style={{ color: "#848e9c" }}>{item.label}</div>
                <div className="text-sm font-medium mt-0.5" style={{ color: "#eaecef" }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Module Progress */}
        <div className="space-y-3">
          {MODULES.map(module => {
            const moduleDone = module.features.filter(f => f.status === "done").length;
            const moduleTotal = module.features.length;
            const modulePct = Math.round((moduleDone / moduleTotal) * 100);
            const isExpanded = expandedModule === module.id;

            return (
              <div key={module.id} className="card-q overflow-hidden">
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-[#252a30] transition-colors"
                  onClick={() => setExpandedModule(isExpanded ? null : module.id)}
                >
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ backgroundColor: `${module.color}20` }}>
                    <module.icon size={16} style={{ color: module.color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium" style={{ color: "#eaecef" }}>{module.name}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-num" style={{ color: module.color }}>{modulePct}%</span>
                        <span className="text-xs" style={{ color: "#848e9c" }}>{moduleDone}/{moduleTotal}</span>
                        {isExpanded ? <ChevronUp size={14} style={{ color: "#848e9c" }} /> : <ChevronDown size={14} style={{ color: "#848e9c" }} />}
                      </div>
                    </div>
                    <div className="progress-q mt-1.5">
                      <div className="progress-q-fill" style={{ width: `${modulePct}%`, background: `linear-gradient(90deg, ${module.color}, ${module.color}cc)` }} />
                    </div>
                  </div>
                </div>

                {isExpanded && (
                  <div className="px-4 pb-3" style={{ borderTop: "1px solid #2b3139" }}>
                    <div className="pt-3 space-y-2">
                      {module.features.map((feature, i) => (
                        <div key={i} className="flex items-center gap-3 py-1.5">
                          <div className="flex-shrink-0">
                            {feature.status === "done" ? (
                              <CheckCircle size={14} style={{ color: "#0ecb81" }} />
                            ) : feature.status === "in-progress" ? (
                              <div className="w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center" style={{ borderColor: "#1890ff" }}>
                                <div className="w-1.5 h-1.5 rounded-full live-dot" style={{ backgroundColor: "#1890ff" }} />
                              </div>
                            ) : (
                              <Clock size={14} style={{ color: "#5e6673" }} />
                            )}
                          </div>
                          <span className="text-sm flex-1" style={{ color: feature.status === "done" ? "#eaecef" : feature.status === "in-progress" ? "#eaecef" : "#5e6673" }}>
                            {feature.name}
                          </span>
                          <span className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 ${feature.priority === "P0" ? "badge-degraded" : "badge-pending"}`}>
                            {feature.priority}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Next Steps */}
        <div className="card-q p-5">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "#eaecef" }}>下一步计划</h3>
          <div className="space-y-2">
            {[
              { step: "1", task: "接入真实Binance API，实现账户余额、持仓、历史交易数据同步", priority: "P1" },
              { step: "2", task: "实现实时推送通道，信号触发、回测完成等事件通知", priority: "P1" },
              { step: "3", task: "构建真实数据下载引擎，支持多周期K线数据自动获取", priority: "P1" },
              { step: "4", task: "实现IC扫描真实计算，基于历史数据计算52+特征信息系数", priority: "P1" },
              { step: "5", task: "搭建样本外滚动验证引擎，确保策略在真实场景里不过度美化", priority: "P1" },
            ].map(item => (
              <div key={item.step} className="flex items-start gap-3 p-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                <span className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0" style={{ backgroundColor: "#2b3139", color: "#f0b90b" }}>
                  {item.step}
                </span>
                <span className="text-sm flex-1" style={{ color: "#eaecef" }}>{item.task}</span>
                <span className="badge-pending text-xs flex-shrink-0">{item.priority}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}
