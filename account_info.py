"""Read-only facts about the signed-in Codex account, its models, and its quota.

Two of the three answers are already on disk and need no network at all:

* the plan (and subscription window) is decoded from the claims inside
  `~/.codex/auth.json`;
* the model list the desktop picker renders comes from the catalog file, or from
  the app's own account-scoped cache.

Only the live quota needs a request. It is the same one the desktop app makes:
`GET <backend>/wham/usage/plan_limit_history?days=7` with the ChatGPT access
token. That call is gated behind its own confirmation and is never made on load.

Nothing here writes a file, and no token value is ever returned to the caller:
`read_account()` returns decoded claims and expiry only. Upstream strings pass
through `connection_test.redact`, the same reviewed filter the connection probe
uses, so a value that echoes a credential cannot reach the browser.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

import certifi

from config_core import ConfigError
# Reuses the reviewed upstream-string filter from the connection probe rather than
# adding a second, differently-behaved redaction path.
from connection_test import redact
from model_discovery import NoRedirect

AUTH_NAME = "auth.json"
APP_CACHE_NAME = "models_cache.json"
LEGACY_CACHE_NAME = "opencodex-catalog.json"
MAX_AUTH_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_CLAIM_BYTES = 64 * 1024

# The claim that carries plan and subscription facts for ChatGPT logins.
AUTH_CLAIM = "https://api.openai.com/auth"

PROD_BASE = "https://chatgpt.com/backend-api"
DEV_BASE = "http://localhost:8000/api"
USAGE_PATH = "/wham/usage/plan_limit_history"
USAGE_DAYS = 7

# The desktop app sends the token with the client identity it was minted for.
ORIGINATOR = "Codex Desktop"

REQUEST_TIMEOUT = 25
BODY_DEADLINE = 30
MAX_BYTES = 256 * 1024
MAX_PERIODS = 40
MAX_BREAKDOWN_ROWS = 20

# The 5-hour and 7-day windows the endpoint reports. Labels are rendered by the
# UI: backend fields other than message/warnings are never localized.
WINDOW_MINUTES = (300, 10080)

CONSENT_MESSAGE = (
    "将读取 ~/.codex/auth.json 里的 ChatGPT 访问令牌，并用它向 chatgpt.com 查询用量。"
    "这是一次只读请求：不修改配置，不上传你的配置文件，也不保存令牌。是否继续？"
)

# Basis points: the endpoint reports usage in hundredths of a percent.
BASIS_POINTS = 10000


def _iso(seconds):
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    try:
        if seconds > 253402300799 or seconds < 0:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))
    except (OSError, OverflowError, ValueError):
        return None


def decode_claims(token):
    """Decode a JWT payload without verifying it; a malformed token yields {}.

    The signature is not checked: this only reads values the local app already
    trusted, and nothing here is used for authorization.
    """
    if not isinstance(token, str) or token.count(".") != 2 or len(token) > MAX_CLAIM_BYTES:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        claims = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError, RecursionError):
        return {}
    return claims if isinstance(claims, dict) else {}


def read_account(home):
    """Decode the local sign-in facts. Never returns a token or API key value."""
    path = Path(home) / AUTH_NAME
    result = {
        "path": str(path), "available": False, "auth_mode": None, "plan": None,
        "email": None, "account_id": None, "subscription_until": None,
        "access_token_expires_at": None, "token_expired": False,
        "usage_supported": False, "note": None,
    }
    try:
        if path.is_symlink() or not path.is_file():
            result["note"] = "auth_missing"
            return result
        if path.stat().st_size > MAX_AUTH_BYTES:
            result["note"] = "auth_unreadable"
            return result
        # Read bytes and decode strictly: a malformed file must not raise.
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        result["note"] = "auth_unreadable"
        return result
    if not isinstance(data, dict):
        result["note"] = "auth_unreadable"
        return result
    result["available"] = True
    mode = data.get("auth_mode")
    result["auth_mode"] = redact(mode, "") or None
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    id_token, access_token = tokens.get("id_token"), tokens.get("access_token")
    account_id = redact(tokens.get("account_id"), "") or None

    identity = decode_claims(id_token)
    access = decode_claims(access_token)
    # The access token carries the same identity block and is refreshed with it.
    auth_block = identity.get(AUTH_CLAIM)
    if not isinstance(auth_block, dict):
        auth_block = access.get(AUTH_CLAIM)
    auth_block = auth_block if isinstance(auth_block, dict) else {}

    result["plan"] = redact(auth_block.get("chatgpt_plan_type"), "") or None
    result["subscription_until"] = redact(auth_block.get("chatgpt_subscription_active_until"), "") or None
    result["account_id"] = redact(auth_block.get("chatgpt_account_id"), "") or account_id
    profile = access.get("https://api.openai.com/profile")
    email = profile.get("email") if isinstance(profile, dict) else identity.get("email")
    result["email"] = redact(email, "") or None
    result["access_token_expires_at"] = _iso(access.get("exp"))

    if result["auth_mode"] != "chatgpt":
        # An API key cannot read ChatGPT plan usage; say so instead of guessing.
        result["note"] = "not_chatgpt_login" if result["auth_mode"] else "auth_unreadable"
        return result
    if not isinstance(access_token, str) or not access_token:
        result["note"] = "signed_out"
        return result
    expires = access.get("exp")
    if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires < time.time():
        result["token_expired"] = True
        result["note"] = "token_expired"
        return result
    result["usage_supported"] = True
    return result


def _load_catalog(path):
    try:
        p = Path(path).expanduser()
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_CATALOG_BYTES:
            return None
        body = json.loads(p.read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        return None
    return body


def existing_models(home, configured_path, configured_slugs, managed_models):
    """Models Codex itself already offers, with duplicate markers.

    `managed_models` are model IDs this tool already wrote as profiles;
    `configured_slugs` are the entries of the catalog the picker currently renders.
    Either one means adding the model again would be a duplicate.
    """
    home = Path(home)
    candidates = []
    if configured_path is not None:
        candidates.append((configured_path, "configured_catalog"))
    candidates.append((home / APP_CACHE_NAME, "app_cache"))
    candidates.append((home / LEGACY_CACHE_NAME, "legacy_cache"))

    result = {"kind": "none", "source": None, "fetched_at": None, "client_version": None,
              "total": 0, "visible": 0, "hidden": 0, "models": [], "note": None}
    body = None
    for path, kind in candidates:
        body = _load_catalog(path)
        if body is not None:
            result.update(kind=kind, source=str(Path(path).expanduser()))
            break
    if body is None:
        result["note"] = "catalog_missing"
        return result

    result["fetched_at"] = redact(body.get("fetched_at"), "") or None
    result["client_version"] = redact(body.get("client_version"), "") or None
    managed = {m for m in managed_models if isinstance(m, str) and m}
    configured = {m for m in configured_slugs if isinstance(m, str) and m}
    seen = set()
    for entry in body["models"]:
        if not isinstance(entry, dict):
            continue
        slug = redact(entry.get("slug"), "")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        hidden = entry.get("visibility") != "list"
        upgrade = entry.get("upgrade")
        upgrade_to = redact(upgrade.get("model"), "") if isinstance(upgrade, dict) else ""
        result["models"].append({
            "slug": slug,
            "display_name": redact(entry.get("display_name"), "") or slug,
            "hidden": hidden,
            "upgrade_to": upgrade_to or None,
            "added": slug in managed or slug in configured,
            "in_current_catalog": slug in configured,
        })
        result["total"] += 1
        if hidden:
            result["hidden"] += 1
        else:
            result["visible"] += 1
    if not result["models"]:
        result["note"] = "catalog_empty"
    return result


def api_base():
    """Honour the same overrides the desktop app honours, defaulting to production."""
    override = (os.environ.get("CODEX_API_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    if (os.environ.get("CODEX_API_ENDPOINT") or "").lower() == "localhost":
        return DEV_BASE
    return PROD_BASE


def usage_endpoint():
    """The exact URL the desktop app requests, including the coverage window.

    The window is part of the request, not a formatting choice: omitting it lets
    the backend pick its own default, so the figure shown would no longer be the
    one the app itself displays.
    """
    return f"{api_base()}{USAGE_PATH}?days={USAGE_DAYS}"


def read_capped(response):
    deadline = time.monotonic() + BODY_DEADLINE
    chunks, total = [], 0
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
            raise ConfigError("用量响应过大，已停止读取，未显示任何数据。", "usage_too_large", 502)
    return b"".join(chunks)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _percent(basis_points):
    value = _number(basis_points)
    if value is None or value < 0:
        return None
    return round(value / BASIS_POINTS * 100, 2)


def _breakdowns(raw, secret):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_PERIODS]:
        if not isinstance(item, dict):
            continue
        dimension = redact(item.get("dimension"), secret)
        rows = []
        if isinstance(item.get("rows"), list):
            for row in item["rows"][:MAX_BREAKDOWN_ROWS]:
                if not isinstance(row, dict):
                    continue
                key = redact(row.get("key"), secret)
                percent = _percent(row.get("basis_points"))
                if key and percent is not None:
                    rows.append({"key": key, "percent": percent})
        if dimension and rows:
            out.append({"dimension": dimension, "rows": rows})
    return out


def parse_usage(raw, secret=""):
    """Map the documented payload onto a bounded, sanitized summary."""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        body = None
    if not isinstance(body, dict):
        return None
    periods = []
    for item in (body.get("periods") or [])[:MAX_PERIODS]:
        if not isinstance(item, dict):
            continue
        minutes = _number(item.get("window_minutes"))
        minutes = int(minutes) if minutes is not None else None
        periods.append({
            "id": redact(item.get("id"), secret),
            "window_minutes": minutes,
            "plan_type": redact(item.get("plan_type"), secret),
            "starts_at": redact(item.get("starts_at"), secret) or None,
            "ends_at": redact(item.get("ends_at"), secret) or None,
            "accounting_complete": item.get("accounting_complete") is True,
            "used_percent": _percent(item.get("used_basis_points")),
            "breakdowns": _breakdowns(item.get("breakdowns"), secret),
        })
    # Newest window first: the endpoint happens to return that order, but the UI
    # should not depend on it. False also covers a missing value, matching the
    # schema default of "approximate".
    return {
        "data_as_of": redact(body.get("data_as_of"), secret) or None,
        "coverage_start": redact(body.get("coverage_start"), secret) or None,
        "coverage_complete": body.get("coverage_complete") is True,
        "approximate": body.get("approximate") is not False,
        "periods": sorted(
            (p for p in periods if p["window_minutes"] is not None or p["used_percent"] is not None),
            key=lambda p: (p["starts_at"] or "", p["window_minutes"] or 0), reverse=True),
    }


def send(endpoint, token, account_id):
    headers = {
        "Accept": "application/json", "Accept-Encoding": "identity",
        "Authorization": "Bearer " + token, "originator": ORIGINATOR,
        "User-Agent": "CodexLocalConfigurator/1.2",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
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
                raise ConfigError("用量接口发生了跳转。为避免令牌泄露，未跟随跳转。", "usage_redirect", 502)
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ConfigError("用量响应使用了不支持的压缩方式，无法解析。", "usage_encoding", 502)
            return status, read_capped(response), int((time.monotonic() - started) * 1000)
    except urllib.error.HTTPError as e:
        # The error body could echo the token; never read or return it.
        status = e.code
        e.close()
        return status, b"", int((time.monotonic() - started) * 1000)
    except (TimeoutError, socket.timeout):
        raise ConfigError("查询用量超时。请检查网络后重试；未发送任何配置内容。", "usage_timeout", 504) from None
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError) as e:
        raise ConfigError(
            "无法连接 chatgpt.com 查询用量。请检查网络、代理或 TLS 证书；本工具不会跳过证书校验。",
            "usage_network", 502) from e


def fetch_usage(store, payload):
    """Query the ChatGPT backend for the signed-in account, after confirmation."""
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise ConfigError(CONSENT_MESSAGE, "confirmation_required")
    home = getattr(store, "home", None)
    account = read_account(home)
    if not account["usage_supported"]:
        note = account.get("note")
        if note == "auth_missing":
            message = "没有找到 ~/.codex/auth.json：请先用 ChatGPT 账号登录 Codex，再查询用量。"
        elif note == "token_expired":
            message = "本机保存的 ChatGPT 登录令牌已过期。请在 Codex 里发一条消息让它自动刷新，然后重试。"
        elif note == "not_chatgpt_login":
            message = "当前 Codex 使用 API Key 登录，没有 ChatGPT 套餐用量可查。"
        else:
            message = "无法读取本机登录信息，因此无法查询用量。"
        raise ConfigError(message, "usage_unavailable")
    token = _access_token(home)
    if not token:
        raise ConfigError("无法读取本机登录信息，因此无法查询用量。", "usage_unavailable")
    endpoint = usage_endpoint()
    status, raw, latency_ms = send(endpoint, token, account["account_id"])
    result = {
        "ok": True, "endpoint": api_base(), "queried_at": _iso(time.time()),
        "http_status": status, "latency_ms": latency_ms,
        "plan": account["plan"], "subscription_until": account["subscription_until"],
    }
    if status == 404:
        result.update({"periods": [], "data_as_of": None, "coverage_start": None,
                       "coverage_complete": False, "approximate": True,
                       "message": "这个账号暂时没有可用的用量数据（接口返回 404）。"})
        return result
    if not 200 <= status < 300:
        if status in (401, 403):
            raise ConfigError("用量接口拒绝了这次请求：登录已过期或未授权。请在 Codex 里重新登录后重试。",
                              "usage_unauthorized", 401)
        if status == 429:
            raise ConfigError("用量接口限流，请稍后重试。", "usage_rate_limited", 429)
        raise ConfigError(f"用量接口返回 HTTP {status}。为避免泄露令牌，未读取错误正文；请稍后重试。",
                          "usage_failed", 502)
    parsed = parse_usage(raw, token)
    if parsed is None:
        raise ConfigError("用量接口返回了成功状态，但响应不是有效 JSON，无法解析。", "usage_invalid", 502)
    result.update(parsed)
    result["message"] = "用量数据来自 chatgpt.com 的官方接口；仅供参考，以 Codex 界面显示为准。"
    return result


def _access_token(home):
    """Read the access token for the request only; the value never leaves this module."""
    try:
        data = json.loads((Path(home) / AUTH_NAME).read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    tokens = data.get("tokens") if isinstance(data, dict) else None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]{20,8192}", token):
        return ""
    return token
