"""Authorization tests for the portal proxies mounted next to the Gradio app.

`/history-proxy` and `/runpod-management` are FastAPI routes registered
alongside the mounted Gradio app, so Gradio's `auth=` callback never runs for
them and these functions are the only gate.
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import app
import portal_auth

SECRET = "app-authorization-test-secret-value"
ADMIN_EMAIL = "admin.user@brickvisual.com"
PLAIN_EMAIL = "plain.user@brickvisual.com"


class FakeRequest:
    """Minimal stand-in for a Starlette request."""

    def __init__(self, query: dict | None = None, cookies: dict | None = None):
        self.query_params = dict(query or {})
        self.cookies = dict(cookies or {})


class FakeAuthService:
    """Answers `get_identity` from a fixed email -> role mapping."""

    def __init__(self, roles: dict[str, str]):
        self.roles = roles

    def get_identity(self, email: str):
        normalized = (email or "").strip().lower()
        return SimpleNamespace(email=normalized, role=self.roles.get(normalized, "user"))


class PortalAuthTestCase(unittest.TestCase):
    """Pins the signing secret and the user directory for every test."""

    roles = {ADMIN_EMAIL: "admin", PLAIN_EMAIL: "user"}

    def setUp(self):
        for patcher in (
            patch.object(app, "HISTORY_PORTAL_SSO_SECRET", SECRET),
            patch.object(app, "auth_service", FakeAuthService(self.roles)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def future(seconds: int = 600) -> int:
        return int(time.time()) + seconds

    @staticmethod
    def past(seconds: int = 600) -> int:
        return int(time.time()) - seconds


class RoleTests(PortalAuthTestCase):
    def test_unknown_roles_fall_back_to_user(self):
        for value in (None, "", "   ", "superuser", "ADMINISTRATOR"):
            with self.subTest(value=value):
                self.assertEqual(app._normalize_role(value), "user")

    def test_known_roles_are_normalized(self):
        self.assertEqual(app._normalize_role("  ADMIN "), "admin")
        self.assertEqual(app._normalize_role("Ex"), "ex")
        self.assertEqual(app._normalize_role("user"), "user")

    def test_admin_analytics_is_limited_to_admin_and_executive(self):
        self.assertTrue(app._can_view_admin_analytics("admin"))
        self.assertTrue(app._can_view_admin_analytics("ex"))
        self.assertFalse(app._can_view_admin_analytics("user"))
        self.assertFalse(app._can_view_admin_analytics(None))

    def test_runpod_management_is_limited_to_admin_and_executive(self):
        self.assertTrue(app._can_view_runpod_management("admin"))
        self.assertTrue(app._can_view_runpod_management("ex"))
        self.assertFalse(app._can_view_runpod_management("user"))
        self.assertFalse(app._can_view_runpod_management("SUPERUSER"))

    def test_runpod_billing_is_limited_to_the_configured_allowlist(self):
        with patch.object(app, "RUNPOD_BILLING_EMAILS", {"owner@brickvisual.com"}):
            self.assertTrue(app._can_view_runpod_billing("owner@brickvisual.com"))
            self.assertTrue(app._can_view_runpod_billing("  OWNER@brickvisual.com "))
            self.assertFalse(app._can_view_runpod_billing(ADMIN_EMAIL))
            self.assertFalse(app._can_view_runpod_billing(None))


class ManagementTokenTests(PortalAuthTestCase):
    def valid_token(self, email=ADMIN_EMAIL, role="admin", exp=None, nonce="nonce-1"):
        exp = self.future() if exp is None else exp
        return {
            "email": email,
            "role": role,
            "exp": str(exp),
            "nonce": nonce,
            "sig": app._runpod_management_signature(email, role, exp, nonce),
        }

    def test_valid_token_is_accepted(self):
        token = self.valid_token()

        self.assertTrue(app._verify_runpod_management_token(**token))

    def test_expired_token_is_rejected(self):
        token = self.valid_token(exp=self.past())

        self.assertFalse(app._verify_runpod_management_token(**token))

    def test_tampered_signature_is_rejected(self):
        token = self.valid_token()
        token["sig"] = "0" * len(token["sig"])

        self.assertFalse(app._verify_runpod_management_token(**token))

    def test_token_signed_with_another_secret_is_rejected(self):
        with patch.object(app, "HISTORY_PORTAL_SSO_SECRET", "a-completely-different-secret"):
            token = self.valid_token()

        self.assertFalse(app._verify_runpod_management_token(**token))

    def test_role_cannot_be_escalated_in_the_query_string(self):
        # Signed as a plain user, then presented as admin.
        token = self.valid_token(email=PLAIN_EMAIL, role="user")
        token["role"] = "admin"

        self.assertFalse(app._verify_runpod_management_token(**token))

    def test_unprivileged_role_is_rejected_even_when_signed(self):
        token = self.valid_token(email=PLAIN_EMAIL, role="user")

        self.assertFalse(app._verify_runpod_management_token(**token))

    def test_incomplete_tokens_are_rejected(self):
        for field in ("email", "exp", "nonce", "sig"):
            with self.subTest(missing=field):
                token = self.valid_token()
                token[field] = None
                self.assertFalse(app._verify_runpod_management_token(**token))

    def test_non_numeric_expiry_is_rejected(self):
        token = self.valid_token()
        token["exp"] = "not-a-timestamp"

        self.assertFalse(app._verify_runpod_management_token(**token))


class ManagementCookieTests(PortalAuthTestCase):
    def test_cookie_round_trip(self):
        exp = self.future()
        sig = app._runpod_management_signature(ADMIN_EMAIL, "admin", exp, "nonce-1")
        packed = app._pack_runpod_management_cookie(ADMIN_EMAIL, "admin", exp, "nonce-1", sig)

        self.assertEqual(
            app._unpack_runpod_management_cookie(packed),
            (ADMIN_EMAIL, "admin", exp, "nonce-1", sig),
        )

    def test_malformed_cookies_are_rejected(self):
        for value in (None, "", "not-base64!!", portal_auth.pack_token("a", "b")):
            with self.subTest(value=value):
                self.assertIsNone(app._unpack_runpod_management_cookie(value))

    def test_unknown_role_in_cookie_is_downgraded_to_user(self):
        packed = portal_auth.pack_token(ADMIN_EMAIL, "root", self.future(), "n", "sig")

        unpacked = app._unpack_runpod_management_cookie(packed)

        self.assertIsNotNone(unpacked)
        self.assertEqual(unpacked[1], "user")


class AuthorizeManagementRequestTests(PortalAuthTestCase):
    def query_for(self, email=ADMIN_EMAIL, role="admin", exp=None, nonce="nonce-1"):
        exp = self.future() if exp is None else exp
        return {
            "email": email,
            "role": role,
            "exp": str(exp),
            "nonce": nonce,
            "sig": app._runpod_management_signature(email, role, exp, nonce),
        }

    def test_signed_query_from_a_current_admin_is_authorized(self):
        access = app._authorize_runpod_management_request(FakeRequest(query=self.query_for()))

        self.assertIsNotNone(access)
        _cookie, role = access
        self.assertEqual(role, "admin")

    def test_signed_query_is_rejected_when_the_directory_no_longer_grants_access(self):
        # A token minted while the user was an admin must stop working once the
        # directory demotes them.
        query = self.query_for(email=PLAIN_EMAIL, role="admin")
        query["sig"] = app._runpod_management_signature(PLAIN_EMAIL, "admin", int(query["exp"]), "nonce-1")

        self.assertIsNone(app._authorize_runpod_management_request(FakeRequest(query=query)))

    def test_cookie_authorizes_follow_up_requests_without_query_params(self):
        access = app._authorize_runpod_management_request(FakeRequest(query=self.query_for()))
        self.assertIsNotNone(access)
        cookie, _role = access

        follow_up = app._authorize_runpod_management_request(
            FakeRequest(cookies={app.RUNPOD_MANAGEMENT_COOKIE_NAME: cookie})
        )

        self.assertIsNotNone(follow_up)
        self.assertEqual(follow_up[1], "admin")

    def test_expired_cookie_is_rejected(self):
        exp = self.past()
        sig = app._runpod_management_signature(ADMIN_EMAIL, "admin", exp, "nonce-1")
        cookie = app._pack_runpod_management_cookie(ADMIN_EMAIL, "admin", exp, "nonce-1", sig)

        self.assertIsNone(
            app._authorize_runpod_management_request(
                FakeRequest(cookies={app.RUNPOD_MANAGEMENT_COOKIE_NAME: cookie})
            )
        )

    def test_unauthenticated_request_is_rejected(self):
        self.assertIsNone(app._authorize_runpod_management_request(FakeRequest()))

    def test_history_cookie_cannot_authorize_the_management_console(self):
        history_cookie = app._issue_history_portal_cookie(ADMIN_EMAIL)

        self.assertIsNone(
            app._authorize_runpod_management_request(
                FakeRequest(cookies={app.RUNPOD_MANAGEMENT_COOKIE_NAME: history_cookie})
            )
        )


class HistoryPortalTokenTests(PortalAuthTestCase):
    def url_token(self, email=ADMIN_EMAIL, exp=None, nonce="nonce-1"):
        exp = self.future() if exp is None else exp
        return {
            "email": email,
            "exp": str(exp),
            "nonce": nonce,
            "sig": app._history_portal_url_signature(email, exp, nonce),
        }

    def test_valid_entry_token_returns_the_signed_email(self):
        self.assertEqual(
            app._verify_history_portal_url_token(**self.url_token()),
            ADMIN_EMAIL,
        )

    def test_generated_sso_url_carries_a_token_this_app_accepts(self):
        url = app._build_history_portal_sso_url(ADMIN_EMAIL, "http://127.0.0.1:8199")
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}

        self.assertEqual(
            app._verify_history_portal_url_token(
                query["email"],
                query["exp"],
                query["nonce"],
                query["sig"],
            ),
            ADMIN_EMAIL,
        )

    def test_expired_entry_token_is_rejected(self):
        self.assertIsNone(app._verify_history_portal_url_token(**self.url_token(exp=self.past())))

    def test_tampered_entry_token_is_rejected(self):
        token = self.url_token()
        token["email"] = PLAIN_EMAIL

        self.assertIsNone(app._verify_history_portal_url_token(**token))

    def test_entry_token_signed_with_another_secret_is_rejected(self):
        with patch.object(app, "HISTORY_PORTAL_SSO_SECRET", "a-completely-different-secret"):
            token = self.url_token()

        self.assertIsNone(app._verify_history_portal_url_token(**token))

    def test_incomplete_entry_tokens_are_rejected(self):
        for field in ("email", "exp", "nonce", "sig"):
            with self.subTest(missing=field):
                token = self.url_token()
                token[field] = None
                self.assertIsNone(app._verify_history_portal_url_token(**token))

    def test_session_cookie_round_trip(self):
        cookie = app._issue_history_portal_cookie(ADMIN_EMAIL)

        self.assertEqual(app._verify_history_portal_cookie(cookie), ADMIN_EMAIL)

    def test_session_cookie_signed_with_another_secret_is_rejected(self):
        with patch.object(app, "HISTORY_PORTAL_SSO_SECRET", "a-completely-different-secret"):
            cookie = app._issue_history_portal_cookie(ADMIN_EMAIL)

        self.assertIsNone(app._verify_history_portal_cookie(cookie))

    def test_expired_session_cookie_is_rejected(self):
        exp = self.past()
        sig = app._history_portal_cookie_signature(ADMIN_EMAIL, exp, "nonce-1")
        cookie = portal_auth.pack_token(ADMIN_EMAIL, exp, "nonce-1", sig)

        self.assertIsNone(app._verify_history_portal_cookie(cookie))

    def test_entry_token_signature_is_not_valid_as_a_cookie_signature(self):
        # The two families use different context prefixes, so a URL signature
        # must not be replayable as a session cookie.
        exp = self.future()
        cookie = portal_auth.pack_token(
            ADMIN_EMAIL,
            exp,
            "nonce-1",
            app._history_portal_url_signature(ADMIN_EMAIL, exp, "nonce-1"),
        )

        self.assertIsNone(app._verify_history_portal_cookie(cookie))

    def test_malformed_session_cookies_are_rejected(self):
        for value in (None, "", "not-base64!!", portal_auth.pack_token("a", "b")):
            with self.subTest(value=value):
                self.assertIsNone(app._verify_history_portal_cookie(value))


class AuthorizeHistoryProxyRequestTests(PortalAuthTestCase):
    def test_signed_query_is_authorized(self):
        exp = self.future()
        query = {
            "email": ADMIN_EMAIL,
            "exp": str(exp),
            "nonce": "nonce-1",
            "sig": app._history_portal_url_signature(ADMIN_EMAIL, exp, "nonce-1"),
        }

        self.assertEqual(
            app._authorize_history_proxy_request(FakeRequest(query=query)),
            ADMIN_EMAIL,
        )

    def test_session_cookie_authorizes_iframe_asset_requests(self):
        cookie = app._issue_history_portal_cookie(PLAIN_EMAIL)

        self.assertEqual(
            app._authorize_history_proxy_request(
                FakeRequest(cookies={app.HISTORY_PORTAL_COOKIE_NAME: cookie})
            ),
            PLAIN_EMAIL,
        )

    def test_unauthenticated_request_is_rejected(self):
        self.assertIsNone(app._authorize_history_proxy_request(FakeRequest()))

    def test_unsigned_query_string_is_rejected(self):
        query = {"email": ADMIN_EMAIL, "exp": str(self.future()), "nonce": "n", "sig": "forged"}

        self.assertIsNone(app._authorize_history_proxy_request(FakeRequest(query=query)))

    def test_management_cookie_cannot_authorize_the_history_proxy(self):
        exp = self.future()
        sig = app._runpod_management_signature(ADMIN_EMAIL, "admin", exp, "nonce-1")
        management_cookie = app._pack_runpod_management_cookie(ADMIN_EMAIL, "admin", exp, "nonce-1", sig)

        self.assertIsNone(
            app._authorize_history_proxy_request(
                FakeRequest(cookies={app.HISTORY_PORTAL_COOKIE_NAME: management_cookie})
            )
        )


class SigningSecretStartupTests(unittest.TestCase):
    def test_startup_fails_without_a_secret(self):
        with patch.object(app, "HISTORY_PORTAL_SSO_SECRET", ""):
            with self.assertRaises(portal_auth.SigningSecretError):
                app._require_portal_signing_secret()

    def test_startup_fails_on_the_retired_repository_default(self):
        with patch.object(app, "HISTORY_PORTAL_SSO_SECRET", portal_auth.RETIRED_DEFAULT_SECRET):
            with self.assertRaises(portal_auth.SigningSecretError):
                app._require_portal_signing_secret()

    def test_startup_succeeds_with_a_strong_secret(self):
        with patch.object(app, "HISTORY_PORTAL_SSO_SECRET", SECRET):
            app._require_portal_signing_secret()


if __name__ == "__main__":
    unittest.main()
