"""Connection-test regressions: one minimal Responses probe, owned loopback only.

Run with the requested interpreter and -B. load_tests deliberately selects only
these classes, never the test_config cases imported for fixture reuse.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_core as core
import connection_test as probe
import test_config as existing
from test_discovery import UpstreamServer, reply

SECRET = existing.SECRET
PRIVATE_BODY = "UPSTREAM_PRIVATE_BODY_MUST_NOT_ESCAPE"
RESPONSE_OBJECT = {"object": "response", "id": "resp_probe", "model": "probe-model",
                   "output": [{"type": "message", "content": [{"type": "output_text", "text": "pong"}]}]}


def load_tests(loader, tests, pattern):
    selected = unittest.TestSuite()
    for case in (PureFunctionTests, ConnectionTests):
        selected.addTests(loader.loadTestsFromTestCase(case))
    return selected


class ConnectionFixture(existing.ConfigFixture):
    def setUp(self):
        super().setUp()
        self.allowed = set()
        original_connect = socket.socket.connect
        original_create_connection = socket.create_connection

        def guarded_connect(sock, address):
            if not isinstance(address, tuple) or address[:2] not in self.allowed:
                raise AssertionError("Tests may connect only to their own loopback listeners")
            return original_connect(sock, address)

        def guarded_create_connection(address, *args, **kwargs):
            if address not in self.allowed:
                raise AssertionError("Tests must not resolve or contact external/live endpoints")
            return original_create_connection(address, *args, **kwargs)

        for patcher in (
            mock.patch.object(socket.socket, "connect", guarded_connect),
            mock.patch.object(socket, "create_connection", guarded_create_connection),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("Real subprocess forbidden")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def upstream(self, respond):
        httpd = UpstreamServer(respond)
        address = ("127.0.0.1", httpd.server_port)
        self.allowed.add(address)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)
            self.allowed.discard(address)
            self.assertFalse(thread.is_alive(), "Test server did not stop")
            self.assertEqual(httpd.errors, [], "Unexpected upstream handler error")

    def payload(self, base, **changes):
        result = {"confirmed": True, "base_url": base, "auth_mode": "keychain",
                  "api_key": SECRET, "model": "probe-model"}
        result.update(changes)
        return result

    @contextmanager
    def readonly(self):
        tree = self.tree_snapshot()
        plans = copy.deepcopy(self.store.plans)
        items = copy.deepcopy(self.fake.items)
        exists = list(self.fake.exists_calls)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(core, "atomic_write", side_effect=AssertionError("Probe wrote a file")), \
                mock.patch.object(self.store, "preview", side_effect=AssertionError("Probe retained a plan")), \
                mock.patch.object(self.store, "apply", side_effect=AssertionError("Probe applied config")), \
                mock.patch.object(self.fake, "add", side_effect=AssertionError("Probe wrote a credential")), \
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

    def probe(self, httpd, **changes):
        payload = self.payload(httpd.origin, **changes)
        before = copy.deepcopy(payload)
        with self.readonly():
            result = probe.test_connection(self.store, payload)
        self.assertEqual(payload, before, "The probe must not mutate its payload")
        self.assert_no_secret(result)
        self.assertNotIn(PRIVATE_BODY, json.dumps(result))
        return result


class PureFunctionTests(ConnectionFixture):
    def test_request_body_is_minimal_and_never_asks_the_provider_to_retain_it(self):
        body = json.loads(probe.request_body("probe-model"))
        self.assertEqual(body, {"model": "probe-model", "input": "ping",
                                "max_output_tokens": 16, "stream": False, "store": False})
        self.assertEqual(probe.PROTOCOL, "responses")

    def test_redact_drops_credentials_control_characters_and_spaced_values(self):
        # An echoed credential is replaced, and any value that mixes it with other
        # text is dropped entirely rather than partially sanitized.
        self.assertEqual(probe.redact(SECRET, SECRET), "[redacted]")
        self.assertEqual(probe.redact("echo " + SECRET, SECRET), "")
        self.assertEqual(probe.redact(None, SECRET), "")
        self.assertEqual(probe.redact("two words", ""), "")
        # Control characters are stripped rather than trimmed into a different value.
        self.assertEqual(probe.redact("line\nbreak", ""), "linebreak")
        self.assertEqual(probe.redact("x" * 500, ""), "x" * probe.MAX_FIELD)

    def test_parse_body_rejects_oversized_non_json_and_non_object_replies(self):
        self.assertIsNone(probe.parse_body(b"x" * (probe.MAX_BYTES + 1)))
        self.assertIsNone(probe.parse_body(b"not json"))
        self.assertIsNone(probe.parse_body(b"[1, 2]"))
        self.assertEqual(probe.parse_body(b'{"object": "response"}'), {"object": "response"})

    def test_classify_accepts_a_real_responses_object(self):
        verdict = probe.classify(200, RESPONSE_OBJECT, SECRET)
        self.assertTrue(verdict["compatible"])
        self.assertEqual(verdict["response_type"], "response")
        self.assertEqual(verdict["reported_model"], "probe-model")
        self.assertIn(probe.TOKEN_NOTE, verdict["warnings"])
        # Success claims exactly one minimal request, never general compatibility.
        self.assertEqual(len(verdict["warnings"]), 2)

    def test_classify_accepts_a_response_with_only_an_output_list(self):
        verdict = probe.classify(200, {"output": []}, SECRET)
        self.assertTrue(verdict["compatible"])
        self.assertEqual(verdict["reported_model"], "")

    def test_classify_rejects_chat_completions_shape(self):
        verdict = probe.classify(200, {"object": "chat.completion", "model": "probe-model",
                                       "choices": [{"message": {"content": "pong"}}]}, SECRET)
        self.assertFalse(verdict["compatible"])
        self.assertEqual(verdict["response_type"], "chat.completion")
        self.assertIn("Chat Completions", verdict["message"])

    def test_classify_rejects_a_successful_reply_without_the_responses_shape(self):
        verdict = probe.classify(200, {"object": "list", "data": [{"id": "probe-model"}]}, SECRET)
        self.assertFalse(verdict["compatible"])

    def test_classify_rejects_a_successful_reply_that_is_not_json(self):
        verdict = probe.classify(200, None, SECRET)
        self.assertFalse(verdict["compatible"])
        self.assertEqual(verdict["reported_model"], "")

    def test_classify_maps_every_failure_status_to_a_protocol_or_credential_reason(self):
        cases = {
            301: "跳转", 302: "跳转", 400: "模型 ID", 401: "认证", 403: "认证",
            404: "/responses", 405: "/responses", 429: "限流", 500: "HTTP 500",
            501: "/responses", 503: "HTTP 503",
        }
        for status, expected in cases.items():
            verdict = probe.classify(status, None, SECRET)
            self.assertFalse(verdict["compatible"], status)
            self.assertIn(expected, verdict["message"], status)
            self.assertIn(probe.TOKEN_NOTE, verdict["warnings"], status)

    def test_classify_never_returns_the_credential_from_an_upstream_field(self):
        exact = probe.classify(200, dict(RESPONSE_OBJECT, model=SECRET), SECRET)
        self.assertEqual(exact["reported_model"], "[redacted]")
        mixed = probe.classify(200, dict(RESPONSE_OBJECT, model="echo " + SECRET), SECRET)
        self.assertEqual(mixed["reported_model"], "")
        self.assertNotIn(SECRET, json.dumps(mixed))

    def test_probe_requires_explicit_consent_before_any_request(self):
        error = self.assert_error(
            lambda: probe.test_connection(self.store, self.payload("http://127.0.0.1:1/v1", confirmed=False)),
            "confirmation_required")
        self.assertIn("测试连接", str(error))

    def test_probe_rejects_a_base_url_that_already_points_at_responses(self):
        self.assert_error(
            lambda: probe.test_connection(self.store, self.payload("http://127.0.0.1:1/v1/responses")))


class ConnectionTests(ConnectionFixture):
    def test_probe_sends_exactly_one_minimal_request_and_reports_compatibility(self):
        def respond(handler):
            reply(handler, RESPONSE_OBJECT)

        with self.upstream(respond) as httpd:
            result = self.probe(httpd)
        self.assertTrue(result["compatible"])
        self.assertTrue(result["connection_tested"])
        self.assertEqual(result["protocol"], "responses")
        self.assertEqual(result["endpoint"], httpd.origin + "/responses")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["model"], "probe-model")
        self.assertIsInstance(result["latency_ms"], int)
        self.assertGreaterEqual(result["latency_ms"], 0)
        records = httpd.records
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["method"], records[0]["path"]), ("POST", "/responses"))
        self.assertEqual(json.loads(records[0]["body"]), json.loads(probe.request_body("probe-model")))
        self.assertEqual(records[0]["headers"]["authorization"], ["Bearer " + SECRET])

    def test_probe_sends_no_authorization_header_without_credentials(self):
        def respond(handler):
            reply(handler, RESPONSE_OBJECT)

        with self.upstream(respond) as httpd:
            result = self.probe(httpd, auth_mode="none", api_key="",
                                base_url="http://127.0.0.1:" + str(httpd.server_port) + "/v1")
        self.assertTrue(result["compatible"])
        self.assertNotIn("authorization", httpd.records[0]["headers"])

    def test_probe_reports_auth_failure_without_reading_the_error_body(self):
        def respond(handler):
            reply(handler, {"error": {"message": PRIVATE_BODY}}, status=401)

        with self.upstream(respond) as httpd:
            result = self.probe(httpd)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["http_status"], 401)
        self.assertIn("认证", result["message"])
        self.assertEqual(len(httpd.records), 1)

    def test_probe_refuses_to_follow_a_redirect(self):
        def respond(handler):
            reply(handler, "", status=302, headers={"Location": "/v1/responses"})

        with self.upstream(respond) as httpd:
            result = self.probe(httpd)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["http_status"], 302)
        self.assertIn("跳转", result["message"])
        # One request only: the credential is never replayed to the redirect target.
        self.assertEqual(len(httpd.records), 1)

    def test_probe_rejects_an_unsupported_content_encoding(self):
        def respond(handler):
            reply(handler, b'{"object": "response"}', headers={"Content-Encoding": "gzip"})

        with self.upstream(respond) as httpd:
            self.assert_error(lambda: self.probe(httpd), "connection_encoding", 502)

    def test_probe_stops_reading_an_oversized_reply(self):
        def respond(handler):
            reply(handler, b"x" * (probe.MAX_BYTES + 1))

        with self.upstream(respond) as httpd:
            self.assert_error(lambda: self.probe(httpd), "connection_too_large", 502)

    def test_probe_times_out_without_saving_or_reporting_a_verdict(self):
        def respond(handler):
            import time
            time.sleep(1.0)
            reply(handler, RESPONSE_OBJECT)

        with self.upstream(respond) as httpd:
            with mock.patch.object(probe, "REQUEST_TIMEOUT", 0.05):
                self.assert_error(lambda: self.probe(httpd), "connection_timeout", 504)

    def test_probe_reports_an_unreachable_address_as_a_network_error(self):
        # Port 1 on loopback refuses the connection; no external host is contacted.
        self.allowed.add(("127.0.0.1", 1))
        self.assert_error(lambda: self.probe(SimpleNamespace(origin="http://127.0.0.1:1")),
                          "connection_network", 502)

    def test_probe_redacts_a_credential_echoed_by_the_provider(self):
        def respond(handler):
            reply(handler, dict(RESPONSE_OBJECT, model="echo " + SECRET))

        with self.upstream(respond) as httpd:
            result = self.probe(httpd)
        self.assertEqual(result["reported_model"], "")
        self.assertNotIn(SECRET, json.dumps(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
