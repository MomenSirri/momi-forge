from __future__ import annotations

import html
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

import gradio as gr
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from auth_service import COMPANY_DOMAIN, get_auth_service
import portal_auth


logger = logging.getLogger(__name__)

APP_PUBLIC_HOST = os.getenv("APP_PUBLIC_HOST", "").strip()
SPLASH_ASSETS_DIR = Path(__file__).resolve().parent / "splash_assets"
HISTORY_PORTAL_URL = os.getenv(
    "HISTORY_PORTAL_URL",
    "http://localhost:8199",
).strip()
HISTORY_PORTAL_PROXY_PATH = os.getenv(
    "HISTORY_PORTAL_PROXY_PATH",
    "/history-proxy",
).strip() or "/history-proxy"
HISTORY_PORTAL_USE_PROXY = os.getenv(
    "HISTORY_PORTAL_USE_PROXY",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
HISTORY_PORTAL_SSO_SECRET = os.getenv(
    "HISTORY_PORTAL_SSO_SECRET",
    "",
).strip()
HISTORY_PORTAL_SSO_TTL_SECONDS = max(
    60,
    int(os.getenv("HISTORY_PORTAL_SSO_TTL_SECONDS", "900")),
)
HISTORY_PORTAL_COOKIE_NAME = "momi_history_portal"
RUNPOD_MANAGEMENT_PROXY_PATH = os.getenv(
    "RUNPOD_MANAGEMENT_PROXY_PATH",
    "/runpod-management",
).strip() or "/runpod-management"
RUNPOD_MANAGEMENT_URL = os.getenv(
    "RUNPOD_MANAGEMENT_URL",
    RUNPOD_MANAGEMENT_PROXY_PATH,
).strip()
RUNPOD_MANAGEMENT_DIST_DIR = Path(
    os.getenv(
        "RUNPOD_MANAGEMENT_DIST_DIR",
        str(
            Path(__file__).resolve().parent
            / "runpod_management"
            / "webapp"
            / "frontend"
            / "dist"
        ),
    )
)
RUNPOD_MANAGEMENT_API_UPSTREAM_URL = os.getenv(
    "RUNPOD_MANAGEMENT_API_UPSTREAM_URL",
    "https://127.0.0.1:8843",
).strip()
RUNPOD_MANAGEMENT_API_CA_BUNDLE_ENV = (
    "RUNPOD_MANAGEMENT_API_CA_BUNDLE"
)
RUNPOD_MANAGEMENT_API_CA_BUNDLE = os.getenv(
    RUNPOD_MANAGEMENT_API_CA_BUNDLE_ENV,
    str(Path(__file__).resolve().parent / "openssl" / "cert.pem"),
).strip()
RUNPOD_MANAGEMENT_COOKIE_NAME = "momi_runpod_management"
RUNPOD_MANAGEMENT_SSO_TTL_SECONDS = max(
    60,
    int(
        os.getenv(
            "RUNPOD_MANAGEMENT_SSO_TTL_SECONDS",
            str(HISTORY_PORTAL_SSO_TTL_SECONDS),
        )
    ),
)
RUNPOD_MANAGEMENT_ROLES = {"admin", "ex"}
ADMIN_ANALYTICS_ROLES = {"admin", "ex"}
RUNPOD_BILLING_EMAILS = {
    email
    for email in (
        item.strip().lower()
        for item in os.getenv(
            "RUNPOD_BILLING_EMAILS",
            "momen.sirri@brickvisual.com",
        ).split(",")
    )
    if email
}

auth_service = get_auth_service()
_warned_missing_ca_paths: set[str] = set()


def _resolve_runpod_management_ca_bundle() -> str:
    configured = (
        RUNPOD_MANAGEMENT_API_CA_BUNDLE
        or str(Path(__file__).resolve().parent / "openssl" / "cert.pem")
    )
    ca_path = Path(configured).expanduser()
    if ca_path.is_file():
        return str(ca_path)

    path_text = str(ca_path)
    message = (
        f"{RUNPOD_MANAGEMENT_API_CA_BUNDLE_ENV} points to a missing "
        f"CA bundle: {path_text}"
    )
    if path_text not in _warned_missing_ca_paths:
        logger.warning(message)
        _warned_missing_ca_paths.add(path_text)
    raise FileNotFoundError(message)


def _create_runpod_management_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=60.0,
        verify=_resolve_runpod_management_ca_bundle(),
    )


def _normalize_role(value: str | None) -> str:
    role = (value or "").strip().lower()
    return role if role in {"user", "admin", "ex"} else "user"


def _can_view_admin_analytics(role: str | None) -> bool:
    return _normalize_role(role) in ADMIN_ANALYTICS_ROLES


def _can_view_runpod_management(role: str | None) -> bool:
    return _normalize_role(role) in RUNPOD_MANAGEMENT_ROLES


def _can_view_runpod_billing(email: str | None) -> bool:
    return (email or "").strip().lower() in RUNPOD_BILLING_EMAILS


def _resolve_history_portal_base_url(request: gr.Request | None = None) -> str:
    if HISTORY_PORTAL_USE_PROXY:
        return _normalized_history_proxy_path()

    configured = (HISTORY_PORTAL_URL or "").strip()
    parsed = urlparse(configured if "://" in configured else f"http://{configured}")
    scheme = parsed.scheme or "http"
    port = parsed.port or 8199
    configured_host = (parsed.hostname or "127.0.0.1").strip()

    host_header = ""
    forwarded_host_header = ""
    forwarded_proto_header = ""
    if request is not None and getattr(request, "headers", None):
        host_header = str(request.headers.get("host", "")).strip()
        forwarded_host_header = str(request.headers.get("x-forwarded-host", "")).strip()
        forwarded_proto_header = str(request.headers.get("x-forwarded-proto", "")).strip()

    if forwarded_proto_header:
        scheme = forwarded_proto_header.split(",", 1)[0].strip() or scheme

    def _extract_host(raw_host: str) -> str:
        text = (raw_host or "").split(",", 1)[0].strip()
        if not text:
            return ""
        if text.startswith("[") and "]" in text:
            return text[1:text.index("]")]
        if text.count(":") == 1:
            return text.split(":", 1)[0].strip()
        return text

    request_host = _extract_host(forwarded_host_header) or _extract_host(host_header)
    public_host = _extract_host(APP_PUBLIC_HOST)

    def _is_local_or_placeholder(host_value: str) -> bool:
        normalized = (host_value or "").strip().lower()
        return normalized in {"", "0.0.0.0", "127.0.0.1", "localhost", "::1"}

    if request_host and not _is_local_or_placeholder(request_host):
        host = request_host
    elif public_host and not _is_local_or_placeholder(public_host):
        host = public_host
    elif configured_host and not _is_local_or_placeholder(configured_host):
        host = configured_host
    else:
        host = request_host or public_host or configured_host or "127.0.0.1"
        if _is_local_or_placeholder(host):
            host = "127.0.0.1"

    return f"{scheme}://{host}:{port}"


def _normalized_history_proxy_path() -> str:
    path = (HISTORY_PORTAL_PROXY_PATH or "/history-proxy").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/history-proxy"


def _normalized_runpod_management_proxy_path() -> str:
    path = (RUNPOD_MANAGEMENT_PROXY_PATH or "/runpod-management").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/runpod-management"


def _history_portal_upstream_base_url() -> str:
    configured = (HISTORY_PORTAL_URL or "").strip()
    parsed = urlparse(configured if "://" in configured else f"http://{configured}")
    scheme = (parsed.scheme or "http").strip().lower()
    port = parsed.port or 8199
    # Proxy always talks to local history server process to avoid LAN/firewall exposure.
    return f"{scheme}://127.0.0.1:{port}"


def _history_portal_url_signature(email: str, exp: int, nonce: str) -> str:
    """Signature scheme the upstream history server validates. Do not change."""
    return portal_auth.sign(HISTORY_PORTAL_SSO_SECRET, email, exp, nonce)


def _build_history_portal_sso_url(email: str | None, base_url: str | None = None) -> str:
    base_url = (base_url or HISTORY_PORTAL_URL).rstrip("/")
    normalized_email = (email or "").strip().lower()
    if not base_url:
        return ""
    if not normalized_email or not HISTORY_PORTAL_SSO_SECRET:
        return base_url

    exp = int(time.time()) + HISTORY_PORTAL_SSO_TTL_SECONDS
    nonce = secrets.token_urlsafe(12)
    query = urlencode(
        {
            "email": normalized_email,
            "exp": exp,
            "nonce": nonce,
            "sig": _history_portal_url_signature(normalized_email, exp, nonce),
        }
    )
    return f"{base_url}/?{query}"


def _verify_history_portal_url_token(
    email: str | None,
    exp: int | str | None,
    nonce: str | None,
    sig: str | None,
) -> str | None:
    """Return the signed-for email when the entry token is valid."""
    normalized_email = (email or "").strip().lower()
    exp_int = portal_auth.coerce_expiry(exp)
    if not normalized_email or exp_int is None or not nonce or not sig:
        return None
    if portal_auth.is_expired(exp_int):
        return None
    expected = _history_portal_url_signature(normalized_email, exp_int, str(nonce))
    return normalized_email if portal_auth.signature_matches(expected, sig) else None


def _history_portal_cookie_signature(email: str, exp: int, nonce: str) -> str:
    return portal_auth.sign(HISTORY_PORTAL_SSO_SECRET, "history-portal-session", email, exp, nonce)


def _issue_history_portal_cookie(email: str) -> str:
    normalized_email = (email or "").strip().lower()
    exp = int(time.time()) + HISTORY_PORTAL_SSO_TTL_SECONDS
    nonce = secrets.token_urlsafe(12)
    sig = _history_portal_cookie_signature(normalized_email, exp, nonce)
    return portal_auth.pack_token(normalized_email, exp, nonce, sig)


def _verify_history_portal_cookie(value: str | None) -> str | None:
    """Return the email carried by a valid, unexpired session cookie."""
    fields = portal_auth.unpack_token(value, 4)
    if fields is None:
        return None

    email, exp_text, nonce, sig = fields
    normalized_email = email.strip().lower()
    exp_int = portal_auth.coerce_expiry(exp_text)
    if not normalized_email or exp_int is None or portal_auth.is_expired(exp_int):
        return None
    expected = _history_portal_cookie_signature(normalized_email, exp_int, nonce)
    return normalized_email if portal_auth.signature_matches(expected, sig) else None


def _authorize_history_proxy_request(request: Request) -> str | None:
    """Authorize a proxied history request via entry token or session cookie."""
    query = request.query_params
    email = _verify_history_portal_url_token(
        query.get("email"),
        query.get("exp"),
        query.get("nonce"),
        query.get("sig"),
    )
    if email:
        return email

    return _verify_history_portal_cookie(request.cookies.get(HISTORY_PORTAL_COOKIE_NAME))


def _runpod_management_signature(email: str, role: str, exp: int, nonce: str) -> str:
    return portal_auth.sign(
        HISTORY_PORTAL_SSO_SECRET,
        "runpod-management",
        email,
        _normalize_role(role),
        exp,
        nonce,
    )


def _pack_runpod_management_cookie(email: str, role: str, exp: int, nonce: str, sig: str) -> str:
    return portal_auth.pack_token(email, _normalize_role(role), exp, nonce, sig)


def _unpack_runpod_management_cookie(value: str | None) -> tuple[str, str, int, str, str] | None:
    fields = portal_auth.unpack_token(value, 5)
    if fields is None:
        return None

    email, role, exp_text, nonce, sig = fields
    exp_int = portal_auth.coerce_expiry(exp_text)
    if exp_int is None:
        return None
    return email, _normalize_role(role), exp_int, nonce, sig


def _verify_runpod_management_token(
    email: str | None,
    role: str | None,
    exp: int | str | None,
    nonce: str | None,
    sig: str | None,
) -> bool:
    normalized_email = (email or "").strip().lower()
    normalized_role = _normalize_role(role)
    if not normalized_email or not _can_view_runpod_management(normalized_role) or not nonce or not sig:
        return False
    exp_int = portal_auth.coerce_expiry(exp)
    if exp_int is None or portal_auth.is_expired(exp_int):
        return False
    expected = _runpod_management_signature(normalized_email, normalized_role, exp_int, nonce)
    return portal_auth.signature_matches(expected, sig)


def _current_runpod_management_role(email: str | None) -> str | None:
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return None
    try:
        identity = auth_service.get_identity(normalized_email)
    except Exception:
        return None
    role = _normalize_role(getattr(identity, "role", None))
    return role if _can_view_runpod_management(role) else None


def _authorize_runpod_management_request(request: Request) -> tuple[str, str] | None:
    query = request.query_params
    email = query.get("email")
    role = query.get("role")
    exp = query.get("exp")
    nonce = query.get("nonce")
    sig = query.get("sig")
    if _verify_runpod_management_token(email, role, exp, nonce, sig):
        effective_role = _current_runpod_management_role(email)
        if effective_role:
            normalized_email = (email or "").strip().lower()
            exp_int = int(exp or 0)
            next_sig = _runpod_management_signature(normalized_email, effective_role, exp_int, nonce or "")
            return (
                _pack_runpod_management_cookie(normalized_email, effective_role, exp_int, nonce or "", next_sig),
                effective_role,
            )

    cookie_data = _unpack_runpod_management_cookie(request.cookies.get(RUNPOD_MANAGEMENT_COOKIE_NAME))
    if cookie_data:
        cookie_email, cookie_role, cookie_exp, cookie_nonce, cookie_sig = cookie_data
        if _verify_runpod_management_token(cookie_email, cookie_role, cookie_exp, cookie_nonce, cookie_sig):
            effective_role = _current_runpod_management_role(cookie_email)
            if effective_role:
                next_sig = _runpod_management_signature(cookie_email, effective_role, cookie_exp, cookie_nonce)
                return (
                    _pack_runpod_management_cookie(cookie_email, effective_role, cookie_exp, cookie_nonce, next_sig),
                    effective_role,
                )

    return None


def _build_runpod_management_url(email: str | None, role: str | None) -> str:
    configured = (RUNPOD_MANAGEMENT_URL or "").strip() or _normalized_runpod_management_proxy_path()
    if configured.startswith(("http://", "https://")):
        return configured

    base_url = configured.rstrip("/") or _normalized_runpod_management_proxy_path()
    normalized_email = (email or "").strip().lower()
    normalized_role = _normalize_role(role)
    if not normalized_email or not _can_view_runpod_management(normalized_role) or not HISTORY_PORTAL_SSO_SECRET:
        return f"{base_url}/"

    exp = int(time.time()) + RUNPOD_MANAGEMENT_SSO_TTL_SECONDS
    nonce = secrets.token_urlsafe(12)
    sig = _runpod_management_signature(normalized_email, normalized_role, exp, nonce)
    query = urlencode(
        {
            "email": normalized_email,
            "role": normalized_role,
            "exp": exp,
            "nonce": nonce,
            "sig": sig,
        }
    )
    return f"{base_url}/?{query}"


def _history_portal_html(portal_url: str | None = None) -> str:
    target_url = (portal_url or "").strip() or _normalized_history_proxy_path()
    safe_url = html.escape(target_url, quote=True)
    return f"""
    <div style="padding-top:24px;background:#030507;">
      <iframe
        id="momi-history-portal-frame"
        src="{safe_url}"
        title="Momi-AI History Portal"
        style="display:block;width:100%;height:calc(100vh - 220px);border:0;border-radius:12px;background:#030507;"
      ></iframe>
    </div>
    """


def _runpod_management_html(management_url: str | None = None) -> str:
    target_url = (management_url or "").strip() or _normalized_runpod_management_proxy_path()
    safe_url = html.escape(target_url, quote=True)
    return f"""
    <div class="runpod-management-embed">
      <iframe
        id="momi-runpod-management-frame"
        class="runpod-management-frame"
        src="{safe_url}"
        title="RunPod Management"
      ></iframe>
      <p class="runpod-management-fallback">
        If the management console does not appear, open it directly:
        <a href="{safe_url}" target="_blank" rel="noopener noreferrer">RunPod Management</a>
      </p>
    </div>
    """


def _require_portal_signing_secret() -> None:
    """Refuse to start when the portal proxies cannot be signed safely."""
    for warning in portal_auth.validate_signing_secret(HISTORY_PORTAL_SSO_SECRET):
        print(f"[momi] WARNING: {warning}")


def create_server_app(*, blocks: gr.Blocks) -> FastAPI:
    _require_portal_signing_secret()

    server_app = FastAPI()
    proxy_path = _normalized_history_proxy_path()
    upstream_base = _history_portal_upstream_base_url().rstrip("/")
    runpod_proxy_path = _normalized_runpod_management_proxy_path()
    runpod_api_upstream_base = (RUNPOD_MANAGEMENT_API_UPSTREAM_URL or "https://127.0.0.1:8843").rstrip("/")
    runpod_assets_dir = RUNPOD_MANAGEMENT_DIST_DIR / "assets"

    @server_app.middleware("http")
    async def _default_gradio_dark_theme(request: Request, call_next: Callable[[Request], Any]) -> Response:
        accept = request.headers.get("accept", "")
        wants_html = not accept or "text/html" in accept or "*/*" in accept
        if (
            request.method in {"GET", "HEAD"}
            and request.url.path == "/"
            and "__theme" not in request.query_params
            and wants_html
        ):
            return RedirectResponse(str(request.url.include_query_params(__theme="dark")), status_code=307)
        return await call_next(request)

    if SPLASH_ASSETS_DIR.is_dir():
        server_app.mount("/splash-assets", StaticFiles(directory=str(SPLASH_ASSETS_DIR)), name="splash-assets")
    if runpod_assets_dir.is_dir():
        server_app.mount(
            f"{runpod_proxy_path}/assets",
            StaticFiles(directory=str(runpod_assets_dir)),
            name="runpod-management-assets",
        )

    hop_by_hop_headers = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }

    @server_app.api_route(proxy_path, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    @server_app.api_route(f"{proxy_path}/{{proxy_path_tail:path}}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def _history_proxy(request: Request, proxy_path_tail: str = "") -> Response:
        # These routes are siblings of the mounted Gradio app, so Gradio's auth
        # callback never runs for them. Authorize every request explicitly.
        access_email = _authorize_history_proxy_request(request)
        if not access_email:
            return Response(
                content="History portal requires an active session. Reopen the History tab.",
                status_code=403,
                media_type="text/plain; charset=utf-8",
            )

        target_path = f"/{(proxy_path_tail or '').lstrip('/')}"
        target_url = f"{upstream_base}{target_path}"
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        forward_headers: dict[str, str] = {}
        for key, value in request.headers.items():
            key_lower = key.lower()
            if key_lower in hop_by_hop_headers or key_lower == "host":
                continue
            forward_headers[key] = value

        body = await request.body()

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                upstream_response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=forward_headers,
                    content=body if body else None,
                )
        except httpx.HTTPError as error:
            return Response(
                content=f"History upstream unavailable: {error}",
                status_code=502,
                media_type="text/plain; charset=utf-8",
            )

        response_headers: dict[str, str] = {}
        for key, value in upstream_response.headers.items():
            if key.lower() in hop_by_hop_headers:
                continue
            response_headers[key] = value

        response = Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )
        # Refresh the session on every authorized request so an open History tab
        # keeps working past the entry token's TTL.
        response.set_cookie(
            HISTORY_PORTAL_COOKIE_NAME,
            _issue_history_portal_cookie(access_email),
            max_age=HISTORY_PORTAL_SSO_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            path=proxy_path,
        )
        return response

    @server_app.get(runpod_proxy_path)
    @server_app.get(f"{runpod_proxy_path}/")
    async def _runpod_management_index(request: Request) -> Response:
        access = _authorize_runpod_management_request(request)
        if not access:
            return Response(
                content="RunPod Management requires an active management session.",
                status_code=403,
                media_type="text/plain; charset=utf-8",
            )
        access_cookie, _access_role = access

        index_file = RUNPOD_MANAGEMENT_DIST_DIR / "index.html"
        if not index_file.is_file():
            return Response(
                content=(
                    "RunPod Management build was not found. "
                    f"Expected index file: {index_file}"
                ),
                status_code=502,
                media_type="text/plain; charset=utf-8",
            )

        index_html = index_file.read_text(encoding="utf-8")
        asset_prefix = f"{runpod_proxy_path}/assets/"
        index_html = (
            index_html
            .replace('src="/assets/', f'src="{asset_prefix}')
            .replace('href="/assets/', f'href="{asset_prefix}')
        )
        response = Response(content=index_html, media_type="text/html; charset=utf-8")
        response.set_cookie(
            RUNPOD_MANAGEMENT_COOKIE_NAME,
            access_cookie,
            max_age=RUNPOD_MANAGEMENT_SSO_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    @server_app.api_route("/api/{runpod_api_tail:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def _runpod_api_proxy(request: Request, runpod_api_tail: str = "") -> Response:
        access = _authorize_runpod_management_request(request)
        if not access:
            return Response(
                content="RunPod Management API requires an active management session.",
                status_code=403,
                media_type="text/plain; charset=utf-8",
            )
        _access_cookie, access_role = access

        target_path = f"/api/{(runpod_api_tail or '').lstrip('/')}"
        target_url = f"{runpod_api_upstream_base}{target_path}"
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        forward_headers: dict[str, str] = {}
        for key, value in request.headers.items():
            key_lower = key.lower()
            if key_lower in hop_by_hop_headers or key_lower == "host":
                continue
            forward_headers[key] = value
        forward_headers["x-user-role"] = access_role

        body = await request.body()

        try:
            async with _create_runpod_management_client() as client:
                upstream_response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=forward_headers,
                    content=body if body else None,
                )
        except httpx.HTTPError as error:
            return Response(
                content=f"RunPod Management upstream unavailable: {error}",
                status_code=502,
                media_type="text/plain; charset=utf-8",
            )

        response_headers: dict[str, str] = {}
        for key, value in upstream_response.headers.items():
            if key.lower() in hop_by_hop_headers:
                continue
            response_headers[key] = value

        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    gr.mount_gradio_app(
        app=server_app,
        blocks=blocks,
        path="/",
        auth=auth_service.authenticate,
        auth_message=f"BrickVisual internal access only. Use your @{COMPANY_DOMAIN} email credentials.",
    )

    return server_app
