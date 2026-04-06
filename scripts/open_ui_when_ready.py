from __future__ import annotations

import os
import socket
import subprocess
import time
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8050
TIMEOUT_S = 180.0
SLEEP_S = 0.5
ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "monitor" / "output" / "open_ui_when_ready.log"


def _is_port_open(host: str, port: int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def _open_url(url: str) -> bool:
    if os.name == "nt":
        try:
            os.startfile(url)
            _log(f"Opened via os.startfile: {url}")
            return True
        except OSError as exc:
            _log(f"os.startfile failed: {exc}")
        try:
            subprocess.Popen(["cmd", "/c", "start", "", url], creationflags=subprocess.CREATE_NO_WINDOW)
            _log(f"Opened via cmd start: {url}")
            return True
        except OSError as exc:
            _log(f"cmd start failed: {exc}")
    try:
        ok = webbrowser.open(url, new=2)
        _log(f"Opened via webbrowser.open={ok}: {url}")
        return bool(ok)
    except Exception as exc:
        _log(f"webbrowser.open failed: {exc}")
        return False


def main() -> int:
    url = f"http://{HOST}:{PORT}"
    _log(f"Watcher started for {url} timeout={TIMEOUT_S}s")
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        if _is_port_open(HOST, PORT):
            _log(f"Port became reachable: {HOST}:{PORT}")
            time.sleep(1.0)
            _open_url(url)
            return 0
        time.sleep(SLEEP_S)
    _log(f"Timed out waiting for {HOST}:{PORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
