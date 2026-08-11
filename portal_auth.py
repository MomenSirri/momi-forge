"""Signing primitives shared by the Momi Forge portal proxies.

The history portal and the RunPod management console are mounted as FastAPI
routes next to the Gradio app, so Gradio's `auth=` callback does not cover
them. Both flows authorize every proxied request with an HMAC signed token:
a signed query string on entry, then a signed cookie for the follow-up
asset and API requests the embedded iframe makes.

Keeping the crypto here means both flows share one implementation and can be
unit tested without importing the Gradio app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

# Shipped in the repository as a fallback default until it was retired, so it
# must be treated as public knowledge and rejected outright.
RETIRED_DEFAULT_SECRET = "momi-forge-local-sso-secret"
MIN_SECRET_LENGTH = 32
SECRET_ENV_VAR = "HISTORY_PORTAL_SSO_SECRET"
GENERATE_SECRET_HINT = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'


class SigningSecretError(RuntimeError):
    """The portal signing secret is missing or must not be used."""


def validate_signing_secret(secret: str | None, *, env_var: str = SECRET_ENV_VAR) -> list[str]:
    """Return non-fatal warnings, or raise when the secret is unusable."""
    value = (secret or "").strip()
    if not value:
        raise SigningSecretError(
            f"{env_var} is not set. The history portal and RunPod management proxies sign "
            "their access tokens with it, so starting without one would leave both open. "
            f"Generate a secret with: {GENERATE_SECRET_HINT}"
        )
    if value == RETIRED_DEFAULT_SECRET:
        raise SigningSecretError(
            f"{env_var} is still the placeholder that shipped in the repository. That value "
            "is public, so anyone could forge an admin portal token. Replace it with: "
            f"{GENERATE_SECRET_HINT}"
        )

    warnings: list[str] = []
    if len(value) < MIN_SECRET_LENGTH:
        warnings.append(
            f"{env_var} is only {len(value)} characters long; "
            f"use at least {MIN_SECRET_LENGTH} for a signing key."
        )
    return warnings


def _join(parts: tuple[object, ...]) -> str:
    return "\n".join("" if part is None else str(part) for part in parts)


def sign(secret: str, *parts: object) -> str:
    """HMAC-SHA256 over newline-joined parts. Parts are never user separated."""
    return hmac.new(
        (secret or "").encode("utf-8"),
        _join(parts).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signature_matches(expected: str, provided: str | None) -> bool:
    return hmac.compare_digest(expected, str(provided or ""))


def coerce_expiry(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_expired(exp: int, *, now: float | None = None) -> bool:
    return int(exp) < int(now if now is not None else time.time())


def pack_token(*parts: object) -> str:
    """Encode parts into an opaque, cookie-safe string."""
    return base64.urlsafe_b64encode(_join(parts).encode("utf-8")).decode("ascii").rstrip("=")


def unpack_token(value: str | None, field_count: int) -> tuple[str, ...] | None:
    """Reverse `pack_token`. Returns None for anything malformed.

    The field count must match exactly, so a token from one family (say the
    5-field management cookie) can never be parsed as another (the 4-field
    history cookie). No field may contain a newline.
    """
    if not value or field_count < 1:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return None

    fields = decoded.split("\n")
    if len(fields) != field_count:
        return None
    return tuple(fields)
