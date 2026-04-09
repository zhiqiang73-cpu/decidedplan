import QuantLayout from "@/components/QuantLayout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { trpc } from "@/lib/trpc";
import { useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  RefreshCw,
  Save,
  ShieldCheck,
  Wallet,
  Wifi,
} from "lucide-react";

export default function ApiConfig() {
  const { data: config, refetch } = trpc.apiConfig.get.useQuery();
  const { data: wallet } = trpc.wallet.getSnapshot.useQuery(undefined, { refetchInterval: 30000 });

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [testing, setTesting] = useState(false);

  const saveMutation = trpc.apiConfig.save.useMutation({
    onSuccess: async () => {
      toast.success("API 配置已保存");
      setApiKey("");
      setApiSecret("");
      await refetch();
    },
    onError: () => toast.error("保存失败，请检查输入内容"),
  });

  const testMutation = trpc.apiConfig.testConnection.useMutation({
    onSuccess: async (result) => {
      if (result.success) {
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
      await refetch();
    },
    onError: () => toast.error("连接测试失败"),
    onSettled: () => setTesting(false),
  });

  const walletAssets = Array.isArray(wallet?.assets) ? wallet.assets : [];
  const totalEquity = Number(wallet?.totalEquity ?? 0);
  const availableBalance = Number(wallet?.availableBalance ?? 0);
  const usedMargin = Number(wallet?.usedMargin ?? 0);
  const unrealizedPnl = Number(wallet?.unrealizedPnl ?? 0);

  const connectionTone = config?.isActive
    ? {
        border: "rgba(14,203,129,0.35)",
        background: "rgba(14,203,129,0.10)",
        title: "接口已配置，可执行连接测试",
        detail: "当前后端固定连接 Binance Futures Testnet，页面不再假装支持主网切换。",
        icon: <CheckCircle2 size={18} className="text-profit" />,
      }
    : config?.apiKey
      ? {
          border: "rgba(240,165,0,0.35)",
          background: "rgba(240,165,0,0.10)",
          title: "接口已保存，等待验证",
          detail: "保存后还需要手动点一次连接测试，系统才会确认密钥是否可用。",
          icon: <AlertTriangle size={18} className="text-warning-q" />,
        }
      : {
          border: "rgba(94,102,115,0.35)",
          background: "rgba(94,102,115,0.10)",
          title: "尚未配置接口",
          detail: "先录入 API Key 与 Secret，再做连接测试。",
          icon: <AlertTriangle size={18} style={{ color: "#848e9c" }} />,
        };

  const handleSave = () => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      toast.error("请完整填写 API Key 和 Secret");
      return;
    }

    saveMutation.mutate({ apiKey: apiKey.trim(), apiSecret: apiSecret.trim(), isTestnet: true });
  };

  const handleTest = () => {
    setTesting(true);
    testMutation.mutate();
  };

  return (
    <QuantLayout>
      <div className="p-4 lg:p-6 max-w-5xl space-y-5">
        <div>
          <h1 className="text-xl font-bold" style={{ color: "#eaecef" }}>API 配置</h1>
          <p className="text-sm mt-0.5" style={{ color: "#848e9c" }}>
            只展示后端真实支持的连接方式。当前主链固定走 Binance Futures Testnet。
          </p>
        </div>

        <div
          className="rounded-xl p-4 flex items-start gap-3"
          style={{
            border: `1px solid ${connectionTone.border}`,
            backgroundColor: connectionTone.background,
          }}
        >
          <div className="pt-0.5">{connectionTone.icon}</div>
          <div className="space-y-1">
            <div className="text-sm font-semibold" style={{ color: "#eaecef" }}>{connectionTone.title}</div>
            <div className="text-xs" style={{ color: "#aeb4bc" }}>{connectionTone.detail}</div>
            <div className="flex flex-wrap gap-2 pt-1 text-xs">
              <span className="px-2 py-1 rounded-lg" style={{ backgroundColor: "#1a1d21", color: "#f0b90b" }}>
                连接目标: Binance Futures Testnet
              </span>
              <span className="px-2 py-1 rounded-lg" style={{ backgroundColor: "#1a1d21", color: "#848e9c" }}>
                当前状态: {config?.isActive ? "可用" : config?.apiKey ? "待验证" : "未配置"}
              </span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.05fr_0.95fr] gap-5">
          <div className="card-q p-5 space-y-4">
            <div className="flex items-center gap-2">
              <KeyRound size={16} style={{ color: "#f0b90b" }} />
              <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>接口密钥</h2>
            </div>

            <div>
              <label className="block text-xs mb-1.5" style={{ color: "#848e9c" }}>API Key</label>
              <Input
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={config?.apiKey ? "已配置，输入新值可覆盖" : "输入 Binance API Key"}
                className="font-mono text-sm"
                style={{ backgroundColor: "#161a1e", borderColor: "#2b3139", color: "#eaecef" }}
              />
              {config?.apiKey && (
                <div className="text-xs mt-1" style={{ color: "#848e9c" }}>
                  当前保存值: {config.apiKey}
                </div>
              )}
            </div>

            <div>
              <label className="block text-xs mb-1.5" style={{ color: "#848e9c" }}>API Secret</label>
              <div className="relative">
                <Input
                  type={showSecret ? "text" : "password"}
                  value={apiSecret}
                  onChange={(event) => setApiSecret(event.target.value)}
                  placeholder={config?.apiSecret ? "已配置，输入新值可覆盖" : "输入 Binance API Secret"}
                  className="font-mono text-sm pr-10"
                  style={{ backgroundColor: "#161a1e", borderColor: "#2b3139", color: "#eaecef" }}
                />
                <button
                  type="button"
                  onClick={() => setShowSecret((current) => !current)}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{ color: "#848e9c" }}
                  aria-label={showSecret ? "隐藏密钥" : "显示密钥"}
                >
                  {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <div className="rounded-lg p-3 space-y-2" style={{ backgroundColor: "#161a1e" }}>
              <div className="flex items-center gap-2 text-sm" style={{ color: "#eaecef" }}>
                <ShieldCheck size={14} style={{ color: "#0ecb81" }} />
                后端限制说明
              </div>
              <div className="text-xs leading-6" style={{ color: "#848e9c" }}>
                1. 当前后端固定走测试网，页面不再提供伪切换开关。
                <br />
                2. API Secret 只在保存时上传，列表页始终只显示掩码。
                <br />
                3. 建议只开启交易权限，不要开放提币权限。
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                onClick={handleSave}
                disabled={saveMutation.isPending}
                className="text-sm font-medium"
                style={{ backgroundColor: "#f0b90b", color: "#0b0e11" }}
              >
                <Save size={14} className="mr-1.5" />
                {saveMutation.isPending ? "保存中" : "保存配置"}
              </Button>
              <Button
                onClick={handleTest}
                disabled={testing || !config?.apiKey}
                variant="outline"
                className="text-sm"
                style={{ borderColor: "#2b3139", color: "#eaecef", backgroundColor: "transparent" }}
              >
                {testing ? (
                  <>
                    <RefreshCw size={14} className="mr-1.5 animate-spin" />
                    测试中
                  </>
                ) : (
                  <>
                    <Wifi size={14} className="mr-1.5" />
                    测试连接
                  </>
                )}
              </Button>
            </div>

            {!config?.apiKey && (
              <div className="text-xs" style={{ color: "#848e9c" }}>
                连接测试只会读取后端已经保存的密钥，因此需要先点一次“保存配置”。
              </div>
            )}
          </div>

          <div className="space-y-4">
            <div className="card-q p-5">
              <div className="flex items-center gap-2 mb-4">
                <Wallet size={16} style={{ color: "#f0b90b" }} />
                <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>账户快照</h2>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Metric label="总权益" value={`$${totalEquity.toFixed(2)}`} />
                <Metric label="可用余额" value={`$${availableBalance.toFixed(2)}`} valueColor="#0ecb81" />
                <Metric label="占用保证金" value={`$${usedMargin.toFixed(2)}`} valueColor="#f0b90b" />
                <Metric
                  label="未实现盈亏"
                  value={`${unrealizedPnl >= 0 ? "+" : ""}$${unrealizedPnl.toFixed(2)}`}
                  valueColor={unrealizedPnl >= 0 ? "#0ecb81" : "#f6465d"}
                />
              </div>
            </div>

            <div className="card-q p-5">
              <div className="flex items-center gap-2 mb-3">
                <ShieldCheck size={16} style={{ color: "#f0b90b" }} />
                <h2 className="text-sm font-semibold" style={{ color: "#eaecef" }}>资产明细</h2>
              </div>

              {walletAssets.length === 0 ? (
                <div className="text-sm py-8 text-center" style={{ color: "#848e9c" }}>
                  当前没有可展示的账户快照
                </div>
              ) : (
                <div className="space-y-2">
                  {walletAssets.map((asset: { asset: string; balance: string; unrealizedPnl: string }) => {
                    const balance = Number(asset.balance ?? 0);
                    const pnl = Number(asset.unrealizedPnl ?? 0);
                    return (
                      <div
                        key={asset.asset}
                        className="rounded-lg px-3 py-2.5 flex items-center justify-between"
                        style={{ backgroundColor: "#161a1e" }}
                      >
                        <div>
                          <div className="text-sm font-medium" style={{ color: "#eaecef" }}>{asset.asset}</div>
                          <div className="text-xs" style={{ color: "#848e9c" }}>测试网账户资产</div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm font-num" style={{ color: "#eaecef" }}>{balance.toFixed(4)}</div>
                          <div className="text-xs font-num" style={{ color: pnl >= 0 ? "#0ecb81" : "#f6465d" }}>
                            {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </QuantLayout>
  );
}

function Metric({
  label,
  value,
  valueColor = "#eaecef",
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div className="rounded-lg px-3 py-3" style={{ backgroundColor: "#161a1e" }}>
      <div className="text-xs mb-1" style={{ color: "#848e9c" }}>{label}</div>
      <div className="text-lg font-bold font-num" style={{ color: valueColor }}>{value}</div>
    </div>
  );
}
