"""Fast smoke checks for the live trading runtime."""

from __future__ import annotations

import shutil
import socket
import uuid
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from alpha.live_discovery import LiveDiscoveryEngine
from data.downloader.binance_rest import BinanceRestDownloader
from data.downloader.ws_collector import BinanceWSCollector
from diagnostics.doctor import DoctorReport, format_report, run_preflight_checks
from execution import config as exec_config
from execution.execution_engine import ExecutionEngine
from execution.trade_logger import TradeLogger
from monitor.alert_handler import AlertHandler
from monitor.live_engine import LiveFeatureEngine
from monitor.signal_runner import SignalRunner
from utils.file_io import read_json_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REST_PING_URL = "https://fapi.binance.com/fapi/v1/ping"
WS_HOST = "fstream.binance.com"


@dataclass(frozen=True)
class SmokeCheckResult:
    name: str
    ok: bool
    detail: str
    fatal: bool = False
    duration_s: float = 0.0


@dataclass(frozen=True)
class SmokeReport:
    results: tuple[SmokeCheckResult, ...]
    strict_network: bool = False

    @property
    def has_errors(self) -> bool:
        return any((not item.ok) and item.fatal for item in self.results)

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.results if (not item.ok) and (not item.fatal))

    @property
    def total_duration_s(self) -> float:
        return sum(item.duration_s for item in self.results)


def _timed_check(
    name: str,
    func: Callable[[], tuple[bool, str]],
    *,
    fatal: bool = False,
) -> SmokeCheckResult:
    start = time.perf_counter()
    try:
        ok, detail = func()
    except Exception as exc:  # pragma: no cover - defensive summary path
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    duration_s = time.perf_counter() - start
    return SmokeCheckResult(
        name=name,
        ok=ok,
        detail=detail,
        fatal=fatal and not ok,
        duration_s=duration_s,
    )


def _probe_rest_ping(timeout_s: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(REST_PING_URL, timeout=timeout_s) as response:
            status = getattr(response, "status", 200)
        return status == 200, f"REST ping status={status}"
    except urllib.error.URLError as exc:
        return False, f"REST ping failed: {exc.reason}"
    except Exception as exc:  # pragma: no cover - platform/network variance
        return False, f"REST ping failed: {exc}"


def _probe_ws_host(timeout_s: float = 3.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((WS_HOST, 443), timeout=timeout_s):
            return True, f"TCP connect to {WS_HOST}:443 succeeded"
    except Exception as exc:  # pragma: no cover - platform/network variance
        return False, f"TCP connect to {WS_HOST}:443 failed: {exc}"


def _check_watchdog_preflight(root: Path) -> tuple[bool, str]:
    report: DoctorReport = run_preflight_checks(root)
    if report.has_errors:
        return False, format_report(report)
    if report.warning_count:
        return False, format_report(report)
    return True, "Preflight checks passed with no warnings"


def _check_watchdog_runtime(root: Path) -> tuple[bool, str]:
    ui_dist = root / "ui" / "quant-dashboard" / "dist" / "index.js"
    node_bin = shutil.which("node")
    npm_bin = shutil.which("npm")
    required_scripts = [
        root / "watchdog.py",
        root / "run_monitor.py",
        root / "run_live_discovery.py",
        root / "run_downloader.py",
    ]
    missing = [path.name for path in required_scripts if not path.exists()]
    if missing:
        return False, f"Missing child scripts: {', '.join(missing)}"

    if ui_dist.exists():
        return True, f"UI build ready at {ui_dist}"
    if node_bin and npm_bin:
        return True, "UI build missing, but node/npm are available for runtime build"
    if node_bin is None:
        return False, "Node.js is not available; watchdog will disable UI"
    return False, "UI build missing and npm is unavailable"


def _check_monitor_init(root: Path) -> tuple[bool, str]:
    storage_path = root / "data" / "storage"
    if not storage_path.exists():
        return False, f"Storage path not found: {storage_path}"

    tmp_base = root / "monitor" / "output" / "_tmp"
    tmp_base.mkdir(parents=True, exist_ok=True)
    tmp_root = tmp_base / f"btc_monitor_smoke_{uuid.uuid4().hex}"
    tmp_root.mkdir(parents=True, exist_ok=True)

    try:
        engine = LiveFeatureEngine(storage_path=str(storage_path), warmup_days=1)
        if not engine.supports_external_stream_features:
            return False, "LiveFeatureEngine does not expose external stream support"

        SignalRunner(alpha_cooldown=1)
        AlertHandler(log_dir=str(tmp_root / "logs"))
        trade_logger = TradeLogger(csv_path=tmp_root / "trades.csv")
        ExecutionEngine(
            order_manager=None,
            trade_logger=trade_logger,
            min_confidence=exec_config.MIN_CONFIDENCE,
            entry_timeout_s=exec_config.ENTRY_TIMEOUT_S,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return True, f"Monitor core initialized with storage {storage_path}"


def _check_discovery_init(root: Path) -> tuple[bool, str]:
    storage_path = root / "data" / "storage"
    engine = LiveDiscoveryEngine(
        storage_path=str(storage_path),
        symbol="BTCUSDT",
        top_n=1,
        min_triggers=30,
    )
    pending = read_json_file(root / "alpha" / "output" / "pending_rules.json", [])
    approved = read_json_file(root / "alpha" / "output" / "approved_rules.json", [])
    if not isinstance(pending, list) or not isinstance(approved, list):
        return False, "Discovery rule stores are not valid JSON lists"
    return True, (
        f"Discovery engine initialized; output={engine._output_dir} "
        f"pending={len(pending)} approved={len(approved)}"
    )


def _check_downloader_init(root: Path) -> tuple[bool, str]:
    config_path = root / "config" / "exchanges.yaml"
    downloader = BinanceRestDownloader(config_path=config_path)
    collector = BinanceWSCollector(storage_path=str(downloader.processor.storage_path))
    return True, (
        f"Downloader ready; symbol={downloader.symbol} "
        f"storage={downloader.processor.storage_path} "
        f"checkpoint={downloader.checkpoint.checkpoint_path} "
        f"streams={len(collector.streams)}"
    )


def run_smoke_suite(
    *,
    root: str | Path | None = None,
    strict_network: bool = False,
) -> SmokeReport:
    root_path = Path(root) if root is not None else PROJECT_ROOT
    results = [
        _timed_check("watchdog.preflight", lambda: _check_watchdog_preflight(root_path), fatal=True),
        _timed_check("watchdog.runtime", lambda: _check_watchdog_runtime(root_path), fatal=False),
        _timed_check("monitor.init", lambda: _check_monitor_init(root_path), fatal=True),
        _timed_check("monitor.rest_ping", _probe_rest_ping, fatal=strict_network),
        _timed_check("monitor.ws_host", _probe_ws_host, fatal=strict_network),
        _timed_check("discovery.init", lambda: _check_discovery_init(root_path), fatal=True),
        _timed_check("downloader.init", lambda: _check_downloader_init(root_path), fatal=True),
    ]
    return SmokeReport(results=tuple(results), strict_network=strict_network)


def format_smoke_report(report: SmokeReport) -> str:
    lines = []
    lines.append("| 状态 | 检查 | 耗时 | 说明 |")
    lines.append("|---|---|---:|---|")
    for item in report.results:
        if item.ok:
            status = "OK"
        elif item.fatal:
            status = "ERR"
        else:
            status = "WARN"
        detail = item.detail.replace("\n", "<br>").replace("|", "/")
        lines.append(
            f"| {status} | {item.name} | {item.duration_s:.2f}s | {detail} |"
        )
    lines.append("")
    lines.append(
        "Summary: "
        f"errors={sum(1 for item in report.results if (not item.ok) and item.fatal)} "
        f"warnings={report.warning_count} "
        f"duration={report.total_duration_s:.2f}s "
        f"strict_network={'on' if report.strict_network else 'off'}"
    )
    return "\n".join(lines)




