# -*- coding: utf-8 -*-
"""HTTP 入口：单文件纯标准库 http.server，对齐 routers.ts 行为。

端点（§5）：
  GET  /                       → 静态页面 web/index.html
  GET  /api/v1/health          → 健康检查 + 知识库版本指纹
  GET  /api/v1/bootstrap       → bootstrap(session_id) 全量刷新
  POST /api/v1/chat            → handle_chat({sessionId, messages, ...})
  POST /api/v1/reset           → reset_for_tests(session_id)
  GET  /api/v1/attempts/:id    → 拉取某条 trace 的全部 attempts（debug 面板）

设计要点：
  - CORS: 任意 Origin（本地 demo）。
  - UTF-8: 始终返回 application/json; charset=utf-8 + Content-Type 文本用 utf-8 解码。
  - 端口回退: 绑定失败自动 +1，最多尝试 PORT_FALLBACK_TRIES 次。
  - 统一错误对象: { "error": { "code", "message", "details?" } }
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 让 ``python server.py`` 与 ``python -m engine.server`` 都能跑
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import chat as engine_chat  # noqa: E402
from engine import config  # noqa: E402
from engine import knowledge  # noqa: E402
from engine import store  # noqa: E402

LOGGER = logging.getLogger("cat-care-agent")


class JsonResponse:
    """统一 JSON 响应构造器，附带 CORS 头。"""

    @staticmethod
    def _headers(length: int) -> list[tuple[str, str]]:
        return [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(length)),
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type"),
            ("Cache-Control", "no-store"),
        ]

    @staticmethod
    def send(handler: "Handler", status: int, payload: dict | list, head: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        for key, value in JsonResponse._headers(len(body)):
            handler.send_header(key, value)
        handler.end_headers()
        if not head:
            handler.wfile.write(body)

    @staticmethod
    def error(handler: "Handler", status: int, code: str, message: str, details: dict | None = None, head: bool = False) -> None:
        payload = {"error": {"code": code, "message": message, "details": details or {}}}
        JsonResponse.send(handler, status, payload, head=head)

    @staticmethod
    def html(handler: "Handler", status: int, body: bytes, content_type: str = "text/html; charset=utf-8", head: bool = False) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        if not head:
            handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "cat-care-agent/1.0"
    # 静音 BaseHTTPRequestHandler 自带 access log；由 LOGGER 接管
    def log_message(self, format: str, *args) -> None:  # noqa: A002 (signature 来自 stdlib)
        LOGGER.info("%s - %s", self.address_string(), format % args)

    # ---------------------------- CORS 预检
    def do_OPTIONS(self) -> None:  # noqa: N802 (stdlib naming)
        JsonResponse.html(self, HTTPStatus.NO_CONTENT, b"")

    # ---------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        try:
            self._dispatch_get()
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("GET %s failed", self.path)
            JsonResponse.error(self, HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc),
                               {"trace": traceback.format_exc(limit=5).splitlines()[-3:]})

    # ---------------------------- HEAD（预览面板探活用，仅回头部）
    def do_HEAD(self) -> None:  # noqa: N802
        try:
            self._dispatch_get(head=True)
        except Exception:  # noqa: BLE001
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _dispatch_get(self, head: bool = False) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            # 每次请求重新读取，便于无需重启即可热更新前端
            html_path = config.WEB_DIR / "index.html"
            body = html_path.read_bytes() if html_path.exists() else b"<!doctype html><meta charset=utf-8><title>cat-care-agent</title><p>index.html missing</p>"
            JsonResponse.html(self, HTTPStatus.OK, body, head=head)
            return

        if path == "/api/v1/health":
            JsonResponse.send(self, HTTPStatus.OK, {
                "ok": True,
                "generator": config.generator_mode(),
                "kb_sha256": config.EXPECTED_KB_SHA256,
                "knowledge_count": len(knowledge.KB_ALL),
                "verified_source_count": len(knowledge.VERIFIED_SOURCE_IDS),
                "fallback_entry": config.FALLBACK_ENTRY_ID,
                "uptime_unix": int(time.time()),
                "app_version": "1.0.0",
            }, head=head)
            return

        if path == "/api/v1/bootstrap":
            query = parse_qs(parsed.query)
            session_id = (query.get("sessionId") or [""])[0].strip() or f"web-session-{int(time.time() * 1000)}"
            JsonResponse.send(self, HTTPStatus.OK, engine_chat.bootstrap(session_id), head=head)
            return

        # GET /api/v1/attempts/<trace_id>
        if path.startswith("/api/v1/attempts/"):
            trace_id = path[len("/api/v1/attempts/"):].strip()
            if not trace_id:
                JsonResponse.error(self, HTTPStatus.BAD_REQUEST, "missing_trace_id", "trace_id is required")
                return
            rows = store.list_review_by_trace(trace_id)
            if not rows:
                JsonResponse.error(self, HTTPStatus.NOT_FOUND, "trace_not_found", f"trace {trace_id} not found")
                return
            JsonResponse.send(self, HTTPStatus.OK, {"trace_id": trace_id, "attempts": rows}, head=head)
            return

        JsonResponse.error(self, HTTPStatus.NOT_FOUND, "not_found", f"unknown path: {path}", head=head)

    # ---------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        try:
            self._dispatch_post()
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("POST %s failed", self.path)
            JsonResponse.error(self, HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc),
                               {"trace": traceback.format_exc(limit=5).splitlines()[-3:]})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    def _dispatch_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/v1/chat":
            try:
                payload = self._read_body()
            except ValueError as exc:
                JsonResponse.error(self, HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
                return
            if not isinstance(payload, dict):
                JsonResponse.error(self, HTTPStatus.BAD_REQUEST, "bad_request", "JSON body must be an object")
                return
            JsonResponse.send(self, HTTPStatus.OK, engine_chat.handle_chat(payload))
            return

        if path == "/api/v1/reset":
            try:
                payload = self._read_body()
            except ValueError as exc:
                JsonResponse.error(self, HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
                return
            session_id = (payload or {}).get("sessionId") or ""
            if not session_id:
                JsonResponse.error(self, HTTPStatus.BAD_REQUEST, "missing_session", "sessionId required")
                return
            engine_chat.reset_for_tests(session_id)
            JsonResponse.send(self, HTTPStatus.OK, {"ok": True, "session_id": session_id})
            return

        JsonResponse.error(self, HTTPStatus.NOT_FOUND, "not_found", f"unknown path: {path}")


def _make_server(host: str, port: int, tries: int) -> tuple["ThreadingHTTPServer", int]:
    """只绑定一次（Linux 下不能对同一地址重复 bind）。

    HTTPServer 默认 allow_reuse_address=1（已置 SO_REUSEADDR）；端口回退时逐个尝试构造。
    """
    last_err: OSError | None = None
    for offset in range(tries):
        try:
            srv = ThreadingHTTPServer((host, port + offset), Handler)
            srv.daemon_threads = True
            return srv, port + offset
        except OSError as exc:  # noqa: PERF203
            last_err = exc
            continue
    raise RuntimeError(f"无法绑定 {host}:{port} 附近 {tries} 个端口：{last_err}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 启动时校验知识库指纹
    issues = knowledge.self_check(verbose=False)
    if issues:
        LOGGER.warning("knowledge self-check issues: %s", issues)

    # 发布沙箱注入标准 PORT 环境变量时需监听 0.0.0.0；本地无 PORT 时维持 127.0.0.1
    deploy_port = int(os.environ.get("PORT") or config.PORT)
    deploy_host = "0.0.0.0" if os.environ.get("PORT") else config.HOST
    server, actual_port = _make_server(deploy_host, deploy_port, config.PORT_FALLBACK_TRIES)

    LOGGER.info("cat-care-agent listening on http://%s:%s", deploy_host, actual_port)
    LOGGER.info("generator=%s, db=%s", config.generator_mode(), config.DB_PATH)

    def _serve() -> None:
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

    thread = threading.Thread(target=_serve, daemon=True, name="cat-care-agent-server")
    thread.start()

    # 把端口暴露给调用方（前端 dev 模式需要）
    print(f"CAT_AGENT_PORT={actual_port}", flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        LOGGER.info("shutting down")


if __name__ == "__main__":
    main()
