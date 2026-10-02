#!/usr/bin/env python3
"""Loopback-only UI. Remote GET /models only on explicit consent; no generation calls."""
from __future__ import annotations

import argparse
import errno
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import secrets
import sys
import threading
import urllib.request
import webbrowser

from account_info import fetch_usage
from config_core import ConfigError, ConfigStore, atomic_write
from connection_test import PROTOCOL, test_connection
from model_discovery import discover_models
from backend_i18n import translate_response, _EXACT_TRANSLATIONS, _TEMPLATES

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VERSION = "0.6.7"
MAX_BODY = 65536


def default_runtime_path():
    return Path.home() / "Library/Application Support/WeSwitch/runtime.json"


def browser_message_catalog():
    templates = []
    for pattern, english in _TEMPLATES:
        chinese = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", pattern.pattern)
        chinese = chinese.removeprefix(r"\A").removesuffix(r"\Z").replace(r"\.", ".")
        templates.append({"zh": chinese, "en": english})
    return {"exact": _EXACT_TRANSLATIONS, "templates": templates}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, token=None):
        super().__init__(address, Handler)
        self.store = store
        self.discovery_slots = threading.BoundedSemaphore(2)
        # A connection test spends real quota, so only one may run at a time.
        self.connection_slots = threading.BoundedSemaphore(1)
        # The usage query carries the ChatGPT token; keep it strictly serial.
        self.usage_slots = threading.BoundedSemaphore(1)
        self.token = token or secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "WeSwitch/0.6.7"
    sys_version = ""

    def log_message(self, *_):
        # Suppress URL/body/token/credential logging, including malformed requests.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def send(self, status, data, content_type="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = translate_response(data, self.headers.get("X-WeSwitch-Language"))
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error(self, message, code="invalid", status=400):
        self.send(status, {"error": message, "code": code})

    def trusted(self, api=False):
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected_host:
            self.error("拒绝不匹配的本机请求地址。", "host_rejected", 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.error("拒绝来自其他网页的请求。", "origin_rejected", 403)
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.error("拒绝跨站请求。", "cross_site_rejected", 403)
            return False
        if api:
            token = self.headers.get("X-Codex-UI-Token", "")
            if not hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8")):
                self.error("会话凭据失效，请重新双击启动入口。", "unauthorized", 401)
                return False
        return True

    def do_GET(self):
        is_api = self.path.startswith("/api/")
        if not self.trusted(is_api):
            return
        if self.path in ("/", "/index.html"):
            self.send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/backend-i18n.js":
            code = "window.WeSwitchBackendMessages = " + json.dumps(browser_message_catalog(), ensure_ascii=True) + ";"
            self.send(200, code.encode("utf-8"), "text/javascript; charset=utf-8")
        elif self.path == "/i18n.js":
            self.send(200, (ROOT / "i18n.js").read_bytes(), "text/javascript; charset=utf-8")
        elif self.path == "/favicon.ico":
            self.send(204, b"", "image/x-icon")
        elif self.path == "/api/state":
            try:
                state = self.server.store.state()
                state["tool_version"] = VERSION
                self.send(200, state)
            except ConfigError as e:
                self.error(str(e), e.code, e.status)
            except Exception:
                self.error("无法读取配置；请确认文件权限。未输出原文件内容或密钥。", "read_failed", 500)
        else:
            self.error("页面不存在。", "not_found", 404)

    def do_POST(self):
        if not self.trusted(True):
            return
        if self.path not in ("/api/preview", "/api/apply", "/api/models", "/api/connection-test",
                             "/api/rollback", "/api/usage", "/api/remove"):
            self.error("接口不存在。", "not_found", 404)
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.error("仅接受 JSON 请求。", "content_type", 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
            self.error("请求长度无效或超过限制。", "body_too_large", 413)
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError, TimeoutError):
            self.error("请求不是有效 JSON。")
            return
        try:
            if self.path == "/api/preview":
                # Preview never accepts a real key, so it cannot retain one in a plan.
                if payload.get("api_key"):
                    raise ConfigError("预览请求不得包含 API Key。")
                result = self.server.store.preview(payload)
            elif self.path == "/api/models":
                if not self.server.discovery_slots.acquire(blocking=False):
                    raise ConfigError("已有模型列表请求正在处理，请等待完成后重试。", "models_busy", 429)
                try:
                    result = discover_models(self.server.store, payload)
                finally:
                    self.server.discovery_slots.release()
            elif self.path == "/api/connection-test":
                if not self.server.connection_slots.acquire(blocking=False):
                    raise ConfigError("已有连接测试正在进行，请等待完成后重试。", "connection_busy", 429)
                try:
                    result = test_connection(self.server.store, payload)
                finally:
                    self.server.connection_slots.release()
            elif self.path == "/api/rollback":
                result = self.server.store.restore(payload)
            elif self.path == "/api/remove":
                result = self.server.store.remove_models(payload)
            elif self.path == "/api/usage":
                if not self.server.usage_slots.acquire(blocking=False):
                    raise ConfigError("已有用量查询正在进行，请等待完成后重试。", "usage_busy", 429)
                try:
                    result = fetch_usage(self.server.store, payload)
                finally:
                    self.server.usage_slots.release()
            else:
                result = self.server.store.apply(payload)
            self.send(200, result)
        except ConfigError as e:
            self.error(str(e), e.code, e.status)
        except Exception:
            self.error("操作未能完成。请检查本机权限和备份目录；不要重复提交。详细异常已隐藏以保护凭据。", "operation_failed", 500)

    def do_OPTIONS(self):
        self.error("此工具不允许跨域访问。", "cors_disabled", 403)


def reuse_existing(runtime, config_home, should_open):
    try:
        meta = json.loads(runtime.read_text())
        if meta.get("config_home") != str(config_home.resolve()):
            return False
        origin = meta["origin"]
        if not origin.startswith("http://127.0.0.1:") or not origin.rsplit(":", 1)[-1].isdigit():
            return False
        request = urllib.request.Request(origin + "/api/state", headers={"X-Codex-UI-Token": meta["token"]})
        # Do not send the local session token through any environment-configured proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            data = json.loads(response.read())
            if data.get("config_path") != str(config_home.resolve() / "config.toml") or data.get("tool_version") != VERSION:
                return False
        if should_open:
            webbrowser.open(origin + "/#token=" + meta["token"])
        print("本地配置界面已在运行。")
        return True
    except (OSError, ValueError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Codex 本地模型配置界面；真实配置仅在界面确认后修改。")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开界面")
    parser.add_argument("--config-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--runtime", type=Path, default=default_runtime_path())
    parser.add_argument("--demo", action="store_true", help="仅显示测试标识；请同时传入专用 --config-home")
    args = parser.parse_args()
    if args.demo and args.config_home.expanduser().resolve() == (Path.home() / ".codex").resolve():
        parser.error("演示模式必须指定独立的 --config-home，不能使用真实配置。")
    args.runtime = args.runtime.expanduser()
    args.config_home = args.config_home.expanduser()
    if args.runtime.is_symlink():
        parser.error("运行状态文件不能为符号链接。")
    if args.runtime.parent.is_symlink():
        parser.error("Refusing a symlinked runtime directory.")
    if not args.runtime.parent.is_dir():
        args.runtime.parent.mkdir(parents=True, mode=0o700)
    if reuse_existing(args.runtime, args.config_home, args.open):
        return
    store = ConfigStore(args.config_home, demo=args.demo)
    try:
        server = LocalServer(("127.0.0.1", args.port), store)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        server = LocalServer(("127.0.0.1", 0), store)
    meta = {"origin": server.origin, "token": server.token, "config_home": str(store.home),
            "pid": os.getpid(), "url": server.origin + "/#token=" + server.token}
    atomic_write(args.runtime, json.dumps(meta, indent=2).encode("utf-8"))
    print(f"本地配置界面已启动：{server.origin}；仅监听 127.0.0.1。", flush=True)
    print("真实配置尚未修改。使用启动入口打开带会话凭据的界面；Ctrl+C 可停止服务。", flush=True)
    if args.open:
        webbrowser.open(meta["url"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
