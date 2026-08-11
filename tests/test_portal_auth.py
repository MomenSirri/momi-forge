import unittest

import portal_auth
from portal_auth import SigningSecretError

SECRET = "unit-test-secret-value-with-enough-length"


class ValidateSigningSecretTests(unittest.TestCase):
    def test_missing_secret_is_rejected(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SigningSecretError, "is not set"):
                    portal_auth.validate_signing_secret(value)

    def test_retired_repository_default_is_rejected(self):
        with self.assertRaisesRegex(SigningSecretError, "placeholder"):
            portal_auth.validate_signing_secret(portal_auth.RETIRED_DEFAULT_SECRET)

    def test_short_secret_warns_but_is_accepted(self):
        warnings = portal_auth.validate_signing_secret("short-secret")

        self.assertEqual(len(warnings), 1)
        self.assertIn("at least", warnings[0])

    def test_strong_secret_produces_no_warnings(self):
        self.assertEqual(portal_auth.validate_signing_secret(SECRET), [])

    def test_error_message_names_the_environment_variable(self):
        with self.assertRaises(SigningSecretError) as caught:
            portal_auth.validate_signing_secret("", env_var="SOME_OTHER_SECRET")

        self.assertIn("SOME_OTHER_SECRET", str(caught.exception))


class SignTests(unittest.TestCase):
    def test_signature_is_deterministic(self):
        self.assertEqual(
            portal_auth.sign(SECRET, "user@example.com", 123, "nonce"),
            portal_auth.sign(SECRET, "user@example.com", 123, "nonce"),
        )

    def test_signature_depends_on_secret(self):
        self.assertNotEqual(
            portal_auth.sign(SECRET, "user@example.com"),
            portal_auth.sign(f"{SECRET}-other", "user@example.com"),
        )

    def test_part_boundaries_are_not_ambiguous(self):
        # "a" + "bc" must not sign the same as "ab" + "c", otherwise a crafted
        # email could shift bytes into the expiry field.
        self.assertNotEqual(
            portal_auth.sign(SECRET, "a", "bc"),
            portal_auth.sign(SECRET, "ab", "c"),
        )

    def test_context_prefix_separates_token_families(self):
        self.assertNotEqual(
            portal_auth.sign(SECRET, "runpod-management", "user@example.com"),
            portal_auth.sign(SECRET, "history-portal-session", "user@example.com"),
        )

    def test_none_parts_sign_as_empty(self):
        self.assertEqual(
            portal_auth.sign(SECRET, None, "x"),
            portal_auth.sign(SECRET, "", "x"),
        )


class SignatureMatchesTests(unittest.TestCase):
    def test_matching_signature(self):
        signature = portal_auth.sign(SECRET, "payload")

        self.assertTrue(portal_auth.signature_matches(signature, signature))

    def test_rejects_missing_and_wrong_signatures(self):
        signature = portal_auth.sign(SECRET, "payload")

        for provided in (None, "", "deadbeef", signature[:-1], signature.upper()):
            with self.subTest(provided=provided):
                self.assertFalse(portal_auth.signature_matches(signature, provided))


class ExpiryTests(unittest.TestCase):
    def test_coerce_expiry_accepts_ints_and_numeric_strings(self):
        self.assertEqual(portal_auth.coerce_expiry(1700000000), 1700000000)
        self.assertEqual(portal_auth.coerce_expiry("1700000000"), 1700000000)
        self.assertEqual(portal_auth.coerce_expiry("0"), 0)

    def test_coerce_expiry_rejects_junk(self):
        for value in (None, "", "later", "12.5", [], {}):
            with self.subTest(value=value):
                self.assertIsNone(portal_auth.coerce_expiry(value))

    def test_is_expired_compares_against_now(self):
        self.assertTrue(portal_auth.is_expired(999, now=1000))
        self.assertFalse(portal_auth.is_expired(1001, now=1000))

    def test_expiry_exactly_now_is_still_valid(self):
        self.assertFalse(portal_auth.is_expired(1000, now=1000))


class TokenPackingTests(unittest.TestCase):
    def test_round_trip(self):
        packed = portal_auth.pack_token("user@example.com", 1700000000, "nonce", "sig")

        self.assertEqual(
            portal_auth.unpack_token(packed, 4),
            ("user@example.com", "1700000000", "nonce", "sig"),
        )

    def test_packed_token_is_url_and_cookie_safe(self):
        packed = portal_auth.pack_token("user@example.com", 1, "a/b+c", "sig")

        self.assertNotIn("=", packed)
        self.assertNotIn("/", packed)
        self.assertNotIn("+", packed)

    def test_unpack_rejects_wrong_field_count(self):
        packed = portal_auth.pack_token("a", "b", "c")

        self.assertIsNone(portal_auth.unpack_token(packed, 4))
        self.assertIsNone(portal_auth.unpack_token(packed, 2))

    def test_unpack_rejects_malformed_values(self):
        for value in (None, "", "!!!not-base64!!!", "YQ"):
            with self.subTest(value=value):
                self.assertIsNone(portal_auth.unpack_token(value, 4))

    def test_token_from_another_family_is_not_reinterpreted(self):
        management_cookie = portal_auth.pack_token("a", "admin", 1, "nonce", "sig")

        self.assertIsNone(portal_auth.unpack_token(management_cookie, 4))


if __name__ == "__main__":
    unittest.main()
