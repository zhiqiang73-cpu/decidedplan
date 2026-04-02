from __future__ import annotations

import socket
import time
import webbrowser


HOST = "127.0.0.1"
PORT = 8050
TIMEOUT_S = 90.0
SLEEP_S = 0.5


def _is_port_open(host: str, port: int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def main() -> int:
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        if _is_port_open(HOST, PORT):
            webbrowser.open(f"http://{HOST}:{PORT}")
            return 0
        time.sleep(SLEEP_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
