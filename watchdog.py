"""Watchdog entrypoint for the live BTC system."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from diagnostics.doctor import format_report, run_preflight_checks

logger = logging.getLogger("watchdog")
ROOT = Path(__file__).resolve().parent


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def _clean_path_arg(value: str) -> str:
    return value.strip().strip('"').strip("'")


def setup_logging(log_dir: str) -> None:
    log_dir_path = _resolve_project_path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir_path / "watchdog.log", encoding="utf-8"),
        ],
        force=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run and guard the live monitor, discovery engine, UI server, and websocket collector.",
        epilog="Pass extra args to run_monitor.py after --",
    )
    parser.add_argument("--log-dir", default="monitor/output", help="Directory for watchdog logs")
    parser.add_argument("--max-restarts", type=int, default=200, help="Max restart attempts per child process")
    parser.add_argument("--delay", type=int, default=30, help="Base restart delay in seconds")
    parser.add_argument("--no-discovery", action="store_true", help="Do not start live discovery")
    parser.add_argument("--no-data-sync", action="store_true", help="Do not start websocket data collection")
    parser.add_argument("--discovery-interval", type=float, default=6.0, help="Discovery interval in hours")
    parser.add_argument("--data-days", type=int, default=30, help="Lookback window for discovery")
    parser.add_argument("--eth-discovery", action="store_true", help="Also start ETHUSDT live discovery")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip run_doctor-style preflight checks")
    parser.add_argument("--no-ui", action="store_true", help="Do not start the dashboard UI server")
    parser.add_argument("monitor_args", nargs=argparse.REMAINDER, help="Extra args forwarded to run_monitor.py")
    return parser.parse_args()


class ProcessGuard:
    def __init__(
        self,
        name: str,
        cmd: list[str],
        max_restarts: int,
        delay: int,
        log_dir: str | Path,
        *,
        cwd: str | Path,
        env_overrides: dict[str, str] | None = None,
        hide_window: bool = False,
    ):
        self.name = name
        self.cmd = cmd
        self.max_restarts = max_restarts
        self.delay = delay
        self.log_dir = _resolve_project_path(log_dir) / "processes"
        self.cwd = _resolve_project_path(cwd)
        self.env_overrides = env_overrides or {}
        self.hide_window = hide_window
        self.proc: subprocess.Popen | None = None
        self._log_handle = None
        self.restart_count = 0
        self.start_ts = 0.0

    def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self._log_handle is not None and not self._log_handle.closed:
            self._log_handle.close()
        self._log_handle = open(self.log_dir / f"{self.name}.log", "w", encoding="utf-8")
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.update(self.env_overrides)
        creationflags = 0
        if self.hide_window and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(self.cwd),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
        self.start_ts = time.time()
        logger.info("[%s] started pid=%s cmd=%s", self.name, self.proc.pid, " ".join(self.cmd))

    def check_and_restart(self) -> bool:
        if self.proc is None:
            return True

        rc = self.proc.poll()
        if rc is None:
            return True

        runtime = time.time() - self.start_ts
        if rc == 0:
            logger.info("[%s] exited normally after %.0fs", self.name, runtime)
            return True

        self.restart_count += 1
        if self.restart_count > self.max_restarts:
            logger.error("[%s] exceeded max restarts (%s)", self.name, self.max_restarts)
            return False

        wait_s = self.delay if runtime >= 60 else min(self.delay * 2, 120)
        logger.warning(
            "[%s] exited with code=%s after %.1fs; restarting in %ss (%s/%s)",
            self.name,
            rc,
            runtime,
            wait_s,
            self.restart_count,
            self.max_restarts,
        )
        time.sleep(wait_s)
        self.start()
        return True

    def terminate(self) -> None:
        if self.proc and self.proc.poll() is None:
            logger.info("[%s] stopping pid=%s", self.name, self.proc.pid)
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_handle is not None and not self._log_handle.closed:
            self._log_handle.close()


def main() -> None:
    args = parse_args()
    args.log_dir = _clean_path_arg(args.log_dir)
    setup_logging(args.log_dir)
    project_root = ROOT

    if not args.skip_preflight:
        report = run_preflight_checks(project_root)
        for line in format_report(report).splitlines():
            logger.info("[preflight] %s", line)
        if report.has_errors:
            logger.error("Preflight failed. Run python run_doctor.py for details.")
            raise SystemExit(2)

    ui_dir = project_root / "ui" / "quant-dashboard"
    ui_entry = ui_dir / "dist" / "index.js"
    ui_enabled = ui_entry.exists() and not args.no_ui
    if ui_enabled:
        logger.info("[ui] Using Quant Dashboard production server at %s", ui_entry)
    elif args.no_ui:
        logger.info("[ui] dashboard disabled by --no-ui")
    else:
        logger.warning("[ui] %s is missing; dashboard disabled", ui_entry)

    extra = [_clean_path_arg(a) for a in args.monitor_args if a != "--"]
    monitor_cmd = [sys.executable, "run_monitor.py", *extra]
    discovery_cmd = [
        sys.executable,
        "run_live_discovery.py",
        "--watch",
        "--symbol",
        "BTCUSDT",
        "--interval",
        str(args.discovery_interval),
        "--data-days",
        str(args.data_days),
    ]
    discovery_eth_cmd = [
        sys.executable,
        "run_live_discovery.py",
        "--watch",
        "--symbol",
        "ETHUSDT",
        "--interval",
        str(args.discovery_interval),
        "--data-days",
        str(args.data_days),
    ]
    ui_cmd = ["node", "dist/index.js"] if ui_enabled else []
    sync_cmd = [sys.executable, "run_ws.py", "--storage-path", "data/storage", "--flush-interval", "60"]

    logger.info("=" * 60)
    logger.info("live watchdog starting")
    logger.info("monitor: %s", " ".join(monitor_cmd))
    if not args.no_discovery:
        logger.info("discovery: %s", " ".join(discovery_cmd))
        logger.info("discovery interval: %.1f hours", args.discovery_interval)
    else:
        logger.info("discovery: disabled")
    if not args.no_data_sync:
        logger.info("data sync: liquidations + book_ticker + agg_trades + mark_price")
    else:
        logger.info("data sync: disabled")
    if ui_enabled:
        logger.info("ui: http://127.0.0.1:8050  (Quant Dashboard)")
    else:
        logger.info("ui: disabled")
    logger.info("max restarts: %s  base delay: %ss", args.max_restarts, args.delay)
    logger.info("=" * 60)

    monitor_guard = ProcessGuard(
        "monitor",
        monitor_cmd,
        args.max_restarts,
        args.delay,
        args.log_dir,
        cwd=project_root,
    )
    monitor_guard.start()

    discovery_guard: ProcessGuard | None = None
    if not args.no_discovery:
        discovery_guard = ProcessGuard(
            "discovery-btc",
            discovery_cmd,
            args.max_restarts,
            args.delay,
            args.log_dir,
            cwd=project_root,
        )
        discovery_guard.start()

    discovery_eth_guard: ProcessGuard | None = None
    if not args.no_discovery and args.eth_discovery:
        discovery_eth_guard = ProcessGuard(
            "discovery-eth",
            discovery_eth_cmd,
            args.max_restarts,
            args.delay,
            args.log_dir,
            cwd=project_root,
        )
        discovery_eth_guard.start()

    ui_guard: ProcessGuard | None = None
    if ui_enabled:
        ui_guard = ProcessGuard(
            "ui",
            ui_cmd,
            args.max_restarts,
            args.delay,
            args.log_dir,
            cwd=ui_dir,
            env_overrides={"NODE_ENV": "production", "PORT": "8050"},
            hide_window=True,
        )
        try:
            ui_guard.start()
        except FileNotFoundError as exc:
            logger.warning("[ui] failed to start dashboard: %s; dashboard disabled", exc)
            ui_guard = None

    sync_guard: ProcessGuard | None = None
    if not args.no_data_sync:
        sync_guard = ProcessGuard(
            "data-sync",
            sync_cmd,
            args.max_restarts,
            args.delay,
            args.log_dir,
            cwd=project_root,
        )
        sync_guard.start()

    try:
        while True:
            time.sleep(5)
            if not monitor_guard.check_and_restart():
                logger.error("monitor could not be recovered; shutting down")
                break
            if discovery_guard is not None and not discovery_guard.check_and_restart():
                logger.warning("discovery-btc could not be recovered; leaving discovery disabled")
                discovery_guard = None
            if discovery_eth_guard is not None and not discovery_eth_guard.check_and_restart():
                logger.warning("discovery-eth could not be recovered; leaving ETH discovery disabled")
                discovery_eth_guard = None
            if sync_guard is not None and not sync_guard.check_and_restart():
                logger.warning("data-sync could not be recovered; leaving collection disabled")
                sync_guard = None
            if ui_guard is not None and not ui_guard.check_and_restart():
                logger.warning("ui could not be recovered; leaving dashboard disabled")
                ui_guard = None
    except KeyboardInterrupt:
        logger.info("watchdog interrupted by user")
    finally:
        monitor_guard.terminate()
        if discovery_guard is not None:
            discovery_guard.terminate()
        if discovery_eth_guard is not None:
            discovery_eth_guard.terminate()
        if sync_guard is not None:
            sync_guard.terminate()
        if ui_guard is not None:
            ui_guard.terminate()
        logger.info("watchdog stopped")


if __name__ == "__main__":
    main()





