"""Preflight checks for the live trading system."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from execution import config as exec_config
from execution.trade_logger import _CSV_HEADER
from monitor.alpha_rules import validate_approved_rule_pool
from monitor.live_catalog import LIVE_STRATEGIES, build_strategy_status_rows, live_strategy_families
from monitor.live_engine import LiveFeatureEngine
from monitor.exit_policy_config import has_explicit_exit_params
from ui.strategy_descriptions import STRATEGY_ZH
from utils.file_io import read_json_file


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    fatal: bool = False


@dataclass(frozen=True)
class DoctorReport:
    results: tuple[CheckResult, ...]

    @property
    def has_errors(self) -> bool:
        return any((not item.ok) and item.fatal for item in self.results)

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.results if (not item.ok) and (not item.fatal))


def _check_python_dependencies() -> CheckResult:
    required = ("websockets", "aiohttp", "pandas", "numpy", "pyarrow")
    missing = []
    for name in required:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    if missing:
        return CheckResult(
            name="python_dependencies",
            ok=False,
            fatal=True,
            detail=f"Missing packages: {', '.join(missing)}",
        )
    return CheckResult("python_dependencies", True, "Core runtime dependencies are importable.")


def _check_runtime_directories(root: Path) -> CheckResult:
    required = [
        root / "monitor" / "output",
        root / "execution" / "logs",
        root / "alpha" / "output",
        root / "logs",
        root / "data" / "storage",
    ]
    failed = []
    for path in required:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            failed.append(str(path))
    if failed:
        return CheckResult(
            name="runtime_directories",
            ok=False,
            fatal=True,
            detail=f"Failed to create required directories: {', '.join(failed)}",
        )
    return CheckResult("runtime_directories", True, "Runtime directories are ready.")


def _check_live_strategy_catalog() -> CheckResult:
    live_families = set(live_strategy_families())
    desc_families = set(STRATEGY_ZH.keys())
    missing_desc = sorted(live_families - desc_families)
    extra_desc = sorted(desc_families - live_families)
    status_rows = build_strategy_status_rows(has_explicit_exit_params)
    if len(status_rows) != len(LIVE_STRATEGIES):
        return CheckResult(
            name="live_strategy_catalog",
            ok=False,
            fatal=True,
            detail="Catalog row count does not match live strategy count.",
        )
    if missing_desc:
        return CheckResult(
            name="live_strategy_catalog",
            ok=False,
            fatal=True,
            detail=f"Missing UI descriptions for: {', '.join(missing_desc)}",
        )
    detail = f"{len(LIVE_STRATEGIES)} live strategies are cataloged."
    if extra_desc:
        return CheckResult(
            name="live_strategy_catalog",
            ok=False,
            fatal=False,
            detail=f"{detail} Extra UI descriptions not used by live catalog: {', '.join(extra_desc)}",
        )
    return CheckResult("live_strategy_catalog", True, detail)


def _check_live_feature_parity() -> CheckResult:
    engine = LiveFeatureEngine(storage_path="data/storage", warmup_days=1)
    if not engine.supports_external_stream_features:
        return CheckResult(
            name="live_feature_parity",
            ok=False,
            fatal=True,
            detail="LiveFeatureEngine does not expose external-stream feature support.",
        )
    return CheckResult(
        name="live_feature_parity",
        ok=True,
        detail="LiveFeatureEngine includes parquet-backed microstructure feature enrichment.",
    )


def _check_alpha_rule_files(root: Path) -> CheckResult:
    pending = read_json_file(root / "alpha" / "output" / "pending_rules.json", [])
    approved = read_json_file(root / "alpha" / "output" / "approved_rules.json", [])
    if not isinstance(pending, list) or not isinstance(approved, list):
        return CheckResult(
            name="alpha_rule_files",
            ok=False,
            fatal=True,
            detail="Pending/approved alpha files are not valid JSON lists.",
        )
    issues = validate_approved_rule_pool(approved)
    if issues:
        preview = " | ".join(issues[:3])
        more = "" if len(issues) <= 3 else f" | +{len(issues) - 3} more"
        return CheckResult(
            name="alpha_rule_files",
            ok=False,
            fatal=True,
            detail=f"Approved alpha pool has blocking issues: {preview}{more}",
        )
    return CheckResult(
        name="alpha_rule_files",
        ok=True,
        detail=f"Alpha rule stores readable. pending={len(pending)} approved={len(approved)}",
    )


def _check_trade_log(root: Path) -> CheckResult:
    csv_path = root / "execution" / "logs" / "trades.csv"
    if not csv_path.exists():
        return CheckResult(
            name="trade_log",
            ok=True,
            fatal=False,
            detail="Trade log not created yet; execution layer will create it on first write.",
        )
    try:
        first_line = csv_path.read_text(encoding="utf-8").splitlines()[0]
    except Exception as exc:
        return CheckResult("trade_log", False, f"Unable to read trade log: {exc}", fatal=False)
    expected = ",".join(_CSV_HEADER)
    if first_line != expected:
        return CheckResult(
            name="trade_log",
            ok=False,
            fatal=False,
            detail="Trade log header is stale; TradeLogger will rotate it automatically.",
        )
    return CheckResult("trade_log", True, "Trade log header matches runtime schema.")


def _check_storage_coverage(root: Path) -> CheckResult:
    storage = root / "data" / "storage"
    must_have = ["klines"]
    optional = ["liquidations", "book_ticker", "agg_trades", "mark_price"]
    missing_must = [name for name in must_have if not (storage / name).exists()]
    missing_optional = [name for name in optional if not (storage / name).exists()]
    if missing_must:
        return CheckResult(
            name="storage_coverage",
            ok=False,
            fatal=True,
            detail=f"Missing required storage datasets: {', '.join(missing_must)}",
        )
    if missing_optional:
        return CheckResult(
            name="storage_coverage",
            ok=False,
            fatal=False,
            detail=f"Core klines exist; optional microstructure datasets not present yet: {', '.join(missing_optional)}",
        )
    return CheckResult(
        name="storage_coverage",
        ok=True,
        detail="Core and microstructure storage datasets are present.",
    )


def _check_execution_mode() -> CheckResult:
    if exec_config.ENABLED:
        return CheckResult("execution_mode", True, "Exchange credentials loaded. Live execution path is enabled.")
    return CheckResult(
        name="execution_mode",
        ok=False,
        fatal=False,
        detail="Exchange credentials missing. System will run in paper mode.",
    )


def run_preflight_checks(root: str | Path | None = None) -> DoctorReport:
    root_path = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    results = (
        _check_python_dependencies(),
        _check_runtime_directories(root_path),
        _check_live_strategy_catalog(),
        _check_live_feature_parity(),
        _check_alpha_rule_files(root_path),
        _check_trade_log(root_path),
        _check_storage_coverage(root_path),
        _check_execution_mode(),
    )
    return DoctorReport(results=results)


def format_report(report: DoctorReport) -> str:
    lines = []
    for item in report.results:
        if item.ok:
            prefix = "[OK]"
        elif item.fatal:
            prefix = "[ERR]"
        else:
            prefix = "[WARN]"
        lines.append(f"{prefix:<6} {item.name:<22} {item.detail}")
    lines.append(
        f"Summary: errors={sum(1 for item in report.results if (not item.ok) and item.fatal)} "
        f"warnings={report.warning_count}"
    )
    return "\n".join(lines)

