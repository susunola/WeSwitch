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
# The only wire protocol Codex accepts. `wire_api = "chat"` was removed from
# codex-cli, so there is no protocol to choose; only base_url may differ.
WIRE_API = "responses"
# Where the desktop app installs, in the order to check. Shared so the binary
# lookup and the version panel can never drift apart.
APP_CANDIDATES = (Path("/Applications/Codex.app"), Path("/Applications/ChatGPT.app"),
                  Path.home() / "Applications/Codex.app")
# Evidence: the codex-cli 0.159.2 binary serializes its reasoning-effort enum as
# none/minimal/low/medium/high/xhigh/max/ultra/persistent, in that order. Anything
# outside this set is rejected by Codex before it ever reaches the provider.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent")
# Backup directory names start with a UTC timestamp, so sorting them as strings
# is sorting them in the order they were taken.
BACKUP_ID = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{8}")
# Retention: the newest MAX_BACKUPS, plus the oldest one. The oldest is pinned
# because it is the only copy of the configuration as it was before this tool
# was ever applied; without it, a run of applies would delete the way back.
MAX_BACKUPS = 5


class ConfigError(Exception):
    def __init__(self, message, code="invalid", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    # os.replace replaces a symlink node, but a symlinked parent would make the
    # temporary file land outside the directory the caller checked.
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError(f"refusing to write through a symlink: {path}")
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
    if effort not in ("", *REASONING_EFFORTS):
        raise ConfigError("不支持的推理强度。")
    for key in ("set_default", "has_key"):
        if type(payload.get(key, True if key == "set_default" else False)) is not bool:
            raise ConfigError(f"字段 {key} 必须为布尔值。")
    save_profiles = payload.get("save_profiles", True)
    if type(save_profiles) is not bool:
        raise ConfigError("字段 save_profiles 必须为布尔值。")
    # Older clients omit both flags, so the default is off for them.
    write_catalog = payload.get("write_catalog", False)
    force_api_login = payload.get("force_api_login", False)
    for key in ("write_catalog", "force_api_login"):
        if type(payload.get(key, False)) is not bool:
            raise ConfigError(f"字段 {key} 必须为布尔值。")
    if force_api_login and auth_mode == "none":
        raise ConfigError("强制 API Key 登录需要钥匙串或环境变量认证；无认证模式不支持。")
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
        "write_catalog": write_catalog,
        "force_api_login": force_api_login,
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
        # The built-in model list only needs to be read once per process; the
        # bundled-catalog fallback costs one Codex CLI call and `state()` polls.
        self.seed_cache = None
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
        for app in APP_CANDIDATES:
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

    def codex_binary(self):
        """Absolute path to the Codex CLI inside the installed app, or None.

        Only ever asked about the shape of a model catalog and whether it parses.
        Every caller must tolerate None: the tool stays fully usable, just
        unverified, when Codex is not installed on this machine.
        """
        for app in APP_CANDIDATES:
            base = app / "Contents/Resources/codex-cli"
            for rel in ("CodexCLI.app/Contents/MacOS/codex", "bin/codex"):
                candidate = base / rel
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
        return shutil.which("codex")

    def builtin_catalog(self):
        """The built-in models the picker shows now, for seeding a first catalog.

        Seeding from this list is what makes a catalog safe to create at all: the
        merge starts from every model the user already has, so writing the file
        cannot make the official models disappear.
        """
        with self.lock:
            if self.seed_cache is None:
                from model_catalog import read_seed
                entries, origin = read_seed(self.home, self.codex_binary())
                self.seed_cache = {"origin": origin, "count": len(entries), "entries": entries}
            return self.seed_cache

    def catalog_path(self, data):
        """Resolve `model_catalog_json` to an absolute path, or None when unset.

        Relative paths are interpreted under the Codex home, the same way the
        desktop app resolves them. Every caller must go through this helper:
        a path resolved against the process working directory instead would
        point at a different file than the one the state panel described.
        """
        raw = data.get("model_catalog_json")
        if not isinstance(raw, str) or not raw.strip():
            return None
        p = Path(raw).expanduser()
        return p if p.is_absolute() else self.home / p

    def catalog_source(self, data):
        """Return the catalog file a merge would read from, or None when there is none.

        The UI uses this to decide whether "write model catalog" can be offered at
        all. Merging from nothing would produce a catalog holding only custom models,
        which makes the built-in models disappear from the desktop picker.
        """
        p = self.catalog_path(data)
        if p is None:
            # Imported here: model_catalog reads the effort list from this module.
            from model_catalog import target_path as catalog_target_path
            p = catalog_target_path(self.home)
        return str(p) if p.is_file() and not p.is_symlink() else None

    def catalog_info(self, data):
        raw = data.get("model_catalog_json")
        # `builtin` says whether a first catalog can be created safely at all: the
        # UI only offers the merge when there is either an existing file to merge
        # into, or a built-in list to seed one from.
        seed = self.builtin_catalog()
        result = {"path": raw if isinstance(raw, str) else None, "count": 0, "preserved": True,
                  "source": self.catalog_source(data),
                  "builtin": {"origin": seed["origin"], "count": seed["count"]}}
        slugs = []
        p = self.catalog_path(data)
        if p is not None:
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
            catalog, slugs = self.catalog_info(data)
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
                    if not p.is_dir() or p.is_symlink() or not BACKUP_ID.fullmatch(p.name):
                        continue
                    try:
                        manifest = json.loads((p / "manifest.json").read_text())
                        backups.append({"id": p.name, "path": str(p), "created": manifest["created"]})
                    except (OSError, ValueError, KeyError):
                        continue
            warnings = ["仅在你确认获取列表时请求服务商 /models；不会发送模型生成请求。获取列表与配置写入是独立操作。"]
            if catalog.get("warning"):
                warnings.append(catalog["warning"])
            # Imported here: account_info reuses the connection test's redaction
            # helper, which imports this module.
            from account_info import existing_models, read_account
            managed = {entry["model"] for provider in providers for entry in provider["switchable_models"]}
            account = read_account(self.home)
            installed = existing_models(self.home, self.catalog_path(data), slugs, managed)
            return {
                "config_path": str(self.path), "revision": revision,
                "current": {"model": data.get("model"), "model_provider": data.get("model_provider", "openai"),
                            "reasoning_effort": data.get("model_reasoning_effort")},
                "catalog": catalog, "app": self.app_info(), "providers": providers,
                "account": account, "installed": installed,
                "protocol": WIRE_API,
                "backups": backups, "keychain_available": self.keychain.available,
                # Retention is enforced on the next apply or restore, never on load,
                # so merely opening the UI cannot delete anything. Exposed so the UI
                # states the rule from the same constants the pruning uses.
                "backup_keep_recent": MAX_BACKUPS, "backup_keep_oldest": True,
                "warnings": warnings, "demo": self.demo,
                "managed_models": self.managed_models(),
            }

    def preview(self, payload):
        f = normalize(payload)
        # Imported here: model_catalog reads the effort list from this module.
        from model_catalog import cache_plan
        from model_catalog import load_catalog as catalog_load
        from model_catalog import plan as catalog_plan
        from model_catalog import preview_text as catalog_preview_text
        from model_catalog import render as catalog_render
        from model_catalog import validate as catalog_validate
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
            catalog_state, slugs = self.catalog_info(data)
            missing = [m for m in f["models"] if m not in slugs]
            catalog_write = None
            catalog_changes = []
            if f["write_catalog"]:
                # Same resolution as catalog_source(), so the checkbox can never
                # promise a merge the preview then refuses.
                source = self.catalog_source(data)
                seed = self.builtin_catalog()
                if source:
                    source_entries, _ = catalog_load(source)
                else:
                    # No catalog yet. Start from the built-in list so the file we
                    # create still holds every model the picker has today; merging
                    # into nothing is what would hide the official models.
                    source_entries = seed["entries"]
                if not source_entries:
                    raise ConfigError("未找到现有模型目录：model_catalog_json 未设置，也读不到 Codex 内置模型列表。为避免让官方模型从选择器中消失，本工具不新建只包含自定义模型的目录。", "catalog_unavailable")
                catalog_write = catalog_plan(self.home, source_entries, f["models"], f["name"],
                                             f["reasoning_effort"], source_path=source or "",
                                             seed_entries=seed["entries"])
                if not catalog_write["source_count"]:
                    # Merging from an empty source would hide every built-in model.
                    raise ConfigError("现有模型目录为空或无法解析，为保证官方模型仍可选，未生成新目录。", "catalog_unavailable")
                # Ask Codex itself, in a throwaway CODEX_HOME, whether it accepts
                # the file. A rejected catalog costs every model in the picker,
                # not just the custom one, so this runs before anything is saved.
                rejected = catalog_validate(catalog_write["rendered"], self.codex_binary())
                if rejected:
                    # Codex may be rejecting an entry that was already in the user's
                    # own catalog, not the one just built. Asking about the source on
                    # its own turns "your catalog is broken" into an actionable
                    # message instead of a raw parser error naming a field the user
                    # never wrote.
                    if source and catalog_validate(catalog_render({"models": source_entries}),
                                                   self.codex_binary()):
                        raise ConfigError(f"现有模型目录本身就无法被 Codex 解析，未生成任何文件：{rejected}",
                                          "catalog_rejected")
                    raise ConfigError(f"Codex 拒绝这份模型目录，未生成任何文件：{rejected}", "catalog_rejected")
                catalog_changes = [
                    f"写入模型目录：{catalog_write['path']}",
                    f"合并现有目录 {catalog_write['source_count']} 条记录，新增 {len(catalog_write['added'])} 个模型条目",
                    "设置 model_catalog_json 指向新目录文件；原目录文件已备份。",
                ]
                if source is None:
                    catalog_changes.append(
                        "本机原先没有模型目录；底稿取自本机缓存的模型列表 models_cache.json。"
                        if seed["origin"] == "cache" else
                        "本机原先没有模型目录；底稿取自 Codex 内置模型目录 codex debug models --bundled。")
                    warnings.append("目录写入后，桌面端选择器只读取这个文件；Codex 之后的远程目录更新不会自动生效。重新运行本工具会用当时的内置列表重建底稿。")
                else:
                    warnings.append("目录写入后，桌面端选择器改用这个文件；Codex 之后的远程目录更新不会再生效。把 model_catalog_json 改回原路径即可恢复。")
                warnings.append("目录条目只决定桌面端的显示名称与推理选项；实际请求仍使用你填写的模型 ID 和 API 地址。")
                if catalog_write["skipped"]:
                    warnings.append(f"这些模型 ID 已在目录中，保留原有条目：{'、'.join(catalog_write['skipped'])}")
                seed_models = json.loads(catalog_write["rendered"].decode("utf-8")).get("models", [])
                catalog_write["cache"] = cache_plan(self.home, catalog_write["entries"], seed_models)
                cache = catalog_write["cache"]
                catalog_changes.append(
                    f"同步桌面选择器缓存：{cache['path']}，新增 {len(cache['added'])} 个模型")
                warnings.append(
                    "登录后的桌面选择器渲染的是 models_cache.json，不只是 models.json。已刷新缓存时间，避免立刻被远程推荐集覆盖。请完全退出 Codex（⌘Q）后再看下拉；若之后远程目录刷新盖掉缓存，重新应用一次即可。")
            elif missing:
                warnings.append(
                    f"这些模型不在现有本地目录中：{'、'.join(missing)}"
                    f"。桌面端选择器只渲染目录条目，未列入时选择器会退回默认推荐模型集；勾选写入模型目录可让它们出现在列表中。")
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
            provider.add("wire_api", WIRE_API)
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
            if f["auth_mode"] == "keychain":
                changes.append("桌面端使用当前提供方的 Key；auth.json 里的官方登录保持不动。")
                warnings.append("Key 会写入当前提供方的 experimental_bearer_token，供桌面端读取。不要在下拉里改回官方模型，否则请求仍会发到 OpenAI。")
            snippet = tomlkit.document()
            changes = [f"{'更新' if existing else '添加'}供应商：{provider_id}",
                       "合并写入新的模型目录；原始目录文件保留在备份中" if f["write_catalog"]
                       else "保留现有模型目录和其他供应商"]
            changes.extend(catalog_changes)
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
            if f["write_catalog"]:
                for target in (doc, snippet):
                    target["model_catalog_json"] = catalog_write["path"]
                if "desktop" not in doc:
                    doc["desktop"] = tomlkit.table()
                doc["desktop"]["enabled-reasoning-efforts"] = list(REASONING_EFFORTS)
                if "desktop" not in snippet:
                    snippet["desktop"] = tomlkit.table()
                snippet["desktop"]["enabled-reasoning-efforts"] = list(REASONING_EFFORTS)
                changes.append("在 [desktop] 中启用全部推理强度选项，避免所选强度被界面隐藏。")
            if f["force_api_login"]:
                for target in (doc, snippet):
                    target["preferred_auth_method"] = "apikey"
                    target["forced_login_method"] = "api"
                changes.append("写入 preferred_auth_method 与 forced_login_method，启动后直接使用 API Key 登录。")
                warnings.append("改为 API Key 登录后，此前用 ChatGPT 账号登录的会话历史会归到另一种登录方式下而暂时看不到；改回即可恢复，不会被删除。")
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
                if f["write_catalog"]:
                    d.pop("model_catalog_json", None)
                    desktop = d.get("desktop")
                    if isinstance(desktop, dict):
                        desktop.pop("enabled-reasoning-efforts", None)
                        if not desktop:
                            d.pop("desktop", None)
                if f["force_api_login"]:
                    for key in ("preferred_auth_method", "forced_login_method"):
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
            if catalog_write:
                public["catalog"] = {
                    "path": catalog_write["path"], "source_path": catalog_write["source_path"],
                    "added": catalog_write["added"], "skipped": catalog_write["skipped"],
                    "count": catalog_write["count"], "source_count": catalog_write["source_count"],
                    "preview": catalog_preview_text(catalog_write["entries"]),
                    "cache_path": catalog_write["cache"]["path"],
                    "cache_added": catalog_write["cache"]["added"],
                }
            self.plans = {k: v for k, v in self.plans.items() if time.monotonic() - v["created"] < 900}
            if len(self.plans) >= 100:
                self.plans.pop(next(iter(self.plans)))
            self.plans[plan_id] = {"form": f, "raw": raw, "revision": revision, "rendered": rendered,
                                   "account": account, "backup_id": backup_id, "created": time.monotonic(),
                                   "catalog": catalog_write}
            return public

    def prune_backups(self, protected=()):
        """Delete backups past the retention limit. The caller holds the write lock.

        Keeps the newest MAX_BACKUPS and always the oldest one, so a long run of
        applies still leaves a way back to the configuration as it was before this
        tool was ever applied. Anything else that looks like a backup is removed.

        Only directories directly inside the backup root whose name matches the
        backup id exactly are ever touched; a symlink is skipped rather than
        followed, so this can never delete outside the backup directory.
        """
        protected = {name for name in protected if isinstance(name, str)}
        if self.backup_root.is_symlink() or not self.backup_root.is_dir():
            return []
        root = self.backup_root.resolve()
        candidates = []
        for p in self.backup_root.iterdir():
            if p.is_symlink() or not BACKUP_ID.fullmatch(p.name) or not p.is_dir():
                continue
            candidates.append(p)
        # Names begin with a UTC timestamp, so string order is chronological order.
        candidates.sort(key=lambda p: p.name)
        keep = {p.name for p in candidates[-MAX_BACKUPS:]} | protected
        # The oldest is pinned because it is the only copy of the configuration as
        # it was before this tool was ever applied. It is taken from the unprotected
        # backups so that a clock jumping backwards cannot promote a freshly taken
        # backup into that role and evict the genuine original.
        for p in candidates:
            if p.name not in protected:
                keep.add(p.name)
                break
        removed = []
        for p in candidates:
            if p.name in keep:
                continue
            # Defense in depth: refuse anything not directly inside the backup root.
            if p.parent.resolve() != root:
                continue
            try:
                shutil.rmtree(p)
            except OSError:
                # A backup that cannot be removed is left in place; retention is
                # housekeeping, and it must never fail the apply that triggered it.
                continue
            removed.append(p.name)
        return removed

    def _prune_locked(self, protected=()):
        """Prune while holding the cross-process lock that apply() also takes.

        restore() does not otherwise hold it, and pruning must not race with an
        apply that is creating a backup of its own.
        """
        import fcntl
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.backup_root / ".write.lock", flags, 0o600), "wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self.prune_backups(protected=protected)

    def restore(self, payload):
        """Restore one backup produced by apply(), after an explicit confirmation.

        The current configuration is copied into a fresh backup first, so a
        restore is itself reversible. Only files this tool wrote are touched.
        """
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise ConfigError("请先确认，才会还原备份。", "confirmation_required")
        backup_id = text(payload, "backup_id", 64)
        if not BACKUP_ID.fullmatch(backup_id):
            raise ConfigError("备份标识无效。", "invalid_backup")
        with self.lock:
            if self.backup_root.is_symlink() or not self.backup_root.is_dir():
                raise ConfigError("备份目录不存在或是符号链接，已停止还原。", "invalid_backup")
            backup = self.backup_root / backup_id
            if not backup.is_dir() or backup.is_symlink():
                raise ConfigError("备份不存在。", "invalid_backup")
            try:
                manifest = json.loads((backup / "manifest.json").read_text())
                stored = (backup / "config.toml").read_bytes()
            except (OSError, ValueError):
                raise ConfigError("备份清单或配置文件无法读取，已停止还原。", "invalid_backup") from None
            if not manifest.get("applied") or not isinstance(manifest.get("before_sha256"), str):
                raise ConfigError("这个备份不完整或不是已应用的备份，已停止还原。", "invalid_backup")
            if digest(stored) != manifest["before_sha256"]:
                raise ConfigError("备份内容与清单记录不一致，已停止还原。", "invalid_backup")
            if manifest.get("existed") is False:
                raise ConfigError("这个备份记录的是“原本没有配置文件”。为避免删除文件，本工具不自动删除；请手动处理。", "invalid_backup")
            raw, revision, _, _ = self.snapshot()
            safety_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
            safety = self.backup_root / safety_id
            safety.mkdir(mode=0o700)
            if revision != "missing":
                shutil.copy2(self.path, safety / "config.toml", follow_symlinks=False)
                os.chmod(safety / "config.toml", 0o600)
                if digest((safety / "config.toml").read_bytes()) != revision:
                    raise ConfigError("还原前的安全副本校验失败，未修改任何文件。", "backup_failed", 500)
            atomic_write(safety / "manifest.json", json.dumps({
                "created": datetime.now(timezone.utc).isoformat(), "config_path": str(self.path),
                "existed": revision != "missing", "before_sha256": revision,
                "after_sha256": manifest["before_sha256"], "applied": False,
                "note": "Copy taken immediately before a restore; restores the state that was rolled back.",
            }, ensure_ascii=False, indent=2).encode())
            catalog_manifest = manifest.get("catalog")
            catalog_restored = False
            if isinstance(catalog_manifest, dict) and catalog_manifest.get("backups"):
                entries = catalog_manifest["backups"]
                # Older backups stored bare filenames and restored the first one
                # onto the new catalog path. Keep that readable, but never follow
                # a path outside this Codex home.
                if entries and isinstance(entries[0], str):
                    entries = [{"name": entries[0], "path": catalog_manifest.get("path", "")}]
                home = self.home.resolve()
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    raw_target = item.get("path", "")
                    if not isinstance(name, str) or not isinstance(raw_target, str) or not raw_target:
                        continue
                    target = Path(raw_target)
                    if target.is_symlink() or target.parent.is_symlink():
                        continue
                    try:
                        resolved = target.resolve() if target.exists() else target.parent.resolve() / target.name
                    except OSError:
                        continue
                    if resolved != home and home not in resolved.parents:
                        continue
                    saved = backup / name
                    if saved.is_file() and not saved.is_symlink() and saved.parent == backup:
                        atomic_write(target, saved.read_bytes())
                        catalog_restored = True
                created = Path(catalog_manifest.get("path", ""))
                if catalog_manifest.get("before_sha256") is None and created.is_file() and not created.is_symlink():
                    try:
                        resolved = created.resolve()
                    except OSError:
                        resolved = None
                    if resolved is not None and (resolved == home or home in resolved.parents):
                        created.unlink()
                        catalog_restored = True
            cache_manifest = catalog_manifest.get("cache") if isinstance(catalog_manifest, dict) else None
            if isinstance(cache_manifest, dict):
                target = Path(cache_manifest.get("path", ""))
                expected = self.home / "models_cache.json"
                if target == expected and not target.is_symlink():
                    backup_name = cache_manifest.get("backup")
                    if cache_manifest.get("existed") and isinstance(backup_name, str):
                        saved = backup / backup_name
                        if saved.is_file() and not saved.is_symlink() and saved.parent == backup:
                            atomic_write(target, saved.read_bytes())
                    elif not cache_manifest.get("existed") and target.is_file() and not target.is_symlink():
                        target.unlink()
            atomic_write(self.path, stored)
            if self.path.read_bytes() != stored:
                raise ConfigError("还原后文件再次发生变化，请检查备份目录，不要重复还原。", "conflict", 409)
            # The safety copy holds the configuration that is no longer live, so it
            # must survive; the restored backup does not need pinning because its
            # content is the live configuration now.
            pruned = self._prune_locked(protected={safety_id})
            return {
                "ok": True, "backup_id": backup_id, "safety_backup": safety_id,
                "config_path": str(self.path), "catalog_restored": catalog_restored,
                "pruned_backups": pruned,
                "message": "已还原该备份。还原前的配置已另存为新备份，可再次还原；请完全退出应用（⌘Q）后重新打开。",
            }

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
                catalog = plan.get("catalog")
                catalog_backups = []
                catalog_manifest = None
                if catalog:
                    seen = set()
                    for name, source in (("catalog-source.json", catalog["source_path"]),
                                         ("catalog-target.json", catalog["path"])):
                        p = Path(source).expanduser() if source else None
                        if not p or not p.is_file() or p.is_symlink():
                            continue
                        resolved = str(p.resolve())
                        if resolved in seen:
                            continue
                        seen.add(resolved)
                        shutil.copy2(p, backup / name, follow_symlinks=False)
                        os.chmod(backup / name, 0o600)
                        catalog_backups.append({"name": name, "path": str(p)})
                    cache = catalog.get("cache") or {}
                    cache_backup = None
                    cache_path = Path(cache.get("path", ""))
                    if cache.get("existed") and cache_path.is_file() and not cache_path.is_symlink():
                        shutil.copy2(cache_path, backup / "models-cache.json", follow_symlinks=False)
                        os.chmod(backup / "models-cache.json", 0o600)
                        cache_backup = "models-cache.json"
                    catalog_manifest = {
                        "path": catalog["path"], "source_path": catalog["source_path"],
                        "added": catalog["added"], "skipped": catalog["skipped"],
                        "backups": catalog_backups,
                        "before_sha256": digest(Path(catalog["path"]).read_bytes()) if Path(catalog["path"]).is_file() else None,
                        "after_sha256": digest(catalog["rendered"]),
                        "cache": {
                            "path": cache.get("path", ""),
                            "backup": cache_backup,
                            "existed": bool(cache.get("existed")),
                            "before_sha256": digest(cache_path.read_bytes()) if cache_backup else None,
                            "after_sha256": digest(cache["rendered"]) if cache.get("rendered") else None,
                        },
                    }
                manifest = {
                    "created": datetime.now(timezone.utc).isoformat(), "config_path": str(self.path),
                    "existed": plan["revision"] != "missing", "before_sha256": plan["revision"],
                    "after_sha256": digest(plan["rendered"]), "provider_id": f["provider_id"],
                    "models": f["models"], "default_model": f["default_model"],
                    "profiles": [{"model": m, "profile": n} for m, n in profile_names(f["provider_id"], f["models"])] if f["save_profiles"] else [],
                    "catalog": catalog_manifest,
                    "credential_recovery": "Existing Keychain items were not overwritten or deleted.",
                    "applied": False,
                }
                atomic_write(backup / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
                try:
                    if secret:
                        self.keychain.add(plan["account"], secret)
                    if self.snapshot()[1] != plan["revision"]:
                        raise ConfigError("保存期间配置发生变化，已停止写入；新钥匙串条目可能已建立但未启用。", "conflict", 409)
                    if catalog:
                        # Written first: if the config write then fails, config.toml
                        # still points at the previous catalog, so nothing changes.
                        atomic_write(Path(catalog["path"]), catalog["rendered"])
                        cache = catalog.get("cache") or {}
                        cache_path = Path(cache.get("path", ""))
                        if cache.get("rendered") and not cache_path.is_symlink():
                            atomic_write(cache_path, cache["rendered"])
                    rendered = plan["rendered"]
                    if secret and f["auth_mode"] == "keychain":
                        written = tomlkit.parse(rendered.decode("utf-8"))
                        written["model_providers"][f["provider_id"]]["experimental_bearer_token"] = secret
                        rendered = tomlkit.dumps(written).encode("utf-8")
                    atomic_write(self.path, rendered)
                    plan["rendered"] = rendered
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
                # Inside the write lock, so the set that is pruned cannot change
                # under us. The backup just taken is protected explicitly: if the
                # clock moved backwards its name could sort older than the limit.
                pruned = self.prune_backups(protected={plan["backup_id"]})
                self.remember_models(f["provider_id"], f["models"], f["base_url"])
                return {
                    "ok": True, "backup_path": str(backup), "config_path": str(self.path),
                    "provider_id": f["provider_id"], "model": f["model"], "models": f["models"],
                    "default_model": f["default_model"], "set_default": f["set_default"],
                    "save_profiles": f["save_profiles"],
                    "profiles": [{"model": m, "profile": n, "default": m == f["default_model"]}
                                 for m, n in profile_names(f["provider_id"], f["models"])] if f["save_profiles"] else [],
                    "keychain_saved": bool(secret), "connection_tested": False, "restart_required": True,
                    "pruned_backups": pruned,
                    "catalog": {"path": catalog["path"], "added": catalog["added"],
                                "skipped": catalog["skipped"], "count": catalog["count"]} if catalog else None,
                    "message": "配置已备份并写入。请保存工作，完全退出应用（⌘Q）后重新打开，再用新会话验证模型。",
                }


    def managed_path(self):
        return self.home / "weswitch-managed.json"

    def managed_models(self):
        path = self.managed_path()
        if not path.is_file() or path.is_symlink():
            return []
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, dict):
            return []
        return [{"slug": slug, "provider_id": item.get("provider_id", "")}
                for slug, item in models.items()
                if isinstance(slug, str) and isinstance(item, dict) and not official_slug(slug)]

    def remember_models(self, provider_id, models, base_url):
        path = self.managed_path()
        current = {}
        if path.is_file() and not path.is_symlink():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded.get("models"), dict):
                    current = loaded["models"]
            except (OSError, ValueError):
                current = {}
        for model in models:
            if official_slug(model):
                continue
            current[model] = {"provider_id": provider_id, "base_url": base_url}
        atomic_write(path, json.dumps({"models": current}, ensure_ascii=False, indent=2).encode())

    def remove_models(self, payload):
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            raise ConfigError("请先确认删除。", "confirmation_required")
        requested = payload.get("models")
        if not isinstance(requested, list) or not requested or not all(isinstance(item, str) for item in requested):
            raise ConfigError("请选择要删除的自定义模型。")
        managed = {item["slug"] for item in self.managed_models()}
        blocked = [item for item in requested if official_slug(item) or item not in managed]
        if blocked:
            raise ConfigError("只能删除本工具添加的模型，默认 GPT 模型不会删除。")
        from model_catalog import load_catalog, remove_slugs, render
        removed = []
        for path in (self.home / "models.json", self.home / "models_cache.json"):
            if path.is_file() and not path.is_symlink():
                document, gone = remove_slugs(load_catalog(path), requested)
                if gone:
                    atomic_write(path, render(document))
                    removed.extend(gone)
        path = self.managed_path()
        data = json.loads(path.read_text())
        for item in requested:
            data["models"].pop(item, None)
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode())
        return {"removed": sorted(set(removed)), "message": "已从目录和选择器缓存删除所选自定义模型。默认 GPT 模型未改动。请完全退出 Codex 后再打开。"}


def official_slug(slug):
    return slug.startswith(("gpt-", "gpt", "o1", "o3", "o4", "codex-", "chatgpt-"))
