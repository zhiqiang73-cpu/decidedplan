import { useState, useEffect, useRef } from "react";
import { Link, useLocation } from "wouter";
import {
  LayoutDashboard, Settings, TrendingUp, Zap, BarChart2,
  History, GitBranch, ChevronLeft, ChevronRight, Bell,
  Activity, Wifi, WifiOff, Menu, X, CheckCircle, AlertCircle,
  Info, AlertTriangle, Clock, Layers
} from "lucide-react";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const NAV_ITEMS = [
  { path: "/", icon: LayoutDashboard, label: "仪表盘", labelEn: "Dashboard" },
  { path: "/alpha", icon: Zap, label: "Alpha引擎", labelEn: "Alpha Engine", highlight: true },
  { path: "/strategies", icon: TrendingUp, label: "策略池", labelEn: "Strategy Pool" },
  { path: "/pairs", icon: BarChart2, label: "交易对", labelEn: "Trading Pairs" },
  { path: "/trades", icon: History, label: "交易记录", labelEn: "Trade History" },
  { path: "/positions", icon: Activity, label: "持仓监控", labelEn: "Positions" },
  { path: "/api-config", icon: Settings, label: "API配置", labelEn: "API Config" },
  { path: "/dev-progress", icon: GitBranch, label: "开发进度", labelEn: "Dev Progress" },
];

export default function QuantLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [location] = useLocation();
  const notifRef = useRef<HTMLDivElement>(null);

  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, {
    refetchInterval: 30000,
  });
  const { data: events } = trpc.systemEvents.list.useQuery({ limit: 12 });
  const { connected, recentSignals } = useWebSocket();

  const systemOk = health?.overall && health.overall >= 80;
  const systemWarn = health?.overall && health.overall >= 60 && health.overall < 80;

  // Close notification panel when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifOpen(false);
      }
    }
    if (notifOpen) document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [notifOpen]);

  const unreadCount = (events?.length ?? 0) + recentSignals.length;

  return (
    <div className="flex h-screen overflow-hidden" style={{ backgroundColor: "#0b0e11" }}>
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`
          fixed lg:relative z-50 flex flex-col h-full transition-all duration-300
          ${collapsed ? "w-16" : "w-56"}
          ${mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"}
        `}
        style={{ backgroundColor: "#0b0e11", borderRight: "1px solid #1e2329" }}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-4 py-4" style={{ borderBottom: "1px solid #1e2329", minHeight: 56 }}>
          <div className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "linear-gradient(135deg, #f0b90b, #f8d12f)" }}>
            <Zap size={16} style={{ color: "#0b0e11" }} />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="font-bold text-sm" style={{ color: "#eaecef" }}>QuantAlpha</div>
              <div className="text-xs" style={{ color: "#848e9c" }}>量化交易系统</div>
            </div>
          )}
        </div>

        {/* System Status */}
        {!collapsed ? (
          <div className="mx-3 mt-3 mb-1 px-3 py-2 rounded-lg flex items-center gap-2" style={{ backgroundColor: "#1e2329" }}>
            <span className={systemOk ? "live-dot" : systemWarn ? "live-dot-warning" : "live-dot-error"} />
            <span className="text-xs" style={{ color: "#848e9c" }}>
              系统: <span style={{ color: systemOk ? "#0ecb81" : systemWarn ? "#f0a500" : "#f6465d" }}>
                {systemOk ? "正常" : systemWarn ? "警告" : "异常"}
              </span>
            </span>
            {health?.overall && (
              <span className="ml-auto text-xs font-num" style={{ color: "#848e9c" }}>{health.overall}%</span>
            )}
          </div>
        ) : (
          <div className="flex justify-center mt-3 mb-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="w-8 h-8 rounded-lg flex items-center justify-center cursor-default" style={{ backgroundColor: "#1e2329" }}>
                  <span className={`w-2 h-2 rounded-full ${systemOk ? "bg-[#0ecb81]" : systemWarn ? "bg-[#f0a500]" : "bg-[#f6465d]"}`} />
                </div>
              </TooltipTrigger>
              <TooltipContent side="right">
                系统状态: {systemOk ? "正常" : systemWarn ? "警告" : "异常"} {health?.overall}%
              </TooltipContent>
            </Tooltip>
          </div>
        )}

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-2 px-2">
          {NAV_ITEMS.map((item) => {
            const isActive = location === item.path || (item.path !== "/" && location.startsWith(item.path));
            const navItem = (
              <div
                className={`
                  flex items-center gap-3 px-3 py-2.5 rounded-lg mb-0.5 cursor-pointer transition-all duration-150
                  ${collapsed ? "justify-center" : ""}
                  ${isActive ? "text-sm font-medium" : "text-sm hover:bg-[#1e2329]"}
                `}
                style={{
                  backgroundColor: isActive ? "#1e2329" : "transparent",
                  color: isActive ? "#eaecef" : "#848e9c",
                  borderLeft: !collapsed && isActive ? "2px solid #f0b90b" : "2px solid transparent",
                }}
                onClick={() => setMobileOpen(false)}
              >
                <item.icon
                  size={18}
                  style={{ color: isActive ? "#f0b90b" : item.highlight ? "#f0b90b" : "#848e9c", flexShrink: 0 }}
                />
                {!collapsed && (
                  <>
                    <span className="truncate">{item.label}</span>
                    {item.highlight && !isActive && (
                      <span className="ml-auto text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: "rgba(240,185,11,0.15)", color: "#f0b90b" }}>
                        核心
                      </span>
                    )}
                  </>
                )}
              </div>
            );

            return collapsed ? (
              <Tooltip key={item.path}>
                <TooltipTrigger asChild>
                  <Link href={item.path}>{navItem}</Link>
                </TooltipTrigger>
                <TooltipContent side="right">
                  {item.label}
                  {item.highlight && <span className="ml-1 text-[#f0b90b]">核心</span>}
                </TooltipContent>
              </Tooltip>
            ) : (
              <Link key={item.path} href={item.path}>{navItem}</Link>
            );
          })}
        </nav>

        {/* Collapse toggle */}
        <div className="p-2" style={{ borderTop: "1px solid #1e2329" }}>
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="w-full flex items-center justify-center py-2 rounded-lg transition-colors hover:bg-[#1e2329]"
            style={{ color: "#848e9c" }}
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
            {!collapsed && <span className="ml-2 text-xs">收起侧栏</span>}
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <header
          className="flex items-center justify-between px-4 py-3 flex-shrink-0"
          style={{ backgroundColor: "#0b0e11", borderBottom: "1px solid #1e2329", minHeight: 56 }}
        >
          <div className="flex items-center gap-3">
            <button
              className="lg:hidden p-1.5 rounded-lg hover:bg-[#1e2329]"
              style={{ color: "#848e9c" }}
              onClick={() => setMobileOpen(true)}
            >
              <Menu size={20} />
            </button>
            <div className="hidden sm:flex items-center gap-2 text-sm" style={{ color: "#848e9c" }}>
              <Clock size={12} />
              <span>UTC</span>
              <LiveClock />
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Network status */}
            <div
              className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg transition-colors"
              style={{ backgroundColor: "#1e2329", color: connected ? "#0ecb81" : "#848e9c" }}
            >
              {connected ? <Wifi size={12} /> : <WifiOff size={12} />}
              <span className="hidden sm:inline">{connected ? "4/4 流" : "重连中"}</span>
            </div>

            {/* Notifications */}
            <div className="relative" ref={notifRef}>
              <button
                onClick={() => setNotifOpen(!notifOpen)}
                className="relative p-1.5 rounded-lg hover:bg-[#1e2329] transition-colors"
                style={{ color: notifOpen ? "#f0b90b" : "#848e9c" }}
              >
                <Bell size={18} />
                {unreadCount > 0 && (
                  <span
                    className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold"
                    style={{ backgroundColor: "#f6465d", color: "#fff" }}
                  >
                    {unreadCount > 9 ? "9+" : unreadCount}
                  </span>
                )}
              </button>

              {/* Notification Panel */}
              {notifOpen && (
                <div
                  className="absolute right-0 top-10 w-80 rounded-xl shadow-2xl z-50 overflow-hidden animate-notif-slide"
                  style={{ backgroundColor: "#1e2329", border: "1px solid #2b3139" }}
                >
                  <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
                    <div className="flex items-center gap-2">
                      <Bell size={14} style={{ color: "#f0b90b" }} />
                      <span className="text-sm font-semibold" style={{ color: "#eaecef" }}>通知中心</span>
                    </div>
                    <button onClick={() => setNotifOpen(false)} style={{ color: "#848e9c" }}>
                      <X size={14} />
                    </button>
                  </div>

                  {/* Recent Signals */}
                  {recentSignals.length > 0 && (
                    <div>
                      <div className="px-4 py-2 text-xs font-medium" style={{ color: "#848e9c", backgroundColor: "#161a1e" }}>
                        最新信号 ({recentSignals.length})
                      </div>
                      {recentSignals.slice(0, 3).map((sig, i) => (
                        <div key={i} className="flex items-start gap-3 px-4 py-3" style={{ borderBottom: "1px solid #2b3139" }}>
                          <div className={`mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${sig.direction === "LONG" ? "bg-[#0ecb81]" : "bg-[#f6465d]"}`} />
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium" style={{ color: "#eaecef" }}>
                              {sig.symbol} <span className={sig.direction === "LONG" ? "text-[#0ecb81]" : "text-[#f6465d]"}>{sig.direction}</span>
                            </div>
                            <div className="text-xs" style={{ color: "#848e9c" }}>
                              {sig.strategyId} · 置信度 {(sig.confidence * 100).toFixed(0)}%
                            </div>
                          </div>
                          <div className="text-xs font-num flex-shrink-0" style={{ color: "#5e6673" }}>
                            刚刚
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* System Events */}
                  <div>
                    <div className="px-4 py-2 text-xs font-medium" style={{ color: "#848e9c", backgroundColor: "#161a1e" }}>
                      系统事件
                    </div>
                    <div className="overflow-y-auto" style={{ maxHeight: 240 }}>
                      {(events ?? []).slice(0, 8).map((e, i) => (
                        <div key={i} className="flex items-start gap-3 px-4 py-2.5" style={{ borderBottom: "1px solid #1e2329" }}>
                          <div className="flex-shrink-0 mt-0.5">
                            {e.severity === "info" ? (
                              <CheckCircle size={13} style={{ color: "#0ecb81" }} />
                            ) : e.severity === "warning" ? (
                              <AlertTriangle size={13} style={{ color: "#f0a500" }} />
                            ) : (
                              <AlertCircle size={13} style={{ color: "#f6465d" }} />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-medium" style={{ color: "#eaecef" }}>{e.title}</div>
                            <div className="text-xs truncate" style={{ color: "#848e9c" }}>{e.message}</div>
                          </div>
                          <div className="flex-shrink-0 text-xs font-num" style={{ color: "#5e6673" }}>
                            {e.occurredAt ? formatRelTime(new Date(e.occurredAt)) : ""}
                          </div>
                        </div>
                      ))}
                      {(events ?? []).length === 0 && (
                        <div className="flex flex-col items-center justify-center py-8" style={{ color: "#848e9c" }}>
                          <Info size={24} style={{ opacity: 0.3 }} />
                          <p className="text-xs mt-2">暂无系统事件</p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto" style={{ backgroundColor: "#0b0e11" }}>
          {children}
        </main>
      </div>
    </div>
  );
}

function LiveClock() {
  const [timeStr, setTimeStr] = useState(() => {
    const now = new Date();
    const d = now.toISOString().slice(0, 10);
    const t = now.toISOString().slice(11, 19);
    return { d, t };
  });

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      setTimeStr({
        d: now.toISOString().slice(0, 10),
        t: now.toISOString().slice(11, 19),
      });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <span className="font-num">
      <span style={{ color: "#5e6673" }}>{timeStr.d}</span>
      <span className="ml-1" style={{ color: "#eaecef" }}>{timeStr.t}</span>
    </span>
  );
}

function formatRelTime(date: Date): string {
  const diff = Date.now() - date.getTime();
  if (diff < 60000) return `${Math.floor(diff / 1000)}s前`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h前`;
  return `${Math.floor(diff / 86400000)}d前`;
}
