"""One-shot Responses-protocol connection check, sent only after explicit consent.

Codex has exactly one wire protocol: the Responses API. `wire_api = "chat"` was
removed from codex-cli (`Chat Completions is no longer supported`), and the config
schema exposes no protocol switch. So there is nothing to select -- the only open
question before writing config is whether this endpoint accepts a minimal
Responses request with these credentials. This module answers that one question.

The check is a real request: it asks for at most MAX_OUTPUT_TOKENS output tokens
and therefore consumes a small amount of quota. The UI must say so before sending.
No configuration file or Keychain item is read or written by this module; the
credential path is shared with the read-only model list.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.request

import certifi

from config_core import WIRE_API, ConfigError, text, validate_model
from model_discovery import NoRedirect, resolve_credentials

# The only wire protocol Codex can be configured to use.
PROTOCOL = WIRE_API

CONSENT_MESSAGE = "请确认目标地址并点击测试连接，才会发送请求。"

# A truncated reply still reveals the response shape, so 256 KB is generous.
MAX_BYTES = 256 * 1024
MAX_FIELD = 200
REQUEST_TIMEOUT = 25
BODY_DEADLINE = 30
MAX_OUTPUT_TOKENS = 16
PING_TEXT = "ping"

# Kept separate from the model list: a generation request deserves a longer budget.
TOKEN_NOTE = "这次测试向服务商真实发送了一次最短请求（最多输出 16 个 token），可能产生少量费用。"


def request_body(model):
    return json.dumps({
        "model": model,
        "input": PING_TEXT,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
        # Never ask the provider to retain the probe.
        "store": False,
    }, ensure_ascii=False).encode("utf-8")


def redact(value, secret):
    """Sanitize one upstream-derived field before it can reach the browser.

    Drops the credential itself, control characters, and anything outside a
    conservative printable set. Returns '' when the field cannot be trusted.
    """
    if not isinstance(value, str):
        return ""
    # Replace before truncating: a credential longer than MAX_FIELD would otherwise
    # be cut short first and no longer match, letting its first MAX_FIELD characters
    # through. ChatGPT access tokens are JWTs and are far longer than this limit.
    if secret:
        value = value.replace(secret, "[redacted]")
    value = value[:MAX_FIELD]
    value = "".join(c for c in value if 32 <= ord(c) < 127 or c in "-_.:/@")
    value = value.strip()
    if not value or any(ord(c) < 32 for c in value) or " " in value:
        return ""
    return value


def resolve(store, payload):
    base, secret = resolve_credentials(store, payload, CONSENT_MESSAGE)
    model = validate_model(text(payload, "model", 200))
    return base + "/responses", secret, model


def parse_body(raw):
    """Return the decoded object, or None when the reply is not usable JSON."""
    if len(raw) > MAX_BYTES:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return body if isinstance(body, dict) else None


def classify(status, body, secret):
    """Map one response onto a verdict, a human message, and warnings."""
    warnings = [TOKEN_NOTE]
    reported_model = redact(body.get("model"), secret) if body else ""
    response_type = redact(body.get("object"), secret) if body else ""
    if not 200 <= status < 300:
        if status in (401, 403):
            message = "认证被拒绝。请核对 API Key 或环境变量；未读取上游错误正文。"
        elif status in (404, 405, 501):
            message = "这个地址没有可用的 /responses 接口。Codex 只支持 Responses 协议，不能改为其他协议。"
        elif status == 429:
            message = "服务商限流，请稍后重试。"
        elif status == 400:
            message = "请求被拒绝（HTTP 400）。常见原因是模型 ID 不受支持；未读取上游错误正文。"
        elif 300 <= status < 400:
            message = "接口要求跳转。为避免密钥泄露，未跟随跳转；请填写最终 API 地址。"
        else:
            message = f"服务端返回 HTTP {status}；未读取错误正文，请稍后重试。"
        return {
            "compatible": False, "message": message, "warnings": warnings,
            "response_type": response_type, "reported_model": reported_model,
        }
    if body is None:
        return {
            "compatible": False,
            "message": "地址返回成功状态，但响应不是有效 JSON；可能是网页或网关。Codex 无法使用它。",
            "warnings": warnings, "response_type": "", "reported_model": "",
        }
    if response_type == "chat.completion":
        return {
            "compatible": False,
            "message": "地址返回的是 Chat Completions 结构。Codex 只支持 Responses 协议，不能切换协议。",
            "warnings": warnings, "response_type": response_type, "reported_model": reported_model,
        }
    looks_like_responses = (
        response_type == "response"
        or isinstance(body.get("output"), list)
        or (isinstance(body.get("id"), str) and body["id"].startswith("resp_"))
    )
    if not looks_like_responses:
        return {
            "compatible": False,
            "message": "地址返回了成功状态，但响应结构不是 Responses（缺少 response 或 output）。Codex 按 Responses 解析，可能无法使用。",
            "warnings": warnings, "response_type": response_type, "reported_model": reported_model,
        }
    warnings.append("测试成功只说明这个地址接受了一次最短的 Responses 请求；工具调用、长上下文和流式输出仍需在 Codex 新会话中验证。")
    message = "服务端接受了 Responses 请求并返回响应对象；协议兼容，凭据可用。"
    return {
        "compatible": True, "message": message, "warnings": warnings,
        "response_type": response_type, "reported_model": reported_model,
    }


def read_capped(response):
    deadline = time.monotonic() + BODY_DEADLINE
    chunks = []
    total = 0
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError()
        chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
        if time.monotonic() >= deadline:
            raise TimeoutError()
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_BYTES:
            raise ConfigError("连接测试响应过大，已停止读取，无法判断协议兼容性。", "connection_too_large", 502)
    return b"".join(chunks)


def send(endpoint, secret, model):
    headers = {
        "Accept": "application/json", "Accept-Encoding": "identity",
        "Content-Type": "application/json", "User-Agent": "CodexLocalConfigurator/1.1",
    }
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = urllib.request.Request(endpoint, data=request_body(model), headers=headers, method="POST")
    # Same discipline as the model list: no ambient proxies, no redirect following,
    # and a portable CA bundle so certificate validation is never skipped.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.getcode()
            if response.geturl() != endpoint:
                raise ConfigError("接口发生了跳转。为避免密钥泄露，未跟随跳转；请填写最终 API 地址。", "connection_redirect", 502)
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ConfigError("连接测试响应使用了不支持的压缩方式，无法判断协议兼容性。", "connection_encoding", 502)
            raw = read_capped(response)
            return status, raw, int((time.monotonic() - started) * 1000)
    except urllib.error.HTTPError as e:
        status = e.code
        # The upstream body may echo the credential; never read, log or return it.
        e.close()
        return status, b"", int((time.monotonic() - started) * 1000)
    except (TimeoutError, socket.timeout):
        raise ConfigError("连接测试超时。请检查网络或服务商状态；未保存任何凭据或配置。", "connection_timeout", 504) from None
    except (urllib.error.URLError, ssl.SSLError, OSError, http.client.HTTPException, ValueError):
        raise ConfigError("无法连接该地址。请检查地址、网络或 TLS 证书；本工具不会跳过证书校验。", "connection_network", 502) from None


def test_connection(store, payload):
    endpoint, secret, model = resolve(store, payload)
    status, raw, latency_ms = send(endpoint, secret, model)
    result = classify(status, parse_body(raw), secret)
    result.update({
        "protocol": PROTOCOL, "endpoint": endpoint, "model": model,
        "http_status": status, "latency_ms": latency_ms, "connection_tested": True,
    })
    return result
