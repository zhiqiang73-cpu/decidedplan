import { useState } from "react";
import { Link, useLocation } from "wouter";
import {
  LayoutDashboard,
  Settings,
  TrendingUp,
  Zap,
  BarChart2,
  History,
  GitBranch,
  Activity,
  Menu,
  X,
  Wifi,
  WifiOff,
  Clock,
} from "lucide-react";
import { trpc } from "@/lib/trpc";
import { useWebSocket } from "@/hooks/useWebSocket";
import { splitDateTimeUTC8 } from "@/lib/time";

const NAV_ITEMS = [
  { path: "/", icon: LayoutDashboard, label: "仪表盘" },
  { path: "/alpha", icon: Zap, label: "Alpha引擎" },
  { path: "/strategies", icon: TrendingUp, label: "策略池" },
  { path: "/pairs", icon: BarChart2, label: "交易对" },
  { path: "/trades", icon: History, label: "交易记录" },
  { path: "/positions", icon: Activity, label: "持仓监控" },
  { path: "/api-config", icon: Settings, label: "API配置" },
  { path: "/dev-progress", icon: GitBranch, label: "开发进度" },
];

export default function QuantLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [location] = useLocation();
  const { connected } = useWebSocket();
  const { data: health } = trpc.alphaEngine.getSystemHealth.useQuery(undefined, { refetchInterval: 30000 });

  const systemScore = health?.overall ?? 0;
  const systemStatus = systemScore >= 80 ? "正常" : systemScore >= 60 ? "警告" : "异常";
  const systemColor = systemScore >= 80 ? "#0ecb81" : systemScore >= 60 ? "#f0a500" : "#f6465d";

  const clock = splitDateTimeUTC8(new Date());

  return (
    <div className="flex h-screen overflow-hidden" style={{ backgroundColor: "#0b0e11" }}>
      {mobileOpen && (
        <button
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label="关闭侧栏"
        />
      )}

      <aside
        className={`fixed lg:relative z-50 flex flex-col h-full w-56 transition-transform duration-300 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        }`}
        style={{ backgroundColor: "#0b0e11", borderRight: "1px solid #1e2329" }}
      >
        <div className="flex items-center gap-3 px-4 py-4" style={{ borderBottom: "1px solid #1e2329" }}>
          <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "linear-gradient(135deg, #f0b90b, #f8d12f)" }}>
            <Zap size={16} style={{ color: "#0b0e11" }} />
          </div>
          <div>
            <div className="font-bold text-sm" style={{ color: "#eaecef" }}>QuantAlpha</div>
            <div className="text-xs" style={{ color: "#848e9c" }}>量化交易系统</div>
          </div>
        </div>

        <div className="mx-3 mt-3 mb-1 px-3 py-2 rounded-lg flex items-center gap-2" style={{ backgroundColor: "#1e2329" }}>
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: systemColor }} />
          <span className="text-xs" style={{ color: "#848e9c" }}>
            系统: <span style={{ color: systemColor }}>{systemStatus}</span>
          </span>
          <span className="ml-auto text-xs font-num" style={{ color: "#848e9c" }}>{systemScore}%</span>
        </div>

        <nav className="flex-1 overflow-y-auto py-2 px-2">
          {NAV_ITEMS.map((item) => {
            const isActive = location === item.path || (item.path !== "/" && location.startsWith(item.path));
            return (
              <Link key={item.path} href={item.path}>
                <div
                  className="flex items-center gap-3 px-3 py-2.5 rounded-lg mb-0.5 cursor-pointer transition-all duration-150"
                  style={{
                    backgroundColor: isActive ? "#1e2329" : "transparent",
                    color: isActive ? "#eaecef" : "#848e9c",
                    borderLeft: isActive ? "2px solid #f0b90b" : "2px solid transparent",
                  }}
                  onClick={() => setMobileOpen(false)}
                >
                  <item.icon size={18} style={{ color: isActive ? "#f0b90b" : "#848e9c", flexShrink: 0 }} />
                  <span className="text-sm truncate">{item.label}</span>
                </div>
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header
          className="flex items-center justify-between px-4 py-3 flex-shrink-0"
          style={{ backgroundColor: "#0b0e11", borderBottom: "1px solid #1e2329" }}
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
              <span>UTC+8</span>
              <span className="font-num">
                <span style={{ color: "#5e6673" }}>{clock.date}</span>
                <span className="ml-1" style={{ color: "#eaecef" }}>{clock.time}</span>
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div
              className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg"
              style={{ backgroundColor: "#1e2329", color: connected ? "#0ecb81" : "#848e9c" }}
            >
              {connected ? <Wifi size={12} /> : <WifiOff size={12} />}
              <span className="hidden sm:inline">{connected ? "4/4 流" : "重连中"}</span>
            </div>
            <button className="p-1.5 rounded-lg hover:bg-[#1e2329]" style={{ color: "#848e9c" }}>
              <X size={0} />
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto" style={{ backgroundColor: "#0b0e11" }}>
          {children}
        </main>
      </div>
    </div>
  );
}

