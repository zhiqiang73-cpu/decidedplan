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
_DISCOVERY_LOG_FILE = ROOT / "alpha" / "output" / "discovery.log"


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


# ── KIMI 摘要 ────────────────────────────────────────────────────────────────

def _get_ai_summary() -> dict:
    """
    读取 engine_state.json 最近决策，调用 KIMI API 生成自然语言摘要。
    KIMI 不可达时返回 error 字段，前端显示"KIMI 未连接"。
    """
    from datetime import datetime, timezone

    state = _read_json(_ENGINE_STATE_FILE, {})
    stats = state.get("stats", {})
    decisions = state.get("recent_decisions", [])

    approved_count = stats.get("last_run_summary", {}).get("approved", 0)
    rejected_count = stats.get("last_run_summary", {}).get("rejected", 0)
    review_count = stats.get("last_run_summary", {}).get("review", 0)

    # 构造决策摘要文字（最近 5 条）
    decision_lines = []
    for d in decisions[-5:]:
        rule_id = str(d.get("id", ""))[:30]
        direction = d.get("direction", "?")
        oos_wr = d.get("oos_wr", "?")
        mechanism = d.get("mechanism_type", "?")
        decision_lines.append(f"  - {rule_id} | {direction} | WR={oos_wr}% | {mechanism}")
    decisions_text = "\n".join(decision_lines) if decision_lines else "  (本次无决策)"

    prompt = (
        f"你是一个量化策略引擎的助手。引擎最近完成扫描，结果如下：\n"
        f"- 审批通过：{approved_count} 条  拒绝：{rejected_count} 条  待人工审查：{review_count} 条\n"
        f"- 最近决策：\n{decisions_text}\n\n"
        f"请用 2-3 句话总结本次扫描结果，指出发现了什么类型的策略信号，以及值得关注的地方。"
        f"回答用中文，不超过 100 字。"
    )

    try:
        promoter = _get_promoter()
        if promoter is None:
            return {"summary": "", "error": "AutoPromoter 未初始化"}
        summary_text = promoter._llm_chat(prompt)
        if not summary_text:
            return {"summary": "", "error": "LLM 返回空"}
        return {
            "summary": summary_text.strip(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("[Dashboard] KIMI 摘要失败: %s", exc)
        return {"summary": "", "error": str(exc)[:80]}




# ── KIMI 策略发现会话摘要 ────────────────────────────────────────────────────

def _get_kimi_sessions() -> dict:
    """
    汇总 Kimi 策略发现引擎的产出数据。
    - 从 approved_rules.json / pending_rules.json 提取 origin==kimi_researcher 的卡片
    - 从 discovery.log 提取最近 [KIMI] 日志行
    - 统计累计假设数（全量 [KIMI] 行数）和累计批准数
    """
    # 过滤 kimi 来源的已批准策略
    approved_all = _read_json(_APPROVED_FILE, [])
    approved_kimi = [r for r in approved_all if r.get("origin") == "kimi_researcher"]

    # 过滤 kimi 来源的待审核候选
    pending_all = _read_json(_PENDING_FILE, [])
    pending_kimi = [r for r in pending_all if r.get("origin") == "kimi_researcher"]

    # engine_state 中的最后运行时间
    state = _read_json(_ENGINE_STATE_FILE, {})
    last_run_at = state.get("last_run_at", "")

    # 读取 discovery.log 最近的 [KIMI] 行
    recent_log_lines: list[str] = []
    total_hypotheses = 0
    if _DISCOVERY_LOG_FILE.exists():
        try:
            # 文件可能很大，只读末尾 5000 字节来获取最近日志，减少 IO
            with open(_DISCOVERY_LOG_FILE, encoding="utf-8", errors="replace") as fh:
                # 全量扫描计数 total_hypotheses
                all_lines = fh.readlines()
            kimi_lines = [ln.strip() for ln in all_lines if "[KIMI]" in ln]
            total_hypotheses = len(kimi_lines)
            # 取最近 50 条 [KIMI] 日志
            recent_log_lines = kimi_lines[-50:]
        except Exception as exc:
            logger.debug("[Dashboard] 读取 discovery.log 失败: %s", exc)

    return {
        "approved_kimi": approved_kimi,
        "pending_kimi": pending_kimi,
        "recent_log_lines": recent_log_lines,
        "last_run_at": last_run_at,
        "total_hypotheses": total_hypotheses,
        "total_approved": len(approved_kimi),
    }
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

        elif path == "/api/ai_summary":
            self._send_json(_get_ai_summary())

        elif path == "/api/kimi_sessions":
            # 返回 Kimi 引擎发现的策略和日志摘要
            self._send_json(_get_kimi_sessions())

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
