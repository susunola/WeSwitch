"""Offline unittest coverage; source files are parsed, never imported or executed."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import re
import unittest

import backend_i18n as i18n


ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ("config_core.py", "model_discovery.py", "connection_test.py", "keychain.py", "server.py")
CJK = re.compile(r"[\u3400-\u9fff]")
KNOWN = "不支持的认证方式。"
ENGLISH = "Unsupported authentication method."


def extract_messages(source):
    """Separate complete literals from f-strings and ignore documentation.

    Do not count f-string fragments (including conditional labels and fallback
    values) as independent messages. Their entire templates are checked instead.
    Docstrings and constants reused inside f-strings are not standalone messages.
    """
    fixed = []
    dynamic = []
    tree = ast.parse(source)
    in_fstring = set()
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            first = body[0] if body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            in_fstring.add(id(node))
            for part in ast.walk(node):
                in_fstring.add(id(part))
            continue
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            shape = "".join(part.value if isinstance(part, ast.Constant) else "{}"
                            for part in node.values)
            # A nested f-string join is one placeholder, not flattened literal parts.
            if any(not isinstance(part, ast.Constant) for part in node.values):
                shape = re.sub(r"(?:\{\}){2,}", "{}", shape)
            if CJK.search(shape):
                dynamic.append((node.lineno, shape))
            continue
        if not isinstance(node, ast.Constant) or id(node) in in_fstring or id(node) in docstrings:
            continue
        if isinstance(node.value, str) and CJK.search(node.value):
            fixed.append((node.lineno, node.value))
    return fixed, dynamic


# Each source f-string shape has complete examples and independent expectations.
# Both branches of the provider action and ambiguous model fallbacks are covered.
DYNAMIC_CASES = {
    "字段 {} 必须是文本。": (
        ("字段 provider_id 必须是文本。", "Field provider_id must be text."),
    ),
    "字段 {} 包含控制字符。": (
        ("字段 model 包含控制字符。", "Field model contains control characters."),
    ),
    "字段 {} 为空、过长或包含控制字符。": (
        ("字段 base_url 为空、过长或包含控制字符。",
         "Field base_url is empty, too long, or contains control characters."),
    ),
    "字段 {} 必须为布尔值。": (
        ("字段 set_default 必须为布尔值。", "Field set_default must be a boolean."),
        ("字段 has_key 必须为布尔值。", "Field has_key must be a boolean."),
    ),
    "{}供应商：{}": (
        ("添加供应商：my-gateway_v2", "Add provider: my-gateway_v2"),
        ("更新供应商：existing-gateway", "Update provider: existing-gateway"),
    ),
    "默认模型：{} → {}": (
        ("默认模型：old-model → new/model:v2", "Default model: old-model → new/model:v2"),
        ("默认模型：未设置 → 模型-中文", "Default model: 未设置 → 模型-中文"),
        ("默认模型： → new-model", "Default model:  → new-model"),
    ),
    "移除旧模型专属参数：{}（原值完整保留在备份中）": (
        ("移除旧模型专属参数：model_context_window（原值完整保留在备份中）",
         "Remove previous model-specific parameter: model_context_window (the original value is fully preserved in the backup)"),
    ),
    "忽略了 {} 条格式不正确或包含敏感内容的记录。": (
        ("忽略了 27 条格式不正确或包含敏感内容的记录。",
         "Ignored 27 records with an invalid format or sensitive content."),
    ),
    "服务商模型列表接口返回 HTTP {}；未读取错误正文，请稍后重试或手动填写。": (
        ("服务商模型列表接口返回 HTTP 503；未读取错误正文，请稍后重试或手动填写。",
         "The provider's model list endpoint returned HTTP 503; the error body was not read. Retry later or enter the model manually."),
    ),
    "服务端返回 HTTP {}；未读取错误正文，请稍后重试。": (
        ("服务端返回 HTTP 500；未读取错误正文，请稍后重试。",
         "The service returned HTTP 500; the error body was not read. Retry later."),
        ("服务端返回 HTTP 418；未读取错误正文，请稍后重试。",
         "The service returned HTTP 418; the error body was not read. Retry later."),
    ),
    "无法检查钥匙串（系统状态 {}）。请先解锁登录钥匙串。": (
        ("无法检查钥匙串（系统状态 -25293）。请先解锁登录钥匙串。",
         "Unable to check Keychain (system status -25293). Unlock the login Keychain first."),
    ),
    "钥匙串保存未成功（系统状态 {}）。未写入 Codex 配置。": (
        ("钥匙串保存未成功（系统状态 -25299）。未写入 Codex 配置。",
         "Keychain could not be saved (system status -25299). The Codex configuration was not written."),
        ("钥匙串保存未成功（系统状态 50）。未写入 Codex 配置。",
         "Keychain could not be saved (system status 50). The Codex configuration was not written."),
    ),
    "一次添加 {} 个模型：{}": (
        ("一次添加 2 个模型：deepseek-reasoner、deepseek-chat",
         "Add 2 models at once: deepseek-reasoner、deepseek-chat"),
    ),
    "为其余模型写入可切换 profile：{}": (
        ("为其余模型写入可切换 profile：my-gateway-deepseek-chat",
         "Write switchable profiles for the remaining models: my-gateway-deepseek-chat"),
    ),
    "写入可切换 profile：{}": (
        ("写入可切换 profile：my-gateway-deepseek-reasoner",
         "Write a switchable profile: my-gateway-deepseek-reasoner"),
    ),
    "移除不再选择的托管 profile：{}（原值完整保留在备份中）": (
        ("移除不再选择的托管 profile：my-gateway-gpt-4（原值完整保留在备份中）",
         "Remove the managed profile that is no longer selected: my-gateway-gpt-4 (the original value is fully preserved in the backup)"),
    ),
    "这些模型不在现有本地目录中：{}。桌面端选择器只渲染目录条目，未列入时选择器会退回默认推荐模型集；勾选写入模型目录可让它们出现在列表中。": (
        ("这些模型不在现有本地目录中：a、b。桌面端选择器只渲染目录条目，未列入时选择器会退回默认推荐模型集；勾选写入模型目录可让它们出现在列表中。",
         "These models are not in the existing local catalog: a、b. The desktop picker renders catalog entries only, so an unlisted model makes the picker fall back to the default recommended set. Enable 'write model catalog' to make them appear in the list."),
    ),
    "写入模型目录：{}": (
        ("写入模型目录：/Users/me/.codex/models.json",
         "Write the model catalog: /Users/me/.codex/models.json"),
    ),
    "合并现有目录 {} 条记录，新增 {} 个模型条目": (
        ("合并现有目录 8 条记录，新增 2 个模型条目",
         "Merge 8 entries from the existing catalog and add 2 model entries"),
        ("合并现有目录 0 条记录，新增 1 个模型条目",
         "Merge 0 entries from the existing catalog and add 1 model entries"),
    ),
    "这些模型 ID 已在目录中，保留原有条目：{}": (
        ("这些模型 ID 已在目录中，保留原有条目：gpt-5.6-terra、deepseek-v4-pro",
         "These model IDs are already in the catalog; the existing entries are kept: gpt-5.6-terra、deepseek-v4-pro"),
    ),
    "profile 名称 {} 已被占用且含本工具不写入的设置。请换个供应商 ID，或手动改名/删除该 profile 后重试。": (
        ("profile 名称 my-gateway-fast 已被占用且含本工具不写入的设置。请换个供应商 ID，或手动改名/删除该 profile 后重试。",
         "The profile name my-gateway-fast is already used and contains settings this tool does not write. Choose a different provider ID, or rename or remove that profile manually before retrying."),
    ),
    "一次最多添加 {} 个模型；请分批添加。": (
        ("一次最多添加 100 个模型；请分批添加。",
         "At most 100 models can be added at once; add them in batches."),
    ),
    "本地配置界面已启动：{}；仅监听 127.0.0.1。": (
        ("本地配置界面已启动：http://127.0.0.1:18765；仅监听 127.0.0.1。",
         "The local configuration UI has started: http://127.0.0.1:18765; listening only on 127.0.0.1."),
    ),
}


class LanguageTests(unittest.TestCase):
    def test_explicit_english(self):
        for language in ("en", "EN", "En", " en ", "\ten\t"):
            with self.subTest(language=language):
                self.assertEqual(i18n.normalize_language(language), "en")
                self.assertEqual(i18n.translate_text(KNOWN, language), ENGLISH)

    def test_chinese_default_and_unsupported_languages(self):
        self.assertEqual(i18n.normalize_language(), "zh")
        for language in (None, "", "zh", "zh-CN", "ZH", "fr", "en-US", "en_GB",
                         "en,zh;q=0.9", "english", "en\x00", 1, True, b"en", [], {}):
            with self.subTest(language=language):
                self.assertEqual(i18n.normalize_language(language), "zh")
                self.assertEqual(i18n.translate_text(KNOWN, language), KNOWN)
                self.assertEqual(i18n.translate_response({"error": KNOWN}, language), {"error": KNOWN})

    def test_language_is_request_local(self):
        payload = {"error": KNOWN}
        for _ in range(3):
            self.assertEqual(i18n.translate_response(payload, "en"), {"error": ENGLISH})
            self.assertEqual(i18n.translate_response(payload), payload)
            self.assertEqual(i18n.translate_text(KNOWN), KNOWN)


class SourceCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.messages = {name: extract_messages((ROOT / name).read_text(encoding="utf-8"))
                        for name in SOURCE_FILES}

    def assert_fixed_covered(self, filename):
        fixed, _ = self.messages[filename]
        self.assertTrue(fixed, f"No Chinese messages extracted from {filename}")
        for line, message in fixed:
            with self.subTest(source=f"{filename}:{line}", message=message):
                self.assertIn(message, i18n._EXACT_TRANSLATIONS)
                translated = i18n.translate_text(message, "en")
                self.assertTrue(translated)
                self.assertNotEqual(translated, message)
                self.assertIsNone(CJK.search(translated))
                self.assertEqual(i18n.translate_text(message), message)

    def test_config_core_fixed_messages(self):
        self.assert_fixed_covered("config_core.py")

    def test_model_discovery_fixed_messages(self):
        self.assert_fixed_covered("model_discovery.py")

    def test_keychain_fixed_messages(self):
        self.assert_fixed_covered("keychain.py")

    def test_server_fixed_messages_including_cli(self):
        self.assert_fixed_covered("server.py")

    def test_translation_table_contains_only_complete_source_messages(self):
        extracted = {message for fixed, _ in self.messages.values() for _, message in fixed}
        self.assertEqual(set(i18n._EXACT_TRANSLATIONS), extracted)

    def test_all_source_fstrings_have_explicit_examples(self):
        extracted = {shape for _, dynamic in self.messages.values() for _, shape in dynamic}
        self.assertEqual(extracted, set(DYNAMIC_CASES))
        for name, (_, dynamic) in self.messages.items():
            for line, shape in dynamic:
                with self.subTest(source=f"{name}:{line}", template=shape):
                    for message, expected in DYNAMIC_CASES[shape]:
                        self.assertEqual(i18n.translate_text(message, "en"), expected)
                        self.assertEqual(i18n.translate_text(message), message)

    def test_extractor_excludes_docs_english_comments_and_template_fragments(self):
        source = '''"""模块说明。"""
# Chinese comments are not messages: 注释。
def example():
    """函数说明。"""
    english = "Already English."
    message = "不支持的认证方式。"
    change = f"{'更新' if existing else '添加'}供应商：{provider_id}"
    model = f"默认模型：{data.get('model', '未设置')} → {model_id}"
'''
        fixed, dynamic = extract_messages(source)
        self.assertEqual([message for _, message in fixed], [KNOWN])
        self.assertEqual([shape for _, shape in dynamic], ["{}供应商：{}", "默认模型：{} → {}"])

    def test_table_has_no_duplicate_keys(self):
        tree = ast.parse((ROOT / "backend_i18n.py").read_text(encoding="utf-8"))
        table = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "_EXACT_TRANSLATIONS"
                             for target in node.targets))
        keys = [ast.literal_eval(key) for key in table.keys]
        self.assertEqual(len(keys), len(set(keys)))


class TextTests(unittest.TestCase):
    def test_every_exact_translation(self):
        for message, expected in i18n._EXACT_TRANSLATIONS.items():
            with self.subTest(message=message):
                self.assertEqual(i18n.translate_text(message, "en"), expected)
                self.assertEqual(i18n.translate_text(expected, "en"), expected)

    def test_unknown_messages_are_unchanged(self):
        for message in ("", "未知但无害的提示。", "Future English message.",
                        "不支持的新功能。", "更新", "添加", "未设置"):
            with self.subTest(message=message):
                self.assertEqual(i18n.translate_text(message, "en"), message)

    def test_non_string_input_is_unchanged(self):
        for value in (None, True, False, 42, 3.25, b"raw", [KNOWN], {"error": KNOWN}, (KNOWN,)):
            with self.subTest(value=value):
                self.assertIs(i18n.translate_text(value, "en"), value)

    def test_matches_are_whole_messages_not_substrings(self):
        messages = [(KNOWN, False)] + [(message, shape.endswith("{}"))
                                      for shape, examples in DYNAMIC_CASES.items()
                                      for message, _ in examples]
        for message, terminal_capture in messages:
            candidates = ["prefix " + message, " " + message, message + "\n",
                          "\n" + message, message + "\r\n"]
            # A terminal parameter has no closing delimiter: appended characters
            # belong to that parameter and must be retained, not reinterpreted.
            if not terminal_capture:
                candidates.extend((message + " suffix", message + " "))
            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    self.assertEqual(i18n.translate_text(candidate, "en"), candidate)

    def test_all_templates_are_explicitly_anchored_and_exercised(self):
        examples = [message for cases in DYNAMIC_CASES.values() for message, _ in cases]
        for pattern, _ in i18n._TEMPLATES:
            with self.subTest(pattern=pattern.pattern):
                self.assertTrue(pattern.pattern.startswith(r"\A"))
                self.assertTrue(pattern.pattern.endswith(r"\Z"))
                self.assertTrue(any(pattern.fullmatch(message) for message in examples))
        for message in examples:
            self.assertEqual(sum(pattern.fullmatch(message) is not None
                                 for pattern, _ in i18n._TEMPLATES), 1)

    def test_provider_identifiers_are_preserved_verbatim(self):
        for provider in ("my-provider_v2", "供应商-中文", KNOWN, "未设置", "{field}\\1$1", "  mixed-中文  "):
            with self.subTest(provider=provider):
                self.assertEqual(i18n.translate_text(f"添加供应商：{provider}", "en"),
                                 f"Add provider: {provider}")
                self.assertEqual(i18n.translate_text(f"更新供应商：{provider}", "en"),
                                 f"Update provider: {provider}")

    def test_model_identifiers_are_not_translated_or_trimmed(self):
        for old, new in (("未设置", "未设置"), (KNOWN, "更新供应商：中文"),
                         ("  old/model:中文  ", "new/{model}\\1$1"),
                         ("旧 → 标识", "新模型/β:v2")):
            with self.subTest(old=old, new=new):
                self.assertEqual(i18n.translate_text(f"默认模型：{old} → {new}", "en"),
                                 f"Default model: {old} → {new}")

    def test_captured_paths_and_field_names_are_preserved(self):
        for parameter in ("/Users/用户/配置 文件.toml", r"C:\Users\用户\配置.toml",
                          "{field}\\g<1>$1", KNOWN, " model_context_window "):
            with self.subTest(parameter=parameter):
                self.assertEqual(i18n.translate_text(f"字段 {parameter} 必须是文本。", "en"),
                                 f"Field {parameter} must be text.")
                self.assertEqual(
                    i18n.translate_text(f"移除旧模型专属参数：{parameter}（原值完整保留在备份中）", "en"),
                    f"Remove previous model-specific parameter: {parameter} (the original value is fully preserved in the backup)")

    def test_numeric_captures_are_preserved_without_coercion(self):
        for count in ("0", "1", "0007", "5000"):
            self.assertEqual(i18n.translate_text(f"忽略了 {count} 条格式不正确或包含敏感内容的记录。", "en"),
                             f"Ignored {count} records with an invalid format or sensitive content.")
        for status in ("0", "-0", "-25293", "50", "00050"):
            self.assertEqual(
                i18n.translate_text(f"无法检查钥匙串（系统状态 {status}）。请先解锁登录钥匙串。", "en"),
                f"Unable to check Keychain (system status {status}). Unlock the login Keychain first.")

    def test_malformed_templates_fall_back_without_guessing(self):
        for message in ("忽略了 many 条格式不正确或包含敏感内容的记录。",
                        "忽略了 １２ 条格式不正确或包含敏感内容的记录。",
                        "服务商模型列表接口返回 HTTP OOPS；未读取错误正文，请稍后重试或手动填写。",
                        "无法检查钥匙串（系统状态 unknown）。请先解锁登录钥匙串。",
                        "字段 name 必须是文本!", "删除供应商：provider", "添加供应商：",
                        "默认模型：old -> new"):
            with self.subTest(message=message):
                self.assertEqual(i18n.translate_text(message, "en"), message)

    def test_multiline_code_and_embedded_messages_are_unchanged(self):
        for code in (f'# {KNOWN}\nmodel = "添加供应商：demo"\n',
                     'error = "不支持的认证方式。"\nmessage = "页面不存在。"',
                     '```toml\nmodel = "默认模型：old → new"\n```',
                     "添加供应商：first\nsecond", "字段 line\nbreak 必须是文本。"):
            with self.subTest(code=code):
                self.assertEqual(i18n.translate_text(code, "en"), code)

    def test_existing_english_credential_descriptions_are_unchanged(self):
        for message in ("Existing Keychain items were not overwritten or deleted.",
                        "Codex custom provider / provider-name"):
            self.assertEqual(i18n.translate_text(message, "en"), message)

    def test_cli_messages_can_use_translate_text_independently(self):
        self.assertEqual(i18n.translate_text("本地配置界面已在运行。", "en"),
                         "The local configuration UI is already running.")
        self.assertEqual(i18n.translate_text("在默认浏览器打开界面", "en"),
                         "Open the UI in the default browser")
        message, expected = DYNAMIC_CASES["本地配置界面已启动：{}；仅监听 127.0.0.1。"][0]
        self.assertEqual(i18n.translate_text(message, "en"), expected)


class ResponseTests(unittest.TestCase):
    def test_only_allowed_human_fields_are_translated(self):
        payload = {
            "error": KNOWN, "message": KNOWN, "warnings": [KNOWN], "changes": [KNOWN],
            "catalog": {"warning": KNOWN, "path": KNOWN},
            "warning": KNOWN, "description": KNOWN, "detail": KNOWN, "title": KNOWN,
            "credential_recovery": KNOWN, "other": [KNOWN, {"label": KNOWN}],
        }
        expected = copy.deepcopy(payload)
        expected.update(error=ENGLISH, message=ENGLISH, warnings=[ENGLISH], changes=[ENGLISH])
        expected["catalog"]["warning"] = ENGLISH
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_no_input_mutation_or_nested_mutable_aliasing(self):
        payload = {"warnings": [KNOWN, {"message": KNOWN}],
                   "catalog": {"warning": KNOWN}, "paths": [["/tmp/中文"]],
                   "unknown": {"list": [KNOWN]}}
        before = copy.deepcopy(payload)
        translated = i18n.translate_response(payload, "en")
        self.assertEqual(payload, before)
        self.assertIsNot(translated, payload)
        translated["warnings"][1]["message"] = "changed"
        translated["warnings"].append("new")
        translated["catalog"]["warning"] = "changed"
        translated["paths"][0].append("new")
        translated["unknown"]["list"].append("new")
        self.assertEqual(payload, before)

    def test_chinese_default_returns_a_fresh_unchanged_structure(self):
        payload = {"error": KNOWN, "warnings": [KNOWN], "catalog": {"warning": KNOWN}}
        for language in (None, "zh", "fr", "en-US"):
            translated = i18n.translate_response(payload, language)
            self.assertEqual(translated, payload)
            self.assertIsNot(translated, payload)
            self.assertIsNot(translated["warnings"], payload["warnings"])
            self.assertIsNot(translated["catalog"], payload["catalog"])

    def test_technical_fields_preserve_even_exact_message_collisions(self):
        payload = {
            "config_path": KNOWN, "revision": KNOWN, "models": [{"id": KNOWN}],
            "provider": {"name": KNOWN, "id": KNOWN}, "providers": [{"name": KNOWN, "id": KNOWN}],
            "snippet": KNOWN, "snippets": [KNOWN], "endpoint": KNOWN, "base_url": KNOWN,
            "path": KNOWN, "paths": [KNOWN], "files": [KNOWN], "backup_path": KNOWN,
            "plan_id": KNOWN, "provider_id": KNOWN, "model_id": KNOWN, "backup_id": KNOWN,
            "code": KNOWN, "current": {"model": KNOWN, "model_provider": KNOWN},
            "catalog": {"warning": KNOWN, "path": KNOWN}, "error": KNOWN,
        }
        expected = copy.deepcopy(payload)
        expected["catalog"]["warning"] = ENGLISH
        expected["error"] = ENGLISH
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_protected_subtrees_are_opaque_even_for_unexpected_shapes(self):
        nested = {"message": KNOWN, "catalog": {"warning": KNOWN}, "warnings": [KNOWN]}
        payload = {field: copy.deepcopy(nested) for field in i18n._OPAQUE_FIELDS}
        self.assertEqual(i18n.translate_response(payload, "en"), payload)
        translated = i18n.translate_response(payload, "en")
        self.assertIsNot(translated["snippet"], payload["snippet"])
        self.assertIsNot(translated["snippet"]["warnings"], payload["snippet"]["warnings"])

    def test_message_arrays_are_field_scoped_not_blanket_translated(self):
        payload = {"warnings": [KNOWN, [KNOWN, {"message": KNOWN, "id": KNOWN, "other": KNOWN}]],
                   "changes": {"message": KNOWN, "arbitrary": [KNOWN]},
                   "error": {"message": KNOWN, "code": "invalid"}}
        expected = {"warnings": [ENGLISH, [ENGLISH, {"message": ENGLISH, "id": KNOWN, "other": KNOWN}]],
                    "changes": {"message": ENGLISH, "arbitrary": [KNOWN]},
                    "error": {"message": ENGLISH, "code": "invalid"}}
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_nested_wrappers_and_root_arrays(self):
        payload = [KNOWN, {"result": [[{"error": KNOWN, "message": KNOWN}]]},
                   [None, {"data": {"catalog": {"warning": KNOWN}, "warning": KNOWN}}]]
        expected = [KNOWN, {"result": [[{"error": ENGLISH, "message": ENGLISH}]]},
                    [None, {"data": {"catalog": {"warning": ENGLISH}, "warning": KNOWN}}]]
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_catalog_warning_does_not_leak_to_other_warning_fields(self):
        payload = {"warning": KNOWN, "other": {"warning": KNOWN},
                   "catalog": {"warning": KNOWN, "extra": {"warning": KNOWN},
                               "message": KNOWN},
                   "warnings": [{"warning": KNOWN}]}
        expected = copy.deepcopy(payload)
        expected["catalog"].update(warning=ENGLISH, message=ENGLISH)
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_mixed_json_types_and_empty_containers(self):
        payload = {"error": None, "message": False,
                   "warnings": [KNOWN, None, False, True, 0, 2.75, [], {}, [KNOWN]],
                   "changes": 12, "catalog": {"warning": [None, KNOWN]},
                   "empty": {"list": [], "object": {}}}
        expected = copy.deepcopy(payload)
        expected["warnings"][0] = ENGLISH
        expected["warnings"][-1][0] = ENGLISH
        expected["catalog"]["warning"][1] = ENGLISH
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_scalar_responses_are_not_implicitly_human_messages(self):
        for value in (KNOWN, "添加供应商：demo", None, True, 0, 2.5, b"raw"):
            self.assertIs(i18n.translate_response(value, "en"), value)

    def test_unknown_human_messages_remain_original(self):
        unknown = "未知但无害的提示。"
        payload = {"error": unknown, "message": unknown, "warnings": [unknown],
                   "changes": [unknown], "catalog": {"warning": unknown}}
        self.assertEqual(i18n.translate_response(payload, "en"), payload)

    def test_multiline_code_is_unchanged_in_every_field(self):
        code = 'model = "不支持的认证方式。"\n# 添加供应商：demo\n[model_providers.demo]\n'
        payload = {"error": code, "message": code, "warnings": [code], "changes": [code],
                   "snippet": code, "snippets": [code], "catalog": {"warning": code}}
        self.assertEqual(i18n.translate_response(payload, "en"), payload)

    def test_dynamic_parameters_survive_response_translation(self):
        payload = {"changes": ["添加供应商：中文-provider", "默认模型：未设置 → 中文/模型:v1"],
                   "model": "中文/模型:v1", "provider_id": "中文-provider"}
        expected = {"changes": ["Add provider: 中文-provider", "Default model: 未设置 → 中文/模型:v1"],
                    "model": "中文/模型:v1", "provider_id": "中文-provider"}
        self.assertEqual(i18n.translate_response(payload, "en"), expected)

    def test_aliased_message_and_identifier_lists_do_not_cross_translate(self):
        shared = [KNOWN, {"message": KNOWN}]
        payload = {"warnings": shared, "ids": shared, "other": shared}
        translated = i18n.translate_response(payload, "en")
        self.assertEqual(translated["warnings"], [ENGLISH, {"message": ENGLISH}])
        self.assertEqual(translated["ids"], [KNOWN, {"message": KNOWN}])
        self.assertEqual(translated["other"], [KNOWN, {"message": ENGLISH}])
        self.assertIsNot(translated["warnings"], translated["ids"])
        self.assertEqual(shared, [KNOWN, {"message": KNOWN}])

    def test_deep_arrays_do_not_hit_python_recursion_limit(self):
        depth = 2000
        payload = {"error": KNOWN}
        for _ in range(depth):
            payload = [payload]
        for language, expected in (("en", ENGLISH), ("zh", KNOWN)):
            translated = i18n.translate_response(payload, language)
            source_cursor, result_cursor = payload, translated
            for _ in range(depth):
                self.assertIsNot(result_cursor, source_cursor)
                source_cursor, result_cursor = source_cursor[0], result_cursor[0]
            self.assertEqual(result_cursor, {"error": expected})
            self.assertEqual(source_cursor, {"error": KNOWN})

    def test_deep_message_arrays_translate_only_message_leaves(self):
        value = KNOWN
        for _ in range(2000):
            value = [value]
        payload = {"warnings": value, "paths": value, "arbitrary": value}
        translated = i18n.translate_response(payload, "en")
        for key, expected in (("warnings", ENGLISH), ("paths", KNOWN), ("arbitrary", KNOWN)):
            cursor = translated[key]
            for _ in range(2000):
                cursor = cursor[0]
            self.assertEqual(cursor, expected)

    def test_deep_mixed_dicts_and_lists(self):
        payload = {"catalog": {"warning": KNOWN}}
        for _ in range(1100):
            payload = {"result": [None, payload]}
        translated = i18n.translate_response(payload, "en")
        for _ in range(1100):
            translated = translated["result"][1]
        self.assertEqual(translated, {"catalog": {"warning": ENGLISH}})

    def test_json_roundtrip_keys_types_and_order_remain_intact(self):
        payload = {"ok": False, "code": "unauthorized", "error": KNOWN, "warnings": [],
                   "count": 0, "ratio": 1.25, "optional": None, "models": [{"id": "中文/模型"}]}
        translated = i18n.translate_response(payload, "en")
        self.assertEqual(list(translated), list(payload))
        self.assertEqual(json.loads(json.dumps(translated, ensure_ascii=False)), translated)
        self.assertIs(translated["ok"], False)
        self.assertIs(type(translated["count"]), int)
        self.assertIs(type(translated["ratio"]), float)
        self.assertEqual(translated["error"], ENGLISH)
        self.assertEqual(translated["models"], payload["models"])

    def test_translation_is_idempotent(self):
        payload = {"warnings": [KNOWN], "changes": ["添加供应商：供应商"],
                   "catalog": {"warning": KNOWN}, "models": [{"id": KNOWN}]}
        first = i18n.translate_response(payload, "en")
        second = i18n.translate_response(first, "en")
        self.assertEqual(second, first)
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
