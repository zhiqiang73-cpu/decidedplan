"""
实时信号监控入口 (Run Monitor)

流程:
  1. 热启动：从 Parquet 加载最近 N 天历史数据，填满 3000 根滚动窗口
  2. 启动 REST 轮询协程: 每 5 分钟刷新 OI / 多空比，每 8 小时刷新资金费率
  3. 连接 Binance Futures WebSocket: btcusdt@kline_1m
  4. 每次收到一根闭合 K 线:
       → update 特征引擎
       → 运行 Phase 1 + Phase 2 信号检测
       → 报告输出
  5. 自动重连（最多 reconnect_limit 次，每次退避 reconnect_delay 秒）

用法:
  python run_monitor.py
  python run_monitor.py --storage data/storage --warmup-days 3
  python run_monitor.py --no-warmup   # 跳过热启动（测试用）

依赖:
  pip install websockets
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
import threading
import time
from urllib import parse, request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 让 import 能找到项目根目录
sys.path.insert(0, str(Path(__file__).parent))
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

try:
    import websockets
except ImportError:
    print("[ERROR] 缂哄皯 websockets 搴擄紝璇疯繍琛? pip install websockets")
    sys.exit(1)

from execution import config as exec_config
from execution.execution_engine import ExecutionEngine
from execution.order_manager import OrderManager
from execution.trade_logger import TradeLogger
from monitor.live_engine import LiveFeatureEngine
from monitor.live_catalog import (
    LIVE_STRATEGIES,
    build_strategy_status_rows,
    canonical_signal_name,
    resolve_strategy_id_from_signal_name,
)
from monitor.signal_runner import SignalRunner
from monitor.signal_health import SignalHealth
from monitor.alert_handler import AlertHandler
from utils.file_io import read_json_file, write_json_atomic
from monitor.mechanism_tracker import (
    MECHANISM_CATALOG,
    MECHANISM_CATEGORIES,
    _FAMILY_TO_MECHANISM,
    get_force_category,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

# ──────────────────── system_state helpers ────────────────────
try:
    from ui.strategy_descriptions import STRATEGY_ZH as _STRATEGY_ZH
except Exception:
    _STRATEGY_ZH: dict = {}

_STATE_PATH = PROJECT_ROOT / "monitor" / "output" / "system_state.json"
_FORCE_STATE_PATH = PROJECT_ROOT / "monitor" / "output" / "force_library_state.json"
_DISCOVERY_HEARTBEAT_STALE_S = 3 * 60

# Lookup: family -> LiveStrategySpec (for oos_win_rate + mechanism_type in strategy payload)
_LIVE_SPEC_BY_FAMILY: dict = {spec.family: spec for spec in LIVE_STRATEGIES}
_TRADES_CSV = PROJECT_ROOT / "execution" / "logs" / "trades.csv"
_DISCOVERY_HEARTBEAT = PROJECT_ROOT / "monitor" / "output" / "discovery_heartbeat.json"


def _count_today_trades() -> dict:
    """Parse trades.csv; return {family: {triggers, wins, not_filled, errors}} for UTC today."""
    today = datetime.now(timezone.utc).date().isoformat()
    stats: dict = {}
    try:
        with open(_TRADES_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entry_time = row.get("entry_time", "")
                if today not in entry_time:
                    continue
                sig = row.get("signal_name", "unknown")
                strategy_id = resolve_strategy_id_from_signal_name(sig)
                if strategy_id not in stats:
                    stats[strategy_id] = {
                        "triggers": 0,
                        "wins": 0,
                        "not_filled": 0,
                        "errors": 0,
                    }
                stats[strategy_id]["triggers"] += 1
                reason = row.get("exit_reason", "") or ""
                net_str = row.get("net_return_pct", "0") or "0"
                try:
                    net = float(net_str)
                except ValueError:
                    net = 0.0
                if reason == "not_filled":
                    stats[strategy_id]["not_filled"] += 1
                elif reason == "error":
                    stats[strategy_id]["errors"] += 1
                elif net > 0:
                    stats[strategy_id]["wins"] += 1
    except Exception:
        pass
    return stats


def _build_strategy_payload(
    status_rows: list[dict],
    today_stats: dict,
    *,
    default_status: str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    strategies = []
    daily_totals = {"triggers": 0, "wins": 0, "not_filled": 0, "errors": 0}

    for row in status_rows:
        family = str(row.get("family", ""))
        info = _STRATEGY_ZH.get(family, {})
        fam_stats = today_stats.get(
            family, {"triggers": 0, "wins": 0, "not_filled": 0, "errors": 0}
        )
        _spec = _LIVE_SPEC_BY_FAMILY.get(family)
        strategies.append(
            {
                "strategy_id": family,
                "family": family,
                "name": info.get("name", family),
                "phase": row.get("phase", ""),
                "label": row.get("label", ""),
                "canonical_signal_name": row.get("canonical_signal_name", canonical_signal_name(family)),
                "direction": info.get("direction", "unknown"),
                "status": default_status or row.get("status", "unknown"),
                "entry_conditions": info.get("entry_zh", ""),
                "exit_conditions": info.get("exit_zh", ""),
                "today": fam_stats,
                "oos_win_rate": _spec.oos_win_rate if _spec is not None else None,
                "mechanism_type": _spec.mechanism_type if _spec is not None else "",
            }
        )
        for key in daily_totals:
            daily_totals[key] += int(fam_stats.get(key, 0) or 0)

    return strategies, daily_totals


def _read_discovery_alive() -> bool:
    """Read discovery heartbeat without letting JSON/IO issues break monitor writes."""
    try:
        hb = json.loads(_DISCOVERY_HEARTBEAT.read_text(encoding="utf-8"))
        if not bool(hb.get("alive", False)):
            return False
        updated = hb.get("updated")
        if updated is None:
            return True
        return (time.time() - float(updated)) <= _DISCOVERY_HEARTBEAT_STALE_S
    except Exception:
        return False


def _write_system_state(
    close: float,
    ts_ms: int,
    runner: "SignalRunner",
    execution_engine: "ExecutionEngine",
    connected: bool = True,
    default_strategy_status: str | None = None,
) -> None:
    """Write monitor/output/system_state.json atomically. Never raises."""
    try:
        heartbeat_ts_utc = datetime.now(timezone.utc).isoformat()
        market_ts_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()

        # Discovery alive: read dedicated heartbeat written by run_live_discovery.py.
        # Using a heartbeat file avoids the mtime-heuristic problem where the discovery
        # process sleeping between scans (up to 6h) would falsely appear as dead.
        discovery_alive = _read_discovery_alive()

        # Balance from order manager — run in thread to avoid blocking the asyncio event loop
        balance = None
        try:
            if execution_engine.order_manager is not None:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                    _fut = _pool.submit(execution_engine.order_manager.get_usdt_balance)
                    try:
                        balance = _fut.result(timeout=5.0)
                    except (_cf.TimeoutError, Exception):
                        pass
        except Exception:
            pass

        # Snapshot positions and pending orders under the execution lock
        positions = []
        pending_orders = []
        try:
            with execution_engine._lock:
                for pos in execution_engine._open_positions.values():
                    # Compute unrealized P&L
                    unrealized_pnl_pct = 0.0
                    if pos.entry_price and pos.entry_price > 0 and close > 0:
                        raw_pnl = (close - pos.entry_price) / pos.entry_price * 100
                        unrealized_pnl_pct = -raw_pnl if pos.direction == "short" else raw_pnl
                    # Extract MFE/MAE and bars_held from runtime_state
                    rs = pos.runtime_state or {}
                    positions.append(
                        {
                            "strategy_id": pos.family,
                            "signal_name": pos.signal_name,
                            "raw_signal_name": str((pos.entry_snapshot or {}).get("raw_signal_name", "") or ""),
                            "family": pos.family,
                            "direction": pos.direction,
                            "qty": pos.qty,
                            "entry_price": pos.entry_price,
                            "confidence": pos.confidence,
                            "unrealized_pnl_pct": round(unrealized_pnl_pct, 4),
                            "bars_held": rs.get("bars_held", 0),
                            "mfe_pct": round(float(rs.get("mfe_pct", 0)), 4),
                            "mae_pct": round(float(rs.get("mae_pct", 0)), 4),
                            "entry_time": pos.entry_time.isoformat()
                            if pos.entry_time
                            else None,
                            "exit_due": pos.exit_due_time.isoformat()
                            if pos.exit_due_time
                            else None,
                            "dynamic_exit": pos.dynamic_exit_enabled,
                            "exit_logic": str((pos.entry_snapshot or {}).get("exit_summary", "") or ""),
                        }
                    )
                for pend in execution_engine._pending_entries.values():
                    pending_orders.append(
                        {
                            "order_id": pend.order_id,
                            "strategy_id": pend.family,
                            "signal_name": pend.signal_name,
                            "raw_signal_name": str((pend.entry_snapshot or {}).get("raw_signal_name", "") or ""),
                            "family": pend.family,
                            "direction": pend.direction,
                            "qty": pend.qty,
                            "requested_price": pend.requested_price,
                        }
                    )
        except Exception:
            pass

        # Today's trade stats per family
        today_stats = _count_today_trades()

        # Strategy status from runner
        status_rows: list[dict] = []
        current_regime: str = "--"
        try:
            status_rows = runner.strategy_status_rows()
        except Exception:
            pass
        try:
            current_regime = str(runner._regime_detector.current_regime or "--")
        except Exception:
            pass

        strategies, daily_totals = _build_strategy_payload(
            status_rows,
            today_stats,
            default_status=default_strategy_status,
        )

        # Signal pipeline stats from decision logger (injected into execution_engine)
        pipeline_stats = {}
        try:
            dl = getattr(execution_engine, "_decision_logger", None)
            if dl is not None:
                pipeline_stats = dl.get_stats()
        except Exception:
            pass

        state = {
            "timestamp": heartbeat_ts_utc,
            "market_timestamp": market_ts_utc,
            "monitor_alive": True,
            "discovery_alive": discovery_alive,
            "connected": connected,
            "symbol": "BTCUSDT",
            "price": close,
            "balance": balance,
            "regime": current_regime,
            "positions": positions,
            "pending_orders": pending_orders,
            "strategies": strategies,
            "daily_totals": daily_totals,
            "signal_pipeline": pipeline_stats,
        }

        write_json_atomic(_STATE_PATH, state, ensure_ascii=False, indent=2)
        _write_force_library_state()

    except Exception:
        pass


def _write_force_library_state() -> None:
    """Write monitor/output/force_library_state.json atomically. Never raises."""
    try:
        # 1. Build mechanism -> [family] mapping from _FAMILY_TO_MECHANISM
        mech_to_families: dict = {}
        for family, mech in _FAMILY_TO_MECHANISM.items():
            mech_to_families.setdefault(mech, []).append(family)

        # 2. oos_win_rate per family from LIVE_STRATEGIES
        family_to_oos: dict = {spec.family: spec.oos_win_rate for spec in LIVE_STRATEGIES}

        # 3. Concentration: count open positions per force category
        concentration: dict = {cat: 0 for cat in MECHANISM_CATEGORIES}
        try:
            state = read_json_file(_STATE_PATH, {})
            for pos in state.get("positions", []):
                fam = pos.get("family", "")
                mech = _FAMILY_TO_MECHANISM.get(fam, "generic_alpha")
                cat = get_force_category(mech)
                concentration[cat] = concentration.get(cat, 0) + 1
        except Exception:
            pass

        # 4. Group mechanisms by category
        cat_to_mechs: dict = {}
        for mech_id, cfg in MECHANISM_CATALOG.items():
            cat_to_mechs.setdefault(cfg.category, []).append(mech_id)

        # 5. Build categories list
        categories = []
        for cat_id, cat_desc in MECHANISM_CATEGORIES.items():
            # MECHANISM_CATEGORIES values are "DisplayName — description"
            if " — " in cat_desc:
                cat_name, cat_description = cat_desc.split(" — ", 1)
            else:
                cat_name = cat_id
                cat_description = cat_desc

            mechanisms = []
            for mech_id in cat_to_mechs.get(cat_id, []):
                cfg = MECHANISM_CATALOG[mech_id]
                families = mech_to_families.get(mech_id, [])
                rates = [
                    family_to_oos[f]
                    for f in families
                    if f in family_to_oos and family_to_oos[f] is not None
                ]
                oos_wr = round(sum(rates) / len(rates), 1) if rates else None
                phys = cfg.physics if isinstance(cfg.physics, dict) else {}
                mechanisms.append({
                    "id": mech_id,
                    "display_name": cfg.display_name or mech_id,
                    "essence": phys.get("essence", ""),
                    "why_temporary": phys.get("why_temporary", ""),
                    "edge_source": phys.get("edge_source", ""),
                    "strategies": families,
                    "oos_win_rate": oos_wr,
                    "relations": {
                        "reinforces": cfg.relations.get("reinforces", []) if isinstance(cfg.relations, dict) else [],
                        "conflicts_with": cfg.relations.get("conflicts_with", []) if isinstance(cfg.relations, dict) else [],
                        "often_follows": cfg.relations.get("often_follows", []) if isinstance(cfg.relations, dict) else [],
                    },
                })

            categories.append({
                "id": cat_id,
                "name": cat_name,
                "description": cat_description,
                "mechanisms": mechanisms,
            })

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "categories": categories,
            "concentration": concentration,
        }
        write_json_atomic(_FORCE_STATE_PATH, payload, ensure_ascii=False, indent=2)

    except Exception:
        pass


async def _heartbeat_state_publisher(
    runner: "SignalRunner",
    execution_engine: "ExecutionEngine",
    runtime_state: dict[str, float | int | bool | None],
    interval_s: float = 15.0,
) -> None:
    """Keep system_state.json fresh even when the market stream is disconnected."""
    while True:
        now_ms = int(time.time() * 1000)
        state_ts_ms = int(runtime_state.get("market_ts_ms") or now_ms)
        state_price = float(runtime_state.get("price") or 0.0)
        state_connected = bool(runtime_state.get("connected"))
        warmup_in_progress = bool(runtime_state.get("warming_up"))
        _write_system_state(
            state_price,
            state_ts_ms,
            runner,
            execution_engine,
            connected=state_connected,
            default_strategy_status="warming_up" if warmup_in_progress else None,
        )
        await asyncio.sleep(interval_s)


def _read_last_state_snapshot() -> tuple[float | None, int | None]:
    """Best-effort read of the last known price/timestamp for UI fallback."""
    try:
        prev = read_json_file(_STATE_PATH, {})
        price = prev.get("price")
        ts_text = prev.get("timestamp")
        ts_ms = None
        if ts_text:
            ts_ms = int(datetime.fromisoformat(ts_text).timestamp() * 1000)
        return (
            float(price) if price is not None else None,
            ts_ms,
        )
    except Exception:
        return None, None


logger = logging.getLogger(__name__)


def setup_logging(log_dir: str | Path) -> None:
    log_dir_path = _resolve_project_path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir_path / "monitor.log", encoding="utf-8"),
        ],
        force=True,
    )

# ──────────────────── 常量 (Constants) ────────────────────
# 币安WebSocket端点列表（自动切换到可用端点）
WEBSOCKET_ENDPOINTS = [
    "wss://fstream.binance.com/ws/btcusdt@kline_1m",
    "wss://fstream1.binance.com/ws/btcusdt@kline_1m",
    "wss://fstream2.binance.com/ws/btcusdt@kline_1m",
    "wss://fstream3.binance.com/ws/btcusdt@kline_1m",
]
REST_BASE = "https://fapi.binance.com"

HEARTBEAT_EVERY = 60  # 姣?N 鏍?K 绾挎墦鍗板績璺?
RECONNECT_DELAY = 5  # 断线后等待秒数（指数退避 × 2）
RECONNECT_LIMIT = 20  # 最多重连次数
WS_HEARTBEAT_S = 30
WS_CONNECT_TIMEOUT_S = 20
WS_IDLE_TIMEOUT_S = 180


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BTC/USDT live monitor")
    parser.add_argument("--storage", default="data/storage", help="Parquet storage root")
    parser.add_argument("--warmup-days", type=int, default=3, help="Warmup lookback in days")
    parser.add_argument("--no-warmup", action="store_true", help="Skip historical warmup")
    parser.add_argument(
        "--alpha-cooldown",
        type=int,
        default=1,
        help="Alpha rule cooldown in bars; research defaults to 1 for denser sampling",
    )
    parser.add_argument(
        "--p2-startup-grace-bars",
        type=int,
        default=3,
        help="Block P2 execution during the first N live bars after startup",
    )
    parser.add_argument(
        "--p2-group-cooldown-min",
        type=int,
        default=5,
        help="Minimum cooldown minutes per P2 composite group (was 10; AdaptiveCooldown handles throttling)",
    )
    parser.add_argument(
        "--p2-max-groups-per-bar",
        type=int,
        default=3,
        help="Max number of P2 composite groups allowed per closed bar (was 2; force concentration in exec is the real limit)",
    )
    parser.add_argument("--log-dir", default="monitor/output", help="Directory for monitor logs")
    return parser.parse_args()


def _build_order_manager_with_timeout(timeout_s: float = 20.0) -> OrderManager | None:
    """Initialize the REST order manager without blocking monitor startup forever."""
    if not exec_config.ENABLED:
        return None

    result: dict[str, object] = {}

    def _worker() -> None:
        try:
            result["manager"] = OrderManager(
                api_key=exec_config.API_KEY,
                api_secret=exec_config.API_SECRET,
                testnet=exec_config.TESTNET,
                symbol=exec_config.SYMBOL,
            )
        except Exception as exc:  # pragma: no cover - defensive thread handoff
            result["error"] = exc

    thread = threading.Thread(target=_worker, name="order-manager-init", daemon=True)
    thread.start()
    thread.join(timeout_s)

    if thread.is_alive():
        logger.warning(
            f"[EXEC] OrderManager init timed out after {timeout_s:.0f}s; "
            "continuing in paper mode"
        )
        return None

    error_obj = result.get("error")
    if error_obj is not None:
        raise error_obj  # type: ignore[misc]

    manager = result.get("manager")
    return manager if isinstance(manager, OrderManager) else None


# ──────────────────── REST 辅助数据 ────────────────────


def _fetch_json_sync(url: str, params: dict | None = None) -> dict:
    try:
        query = parse.urlencode(params or {}, doseq=True)
        req_url = f"{url}?{query}" if query else url
        with request.urlopen(req_url, timeout=5) as resp:
            if getattr(resp, "status", 200) == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug(f"REST 璇锋眰澶辫触 {url}: {exc}")
    return {}


async def _fetch_json(url: str, params: dict | None = None) -> dict:
    return await asyncio.to_thread(_fetch_json_sync, url, params)


async def poll_side_data(
    engine: LiveFeatureEngine,
    interval_oi: int = 60,  # was 300; 4/6 alpha rules depend on OI freshness
    interval_fr: int = 300,  # keep funding_rate inside the 10min freshness window
) -> None:
    """
    鍚庡彴鍗忕▼锛氬畾鏈熶粠 Binance REST API 鎷夊彇杈呭姪鏁版嵁骞舵洿鏂扮壒寰佸紩鎿庣紦瀛樸€?
    """
    last_fr = 0

    while True:
        now = time.time()

        oi_data = await _fetch_json(
            f"{REST_BASE}/fapi/v1/openInterest",
            params={"symbol": "BTCUSDT"},
        )
        oi_val = None
        if oi_data:
            try:
                oi_val = float(oi_data.get("openInterest", 0))
            except (ValueError, TypeError):
                pass

        lsr_data = await _fetch_json(
            f"{REST_BASE}/futures/data/globalLongShortAccountRatio",
            params={"symbol": "BTCUSDT", "period": "5m", "limit": 1},
        )
        lsr_val = long_val = short_val = None
        if lsr_data and isinstance(lsr_data, list) and len(lsr_data) > 0:
            try:
                latest = lsr_data[0]
                lsr_val = float(latest.get("longShortRatio", 0))
                long_val = float(latest.get("longAccount", 0))
                short_val = float(latest.get("shortAccount", 0))
            except (ValueError, TypeError):
                pass

        fr_val = None
        if now - last_fr >= interval_fr:
            fr_data = await _fetch_json(
                f"{REST_BASE}/fapi/v1/premiumIndex",
                params={"symbol": "BTCUSDT"},
            )
            if fr_data:
                try:
                    fr_val = float(fr_data.get("lastFundingRate", 0))
                    last_fr = now
                except (ValueError, TypeError):
                    pass

        engine.update_side_data(
            funding_rate=fr_val,
            open_interest=oi_val,
            long_short_ratio=lsr_val,
            long_account=long_val,
            short_account=short_val,
        )

        if oi_val is not None:
            logger.debug(
                f"[REST] OI={oi_val:,.0f} BTC | LSR={lsr_val:.3f}"
                if lsr_val
                else "[REST] OI 鏇存柊"
            )

        await asyncio.sleep(interval_oi)


# ──────────────────── WebSocket 主循环 ────────────────────


async def run_websocket(
    engine: LiveFeatureEngine,
    runner: SignalRunner,
    alerter: AlertHandler,
    execution_engine: ExecutionEngine,
    runtime_state: dict[str, float | int | bool | None],
) -> None:
    """Connect to the Binance WebSocket, process closed 1m klines, and auto-reconnect."""
    bar_count = 0
    reconnect_n = 0
    endpoint_idx = 0
    delay = RECONNECT_DELAY
    last_close: float | None = None
    last_ts_ms: int | None = None

    while reconnect_n <= RECONNECT_LIMIT:
        ws_url = WEBSOCKET_ENDPOINTS[endpoint_idx % len(WEBSOCKET_ENDPOINTS)]

        try:
            logger.info(f"杩炴帴 WebSocket: {ws_url}")
            async with websockets.connect(
                ws_url,
                open_timeout=WS_CONNECT_TIMEOUT_S,
                ping_interval=WS_HEARTBEAT_S,
                ping_timeout=WS_HEARTBEAT_S,
                close_timeout=10,
                max_queue=16,
            ) as ws:
                reconnect_n = 0
                delay = RECONNECT_DELAY
                endpoint_idx = 0
                runtime_state["connected"] = True
                logger.info("WebSocket 宸茶繛鎺ワ紝寮€濮嬬洃鍚?K 绾?..")

                while True:
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=WS_IDLE_TIMEOUT_S
                        )
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError(
                            f"{WS_IDLE_TIMEOUT_S}s 鍐呮湭鏀跺埌浠讳綍 WebSocket 鏁版嵁"
                        ) from exc

                    if not raw:
                        continue

                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if data.get("e") != "kline":
                        continue

                    kline = data["k"]
                    if not kline.get("x", False):
                        continue

                    bar_count += 1
                    ts_ms = int(kline.get("t", 0))
                    close = float(kline.get("c", 0))
                    last_ts_ms = ts_ms
                    last_close = close
                    runtime_state["market_ts_ms"] = ts_ms
                    runtime_state["price"] = close
                    runtime_state["connected"] = True

                    try:
                        df = engine.update(kline)
                    except Exception as exc:
                        logger.warning(f"鐗瑰緛鏇存柊寮傚父: {exc}")
                        continue

                    latest_features = (
                        df.iloc[-1] if df is not None and len(df) > 0 else None
                    )
                    execution_engine.on_bar(latest_features)

                    if bar_count % HEARTBEAT_EVERY == 0:
                        alerter.send_heartbeat(bar_count, ts_ms)

                    try:
                        raw_alerts, composite_alerts = runner.run(df)
                    except Exception as exc:
                        logger.warning(f"淇″彿妫€娴嬪紓甯? {exc}")
                        continue

                    # 同步市场状态到执行引擎（regime + flow_type + trend）
                    execution_engine.update_market_state(
                        runner.current_regime,
                        runner.current_flow,
                        trend_direction=runner.current_trend,
                    )

                    if raw_alerts:
                        alerter.send_batch(raw_alerts)

                    if composite_alerts:
                        tradeable = [
                            a
                            for a in composite_alerts
                            if a.get("confidence", 1) >= 2 or a.get("phase") == "P1"
                        ]
                        if tradeable:
                            for alert in tradeable:
                                execution_engine.on_signal(alert, latest_features)

                    _write_system_state(close, ts_ms, runner, execution_engine)

        except (
            asyncio.TimeoutError,
            websockets.exceptions.WebSocketException,
            ConnectionResetError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as exc:
            reconnect_n += 1
            endpoint_idx += 1

            prev_close, prev_ts = _read_last_state_snapshot()
            state_ts = last_ts_ms or prev_ts or int(time.time() * 1000)
            state_close = last_close if last_close is not None else (prev_close or 0.0)
            runtime_state["market_ts_ms"] = state_ts
            runtime_state["price"] = state_close
            runtime_state["connected"] = False
            _write_system_state(
                state_close,
                state_ts,
                runner,
                execution_engine,
                connected=False,
            )

            next_ep = WEBSOCKET_ENDPOINTS[endpoint_idx % len(WEBSOCKET_ENDPOINTS)]
            logger.warning(
                f"WebSocket reconnect after {type(exc).__name__}: {exc}; "
                f"next endpoint={next_ep.split('//')[1].split('/')[0]} "
                f"in {delay:.0f}s ({reconnect_n}/{RECONNECT_LIMIT})"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

        except Exception as exc:
            prev_close, prev_ts = _read_last_state_snapshot()
            state_ts = last_ts_ms or prev_ts or int(time.time() * 1000)
            state_close = last_close if last_close is not None else (prev_close or 0.0)
            runtime_state["market_ts_ms"] = state_ts
            runtime_state["price"] = state_close
            runtime_state["connected"] = False
            _write_system_state(
                state_close,
                state_ts,
                runner,
                execution_engine,
                connected=False,
            )
            logger.error(f"Unexpected fatal monitor error: {exc}", exc_info=True)
            raise

    logger.error(f"Reached reconnect limit ({RECONNECT_LIMIT}); monitor exiting")


# ──────────────────── 主函数 (Main) ────────────────────


async def main():
    args = parse_args()
    log_dir = _resolve_project_path(args.log_dir)
    storage_path = _resolve_project_path(args.storage)
    setup_logging(log_dir)

    print()
    print("=" * 60)
    print("  BTC/USDT 瀹炴椂淇″彿鐩戞帶")
    print(f"  启动时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  存储路径: {storage_path}")
    print(f"  Alpha 鍐峰嵈鏈? {args.alpha_cooldown} bars")
    print(
        "  P2 startup guard: "
        f"{args.p2_startup_grace_bars} bars | "
        f"group cooldown {args.p2_group_cooldown_min} min | "
        f"max groups/bar {args.p2_max_groups_per_bar}"
    )
    print("=" * 60)
    print()

    engine = LiveFeatureEngine(storage_path=str(storage_path), warmup_days=args.warmup_days)
    runner = SignalRunner(
        alpha_cooldown=args.alpha_cooldown,
        p2_startup_grace_bars=args.p2_startup_grace_bars,
        p2_group_cooldown_min=args.p2_group_cooldown_min,
        p2_max_groups_per_bar=args.p2_max_groups_per_bar,
    )
    alerter = AlertHandler(log_dir=str(log_dir))
    trade_logger = TradeLogger()

    # Write an initial state immediately so UI shows a live monitor status
    # even if execution connectivity is slow.
    _last_price: float | None = None
    _last_balance: float | None = None
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        import urllib.request

        # 1. 浠?REST API 鎷夊綋鍓嶄环鏍硷紙涓嶄緷璧?WebSocket锛?
        for _api_host in ["fapi.binance.com", "fapi1.binance.com", "fapi2.binance.com"]:
            try:
                _url = f"https://{_api_host}/fapi/v1/ticker/price?symbol=BTCUSDT"
                _resp = urllib.request.urlopen(_url, timeout=8)
                _data = _json.loads(_resp.read())
                _last_price = float(_data["price"])
                logger.info(
                    f"[INIT] 褰撳墠 BTC 浠锋牸: {_last_price:.1f} (from {_api_host})"
                )
                break
            except Exception:
                continue

        # 2. 浠庝氦鏄撴墍鎷変綑棰?
        # 3. Fallback: 浠庝笂娆＄姸鎬佺户鎵?
        if _last_price is None or _last_balance is None:
            try:
                _prev = read_json_file(_STATE_PATH, {})
                if _last_price is None:
                    _last_price = _prev.get("price")
                if _last_balance is None:
                    _last_balance = _prev.get("balance")
            except Exception:
                pass
        status_rows = build_strategy_status_rows(lambda _family, _direction: False)
        strategies, daily_totals = _build_strategy_payload(
            status_rows,
            {},
            default_status="warming_up",
        )
        _initial_state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "monitor_alive": True,
            "discovery_alive": _read_discovery_alive(),
            "connected": False,
            "symbol": "BTCUSDT",
            "price": _last_price,
            "balance": _last_balance,
            "regime": "--",
            "positions": [],
            "pending_orders": [],
            "strategies": strategies,
            "daily_totals": daily_totals,
        }
        write_json_atomic(_STATE_PATH, _initial_state, ensure_ascii=False, indent=2)
    except Exception:
        pass

    order_manager: OrderManager | None = None
    if exec_config.ENABLED:
        try:
            order_manager = _build_order_manager_with_timeout(timeout_s=20.0)
        except Exception as exc:
            logger.warning(f"[EXEC] OrderManager init failed: {exc}")

    execution_engine = ExecutionEngine(
        order_manager=order_manager,
        trade_logger=trade_logger,
        min_confidence=exec_config.MIN_CONFIDENCE,
        entry_timeout_s=exec_config.ENTRY_TIMEOUT_S,
    )

    # Wire signal health tracking for auto-degradation of failing strategies
    signal_health = SignalHealth()
    runner.set_signal_health(signal_health)
    execution_engine.set_signal_health(signal_health)
    execution_engine.set_signal_runner(runner)

    # Wire decision audit logger for full signal pipeline transparency
    from monitor.decision_logger import DecisionLogger
    decision_logger = DecisionLogger(log_dir=args.log_dir)
    execution_engine.set_decision_logger(decision_logger)

    # Wire conviction engine (adaptive brain) — Phase 0: shadow mode only
    from monitor.conviction_engine import ConvictionEngine
    conviction_engine = ConvictionEngine(shadow_mode=True)
    execution_engine.set_conviction_engine(conviction_engine)
    logger.info(
        "[INIT] ConvictionEngine wired (shadow_mode=True): %s",
        conviction_engine.status_summary(),
    )
    logger.info("[INIT] SignalHealth + AdaptiveCooldown + DecisionLogger + Brain wired")

    runtime_state: dict[str, float | int | bool | None] = {
        "price": _last_price or 0.0,
        "market_ts_ms": int(time.time() * 1000),
        "connected": False,
        "warming_up": not args.no_warmup,
    }

    if execution_engine.order_manager is not None:
        try:
            _last_balance = execution_engine.order_manager.get_usdt_balance()
            logger.info(f"[INIT] 当前余额: {_last_balance:.2f} USDT")
            prev_state = read_json_file(_STATE_PATH, {})
            prev_state["balance"] = _last_balance
            write_json_atomic(_STATE_PATH, prev_state, ensure_ascii=False, indent=2)
        except Exception:
            pass

    heartbeat_task = asyncio.create_task(
        _heartbeat_state_publisher(runner, execution_engine, runtime_state)
    )
    try:
        if not args.no_warmup:
            await asyncio.to_thread(engine.warmup)
            runtime_state["warming_up"] = False
        else:
            logger.info("Skipping warmup (--no-warmup)")
            runtime_state["warming_up"] = False
    except Exception:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        raise

    ws_task = asyncio.create_task(
        run_websocket(engine, runner, alerter, execution_engine, runtime_state)
    )
    poll_task = asyncio.create_task(poll_side_data(engine))

    done, pending = await asyncio.wait(
        [heartbeat_task, ws_task, poll_task], return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    if heartbeat_task in done:
        exc = heartbeat_task.exception()
        if exc is not None:
            logger.error(f"Heartbeat task exited with error: {exc}", exc_info=exc)
            raise exc
        raise RuntimeError("Heartbeat task exited unexpectedly")

    if ws_task in done:
        exc = ws_task.exception()
        if exc is not None:
            logger.error(f"WebSocket task exited with error: {exc}", exc_info=exc)
            raise exc
        raise RuntimeError("WebSocket task exited unexpectedly")

    exc = poll_task.exception()
    if exc is not None:
        logger.error(f"Side-data polling task exited with error: {exc}", exc_info=exc)
        raise exc
    raise RuntimeError("Side-data polling task exited unexpectedly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\\n[Monitor] interrupted by user")








