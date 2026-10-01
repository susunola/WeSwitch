"""Real-browser bilingual regressions: disposable config, fake keys, owned loopback only."""
import copy
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import tomllib
from unittest import mock

from backend_i18n import translate_text
from config_core import ConfigStore
from server import LocalServer
from test_config import FakeKeychain
from test_discovery import UpstreamServer, reply

SESSION = f"weswitch-i18n-test-{os.getpid()}"
TEST_KEY = "sk-browser-test-only"
REPLACEMENT_KEY = "sk-browser-replacement-only"
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
PASSED = 0
FAILURES = []


def browser(*args):
    # Inherit PATH, including the caller's working Node.js installation.
    result = subprocess.run(
        ["agent-browser", "--session", SESSION, *args], env=dict(os.environ),
        capture_output=True, text=True, timeout=40,
    )
    if result.returncode:
        raise AssertionError("Browser command " + args[0] + " failed: " + (result.stderr or result.stdout))
    return result.stdout


def js(code):
    return browser("eval", code)


def value(expression):
    return json.loads(js(expression))


def wait_for(expression):
    js("(async()=>{const end=Date.now()+8000;while(!(" + expression + ")){"
       "if(Date.now()>end)throw new Error('UI wait failed: '+" + json.dumps(expression) + ");"
       "await new Promise(r=>requestAnimationFrame(r));}return true;})()")


def expect(condition, label, detail=None):
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILURES.append(label)
        # Escape diagnostic Unicode so test output remains English while preserving exact evidence.
        print("FAIL: " + label + (" | " + json.dumps(detail, ensure_ascii=True) if detail is not None else ""), flush=True)


def check(expression, label):
    expect(value("Boolean(" + expression + ")"), label)


def texts(expected, label):
    actual = value("Object.fromEntries(" + json.dumps(list(expected)) +
                   ".map(id=>[id,document.getElementById(id).textContent.trim()]))")
    expect(actual == expected, label, actual)


def fill(values):
    js("(()=>{const f=document.getElementById('provider-form');for(const [k,v] of Object.entries(" +
       json.dumps(values) + ")){const e=f.elements[k];if(e.type==='checkbox')e.checked=v;else e.value=v;"
       "e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}return true;})()")


def click(element):
    # Deterministic DOM activation: CLI coordinate clicks can miss off-screen controls.
    js("document.getElementById(" + json.dumps(element) + ").click();true")


def audit_english(label):
    # Inspect rendered text, option labels, placeholders and accessible names, not script/source text.
    # No user-data exemption is needed: all fixture names and model IDs are ASCII.
    findings = value(r"""(()=>{
      const cjk=/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
      const out=[];
      const visible=e=>e && !e.closest('[hidden],script,style,noscript') &&
        getComputedStyle(e).visibility!=='hidden' && (e.getClientRects().length ||
          (e.tagName==='OPTION' && e.closest('select')?.getClientRects().length));
      const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
      for(let n; n=walker.nextNode();){
        const e=n.parentElement, s=n.nodeValue.trim();
        if(visible(e) && cjk.test(s) && !(e.id==='language-zh' && s==='中文'))
          out.push({element:e.id||e.tagName,text:s});
      }
      for(const e of document.querySelectorAll('[placeholder],[aria-label],[title],input')){
        if(!visible(e))continue;
        for(const a of ['placeholder','aria-label','title']){
          const s=e.getAttribute(a);if(s && cjk.test(s))out.push({element:e.id||e.tagName,attribute:a,text:s});
        }
        if(e.tagName==='INPUT' && cjk.test(e.value))out.push({element:e.id,attribute:'value',text:e.value});
      }
      return out;
    })()""")
    expect(not findings, label + ": no visible CJK outside the Chinese language button", findings)
    missing = value("WeSwitchI18n.missingKeys")
    expect(not missing, label + ": exercised known strings have no missingKeys", missing)


def storage_check(label, token):
    stored = value("({local:Object.fromEntries(Object.entries(localStorage)),session:Object.fromEntries(Object.entries(sessionStorage))})")
    expect(set(stored["local"]) <= {"weswitch.language"} and not stored["session"],
           label + ": only the language preference may persist", list(stored["local"]))
    expect(stored["local"].get("weswitch.language") in (None, "en", "zh-CN"),
           label + ": language preference has an allowed value")
    encoded = json.dumps(stored)
    expect(all(secret not in encoded for secret in (token, TEST_KEY, REPLACEMENT_KEY)),
           label + ": neither API keys nor the session token persist")


def toggle(language, upstream, store, label):
    before_requests = value("window.__requests.length")
    before_upstream = len(upstream.records)
    before_plans = copy.deepcopy(store.plans)
    before_config = store.path.read_bytes()
    before_keys = copy.deepcopy(store.keychain.items)
    js("""window.__preserved=()=>{
      const ids=['name','provider_id','base_url','model','auth_mode','api_key','env_key',
        'reasoning_effort','set_default','model-search','model-select','confirmation'];
      return {fields:ids.map(id=>{const e=document.getElementById(id);return [id,e.value,e.checked,e.disabled,e.type];}),
        panels:['models-panel','review-panel','preview-content','preview-stale','success-panel'].map(id=>[id,document.getElementById(id).hidden]),
        raw:['preview-snippet','preview-files','preview-provider','preview-model','preview-revision'].map(id=>[id,document.getElementById(id).textContent]),
        applyDisabled:document.getElementById('apply-button').disabled,
        workflow:document.getElementById('workflow').dataset.step};
    };window.__beforeSwitch=JSON.stringify(window.__preserved());
    window.__controlNodes=[...document.querySelectorAll('#provider-form input,#provider-form select,#confirmation')];true""")
    click("language-en" if language == "en" else "language-zh")
    wait_for("document.documentElement.lang === " + json.dumps(language))
    check("WeSwitchI18n.language === " + json.dumps(language) +
          " && document.title === 'WeSwitch' && document.querySelector('[data-locale=" + language +
          "]').getAttribute('aria-pressed') === 'true'", label + ": locale, title and pressed state")
    check("JSON.stringify(window.__preserved())===window.__beforeSwitch && window.__controlNodes.every(e=>e.isConnected && document.getElementById(e.id)===e)",
          label + ": values, selections, controls, preview and confirmation preserved")
    expect(value("window.__requests.length") == before_requests and len(upstream.records) == before_upstream,
           label + ": no API request or upstream GET was triggered")
    expect(store.plans == before_plans and store.path.read_bytes() == before_config and store.keychain.items == before_keys,
           label + ": plans, config and fake credentials unchanged")


def latest_response(path, language, status, label):
    record = value("window.__requests.filter(r=>r.path===" + json.dumps(path) + ").at(-1)")
    expect(record is not None and record.get("status") == status and record["language"] == language
           and record["tokenPresent"] and record["method"] == ("GET" if path == "/api/state" else "POST"),
           label + ": real HTTP response and locale/session headers", record and {
               key: record.get(key) for key in ("path", "status", "language", "method", "tokenPresent")})
    return record["data"] if record and "data" in record else {}


def main():
    global PASSED
    PASSED = 0
    FAILURES.clear()
    finished = False
    with tempfile.TemporaryDirectory(prefix="codex-ui-browser-", dir="/tmp") as temp:
        home = Path(temp)
        initial = b'# Keep this comment\nmodel = "before"\n[mcp_servers.example]\ncommand = "example"\n'
        config = home / "config.toml"
        config.write_bytes(initial)
        store = ConfigStore(home, keychain=FakeKeychain(), demo=True)
        # Do not read metadata from a real installed application, even for screenshots.
        store.app_info = lambda: {"path": str(home / "TEST-ONLY-Codex.app"), "version": "TEST ONLY", "cli_version": "TEST ONLY"}
        server = LocalServer(("127.0.0.1", 0), store)
        gate = threading.Event()

        def respond(handler):
            if handler.path == "/v1/models":
                if not gate.wait(30):
                    raise AssertionError("Test did not release the discovery gate")
                reply(handler, {"data": [{"id": "test-model"}, {"id": "another-model"},
                                        {"id": "test-model"}, {"id": 123}], "has_more": True})
            elif handler.path == "/empty/models":
                reply(handler, {"data": []})
            else:
                reply(handler, {"error": "private-upstream-message"}, status=404)

        upstream = UpstreamServer(respond)
        original_connect = socket.socket.connect

        def owned_connect(sock, address):
            if not isinstance(address, tuple) or address[:2] not in {
                ("127.0.0.1", server.server_port), ("127.0.0.1", upstream.server_port),
            }:
                raise AssertionError("Browser tests may contact only their owned loopback servers")
            return original_connect(sock, address)

        worker = threading.Thread(target=server.serve_forever, daemon=True)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        worker.start()
        upstream_thread.start()
        try:
            with mock.patch.object(socket.socket, "connect", owned_connect):
                browser("open", server.origin + "/#token=" + server.token)
                browser("snapshot", "-i")
                # Remove only our preference, then reload with a fresh fragment: never clear unrelated storage.
                js("localStorage.removeItem('weswitch.language');true")
                # A fragment-only navigation does not reload or fire a load event.
                browser("open", "about:blank")
                browser("open", server.origin + "/#token=" + server.token)
                browser("snapshot", "-i")
                browser("set", "viewport", "1440", "1000")
                wait_for("!document.getElementById('preview-button').disabled")
                check("document.documentElement.lang==='zh-CN' && WeSwitchI18n.language==='zh-CN' && document.title==='WeSwitch'",
                      "Chinese is the default after removing only our preference")
                check("!location.hash && document.getElementById('error-banner').hidden", "Token fragment removed and initial state ready")
                texts({"workspace-title": "为 Codex 接入你的模型", "form-title": "提供方配置", "preview-label": "预览配置更改"},
                      "Default Chinese heading and static form")
                storage_check("Initial isolated browser", server.token)
                # Observe actual fetches; do not replace responses or call the backend directly.
                js("""window.__requests=[];window.__fetch=window.fetch;
                  window.fetch=async(input,init={})=>{
                    const h=new Headers(init.headers),url=new URL(input,location.href);
                    const body=init.body?JSON.parse(init.body):null;
                    const r={path:url.pathname,method:init.method||'GET',language:h.get('X-WeSwitch-Language'),
                      tokenPresent:!!h.get('X-Codex-UI-Token'),bodyHasKey:!!body?.api_key};
                    window.__requests.push(r);
                    const response=await window.__fetch(input,init);
                    r.status=response.status;r.data=await response.clone().json();return response;
                  };window.__confirmations=[];window.__consent=false;
                  window.confirm=s=>{window.__confirmations.push(s);return window.__consent;};true""")
                click("refresh-button")
                wait_for("!document.getElementById('refresh-button').disabled")
                state = latest_response("/api/state", "zh-CN", 200, "Chinese state")
                expect(bool(state.get("warnings")) and any(CJK.search(s) for s in state["warnings"]), "Actual Chinese backend state warnings exercised")
                click("preview-button")
                texts({"name-error": "请填写提供方名称。", "model-error": "请填写提供方实际支持的模型 ID。",
                       "api_key-error": "此提供方没有可复用的受管理凭据，请填写 API Key。"}, "Chinese required-field errors")
                check("!document.getElementById('base_url-error').hidden && document.getElementById('name').getAttribute('aria-invalid')==='true'", "Chinese errors visibly mark invalid fields")
                toggle("en", upstream, store, "Switch existing Chinese errors to English")
                texts({"workspace-title": "Bring your models to Codex", "form-title": "Provider configuration",
                       "preview-label": "Preview changes", "name-error": "Enter a provider name.",
                       "model-error": "Enter a model ID that your provider actually supports.",
                       "api_key-error": "This provider has no reusable managed credentials. Enter an API key."},
                      "English headings, static form and existing dynamic errors")
                check("document.getElementById('name').placeholder==='e.g. My model service' && document.getElementById('language-zh').getAttribute('aria-label')==='Switch to Chinese'", "English placeholders and language-button accessibility")
                check("window.__requests.length===1", "Missing fields never submit a preview")
                audit_english("English with existing Chinese state warnings")
                # Explicit refresh obtains English backend messages, separate from a locale toggle.
                click("refresh-button")
                wait_for("!document.getElementById('refresh-button').disabled")
                state = latest_response("/api/state", "en", 200, "English state")
                expect(bool(state.get("warnings")) and not CJK.search(json.dumps(state["warnings"], ensure_ascii=False)), "Actual English state warning response")
                audit_english("English refreshed state")
                click("new-provider")
                click("preview-button")
                texts({"name-error": "Enter a provider name.", "model-error": "Enter a model ID that your provider actually supports."}, "Newly generated English required-field errors")
                fill({"auth_mode": "env"})
                click("preview-button")
                texts({"env_key-error": "Enter a valid environment variable name, such as OPENAI_API_KEY, not the secret value."}, "English environment-variable validation")
                fill({"name": "TEST ONLY Browser", "provider_id": "browser-test", "base_url": upstream.origin + "/v1",
                      "model": "invalid model", "auth_mode": "none"})
                click("preview-button")
                wait_for("!document.getElementById('error-banner').hidden && !document.getElementById('preview-button').disabled")
                error = latest_response("/api/preview", "en", 400, "Real backend model validation error")
                expect(error.get("error") == "The model ID is empty, too long, or contains whitespace; enter the exact ID supplied by the provider.", "Backend error is actually English", error)
                texts({"error-message": error.get("error", "MISSING ERROR")}, "DOM displays the real backend validation error")
                audit_english("English backend error")
                expect(config.read_bytes() == initial and not store.plans and not store.keychain.items, "Validation errors do not write or retain plans")

                click("new-provider")
                fill({"provider_id": "browser-test", "base_url": upstream.origin + "/v1", "auth_mode": "keychain", "api_key": TEST_KEY})
                toggle("zh-CN", upstream, store, "Chinese discovery consent")
                click("fetch-models")
                check("window.__confirmations.at(-1)===" + json.dumps("将使用你的凭据向 " + upstream.origin + "/v1/models 读取模型列表，不保存密钥、不修改配置、不调用模型生成。是否继续？"), "Exact Chinese credential consent dialog")
                expect(not upstream.records, "Cancelled Chinese consent sends no GET")
                toggle("en", upstream, store, "English discovery consent")
                click("fetch-models")
                check("window.__confirmations.at(-1)===" + json.dumps("Use your credentials to fetch the model list from " + upstream.origin + "/v1/models? No keys will be saved, no configuration changed, and no model generation called. Continue?"), "Exact English credential consent dialog")
                check("document.getElementById('models-status').textContent.startsWith('Cancelled.')", "English cancellation status")
                fill({"auth_mode": "none"})
                click("fetch-models")
                check("window.__confirmations.at(-1)===" + json.dumps("Fetch the model list from " + upstream.origin + "/v1/models without authentication credentials? No keys will be saved, no configuration changed, and no model generation called. Continue?"), "Exact English no-auth consent dialog")
                expect(not upstream.records, "All cancelled dialogs send no GET")
                fill({"auth_mode": "keychain", "api_key": TEST_KEY})
                toggle("zh-CN", upstream, store, "Start Chinese discovery")
                js("window.__consent=true;document.getElementById('fetch-models').click();true")
                wait_for("document.getElementById('fetch-models').disabled")
                expect(upstream.arrived.wait(5), "Confirmed discovery reaches the owned upstream")
                toggle("en", upstream, store, "Switch while discovery is in flight")
                texts({"fetch-models-label": "Fetching…", "models-status": "Fetching models… No credentials or configuration will be saved, and no model generation will be called."}, "English in-flight discovery labels")
                toggle("zh-CN", upstream, store, "Restore Chinese before discovery response")
                gate.set()
                wait_for("!document.getElementById('models-panel').hidden && !document.getElementById('fetch-models').disabled")
                discovery = latest_response("/api/models", "zh-CN", 200, "Chinese discovery response")
                expect(discovery.get("count") == 2 and discovery.get("complete") is False and len(discovery.get("warnings", [])) == 3,
                       "Discovery deduplicates, skips invalid records and reports pagination warnings")
                expect(len(upstream.records) == 1 and upstream.records[0]["method"] == "GET" and upstream.records[0]["path"] == "/v1/models"
                       and upstream.records[0]["headers"].get("authorization") == ["Bearer " + TEST_KEY], "Only one authenticated GET reaches the owned upstream")
                check("document.getElementById('name').value==='' && document.getElementById('model').value==='' && document.getElementById('model-select').selectedOptions.length===0 && document.getElementById('model-select').options.length===2", "Discovery needs no name/model and never selects a model automatically")
                js("const s=document.getElementById('model-search');s.value='another';s.dispatchEvent(new Event('input',{bubbles:true}));true")
                check("document.getElementById('model-select').options.length===1", "Local search filters the model list")
                js("(()=>{const s=document.getElementById('model-search');s.value='';s.dispatchEvent(new Event('input',{bubbles:true}));})();true")
                js("(()=>{const list=document.getElementById('model-select');Array.from(list.options).find(o=>o.value==='test-model').selected=true;list.dispatchEvent(new Event('change',{bubbles:true}));})();true")
                js("document.getElementById('add-selected-models').click();true")
                check("document.getElementById('model').value==='test-model' && document.getElementById('apply-button').disabled", "Adding a discovered model fills the batch list without enabling apply")
                check("document.getElementById('default_model').options.length===2 && document.getElementById('default_model').value===''", "Default model picker offers the added model")
                js("(()=>{const list=document.getElementById('model-select');Array.from(list.options).forEach(o=>o.selected=true);list.dispatchEvent(new Event('change',{bubbles:true}));document.getElementById('add-selected-models').click();})();true")
                check("document.getElementById('model').value.split(String.fromCharCode(10)).join()==='test-model,another-model'", "Several discovered models are added in one step under the same provider")
                js("document.getElementById('model').value='test-model';document.getElementById('model').dispatchEvent(new Event('input',{bubbles:true}));true")
                toggle("en", upstream, store, "Switch selected discovery result")
                texts({"models-filter-status": "Showing 2 / 2 model IDs"}, "English dynamic discovery count")
                expected_warnings = [translate_text(s, "en") for s in discovery.get("warnings", [])]
                actual_warnings = value("[...document.querySelectorAll('#models-warnings li')].map(e=>e.textContent)")
                expect(actual_warnings == expected_warnings, "Known Chinese discovery warnings translate in place", actual_warnings)
                audit_english("English selected discovery result")
                expect(config.read_bytes() == initial and not store.plans and not store.keychain.items, "Discovery and locale switches leave config, plans and credentials untouched")
                storage_check("Populated discovery key", server.token)

                toggle("zh-CN", upstream, store, "Prepare existing Chinese preview")
                fill({"name": "TEST ONLY Browser", "reasoning_effort": "high"})
                click("preview-button")
                wait_for("!document.getElementById('preview-content').hidden && !document.getElementById('preview-button').disabled")
                preview = latest_response("/api/preview", "zh-CN", 200, "Chinese keychain preview")
                check("!window.__requests.filter(r=>r.path==='/api/preview').at(-1).bodyHasKey && document.getElementById('api_key').value===" + json.dumps(TEST_KEY), "Preview omits the key from HTTP but preserves it in the field")
                check("document.getElementById('apply-button').disabled && !document.getElementById('confirmation').checked", "Preview requires explicit confirmation")
                expect(config.read_bytes() == initial and not store.keychain.items and bool(store.plans), "Preview creates only an in-memory plan")
                browser("check", "#confirmation")
                check("!document.getElementById('apply-button').disabled", "Checking confirmation enables apply")
                toggle("en", upstream, store, "Switch confirmed Chinese preview to English")
                for field in ("changes", "warnings"):
                    actual = value("[...document.querySelectorAll('#preview-" + field + " li')].map(e=>e.textContent)")
                    expect(actual == [translate_text(s, "en") for s in preview.get(field, [])],
                           "Known Chinese preview " + field + " translate without a new preview", actual)
                check("document.querySelector('label[for=confirmation]').textContent.trim()==='I have reviewed the file paths, configuration changes, and notices, saved my work, and confirm applying this change.'", "English apply confirmation string")
                audit_english("English confirmed Chinese preview")
                check("document.documentElement.scrollWidth<=innerWidth && document.getElementById('api_key').type==='password' && !location.hash", "English full-page capture has no horizontal overflow or exposed key/token")
                browser("screenshot", "--full", str(Path(__file__).parent / "ui-weswitch-en.png"))
                toggle("zh-CN", upstream, store, "Return confirmed preview to Chinese")
                check("document.querySelector('label[for=confirmation]').textContent.trim()==='我已核对上述文件路径、配置差异与提示，已保存工作，确认应用本次更改。'", "Chinese apply confirmation string restored")
                check("document.documentElement.scrollWidth<=innerWidth", "Chinese layout has no horizontal overflow")
                browser("screenshot", "--full", str(Path(__file__).parent / "ui-weswitch-zh.png"))
                toggle("en", upstream, store, "English discovery fallback")
                fill({"api_key": REPLACEMENT_KEY})
                check("document.getElementById('models-panel').hidden && document.getElementById('apply-button').disabled && !document.getElementById('confirmation').checked", "Changing credentials invalidates discovery, preview and confirmation")
                fill({"base_url": upstream.origin + "/unsupported"})
                click("fetch-models")
                wait_for("!document.getElementById('fetch-models').disabled && document.getElementById('models-status').dataset.tone==='warning'")
                error = latest_response("/api/models", "en", 502, "Real upstream HTTP failure")
                expect(error.get("code") == "models_http" and "does not support" in error.get("error", "") and not CJK.search(error.get("error", "")), "Sanitized actual backend HTTP error is English", error)
                check("document.getElementById('models-status').textContent.includes('does not support') && document.getElementById('models-status').textContent.includes('manually') && !document.getElementById('preview-button').disabled && document.getElementById('model').value==='test-model'", "Unsupported endpoint preserves manual fallback")
                check("!document.body.textContent.includes('private-upstream-message')", "Private upstream error body is never displayed")
                audit_english("English HTTP error and manual fallback")
                fill({"base_url": upstream.origin + "/empty"})
                click("fetch-models")
                wait_for("!document.getElementById('fetch-models').disabled && document.getElementById('models-status').textContent.includes('No model IDs')")
                empty = latest_response("/api/models", "en", 200, "Empty English discovery response")
                expect(empty.get("models") == [] and bool(empty.get("warnings")) and not CJK.search(json.dumps(empty["warnings"], ensure_ascii=False)), "Empty-list warnings are returned in English")
                check("document.getElementById('models-panel').hidden && document.getElementById('model').value==='test-model'", "Empty list preserves manual model entry")
                audit_english("English empty discovery")
                expect(config.read_bytes() == initial and not store.keychain.items, "Fallbacks do not write config or credentials")

                # Preserve the original no-auth preview/stale/confirm/apply/backup workflow.
                click("new-provider")
                fill({"name": "TEST ONLY Browser", "provider_id": "browser-test", "base_url": upstream.origin + "/unsupported",
                      "model": "test-model\ntest-model-two", "auth_mode": "none"})
                click("preview-button")
                wait_for("!document.getElementById('preview-content').hidden && !document.getElementById('preview-button').disabled")
                latest_response("/api/preview", "en", 200, "Manual fallback preview")
                expect(config.read_bytes() == initial, "Original preview never writes config")
                check("document.getElementById('apply-button').disabled && !document.getElementById('preview-snippet').textContent.includes('api_key')", "Original preview is key-free and gated")
                js("document.getElementById('default_model').value='test-model-two';document.getElementById('default_model').dispatchEvent(new Event('change',{bubbles:true}));true")
                fill({"model": "test-model-two\ntest-model-three"})
                check("document.getElementById('apply-button').disabled && !document.getElementById('preview-stale').hidden", "Editing after preview invalidates confirmation")
                texts({"preview-stale": "The form has changed. Preview and confirm again."}, "English stale-preview error")
                click("preview-button")
                wait_for("!document.getElementById('preview-content').hidden && !document.getElementById('preview-button').disabled")
                browser("check", "#confirmation")
                check("!document.getElementById('apply-button').disabled", "Original re-preview and confirmation enable apply")
                audit_english("English fresh preview before apply")
                click("apply-button")
                wait_for("!document.getElementById('success-panel').hidden && !document.getElementById('refresh-button').disabled")
                applied = latest_response("/api/apply", "en", 200, "Original isolated apply")
                expect(applied.get("ok") is True and applied.get("connection_tested") is False and not CJK.search(applied.get("message", "")), "Actual apply result is English and makes no generation claim")
                data = tomllib.loads(config.read_text())
                expect(data["model"] == "test-model-two" and data["model_provider"] == "browser-test", "Only the disposable default model is updated")
                profiles = data.get("profiles", {})
                expect(profiles.get("browser-test-test-model-two") == {"model": "test-model-two", "model_provider": "browser-test"}
                       and profiles.get("browser-test-test-model-three") == {"model": "test-model-three", "model_provider": "browser-test"},
                       "Every batch model is saved as a switchable profile without repeating the provider")
                expect(data["mcp_servers"]["example"]["command"] == "example" and config.read_bytes().startswith(b"# Keep this comment\n"), "Unrelated MCP config and comment survive apply")
                backups = list((home / "model-ui-backups").glob("*/config.toml"))
                expect(len(backups) == 1 and backups[0].read_bytes() == initial, "Exactly one backup preserves the original config")
                expect(not store.keychain.items and not store.keychain.add_calls, "No real or fake Keychain writes in no-auth apply")
                check("document.getElementById('api_key').value==='' && document.getElementById('api_key').type==='password'", "Secrets are cleared after apply")
                storage_check("After apply", server.token)
                audit_english("English success and saved provider")
                toggle("zh-CN", upstream, store, "Switch completed result to Chinese")
                toggle("en", upstream, store, "Return completed result to English")
                audit_english("English restored completed result")
                requests_before = len(upstream.records)
                browser("open", "about:blank")
                browser("open", server.origin)
                browser("snapshot", "-i")
                check("document.documentElement.lang==='en' && localStorage.getItem('weswitch.language')==='en'", "Only language survives a token-free reload")
                check("document.getElementById('preview-button').disabled && document.getElementById('apply-button').disabled && !location.hash", "Token-free reload never regains write access")
                storage_check("Token-free reload", server.token)
                audit_english("English token gate")
                expect(len(upstream.records) == requests_before == 3 and all(r["method"] == "GET" and r["path"] in ("/v1/models", "/unsupported/models", "/empty/models") for r in upstream.records), "Exactly three consented loopback model GETs; no model generation")
                expect(len(list((home / "model-ui-backups").glob("*/config.toml"))) == 1, "Language switches and token gate never reapply")
                finished = True
        except Exception as error:
            expect(False, "Workflow aborted; subsequent assertions were not executed", str(error))
        finally:
            gate.set()
            try:
                browser("close")
                expect(True, "Isolated browser closed in finally")
            except Exception as error:
                expect(False, "Browser close failed", str(error))
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
                upstream.shutdown()
                upstream.server_close()
                upstream_thread.join(timeout=3)
                expect(not worker.is_alive() and not upstream_thread.is_alive() and not upstream.errors, "Disposable servers stopped without handler errors", upstream.errors)
    print(f"SUMMARY: {PASSED} assertions passed; {len(FAILURES)} failed; workflow {'completed' if finished else 'aborted'}.", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
