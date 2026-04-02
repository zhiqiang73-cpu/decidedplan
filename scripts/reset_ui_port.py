from __future__ import annotations

import re
import subprocess


PORT = 8050
PID_RE = re.compile(r"\s+(\d+)\s*$")


def _list_pids_on_port(port: int) -> list[int]:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: set[int] = set()
    target = f":{port}"
    for line in result.stdout.splitlines():
        if target not in line:
            continue
        if "LISTENING" not in line and "ESTABLISHED" not in line:
            continue
        match = PID_RE.search(line)
        if not match:
            continue
        pid = int(match.group(1))
        if pid > 0:
            pids.add(pid)
    return sorted(pids)


def _kill_pid(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def main() -> int:
    for pid in _list_pids_on_port(PORT):
        _kill_pid(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
