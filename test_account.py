"""Account, installed-model, and usage-query regressions.

Local facts are read from a disposable home; the only network is a loopback
listener this test owns. load_tests selects these classes only, never the
test_config cases imported for fixture reuse.
"""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import time
import unittest
from unittest import mock

import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import account_info as account
import test_config as existing
import test_connection as probe
from test_discovery import reply

SECRET = existing.SECRET
AUTH_CLAIM = "https://api.openai.com/auth"
PROFILE_CLAIM = "https://api.openai.com/profile"
ACCOUNT_ID = "acct-0123456789"
PRIVATE_BODY = "UPSTREAM_PRIVATE_BODY_MUST_NOT_ESCAPE"
USAGE_PATH = "/wham/usage/plan_limit_history"


def load_tests(loader, tests, pattern):
    selected = unittest.TestSuite()
    for case in (TokenTests, LocalFactTests, InstalledModelTests, UsageParseTests, UsageRequestTests):
        selected.addTests(loader.loadTestsFromTestCase(case))
    return selected


def segment(payload):
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def jwt(claims):
    """An unsigned token with a readable payload; decode_claims never verifies."""
    return segment({"alg": "none"}) + "." + segment(claims) + "." + segment({"sig": "x"})


def claims(**changes):
    """The claims the desktop app really stores, in the shape it really stores them."""
    result = {
        AUTH_CLAIM: {
            "chatgpt_plan_type": "plus",
            "chatgpt_subscription_active_until": "2026-10-26T12:07:33+00:00",
            "chatgpt_account_id": ACCOUNT_ID,
        },
        PROFILE_CLAIM: {"email": "person@example.invalid"},
        "exp": int(time.time()) + 3600,
    }
    result.update(changes)
    return result


# The access token is itself a JWT: that is where the plan, the e-mail and the
# expiry actually live, so an opaque placeholder would not exercise the reader.
TOKEN = jwt(claims())


def usage_period(identifier, starts, basis_points, **changes):
    result = {"id": identifier, "window_minutes": 10080, "plan_type": "plus",
              "starts_at": starts, "ends_at": "2026-09-30T05:00:00Z",
              "accounting_complete": True, "used_basis_points": basis_points,
              "breakdowns": []}
    result.update(changes)
    return result


# Basis points are hundredths of a percent, so 10107 is the 101.07% the desktop
# app itself displays for an account that has used slightly more than its quota.
USAGE_BODY = {
    "data_as_of": "2026-10-01T00:00:00Z",
    "coverage_start": "2026-09-24T00:00:00Z",
    "coverage_complete": False,
    "approximate": True,
    "boundary_tolerance_seconds": 60,
    "periods": [
        usage_period("p-middle", "2026-09-16T05:00:00Z", 10131),
        usage_period("p-newest", "2026-09-23T05:00:00Z", 10107, breakdowns=[
            {"dimension": "model", "rows": [{"key": "gpt-5.2-codex", "basis_points": 7000},
                                            {"key": "gpt-5.2", "basis_points": 3107}]},
            {"dimension": "surface", "rows": [{"key": "desktop", "basis_points": 10107}]},
        ]),
        usage_period("p-oldest", "2026-09-09T05:00:00Z", 6902),
    ],
}

CATALOG_BODY = {
    "client_version": "0.159.2",
    "fetched_at": "2026-10-01T00:00:00Z",
    "models": [
        {"slug": "gpt-5.2-codex", "display_name": "GPT-5.2 Codex", "visibility": "list"},
        {"slug": "gpt-5.2", "display_name": "GPT-5.2", "visibility": "list",
         "upgrade": {"model": "gpt-6.1-sol"}},
        {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "hidden",
         "upgrade": {"model": "gpt-6.1-sol"}},
    ],
}


class AccountFixture(probe.ConnectionFixture):
    """Loopback-only sockets, disposable home, and a written auth.json."""

    def setUp(self):
        super().setUp()
        self.auth = self.home / "auth.json"
        self.token = TOKEN

    def write_auth(self, document):
        self.auth.write_text(json.dumps(document) if isinstance(document, dict) else document)

    def signed_in(self, access=None, identity=None, token=None, account_id=ACCOUNT_ID):
        """Write a ChatGPT login. `access` overrides the access token's claims."""
        if token is None:
            token = TOKEN if access is None else jwt(claims() if access is True else access)
        self.token = token
        tokens = {"access_token": token,
                  "id_token": jwt(claims() if identity is None else identity)}
        if account_id:
            tokens["account_id"] = account_id
        self.write_auth({"auth_mode": "chatgpt", "tokens": tokens})

    @contextmanager
    def api(self, base):
        # api_base() reads the environment on every call, so the override applies
        # to the server thread as well as to this one.
        with mock.patch.dict(os.environ, {"CODEX_API_BASE_URL": base, "CODEX_API_ENDPOINT": ""}):
            yield

    def fetch(self, **changes):
        payload = {"confirmed": True}
        payload.update(changes)
        before = copy.deepcopy(payload)
        with self.readonly():
            result = account.fetch_usage(self.store, payload)
        self.assertEqual(payload, before, "The query must not mutate its payload")
        self.assert_no_token(result)
        self.assertNotIn(PRIVATE_BODY, json.dumps(result))
        return result

    def assert_no_token(self, *values):
        for value in values:
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            elif not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            self.assertNotIn(self.token, value)
            self.assertNotIn(self.token.split(".")[0], value)


class TokenTests(AccountFixture):
    def test_malformed_tokens_decode_to_an_empty_mapping(self):
        for value in (None, "", "a.b", "a.b.c.d", 42, b"a.b.c", ["a", "b", "c"]):
            with self.subTest(value=value):
                self.assertEqual(account.decode_claims(value), {})

    def test_unpadded_and_padded_base64_payloads_both_decode(self):
        self.assertEqual(account.decode_claims(jwt({"plan": "plus"})), {"plan": "plus"})

    def test_a_json_payload_that_is_not_an_object_is_rejected(self):
        self.assertEqual(account.decode_claims(segment([1, 2]) + "." + segment([1]) + ".x"), {})

    def test_oversized_and_unreadable_payloads_are_rejected(self):
        self.assertEqual(account.decode_claims("a." + "x" * (account.MAX_CLAIM_BYTES + 1) + ".b"), {})

    def test_access_token_shape_is_enforced_before_any_request(self):
        self.assertEqual(account._access_token(self.home), "")
        self.write_auth({"auth_mode": "chatgpt", "tokens": {"access_token": "short"}})
        self.assertEqual(account._access_token(self.home), "")
        self.write_auth({"auth_mode": "chatgpt", "tokens": {"access_token": TOKEN}})
        self.assertEqual(account._access_token(self.home), TOKEN)
        self.write_auth({"auth_mode": "chatgpt", "tokens": {"access_token": TOKEN + "###"}})
        self.assertEqual(account._access_token(self.home), "")


class LocalFactTests(AccountFixture):
    def test_missing_auth_file_reports_a_note_and_no_plan(self):
        result = account.read_account(self.home)
        self.assertEqual(result["note"], "auth_missing")
        self.assertFalse(result["available"])
        self.assertFalse(result["usage_supported"])
        self.assertIsNone(result["plan"])

    def test_unreadable_auth_file_is_reported_not_raised(self):
        for body in ("{not json", "[]", "\xff\xfe"):
            with self.subTest(body=body):
                self.write_auth(body)
                result = account.read_account(self.home)
                self.assertEqual(result["note"], "auth_unreadable")
                self.assertFalse(result["usage_supported"])

    def test_an_oversized_auth_file_is_refused_without_reading_its_claims(self):
        self.write_auth({"auth_mode": "chatgpt", "tokens": {"access_token": TOKEN},
                         "padding": "x" * (account.MAX_AUTH_BYTES + 1)})
        self.assertEqual(account.read_account(self.home)["note"], "auth_unreadable")

    def test_api_key_login_has_no_chatgpt_plan_to_report(self):
        self.write_auth({"auth_mode": "apikey", "OPENAI_API_KEY": SECRET,
                         "tokens": {"access_token": TOKEN, "id_token": jwt(claims())}})
        result = account.read_account(self.home)
        self.assertEqual(result["note"], "not_chatgpt_login")
        self.assertFalse(result["usage_supported"])
        self.assert_no_token(result)
        self.assert_no_secret(result)

    def test_a_chatgpt_login_without_an_access_token_is_signed_out(self):
        self.write_auth({"auth_mode": "chatgpt", "tokens": {"id_token": jwt(claims())}})
        result = account.read_account(self.home)
        self.assertEqual(result["note"], "signed_out")
        self.assertFalse(result["usage_supported"])

    def test_an_expired_access_token_is_reported_as_expired_not_usable(self):
        self.signed_in(access=claims(exp=int(time.time()) - 60))
        result = account.read_account(self.home)
        self.assertEqual(result["note"], "token_expired")
        self.assertTrue(result["token_expired"])
        self.assertFalse(result["usage_supported"])

    def test_a_chatgpt_login_reports_plan_identity_and_expiry_without_the_token(self):
        self.signed_in()
        result = account.read_account(self.home)
        self.assertTrue(result["usage_supported"])
        self.assertIsNone(result["note"])
        self.assertEqual(result["plan"], "plus")
        self.assertEqual(result["email"], "person@example.invalid")
        self.assertEqual(result["account_id"], "acct-0123456789")
        self.assertEqual(result["subscription_until"], "2026-10-26T12:07:33+00:00")
        self.assertEqual(result["auth_mode"], "chatgpt")
        self.assertFalse(result["token_expired"])
        self.assertRegex(result["access_token_expires_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assert_no_token(result)
        self.assertEqual(result["path"], str(self.auth))

    def test_the_token_claim_can_supply_identity_when_the_id_token_cannot(self):
        self.signed_in(identity="broken")
        result = account.read_account(self.home)
        self.assertTrue(result["usage_supported"])
        self.assertEqual(result["plan"], "plus")

    def test_a_symlinked_auth_file_is_never_followed(self):
        target = self.home / "elsewhere.json"
        target.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": TOKEN}}))
        self.auth.symlink_to(target)
        self.assertEqual(account.read_account(self.home)["note"], "auth_missing")

    def test_state_exposes_the_account_summary_without_any_token(self):
        self.signed_in()
        state = self.store.state()
        self.assertEqual(state["account"]["plan"], "plus")
        self.assertTrue(state["account"]["usage_supported"])
        self.assert_no_token(state)
        self.assert_no_secret(state)

    def test_a_missing_auth_file_still_yields_a_complete_state_document(self):
        state = self.store.state()
        self.assertIn("account", state)
        self.assertFalse(state["account"]["available"])
        self.assertIn("installed", state)

    def test_environment_overrides_select_the_usage_base(self):
        with mock.patch.dict(os.environ, {"CODEX_API_BASE_URL": "", "CODEX_API_ENDPOINT": ""}):
            self.assertEqual(account.usage_endpoint(), account.PROD_BASE + USAGE_PATH + "?days=7")
        with mock.patch.dict(os.environ, {"CODEX_API_BASE_URL": "", "CODEX_API_ENDPOINT": "localhost"}):
            self.assertEqual(account.usage_endpoint(), account.DEV_BASE + USAGE_PATH + "?days=7")
        with mock.patch.dict(os.environ, {"CODEX_API_BASE_URL": "https://example.invalid/base/",
                                         "CODEX_API_ENDPOINT": "localhost"}):
            self.assertEqual(account.usage_endpoint(),
                             "https://example.invalid/base" + USAGE_PATH + "?days=7")

    def test_the_request_asks_for_the_documented_coverage_window(self):
        # The window is part of the contract; without it the backend applies its own
        # default and the figure would not match what the desktop app shows.
        self.assertIn("?days=" + str(account.USAGE_DAYS), account.usage_endpoint())


class InstalledModelTests(AccountFixture):
    def write_cache(self, name, body):
        (self.home / name).write_text(json.dumps(body))

    def test_the_configured_catalog_wins_over_every_cache(self):
        self.write_cache("models_cache.json", CATALOG_BODY)
        self.write_cache("opencodex-catalog.json", CATALOG_BODY)
        result = account.existing_models(self.home, self.catalog, ["original-model"], set())
        self.assertEqual(result["kind"], "configured_catalog")
        self.assertEqual(result["source"], str(self.catalog))

    def test_the_app_cache_is_used_when_no_catalog_is_configured(self):
        self.write_cache("models_cache.json", CATALOG_BODY)
        result = account.existing_models(self.home, None, [], set())
        self.assertEqual(result["kind"], "app_cache")
        self.assertEqual(result["source"], str(self.home / "models_cache.json"))

    def test_the_legacy_cache_is_the_last_resort(self):
        self.write_cache("opencodex-catalog.json", CATALOG_BODY)
        result = account.existing_models(self.home, None, [], set())
        self.assertEqual(result["kind"], "legacy_cache")
        self.assertEqual(result["source"], str(self.home / "opencodex-catalog.json"))

    def test_no_source_at_all_is_reported_rather_than_guessed(self):
        result = account.existing_models(self.home, None, [], set())
        self.assertEqual(result["kind"], "none")
        self.assertIsNone(result["source"])
        self.assertEqual(result["note"], "catalog_missing")
        self.assertEqual(result["models"], [])

    def test_unreadable_and_empty_caches_are_reported(self):
        self.write_cache("models_cache.json", ["not", "an", "object"])
        self.assertEqual(account.existing_models(self.home, None, [], set())["note"], "catalog_missing")
        self.write_cache("models_cache.json", {"models": []})
        self.assertEqual(account.existing_models(self.home, None, [], set())["note"], "catalog_empty")

    def test_counts_visibility_and_marks_duplicates_and_upgrades(self):
        self.write_cache("models_cache.json", CATALOG_BODY)
        result = account.existing_models(self.home, None, ["gpt-5.2"], {"gpt-5.2-codex"})
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["visible"], 2)
        self.assertEqual(result["hidden"], 1)
        self.assertEqual(result["client_version"], "0.159.2")
        by_slug = {entry["slug"]: entry for entry in result["models"]}
        # A model this tool already wrote and a model already in the catalog are both
        # duplicates, and those are what the picker must not select again by default.
        self.assertTrue(by_slug["gpt-5.2-codex"]["added"])
        self.assertFalse(by_slug["gpt-5.2-codex"]["in_current_catalog"])
        self.assertTrue(by_slug["gpt-5.2"]["added"])
        self.assertTrue(by_slug["gpt-5.2"]["in_current_catalog"])
        self.assertFalse(by_slug["gpt-5.5"]["added"])
        self.assertTrue(by_slug["gpt-5.5"]["hidden"])
        self.assertEqual(by_slug["gpt-5.2"]["upgrade_to"], "gpt-6.1-sol")
        self.assertIsNone(by_slug["gpt-5.2-codex"]["upgrade_to"])

    def test_duplicate_slugs_are_collapsed_and_non_entries_skipped(self):
        self.write_cache("models_cache.json", {"models": [
            {"slug": "keep-me"}, {"slug": "keep-me"}, {"slug": ""}, "not-a-dict", {"no": "slug"}]})
        result = account.existing_models(self.home, None, [], set())
        self.assertEqual([entry["slug"] for entry in result["models"]], ["keep-me"])

    def test_a_display_name_with_a_space_falls_back_to_the_slug(self):
        # redact() refuses any value containing a space, so the readable name cannot
        # be trusted; the slug is the identifier the picker actually keys on.
        self.write_cache("models_cache.json", CATALOG_BODY)
        result = account.existing_models(self.home, None, [], set())
        by_slug = {entry["slug"]: entry for entry in result["models"]}
        self.assertEqual(by_slug["gpt-5.2-codex"]["display_name"], "gpt-5.2-codex")

    def test_state_marks_already_added_models_for_the_picker(self):
        # The fixture config points model_catalog_json at a catalog holding exactly
        # one slug, so that is the source the state panel must report.
        preview = self.store.preview(self.form(model="original-model"))
        self.store.apply(self.apply_payload(self.form(model="original-model"), preview))
        state = self.store.state()
        self.assertEqual(state["installed"]["kind"], "configured_catalog")
        self.assertEqual(state["installed"]["total"], 1)
        entry = state["installed"]["models"][0]
        self.assertEqual(entry["slug"], "original-model")
        # The provider profile written by apply() is what makes this a duplicate.
        self.assertTrue(entry["added"])
        self.assertTrue(entry["in_current_catalog"])


class UsageParseTests(AccountFixture):
    def test_basis_points_become_percentages_and_may_exceed_one_hundred(self):
        # 1010700 basis points is 101.07%; an over-quota window is reported as such
        # rather than clamped, because clamping would hide that the account is over.
        self.assertEqual(account._percent(10000), 100.0)
        self.assertEqual(account._percent(10107), 101.07)
        self.assertEqual(account._percent(1), 0.01)
        self.assertEqual(account._percent(0), 0.0)
        self.assertEqual(account._percent(6902), 69.02)
        for value in (-1, None, True, "100", [], {}):
            with self.subTest(value=value):
                self.assertIsNone(account._percent(value))

    def test_the_documented_payload_is_mapped_without_inventing_fields(self):
        parsed = account.parse_usage(json.dumps(USAGE_BODY).encode("utf-8"))
        self.assertEqual(parsed["data_as_of"], "2026-10-01T00:00:00Z")
        self.assertEqual(parsed["coverage_start"], "2026-09-24T00:00:00Z")
        self.assertFalse(parsed["coverage_complete"])
        self.assertTrue(parsed["approximate"])
        self.assertEqual(len(parsed["periods"]), 3)
        newest = parsed["periods"][0]
        self.assertEqual(newest["id"], "p-newest")
        self.assertEqual(newest["used_percent"], 101.07)
        self.assertEqual(newest["plan_type"], "plus")
        self.assertTrue(newest["accounting_complete"])
        self.assertEqual(newest["breakdowns"], [
            {"dimension": "model", "rows": [{"key": "gpt-5.2-codex", "percent": 70.0},
                                            {"key": "gpt-5.2", "percent": 31.07}]},
            {"dimension": "surface", "rows": [{"key": "desktop", "percent": 101.07}]},
        ])

    def test_periods_are_sorted_newest_first_regardless_of_arrival_order(self):
        # The endpoint happens to return newest first; the UI must not depend on it.
        parsed = account.parse_usage(json.dumps(USAGE_BODY).encode("utf-8"))
        self.assertEqual([period["id"] for period in parsed["periods"]],
                         ["p-newest", "p-middle", "p-oldest"])
        reversed_body = dict(USAGE_BODY, periods=list(reversed(USAGE_BODY["periods"])))
        self.assertEqual(account.parse_usage(json.dumps(reversed_body).encode("utf-8")),
                         parsed)

    def test_missing_flags_default_to_conservative_values(self):
        parsed = account.parse_usage(json.dumps({"periods": []}).encode("utf-8"))
        self.assertFalse(parsed["coverage_complete"])
        self.assertTrue(parsed["approximate"])
        self.assertIsNone(parsed["data_as_of"])
        self.assertEqual(parsed["periods"], [])

    def test_a_period_without_any_usable_number_is_dropped(self):
        body = {"periods": [{"id": "empty"}, {"id": "kept", "used_basis_points": 5},
                            "not-a-dict", {"id": "no-number", "window_minutes": 300}]}
        parsed = account.parse_usage(json.dumps(body).encode("utf-8"))
        # Both survivors lack a start time, so the tiebreak is window length descending.
        self.assertEqual([period["id"] for period in parsed["periods"]], ["no-number", "kept"])
        self.assertIsNone(parsed["periods"][1]["window_minutes"])
        self.assertEqual(parsed["periods"][1]["used_percent"], 0.05)

    def test_a_five_hour_and_a_seven_day_window_keep_their_own_lengths(self):
        body = {"periods": [usage_period("week", "2026-09-23T05:00:00Z", 100, window_minutes=10080),
                            usage_period("five", "2026-09-23T06:00:00Z", 200, window_minutes=300)]}
        parsed = account.parse_usage(json.dumps(body).encode("utf-8"))
        self.assertEqual({period["id"]: period["window_minutes"] for period in parsed["periods"]},
                         {"five": 300, "week": 10080})

    def test_invalid_json_and_non_object_documents_are_refused(self):
        for raw in (b"not json", b"[1, 2]", b"", b'"text"', b"null"):
            with self.subTest(raw=raw):
                self.assertIsNone(account.parse_usage(raw))
        self.assertEqual(account.parse_usage(b"{}"), {
            "data_as_of": None, "coverage_start": None, "coverage_complete": False,
            "approximate": True, "periods": [],
        })

    def test_breakdowns_and_periods_are_bounded(self):
        rows = [{"key": "model-%d" % index, "basis_points": 1} for index in range(account.MAX_BREAKDOWN_ROWS + 5)]
        body = {"periods": [usage_period("p", "2026-09-23T05:00:00Z", 1,
                                        breakdowns=[{"dimension": "model", "rows": rows}])
                            for _ in range(account.MAX_PERIODS + 5)]}
        parsed = account.parse_usage(json.dumps(body).encode("utf-8"))
        self.assertEqual(len(parsed["periods"]), account.MAX_PERIODS)
        self.assertEqual(len(parsed["periods"][0]["breakdowns"][0]["rows"]), account.MAX_BREAKDOWN_ROWS)

    def test_a_breakdown_row_without_a_key_or_a_number_is_dropped(self):
        breakdowns = [
            {"dimension": "model", "rows": [{"key": "", "basis_points": 5},
                                            {"key": "ok", "basis_points": None},
                                            {"key": "good", "basis_points": 5}]},
            {"dimension": "", "rows": [{"key": "x", "basis_points": 5}]},
            {"dimension": "surface", "rows": "not-a-list"},
        ]
        period = usage_period("p", "2026-09-23T05:00:00Z", 1, breakdowns=breakdowns)
        parsed = account.parse_usage(json.dumps({"periods": [period]}).encode("utf-8"))
        self.assertEqual(parsed["periods"][0]["breakdowns"],
                         [{"dimension": "model", "rows": [{"key": "good", "percent": 0.05}]}])

    def test_an_echoed_token_never_survives_parsing(self):
        body = dict(USAGE_BODY, data_as_of=TOKEN,
                    periods=[usage_period(TOKEN, "2026-09-23T05:00:00Z", 1, plan_type=TOKEN,
                                          breakdowns=[{"dimension": "model",
                                                       "rows": [{"key": TOKEN, "basis_points": 1}]}])])
        parsed = account.parse_usage(json.dumps(body).encode("utf-8"), TOKEN)
        self.assert_no_token(parsed)
        self.assertEqual(parsed["periods"][0]["id"], "[redacted]")


class UsageRequestTests(AccountFixture):
    def respond_with(self, body=USAGE_BODY, status=200, headers=None):
        def respond(handler):
            reply(handler, body, status=status, headers=headers)
        return respond

    def test_consent_is_required_before_anything_is_sent(self):
        self.signed_in()
        for payload in ({}, {"confirmed": False}, {"confirmed": "yes"}, None, [], "x"):
            with self.subTest(payload=payload):
                error = self.assert_error(lambda: account.fetch_usage(self.store, payload),
                                          "confirmation_required")
                self.assertIn("chatgpt.com", str(error))

    def test_a_missing_login_is_reported_before_any_request(self):
        error = self.assert_error(lambda: self.fetch(), "usage_unavailable")
        self.assertIn("auth.json", str(error))

    def test_an_expired_login_is_reported_before_any_request(self):
        self.signed_in(access=claims(exp=int(time.time()) - 60))
        error = self.assert_error(lambda: self.fetch(), "usage_unavailable")
        self.assertIn("过期", str(error))

    def test_an_api_key_login_is_reported_before_any_request(self):
        self.write_auth({"auth_mode": "apikey", "tokens": {"access_token": TOKEN}})
        error = self.assert_error(lambda: self.fetch(), "usage_unavailable")
        self.assertIn("API Key", str(error))

    def test_one_authenticated_request_is_sent_to_the_documented_endpoint(self):
        self.signed_in()
        with self.upstream(self.respond_with()) as httpd, self.api(httpd.origin + "/api"):
            result = self.fetch()
        self.assertTrue(result["ok"])
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["endpoint"], httpd.origin + "/api")
        self.assertEqual(result["plan"], "plus")
        self.assertEqual(result["subscription_until"], "2026-10-26T12:07:33+00:00")
        self.assertEqual(len(result["periods"]), 3)
        self.assertIsInstance(result["latency_ms"], int)
        self.assertRegex(result["queried_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assert_no_token(result)
        self.assertEqual(len(httpd.records), 1)
        record = httpd.records[0]
        self.assertEqual(record["method"], "GET")
        self.assertEqual(record["path"], "/api" + USAGE_PATH + "?days=7")
        self.assertEqual(record["headers"]["authorization"], ["Bearer " + TOKEN])
        self.assertEqual(record["headers"]["originator"], ["Codex Desktop"])
        self.assertEqual(record["headers"]["chatgpt-account-id"], ["acct-0123456789"])
        self.assertEqual(record["body"], b"")

    def test_the_account_header_is_omitted_when_there_is_no_account_id(self):
        without_id = claims()
        del without_id[AUTH_CLAIM]["chatgpt_account_id"]
        self.signed_in(access=without_id, identity=without_id, account_id="")
        with self.upstream(self.respond_with()) as httpd, self.api(httpd.origin + "/api"):
            self.fetch()
        self.assertNotIn("chatgpt-account-id", httpd.records[0]["headers"])

    def test_a_four_oh_four_is_an_empty_result_not_a_failure(self):
        self.signed_in()
        with self.upstream(self.respond_with({"detail": "no data"}, status=404)) as httpd, \
                self.api(httpd.origin + "/api"):
            result = self.fetch()
        self.assertTrue(result["ok"])
        self.assertEqual(result["http_status"], 404)
        self.assertEqual(result["periods"], [])
        self.assertIn("404", result["message"])
        self.assertNotIn(PRIVATE_BODY, json.dumps(result))

    def test_an_unauthorized_reply_is_reported_without_reading_the_error_body(self):
        self.signed_in()
        with self.upstream(self.respond_with({"error": PRIVATE_BODY}, status=401)) as httpd, \
                self.api(httpd.origin + "/api"):
            error = self.assert_error(lambda: self.fetch(), "usage_unauthorized", 401)
        self.assertNotIn(PRIVATE_BODY, str(error))
        self.assertIn("重新登录", str(error))

    def test_a_forbidden_reply_is_also_unauthorized(self):
        self.signed_in()
        with self.upstream(self.respond_with({}, status=403)) as httpd, \
                self.api(httpd.origin + "/api"):
            self.assert_error(lambda: self.fetch(), "usage_unauthorized", 401)

    def test_rate_limiting_is_reported_distinctly(self):
        self.signed_in()
        with self.upstream(self.respond_with({}, status=429)) as httpd, \
                self.api(httpd.origin + "/api"):
            self.assert_error(lambda: self.fetch(), "usage_rate_limited", 429)

    def test_any_other_failure_hides_the_body_and_does_not_replay_the_token(self):
        self.signed_in()
        with self.upstream(self.respond_with({"error": PRIVATE_BODY}, status=500)) as httpd, \
                self.api(httpd.origin + "/api"):
            error = self.assert_error(lambda: self.fetch(), "usage_failed", 502)
        self.assertNotIn(PRIVATE_BODY, str(error))
        self.assertIn("HTTP 500", str(error))
        self.assertEqual(len(httpd.records), 1)

    def test_a_redirect_is_never_followed_with_the_token(self):
        self.signed_in()
        with self.upstream(self.respond_with("", status=302,
                                            headers={"Location": "https://example.invalid/steal"})) as httpd, \
                self.api(httpd.origin + "/api"):
            self.assert_error(lambda: self.fetch(), "usage_failed", 502)
        self.assertEqual(len(httpd.records), 1)

    def test_an_unsupported_content_encoding_is_refused_unparsed(self):
        self.signed_in()
        with self.upstream(self.respond_with(USAGE_BODY, headers={"Content-Encoding": "gzip"})) as httpd, \
                self.api(httpd.origin + "/api"):
            self.assert_error(lambda: self.fetch(), "usage_encoding", 502)

    def test_an_oversized_body_stops_reading(self):
        self.signed_in()
        with self.upstream(self.respond_with(b"x" * (account.MAX_BYTES + 1))) as httpd, \
                self.api(httpd.origin + "/api"):
            self.assert_error(lambda: self.fetch(), "usage_too_large", 502)

    def test_a_timeout_is_reported_without_a_partial_result(self):
        self.signed_in()

        def respond(handler):
            time.sleep(1.0)
            reply(handler, USAGE_BODY)

        with self.upstream(respond) as httpd, self.api(httpd.origin + "/api"):
            with mock.patch.object(account, "REQUEST_TIMEOUT", 0.05):
                self.assert_error(lambda: self.fetch(), "usage_timeout", 504)

    def test_an_unreachable_host_is_reported_as_a_network_error(self):
        # Port 1 on loopback refuses the connection; no external host is contacted.
        self.signed_in()
        self.allowed.add(("127.0.0.1", 1))
        with self.api("http://127.0.0.1:1/api"):
            self.assert_error(lambda: self.fetch(), "usage_network", 502)

    def test_a_successful_reply_that_is_not_json_is_refused(self):
        self.signed_in()
        with self.upstream(self.respond_with(b"<!doctype html>")) as httpd, \
                self.api(httpd.origin + "/api"):
            self.assert_error(lambda: self.fetch(), "usage_invalid", 502)

    def test_the_query_never_writes_a_file_or_saves_a_credential(self):
        # fetch() already runs inside readonly(), which asserts exactly that; this
        # case re-checks the whole home directory around the request itself.
        self.signed_in()
        before = self.tree_snapshot()
        with self.upstream(self.respond_with()) as httpd, self.api(httpd.origin + "/api"):
            result = self.fetch()
        self.assertTrue(result["ok"])
        self.assertEqual(self.tree_snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
