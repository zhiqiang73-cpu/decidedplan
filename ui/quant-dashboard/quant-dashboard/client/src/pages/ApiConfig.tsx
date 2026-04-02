import QuantLayout from "@/components/QuantLayout";
import { trpc } from "@/lib/trpc";
import { useState } from "react";
import { toast } from "sonner";
import { Eye, EyeOff, Save, Wifi, WifiOff, Shield, CheckCircle, AlertCircle, DollarSign, TrendingUp, Activity } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

export default function ApiConfig() {
  const { data: config, refetch } = trpc.apiConfig.get.useQuery();
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 30000 });

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [isTestnet, setIsTestnet] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testing, setTesting] = useState(false);

  const saveMut = trpc.apiConfig.save.useMutation({
    onSuccess: () => { toast.success("API配置已保存"); refetch(); setApiKey(""); setApiSecret(""); },
    onError: () => toast.error("保存失败"),
  });
  const testMut = trpc.apiConfig.testConnection.useMutation({
    onSuccess: (r) => {
      if (r.success) {
        toast.success(`连接成功！延迟 ${r.latency}ms`);
      } else {
        toast.error(r.message);
      }
      refetch();
    },
    onError: () => toast.error("测试连接失败"),
    onSettled: () => setTesting(false),
  });

  const handleSave = () => {
    if (!apiKey || !apiSecret) { toast.error("请填写完整的API Key和Secret"); return; }
    saveMut.mutate({ apiKey, apiSecret, isTestnet });
  };

  const handleTest = () => {
    setTesting(true);
    testMut.mutate();
  };

  const totalEquity = parseFloat(wallet?.totalEquity ?? "0");
  const unrealizedPnl = parseFloat(wallet?.unrealizedPnl ?? "0");
  const usedMargin = parseFloat(wallet?.usedMargin ?? "0");

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 space-y-5 max-w-4xl">
        <div>
          <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>API 配置</h1>
          <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>配置币安实盘API密钥，连接真实交易账户</p>
        </div>

        {/* Connection Status Banner */}
        <div className={`flex items-center gap-3 p-4 rounded-xl ${config?.isActive ? "bg-profit-subtle border-profit" : "bg-warning-subtle"}`}
          style={{ border: `1px solid ${config?.isActive ? "rgba(14,203,129,0.3)" : "rgba(240,165,0,0.3)"}` }}>
          {config?.isActive ? (
            <><CheckCircle size={20} className="text-profit flex-shrink-0" />
              <div>
                <div className="text-sm font-medium text-profit">已连接 · 实盘API</div>
                <div className="text-xs" style={{ color: "#848e9c" }}>
                  最后测试: {config.lastTestedAt ? new Date(config.lastTestedAt).toUTCString() : "从未"} UTC
                  {config.isTestnet && " · 测试网"}
                </div>
              </div></>
          ) : (
            <><AlertCircle size={20} className="text-warning-q flex-shrink-0" />
              <div>
                <div className="text-sm font-medium text-warning-q">
                  {config ? "连接未验证" : "未配置API"}
                </div>
                <div className="text-xs" style={{ color: "#848e9c" }}>请配置并测试API连接以启用实盘交易</div>
              </div></>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* API Form */}
          <div className="card-q p-5 space-y-4">
            <div className="flex items-center gap-2 mb-1">
              <Shield size={16} style={{ color: "#f0b90b" }} />
              <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>API 密钥配置</h2>
            </div>

            <div>
              <label className="block text-xs mb-1.5" style={{ color: "#848e9c" }}>API Key</label>
              <Input
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder={config?.apiKey ? "已配置 (输入新值以更新)" : "输入 Binance API Key"}
                className="font-mono text-sm"
                style={{ backgroundColor: "#161a1e", borderColor: "#2b3139", color: "#eaecef" }}
              />
            </div>

            <div>
              <label className="block text-xs mb-1.5" style={{ color: "#848e9c" }}>API Secret</label>
              <div className="relative">
                <Input
                  type={showSecret ? "text" : "password"}
                  value={apiSecret}
                  onChange={e => setApiSecret(e.target.value)}
                  placeholder={config?.apiSecret ? "已配置 (输入新值以更新)" : "输入 Binance API Secret"}
                  className="font-mono text-sm pr-10"
                  style={{ backgroundColor: "#161a1e", borderColor: "#2b3139", color: "#eaecef" }}
                />
                <button
                  onClick={() => setShowSecret(!showSecret)}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{ color: "#848e9c" }}
                >
                  {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
              <div>
                <div className="text-sm" style={{ color: "#eaecef" }}>使用测试网</div>
                <div className="text-xs" style={{ color: "#848e9c" }}>Binance Testnet (模拟交易)</div>
              </div>
              <Switch checked={isTestnet} onCheckedChange={setIsTestnet} />
            </div>

            <div className="flex gap-2 pt-1">
              <Button
                onClick={handleSave}
                disabled={saveMut.isPending}
                className="flex-1 text-sm font-medium"
                style={{ backgroundColor: "#f0b90b", color: "#0b0e11" }}
              >
                <Save size={14} className="mr-1.5" />
                {saveMut.isPending ? "保存中..." : "保存配置"}
              </Button>
              <Button
                onClick={handleTest}
                disabled={testing || !config?.apiKey}
                variant="outline"
                className="flex-1 text-sm"
                style={{ borderColor: "#2b3139", color: "#eaecef", backgroundColor: "transparent" }}
              >
                {testing ? (
                  <><RefreshCw size={14} className="mr-1.5 animate-spin" />测试中...</>
                ) : (
                  <><Wifi size={14} className="mr-1.5" />测试连接</>
                )}
              </Button>
            </div>

            <div className="text-xs p-3 rounded-lg" style={{ backgroundColor: "rgba(24,144,255,0.1)", color: "#1890ff", border: "1px solid rgba(24,144,255,0.2)" }}>
              <div className="font-medium mb-1">安全提示</div>
              <ul className="space-y-0.5" style={{ color: "#848e9c" }}>
                <li>• 仅开启现货/合约交易权限，禁止提现权限</li>
                <li>• 建议绑定服务器IP白名单</li>
                <li>• API Secret 加密存储，不会明文传输</li>
              </ul>
            </div>
          </div>

          {/* Wallet Status */}
          <div className="space-y-4">
            <div className="card-q p-5">
              <div className="flex items-center gap-2 mb-4">
                <DollarSign size={16} style={{ color: "#f0b90b" }} />
                <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>钱包状态</h2>
                <span className="live-dot ml-auto" />
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                  <span className="text-sm" style={{ color: "#848e9c" }}>总权益</span>
                  <span className="text-sm font-bold font-num" style={{ color: "#eaecef" }}>
                    ${totalEquity.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                  <span className="text-sm" style={{ color: "#848e9c" }}>可用余额</span>
                  <span className="text-sm font-num text-profit">
                    ${parseFloat(wallet?.availableBalance ?? "0").toLocaleString("en-US", { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                  <span className="text-sm" style={{ color: "#848e9c" }}>占用保证金</span>
                  <span className="text-sm font-num text-warning-q">
                    ${usedMargin.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                  <span className="text-sm" style={{ color: "#848e9c" }}>未实现盈亏</span>
                  <span className={`text-sm font-num ${unrealizedPnl >= 0 ? "text-profit" : "text-loss"}`}>
                    {unrealizedPnl >= 0 ? "+" : ""}${unrealizedPnl.toFixed(2)}
                  </span>
                </div>
              </div>
            </div>

            {/* Asset Breakdown */}
            <div className="card-q p-5">
              <h3 className="text-sm font-semibold mb-3" style={{ color: "#eaecef" }}>资产明细</h3>
              <div className="space-y-2">
                {(wallet?.assets as any[] ?? []).map((asset: any) => (
                  <div key={asset.asset} className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ backgroundColor: "#161a1e" }}>
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold" style={{ backgroundColor: "#2b3139", color: "#f0b90b" }}>
                        {asset.asset[0]}
                      </div>
                      <span className="text-sm font-medium" style={{ color: "#eaecef" }}>{asset.asset}</span>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-num" style={{ color: "#eaecef" }}>{parseFloat(asset.balance).toFixed(4)}</div>
                      {parseFloat(asset.unrealizedPnl) !== 0 && (
                        <div className={`text-xs font-num ${parseFloat(asset.unrealizedPnl) >= 0 ? "text-profit" : "text-loss"}`}>
                          {parseFloat(asset.unrealizedPnl) >= 0 ? "+" : ""}{parseFloat(asset.unrealizedPnl).toFixed(2)}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function RefreshCw({ size, className }: { size: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
    </svg>
  );
}
