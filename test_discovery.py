"""Discovery regressions: disposable /tmp configs, fake credentials, owned loopback only.

Run with the requested interpreter and -B. load_tests deliberately selects only
these classes, never the existing test_config cases imported for fixture reuse.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from unittest import mock
import urllib.error

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_core as core
import keychain as keychain_module
import model_discovery as discovery
import server as server_module
import test_config as existing

SECRET = existing.SECRET
TOKEN = existing.TOKEN
ACCOUNT = "discovery-provider." + "a1" * 12
ENV_KEY = "DISCOVERY_TEST_API_KEY"
GOOD = b'{"data":[{"id":"model-a"}]}'
PRIVATE_BODY = "UPSTREAM_PRIVATE_BODY_MUST_NOT_ESCAPE"


def reply(handler, body=GOOD, status=200, headers=None, framing="length", body_gate=None):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode("utf-8")
    outgoing = {"Content-Type": "application/json", "Connection": "close"}
    if framing == "length":
        outgoing["Content-Length"] = str(len(body))
    elif framing == "chunked":
        outgoing["Transfer-Encoding"] = "chunked"
    outgoing.update(headers or {})
    handler.send_response(status)
    for name, value in outgoing.items():
        handler.send_header(name, value)
    handler.end_headers()
    if body_gate is not None and not body_gate.wait(3):
        raise AssertionError("Test did not release the upstream body gate")
    try:
        if framing == "chunked":
            for start in range(0, len(body), 16384):
                chunk = body[start:start + 16384]
                handler.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            handler.wfile.write(b"0\r\n\r\n")
        else:
            handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        # A size limit or timeout intentionally closes the client early.
        pass


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def dispatch(self):
        # Read the body before responding: a connection test POSTs a real payload and
        # the assertion checks what this tool actually sent.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if 0 < length <= 65536 else b""
        record = {"method": self.command, "path": self.path, "body": body,
                  "headers": {name.lower(): self.headers.get_all(name)
                              for name in self.headers.keys()}}
        with self.server.records_lock:
            self.server.records.append(record)
        self.server.arrived.set()
        self.server.respond(self)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = dispatch


class UpstreamServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, respond):
        super().__init__(("127.0.0.1", 0), UpstreamHandler)
        self.respond = respond
        self.records = []
        self.records_lock = threading.Lock()
        self.arrived = threading.Event()
        self.errors = []
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def handle_error(self, request, client_address):
        self.errors.append(type(sys.exc_info()[1]).__name__)


class DiscoveryFixture(existing.ConfigFixture):
    def setUp(self):
        super().setUp()
        self.allowed_connections = set()
        original_connect = socket.socket.connect
        original_create_connection = socket.create_connection

        def guarded_connect(sock, address):
            if not isinstance(address, tuple) or address[:2] not in self.allowed_connections:
                raise AssertionError("Tests may connect only to their own loopback listeners")
            return original_connect(sock, address)

        def guarded_create_connection(address, *args, **kwargs):
            if address not in self.allowed_connections:
                raise AssertionError("Tests must not resolve or contact external/live endpoints")
            return original_create_connection(address, *args, **kwargs)

        for patcher in (
            mock.patch.dict(discovery.os.environ, {}, clear=True),
            mock.patch.object(socket.socket, "connect", guarded_connect),
            mock.patch.object(socket, "create_connection", guarded_create_connection),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("Real subprocess forbidden")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fake.read = mock.Mock(side_effect=self.read_fake)

    def read_fake(self, account):
        if account not in self.fake.items:
            raise keychain_module.KeychainError("Fake credential missing")
        return self.fake.items[account]

    @contextmanager
    def running(self, httpd):
        address = ("127.0.0.1", httpd.server_port)
        self.allowed_connections.add(address)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        started = False
        try:
            thread.start()
            started = True
            yield httpd
        finally:
            try:
                if started:
                    httpd.shutdown()
            finally:
                httpd.server_close()
                if started:
                    thread.join(timeout=3)
                self.allowed_connections.discard(address)
            self.assertFalse(thread.is_alive(), "Test server did not stop")
            self.assertEqual(getattr(httpd, "errors", []), [], "Unexpected upstream handler error")

    def upstream(self, respond=reply):
        return self.running(UpstreamServer(respond))

    def local(self):
        return self.running(server_module.LocalServer(("127.0.0.1", 0), self.store, token=TOKEN))

    def payload(self, base, **changes):
        payload = {"confirmed": True, "base_url": base, "auth_mode": "keychain", "api_key": SECRET}
        payload.update(changes)
        return payload

    def configure(self, base, **provider):
        config = {"model_providers": {"discovery-provider": dict(base_url=base, **provider)}}
        self.path.write_text(core.tomlkit.dumps(config), encoding="utf-8")

    def managed(self, base, secret=SECRET):
        self.configure(base, auth={"command": "/usr/bin/security", "args": [
            "find-generic-password", "-s", keychain_module.SERVICE, "-a", ACCOUNT, "-w"]})
        self.fake.items[ACCOUNT] = secret

    @contextmanager
    def readonly(self):
        tree = self.tree_snapshot()
        plans = copy.deepcopy(self.store.plans)
        items = copy.deepcopy(self.fake.items)
        exists = list(self.fake.exists_calls)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(core, "atomic_write", side_effect=AssertionError("Discovery wrote a file")), \
                mock.patch.object(self.store, "preview", side_effect=AssertionError("Discovery retained a plan")), \
                mock.patch.object(self.store, "apply", side_effect=AssertionError("Discovery applied config")), \
                mock.patch.object(self.fake, "add", side_effect=AssertionError("Discovery wrote a credential")), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                yield
            finally:
                self.assertEqual(self.tree_snapshot(), tree)
                self.assertEqual(self.store.plans, plans)
                self.assertEqual(self.fake.items, items)
                self.assertEqual(self.fake.exists_calls, exists)
                self.assert_no_secret(out.getvalue(), err.getvalue())
                self.assertNotIn(PRIVATE_BODY, out.getvalue() + err.getvalue())

    def discover(self, payload):
        before = copy.deepcopy(payload)
        with self.readonly():
            result = discovery.discover_models(self.store, payload)
        self.assertEqual(payload, before)
        self.assert_no_secret(result)
        self.assertNotIn(PRIVATE_BODY, json.dumps(result))
        self.assertNotIn("plan_id", result)
        return result

    def denied_before_fetch(self, payload, code="invalid", status=400):
        with mock.patch.object(discovery, "fetch_models") as fetch:
            error = self.assert_error(lambda: self.discover(payload), code, status)
        fetch.assert_not_called()
        return error

    def api(self, httpd, payload=None, *, method="POST", token=TOKEN, headers=None, body=None):
        outgoing = {"Content-Type": "application/json", "Origin": httpd.origin}
        if token is not None:
            outgoing["X-Codex-UI-Token"] = token
        for name, value in (headers or {}).items():
            if value is None:
                outgoing.pop(name, None)
            else:
                outgoing[name] = value
        if payload is not None:
            body = json.dumps(payload).encode()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
        try:
            connection.request(method, "/api/models", body=body, headers=outgoing)
            response = connection.getresponse()
            raw = response.read()
            self.assert_no_secret(raw)
            self.assertNotIn(TOKEN.encode(), raw)
            self.assertNotIn(PRIVATE_BODY.encode(), raw)
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), json.loads(raw)
        finally:
            connection.close()

    def assert_slots_free(self, httpd):
        held = 0
        try:
            for _ in range(2):
                self.assertTrue(httpd.discovery_slots.acquire(blocking=False))
                held += 1
            self.assertFalse(httpd.discovery_slots.acquire(blocking=False))
        finally:
            for _ in range(held):
                httpd.discovery_slots.release()


class ResolutionTests(DiscoveryFixture):
    def test_manual_secret_needs_no_name_model_or_provider_id(self):
        with self.upstream() as upstream, mock.patch.object(self.store, "snapshot") as snapshot:
            result = self.discover(self.payload(upstream.origin + "/v1///"))
            self.assertEqual(result["endpoint"], upstream.origin + "/v1/models")
            self.assertEqual(result["models"], [{"id": "model-a"}])
            self.assertFalse(result["connection_tested"])
            self.assertEqual(upstream.records[0]["path"], "/v1/models")
            snapshot.assert_not_called()
        self.fake.read.assert_not_called()
        self.assertEqual(self.store.plans, {})

    def test_no_auth_sends_no_authorization_and_needs_no_saved_provider(self):
        with self.upstream() as upstream:
            self.discover(self.payload(upstream.origin, auth_mode="none", api_key=""))
            self.assertNotIn("authorization", upstream.records[0]["headers"])
        self.fake.read.assert_not_called()

    def test_consent_must_be_exactly_true_before_config_credentials_or_network(self):
        with mock.patch.object(self.store, "snapshot") as snapshot, \
                mock.patch.object(discovery.os, "environ", {}) as environment:
            for value in (False, None, 0, 1, "true", [], {}):
                with self.subTest(consent=value):
                    self.denied_before_fetch(self.payload("not-a-url", confirmed=value), "confirmation_required")
            self.denied_before_fetch({"base_url": "not-a-url"}, "confirmation_required")
            snapshot.assert_not_called()
            self.assertEqual(environment, {})
        self.fake.read.assert_not_called()

    def test_non_object_payload_rejected_before_network(self):
        for value in (None, [], "text", 42, True):
            with self.subTest(value=value):
                self.denied_before_fetch(value, "confirmation_required")

    def test_non_keychain_mode_rejects_a_supplied_secret(self):
        for mode in ("none", "env", "unsupported"):
            with self.subTest(mode=mode):
                self.denied_before_fetch(self.payload("http://127.0.0.1:1", auth_mode=mode))

    def test_token_whitespace_controls_non_ascii_types_and_oversize_are_rejected(self):
        bad = [None, 4, True, [], {}, "x" * 8193, "has space", " leading", "trailing ",
               "injected\r\nX-Injected:yes", "nul\0key", "tab\tkey", "del\x7fkey", "nonascii-\u00e9"]
        bad.extend("key" + chr(i) + "suffix" for i in range(33))
        for index, secret in enumerate(bad):
            with self.subTest(case=index):
                self.denied_before_fetch(self.payload("http://127.0.0.1:1", api_key=secret))
        self.fake.read.assert_not_called()

    def test_token_boundary_is_8192_printable_ascii_characters(self):
        self.assertEqual(discovery.valid_token("a" * 8192), "a" * 8192)
        self.assertEqual(discovery.valid_token("abc._~+/=-"), "abc._~+/=-")
        self.assert_error(lambda: discovery.valid_token(""))

    def test_url_restrictions_fail_before_credential_read_or_network(self):
        invalid = ["http://gateway.example.invalid/v1", "ftp://127.0.0.1/v1", "file:///tmp/config",
                   "https://user:password@gateway.example.invalid/v1", "https://user@gateway.example.invalid",
                   "https://gateway.example.invalid/v1?key=" + SECRET,
                   "https://gateway.example.invalid/v1#fragment", "//gateway.example.invalid/v1",
                   "http://127.0.0.1:0/v1", "http://127.0.0.1:65536/v1", "http://127.0.0.1:bad/v1",
                   "http://127.1/v1", "http://0.0.0.0/v1", "http://127.0.0.1.example.invalid/v1",
                   "http://[::1", "https://gateway.example.invalid/a/../v1",
                   "https://gateway.example.invalid/a/%2e%2e/v1", "https://gateway.example.invalid/v1\\x",
                   "https://gateway.example.invalid/v1\r\nX-Injected:yes",
                   "https://gateway.example.invalid/v1\x7f", "https://gateway.example.invalid/a b",
                   "https://gateway.example.invalid/" + "x" * 2049]
        invalid.extend("https://gateway.example.invalid/v1/" + part for part in
                       ("%2f", "%5c", "%25", "%00", "%0a", "%0D", "%09"))
        invalid.extend("https://gateway.example.invalid/v1/" + part for part in
                       ("models", "models/", "responses", "chat/completions", "messages"))
        for index, base in enumerate(invalid):
            with self.subTest(case=index):
                self.denied_before_fetch(self.payload(base))
        self.fake.read.assert_not_called()

    def test_authless_remote_https_is_rejected(self):
        self.denied_before_fetch(self.payload("https://gateway.example.invalid/v1", auth_mode="none", api_key=""))

    def test_managed_keychain_reuse_reads_only_the_bound_account(self):
        with self.upstream() as upstream:
            self.managed(upstream.origin + "/v1/")
            result = self.discover(self.payload(upstream.origin + "/v1", api_key="", provider_id="discovery-provider"))
            self.assertEqual(result["count"], 1)
            self.assertEqual(upstream.records[0]["headers"]["authorization"], ["Bearer " + SECRET])
        self.fake.read.assert_called_once_with(ACCOUNT)

    def test_changed_endpoint_blocks_reuse_before_keychain_read(self):
        base = "http://127.0.0.1:32123/v1"
        self.managed(base)
        for changed in (base + "/other", base.replace("32123", "32124"),
                        base.replace("127.0.0.1", "localhost"), base.replace("http:", "https:")):
            with self.subTest(base=changed):
                self.denied_before_fetch(self.payload(changed, api_key="", provider_id="discovery-provider"),
                                         "credential_endpoint_mismatch")
        self.fake.read.assert_not_called()

    def test_arbitrary_helpers_and_altered_security_args_are_never_executed(self):
        base = "http://127.0.0.1:1/v1"
        args = ["find-generic-password", "-s", keychain_module.SERVICE, "-a", ACCOUNT, "-w"]
        helpers = [{}, {"command": "/tmp/untrusted-helper", "args": args},
                   {"command": "/bin/sh", "args": ["-c", "untrusted-command"]},
                   {"command": "/usr/bin/security", "args": args + ["extra"]},
                   {"command": "/usr/bin/security", "args": ["find-generic-password", "-s", "other-service", "-a", ACCOUNT, "-w"]},
                   {"command": "/usr/bin/security", "args": args[:4] + [ACCOUNT + ";injection", "-w"]}]
        for index, auth in enumerate(helpers):
            with self.subTest(case=index):
                self.configure(base, auth=auth)
                self.denied_before_fetch(self.payload(base, api_key="", provider_id="discovery-provider"),
                                         "credential_missing")
        self.fake.read.assert_not_called()
        subprocess.run.assert_not_called()

    def test_keychain_denial_is_sanitized_and_never_contacts_upstream(self):
        base = "http://127.0.0.1:1/v1"
        self.managed(base)
        self.fake.read.side_effect = keychain_module.KeychainError(PRIVATE_BODY + SECRET)
        error = self.denied_before_fetch(self.payload(base, api_key="", provider_id="discovery-provider"),
                                         "credential_missing")
        self.assertNotIn(PRIVATE_BODY, str(error))

    def test_empty_or_invalid_stored_key_is_not_sent(self):
        base = "http://127.0.0.1:1/v1"
        for value in ("", "key\nheader", "key\x7f", "key with space"):
            with self.subTest(value=value):
                self.managed(base, value)
                self.denied_before_fetch(self.payload(base, api_key="", provider_id="discovery-provider"))

    def test_revision_change_during_credential_read_prevents_request(self):
        base = "http://127.0.0.1:1/v1"
        self.managed(base)
        first = self.store.snapshot()
        second = (first[0], "changed-revision", first[2], first[3])
        with mock.patch.object(self.store, "snapshot", side_effect=[first, second]):
            self.denied_before_fetch(self.payload(base, api_key="", provider_id="discovery-provider"), "conflict", 409)
        self.fake.read.assert_called_once_with(ACCOUNT)

    def test_env_reuse_reads_exactly_the_configured_name_for_same_endpoint(self):
        environment = mock.Mock(wraps={ENV_KEY: SECRET, "UNRELATED_SECRET": "must-not-read"})
        with self.upstream() as upstream:
            self.configure(upstream.origin, env_key=ENV_KEY)
            with mock.patch.object(discovery, "os", mock.Mock(environ=environment)):
                self.discover(self.payload(upstream.origin, auth_mode="env", api_key="",
                                           provider_id="discovery-provider", env_key=ENV_KEY))
            self.assertEqual(upstream.records[0]["headers"]["authorization"], ["Bearer " + SECRET])
        self.assertEqual(environment.mock_calls, [mock.call.get(ENV_KEY, "")])
        self.fake.read.assert_not_called()

    def test_arbitrary_env_name_is_rejected_without_any_environment_read(self):
        base = "http://127.0.0.1:1"
        self.configure(base, env_key=ENV_KEY)
        environment = mock.Mock(wraps={"ARBITRARY_SECRET": SECRET})
        with mock.patch.object(discovery, "os", mock.Mock(environ=environment)):
            self.denied_before_fetch(self.payload(base, auth_mode="env", api_key="",
                                     provider_id="discovery-provider", env_key="ARBITRARY_SECRET"), "credential_missing")
        self.assertEqual(environment.mock_calls, [])

    def test_env_wrong_endpoint_or_unknown_provider_never_reads_environment(self):
        base = "http://127.0.0.1:1"
        self.configure(base, env_key=ENV_KEY)
        environment = mock.Mock(wraps={ENV_KEY: SECRET})
        with mock.patch.object(discovery, "os", mock.Mock(environ=environment)):
            for endpoint, provider in ((base + "/changed", "discovery-provider"), (base, "unknown-provider")):
                with self.subTest(provider=provider):
                    self.denied_before_fetch(self.payload(endpoint, auth_mode="env", api_key="",
                                             provider_id=provider, env_key=ENV_KEY), "credential_endpoint_mismatch")
        self.assertEqual(environment.mock_calls, [])

    def test_env_conflicting_auth_is_rejected_before_environment_read(self):
        base = "http://127.0.0.1:1"
        environment = mock.Mock(wraps={ENV_KEY: SECRET})
        for extra in ({"auth": {"command": "untrusted-helper"}}, {"requires_openai_auth": True}):
            with self.subTest(extra=extra), mock.patch.object(discovery, "os", mock.Mock(environ=environment)):
                self.configure(base, env_key=ENV_KEY, **extra)
                self.denied_before_fetch(self.payload(base, auth_mode="env", api_key="",
                                         provider_id="discovery-provider", env_key=ENV_KEY), "credential_missing")
        self.assertEqual(environment.mock_calls, [])

    def test_missing_env_has_clear_error_distinct_from_endpoint_or_auth_errors(self):
        base = "http://127.0.0.1:1"
        self.configure(base, env_key=ENV_KEY)
        for environment in ({}, {ENV_KEY: ""}):
            with self.subTest(empty=bool(environment)), mock.patch.object(discovery, "os", mock.Mock(environ=environment)):
                error = self.denied_before_fetch(self.payload(base, auth_mode="env", api_key="",
                                                 provider_id="discovery-provider", env_key=ENV_KEY), "credential_missing")
                self.assertIn("\u73af\u5883\u53d8\u91cf", str(error))
                self.assertIn("\u624b\u52a8", str(error))
                self.assertNotEqual(str(error), "credential_missing")

    def test_invalid_env_and_provider_names_are_rejected_before_lookup(self):
        base = "http://127.0.0.1:1"
        self.configure(base, env_key=ENV_KEY)
        environment = mock.Mock(wraps={ENV_KEY: SECRET})
        with mock.patch.object(discovery, "os", mock.Mock(environ=environment)):
            for field, values in (("env_key", ("lowercase", "1BAD", "A-B", "A\nB", "A" * 129)),
                                  ("provider_id", ("", "UPPER", "1bad", "x;cmd", "x\n", "x" * 65))):
                for value in values:
                    with self.subTest(field=field, value=value):
                        payload = self.payload(base, auth_mode="env", api_key="",
                                               provider_id="discovery-provider", env_key=ENV_KEY)
                        payload[field] = value
                        self.denied_before_fetch(payload)
        self.assertEqual(environment.mock_calls, [])


class ParsingTests(DiscoveryFixture):
    def parse(self, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        result = discovery.parse_models(raw, SECRET)
        self.assert_no_secret(result)
        return result

    def test_ids_are_deduplicated_and_sorted_by_casefold_then_exact_text(self):
        ids = ["z", "b", "A", "a", "B", "a", "org/model:1", "\u00c9", "\u00e9"]
        result = self.parse({"data": [{"id": value} for value in ids]})
        self.assertEqual([m["id"] for m in result["models"]], ["A", "a", "B", "b", "org/model:1", "z", "\u00c9", "\u00e9"])
        self.assertEqual(result["count"], 8)
        self.assertTrue(result["complete"])
        self.assertFalse(result["connection_tested"])
        self.assertEqual(len(result["warnings"]), 1)

    def test_nonstring_ids_and_nonobject_records_are_skipped_not_stringified(self):
        invalid = [{"id": value} for value in (None, 7, True, [], {}, 1.5)] + [None, 7, "model", []]
        result = self.parse({"data": invalid + [{"id": "valid"}]})
        self.assertEqual(result["models"], [{"id": "valid"}])
        self.assertIn(str(len(invalid)), result["warnings"][1])

    def test_invalid_id_controls_whitespace_empty_and_length_are_filtered(self):
        invalid = ["", "a b", "a\tb", "a\nb", "a\rb", "a\0b", "a\x1fb", "a\x7fb", "a\u00a0b", "x" * 201]
        result = self.parse({"data": [{"id": value} for value in invalid + ["x" * 200]]})
        self.assertEqual(result["models"], [{"id": "x" * 200}])
        self.assertIn(str(len(invalid)), result["warnings"][1])

    def test_all_invalid_records_produce_sanitized_502(self):
        self.assert_error(lambda: self.parse({"data": [{"id": 123}, {"id": SECRET}, {}, None]}), "models_invalid", 502)

    def test_empty_data_is_success_with_warning_and_no_connection_claim(self):
        result = self.parse({"data": []})
        self.assertEqual(result["models"], [])
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["complete"])
        self.assertFalse(result["connection_tested"])
        self.assertEqual(len(result["warnings"]), 2)

    def test_invalid_json_html_and_invalid_utf8_are_sanitized(self):
        bodies = [b"", b"{", ("<html>" + PRIVATE_BODY + SECRET + "</html>").encode(), b"\xff"]
        for index, body in enumerate(bodies):
            with self.subTest(case=index):
                error = self.assert_error(lambda: self.parse(body), "models_invalid", 502)
                self.assertNotIn(PRIVATE_BODY, str(error))

    def test_json_recursion_exception_is_sanitized_independently_of_interpreter_limit(self):
        with mock.patch.object(discovery.json, "loads", side_effect=RecursionError(PRIVATE_BODY + SECRET)):
            error = self.assert_error(lambda: discovery.parse_models(GOOD, SECRET), "models_invalid", 502)
        self.assertNotIn(PRIVATE_BODY, str(error))

    def test_wrong_json_shapes_require_a_top_level_data_array(self):
        for body in (None, [], "text", 7, {}, {"models": []}, {"data": None}, {"data": {}}, {"data": "text"}):
            with self.subTest(body=body):
                self.assert_error(lambda: self.parse(body), "models_unsupported", 502)

    def test_secret_containing_ids_and_untrusted_metadata_never_reach_output(self):
        body = {"data": [{"id": SECRET}, {"id": "prefix-" + SECRET},
                         {"id": "safe", "owned_by": SECRET, "extra": PRIVATE_BODY}],
                "error": SECRET, "metadata": PRIVATE_BODY}
        result = self.parse(body)
        self.assertEqual(result["models"], [{"id": "safe"}])
        self.assertNotIn(PRIVATE_BODY, json.dumps(result))
        self.assertEqual(set(result), {"models", "count", "warnings", "complete", "connection_tested"})

    def test_model_count_limit_is_checked_before_deduplication(self):
        body = {"data": [{"id": "same"}] * discovery.MAX_MODELS}
        self.assertEqual(self.parse(body)["count"], 1)
        body["data"].append({"id": "same"})
        self.assert_error(lambda: self.parse(body), "models_too_large", 502)

    def test_raw_byte_limit_accepts_boundary_but_rejects_one_extra_byte(self):
        raw = GOOD + b" " * (discovery.MAX_BYTES - len(GOOD))
        self.assertEqual(self.parse(raw)["count"], 1)
        self.assert_error(lambda: self.parse(raw + b" "), "models_too_large", 502)

    def test_pagination_flags_mark_incomplete_without_echoing_page_urls(self):
        for extra in ({"has_more": True}, {"next": "https://page.example.invalid/" + SECRET},
                      {"next_page": "https://page.example.invalid/" + SECRET}):
            with self.subTest(field=next(iter(extra))):
                result = self.parse({"data": [{"id": "safe"}], **extra})
                self.assertFalse(result["complete"])
                self.assertEqual(len(result["warnings"]), 2)
        self.assertTrue(self.parse({"data": [], "has_more": False, "next": None, "next_page": ""})["complete"])


class FetchTests(DiscoveryFixture):
    def test_real_get_exact_authorization_only_requested_target_and_no_ambient_proxy(self):
        with self.upstream() as decoy, self.upstream() as upstream:
            environment = {"http_proxy": decoy.origin, "https_proxy": decoy.origin,
                           "HTTP_PROXY": decoy.origin, "HTTPS_PROXY": decoy.origin,
                           "ALL_PROXY": decoy.origin, "NO_PROXY": "", "no_proxy": ""}
            with mock.patch.dict(discovery.os.environ, environment, clear=True):
                self.discover(self.payload(upstream.origin + "/v1"))
            self.assertEqual(len(upstream.records), 1)
            record = upstream.records[0]
            self.assertEqual((record["method"], record["path"]), ("GET", "/v1/models"))
            self.assertEqual(record["headers"]["authorization"], ["Bearer " + SECRET])
            self.assertEqual(record["headers"]["accept"], ["application/json"])
            self.assertEqual(record["headers"]["accept-encoding"], ["identity"])
            for name in ("cookie", "proxy-authorization", "x-codex-ui-token"):
                self.assertNotIn(name, record["headers"])
            self.assertEqual(decoy.records, [])

    def test_success_html_and_malformed_json_bodies_are_never_echoed(self):
        for body in (("<html>" + PRIVATE_BODY + SECRET + "</html>").encode(), ("{" + PRIVATE_BODY + SECRET).encode()):
            with self.subTest(html=body.startswith(b"<")), self.upstream(
                    lambda h: reply(h, body, headers={"Content-Type": "text/html"})) as upstream:
                error = self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_invalid", 502)
                self.assertNotIn(PRIVATE_BODY, str(error))

    def test_upstream_error_statuses_never_echo_body_or_become_local_401(self):
        for status in (401, 403, 404, 405, 429, 500, 501, 503):
            with self.subTest(status=status), self.upstream(
                    lambda h: reply(h, (PRIVATE_BODY + SECRET).encode(), status)) as upstream:
                error = self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_http", 502)
                self.assertNotIn(PRIVATE_BODY, str(error))
                self.assertEqual(len(upstream.records), 1)

    def test_http_error_body_is_closed_without_ever_being_read(self):
        stream = mock.Mock()
        stream.read.side_effect = AssertionError("Read an upstream error body")
        error = urllib.error.HTTPError("http://127.0.0.1:1/models", 401, "Unauthorized", {}, stream)
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(discovery.urllib.request, "build_opener", return_value=opener):
            self.assert_error(lambda: discovery.fetch_models("http://127.0.0.1:1/models", SECRET), "models_http", 502)
        stream.read.assert_not_called()
        stream.close.assert_called_once()

    def test_non_200_success_status_is_not_accepted_as_model_list(self):
        with self.upstream(lambda h: reply(h, b"", 204)) as upstream:
            self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_http", 502)

    def test_unsupported_response_compression_is_rejected(self):
        for encoding in ("gzip", "br", "deflate"):
            with self.subTest(encoding=encoding), self.upstream(
                    lambda h: reply(h, headers={"Content-Encoding": encoding})) as upstream:
                self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_encoding", 502)

    def test_declared_oversize_length_rejected_without_waiting_for_body(self):
        with self.upstream(lambda h: reply(h, b"", headers={"Content-Length": str(discovery.MAX_BYTES + 1)})) as upstream:
            self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_too_large", 502)

    def test_negative_or_nonnumeric_declared_length_is_rejected(self):
        for length in ("-1", "not-a-number"):
            with self.subTest(length=length), self.upstream(
                    lambda h: reply(h, b"", headers={"Content-Length": length})) as upstream:
                self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_too_large", 502)

    def test_chunked_body_over_limit_is_rejected_even_without_content_length(self):
        raw = b"x" * (discovery.MAX_BYTES + 1)
        with self.upstream(lambda h: reply(h, raw, framing="chunked")) as upstream:
            self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_too_large", 502)

    def test_close_delimited_stream_over_limit_is_rejected(self):
        raw = b"x" * (discovery.MAX_BYTES + 1)
        with self.upstream(lambda h: reply(h, raw, framing="close")) as upstream:
            self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_too_large", 502)

    def test_chunked_valid_json_at_byte_limit_is_accepted(self):
        raw = GOOD + b" " * (discovery.MAX_BYTES - len(GOOD))
        with self.upstream(lambda h: reply(h, raw, framing="chunked")) as upstream:
            self.assertEqual(self.discover(self.payload(upstream.origin))["count"], 1)

    def test_redirects_to_second_server_never_deliver_any_request_or_credential(self):
        with self.upstream() as destination:
            for status in (301, 302, 303, 307, 308):
                with self.subTest(status=status), self.upstream(lambda h: reply(
                        h, (PRIVATE_BODY + SECRET).encode(), status,
                        {"Location": destination.origin + "/capture"})) as upstream:
                    self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_http", 502)
                    self.assertEqual(len(upstream.records), 1)
                    self.assertEqual(upstream.records[0]["headers"]["authorization"], ["Bearer " + SECRET])
                    self.assertEqual(destination.records, [])

    def test_same_origin_relative_redirect_is_never_followed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), self.upstream(
                    lambda h: reply(h, b"", status, {"Location": "/capture"})) as upstream:
                self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_http", 502)
                self.assertEqual([record["path"] for record in upstream.records], ["/models"])

    def test_pagination_never_requests_another_page_or_sends_credentials_there(self):
        with self.upstream() as destination:
            for field in ("next", "next_page"):
                with self.subTest(field=field), self.upstream(lambda h: reply(h, {
                        "data": [{"id": "page-one"}], field: destination.origin + "/page-two"})) as upstream:
                    result = self.discover(self.payload(upstream.origin))
                    self.assertFalse(result["complete"])
                    self.assertEqual(result["models"], [{"id": "page-one"}])
                    self.assertEqual(len(upstream.records), 1)
                    self.assertEqual(destination.records, [])

    def test_header_timeout_uses_small_patched_timeout_without_sleep(self):
        gate = threading.Event()

        def stalled(handler):
            if not gate.wait(3):
                raise AssertionError("Header timeout gate not released")
            reply(handler)

        with self.upstream(stalled) as upstream, mock.patch.object(discovery, "REQUEST_TIMEOUT", 0.05):
            try:
                self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_timeout", 504)
                self.assertTrue(upstream.arrived.is_set())
            finally:
                gate.set()

    def test_body_timeout_uses_small_patched_timeout_and_releases_server(self):
        gate = threading.Event()
        with self.upstream(lambda h: reply(h, body_gate=gate)) as upstream, \
                mock.patch.object(discovery, "REQUEST_TIMEOUT", 0.05):
            try:
                self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_timeout", 504)
            finally:
                gate.set()

    def test_body_deadline_is_enforced_after_read_even_when_socket_did_not_timeout(self):
        with self.upstream() as upstream, mock.patch.object(discovery, "BODY_DEADLINE", 0.01), \
                mock.patch.object(discovery.time, "monotonic", side_effect=[0.0, 0.0, 0.02]):
            self.assert_error(lambda: self.discover(self.payload(upstream.origin)), "models_timeout", 504)

    def test_network_exception_details_and_fake_secret_are_sanitized(self):
        opener = mock.Mock()
        for error in (urllib.error.URLError(PRIVATE_BODY + SECRET), OSError(PRIVATE_BODY + SECRET),
                      http.client.HTTPException(PRIVATE_BODY + SECRET), ValueError(PRIVATE_BODY + SECRET)):
            with self.subTest(kind=type(error).__name__), \
                    mock.patch.object(discovery.urllib.request, "build_opener", return_value=opener):
                opener.open.side_effect = error
                caught = self.assert_error(lambda: discovery.fetch_models("http://127.0.0.1:1/models", SECRET),
                                           "models_network", 502)
                self.assertNotIn(PRIVATE_BODY, str(caught))


class LocalModelsHTTPTests(DiscoveryFixture):
    def test_success_has_session_protections_security_headers_and_zero_mutation(self):
        with self.upstream() as upstream, self.local() as local, self.readonly():
            status, headers, result = self.api(local, self.payload(upstream.origin))
            self.assertEqual(status, 200)
            self.assertEqual(result["models"], [{"id": "model-a"}])
            self.assertEqual(result["endpoint"], upstream.origin + "/models")
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(headers["x-content-type-options"], "nosniff")
            self.assertEqual(headers["referrer-policy"], "no-referrer")
            self.assertEqual(headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
            self.assertFalse(any(name.startswith("access-control-") for name in headers))
            self.assertEqual(self.store.plans, {})
            self.assert_slots_free(local)

    def test_wrong_host_is_rejected_before_discovery_even_with_session_token(self):
        with self.local() as local, mock.patch.object(server_module, "discover_models") as discover:
            for host in ("external.example.invalid", f"localhost:{local.server_port}", "127.0.0.1:1"):
                with self.subTest(host=host):
                    status, _, data = self.api(local, self.payload("http://127.0.0.1:1"), headers={"Host": host})
                    self.assertEqual((status, data["code"]), (403, "host_rejected"))
            discover.assert_not_called()

    def test_foreign_origin_is_rejected_before_discovery(self):
        with self.local() as local, mock.patch.object(server_module, "discover_models") as discover:
            for origin in ("https://external.example.invalid", "null", "http://127.0.0.1:1"):
                with self.subTest(origin=origin):
                    status, _, data = self.api(local, self.payload("http://127.0.0.1:1"), headers={"Origin": origin})
                    self.assertEqual((status, data["code"]), (403, "origin_rejected"))
            discover.assert_not_called()

    def test_cross_site_fetch_is_rejected_with_otherwise_valid_headers(self):
        with self.local() as local, mock.patch.object(server_module, "discover_models") as discover:
            status, _, data = self.api(local, self.payload("http://127.0.0.1:1"), headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual((status, data["code"]), (403, "cross_site_rejected"))
            discover.assert_not_called()

    def test_missing_wrong_and_non_ascii_session_tokens_are_local_401(self):
        with self.local() as local, mock.patch.object(server_module, "discover_models") as discover:
            for token in (None, "", "wrong", "\u00e9"):
                with self.subTest(token=token):
                    status, _, data = self.api(local, self.payload("http://127.0.0.1:1"), token=token)
                    self.assertEqual((status, data["code"]), (401, "unauthorized"))
            discover.assert_not_called()

    def test_get_models_does_not_trigger_upstream_discovery(self):
        with self.local() as local, mock.patch.object(server_module, "discover_models") as discover:
            status, _, data = self.api(local, method="GET")
            self.assertEqual((status, data["code"]), (404, "not_found"))
            discover.assert_not_called()

    def test_http_false_consent_is_rejected_before_network_without_retaining_plan(self):
        with self.upstream() as upstream, self.local() as local, self.readonly():
            status, _, data = self.api(local, self.payload(upstream.origin, confirmed=False))
            self.assertEqual((status, data["code"]), (400, "confirmation_required"))
            self.assertEqual(upstream.records, [])
            self.fake.read.assert_not_called()
            self.assert_slots_free(local)

    def test_upstream_401_403_are_502_and_do_not_expire_local_session(self):
        for upstream_status in (401, 403):
            with self.subTest(status=upstream_status), self.upstream(lambda h: reply(
                    h, (PRIVATE_BODY + SECRET).encode(), upstream_status)) as upstream, \
                    self.local() as local, self.readonly():
                status, _, data = self.api(local, self.payload(upstream.origin))
                self.assertEqual((status, data["code"]), (502, "models_http"))
                self.assert_slots_free(local)
                upstream.respond = reply
                status, _, data = self.api(local, self.payload(upstream.origin))
                self.assertEqual((status, data["count"]), (200, 1))

    def test_unexpected_exception_is_sanitized_and_both_slots_are_released(self):
        with self.upstream() as upstream, self.local() as local, self.readonly():
            with mock.patch.object(server_module, "discover_models", side_effect=RuntimeError(PRIVATE_BODY + SECRET)):
                for _ in range(3):
                    status, _, data = self.api(local, self.payload(upstream.origin))
                    self.assertEqual((status, data["code"]), (500, "operation_failed"))
                    self.assert_slots_free(local)
            self.assertEqual(upstream.records, [])
            self.assertEqual(self.api(local, self.payload(upstream.origin))[0], 200)

    def test_config_error_releases_slots_and_following_request_succeeds(self):
        with self.upstream() as upstream, self.local() as local, self.readonly():
            with mock.patch.object(server_module, "discover_models", side_effect=core.ConfigError(
                    "Synthetic timeout", "models_timeout", 504)):
                for _ in range(3):
                    status, _, data = self.api(local, self.payload(upstream.origin))
                    self.assertEqual((status, data["code"]), (504, "models_timeout"))
                    self.assert_slots_free(local)
            self.assertEqual(self.api(local, self.payload(upstream.origin))[0], 200)

    def test_two_real_concurrent_requests_fill_slots_and_third_is_429_then_recover(self):
        gate, both_arrived = threading.Event(), threading.Event()
        lock = threading.Lock()
        arrivals = 0

        def blocked(handler):
            nonlocal arrivals
            with lock:
                arrivals += 1
                if arrivals == 2:
                    both_arrived.set()
            reply(handler, body_gate=gate)

        with self.upstream(blocked) as upstream, self.local() as local, self.readonly(), \
                ThreadPoolExecutor(max_workers=2) as pool:
            futures = []
            try:
                futures = [pool.submit(self.api, local, self.payload(upstream.origin)) for _ in range(2)]
                self.assertTrue(both_arrived.wait(2), "Both requests did not reach the owned upstream")
                status, _, data = self.api(local, self.payload(upstream.origin))
                self.assertEqual((status, data["code"]), (429, "models_busy"))
                self.assertEqual(len(upstream.records), 2)
            finally:
                gate.set()
                results = [future.result(timeout=3) for future in futures]
            self.assertEqual([result[0] for result in results], [200, 200])
            self.assert_slots_free(local)
            self.assertEqual(self.api(local, self.payload(upstream.origin))[0], 200)
            self.assertEqual(len(upstream.records), 3)

    def test_local_session_cookie_and_injected_authorization_are_not_forwarded(self):
        with self.upstream() as upstream, self.local() as local, self.readonly():
            status, _, _ = self.api(local, self.payload(upstream.origin), headers={
                "Authorization": "Bearer WRONG_LOCAL_HEADER", "Cookie": "session=" + TOKEN,
                "Proxy-Authorization": "Basic WRONG_PROXY_HEADER", "X-Extra-Secret": "not-for-upstream"})
            self.assertEqual(status, 200)
            headers = upstream.records[0]["headers"]
            self.assertEqual(headers["authorization"], ["Bearer " + SECRET])
            for name in ("cookie", "proxy-authorization", "x-codex-ui-token", "x-extra-secret", "origin"):
                self.assertNotIn(name, headers)

    def test_malformed_json_content_type_and_body_size_reject_before_discovery(self):
        cases = [(b"{", {}, 400, "invalid"), (b"[]", {}, 400, "invalid"),
                 (b"null", {}, 400, "invalid"), (b"\xff", {}, 400, "invalid"),
                 (b"{}", {"Content-Type": "text/plain"}, 415, "content_type"),
                 (b"x" * (server_module.MAX_BODY + 1), {}, 413, "body_too_large"),
                 (b"", {"Transfer-Encoding": "chunked"}, 413, "body_too_large")]
        with self.local() as local, self.readonly(), mock.patch.object(server_module, "discover_models") as discover:
            for index, (body, headers, expected_status, code) in enumerate(cases):
                with self.subTest(case=index):
                    status, _, data = self.api(local, body=body, headers=headers)
                    self.assertEqual((status, data["code"]), (expected_status, code))
            discover.assert_not_called()
            self.assert_slots_free(local)


class KeychainReadTests(DiscoveryFixture):
    def setUp(self):
        super().setUp()
        self.native = object.__new__(keychain_module.Keychain)
        self.native.available = True

    def read_with_result(self, stdout, stderr=b"", returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch.object(keychain_module.subprocess, "run", return_value=result) as run:
            value = self.native.read(ACCOUNT)
        return value, run

    def assert_sanitized_failure(self, *, result=None, error=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(keychain_module.subprocess, "run", return_value=result, side_effect=error) as run, \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(keychain_module.KeychainError) as caught:
                self.native.read(ACCOUNT)
        self.assert_no_secret(str(caught.exception), out.getvalue(), err.getvalue())
        self.assertNotIn(PRIVATE_BODY, str(caught.exception) + out.getvalue() + err.getvalue())
        run.assert_called_once()
        return str(caught.exception)

    def test_read_uses_exact_managed_service_no_shell_secret_args_or_stdin(self):
        with self.readonly():
            value, run = self.read_with_result((SECRET + "\r\n").encode(), PRIVATE_BODY.encode())
        self.assertEqual(value, SECRET)
        run.assert_called_once_with(
            ["/usr/bin/security", "find-generic-password", "-s", keychain_module.SERVICE, "-a", ACCOUNT, "-w"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=25, check=False)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assert_no_secret(repr(run.call_args))
        self.assertNotIn("input", run.call_args.kwargs)

    def test_invalid_account_including_shell_and_header_injection_never_spawns(self):
        for account in ("", "provider", "A." + "a" * 24, "a." + "z" * 24, ACCOUNT + ";command",
                        ACCOUNT + "\n", "$(command)." + "a" * 24, "a" * 65 + "." + "a" * 24):
            with self.subTest(account=account):
                with self.assertRaises(keychain_module.KeychainError):
                    self.native.read(account)
        subprocess.run.assert_not_called()

    def test_unavailable_keychain_never_spawns(self):
        self.native.available = False
        with self.assertRaises(keychain_module.KeychainError):
            self.native.read(ACCOUNT)
        subprocess.run.assert_not_called()

    def test_nonzero_exit_does_not_echo_stdout_or_stderr(self):
        raw = (PRIVATE_BODY + SECRET).encode()
        self.assert_sanitized_failure(result=subprocess.CompletedProcess([], 44, raw, raw))

    def test_os_error_details_are_sanitized(self):
        self.assert_sanitized_failure(error=OSError(PRIVATE_BODY + SECRET))

    def test_timeout_command_output_and_stderr_are_sanitized(self):
        raw = (PRIVATE_BODY + SECRET).encode()
        self.assert_sanitized_failure(error=subprocess.TimeoutExpired(
            ["/usr/bin/security", PRIVATE_BODY + SECRET], 0.01, output=raw, stderr=raw))

    def test_invalid_utf8_output_is_sanitized(self):
        self.assert_sanitized_failure(result=subprocess.CompletedProcess(
            [], 0, (SECRET + PRIVATE_BODY).encode() + b"\xff", PRIVATE_BODY.encode()))

    def test_empty_output_is_a_clear_error_not_an_empty_credential(self):
        for raw in (b"", b"\r\n"):
            with self.subTest(raw=raw):
                message = self.assert_sanitized_failure(result=subprocess.CompletedProcess([], 0, raw, b""))
                self.assertIn("\u4e3a\u7a7a", message)


TEST_CLASSES = (ResolutionTests, ParsingTests, FetchTests, LocalModelsHTTPTests, KeychainReadTests)


def load_tests(loader, standard_tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in TEST_CLASSES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
