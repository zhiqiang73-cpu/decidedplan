"""
报警处理器 (Alert Handler)

功能:
  1. 控制台彩色格式化输出（ANSI 颜色码，Windows 需开启 ENABLE_VIRTUAL_TERMINAL_PROCESSING）
  2. 写入滚动日志文件 (monitor/output/alerts.log)
  3. 统一格式：时间戳 | Phase | 信号名 | 方向 | 持仓 | 描述

注意:
  Windows 10+ 支持 ANSI 颜色。若终端不支持，颜色码会被过滤掉（无乱码）。
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# ANSI 颜色码
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_GREEN  = "\033[92m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

# 方向 → 颜色
_DIR_COLOR = {
    "long":    _GREEN,
    "short":   _RED,
    "neutral": _CYAN,
}

# Phase → 颜色
_PHASE_COLOR = {
    "P1": _YELLOW,
    "P2": _CYAN,
}

# 是否启用 ANSI（检测 Windows 终端能力）
def _ansi_enabled() -> bool:
    if os.name == "nt":
        # Windows: 尝试启用虚拟终端处理
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 获取当前控制台模式
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong(0)
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            # 启用 ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004)
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
        except Exception:
            return False
    return sys.stdout.isatty()


_USE_ANSI = _ansi_enabled()


def _c(text: str, color: str) -> str:
    """应用颜色（如果不支持 ANSI 则直接返回文本）。"""
    if _USE_ANSI:
        return f"{color}{text}{_RESET}"
    return text


class AlertHandler:
    """
    信号报警处理器。

    Args:
        log_dir:   报警日志目录（默认 monitor/output）
        log_to_file: 是否同时写文件（默认 True）
    """

    def __init__(
        self,
        log_dir: str = "monitor/output",
        log_to_file: bool = True,
    ):
        self.log_to_file = log_to_file
        self._log_path: Path | None = None

        if log_to_file:
            log_dir_path = Path(log_dir)
            log_dir_path.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir_path / "alerts.log"
            logger.info(f"Alert log: {self._log_path}")

    def send(self, alert: dict) -> None:
        """
        处理一条信号报警：打印到控制台并写入日志文件。

        Args:
            alert: SignalRunner.run() 返回的 dict
        """
        line = self._format(alert)

        # 控制台输出（带颜色）
        print(line)

        # 文件日志（纯文本，去除 ANSI 码）
        if self.log_to_file and self._log_path:
            plain = self._strip_ansi(line)
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(plain + "\n")
            except Exception as exc:
                logger.warning(f"Alert log write failed: {exc}")

    def send_batch(self, alerts: List[dict]) -> None:
        """批量处理报警列表。"""
        for alert in alerts:
            self.send(alert)

    def send_heartbeat(self, bar_count: int, ts_ms: int) -> None:
        """
        每 N 根 K 线打印一条心跳日志，表明监控正常运行。
        """
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        ts_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        msg = _c(f"[heartbeat] bar#{bar_count:,}  {ts_str}  — monitoring OK", _CYAN)
        print(msg)

    # ── 格式化 ─────────────────────────────────────────────────────────────
    def _format(self, alert: dict) -> str:
        """生成带颜色的单行报警字符串。"""
        ts_ms = alert.get("timestamp_ms", 0)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc)
        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        phase     = alert.get("phase", "??")
        name      = alert.get("name", "unknown")
        direction = alert.get("direction", "")
        horizon   = alert.get("horizon", 0)
        desc      = alert.get("desc", "")

        phase_c = _PHASE_COLOR.get(phase, _RESET)
        dir_c   = _DIR_COLOR.get(direction, _RESET)

        phase_tag = _c(f"[{phase}]",               phase_c)
        dir_tag   = _c(f"{direction.upper():>7}",  dir_c + _BOLD)
        name_tag  = _c(f"{name:<32}",              _BOLD)

        # 额外字段（Alpha 规则专有）
        extra = ""
        if phase == "P2":
            feat  = alert.get("feature", "")
            val   = alert.get("feature_value", None)
            op    = alert.get("op", "")
            thresh = alert.get("threshold", None)
            if val is not None and thresh is not None:
                extra = _c(
                    f"  [{feat}={val:.5f} {op} {thresh}]",
                    _YELLOW
                )

        line = (
            f"{_c(ts_str, _CYAN)}  "
            f"{phase_tag}  "
            f"{name_tag}  "
            f"{dir_tag}  "
            f"{_c(str(horizon) + 'bars', _RESET):>8}  "
            f"{desc}"
            f"{extra}"
        )

        # 用视觉分隔线突出显示
        sep = _c("=" * 80, _BOLD)
        return f"\n{sep}\n  SIGNAL ALERT  {line}\n{sep}"

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """去除 ANSI 颜色码（写文件时使用）。"""
        import re
        return re.sub(r"\033\[[0-9;]*m", "", text)
