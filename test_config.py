"""Isolated intended-behavior regressions; never access the real Codex home or Keychain."""
from __future__ import annotations

import copy
import ctypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_core as core
import keychain as keychain_module
import server as server_module


SECRET = "sk-regression-only-DO-NOT-LEAK-8d97af"
TOKEN = "regression-session-token-not-a-real-credential"
CATALOG = b'{\n  "models": [{"slug": "original-model", "extra": [1, 2]}],\n  "keep": "exact spacing"\n}\n'
ORIGINAL = b'''# User configuration: preserve this exact comment.
model = "original-model" # Original default model comment.
model_provider = "keep-provider"
model_reasoning_effort = "high"
model_reasoning_summary = "detailed"
model_verbosity = "low"
model_context_window = 128000
model_auto_compact_token_limit = 96000
model_catalog_json = "model_catalog.json" # Keep this catalog reference.
approval_policy = "on-request"
sandbox_mode = "workspace-write"

# Unrelated MCP settings must survive.
[mcp_servers.docs]
command = "/tmp/regression-only/mcp"
args = ["--stdio", "--safe"] # Keep MCP arguments.
enabled = true
[mcp_servers.docs.env]
REGRESSION_SETTING = "retained"

# Security and project trust must survive.
[security]
allow_network = false
trusted_paths = ["/tmp/regression-only"]
[sandbox_workspace_write]
network_access = false
writable_roots = ["/tmp/regression-only"]
[projects."/tmp/regression-only"]
trust_level = "trusted"

# Profile defaults are independent of root defaults.
[profiles.review]
model = "profile-model"
model_provider = "keep-provider"
approval_policy = "never"
[features]
experimental_feature = false

# This other provider is not managed by the UI.
[model_providers.keep-provider]
name = "Keep Provider"
base_url = "https://keep.example.invalid/v1"
wire_api = "responses"
env_key = "KEEP_PROVIDER_KEY"
request_max_retries = 7 # Retain this retry comment exactly.
'''
MODEL_SETTINGS = {
    "model", "model_provider", "model_reasoning_effort", "model_reasoning_summary",
    "model_verbosity", "model_context_window", "model_auto_compact_token_limit",
}


class FakeKeychain:
    available = True

    def __init__(self):
        self.items = {}
        self.add_calls = []
        self.exists_calls = []

    def exists(self, account):
        self.exists_calls.append(account)
        return account in self.items

    def add(self, account, secret):
        self.add_calls.append((account, secret))
        if account in self.items:
            raise keychain_module.KeychainError("Injected duplicate Keychain item")
        self.items[account] = secret


class ConfigFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-config-regression-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "codex-home"
        self.home.mkdir(mode=0o700)
        self.path = self.home / "config.toml"
        self.path.write_bytes(ORIGINAL)
        self.catalog = self.home / "model_catalog.json"
        self.catalog.write_bytes(CATALOG)
        self.fake = FakeKeychain()
        native = mock.patch.object(
            keychain_module.C, "CDLL",
            side_effect=AssertionError("Tests must never load native Keychain frameworks"),
        )
        native.start()
        self.addCleanup(native.stop)
        constructor = mock.patch.object(
            core, "Keychain", side_effect=AssertionError("ConfigStore must use FakeKeychain")
        )
        constructor.start()
        self.addCleanup(constructor.stop)
        app = mock.patch.object(core.ConfigStore, "app_info", return_value={
            "path": None, "version": None, "cli_version": None,
        })
        app.start()
        self.addCleanup(app.stop)
        self.store = core.ConfigStore(self.home, keychain=self.fake)

    def tearDown(self):
        self.assertEqual(self.catalog.read_bytes(), CATALOG, "The model catalog bytes changed")

    def form(self, **changes):
        result = {
            "provider_id": "regression-provider", "name": "Regression Provider",
            "base_url": "https://gateway.example.invalid/v1", "model": "new-model",
            "auth_mode": "env", "env_key": "REGRESSION_API_KEY",
            "reasoning_effort": "medium", "set_default": True, "has_key": False,
        }
        result.update(changes)
        return result

    def apply_payload(self, form, preview, **changes):
        result = dict(form, confirmed=True, plan_id=preview["plan_id"], revision=preview["revision"])
        result.update(changes)
        return result

    def commit(self, form=None, api_key=""):
        form = self.form() if form is None else form
        preview = self.store.preview(form)
        result = self.store.apply(self.apply_payload(form, preview, api_key=api_key))
        return preview, result

    def tree_snapshot(self):
        result = {}
        for path in sorted(self.root.rglob("*")):
            info = path.lstat()
            if path.is_symlink():
                value = ("symlink", str(path.readlink()))
            elif path.is_file():
                value = ("file", path.read_bytes())
            else:
                value = ("directory",)
            result[str(path.relative_to(self.root))] = (
                value, stat.S_IMODE(info.st_mode), info.st_mtime_ns,
            )
        return result

    def assert_error(self, callback, code="invalid", status=400):
        with self.assertRaises(core.ConfigError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status, status)
        self.assertNotIn(SECRET, str(caught.exception))
        return caught.exception

    def assert_no_secret(self, *values):
        for value in values:
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            elif not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            self.assertNotIn(SECRET, value)

    def assert_backup(self, result, original=ORIGINAL):
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_relative_to(self.home))
        self.assertEqual((backup / "config.toml").read_bytes(), original)
        manifest = json.loads((backup / "manifest.json").read_bytes())
        self.assertTrue(manifest["existed"])
        self.assertTrue(manifest["applied"])
        self.assertEqual(manifest["before_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(manifest["after_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        for path in (self.home, self.store.backup_root, backup):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700, str(path))
        for path in (self.path, backup / "config.toml", backup / "manifest.json",
                     self.store.backup_root / ".write.lock"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))
        self.assert_no_secret(manifest)
        return manifest


class ConfigStoreTests(ConfigFixture):
    def test_preview_is_filesystem_read_only_and_does_not_save_key(self):
        before = self.tree_snapshot()
        form = self.form(auth_mode="keychain", has_key=True)
        with mock.patch.object(core, "atomic_write", side_effect=AssertionError("Preview wrote a file")), \
                mock.patch.object(self.fake, "add", side_effect=AssertionError("Preview saved a key")):
            preview = self.store.preview(form)
            state = self.store.state()
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(preview["revision"], hashlib.sha256(ORIGINAL).hexdigest())
        self.assertIn("regression-provider", preview["snippet"])
        self.assertEqual(self.fake.items, {})
        self.assertEqual(self.fake.exists_calls, [])
        self.assertFalse(self.store.backup_root.exists())
        self.assert_no_secret(preview, state)

    def test_preview_does_not_mutate_the_input_form(self):
        form = self.form()
        before = copy.deepcopy(form)
        self.store.preview(form)
        self.assertEqual(form, before)

    def test_apply_preserves_exact_comments_and_unrelated_toml_semantics(self):
        preview, result = self.commit()
        rendered = self.path.read_text()
        for line in ORIGINAL.decode().splitlines():
            if "#" in line:
                comment = line[line.index("#"):]
                self.assertEqual(rendered.count(comment), 1, comment)
        before = tomllib.loads(ORIGINAL.decode())
        after = tomllib.loads(rendered)
        expected = copy.deepcopy(before)
        for setting in MODEL_SETTINGS:
            expected.pop(setting, None)
        expected.update(model="new-model", model_provider="regression-provider", model_reasoning_effort="medium")
        expected["model_providers"]["regression-provider"] = {
            "name": "Regression Provider", "base_url": "https://gateway.example.invalid/v1",
            "wire_api": "responses", "requires_openai_auth": False, "env_key": "REGRESSION_API_KEY",
        }
        expected["profiles"]["regression-provider-new-model"] = {
            "model": "new-model", "model_provider": "regression-provider",
            "model_reasoning_effort": "medium",
        }
        self.assertEqual(after, expected)
        self.assertEqual(self.catalog.read_bytes(), CATALOG)
        self.assertTrue(result["ok"])
        self.assertEqual(preview["model"], after["model"])

    def test_root_scalars_insert_before_tables_when_original_has_no_model(self):
        original = b'# Keep root comment.\nmodel_catalog_json = "model_catalog.json"\n\n[mcp_servers.keep]\ncommand = "keep"\n'
        self.path.write_bytes(original)
        preview, _ = self.commit()
        rendered = self.path.read_text()
        parsed = tomllib.loads(rendered)
        first_table = next(i for i, line in enumerate(rendered.splitlines()) if line.startswith("["))
        for name in ("model", "model_provider", "model_reasoning_effort"):
            index = next(i for i, line in enumerate(rendered.splitlines()) if line.startswith(name + " ="))
            self.assertLess(index, first_table, name)
        self.assertEqual(parsed["mcp_servers"], {"keep": {"command": "keep"}})
        self.assertEqual(parsed["model"], "new-model")
        self.assertEqual(tomllib.loads(preview["snippet"])["model"], "new-model")
        self.assertIn("# Keep root comment.", rendered)

    def test_crlf_backup_bytes_and_private_modes_with_a_new_backup_directory(self):
        original = ORIGINAL.replace(b"\n", b"\r\n")
        self.path.write_bytes(original)
        self.path.chmod(0o644)
        _, result = self.commit()
        self.assert_backup(result, original)

    def test_backup_is_byte_exact_and_all_sensitive_modes_are_private(self):
        original = ORIGINAL.replace(b"\n", b"\r\n")
        self.path.write_bytes(original)
        self.path.chmod(0o644)
        self.store.backup_root.mkdir(mode=0o755)
        self.store.backup_root.chmod(0o755)
        _, result = self.commit()
        self.assert_backup(result, original)

    def test_new_key_saves_through_mock_and_only_security_helper_enters_config(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)
        with mock.patch.object(self.fake, "add", wraps=self.fake.add) as add:
            result = self.store.apply(self.apply_payload(form, preview, api_key=SECRET))
        provider = tomllib.loads(self.path.read_text())["model_providers"][form["provider_id"]]
        account = core.managed_account(provider)
        self.assertRegex(account, r"^regression-provider\.[0-9a-f]{24}$")
        add.assert_called_once_with(account, SECRET)
        self.assertEqual(self.fake.items, {account: SECRET})
        self.assertEqual(provider["auth"]["command"], "/usr/bin/security")
        self.assertEqual(provider["auth"]["args"], [
            "find-generic-password", "-s", keychain_module.SERVICE, "-a", account, "-w",
        ])
        self.assertNotIn("env_key", provider)
        self.assertTrue(result["keychain_saved"])
        self.assertNotIn(preview["plan_id"], self.store.plans)
        manifest = self.assert_backup(result)
        self.assert_no_secret(preview, self.store.state(), result, manifest, self.path.read_bytes())
        for path in self.home.rglob("*"):
            if path.is_file():
                self.assert_no_secret(path.read_bytes())

    def test_existing_plaintext_credentials_do_not_leak_in_state_or_public_preview(self):
        extra = ('\n[model_providers.legacy]\nname = "Legacy"\n'
                 'base_url = "https://user:' + SECRET + '@legacy.example.invalid/v1"\n'
                 'experimental_bearer_token = "' + SECRET + '"\n')
        original = ORIGINAL + extra.encode()
        self.path.write_bytes(original)
        state = self.store.state()
        preview, result = self.commit()
        legacy = next(item for item in state["providers"] if item["id"] == "legacy")
        self.assertEqual(legacy["base_url"], "")
        manifest = json.loads((Path(result["backup_path"]) / "manifest.json").read_bytes())
        self.assert_no_secret(state, preview, manifest, result, self.store.state())
        self.assertEqual((Path(result["backup_path"]) / "config.toml").read_bytes(), original)

    def test_blank_key_apply_reuses_a_seeded_account_without_saving_again(self):
        account = "regression-provider." + "a" * 24
        self.fake.items[account] = SECRET
        existing = ('\n[model_providers.regression-provider]\n'
                    'name = "Existing provider"\n'
                    'base_url = "https://gateway.example.invalid/v1"\n'
                    'wire_api = "responses"\n'
                    '[model_providers.regression-provider.auth]\n'
                    'command = "/usr/bin/security"\n'
                    'args = ["find-generic-password", "-s", '
                    + json.dumps(keychain_module.SERVICE) + ', "-a", '
                    + json.dumps(account) + ', "-w"]\n')
        original = ORIGINAL + existing.encode()
        self.path.write_bytes(original)
        form = self.form(auth_mode="keychain", has_key=False, model="another-model")
        preview, result = self.commit(form)
        provider = tomllib.loads(self.path.read_text())["model_providers"]["regression-provider"]
        self.assertEqual(core.managed_account(provider), account)
        self.assertEqual(self.fake.items, {account: SECRET})
        self.assertEqual(self.fake.add_calls, [])
        self.assertEqual(self.fake.exists_calls, [account])
        self.assertFalse(result["keychain_saved"])
        self.assertEqual(tomllib.loads(self.path.read_text())["model"], "another-model")
        self.assert_backup(result, original)
        self.assert_no_secret(preview, self.store.state(), self.path.read_bytes())

    def test_blank_key_reuses_managed_account_only_for_the_same_endpoint(self):
        self.commit(self.form(auth_mode="keychain", has_key=True), SECRET)
        saved = dict(self.fake.items)
        form = self.form(auth_mode="keychain", has_key=False, model="another-model")
        preview, result = self.commit(form)
        provider = tomllib.loads(self.path.read_text())["model_providers"][form["provider_id"]]
        self.assertEqual(core.managed_account(provider), next(iter(saved)))
        self.assertEqual(self.fake.items, saved)
        self.assertEqual(len(self.fake.add_calls), 1)
        self.assertEqual(self.fake.exists_calls, [next(iter(saved))])
        self.assertFalse(result["keychain_saved"])
        self.assert_no_secret(preview, self.store.state())

    def test_blank_key_rejects_a_changed_endpoint_without_side_effects(self):
        self.commit(self.form(auth_mode="keychain", has_key=True), SECRET)
        before, saved = self.tree_snapshot(), dict(self.fake.items)
        self.assert_error(lambda: self.store.preview(self.form(
            auth_mode="keychain", base_url="https://different.example.invalid/v1",
        )))
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.fake.items, saved)

    def test_blank_key_rejects_missing_managed_key(self):
        self.commit(self.form(auth_mode="keychain", has_key=True), SECRET)
        self.fake.items.clear()
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.preview(self.form(auth_mode="keychain")))
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(len(self.fake.exists_calls), 1)

    def test_new_key_rotation_keeps_the_previous_keychain_item(self):
        self.commit(self.form(auth_mode="keychain", has_key=True), SECRET)
        saved = dict(self.fake.items)
        self.commit(self.form(auth_mode="keychain", has_key=True), "replacement-regression-secret")
        self.assertEqual(len(self.fake.items), 2)
        for account, value in saved.items():
            self.assertEqual(self.fake.items[account], value)
        provider = tomllib.loads(self.path.read_text())["model_providers"]["regression-provider"]
        self.assertNotIn(core.managed_account(provider), saved)

    def test_failed_keychain_add_prevents_any_config_write(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)
        with mock.patch.object(self.fake, "add", side_effect=keychain_module.KeychainError("Injected save failure")) as add, \
                mock.patch.object(core, "atomic_write", wraps=core.atomic_write) as write:
            self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, api_key=SECRET)), "keychain_failed")
        add.assert_called_once()
        self.assertFalse(any(call.args[0] == self.path for call in write.call_args_list))
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertEqual(self.fake.items, {})
        backup = Path(preview["files"][1]).parent
        self.assertEqual((backup / "config.toml").read_bytes(), ORIGINAL)
        self.assertFalse(json.loads((backup / "manifest.json").read_bytes())["applied"])

    def test_failed_atomic_config_replace_retains_original_and_removes_temp_file(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)
        real_replace = core.os.replace

        def injected_replace(source, destination):
            if Path(destination) == self.path:
                raise OSError("Injected config replace failure")
            return real_replace(source, destination)

        with mock.patch.object(core.os, "replace", side_effect=injected_replace) as replace:
            self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, api_key=SECRET)), "write_failed", 500)
        self.assertTrue(any(Path(call.args[1]) == self.path for call in replace.call_args_list))
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertEqual(len(self.fake.items), 1)
        self.assertEqual(list(self.home.rglob(".codex-ui-*")), [])
        backup = Path(preview["files"][1]).parent
        self.assertEqual((backup / "config.toml").read_bytes(), ORIGINAL)
        self.assertFalse(json.loads((backup / "manifest.json").read_bytes())["applied"])

    def test_corrupted_backup_is_detected_before_keychain_or_config_write(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)

        def corrupt_copy(source, destination, **kwargs):
            Path(destination).write_bytes(b"corrupted injected backup")

        with mock.patch.object(core.shutil, "copy2", side_effect=corrupt_copy) as copy_file:
            self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, api_key=SECRET)), "backup_failed", 500)
        copy_file.assert_called_once()
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertEqual(self.fake.add_calls, [])

    def test_final_manifest_failure_does_not_misreport_a_committed_apply(self):
        form = self.form()
        preview = self.store.preview(form)
        real_write = core.atomic_write
        manifest_writes = []

        def fail_final_manifest(path, data, mode=0o600):
            if path.name == "manifest.json":
                manifest_writes.append(json.loads(data))
                if manifest_writes[-1]["applied"]:
                    raise OSError("Injected final manifest failure")
            return real_write(path, data, mode)

        with mock.patch.object(core, "atomic_write", side_effect=fail_final_manifest):
            result = self.store.apply(self.apply_payload(form, preview))
        self.assertEqual([m["applied"] for m in manifest_writes], [False, True])
        self.assertTrue(result["ok"])
        self.assertEqual(tomllib.loads(self.path.read_text())["model"], "new-model")
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)), "stale_preview", 409)

    def test_stale_form_fields_are_rejected_without_writes(self):
        replacements = {
            "provider_id": "different-provider", "name": "Changed name",
            "base_url": "https://different.example.invalid/v1", "model": "different-model",
            "auth_mode": "keychain", "env_key": "DIFFERENT_API_KEY",
            "reasoning_effort": "low", "set_default": False,
        }
        for field, value in replacements.items():
            with self.subTest(field=field):
                form = self.form()
                preview = self.store.preview(form)
                before = self.tree_snapshot()
                self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, **{field: value})), "stale_preview", 409)
                self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.fake.add_calls, [])

    def test_stale_has_key_state_is_rejected(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)
        before = self.tree_snapshot()
        for changes in ({"api_key": ""}, {"has_key": False, "api_key": SECRET}, {"has_key": False, "api_key": ""}):
            with self.subTest(changes=tuple(changes)):
                self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, **changes)), "stale_preview", 409)
                self.assertEqual(self.tree_snapshot(), before)

    def test_wrong_or_missing_revision_is_rejected(self):
        form = self.form()
        preview = self.store.preview(form)
        before = self.tree_snapshot()
        for revision in ("wrong-revision", None, "missing"):
            with self.subTest(revision=revision):
                self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, revision=revision)), "stale_preview", 409)
                self.assertEqual(self.tree_snapshot(), before)

    def test_expired_preview_rejects_at_the_exact_expiration_boundary(self):
        form = self.form()
        with mock.patch.object(core.time, "monotonic", return_value=1000.0):
            preview = self.store.preview(form)
        before = self.tree_snapshot()
        with mock.patch.object(core.time, "monotonic", return_value=1900.0):
            self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)), "stale_preview", 409)
        self.assertEqual(self.tree_snapshot(), before)

    def test_unknown_plan_is_rejected(self):
        form = self.form()
        preview = self.store.preview(form)
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, plan_id="unknown-plan")), "stale_preview", 409)
        self.assertEqual(self.tree_snapshot(), before)

    def test_replayed_preview_is_rejected_after_success(self):
        form = self.form()
        preview, _ = self.commit(form)
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)), "stale_preview", 409)
        self.assertEqual(self.tree_snapshot(), before)

    def test_apply_requires_explicit_boolean_confirmation(self):
        form = self.form()
        preview = self.store.preview(form)
        before = self.tree_snapshot()
        for confirmed in (False, None, 1, "true"):
            with self.subTest(confirmed=confirmed):
                self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, confirmed=confirmed)), "confirmation_required")
                self.assertEqual(self.tree_snapshot(), before)

    def test_external_file_edit_conflicts_without_overwriting_it(self):
        form = self.form()
        preview = self.store.preview(form)
        external = ORIGINAL + b"\n# Independent external change.\n"
        self.path.write_bytes(external)
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)), "conflict", 409)
        self.assertEqual(self.path.read_bytes(), external)
        self.assertEqual(self.tree_snapshot(), before)

    def test_independent_configstore_commit_conflicts_with_earlier_preview(self):
        form = self.form()
        preview = self.store.preview(form)
        other = core.ConfigStore(self.home, keychain=FakeKeychain())
        other_form = self.form(provider_id="second-provider", model="second-model")
        other_preview = other.preview(other_form)
        other.apply(self.apply_payload(other_form, other_preview))
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)), "conflict", 409)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(tomllib.loads(self.path.read_text())["model"], "second-model")

    def test_external_edit_during_keychain_add_is_detected_before_commit(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)
        external = ORIGINAL + b"\n# Concurrent editor changed the config.\n"
        real_add = self.fake.add

        def save_and_edit(account, secret):
            real_add(account, secret)
            self.path.write_bytes(external)

        with mock.patch.object(self.fake, "add", side_effect=save_and_edit) as add:
            self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, api_key=SECRET)), "conflict", 409)
        add.assert_called_once()
        self.assertEqual(self.path.read_bytes(), external)
        self.assertEqual(len(self.fake.items), 1)
        self.assertEqual(Path(preview["files"][1]).read_bytes(), ORIGINAL)

    def test_no_default_preserves_every_root_model_setting_and_profile(self):
        preview, result = self.commit(self.form(set_default=False, reasoning_effort="xhigh"))
        before, after = tomllib.loads(ORIGINAL.decode()), tomllib.loads(self.path.read_text())
        for field in MODEL_SETTINGS:
            self.assertEqual(after[field], before[field], field)
        # Existing profiles are preserved even when no default model is switched.
        self.assertEqual(after["profiles"]["review"], before["profiles"]["review"])
        managed = after["profiles"]["regression-provider-new-model"]
        self.assertEqual(managed["model"], "new-model")
        self.assertEqual(managed["model_provider"], "regression-provider")
        self.assertEqual(managed["model_reasoning_effort"], "xhigh")
        after["profiles"].pop("regression-provider-new-model")
        after["model_providers"].pop("regression-provider")
        self.assertEqual(after, before)
        self.assertNotIn("model", tomllib.loads(preview["snippet"]))
        self.assertFalse(result["set_default"])

    def test_no_default_does_not_create_a_missing_root_model(self):
        self.path.write_bytes(b'[mcp_servers.keep]\ncommand = "keep"\n')
        self.commit(self.form(set_default=False))
        parsed = tomllib.loads(self.path.read_text())
        for field in MODEL_SETTINGS:
            self.assertNotIn(field, parsed)
        self.assertEqual(parsed["mcp_servers"], {"keep": {"command": "keep"}})

    def test_blank_effort_removes_stale_model_specific_options_only(self):
        self.commit(self.form(reasoning_effort=""))
        parsed = tomllib.loads(self.path.read_text())
        for setting in MODEL_SETTINGS - {"model", "model_provider"}:
            self.assertNotIn(setting, parsed)
        self.assertEqual(parsed["profiles"]["review"], tomllib.loads(ORIGINAL.decode())["profiles"]["review"])
        self.assertNotIn("model_reasoning_effort", parsed["profiles"]["regression-provider-new-model"])
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")

    def test_existing_provider_retry_settings_survive_an_update(self):
        self.path.write_bytes(ORIGINAL + b'''\n[model_providers.regression-provider]
name = "Old name"
base_url = "https://gateway.example.invalid/v1"
wire_api = "responses"
env_key = "OLD_KEY"
request_max_retries = 9
stream_max_retries = 6
stream_idle_timeout_ms = 45678
''')
        self.commit()
        provider = tomllib.loads(self.path.read_text())["model_providers"]["regression-provider"]
        self.assertEqual(provider["request_max_retries"], 9)
        self.assertEqual(provider["stream_max_retries"], 6)
        self.assertEqual(provider["stream_idle_timeout_ms"], 45678)
        self.assertEqual(provider["env_key"], "REGRESSION_API_KEY")

    def test_existing_advanced_provider_is_not_silently_overwritten(self):
        self.path.write_bytes(ORIGINAL + b'\n[model_providers.regression-provider]\nhttp_headers = { Authorization = "retained" }\n')
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.preview(self.form()))
        self.assertEqual(self.tree_snapshot(), before)

    def test_local_custom_command_auth_is_external_and_preview_cannot_overwrite_it(self):
        original = ORIGINAL + b'''\n[model_providers.regression-provider]
name = "Local custom auth"
base_url = "http://localhost:8000/v1"
wire_api = "responses"
[model_providers.regression-provider.auth]
command = "/tmp/regression-only/auth-helper"
args = ["--token"] # Preserve the external authentication command.
'''
        self.path.write_bytes(original)
        before = self.tree_snapshot()
        state = self.store.state()
        provider = next(item for item in state["providers"] if item["id"] == "regression-provider")
        self.assertEqual(provider["auth_mode"], "external")
        self.assertFalse(provider["managed_credential"])
        self.assertEqual(provider["base_url"], "http://localhost:8000/v1")
        for mode in ("none", "env", "keychain"):
            with self.subTest(auth_mode=mode):
                form = self.form(auth_mode=mode, base_url=provider["base_url"], has_key=mode == "keychain")
                self.assert_error(lambda: self.store.preview(form))
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})
        self.assertEqual(self.fake.exists_calls, [])
        self.assertEqual(self.fake.add_calls, [])
        self.assertEqual(self.fake.items, {})

    def test_remote_openai_auth_is_external_and_preview_cannot_overwrite_it(self):
        original = ORIGINAL + b'''\n[model_providers.regression-provider]
name = "Remote OpenAI auth"
base_url = "https://gateway.example.invalid/v1"
wire_api = "responses"
requires_openai_auth = true # Preserve the existing OpenAI authentication.
'''
        self.path.write_bytes(original)
        before = self.tree_snapshot()
        state = self.store.state()
        provider = next(item for item in state["providers"] if item["id"] == "regression-provider")
        self.assertEqual(provider["auth_mode"], "external")
        self.assertFalse(provider["managed_credential"])
        self.assertEqual(provider["base_url"], "https://gateway.example.invalid/v1")
        for mode in ("env", "keychain"):
            with self.subTest(auth_mode=mode):
                form = self.form(auth_mode=mode, has_key=mode == "keychain")
                self.assert_error(lambda: self.store.preview(form))
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})
        self.assertEqual(self.fake.exists_calls, [])
        self.assertEqual(self.fake.add_calls, [])
        self.assertEqual(self.fake.items, {})

    def test_plaintext_token_auth_is_external_without_leaking_or_overwriting_it(self):
        original = ORIGINAL + ('\n[model_providers.regression-provider]\n'
                               'name = "Plaintext token auth"\n'
                               'base_url = "https://gateway.example.invalid/v1"\n'
                               'experimental_bearer_token = "' + SECRET + '"\n').encode()
        self.path.write_bytes(original)
        before = self.tree_snapshot()
        state = self.store.state()
        provider = next(item for item in state["providers"] if item["id"] == "regression-provider")
        self.assertEqual(provider["auth_mode"], "external")
        self.assertFalse(provider["managed_credential"])
        self.assert_no_secret(state)
        self.assert_error(lambda: self.store.preview(self.form()))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})
        self.assertEqual(self.fake.exists_calls, [])
        self.assertEqual(self.fake.add_calls, [])
        self.assertEqual(self.fake.items, {})

    def test_no_auth_accepts_only_explicit_loopback_endpoints(self):
        for endpoint in ("http://localhost:8000/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1"):
            with self.subTest(endpoint=endpoint):
                preview = self.store.preview(self.form(auth_mode="none", base_url=endpoint))
                provider = tomllib.loads(preview["snippet"])["model_providers"]["regression-provider"]
                self.assertNotIn("auth", provider)
                self.assertNotIn("env_key", provider)
                self.assertFalse(provider["requires_openai_auth"])
        before = self.tree_snapshot()
        for endpoint in ("https://remote.example.invalid/v1", "https://127.0.0.1.evil.invalid/v1", "https://0.0.0.0/v1"):
            with self.subTest(endpoint=endpoint):
                self.assert_error(lambda: self.store.preview(self.form(auth_mode="none", base_url=endpoint)))
                self.assertEqual(self.tree_snapshot(), before)

    def test_invalid_urls_credentials_queries_remote_http_and_ports_reject(self):
        endpoints = (
            "https://user:password@gateway.example.invalid/v1", "https://user@gateway.example.invalid/v1",
            "https://gateway.example.invalid/v1?api_key=" + SECRET,
            "https://gateway.example.invalid/v1#fragment", "http://remote.example.invalid/v1",
            "ftp://localhost/v1", "file:///tmp/regression-only", "not-a-url", "https:///missing-host",
            "https://gateway.example.invalid:0/v1", "https://gateway.example.invalid:65536/v1",
            "https://gateway.example.invalid:notaport/v1", "https://[invalid/v1",
            "https://gateway.example.invalid/white space", "https://gateway.example.invalid/back\\slash",
            "https://gateway.example.invalid/v1/responses", "https://gateway.example.invalid/v1/chat/completions/",
            "https://gateway.example.invalid/v1/messages",
        )
        before = self.tree_snapshot()
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint.replace(SECRET, "[test-secret]")):
                self.assert_error(lambda: self.store.preview(self.form(base_url=endpoint)))
                self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})

    def test_url_with_empty_credential_marker_is_rejected(self):
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.preview(self.form(base_url="https://@gateway.example.invalid/v1")))
        self.assertEqual(self.tree_snapshot(), before)

    def test_url_path_escape_segments_are_rejected(self):
        for suffix in ("/v1/../private", "/v1/%2e%2e/private", "/v1/%2E%2E%2Fprivate"):
            with self.subTest(path=suffix):
                before = self.tree_snapshot()
                self.assert_error(lambda: self.store.preview(self.form(base_url="https://gateway.example.invalid" + suffix)))
                self.assertEqual(self.tree_snapshot(), before)

    def test_provider_ids_cannot_escape_paths_or_use_reserved_names(self):
        before = self.tree_snapshot()
        for provider in ("../escape", "a/../../escape", "a\\escape", "/tmp/escape", "Uppercase", "a.b",
                         "openai", "ollama", "lmstudio", "azure", "openai-chat-completions"):
            with self.subTest(provider=provider):
                self.assert_error(lambda: self.store.preview(self.form(provider_id=provider)))
                self.assertEqual(self.tree_snapshot(), before)

    def test_model_embedded_newlines_and_control_characters_are_rejected(self):
        before = self.tree_snapshot()
        for model in ("model\nother", "model\rother", "model\tother", "model other", "model\x00other", "model\x7fother"):
            with self.subTest(model=repr(model)):
                self.assert_error(lambda: self.store.preview(self.form(model=model)))
                self.assertEqual(self.tree_snapshot(), before)

    def test_model_leading_and_trailing_newlines_are_rejected_not_trimmed(self):
        for model in ("model\n", "\nmodel", "model\r\n"):
            with self.subTest(model=repr(model)):
                before = self.tree_snapshot()
                self.assert_error(lambda: self.store.preview(self.form(model=model)))
                self.assertEqual(self.tree_snapshot(), before)

    def test_unknown_auth_mode_and_invalid_env_names_are_rejected(self):
        for mode in ("bearer", "unknown", "KEYCHAIN", ""):
            with self.subTest(auth_mode=mode):
                self.assert_error(lambda: self.store.preview(self.form(auth_mode=mode)))
        for name in ("1KEY", "lowercase", "BAD-NAME", "KEY;COMMAND", ""):
            with self.subTest(env_key=name):
                self.assert_error(lambda: self.store.preview(self.form(env_key=name)))
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertFalse(self.store.backup_root.exists())

    def test_non_boolean_flags_and_non_object_forms_are_rejected(self):
        for changes in ({"set_default": 1}, {"set_default": "false"}, {"has_key": "true"}, {"model": 42}):
            with self.subTest(changes=changes):
                self.assert_error(lambda: self.store.preview(self.form(**changes)))
        for value in (None, [], "form", 42):
            with self.subTest(value=value):
                self.assert_error(lambda: self.store.preview(value))
        self.assertEqual(self.store.plans, {})

    def test_only_keychain_mode_can_accept_a_key(self):
        for mode, endpoint in (("env", "https://gateway.example.invalid/v1"), ("none", "http://localhost:8000/v1")):
            with self.subTest(mode=mode):
                self.assert_error(lambda: self.store.preview(self.form(auth_mode=mode, base_url=endpoint, has_key=True)))
        self.assertEqual(self.fake.add_calls, [])
        self.assertEqual(self.path.read_bytes(), ORIGINAL)

    def test_invalid_api_keys_reject_before_filesystem_or_keychain_changes(self):
        form = self.form(auth_mode="keychain", has_key=True)
        preview = self.store.preview(form)
        before = self.tree_snapshot()
        for value in (" leading", "trailing ", "two words", "new\nline", "control\x00", "x" * 8193, 42, None):
            with self.subTest(value_type=type(value).__name__, length=len(value) if isinstance(value, str) else None):
                self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview, api_key=value)))
                self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.fake.add_calls, [])

    def test_malformed_or_invalid_utf8_config_is_rejected_without_changes(self):
        for data in (b'[broken\n', b'model = "one"\nmodel = "two"\n', b'\xff\xfe',
                     b'model_providers = "not-a-table"\n', b'[model_providers]\nbad = 4\n'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                before = self.tree_snapshot()
                self.assert_error(self.store.state)
                self.assert_error(lambda: self.store.preview(self.form()))
                self.assertEqual(self.tree_snapshot(), before)

    def test_oversized_or_non_file_config_is_rejected(self):
        self.path.write_bytes(b"#" + b"x" * (4 * 1024 * 1024))
        self.assert_error(lambda: self.store.preview(self.form()))
        self.path.unlink()
        self.path.mkdir()
        self.assert_error(self.store.state)
        self.assertTrue(self.path.is_dir())
        self.assertEqual(self.fake.add_calls, [])

    def test_config_symlinks_including_dangling_links_are_rejected(self):
        self.path.unlink()
        target = self.root / "external-config.toml"
        target.write_bytes(ORIGINAL)
        for destination in (target, self.root / "nonexistent-config.toml"):
            with self.subTest(destination=destination.name):
                self.path.symlink_to(destination)
                before = self.tree_snapshot()
                self.assert_error(self.store.state)
                self.assert_error(lambda: self.store.preview(self.form()))
                self.assertEqual(self.tree_snapshot(), before)
                self.path.unlink()
        self.assertEqual(target.read_bytes(), ORIGINAL)

    def test_config_swapped_for_symlink_after_preview_is_rejected(self):
        form = self.form()
        preview = self.store.preview(form)
        target = self.root / "external-config.toml"
        target.write_bytes(ORIGINAL)
        self.path.unlink()
        self.path.symlink_to(target)
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)))
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(target.read_bytes(), ORIGINAL)

    def test_symlink_backup_directory_is_rejected(self):
        target = self.root / "external-backups"
        target.mkdir()
        self.store.backup_root.symlink_to(target, target_is_directory=True)
        form = self.form()
        preview = self.store.preview(form)
        before = self.tree_snapshot()
        self.assert_error(lambda: self.store.apply(self.apply_payload(form, preview)))
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(list(target.iterdir()), [])

    def test_missing_model_catalog_setting_returns_null_and_allows_preview_and_apply(self):
        original = b"".join(line for line in ORIGINAL.splitlines(keepends=True)
                            if not line.startswith(b"model_catalog_json ="))
        self.path.write_bytes(original)
        self.assertNotIn("model_catalog_json", tomllib.loads(self.path.read_text()))
        before = self.tree_snapshot()
        state = self.store.state()
        self.assertEqual(state["catalog"], {"path": None, "count": 0, "preserved": True, "source": None})
        self.assertIsNone(json.loads(json.dumps(state))["catalog"]["path"])
        # No model_catalog_json and no models.json: the merge source is absent, so the
        # UI must not offer "write model catalog" rather than hide the built-in models.
        self.assertIsNone(state["catalog"]["source"])
        self.assertEqual(state["current"]["model"], "original-model")
        self.assertEqual(state["revision"], hashlib.sha256(original).hexdigest())
        form = self.form()
        preview = self.store.preview(form)
        self.assertEqual(preview["revision"], state["revision"])
        self.assertEqual(tomllib.loads(preview["snippet"])["model"], form["model"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.tree_snapshot(), before)
        result = self.store.apply(self.apply_payload(form, preview))
        self.assertTrue(result["ok"])
        parsed = tomllib.loads(self.path.read_text())
        self.assertNotIn("model_catalog_json", parsed)
        self.assertEqual(parsed["model"], form["model"])
        self.assertEqual(parsed["model_provider"], form["provider_id"])
        self.assertEqual(self.store.state()["catalog"], state["catalog"])
        self.assertEqual(self.catalog.read_bytes(), CATALOG)
        self.assert_backup(result, original)
        self.assertEqual(self.fake.exists_calls, [])
        self.assertEqual(self.fake.add_calls, [])
        self.assertEqual(self.fake.items, {})

    def test_absent_config_initializes_only_after_apply(self):
        home = self.root / "new-parent" / "new-codex-home"
        store = core.ConfigStore(home, keychain=self.fake)
        form = self.form()
        before = self.tree_snapshot()
        state = store.state()
        preview = store.preview(form)
        self.assertEqual(state["revision"], "missing")
        self.assertEqual(preview["revision"], "missing")
        self.assertEqual(self.tree_snapshot(), before)
        self.assertFalse(home.exists())
        result = store.apply(self.apply_payload(form, preview))
        parsed = tomllib.loads(store.path.read_text())
        self.assertEqual(parsed["model"], "new-model")
        self.assertEqual(parsed["model_provider"], "regression-provider")
        backup = Path(result["backup_path"])
        manifest = json.loads((backup / "manifest.json").read_bytes())
        self.assertFalse(manifest["existed"])
        self.assertTrue(manifest["applied"])
        self.assertEqual(manifest["before_sha256"], "missing")
        self.assertFalse((backup / "config.toml").exists())
        self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
        for directory in (home, store.backup_root, backup):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)


class KeychainTests(unittest.TestCase):
    def setUp(self):
        native = mock.patch.object(keychain_module.C, "CDLL", side_effect=AssertionError("Native access forbidden"))
        native.start()
        self.addCleanup(native.stop)
        self.keychain = object.__new__(keychain_module.Keychain)
        self.keychain.available = True
        self.keychain.cf = mock.Mock()
        self.keychain.sec = mock.Mock()
        query_patch = mock.patch.object(self.keychain, "query", return_value=(101, [102, 103]))
        self.query = query_patch.start()
        self.addCleanup(query_patch.stop)

    def assert_released(self):
        self.assertEqual(self.keychain.cf.CFRelease.call_args_list, [mock.call(101), mock.call(102), mock.call(103)])

    def test_exists_success_uses_metadata_query_and_releases_every_object(self):
        self.keychain.sec.SecItemCopyMatching.return_value = 0
        self.assertTrue(self.keychain.exists("test-account"))
        self.query.assert_called_once_with("test-account")
        self.keychain.sec.SecItemCopyMatching.assert_called_once_with(101, None)
        self.keychain.sec.SecItemAdd.assert_not_called()
        self.assert_released()

    def test_exists_missing_locked_or_denied_returns_false_and_releases(self):
        for status in (-25300, -25308, -25293):
            with self.subTest(status=status):
                self.keychain.cf.reset_mock()
                self.keychain.sec.SecItemCopyMatching.return_value = status
                self.assertFalse(self.keychain.exists("test-account"))
                self.assert_released()

    def test_exists_unexpected_failure_is_reported_and_releases(self):
        self.keychain.sec.SecItemCopyMatching.return_value = -50
        with self.assertRaises(keychain_module.KeychainError) as caught:
            self.keychain.exists("test-account")
        self.assertIn("-50", str(caught.exception))
        self.assert_released()

    def test_exists_native_exception_still_releases(self):
        self.keychain.sec.SecItemCopyMatching.side_effect = OSError("Injected native read failure")
        with self.assertRaises(OSError):
            self.keychain.exists("test-account")
        self.assert_released()

    def test_add_sends_secret_to_query_not_a_process_and_releases(self):
        self.keychain.sec.SecItemAdd.return_value = 0
        self.keychain.add("test-account", SECRET)
        self.query.assert_called_once_with("test-account", SECRET)
        self.keychain.sec.SecItemAdd.assert_called_once_with(101, None)
        self.assertEqual(self.keychain.sec.mock_calls, [mock.call.SecItemAdd(101, None)])
        self.assert_released()

    def test_add_failed_or_duplicate_item_never_updates_or_deletes_an_existing_item(self):
        for status in (-25299, -25293, -50):
            with self.subTest(status=status):
                self.keychain.cf.reset_mock()
                self.keychain.sec.reset_mock()
                self.keychain.sec.SecItemAdd.return_value = status
                with self.assertRaises(keychain_module.KeychainError) as caught:
                    self.keychain.add("test-account", SECRET)
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertEqual(self.keychain.sec.mock_calls, [mock.call.SecItemAdd(101, None)])
                self.assert_released()

    def test_add_native_exception_still_releases(self):
        self.keychain.sec.SecItemAdd.side_effect = OSError("Injected native write failure")
        with self.assertRaises(OSError):
            self.keychain.add("test-account", SECRET)
        self.assert_released()

    def test_unavailable_platform_performs_no_native_calls(self):
        self.keychain.available = False
        self.assertFalse(self.keychain.exists("test-account"))
        with self.assertRaises(keychain_module.KeychainError):
            self.keychain.add("test-account", SECRET)
        self.query.assert_not_called()
        self.assertEqual(self.keychain.sec.mock_calls, [])

    def test_constructor_on_non_macos_does_not_load_native_libraries(self):
        with mock.patch.object(keychain_module.sys, "platform", "linux"), \
                mock.patch.object(keychain_module.C, "CDLL") as load:
            instance = keychain_module.Keychain()
        self.assertFalse(instance.available)
        load.assert_not_called()

    def test_query_uses_utf8_in_memory_data_and_null_dictionary_callbacks(self):
        instance = object.__new__(keychain_module.Keychain)
        instance.cf = mock.Mock()
        instance.string = mock.Mock(side_effect=[201, 202, 204])
        constants = {name: i + 300 for i, name in enumerate((
            "kSecClass", "kSecClassGenericPassword", "kSecAttrService",
            "kSecAttrAccount", "kSecValueData", "kSecAttrLabel",
        ))}
        instance.const = mock.Mock(side_effect=constants.__getitem__)
        captured = {}

        def create_data(allocator, pointer, length):
            self.assertIsNone(allocator)
            captured["secret"] = ctypes.string_at(pointer, length)
            return 203

        def create_dictionary(allocator, keys, values, count, key_callbacks, value_callbacks):
            self.assertEqual((allocator, key_callbacks, value_callbacks), (None, None, None))
            captured["pairs"] = list(zip(list(keys)[:count], list(values)[:count]))
            return 205

        instance.cf.CFDataCreate.side_effect = create_data
        instance.cf.CFDictionaryCreate.side_effect = create_dictionary
        value = "regression-\u00e9-secret"
        dictionary, owned = instance.query("provider.0123", value)
        self.assertEqual(dictionary, 205)
        self.assertEqual(owned, [201, 202, 203, 204])
        self.assertEqual(captured["secret"], value.encode("utf-8"))
        self.assertEqual(captured["pairs"], [
            (constants["kSecClass"], constants["kSecClassGenericPassword"]),
            (constants["kSecAttrService"], 201), (constants["kSecAttrAccount"], 202),
            (constants["kSecValueData"], 203), (constants["kSecAttrLabel"], 204),
        ])
        self.assertEqual(instance.string.call_args_list, [
            mock.call(keychain_module.SERVICE), mock.call("provider.0123"),
            mock.call("Codex custom provider / provider"),
        ])


class HTTPTests(ConfigFixture):
    def setUp(self):
        super().setUp()
        self.httpd = server_module.LocalServer(("127.0.0.1", 0), self.store, token=TOKEN)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01},
            name="codex-regression-http", daemon=True,
        )
        self.addCleanup(self.stop_server)
        self.thread.start()

    def stop_server(self):
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
            self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive(), "Regression HTTP server did not terminate")

    def request(self, method, path, payload=None, *, body=None, token=TOKEN, headers=None):
        request_headers = {}
        if token is not None:
            request_headers["X-Codex-UI-Token"] = token
        if method == "POST":
            request_headers["Content-Type"] = "application/json"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        for name, value in (headers or {}).items():
            if value is None:
                request_headers.pop(name, None)
            else:
                request_headers[name] = value
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            response_headers = {name.lower(): value for name, value in response.getheaders()}
            self.assertFalse(any(name.startswith("access-control-") for name in response_headers), response_headers)
            self.assertNotIn(SECRET.encode(), data)
            return response.status, response_headers, json.loads(data)
        finally:
            connection.close()

    def test_missing_token_is_401_for_get_and_post(self):
        before = self.tree_snapshot()
        for method, path, payload in (("GET", "/api/state", None), ("POST", "/api/preview", self.form())):
            with self.subTest(method=method):
                status, _, data = self.request(method, path, payload, token=None)
                self.assertEqual(status, 401)
                self.assertEqual(data["code"], "unauthorized")
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})

    def test_bad_token_is_401_and_does_not_leak_the_session_token(self):
        status, _, data = self.request("GET", "/api/state", token="incorrect-token")
        self.assertEqual(status, 401)
        self.assertEqual(data["code"], "unauthorized")
        self.assertNotIn(TOKEN, json.dumps(data))

    def test_external_origin_is_403_even_with_valid_token(self):
        for method, path, payload in (("GET", "/api/state", None), ("POST", "/api/preview", self.form())):
            with self.subTest(method=method):
                status, _, data = self.request(method, path, payload, headers={"Origin": "https://external.example.invalid"})
                self.assertEqual(status, 403)
                self.assertEqual(data["code"], "origin_rejected")
        self.assertEqual(self.store.plans, {})

    def test_wrong_host_is_403_even_with_valid_token(self):
        for host in ("external.example.invalid", f"localhost:{self.httpd.server_port}", "127.0.0.1:1"):
            with self.subTest(host=host):
                status, _, data = self.request("GET", "/api/state", headers={"Host": host})
                self.assertEqual(status, 403)
                self.assertEqual(data["code"], "host_rejected")

    def test_cross_site_fetch_is_rejected(self):
        status, _, data = self.request("GET", "/api/state", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(data["code"], "cross_site_rejected")

    def test_options_disables_cors_and_never_returns_allow_origin_headers(self):
        status, _, data = self.request("OPTIONS", "/api/preview", headers={
            "Origin": "https://external.example.invalid", "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(status, 403)
        self.assertEqual(data["code"], "cors_disabled")

    def test_same_origin_state_has_security_headers_and_no_cors(self):
        status, headers, data = self.request("GET", "/api/state", headers={"Origin": self.httpd.origin})
        self.assertEqual(status, 200)
        self.assertEqual(data["config_path"], str(self.path))
        self.assertTrue(data["keychain_available"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])

    def test_body_over_limit_is_413_before_parsing(self):
        before = self.tree_snapshot()
        status, _, data = self.request("POST", "/api/preview", body=b"x" * (server_module.MAX_BODY + 1))
        self.assertEqual(status, 413)
        self.assertEqual(data["code"], "body_too_large")
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})

    def test_valid_body_exactly_at_limit_is_accepted(self):
        encoded = json.dumps(self.form()).encode()
        body = encoded + b" " * (server_module.MAX_BODY - len(encoded))
        self.assertEqual(len(body), server_module.MAX_BODY)
        status, _, data = self.request("POST", "/api/preview", body=body)
        self.assertEqual(status, 200)
        self.assertIn("plan_id", data)
        self.assertEqual(self.path.read_bytes(), ORIGINAL)

    def test_invalid_length_and_transfer_encoding_are_rejected(self):
        for headers in ({"Content-Length": "-1"}, {"Content-Length": "not-a-number"},
                        {"Content-Length": str(server_module.MAX_BODY + 1)}, {"Transfer-Encoding": "chunked"}):
            with self.subTest(headers=headers):
                status, _, data = self.request("POST", "/api/preview", body=b"", headers=headers)
                self.assertEqual(status, 413)
                self.assertEqual(data["code"], "body_too_large")
        self.assertEqual(self.store.plans, {})

    def test_missing_or_wrong_content_type_is_415(self):
        for content_type in (None, "text/plain", "application/x-www-form-urlencoded"):
            with self.subTest(content_type=content_type):
                status, _, data = self.request("POST", "/api/preview", self.form(), headers={"Content-Type": content_type})
                self.assertEqual(status, 415)
                self.assertEqual(data["code"], "content_type")
        self.assertEqual(self.store.plans, {})

    def test_json_content_type_with_charset_is_accepted(self):
        status, _, data = self.request("POST", "/api/preview", self.form(), headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(status, 200)
        self.assertIn("plan_id", data)

    def test_malformed_json_and_non_object_json_are_rejected(self):
        before = self.tree_snapshot()
        for body in (b"{", b"[]", b"null", b'"string"', b"\xff"):
            with self.subTest(body=body):
                status, _, data = self.request("POST", "/api/preview", body=body)
                self.assertEqual(status, 400)
                self.assertEqual(data["code"], "invalid")
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.store.plans, {})

    def test_http_preview_rejects_real_api_key_and_retains_nothing(self):
        before = self.tree_snapshot()
        status, _, data = self.request("POST", "/api/preview", self.form(auth_mode="keychain", has_key=True, api_key=SECRET))
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "invalid")
        self.assertEqual(self.store.plans, {})
        self.assertEqual(self.fake.items, {})
        self.assertEqual(self.tree_snapshot(), before)

    def test_full_http_preview_apply_state_and_replay_with_fake_keychain(self):
        form = self.form(auth_mode="keychain", has_key=True)
        before = self.tree_snapshot()
        status, _, preview = self.request("POST", "/api/preview", form, headers={"Origin": self.httpd.origin})
        self.assertEqual(status, 200)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertEqual(self.fake.items, {})
        payload = self.apply_payload(form, preview, api_key=SECRET)
        status, _, result = self.request("POST", "/api/apply", payload, headers={"Origin": self.httpd.origin})
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertTrue(result["keychain_saved"])
        self.assertFalse(result["connection_tested"])
        manifest = self.assert_backup(result)
        provider = tomllib.loads(self.path.read_text())["model_providers"]["regression-provider"]
        self.assertEqual(self.fake.items, {core.managed_account(provider): SECRET})
        status, _, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["current"]["model"], "new-model")
        self.assertEqual(len(state["backups"]), 1)
        self.assert_no_secret(preview, result, state, manifest, self.path.read_bytes())
        before_replay = self.tree_snapshot()
        status, _, data = self.request("POST", "/api/apply", payload)
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "stale_preview")
        self.assertEqual(self.tree_snapshot(), before_replay)

    def test_http_keychain_failure_prevents_config_commit(self):
        form = self.form(auth_mode="keychain", has_key=True)
        status, _, preview = self.request("POST", "/api/preview", form)
        self.assertEqual(status, 200)
        with mock.patch.object(self.fake, "add", side_effect=keychain_module.KeychainError("Injected Keychain failure")) as add:
            status, _, data = self.request("POST", "/api/apply", self.apply_payload(form, preview, api_key=SECRET))
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "keychain_failed")
        add.assert_called_once()
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertEqual(self.fake.items, {})

    def test_http_stale_fields_and_external_conflicts_are_409(self):
        form = self.form()
        status, _, preview = self.request("POST", "/api/preview", form)
        self.assertEqual(status, 200)
        status, _, data = self.request("POST", "/api/apply", self.apply_payload(form, preview, name="Changed name"))
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "stale_preview")
        external = ORIGINAL + b"\n# HTTP concurrent editor.\n"
        self.path.write_bytes(external)
        status, _, data = self.request("POST", "/api/apply", self.apply_payload(form, preview))
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "conflict")
        self.assertEqual(self.path.read_bytes(), external)

    def test_http_unexpected_exception_hides_sensitive_details(self):
        with mock.patch.object(self.store, "preview", side_effect=RuntimeError("Injected private exception " + SECRET)) as preview:
            status, _, data = self.request("POST", "/api/preview", self.form())
        preview.assert_called_once()
        self.assertEqual(status, 500)
        self.assertEqual(data["code"], "operation_failed")
        self.assertNotIn("Injected private exception", json.dumps(data))
        self.assertEqual(self.path.read_bytes(), ORIGINAL)

    def test_http_malformed_config_returns_controlled_error_without_write(self):
        malformed = b"[broken\n"
        self.path.write_bytes(malformed)
        status, _, data = self.request("GET", "/api/state")
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "invalid")
        self.assertEqual(self.path.read_bytes(), malformed)

    def test_http_path_traversal_does_not_expose_files(self):
        for path in ("/../config_core.py", "/%2e%2e/config.toml", "/api/../config.toml"):
            with self.subTest(path=path):
                status, _, data = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertEqual(data["code"], "not_found")
        self.assertEqual(self.path.read_bytes(), ORIGINAL)


class CatalogTests(ConfigFixture):
    """Merging into `model_catalog_json`, the file the desktop picker renders."""

    def test_relative_catalog_path_resolves_under_the_codex_home_not_the_working_directory(self):
        # Regression: this fixture declares a relative path. Resolving it against the
        # process working directory found no catalog and refused every merge.
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="codex-catalog-cwd-", dir="/tmp") as elsewhere:
            os.chdir(elsewhere)
            self.addCleanup(os.chdir, cwd)
            preview = self.store.preview(self.form(write_catalog=True))
        self.assertEqual(preview["catalog"]["source_path"], str(self.catalog))
        self.assertEqual(preview["catalog"]["source_count"], 1)
        self.assertEqual(preview["catalog"]["added"], ["new-model"])
        self.assertEqual(preview["catalog"]["path"], str(self.home / "models.json"))

    def test_write_catalog_preview_describes_the_merge_without_writing_a_file(self):
        before = self.tree_snapshot()
        with mock.patch.object(core, "atomic_write", side_effect=AssertionError("Preview wrote a file")):
            preview = self.store.preview(self.form(write_catalog=True))
        self.assertEqual(self.tree_snapshot(), before)
        self.assertFalse((self.home / "models.json").exists())
        self.assertIn("合并写入新的模型目录；原始目录文件保留在备份中", preview["changes"])
        self.assertIn("写入模型目录：" + str(self.home / "models.json"), preview["changes"])
        self.assertIn("model_catalog_json", preview["snippet"])
        # The preview shows only what would be added, never the whole catalog.
        self.assertEqual(preview["catalog"]["preview"].count('"slug": "new-model"'), 1)

    def test_write_catalog_apply_merges_after_existing_entries_and_backs_up_the_source(self):
        _, result = self.commit(self.form(write_catalog=True))
        merged = json.loads((self.home / "models.json").read_bytes())
        self.assertEqual([m["slug"] for m in merged["models"]], ["original-model", "new-model"])
        # Existing entries are copied byte-for-byte; only `models` is carried over,
        # matching the shape vendors ship. Unknown top-level keys are not invented.
        self.assertEqual(merged["models"][0], {"slug": "original-model", "extra": [1, 2]})
        self.assertEqual(set(merged), {"models"})
        self.assertEqual(result["catalog"]["added"], ["new-model"])
        self.assertEqual(result["catalog"]["count"], 2)
        parsed = tomllib.loads(self.path.read_text())
        self.assertEqual(parsed["model_catalog_json"], str(self.home / "models.json"))
        backup = Path(result["backup_path"])
        self.assertEqual((backup / "catalog-source.json").read_bytes(), CATALOG)
        self.assertFalse((backup / "catalog-target.json").exists())
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertIsNone(manifest["catalog"]["before_sha256"])
        self.assertEqual(manifest["catalog"]["after_sha256"],
                         hashlib.sha256((self.home / "models.json").read_bytes()).hexdigest())
        self.assertEqual(manifest["catalog"]["backups"], ["catalog-source.json"])

    def test_write_catalog_reports_models_that_already_exist_instead_of_overwriting_them(self):
        preview, result = self.commit(self.form(write_catalog=True, model="original-model"))
        self.assertEqual(result["catalog"]["added"], [])
        self.assertEqual(result["catalog"]["skipped"], ["original-model"])
        merged = json.loads((self.home / "models.json").read_bytes())
        self.assertEqual(merged["models"], [{"slug": "original-model", "extra": [1, 2]}])
        self.assertIn("这些模型 ID 已在目录中，保留原有条目：original-model", preview["warnings"])

    def test_write_catalog_is_refused_when_there_is_no_catalog_to_merge(self):
        # Never create a catalog holding only custom models: it would hide the
        # built-in models in the picker, which is the regression this must avoid.
        self.path.write_bytes(ORIGINAL.replace(b'"model_catalog.json"', b'"missing-catalog.json"'))
        error = self.assert_error(lambda: self.store.preview(self.form(write_catalog=True)),
                                  "catalog_unavailable")
        self.assertIn("官方模型", str(error))

    def test_without_write_catalog_the_catalog_is_preserved_and_the_gap_is_disclosed(self):
        preview, result = self.commit(self.form(write_catalog=False))
        self.assertFalse((self.home / "models.json").exists())
        self.assertIsNone(result["catalog"])
        parsed = tomllib.loads(self.path.read_text())
        self.assertEqual(parsed["model_catalog_json"], "model_catalog.json")
        self.assertTrue(any("new-model" in w and "不在现有本地目录中" in w for w in preview["warnings"]))

    def test_write_catalog_widens_the_desktop_effort_list_when_one_exists(self):
        self.path.write_bytes(ORIGINAL + b'[desktop]\nenabled-reasoning-efforts = ["medium"]\n')
        preview, _ = self.commit(self.form(write_catalog=True, reasoning_effort="max"))
        parsed = tomllib.loads(self.path.read_text())
        self.assertEqual(parsed["desktop"]["enabled-reasoning-efforts"], list(core.REASONING_EFFORTS))
        self.assertIn("在 [desktop] 中启用全部推理强度选项，避免所选强度被界面隐藏。", preview["changes"])

    def test_write_catalog_discloses_a_missing_desktop_table_instead_of_inventing_one(self):
        preview, _ = self.commit(self.form(write_catalog=True))
        self.assertNotIn("desktop", tomllib.loads(self.path.read_text()))
        self.assertTrue(any("配置中没有 [desktop] 表" in w for w in preview["warnings"]))


class LoginModeTests(ConfigFixture):
    """`preferred_auth_method` / `forced_login_method`: the official API-key login switch."""

    def test_force_api_login_writes_both_login_keys(self):
        preview, _ = self.commit(self.form(force_api_login=True))
        parsed = tomllib.loads(self.path.read_text())
        self.assertEqual(parsed["preferred_auth_method"], "apikey")
        self.assertEqual(parsed["forced_login_method"], "api")
        self.assertIn("写入 preferred_auth_method 与 forced_login_method，启动后直接使用 API Key 登录。",
                      preview["changes"])
        self.assertTrue(any("ChatGPT" in w for w in preview["warnings"]))

    def test_force_api_login_leaves_the_config_alone_when_not_requested(self):
        self.commit(self.form(force_api_login=False))
        parsed = tomllib.loads(self.path.read_text())
        self.assertNotIn("preferred_auth_method", parsed)
        self.assertNotIn("forced_login_method", parsed)

    def test_force_api_login_is_refused_without_a_credential(self):
        self.assert_error(lambda: self.store.preview(
            self.form(auth_mode="none", env_key="", force_api_login=True)))


class RestoreTests(ConfigFixture):
    """Rollback of one applied backup; the restore is itself backed up first."""

    def test_restore_returns_the_backed_up_bytes_and_is_itself_reversible(self):
        _, applied = self.commit()
        backup_id = Path(applied["backup_path"]).name
        self.assertIn(backup_id, {b["id"] for b in self.store.state()["backups"]})
        applied_bytes = self.path.read_bytes()
        restored = self.store.restore({"confirmed": True, "backup_id": backup_id})
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["backup_id"], backup_id)
        self.assertEqual(restored["config_path"], str(self.path))
        self.assertFalse(restored["catalog_restored"])
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        # The discarded state is kept, so the restore can itself be undone.
        safety = self.home / "model-ui-backups" / restored["safety_backup"]
        self.assertEqual((safety / "config.toml").read_bytes(), applied_bytes)
        manifest = json.loads((safety / "manifest.json").read_text())
        self.assertFalse(manifest["applied"])
        self.assertEqual(manifest["before_sha256"], hashlib.sha256(applied_bytes).hexdigest())
        self.assertEqual(manifest["after_sha256"], hashlib.sha256(ORIGINAL).hexdigest())

    def test_restore_rolls_back_the_catalog_it_wrote(self):
        _, applied = self.commit(self.form(write_catalog=True))
        backup_id = Path(applied["backup_path"]).name
        restored = self.store.restore({"confirmed": True, "backup_id": backup_id})
        self.assertTrue(restored["catalog_restored"])
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertEqual((self.home / "models.json").read_bytes(), CATALOG)
        self.assertEqual(self.catalog.read_bytes(), CATALOG)

    def test_restore_requires_confirmation_and_a_real_applied_backup(self):
        self.assert_error(lambda: self.store.restore({"backup_id": "x"}), "confirmation_required")
        self.assert_error(lambda: self.store.restore({"confirmed": True, "backup_id": "x"}),
                          "invalid_backup")
        self.assert_error(lambda: self.store.restore(
            {"confirmed": True, "backup_id": "20260101T000000Z-deadbeef"}), "invalid_backup")
        self.assertEqual(self.path.read_bytes(), ORIGINAL)

    def test_restore_rejects_a_backup_whose_bytes_do_not_match_its_manifest(self):
        _, applied = self.commit()
        backup_id = Path(applied["backup_path"]).name
        (self.home / "model-ui-backups" / backup_id / "config.toml").write_bytes(b"# tampered\n")
        self.assert_error(lambda: self.store.restore({"confirmed": True, "backup_id": backup_id}),
                          "invalid_backup")
        self.assertNotEqual(self.path.read_bytes(), b"# tampered\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
