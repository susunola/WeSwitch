"""Preview-first Codex provider configuration, preserving unrelated TOML and comments."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote
import plistlib

import tomlkit
from keychain import Keychain, KeychainError, SERVICE


MAX_MODELS_PER_PROVIDER = 100
# Codex profiles are the supported way to keep several models under one provider.
PROFILE_KEYS = ("model", "model_provider", "model_reasoning_effort")


class ConfigError(Exception):
    def __init__(self, message, code="invalid", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=".codex-ui-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        # Only removes our own uncommitted temporary file, never an existing user file.
        if os.path.exists(name):
            os.unlink(name)


def safe_url(raw):
    """Expose only noncredential endpoint information in state responses."""
    try:
        p = urlsplit(str(raw))
        if p.username or p.password or p.query or p.fragment:
            return ""
        return str(raw)
    except ValueError:
        return ""


def validate_url(value):
    try:
        p = urlsplit(value)
        if not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:
            raise ValueError()
        decoded_path = unquote(p.path)
        if (any(part in (".", "..") for part in decoded_path.split("/"))
                or re.search(r"%(?:2f|5c|25|00|0a|0d)", p.path, re.I)
                or any(ord(c) < 32 for c in decoded_path) or "\\" in decoded_path):
            raise ValueError()
        if p.scheme != "https" and not (
            p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError()
        if any(x.isspace() or ord(x) < 32 for x in value) or "\\" in value:
            raise ValueError()
        if p.port is not None and not 1 <= p.port <= 65535:
            raise ValueError()
        if p.path.rstrip("/").endswith(("/responses", "/chat/completions", "/messages")):
            raise ConfigError("请填写 API 基础地址，不要包含 /responses、/chat/completions 或 /messages。")
        return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), "", ""))
    except ValueError:
        raise ConfigError("API 地址必须为 HTTPS，或本机 localhost / 127.0.0.1 的 HTTP；不允许内嵌账号、查询参数或片段。") from None


def validate_model(value):
    # Control characters are rejected, never silently trimmed into a different model ID.
    if any(ord(c) < 32 or ord(c) == 127 for c in str(value)):
        raise ConfigError("模型 ID 为空、过长或包含空格；请填写服务商提供的精确 ID。")
    value = str(value).strip()
    if not value or len(value) > 200 or re.search(r"\s", value):
        raise ConfigError("模型 ID 为空、过长或包含空格；请填写服务商提供的精确 ID。")
    return value


def profile_slug(model):
    """Derive a Codex profile name from a model ID."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug) or "model"


def text(payload, name, limit=200, required=True):
    value = payload.get(name, "")
    if not isinstance(value, str):
        raise ConfigError(f"字段 {name} 必须是文本。")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfigError(f"字段 {name} 包含控制字符。")
    value = value.strip()
    if (required and not value) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfigError(f"字段 {name} 为空、过长或包含控制字符。")
    return value


def normalize(payload):
    if not isinstance(payload, dict):
        raise ConfigError("请求格式必须为 JSON 对象。")
    provider_id = text(payload, "provider_id", 64)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_id):
        raise ConfigError("供应商 ID 请以小写字母开头，仅使用小写字母、数字、下划线和短横线。")
    if provider_id in {"openai", "ollama", "lmstudio", "azure", "openai-chat-completions"}:
        raise ConfigError("请使用独立供应商 ID，例如 my-gateway；不要覆盖内置供应商。")
    raw_models = payload.get("models")
    if raw_models is None and isinstance(payload.get("model"), str) and payload["model"].strip():
        # v0.1 clients send a single model; keep accepting it.
        raw_models = [payload["model"]]
    if not isinstance(raw_models, list) or not raw_models:
        raise ConfigError("请至少添加一个模型 ID，可一次添加多个。")
    if len(raw_models) > MAX_MODELS_PER_PROVIDER:
        raise ConfigError(f"一次最多添加 {MAX_MODELS_PER_PROVIDER} 个模型；请分批添加。")
    models = []
    for item in raw_models:
        if not isinstance(item, str):
            raise ConfigError("模型 ID 必须是文本。")
        model = validate_model(item)
        if model in models:
            raise ConfigError("模型 ID 重复，请删除重复项后重试。")
        models.append(model)
    default_model = validate_model(payload.get("default_model", models[0]))
    if default_model not in models:
        raise ConfigError("默认模型必须是已添加的模型之一。")
    auth_mode = text(payload, "auth_mode", 20)
    if auth_mode not in {"keychain", "env", "none"}:
        raise ConfigError("不支持的认证方式。")
    base_url = validate_url(text(payload, "base_url", 2048))
    if auth_mode == "none" and urlsplit(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError("无密钥模式仅限本机服务；远程服务请选择钥匙串或环境变量。")
    env_key = text(payload, "env_key", 128, False) if auth_mode == "env" else ""
    if auth_mode == "env" and not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", env_key):
        raise ConfigError("环境变量名仅使用大写字母、数字和下划线，且不能以数字开头。")
    effort = text(payload, "reasoning_effort", 20, False)
    if effort not in {"", "none", "minimal", "low", "medium", "high", "xhigh"}:
        raise ConfigError("不支持的推理强度。")
    for key in ("set_default", "has_key"):
        if type(payload.get(key, True if key == "set_default" else False)) is not bool:
            raise ConfigError(f"字段 {key} 必须为布尔值。")
    save_profiles = payload.get("save_profiles", True)
    if type(save_profiles) is not bool:
        raise ConfigError("字段 save_profiles 必须为布尔值。")
    if len(provider_id) + 1 + max(len(profile_slug(m)) for m in models) > 80:
        raise ConfigError("模型 ID 过长，无法生成合规的 profile 名称；请缩短模型 ID 或供应商 ID。")
    return {
        "provider_id": provider_id,
        "name": text(payload, "name", 120),
        "base_url": base_url,
        "models": models,
        "default_model": default_model,
        "model": default_model,
        "save_profiles": save_profiles,
        "auth_mode": auth_mode,
        "env_key": env_key,
        "reasoning_effort": effort,
        "set_default": payload.get("set_default", True),
        "has_key": payload.get("has_key", False),
    }


def managed_account(provider):
    auth = provider.get("auth", {})
    if not isinstance(auth, dict) or auth.get("command") != "/usr/bin/security":
        return None
    args = auth.get("args", [])
    if (len(args) == 6 and args[:3] == ["find-generic-password", "-s", SERVICE]
            and args[3] == "-a" and args[5] == "-w" and isinstance(args[4], str)
            and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}\.[a-f0-9]{24}", args[4])):
        return args[4]
    return None


def managed_profile(profile):
    """True only when a profile carries just the keys this tool writes."""
    return isinstance(profile, dict) and set(profile) <= set(PROFILE_KEYS)


def profile_names(provider_id, models):
    return [(model, provider_id + "-" + profile_slug(model)) for model in models]


class ConfigStore:
    def __init__(self, home, keychain=None, demo=False):
        original = Path(home).expanduser()
        self.home = original.resolve()
        self.path = self.home / "config.toml"
        self.backup_root = self.home / "model-ui-backups"
        self.keychain = keychain if keychain is not None else Keychain()
        self.demo = demo
        self.plans = {}
        self.lock = threading.RLock()

    def snapshot(self):
        if self.path.is_symlink():
            raise ConfigError("config.toml 是符号链接。为避免改错目标，本工具不自动写入，请先确认实际配置位置。")
        if not self.path.exists():
            return b"", "missing", {}, tomlkit.document()
        if not self.path.is_file() or self.path.stat().st_size > 4 * 1024 * 1024:
            raise ConfigError("配置不是普通文件或超过 4 MB，停止操作。")
        raw = self.path.read_bytes()
        try:
            plain = tomllib.loads(raw.decode("utf-8"))
            doc = tomlkit.parse(raw.decode("utf-8"))
            providers = plain.get("model_providers", {})
            if not isinstance(providers, dict) or any(not isinstance(v, dict) for v in providers.values()):
                raise ValueError()
            return raw, digest(raw), plain, doc
        except (ValueError, TypeError, tomlkit.exceptions.ParseError):
            raise ConfigError("现有 config.toml 不是有效 UTF-8 TOML。未做修改，请先修复原文件。") from None

    def app_info(self):
        for app in (Path("/Applications/Codex.app"), Path("/Applications/ChatGPT.app"), Path.home() / "Applications/Codex.app"):
            info = app / "Contents/Info.plist"
            if info.is_file():
                try:
                    d = plistlib.loads(info.read_bytes())
                    meta = app / "Contents/Resources/codex-cli/codex-package.json"
                    version = json.loads(meta.read_text()).get("version") if meta.is_file() else None
                    return {"path": str(app), "version": d.get("CFBundleShortVersionString"), "cli_version": version}
                except (ValueError, OSError):
                    continue
        return {"path": None, "version": None, "cli_version": None}

    def catalog_info(self, data):
        raw = data.get("model_catalog_json")
        result = {"path": raw if isinstance(raw, str) else None, "count": 0, "preserved": True}
        slugs = []
        if isinstance(raw, str):
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = self.home / p
            try:
                if p.stat().st_size <= 16 * 1024 * 1024:
                    catalog = json.loads(p.read_bytes())
                    models = catalog.get("models", [])
                    if not isinstance(models, list):
                        raise ValueError()
                    slugs = [m["slug"] for m in models if isinstance(m, dict) and isinstance(m.get("slug"), str)]
                    result["count"] = len(models)
            except (OSError, ValueError, AttributeError):
                result["warning"] = "模型目录无法读取或格式异常；本工具不会覆盖它。"
        return result, slugs

    def state(self):
        with self.lock:
            _, revision, data, _ = self.snapshot()
            catalog, _ = self.catalog_info(data)
            providers = []
            for key, provider in data.get("model_providers", {}).items():
                account = managed_account(provider)
                switchable = []
                for name, profile in (data.get("profiles") or {}).items():
                    if (isinstance(profile, dict) and profile.get("model_provider") == key
                            and managed_profile(profile) and isinstance(profile.get("model"), str)):
                        switchable.append({"profile": name, "model": profile["model"]})
                providers.append({
                    "id": key, "name": provider.get("name", key),
                    "base_url": safe_url(provider.get("base_url", "")),
                    "model": data.get("model", "") if data.get("model_provider") == key else "",
                    "switchable_models": switchable,
                    "auth_mode": "keychain" if account else "env" if provider.get("env_key") else "external" if provider.get("auth") or provider.get("requires_openai_auth") or provider.get("experimental_bearer_token") else "none",
                    "env_key": provider.get("env_key", ""), "managed_credential": bool(account),
                })
            backups = []
            if self.backup_root.is_dir() and not self.backup_root.is_symlink():
                for p in sorted(self.backup_root.iterdir(), reverse=True)[:30]:
                    if not p.is_dir() or p.is_symlink() or not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8}", p.name):
                        continue
                    try:
                        manifest = json.loads((p / "manifest.json").read_text())
                        backups.append({"id": p.name, "path": str(p), "created": manifest["created"]})
                    except (OSError, ValueError, KeyError):
                        continue
            warnings = ["仅在你确认获取列表时请求服务商 /models；不会发送模型生成请求。获取列表与配置写入是独立操作。"]
            if catalog.get("warning"):
                warnings.append(catalog["warning"])
            return {
                "config_path": str(self.path), "revision": revision,
                "current": {"model": data.get("model"), "model_provider": data.get("model_provider", "openai"),
                            "reasoning_effort": data.get("model_reasoning_effort")},
                "catalog": catalog, "app": self.app_info(), "providers": providers,
                "backups": backups, "keychain_available": self.keychain.available,
                "warnings": warnings, "demo": self.demo,
            }

    def preview(self, payload):
        f = normalize(payload)
        with self.lock:
            raw, revision, data, doc = self.snapshot()
            provider_id = f["provider_id"]
            existing = data.get("model_providers", {}).get(provider_id, {})
            if (existing.get("auth") and not managed_account(existing)) or existing.get("requires_openai_auth"):
                raise ConfigError("此供应商使用已有的外部认证方式。为避免移除认证，请新建独立供应商 ID。")
            allowed = {"name", "base_url", "wire_api", "auth", "env_key", "requires_openai_auth",
                       "request_max_retries", "stream_max_retries", "stream_idle_timeout_ms"}
            if set(existing) - allowed:
                raise ConfigError("该供应商含额外认证、请求头或高级设置。为避免误覆盖，请换一个新的供应商 ID。")
            warnings = [
                "不修改 auth.json、MCP、项目权限、历史记录或应用本体。",
                "只修改用户级配置；项目配置或已有会话仍可能覆盖 model。请完全退出应用后开启新会话验证。",
                "API 必须兼容 Responses 的流式输出与工具调用；列表获取不验证这些能力。",
            ]
            account = None
            if f["auth_mode"] == "keychain":
                if not self.keychain.available:
                    raise ConfigError("本环境不支持 macOS 钥匙串；请使用环境变量方式。")
                if f["has_key"]:
                    account = provider_id + "." + secrets.token_hex(12)
                else:
                    account = managed_account(existing)
                    if not account or existing.get("base_url") != f["base_url"]:
                        raise ConfigError("新供应商或地址变更需要填写 API Key，不能把原凭据静默发送到新地址。")
                    if not self.keychain.exists(account):
                        raise ConfigError("原钥匙串凭据不存在或未解锁，请重新输入 API Key。")
                warnings.append("API Key 仅保存在 macOS 钥匙串；桌面端首次读取时可能请求钥匙串授权。")
            elif f["has_key"]:
                raise ConfigError("只有钥匙串模式可以接收 API Key。")
            elif f["auth_mode"] == "env":
                warnings.append("环境变量必须在桌面应用进程中可见；本工具不写 shell 配置，也不注入 Dock 环境。")
            else:
                warnings.append("无密钥模式只适用于本机无需认证的服务。")
            catalog, slugs = self.catalog_info(data)
            missing = [m for m in f["models"] if m not in slugs]
            if missing:
                warnings.append(
                    f"这些模型不在现有本地目录中：{'、'.join(missing)}"
                    f"。将保留目录不动，桌面选择器可能显示 Custom、隐藏它们或拒绝选择；需要重启后验证。")
            targets = profile_names(f["provider_id"], f["models"])
            existing_profiles = data.get("profiles") or {}
            if not isinstance(existing_profiles, dict):
                raise ConfigError("现有 profiles 配置格式异常，未做修改。")
            if f["save_profiles"]:
                reserved = data.get("profile")
                if isinstance(reserved, str) and not managed_profile(existing_profiles.get(reserved, {})):
                    reserved = None
                for _, name in targets:
                    current = existing_profiles.get(name)
                    if current is not None and (
                            not managed_profile(current) or current.get("model_provider") != f["provider_id"]):
                        raise ConfigError(f"profile 名称 {name} 已被占用且含本工具不写入的设置。请换个供应商 ID，或手动改名/删除该 profile 后重试。")
            provider = tomlkit.table()
            provider.add("name", f["name"])
            provider.add("base_url", f["base_url"])
            provider.add("wire_api", "responses")
            provider.add("requires_openai_auth", False)
            for key in ("request_max_retries", "stream_max_retries", "stream_idle_timeout_ms"):
                if key in existing:
                    provider.add(key, existing[key])
            if account:
                auth = tomlkit.table()
                auth.add("command", "/usr/bin/security")
                auth.add("args", ["find-generic-password", "-s", SERVICE, "-a", account, "-w"])
                auth.add("timeout_ms", 30000)
                auth.add("refresh_interval_ms", 300000)
                provider.add("auth", auth)
            elif f["auth_mode"] == "env":
                provider.add("env_key", f["env_key"])
            snippet = tomlkit.document()
            changes = [f"{'更新' if existing else '添加'}供应商：{provider_id}", "保留现有模型目录和其他供应商"]
            if len(f["models"]) > 1:
                changes.append(f"一次添加 {len(f['models'])} 个模型：{'、'.join(f['models'])}")
            if f["save_profiles"]:
                others = [n for m, n in targets if m != f["default_model"]]
                changes.append(f"为其余模型写入可切换 profile：{'、'.join(others)}" if others
                               else f"写入可切换 profile：{targets[0][1]}")
            if f["set_default"]:
                for target in (doc, snippet):
                    target["model"] = f["model"]
                    target["model_provider"] = provider_id
                    if f["reasoning_effort"]:
                        target["model_reasoning_effort"] = f["reasoning_effort"]
                    else:
                        target.pop("model_reasoning_effort", None)
                changes.append(f"默认模型：{data.get('model', '未设置')} → {f['model']}")
                # These are model-specific; stale options often break third-party endpoints.
                for key in ("model_reasoning_summary", "model_verbosity", "model_context_window", "model_auto_compact_token_limit"):
                    if key in doc:
                        doc.pop(key)
                        changes.append(f"移除旧模型专属参数：{key}（原值完整保留在备份中）")
                if data.get("model_reasoning_effort") and not f["reasoning_effort"]:
                    changes.append("清除旧的推理强度，避免向新模型发送不支持的选项")
            else:
                changes.append("只保存供应商；不切换当前模型（模型 ID 与推理强度不会写入默认设置）")
            if f["reasoning_effort"]:
                warnings.append("推理强度由你指定；请确认上游模型支持该值。")
            if "model_providers" not in doc:
                doc["model_providers"] = tomlkit.table()
            doc["model_providers"][provider_id] = provider
            snippet["model_providers"] = tomlkit.table()
            snippet["model_providers"][provider_id] = copy.deepcopy(provider)
            managed_names = {name for _, name in targets}
            if f["save_profiles"]:
                if "profiles" not in doc:
                    doc["profiles"] = tomlkit.table()
                for model, name in targets:
                    profile = tomlkit.table()
                    profile.add("model", model)
                    profile.add("model_provider", provider_id)
                    if f["reasoning_effort"]:
                        profile.add("model_reasoning_effort", f["reasoning_effort"])
                    doc["profiles"][name] = profile
                    (snippet.setdefault("profiles", tomlkit.table()))[name] = copy.deepcopy(profile)
            # Stale profiles of this provider that are no longer in the batch would silently
            # point at models the user removed, so drop only those this tool manages.
            for name, current in list((existing_profiles or {}).items()):
                if (name not in managed_names and managed_profile(current)
                        and current.get("model_provider") == provider_id):
                    doc.get("profiles", {}).pop(name, None)
                    changes.append(f"移除不再选择的托管 profile：{name}（原值完整保留在备份中）")
            rendered = tomlkit.dumps(doc).encode("utf-8")
            # Independent parser catches root/table placement regressions before writing.
            parsed = tomllib.loads(rendered.decode("utf-8"))
            if f["set_default"] and (parsed.get("model") != f["model"] or parsed.get("model_provider") != provider_id):
                raise ConfigError("生成的配置校验失败，停止写入。", "internal", 500)
            if f["save_profiles"]:
                parsed_profiles = parsed.get("profiles", {})
                for model, name in targets:
                    entry = parsed_profiles.get(name)
                    if (not isinstance(entry, dict) or entry.get("model") != model
                            or entry.get("model_provider") != provider_id
                            or (f["reasoning_effort"] and entry.get("model_reasoning_effort") != f["reasoning_effort"])):
                        raise ConfigError("生成的 profile 校验失败，停止写入。", "internal", 500)
            untouched_before = copy.deepcopy(data)
            untouched_after = copy.deepcopy(parsed)
            for d in (untouched_before, untouched_after):
                providers = d.get("model_providers", {})
                providers.pop(provider_id, None)
                if not providers:
                    d.pop("model_providers", None)
                kept = d.get("profiles", {})
                for name, current in list(kept.items()):
                    if name in managed_names or (managed_profile(current)
                                                 and current.get("model_provider") == provider_id):
                        kept.pop(name)
                if not kept:
                    d.pop("profiles", None)
                if f["set_default"]:
                    for key in ("model", "model_provider", "model_reasoning_effort", "model_reasoning_summary", "model_verbosity", "model_context_window", "model_auto_compact_token_limit"):
                        d.pop(key, None)
            if untouched_before != untouched_after:
                raise ConfigError("检测到无关设置发生变化，已阻止写入。", "internal", 500)
            plan_id = secrets.token_urlsafe(24)
            backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
            public = {
                "plan_id": plan_id, "revision": revision, "changes": changes, "warnings": warnings,
                "files": [str(self.path), str(self.backup_root / backup_id / "config.toml")],
                "snippet": tomlkit.dumps(snippet), "provider_id": provider_id,
                "model": f["model"], "models": f["models"], "default_model": f["default_model"],
                "set_default": f["set_default"], "save_profiles": f["save_profiles"],
                "profiles": [{"model": m, "profile": n, "default": m == f["default_model"]}
                             for m, n in targets] if f["save_profiles"] else [],
            }
            self.plans = {k: v for k, v in self.plans.items() if time.monotonic() - v["created"] < 900}
            if len(self.plans) >= 100:
                self.plans.pop(next(iter(self.plans)))
            self.plans[plan_id] = {"form": f, "raw": raw, "revision": revision, "rendered": rendered,
                                   "account": account, "backup_id": backup_id, "created": time.monotonic()}
            return public

    def apply(self, payload):
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise ConfigError("请先预览并明确确认修改。", "confirmation_required")
        f = normalize(payload)
        secret = payload.get("api_key", "")
        if not isinstance(secret, str) or len(secret) > 8192:
            raise ConfigError("API Key 格式不正确。")
        if secret and (secret != secret.strip() or any(ord(c) < 33 or ord(c) == 127 for c in secret)):
            raise ConfigError("API Key 不能包含空格或换行。")
        if bool(secret) != f["has_key"]:
            raise ConfigError("密钥输入状态已改变，请重新预览。", "stale_preview", 409)
        with self.lock:
            plan = self.plans.get(payload.get("plan_id"))
            if not plan or time.monotonic() - plan["created"] >= 900:
                raise ConfigError("预览已过期，请重新预览。", "stale_preview", 409)
            if plan["form"] != f or payload.get("revision") != plan["revision"]:
                raise ConfigError("表单与预览不一致，请重新预览。", "stale_preview", 409)
            _, revision, _, _ = self.snapshot()
            if revision != plan["revision"]:
                raise ConfigError("Codex 配置已被其他程序修改；为避免覆盖，请刷新并重新预览。", "conflict", 409)
            if self.backup_root.is_symlink():
                raise ConfigError("备份目录是符号链接，已停止操作。")
            if not self.home.exists():
                self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.backup_root.exists():
                self.backup_root.mkdir(exist_ok=True, mode=0o700)
            if not self.backup_root.is_dir():
                raise ConfigError("无法创建安全备份目录。")
            os.chmod(self.backup_root, 0o700)
            # Serialize independent instances of this application; Codex still gets CAS protection.
            import fcntl
            lock_path = self.backup_root / ".write.lock"
            flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(lock_path, flags, 0o600), "wb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if self.snapshot()[1] != plan["revision"]:
                    raise ConfigError("配置已变化，请刷新并重新预览。", "conflict", 409)
                backup = self.backup_root / plan["backup_id"]
                backup.mkdir(mode=0o700)
                if plan["revision"] != "missing":
                    shutil.copy2(self.path, backup / "config.toml", follow_symlinks=False)
                    os.chmod(backup / "config.toml", 0o600)
                    if digest((backup / "config.toml").read_bytes()) != plan["revision"]:
                        raise ConfigError("备份校验失败，未修改配置。", "backup_failed", 500)
                manifest = {
                    "created": datetime.now(timezone.utc).isoformat(), "config_path": str(self.path),
                    "existed": plan["revision"] != "missing", "before_sha256": plan["revision"],
                    "after_sha256": digest(plan["rendered"]), "provider_id": f["provider_id"],
                    "models": f["models"], "default_model": f["default_model"],
                    "profiles": [{"model": m, "profile": n} for m, n in profile_names(f["provider_id"], f["models"])] if f["save_profiles"] else [],
                    "credential_recovery": "Existing Keychain items were not overwritten or deleted.",
                    "applied": False,
                }
                atomic_write(backup / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
                try:
                    if secret:
                        self.keychain.add(plan["account"], secret)
                    if self.snapshot()[1] != plan["revision"]:
                        raise ConfigError("保存期间配置发生变化，已停止写入；新钥匙串条目可能已建立但未启用。", "conflict", 409)
                    atomic_write(self.path, plan["rendered"])
                except KeychainError as e:
                    raise ConfigError(str(e), "keychain_failed") from None
                except OSError:
                    raise ConfigError("配置写入失败；备份保留在 model-ui-backups。新钥匙串条目可能存在但未启用。", "write_failed", 500) from None
                if self.path.read_bytes() != plan["rendered"]:
                    raise ConfigError("写入后文件再次发生变化。请检查备份与配置，不要继续重复应用。", "conflict", 409)
                manifest["applied"] = True
                # The config is already committed; manifest failure must not pretend the apply failed.
                try:
                    atomic_write(backup / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
                except OSError:
                    pass
                self.plans.pop(payload["plan_id"], None)
                return {
                    "ok": True, "backup_path": str(backup), "config_path": str(self.path),
                    "provider_id": f["provider_id"], "model": f["model"], "models": f["models"],
                    "default_model": f["default_model"], "set_default": f["set_default"],
                    "save_profiles": f["save_profiles"],
                    "profiles": [{"model": m, "profile": n, "default": m == f["default_model"]}
                                 for m, n in profile_names(f["provider_id"], f["models"])] if f["save_profiles"] else [],
                    "keychain_saved": bool(secret), "connection_tested": False, "restart_required": True,
                    "message": "配置已备份并写入。请保存工作，完全退出应用（⌘Q）后重新打开，再用新会话验证模型。",
                }
