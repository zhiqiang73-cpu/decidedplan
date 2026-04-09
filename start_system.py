"""BTC Alpha 控制台页面入口。"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
PYTHON = sys.executable
PORT = 18888

_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BTC Alpha 控制台</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: linear-gradient(180deg, #111827 0%, #0b1220 100%);
    color: #e5e7eb;
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    min-height: 100vh;
    padding: 40px 20px;
  }
  .wrap {
    max-width: 760px;
    margin: 0 auto;
  }
  h1 {
    font-size: 28px;
    margin-bottom: 10px;
  }
  .sub {
    color: #94a3b8;
    font-size: 14px;
    margin-bottom: 28px;
  }
  .panel {
    background: rgba(17, 24, 39, 0.9);
    border: 1px solid #243041;
    border-radius: 16px;
    padding: 22px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    gap: 20px;
    align-items: center;
  }
  .title {
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 6px;
  }
  .desc {
    color: #94a3b8;
    font-size: 13px;
    line-height: 1.5;
  }
  .status {
    margin-top: 10px;
    font-size: 13px;
    color: #cbd5e1;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 999px;
    display: inline-block;
    margin-right: 8px;
    background: #475569;
  }
  .dot.on {
    background: #22c55e;
    box-shadow: 0 0 10px rgba(34, 197, 94, 0.7);
  }
  .btns {
    display: flex;
    gap: 10px;
    flex-shrink: 0;
  }
  button {
    border: none;
    border-radius: 10px;
    padding: 10px 18px;
    font-size: 14px;
    font-family: inherit;
    font-weight: 700;
    cursor: pointer;
    transition: transform 0.12s ease, opacity 0.12s ease;
  }
  button:hover { transform: translateY(-1px); }
  button:disabled { opacity: 0.45; cursor: default; transform: none; }
  .start { background: #2563eb; color: white; }
  .start-dl { background: #7c3aed; color: white; }
  .stop { background: #dc2626; color: white; }
  .bar {
    display: flex;
    gap: 12px;
    margin-top: 20px;
    flex-wrap: wrap;
  }
  .all-start { background: #16a34a; color: white; }
  .all-stop { background: #b91c1c; color: white; }
  .clock {
    margin-top: 26px;
    color: #64748b;
    font-family: Consolas, monospace;
    font-size: 12px;
  }
  @media (max-width: 720px) {
    .panel {
      flex-direction: column;
      align-items: flex-start;
    }
    .btns {
      width: 100%;
    }
    button {
      flex: 1;
    }
  }
</style>
</head>
<body>
  <div class="wrap">
    <h1>BTC Alpha 控制台</h1>
    <p class="sub">Testnet 模式 · 限价入场 · 监控与下载进程统一管理</p>

    <section class="panel">
      <div>
        <div class="title">实时监控与执行</div>
        <div class="desc">启动 run_monitor.py，连接 WebSocket、计算特征、检测信号，并驱动执行层。</div>
        <div class="status"><span class="dot" id="dot-monitor"></span><span id="txt-monitor">未运行</span></div>
      </div>
      <div class="btns">
        <button class="start" id="btn-start-monitor" onclick="startProc('monitor')">启动</button>
        <button class="stop" id="btn-stop-monitor" onclick="stopProc('monitor')" disabled>停止</button>
      </div>
    </section>

    <section class="panel">
      <div>
        <div class="title">历史数据下载</div>
        <div class="desc">启动 run_downloader.py；选 1 后会补齐可回补历史，并持续采集盘口最优买卖价与强平流。</div>
        <div class="status"><span class="dot" id="dot-downloader"></span><span id="txt-downloader">未运行</span></div>
      </div>
      <div class="btns">
        <button class="start-dl" id="btn-start-downloader" onclick="startProc('downloader')">启动</button>
        <button class="stop" id="btn-stop-downloader" onclick="stopProc('downloader')" disabled>停止</button>
      </div>
    </section>

    <div class="bar">
      <button class="all-start" onclick="startAll()">全部启动</button>
      <button class="all-stop" onclick="stopAll()">全部停止</button>
    </div>

    <div class="clock" id="clock"></div>
  </div>

<script>
async function api(action, name) {
  const resp = await fetch('/api', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, name })
  });
  return await resp.json();
}

async function startProc(name) {
  await api('start', name);
  await refresh();
}

async function stopProc(name) {
  await api('stop', name);
  await refresh();
}

async function startAll() {
  await api('start', 'monitor');
  await api('start', 'downloader');
  await refresh();
}

async function stopAll() {
  await api('stop', 'monitor');
  await api('stop', 'downloader');
  await refresh();
}

async function refresh() {
  const resp = await fetch('/status');
  const st = await resp.json();
  for (const name of ['monitor', 'downloader']) {
    const on = !!st[name];
    document.getElementById('dot-' + name).className = 'dot' + (on ? ' on' : '');
    document.getElementById('txt-' + name).textContent = on ? '运行中' : '未运行';
    document.getElementById('btn-start-' + name).disabled = on;
    document.getElementById('btn-stop-' + name).disabled = !on;
  }
}

function tick() {
  document.getElementById('clock').textContent = '本地时间  ' + new Date().toLocaleString('zh-CN');
}

setInterval(refresh, 3000);
setInterval(tick, 1000);
refresh();
tick();
</script>
</body>
</html>
"""

CMDS = {
    "watchdog": [PYTHON, "watchdog.py"],
}
# watchdog.py 统一管理: monitor + ws + discovery + UI(8050)
# 关掉 watchdog 时全部子进程一起关


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _write_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _send_json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self._write_body(body)

    def _send_html(self, html: str, code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self._write_body(body)

    def do_GET(self) -> None:
        if self.path == "/status":
            with _lock:
                status = {name: (proc.poll() is None) for name, proc in _procs.items()}
            self._send_json(
                {
                    "monitor": status.get("monitor", False),
                    "downloader": status.get("downloader", False),
                }
            )
            return

        self._send_html(HTML)

    def do_POST(self) -> None:
        if self.path != "/api":
            self._send_json({"ok": False, "error": "not_found"}, code=404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json({"ok": False, "error": "bad_json"}, code=400)
            return

        action = payload.get("action")
        name = payload.get("name")
        if name not in CMDS:
            self._send_json({"ok": False, "error": "bad_name"}, code=400)
            return

        if action == "start":
            with _lock:
                proc = _procs.get(name)
                if proc is None or proc.poll() is not None:
                    _procs[name] = subprocess.Popen(
                        CMDS[name],
                        cwd=str(ROOT),
                        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                    )
            self._send_json({"ok": True})
            return

        if action == "stop":
            with _lock:
                proc = _procs.pop(name, None)
            if proc and proc.poll() is None:
                proc.terminate()
            self._send_json({"ok": True})
            return

        self._send_json({"ok": False, "error": "bad_action"}, code=400)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"BTC Alpha dashboard -> {url}")
    threading.Timer(1.0, lambda: _open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n控制台已关闭，正在停止所有子进程...")
        with _lock:
            for proc in _procs.values():
                if proc.poll() is None:
                    proc.terminate()


if __name__ == "__main__":
    main()
