"""Explicitly requested, read-only GET /models. No configuration or credential writes."""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import certifi

from config_core import ConfigError, managed_account, text, validate_url
from keychain import KeychainError

MAX_BYTES = 2 * 1024 * 1024
MAX_MODELS = 5000
REQUEST_TIMEOUT = 12
BODY_DEADLINE = 15


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization to a redirected destination, even on the same host.
        return None


def valid_token(value):
    if (not isinstance(value, str) or not value or len(value) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ConfigError("API Key 为空、过长或包含空格/换行/非 ASCII 字符。请核对后重试。")
    return value


def resolve_credentials(store, payload, consent_message):
    """Resolve an explicit, endpoint-bound credential. Returns (base_url, secret).

    Shared by the read-only model list and the one-shot connection test so both
    follow the same never-reuse-a-secret-on-another-address rule. The caller
    supplies the consent message, because only it knows which button was pressed.
    """
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise ConfigError(consent_message, "confirmation_required")
    base = validate_url(text(payload, "base_url", 2048))
    if urlsplit(base).path.rstrip("/").endswith("/models"):
        raise ConfigError("请填写 API 基础地址，不要包含 /models；本工具会自动拼接。")
    mode = text(payload, "auth_mode", 20)
    if mode not in {"keychain", "env", "none"}:
        raise ConfigError("请选择钥匙串、环境变量或无认证模式。")
    secret = payload.get("api_key", "")
    if not isinstance(secret, str):
        raise ConfigError("API Key 必须为文本。")
    if mode != "keychain" and secret:
        raise ConfigError("只有钥匙串模式允许提供 API Key。")
    if mode == "none":
        if urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ConfigError("无认证模式仅限本机服务。远程服务请选择认证方式。")
        return base, ""
    if mode == "keychain" and secret:
        # Brand-new entries can be queried before choosing an ID/name/model or saving anything.
        return base, valid_token(secret)
    provider_id = text(payload, "provider_id", 64)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id):
        raise ConfigError("要复用凭据，请填写有效的已保存提供方 ID。")
    with store.lock:
        _, revision, config, _ = store.snapshot()
        provider = config.get("model_providers", {}).get(provider_id, {})
    try:
        # Endpoint-bound reuse: never release stored secrets to an edited address.
        matches = validate_url(str(provider.get("base_url", ""))) == base
    except ConfigError:
        matches = False
    if not matches:
        raise ConfigError("无法将已保存凭据用于新地址。请选钥匙串模式并在表单中填写此服务的 API Key；获取列表不会保存它。", "credential_endpoint_mismatch")
    if mode == "keychain":
        account = managed_account(provider)
        if not account:
            raise ConfigError("此提供方没有由本工具托管的凭据，请填写 API Key。", "credential_missing")
        try:
            secret = store.keychain.read(account)
        except KeychainError:
            raise ConfigError("钥匙串读取失败或未获授权。请解锁钥匙串，或直接填写 API Key 后重试。", "credential_missing") from None
    else:
        env_key = text(payload, "env_key", 128)
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", env_key):
            raise ConfigError("环境变量名无效。")
        if provider.get("env_key") != env_key or provider.get("auth") or provider.get("requires_openai_auth"):
            raise ConfigError("获取列表仅可复用同一已保存提供方的环境变量。不读取其他系统变量；新服务请用钥匙串模式临时填写 Key。", "credential_missing")
        secret = os.environ.get(env_key, "")
        if not secret:
            raise ConfigError("配置工具进程中没有这个环境变量。可切换钥匙串模式临时填写 Key，或继续手动填写模型 ID。", "credential_missing")
    if store.snapshot()[1] != revision:
        raise ConfigError("读取凭据期间配置发生变化，未发出请求；请刷新状态后重试。", "conflict", 409)
    return base, valid_token(secret)


def parse_models(raw, secret=""):
    if len(raw) > MAX_BYTES:
        raise ConfigError("模型列表响应超过 2 MB，已停止读取；请手动填写模型 ID。", "models_too_large", 502)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ConfigError("服务未返回有效 JSON 模型列表，可能是网页或网关错误；请核对基础地址，或手动填写。", "models_invalid", 502) from None
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ConfigError("接口未返回兼容的 data 模型数组；可继续手动填写服务商给出的模型 ID。", "models_unsupported", 502)
    if len(body["data"]) > MAX_MODELS:
        raise ConfigError("模型数量超过 5000，已停止解析；请缩小服务商列表范围或手动填写。", "models_too_large", 502)
    ids = set()
    invalid = 0
    for item in body["data"]:
        model_id = item.get("id") if isinstance(item, dict) else None
        if (not isinstance(model_id, str) or not model_id or len(model_id) > 200
                or re.search(r"\s", model_id) or any(ord(c) < 32 or ord(c) == 127 for c in model_id)
                or (secret and secret in model_id)):
            invalid += 1
            continue
        ids.add(model_id)
    if body["data"] and not ids:
        raise ConfigError("列表中没有合法的模型 ID；请手动填写，未显示上游原始响应。", "models_invalid", 502)
    warnings = ["列表来自服务商，只证明该接口列出了模型；未验证 Responses、流式输出或工具调用能力。"]
    if invalid:
        warnings.append(f"忽略了 {invalid} 条格式不正确或包含敏感内容的记录。")
    if not ids:
        warnings.append("服务商返回空列表，可能没有模型权限。仍可手动填写模型 ID。")
    # Never follow pagination URLs automatically, because that can move credentials across origins.
    has_more = body.get("has_more") is True or bool(body.get("next") or body.get("next_page"))
    if has_more:
        warnings.append("服务商标记还有后续页，本工具仅显示本次响应，不自动跟随分页地址。")
    return {
        "models": [{"id": value} for value in sorted(ids, key=lambda s: (s.casefold(), s))],
        "count": len(ids), "warnings": warnings, "complete": not has_more,
        "connection_tested": False,
    }


def fetch_models(endpoint, secret):
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "CodexLocalConfigurator/1.1"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    # Do not copy ambient proxies, cookies, request headers or credentials from Codex sessions.
    context = ssl.create_default_context()
    # Bundle a portable CA root set; never depend on the build machine's Python paths.
    context.load_verify_locations(cafile=certifi.where())
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.getcode() != 200 or response.geturl() != endpoint:
                raise ConfigError("模型列表接口未返回成功结果；请核对基础地址。", "models_http", 502)
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ConfigError("模型列表使用了不支持的压缩响应，请手动填写模型 ID。", "models_encoding", 502)
            length = response.headers.get("Content-Length")
            if length:
                try:
                    declared = int(length)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > MAX_BYTES:
                    raise ConfigError("模型列表响应长度无效或超过 2 MB，已停止读取。", "models_too_large", 502)
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
                    raise ConfigError("模型列表响应超过 2 MB，已停止读取。", "models_too_large", 502)
            return parse_models(b"".join(chunks), secret)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()  # Never read, log or return the upstream error body (it may echo credentials).
        if code in (401, 403):
            message = "服务商拒绝认证或无模型列表权限。请检查 API Key；也可继续手动填写模型 ID。"
        elif code in (404, 405, 501):
            message = "服务商不支持此地址的 /models 列表接口。请核对基础地址，或手动填写模型 ID。"
        elif 300 <= code < 400:
            message = "接口要求跳转。为避免密钥泄露，未跟随跳转；请核对并直接填写最终 API 基础地址。"
        elif code == 429:
            message = "服务商限制了请求频率，请稍后手动重试或直接填写模型 ID。"
        else:
            message = f"服务商模型列表接口返回 HTTP {code}；未读取错误正文，请稍后重试或手动填写。"
        # Upstream auth errors must not be confused with local UI session expiry.
        raise ConfigError(message, "models_http", 502) from None
    except (TimeoutError, socket.timeout):
        raise ConfigError("获取模型列表超时，请检查网络或手动填写模型 ID。", "models_timeout", 504) from None
    except (urllib.error.URLError, ssl.SSLError, OSError, http.client.HTTPException, ValueError):
        raise ConfigError("无法连接模型列表接口。请检查地址、网络或 TLS 证书；本工具不会跳过证书校验。", "models_network", 502) from None


CONSENT_MESSAGE = "请确认目标地址并点击获取模型列表，才会发送请求。"


def discover_models(store, payload):
    base, secret = resolve_credentials(store, payload, CONSENT_MESSAGE)
    result = fetch_models(base + "/models", secret)
    result["endpoint"] = base + "/models"
    return result
