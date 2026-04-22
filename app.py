from __future__ import annotations

import base64
from datetime import datetime, timezone
import html
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse
from typing import Any, Callable

# Reduce Gradio/HuggingFace telemetry chatter unless explicitly overridden.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import httpx
import gradio as gr
import plotly.graph_objects as go
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from General_Enhancement_v04 import General_Enhancement_interface
from analytics_store import get_analytics_store
from auth_service import COMPANY_DOMAIN, get_auth_service
from server_upscaler_with_flux_enhancement import fivek

APP_TITLE = "Momi-AI"
APP_DEBUG = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_QUIET = os.getenv("APP_QUIET", "1").strip().lower() in {"1", "true", "yes", "on"}
APP_SERVER_NAME = os.getenv("APP_SERVER_NAME", "0.0.0.0")
APP_SERVER_PORT = int(os.getenv("APP_SERVER_PORT", "8188"))
APP_PUBLIC_HOST = os.getenv("APP_PUBLIC_HOST", "").strip()
APP_SSL_ENABLE_MODE = os.getenv("APP_SSL_ENABLE", "auto").strip().lower()
APP_SSL_CERTFILE = os.getenv("APP_SSL_CERTFILE", "").strip()
APP_SSL_KEYFILE = os.getenv("APP_SSL_KEYFILE", "").strip()
APP_SSL_KEYFILE_PASSWORD = os.getenv("APP_SSL_KEYFILE_PASSWORD", "").strip()
SPLASH_LOTTIE_IFRAME_SRC = os.getenv(
    "SPLASH_LOTTIE_IFRAME_SRC",
    "/splash-assets/player.html",
).strip()
SPLASH_ASSETS_DIR = Path(__file__).resolve().parent / "splash_assets"

HISTORY_PORTAL_URL = os.getenv("HISTORY_PORTAL_URL", "http://localhost:8199").strip()
HISTORY_PORTAL_PROXY_PATH = os.getenv("HISTORY_PORTAL_PROXY_PATH", "/history-proxy").strip() or "/history-proxy"
HISTORY_PORTAL_USE_PROXY = os.getenv("HISTORY_PORTAL_USE_PROXY", "1").strip().lower() in {"1", "true", "yes", "on"}
HISTORY_PORTAL_SSO_SECRET = os.getenv("HISTORY_PORTAL_SSO_SECRET", "momi-forge-local-sso-secret").strip()
HISTORY_PORTAL_SSO_TTL_SECONDS = max(60, int(os.getenv("HISTORY_PORTAL_SSO_TTL_SECONDS", "900")))

ADMIN_OVERVIEW_DAYS = max(1, int(os.getenv("APP_ADMIN_OVERVIEW_DAYS", "30")))
ADMIN_TABLE_LIMIT = max(5, int(os.getenv("APP_ADMIN_TABLE_LIMIT", "25")))
ADMIN_DASHBOARD_TABLE_LIMIT = max(20, int(os.getenv("APP_ADMIN_DASHBOARD_TABLE_LIMIT", "120")))
ADMIN_DATE_RANGE_CHOICES = [
    ("Last 24h", "1"),
    ("7 Days", "7"),
    ("30 Days", "30"),
]
ADMIN_DATE_RANGE_VALUES = {value for _, value in ADMIN_DATE_RANGE_CHOICES}
DEFAULT_ADMIN_DATE_RANGE = str(ADMIN_OVERVIEW_DAYS)
if DEFAULT_ADMIN_DATE_RANGE not in ADMIN_DATE_RANGE_VALUES:
    DEFAULT_ADMIN_DATE_RANGE = "30"

WORKFLOW_DISPLAY_ALIASES: dict[str, str] = {
    "myotherworkflow": "Pro Upscaler",
    "5kupscale": "Pro Upscaler",
    "5kupscalerflux": "Pro Upscaler",
    "proupscaler": "Pro Upscaler",
    "generalenhancementv04": "General Enhancement",
    "generalenhancement": "General Enhancement",
}

WORKFLOW_HEADERS = ["Workflow", "Tasks", "Completed", "Failed", "Avg Duration (ms)"]
USER_HEADERS = ["User", "Name", "Tasks", "Failed", "Avg Duration (ms)"]
FAILURE_HEADERS = [
    "Created (UTC)",
    "User",
    "Workflow",
    "Reason",
    "Error",
    "Task ID",
    "Request ID",
]

store = get_analytics_store()
auth_service = get_auth_service()

EMBEDDED_HIDE_CSS = """
.is-embedded .main-tabs {
  margin-top: 0 !important;
}

.is-embedded .embedded-hide-logout {
  display: none !important;
}

.app-shell-header {
  margin: 0 !important;
  padding: 0 !important;
}

.app-shell-header .app-shell-header-inner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  width: 100%;
  padding: 12px 24px;
  margin: 0;
  background: #05070a;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
}

.app-shell-header .app-brand {
  font-size: 26px;
  font-weight: 800;
  line-height: 1.1;
  color: #f5f7fb;
  letter-spacing: -0.02em;
}

.app-shell-header .app-user-group {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.app-shell-header .app-user-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  object-fit: cover;
  border: 1px solid rgba(255, 255, 255, 0.1);
  display: block;
}

.app-shell-header .app-user-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.2;
}

.app-shell-header .app-user-name {
  font-size: 14px;
  font-weight: 700;
  color: #f7f9fc;
}

.app-shell-header .app-user-email {
  font-size: 12px;
  color: #9aa3b1;
}

.app-shell-header .app-logout-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 8px;
  padding: 6px 16px;
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  transition: border-color 0.2s ease, color 0.2s ease;
}

.app-shell-header .app-logout-btn:hover {
  border-color: #ff9b3d;
  color: #ff9b3d;
}

.main-tabs {
  margin-top: 0 !important;
}

.admin-dashboard-shell {
  gap: 20px !important;
  margin-top: 10px;
}

.admin-dashboard-controls {
  align-items: end !important;
  gap: 12px !important;
}

.admin-dashboard-controls > div {
  background: rgba(16, 20, 26, 0.68);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  backdrop-filter: blur(8px);
}

.admin-refresh-btn {
  align-self: stretch !important;
}

.admin-refresh-btn button {
  height: 40px !important;
  margin-top: auto !important;
  border-radius: 10px !important;
}

.admin-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
}

.admin-kpi-card {
  background: rgba(16, 20, 26, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  padding: 14px 16px;
  backdrop-filter: blur(10px);
  box-shadow: 0 0 24px rgba(39, 104, 201, 0.09);
}

.admin-kpi-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.admin-kpi-label {
  font-size: 11px;
  color: #8f9aad;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.admin-kpi-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: #dbe4f3;
  font-size: 12px;
}

.admin-kpi-value {
  margin-top: 8px;
  font-size: 30px;
  font-weight: 800;
  line-height: 1.1;
  color: #f7f9fd;
}

.admin-kpi-sub {
  margin-top: 6px;
  font-size: 12px;
  color: #a9b3c4;
}

.admin-kpi-sub.is-good {
  color: #6ce6a5;
}

.admin-kpi-sub.is-bad {
  color: #ff8b95;
}

.admin-chart-card {
  background: rgba(16, 20, 26, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  padding: 6px;
  backdrop-filter: blur(8px);
}

.admin-table-card {
  background: rgba(16, 20, 26, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px;
  padding: 14px;
  backdrop-filter: blur(10px);
}

.admin-table-title {
  margin: 0 0 10px 0;
  font-size: 14px;
  font-weight: 700;
  color: #e7edf8;
}

.admin-table-wrap {
  max-height: 360px;
  overflow: auto;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
}

.admin-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.admin-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #10141a;
  color: #d5dfef;
  text-align: left;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.admin-table th,
.admin-table td {
  padding: 10px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  vertical-align: top;
}

.admin-table td {
  color: #c7d0df;
}

.admin-table tr:last-child td {
  border-bottom: none;
}

.admin-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 11px;
  color: #d7e2f5;
}

.admin-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.2;
}

.admin-badge.success {
  background: rgba(76, 217, 147, 0.18);
  color: #7cf1b6;
  border: 1px solid rgba(76, 217, 147, 0.38);
}

.admin-badge.error {
  background: rgba(255, 106, 130, 0.15);
  color: #ff9eb0;
  border: 1px solid rgba(255, 106, 130, 0.35);
}

.admin-status-line {
  color: #9db0ca;
  margin: 0;
  font-size: 13px;
}

.admin-empty {
  color: #8f9aad;
  padding: 14px;
  text-align: center;
}

.momi-splash-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(circle at 20% 20%, rgba(0, 214, 255, 0.14), transparent 45%),
    radial-gradient(circle at 80% 30%, rgba(65, 112, 255, 0.18), transparent 50%),
    linear-gradient(180deg, #03070f 0%, #050913 100%);
  opacity: 1;
  visibility: visible;
  animation: momi-splash-fadeout 0.55s ease 3.2s forwards;
  transition: opacity 0.45s ease, visibility 0.45s ease;
}

.momi-splash-overlay.is-hidden {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}

.momi-splash-card {
  min-width: 280px;
  max-width: 540px;
  padding: 28px 30px 22px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(7, 12, 22, 0.86);
  box-shadow:
    0 18px 64px rgba(0, 0, 0, 0.45),
    inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(8px);
  text-align: center;
}

.momi-splash-brand {
  margin: 0;
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 34px;
  font-weight: 800;
  line-height: 1.1;
  letter-spacing: -0.02em;
  color: #f6f8ff;
}

.momi-splash-sub {
  margin: 8px 0 0;
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  font-size: 14px;
  color: #9db0ca;
}

.momi-splash-lottie-wrap {
  width: 220px;
  height: 220px;
  margin: 10px auto 8px;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(5, 8, 14, 0.65);
}

.momi-splash-lottie {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
  pointer-events: none;
}

.momi-splash-loader {
  width: 42px;
  height: 42px;
  margin: 18px auto 0;
  border-radius: 50%;
  border: 3px solid rgba(79, 112, 255, 0.25);
  border-top-color: #66d7ff;
  animation: momi-spin 0.9s linear infinite;
}

@keyframes momi-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes momi-splash-fadeout {
  to {
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
  }
}

@media (max-width: 1100px) {
  .admin-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 800px) {
  .admin-dashboard-controls {
    flex-wrap: wrap;
  }

  .admin-kpi-grid {
    grid-template-columns: 1fr;
  }
}
"""


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_workflow_alias_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _format_workflow_display_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unknown Workflow"

    alias = WORKFLOW_DISPLAY_ALIASES.get(_normalize_workflow_alias_key(raw))
    if alias:
        return alias

    stripped = re.sub(r"(?:[_\s-]+v\d+)$", "", raw, flags=re.IGNORECASE).strip()
    alias = WORKFLOW_DISPLAY_ALIASES.get(_normalize_workflow_alias_key(stripped))
    if alias:
        return alias

    normalized = re.sub(r"[_\-]+", " ", stripped)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return "Unknown Workflow"

    words: list[str] = []
    for token in normalized.split(" "):
        t = token.strip()
        if not t:
            continue
        if re.fullmatch(r"[A-Z0-9]{2,4}", t):
            words.append(t)
        elif re.fullmatch(r"[a-z0-9]{2,4}", t):
            words.append(t.upper())
        else:
            words.append(t[:1].upper() + t[1:].lower())
    return " ".join(words) or "Unknown Workflow"


def _merge_workflow_rows_by_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        display_name = _format_workflow_display_name(row.get("workflow_name"))
        entry = merged.setdefault(
            display_name,
            {
                "workflow_name": display_name,
                "total_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "_duration_weighted_sum": 0.0,
                "_duration_weight": 0.0,
            },
        )

        total_tasks = int(row.get("total_tasks") or 0)
        completed_tasks = int(row.get("completed_tasks") or 0)
        failed_tasks = int(row.get("failed_tasks") or 0)
        avg_ms = row.get("avg_total_duration_ms")
        try:
            avg_ms_value = float(avg_ms) if avg_ms is not None else 0.0
        except (TypeError, ValueError):
            avg_ms_value = 0.0

        entry["total_tasks"] += total_tasks
        entry["completed_tasks"] += completed_tasks
        entry["failed_tasks"] += failed_tasks

        weight = float(total_tasks if total_tasks > 0 else (1 if avg_ms is not None else 0))
        if weight > 0:
            entry["_duration_weighted_sum"] += avg_ms_value * weight
            entry["_duration_weight"] += weight

    result: list[dict[str, Any]] = []
    for item in merged.values():
        weight = float(item.pop("_duration_weight", 0.0) or 0.0)
        weighted_sum = float(item.pop("_duration_weighted_sum", 0.0) or 0.0)
        item["avg_total_duration_ms"] = int(round(weighted_sum / weight)) if weight > 0 else 0
        result.append(item)

    result.sort(key=lambda row: (-int(row.get("total_tasks") or 0), str(row.get("workflow_name") or "")))
    return result


def _avatar_data_uri(avatar_path: str | None, display_name: str) -> str:
    path = Path(str(avatar_path or "")).expanduser()
    if path.is_file():
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        try:
            raw = path.read_bytes()
            encoded = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        except OSError:
            pass

    initial = (display_name.strip()[:1] or "?").upper()
    placeholder_svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='80' height='80' viewBox='0 0 80 80'>"
        "<rect width='80' height='80' fill='#1a2330'/>"
        f"<text x='50%' y='50%' dominant-baseline='central' text-anchor='middle' "
        "font-family='Inter,Segoe UI,sans-serif' font-size='32' font-weight='700' fill='#e8eef8'>"
        f"{html.escape(initial)}</text></svg>"
    )
    encoded_svg = base64.b64encode(placeholder_svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded_svg}"


def _topbar_html(email: str, display_name: str, avatar_path: str | None) -> str:
    safe_name = html.escape(display_name)
    safe_email = html.escape(email)
    safe_avatar = html.escape(_avatar_data_uri(avatar_path, display_name), quote=True)
    return (
        "<div class='app-shell-header-inner'>"
        f"<div class='app-brand'>{APP_TITLE}</div>"
        "<div class='app-user-group'>"
        f"<img class='app-user-avatar' src='{safe_avatar}' alt='User avatar'>"
        "<div class='app-user-meta'>"
        f"<div class='app-user-name'>{safe_name}</div>"
        f"<div class='app-user-email'>{safe_email}</div>"
        "</div>"
        "<a href='/logout' class='app-logout-btn embedded-hide-logout'>Logout</a>"
        "</div>"
        "</div>"
    )


def _embedded_mode_detector_html() -> str:
    return """
    <script>
    (function () {
      const isEmbedded = (() => {
        try {
          return window.self !== window.top;
        } catch (_error) {
          return true;
        }
      })();

      if (!isEmbedded) {
        return;
      }

      const applyEmbeddedUiState = () => {
        document.documentElement.classList.add("is-embedded");
        if (document.body) {
          document.body.classList.add("is-embedded");
        }

        document.querySelectorAll("button, a").forEach((element) => {
          const text = (element.textContent || "").trim().toLowerCase();
          if (text === "logout") {
            element.classList.add("embedded-hide-logout");
          }
        });
      };

      applyEmbeddedUiState();

      const observer = new MutationObserver(() => {
        applyEmbeddedUiState();
      });
      observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
      });

      window.addEventListener("beforeunload", () => observer.disconnect(), { once: true });
    })();
    </script>
    """


def _app_splash_html() -> str:
    lottie_src = (SPLASH_LOTTIE_IFRAME_SRC or "").strip()
    if lottie_src.startswith("/splash-assets/player.html"):
        player_file = SPLASH_ASSETS_DIR / "player.html"
        if player_file.is_file():
            cache_buster = int(player_file.stat().st_mtime)
            separator = "&" if "?" in lottie_src else "?"
            lottie_src = f"{lottie_src}{separator}v={cache_buster}"

    lottie_embed = html.escape(lottie_src, quote=True)
    lottie_html = ""
    loader_html = "<div class=\"momi-splash-loader\"></div>"
    if lottie_embed:
        lottie_html = f"""
        <div class="momi-splash-lottie-wrap" aria-hidden="true">
          <iframe
            class="momi-splash-lottie"
            src="{lottie_embed}"
            loading="eager"
            referrerpolicy="no-referrer"
            allowfullscreen
          ></iframe>
        </div>
        """
        loader_html = ""

    return f"""
    <div id="momi-splash" class="momi-splash-overlay" aria-live="polite" aria-label="Loading Momi-AI">
      <div class="momi-splash-card">
        <h1 class="momi-splash-brand">Momi-AI</h1>
        <p class="momi-splash-sub">Preparing your workspace...</p>
        {lottie_html}
        {loader_html}
      </div>
    </div>
    """


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


def _history_portal_upstream_base_url() -> str:
    configured = (HISTORY_PORTAL_URL or "").strip()
    parsed = urlparse(configured if "://" in configured else f"http://{configured}")
    scheme = (parsed.scheme or "http").strip().lower()
    port = parsed.port or 8199
    # Proxy always talks to local history server process to avoid LAN/firewall exposure.
    return f"{scheme}://127.0.0.1:{port}"


def _build_history_portal_sso_url(email: str | None, base_url: str | None = None) -> str:
    base_url = (base_url or HISTORY_PORTAL_URL).rstrip("/")
    normalized_email = (email or "").strip().lower()
    if not base_url:
        return ""
    if not normalized_email or not HISTORY_PORTAL_SSO_SECRET:
        return base_url

    exp = int(time.time()) + HISTORY_PORTAL_SSO_TTL_SECONDS
    nonce = secrets.token_urlsafe(12)
    payload = f"{normalized_email}\n{exp}\n{nonce}"
    sig = hmac.new(
        HISTORY_PORTAL_SSO_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    query = urlencode(
        {
            "email": normalized_email,
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


def _resolve_ssl_paths() -> tuple[str, str]:
    cert_candidate = APP_SSL_CERTFILE
    key_candidate = APP_SSL_KEYFILE
    if cert_candidate and key_candidate:
        return cert_candidate, key_candidate

    openssl_dir = Path(__file__).resolve().parent / "openssl"
    cert_default = openssl_dir / "cert.pem"
    key_default = openssl_dir / "key.pem"
    if cert_default.is_file() and key_default.is_file():
        return str(cert_default), str(key_default)

    return cert_candidate, key_candidate


def _resolve_uvicorn_ssl_kwargs() -> tuple[dict[str, Any], bool]:
    cert_path, key_path = _resolve_ssl_paths()
    mode = APP_SSL_ENABLE_MODE

    ssl_allowed_modes = {"auto", "1", "true", "yes", "on", "0", "false", "no", "off"}
    if mode not in ssl_allowed_modes:
        mode = "auto"

    ssl_disabled = mode in {"0", "false", "no", "off"}
    ssl_forced = mode in {"1", "true", "yes", "on"}

    cert_exists = bool(cert_path) and Path(cert_path).is_file()
    key_exists = bool(key_path) and Path(key_path).is_file()
    ssl_ready = cert_exists and key_exists

    if ssl_disabled:
        return {}, False

    if ssl_forced and not ssl_ready:
        raise FileNotFoundError(
            "HTTPS is enabled, but SSL certificate or key file is missing. "
            f"cert={cert_path or '<empty>'}, key={key_path or '<empty>'}"
        )

    if not ssl_ready:
        return {}, False

    kwargs: dict[str, Any] = {
        "ssl_certfile": cert_path,
        "ssl_keyfile": key_path,
    }
    if APP_SSL_KEYFILE_PASSWORD:
        kwargs["ssl_keyfile_password"] = APP_SSL_KEYFILE_PASSWORD
    return kwargs, True


def _admin_summary_html(summary: dict[str, Any], window_days: int) -> str:
    total = int(summary.get("total_tasks") or 0)
    completed = int(summary.get("completed_tasks") or 0)
    failed = int(summary.get("failed_tasks") or 0)
    success_rate = float(summary.get("success_rate_percent") or 0.0)
    avg_duration = summary.get("avg_total_duration_ms")
    avg_text = _safe_text(avg_duration if avg_duration is not None else "-")

    return (
        "<div style='display:flex;flex-wrap:wrap;gap:12px;'>"
        f"<div><b>Window:</b> last {window_days} day(s)</div>"
        f"<div><b>Total Tasks:</b> {total}</div>"
        f"<div><b>Completed:</b> {completed}</div>"
        f"<div><b>Failed:</b> {failed}</div>"
        f"<div><b>Success Rate:</b> {success_rate:.2f}%</div>"
        f"<div><b>Avg Duration:</b> {avg_text} ms</div>"
        "</div>"
    )


def _overview_tables(overview: dict[str, Any]) -> tuple[list[list[Any]], list[list[Any]], list[list[Any]]]:
    wf_rows = [
        [
            _safe_text(_format_workflow_display_name(row.get("workflow_name"))),
            int(row.get("total_tasks") or 0),
            int(row.get("completed_tasks") or 0),
            int(row.get("failed_tasks") or 0),
            int(row.get("avg_total_duration_ms") or 0),
        ]
        for row in overview.get("top_workflows", [])
    ]

    user_rows = [
        [
            _safe_text(row.get("user_email")),
            _safe_text(row.get("user_display_name")),
            int(row.get("total_tasks") or 0),
            int(row.get("failed_tasks") or 0),
            int(row.get("avg_total_duration_ms") or 0),
        ]
        for row in overview.get("top_users", [])
    ]

    failure_rows = [
        [
            _safe_text(row.get("submitted_at")),
            _safe_text(row.get("user_email")),
            _safe_text(_format_workflow_display_name(row.get("workflow_name"))),
            _safe_text(row.get("failure_reason")),
            _safe_text(row.get("error_message")),
            _safe_text(row.get("task_id")),
            _safe_text(row.get("request_id")),
        ]
        for row in overview.get("recent_failures", [])
    ]

    return wf_rows, user_rows, failure_rows


def _coerce_days(value: str | int | None) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        parsed = ADMIN_OVERVIEW_DAYS
    return max(1, parsed)


def _format_duration(ms: Any) -> str:
    try:
        value = int(ms or 0)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    if value < 1000:
        return f"{value} ms"
    return f"{value / 1000:.2f} s"


def _format_admin_dt(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _build_kpi_cards_html(summary: dict[str, Any], *, days: int) -> str:
    total_tasks = int(summary.get("total_tasks") or 0)
    success_rate = float(summary.get("success_rate_percent") or 0.0)
    avg_duration = _format_duration(summary.get("avg_total_duration_ms"))
    active_users = int(summary.get("active_users") or 0)
    success_class = "is-good" if success_rate >= 90 else "is-bad"

    return f"""
    <div class="admin-kpi-grid">
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Total Tasks</div>
          <div class="admin-kpi-icon" aria-hidden="true">◉</div>
        </div>
        <div class="admin-kpi-value">{total_tasks}</div>
        <div class="admin-kpi-sub">Window: last {days} day(s)</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Success Rate</div>
          <div class="admin-kpi-icon" aria-hidden="true">✓</div>
        </div>
        <div class="admin-kpi-value">{success_rate:.2f}%</div>
        <div class="admin-kpi-sub {success_class}">{'Healthy' if success_rate >= 90 else 'Needs attention'}</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Avg Duration</div>
          <div class="admin-kpi-icon" aria-hidden="true">⏱</div>
        </div>
        <div class="admin-kpi-value">{html.escape(avg_duration)}</div>
        <div class="admin-kpi-sub">Across all workflows</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Active Users</div>
          <div class="admin-kpi-icon" aria-hidden="true">👤</div>
        </div>
        <div class="admin-kpi-value">{active_users}</div>
        <div class="admin-kpi-sub">Distinct creators</div>
      </div>
    </div>
    """


def _base_plot_layout(title: str) -> dict[str, Any]:
    return {
        "title": {"text": title, "font": {"size": 14, "color": "#e6edf8"}},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(16, 20, 26, 0.65)",
        "font": {"color": "#c9d3e3", "size": 12},
        "margin": {"l": 44, "r": 20, "t": 42, "b": 40},
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
    }


def _empty_admin_plots() -> tuple[None, None, None]:
    return (None, None, None)


def _safe_plot_render(plot_factory: Callable[[], go.Figure]) -> go.Figure | None:
    try:
        figure = plot_factory()
        # Gradio serializes Plot values through Plotly's JSON export path.
        # Validate here so runtime dependency issues do not break app loading.
        figure.to_json()
        return figure
    except Exception:
        return None


def _build_trend_plot(trend_rows: list[dict[str, Any]]) -> go.Figure:
    fig = go.Figure()
    if not trend_rows:
        fig.add_annotation(
            text="No task data in selected range",
            showarrow=False,
            font={"size": 13, "color": "#92a0b5"},
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
        )
        fig.update_layout(**_base_plot_layout("Tasks Over Time"))
        return fig

    x_values = [str(row.get("day") or "") for row in trend_rows]
    total_values = [int(row.get("total_tasks") or 0) for row in trend_rows]
    completed_values = [int(row.get("completed_tasks") or 0) for row in trend_rows]
    failed_values = [int(row.get("failed_tasks") or 0) for row in trend_rows]

    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=total_values,
            mode="lines+markers",
            name="Total Tasks",
            line={"color": "#ff9b3d", "width": 3},
            marker={"size": 6, "color": "#ff9b3d"},
            fill="tozeroy",
            fillcolor="rgba(255, 155, 61, 0.18)",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=completed_values,
            mode="lines",
            name="Completed",
            line={"color": "#47d793", "width": 2},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=failed_values,
            mode="lines",
            name="Failed",
            line={"color": "#ff6a82", "width": 2},
        )
    )
    fig.update_layout(**_base_plot_layout("Tasks Over Time"))
    fig.update_xaxes(showgrid=False, tickfont={"color": "#9fb0c8"})
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.08)", zeroline=False)
    return fig


def _build_workflow_distribution_plot(workflow_rows: list[dict[str, Any]]) -> go.Figure:
    fig = go.Figure()
    merged_rows = _merge_workflow_rows_by_display(workflow_rows)
    if not merged_rows:
        fig.add_annotation(
            text="No workflow usage yet",
            showarrow=False,
            font={"size": 13, "color": "#92a0b5"},
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
        )
        fig.update_layout(**_base_plot_layout("Workflow Distribution"))
        return fig

    labels = [str(row.get("workflow_name") or "Unknown") for row in merged_rows]
    values = [int(row.get("total_tasks") or 0) for row in merged_rows]
    palette = ["#ff9b3d", "#3fa9f5", "#47d793", "#a78bfa", "#f97316", "#22d3ee"]
    fig.add_trace(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.58,
            marker={"colors": palette},
            textinfo="percent",
            hovertemplate="%{label}<br>Tasks: %{value}<extra></extra>",
        )
    )
    fig.update_layout(**_base_plot_layout("Workflow Distribution"))
    return fig


def _build_performance_plot(workflow_rows: list[dict[str, Any]]) -> go.Figure:
    fig = go.Figure()
    merged_rows = _merge_workflow_rows_by_display(workflow_rows)
    if not merged_rows:
        fig.add_annotation(
            text="No duration samples yet",
            showarrow=False,
            font={"size": 13, "color": "#92a0b5"},
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
        )
        fig.update_layout(**_base_plot_layout("Avg Duration by Workflow"))
        return fig

    ranked = sorted(
        merged_rows,
        key=lambda row: int(row.get("avg_total_duration_ms") or 0),
        reverse=True,
    )[:8]
    x_values = [str(row.get("workflow_name") or "Unknown") for row in ranked]
    y_values = [round((int(row.get("avg_total_duration_ms") or 0) / 1000.0), 2) for row in ranked]

    fig.add_trace(
        go.Bar(
            x=x_values,
            y=y_values,
            marker={"color": "rgba(63,169,245,0.85)"},
            hovertemplate="%{x}<br>Avg: %{y} s<extra></extra>",
            name="Avg Duration (s)",
        )
    )
    fig.update_layout(**_base_plot_layout("Avg Duration by Workflow"))
    fig.update_xaxes(showgrid=False, tickangle=-18, tickfont={"color": "#9fb0c8"})
    fig.update_yaxes(title="Seconds", gridcolor="rgba(255,255,255,0.08)", zeroline=False)
    return fig


def _match_search(value: str, search_query: str) -> bool:
    if not search_query:
        return True
    return search_query in value.lower()


def _render_users_table_html(rows: list[dict[str, Any]], search_query: str) -> str:
    query = (search_query or "").strip().lower()
    filtered = [
        row for row in rows
        if _match_search(str(row.get("user_email") or ""), query)
        or _match_search(str(row.get("user_display_name") or ""), query)
    ]

    body = ""
    for row in filtered:
        total = int(row.get("total_tasks") or 0)
        failed = int(row.get("failed_tasks") or 0)
        fail_ratio = (failed / total) if total else 0.0
        health_badge = (
            "<span class='admin-badge success'>Healthy</span>"
            if fail_ratio < 0.2
            else "<span class='admin-badge error'>Alert</span>"
        )
        body += (
            "<tr>"
            f"<td class='admin-mono'>{html.escape(str(row.get('user_email') or '-'))}</td>"
            f"<td>{html.escape(str(row.get('user_display_name') or '-'))}</td>"
            f"<td>{int(row.get('total_tasks') or 0)}</td>"
            f"<td>{int(row.get('failed_tasks') or 0)}</td>"
            f"<td>{html.escape(_format_duration(row.get('avg_total_duration_ms')))}</td>"
            f"<td>{health_badge}</td>"
            "</tr>"
        )

    if not body:
        body = "<tr><td colspan='6' class='admin-empty'>No matching users.</td></tr>"

    return f"""
    <div class="admin-table-card">
      <h3 class="admin-table-title">Most Active Users</h3>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>User Email</th>
              <th>Name</th>
              <th>Tasks</th>
              <th>Failed</th>
              <th>Avg Duration</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </div>
    """


def _render_failures_table_html(rows: list[dict[str, Any]], search_query: str) -> str:
    query = (search_query or "").strip().lower()
    filtered = [
        row for row in rows
        if _match_search(str(row.get("user_email") or ""), query)
        or _match_search(str(row.get("workflow_name") or ""), query)
        or _match_search(_format_workflow_display_name(row.get("workflow_name")), query)
    ]

    body = ""
    for row in filtered:
        workflow_display = _format_workflow_display_name(row.get("workflow_name"))
        body += (
            "<tr>"
            f"<td>{html.escape(_format_admin_dt(row.get('submitted_at')))}</td>"
            f"<td class='admin-mono'>{html.escape(str(row.get('user_email') or '-'))}</td>"
            f"<td>{html.escape(workflow_display)}</td>"
            "<td><span class='admin-badge error'>Error</span></td>"
            f"<td>{html.escape(str(row.get('failure_reason') or '-'))}</td>"
            f"<td>{html.escape(str(row.get('error_message') or '-'))}</td>"
            f"<td class='admin-mono'>{html.escape(str(row.get('task_id') or '-'))}</td>"
            f"<td class='admin-mono'>{html.escape(str(row.get('request_id') or '-'))}</td>"
            "</tr>"
        )

    if not body:
        body = "<tr><td colspan='8' class='admin-empty'>No matching failures.</td></tr>"

    return f"""
    <div class="admin-table-card">
      <h3 class="admin-table-title">Recent Failures</h3>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Created</th>
              <th>User</th>
              <th>Workflow</th>
              <th>Status</th>
              <th>Reason</th>
              <th>Error</th>
              <th>Task ID</th>
              <th>Request ID</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </div>
    """


def _build_admin_dashboard(
    days: int, search_query: str
) -> tuple[str, str, go.Figure | None, go.Figure | None, go.Figure | None, str, str]:
    dashboard = store.get_admin_dashboard(days=days, limit=ADMIN_DASHBOARD_TABLE_LIMIT)
    summary = dashboard.get("summary", {})
    trend_rows = dashboard.get("trend", [])
    workflow_rows = dashboard.get("workflows", [])
    users_rows = dashboard.get("top_users", [])
    failures_rows = dashboard.get("recent_failures", [])
    trend_plot = _safe_plot_render(lambda: _build_trend_plot(trend_rows))
    workflow_plot = _safe_plot_render(lambda: _build_workflow_distribution_plot(workflow_rows))
    performance_plot = _safe_plot_render(lambda: _build_performance_plot(workflow_rows))

    if trend_plot is None or workflow_plot is None or performance_plot is None:
        status_text = (
            f"<p class='admin-status-line'>Admin analytics is active. Window: last {days} day(s). "
            "Chart rendering is currently unavailable on this server runtime.</p>"
        )
    else:
        status_text = f"<p class='admin-status-line'>Admin analytics is active. Window: last {days} day(s).</p>"

    return (
        status_text,
        _build_kpi_cards_html(summary, days=days),
        trend_plot,
        workflow_plot,
        performance_plot,
        _render_users_table_html(users_rows, search_query),
        _render_failures_table_html(failures_rows, search_query),
    )


def _load_portal_data(request: gr.Request):
    history_base_url = _resolve_history_portal_base_url(request)
    email = getattr(request, "username", None)
    if not email:
        trend_plot, workflow_plot, performance_plot = _empty_admin_plots()
        return (
            _topbar_html("-", "Unknown User", None),
            "<p class='admin-status-line'>Admin access is unavailable.</p>",
            "",
            trend_plot,
            workflow_plot,
            performance_plot,
            _render_users_table_html([], ""),
            _render_failures_table_html([], ""),
            _history_portal_html(history_base_url),
            gr.update(visible=False),
        )

    identity = auth_service.get_identity(email)
    history_url = _build_history_portal_sso_url(identity.email, history_base_url)

    is_admin = str(getattr(identity, "role", "") or "").strip().lower() == "admin"

    if is_admin:
        (
            admin_hint,
            admin_summary,
            trend_plot,
            workflow_plot,
            performance_plot,
            users_table_html,
            failures_table_html,
        ) = _build_admin_dashboard(ADMIN_OVERVIEW_DAYS, "")
    else:
        admin_hint = "<p class='admin-status-line'>Admin analytics is restricted to admin users.</p>"
        admin_summary = ""
        trend_plot, workflow_plot, performance_plot = _empty_admin_plots()
        users_table_html = _render_users_table_html([], "")
        failures_table_html = _render_failures_table_html([], "")

    return (
        _topbar_html(identity.email, identity.display_name, identity.avatar_path),
        admin_hint,
        admin_summary,
        trend_plot,
        workflow_plot,
        performance_plot,
        users_table_html,
        failures_table_html,
        _history_portal_html(history_url),
        gr.update(visible=is_admin),
    )


def _refresh_admin(date_range: str, search_query: str, request: gr.Request):
    email = getattr(request, "username", None)
    if not email:
        trend_plot, workflow_plot, performance_plot = _empty_admin_plots()
        return (
            "<p class='admin-status-line'>Admin access is unavailable.</p>",
            "",
            trend_plot,
            workflow_plot,
            performance_plot,
            _render_users_table_html([], ""),
            _render_failures_table_html([], ""),
        )

    identity = auth_service.get_identity(email)
    if str(getattr(identity, "role", "") or "").strip().lower() != "admin":
        trend_plot, workflow_plot, performance_plot = _empty_admin_plots()
        return (
            "<p class='admin-status-line'>Admin analytics is restricted to admin users.</p>",
            "",
            trend_plot,
            workflow_plot,
            performance_plot,
            _render_users_table_html([], ""),
            _render_failures_table_html([], ""),
        )

    days = _coerce_days(date_range)
    return _build_admin_dashboard(days, search_query or "")


with gr.Blocks(title=APP_TITLE, css=EMBEDDED_HIDE_CSS) as app:
    gr.HTML(_app_splash_html())
    gr.HTML(_embedded_mode_detector_html())

    user_header = gr.HTML(_topbar_html("-", "Loading", None), elem_classes=["app-shell-header"])

    with gr.Tabs(elem_classes=["main-tabs"]):
        with gr.Tab("General Enhancement"):
            General_Enhancement_interface.render()

        with gr.Tab("Pro Upscaler"):
            fivek.render()

        with gr.Tab("History"):
            history_portal_shell = gr.HTML(_history_portal_html())

        with gr.Tab("Admin Analytics", visible=False) as admin_tab:
            with gr.Column(elem_classes=["admin-dashboard-shell"]):
                with gr.Row(elem_classes=["admin-dashboard-controls"]):
                    admin_date_range = gr.Dropdown(
                        choices=ADMIN_DATE_RANGE_CHOICES,
                        value=DEFAULT_ADMIN_DATE_RANGE,
                        label="Date Range",
                    )
                    admin_search = gr.Textbox(
                        label="Search",
                        placeholder="Filter by user email or workflow...",
                    )
                    refresh_admin_btn = gr.Button(
                        "Refresh",
                        variant="secondary",
                        elem_classes=["admin-refresh-btn"],
                    )

                admin_status = gr.HTML("<p class='admin-status-line'>Loading admin analytics...</p>")
                admin_summary = gr.HTML("")

                with gr.Row():
                    tasks_trend_plot = gr.Plot(label="Tasks Over Time", elem_classes=["admin-chart-card"])

                with gr.Row():
                    workflow_distribution_plot = gr.Plot(
                        label="Workflow Distribution",
                        elem_classes=["admin-chart-card"],
                    )
                    performance_plot = gr.Plot(
                        label="Avg Duration by Workflow",
                        elem_classes=["admin-chart-card"],
                    )

                with gr.Row():
                    top_users_table = gr.HTML("")
                    recent_failures_table = gr.HTML("")

    app.load(
        fn=_load_portal_data,
        inputs=None,
        outputs=[
            user_header,
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            top_users_table,
            recent_failures_table,
            history_portal_shell,
            admin_tab,
        ],
        js="""
        () => {
          const splash = document.getElementById("momi-splash");
          if (!splash) {
            return;
          }
          splash.classList.add("is-hidden");
          window.setTimeout(() => {
            const current = document.getElementById("momi-splash");
            if (current) {
              current.remove();
            }
          }, 700);
        }
        """,
    )

    refresh_admin_btn.click(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            top_users_table,
            recent_failures_table,
        ],
    )

    admin_date_range.change(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            top_users_table,
            recent_failures_table,
        ],
    )

    admin_search.change(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            top_users_table,
            recent_failures_table,
        ],
    )


def _create_server_app() -> FastAPI:
    server_app = FastAPI()
    proxy_path = _normalized_history_proxy_path()
    upstream_base = _history_portal_upstream_base_url().rstrip("/")

    if SPLASH_ASSETS_DIR.is_dir():
        server_app.mount("/splash-assets", StaticFiles(directory=str(SPLASH_ASSETS_DIR)), name="splash-assets")

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

        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    gr.mount_gradio_app(
        app=server_app,
        blocks=app,
        path="/",
        auth=auth_service.authenticate,
        auth_message=f"BrickVisual internal access only. Use your @{COMPANY_DOMAIN} email credentials.",
    )

    return server_app


if __name__ == "__main__":
    server = _create_server_app()
    uvicorn_run_kwargs: dict[str, Any] = {
        "app": server,
        "host": APP_SERVER_NAME,
        "port": APP_SERVER_PORT,
        "log_level": "debug" if APP_DEBUG else ("warning" if APP_QUIET else "info"),
        "access_log": not APP_QUIET,
    }
    ssl_kwargs, ssl_enabled = _resolve_uvicorn_ssl_kwargs()
    uvicorn_run_kwargs.update(ssl_kwargs)
    if not APP_QUIET:
        print(f"[momi] HTTPS {'enabled' if ssl_enabled else 'disabled'} on {APP_SERVER_NAME}:{APP_SERVER_PORT}")
    uvicorn.run(**uvicorn_run_kwargs)
