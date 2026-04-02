"""
Alpha 引擎 UI Dashboard HTTP 服务器。

端点:
  GET  /api/state          → engine_state.json 全量
  GET  /api/pending        → pending_rules.json
  GET  /api/approved       → approved_rules.json
  GET  /api/review         → review_queue.json
  POST /api/approve        → {"id": "..."} 人工批准
  POST /api/reject         → {"id": "..."} 人工拒绝
  POST /api/config         → {"api_key": ..., "model": ...} 更新 LLM 配置
  GET  /                   → alpha_dashboard.html

启动:
  python ui/alpha_dashboard_server.py          # 默认 127.0.0.1:7869
  python ui/alpha_dashboard_server.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

_CONFIG_FILE = ROOT / "alpha" / "output" / "promoter_config.json"
_ENGINE_STATE_FILE = ROOT / "alpha" / "output" / "engine_state.json"
_PENDING_FILE = ROOT / "alpha" / "output" / "pending_rules.json"
_APPROVED_FILE = ROOT / "alpha" / "output" / "approved_rules.json"
_REJECTED_FILE = ROOT / "alpha" / "output" / "rejected_rules.json"
_REVIEW_FILE = ROOT / "alpha" / "output" / "review_queue.json"
_HTML_FILE = ROOT / "ui" / "alpha_dashboard.html"


def _read_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ── 促进器单例（后台线程用） ──────────────────────────────────────────────────
_promoter_instance = None
_promoter_lock = threading.Lock()


def _get_promoter():
    global _promoter_instance
    with _promoter_lock:
        if _promoter_instance is None:
            try:
                from alpha.auto_promoter import AutoPromoter
                _promoter_instance = AutoPromoter()
            except Exception as exc:
                logger.warning("[Dashboard] AutoPromoter 初始化失败: %s", exc)
        return _promoter_instance


# ── HTTP 处理器 ───────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        logger.debug("[Dashboard] %s - %s", self.address_string(), fmt % args)

    def _send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except Exception:
                pass
        return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            if _HTML_FILE.exists():
                self._send_html(_HTML_FILE.read_text(encoding="utf-8"))
            else:
                self._send_html("<h1>alpha_dashboard.html not found</h1>")

        elif path == "/api/state":
            state = _read_json(_ENGINE_STATE_FILE, {"status": "idle", "stats": {}})
            self._send_json(state)

        elif path == "/api/pending":
            self._send_json(_read_json(_PENDING_FILE, []))

        elif path == "/api/approved":
            self._send_json(_read_json(_APPROVED_FILE, []))

        elif path == "/api/review":
            self._send_json(_read_json(_REVIEW_FILE, []))

        elif path == "/api/rejected":
            self._send_json(_read_json(_REJECTED_FILE, []))

        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        if path == "/api/approve":
            rule_id = body.get("id", "")
            promoter = _get_promoter()
            if promoter and rule_id:
                ok = promoter.manual_approve(rule_id)
                self._send_json({"ok": ok, "id": rule_id})
            else:
                self._send_json({"ok": False, "error": "missing id or promoter unavailable"}, 400)

        elif path == "/api/reject":
            rule_id = body.get("id", "")
            promoter = _get_promoter()
            if promoter and rule_id:
                ok = promoter.manual_reject(rule_id)
                self._send_json({"ok": ok, "id": rule_id})
            else:
                self._send_json({"ok": False, "error": "missing id or promoter unavailable"}, 400)

        elif path == "/api/config":
            # 更新 LLM 配置（API key / model）
            cfg = _read_json(_CONFIG_FILE, {})
            if "api_key" in body:
                cfg.setdefault("llm", {})["api_key"] = body["api_key"]
            if "model" in body:
                cfg.setdefault("llm", {})["model"] = body["model"]
            if "base_url" in body:
                cfg.setdefault("llm", {})["base_url"] = body["base_url"]
            if "auto_approve" in body:
                cfg.setdefault("thresholds", {})["auto_approve"] = float(body["auto_approve"])
            if "review_queue" in body:
                cfg.setdefault("thresholds", {})["review_queue"] = float(body["review_queue"])
            _write_json(_CONFIG_FILE, cfg)
            # 重置促进器（让它用新配置）
            global _promoter_instance
            with _promoter_lock:
                _promoter_instance = None
            self._send_json({"ok": True})

        elif path == "/api/run_now":
            # 立即触发一次 LLM 验证
            def _bg():
                try:
                    promoter = _get_promoter()
                    if promoter:
                        promoter.run_once()
                except Exception as exc:
                    logger.error("[Dashboard] run_now 失败: %s", exc)

            threading.Thread(target=_bg, daemon=True).start()
            self._send_json({"ok": True, "message": "运行中，请稍后刷新状态"})

        else:
            self._send_json({"error": "not found"}, 404)


# ── 后台促进循环 ──────────────────────────────────────────────────────────────

def _start_promoter_background(interval_hours: float = 1.0) -> None:
    """在后台线程启动促进器循环。"""
    def _loop():
        import time
        logger.info("[Dashboard] 后台促进器启动，间隔 %.1fh", interval_hours)
        while True:
            try:
                promoter = _get_promoter()
                if promoter:
                    promoter.run_once()
            except Exception as exc:
                logger.error("[Dashboard] 后台促进器异常: %s", exc)
            time.sleep(interval_hours * 3600)

    t = threading.Thread(target=_loop, daemon=True, name="AutoPromoterLoop")
    t.start()


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Alpha Engine Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7869)
    parser.add_argument(
        "--no-bg-promoter",
        action="store_true",
        help="不启动后台促进器（仅提供 UI，手动触发）",
    )
    args = parser.parse_args()

    # 读取配置中的端口（如果未通过命令行指定则用配置）
    cfg = _read_json(_CONFIG_FILE, {})
    port = args.port or int(cfg.get("dashboard", {}).get("port", 7869))
    host = args.host or cfg.get("dashboard", {}).get("host", "127.0.0.1")

    if not args.no_bg_promoter:
        interval = float(cfg.get("loop", {}).get("interval_hours", 1))
        _start_promoter_background(interval)

    server = HTTPServer((host, port), DashboardHandler)
    logger.info("[Dashboard] 启动在 http://%s:%d", host, port)
    print(f"\n  Alpha Engine Dashboard: http://{host}:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("[Dashboard] 关闭")
        server.shutdown()


if __name__ == "__main__":
    main()
