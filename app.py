from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import html
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
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from General_Enhancement_v04 import General_Enhancement_interface
from analytics_store import get_analytics_store
from auth_service import COMPANY_DOMAIN, get_auth_service
from flux2_klein_image_edit_9b_distilled import flux2_klein_interface
import portal_auth
from reference_generator import reference_generator_interface
from runpod_status_gadget import (
    RUNPOD_STATUS_GADGET_REFRESH_S,
    build_placeholder_html,
    fetch_multiple_status_gadgets,
)
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
HISTORY_PORTAL_SSO_SECRET = os.getenv("HISTORY_PORTAL_SSO_SECRET", "").strip()
HISTORY_PORTAL_SSO_TTL_SECONDS = max(60, int(os.getenv("HISTORY_PORTAL_SSO_TTL_SECONDS", "900")))
HISTORY_PORTAL_COOKIE_NAME = "momi_history_portal"
RUNPOD_MANAGEMENT_PROXY_PATH = os.getenv("RUNPOD_MANAGEMENT_PROXY_PATH", "/runpod-management").strip() or "/runpod-management"
RUNPOD_MANAGEMENT_URL = os.getenv("RUNPOD_MANAGEMENT_URL", RUNPOD_MANAGEMENT_PROXY_PATH).strip()
RUNPOD_MANAGEMENT_DIST_DIR = Path(
    os.getenv(
        "RUNPOD_MANAGEMENT_DIST_DIR",
        str(Path(__file__).resolve().parent / "runpod_management" / "webapp" / "frontend" / "dist"),
    )
)
RUNPOD_MANAGEMENT_API_UPSTREAM_URL = os.getenv("RUNPOD_MANAGEMENT_API_UPSTREAM_URL", "https://127.0.0.1:8843").strip()
RUNPOD_MANAGEMENT_COOKIE_NAME = "momi_runpod_management"
RUNPOD_MANAGEMENT_SSO_TTL_SECONDS = max(60, int(os.getenv("RUNPOD_MANAGEMENT_SSO_TTL_SECONDS", str(HISTORY_PORTAL_SSO_TTL_SECONDS))))
RUNPOD_MANAGEMENT_ROLES = {"admin", "ex"}
ADMIN_ANALYTICS_ROLES = {"admin", "ex"}
RUNPOD_BILLING_EMAILS = {
    email
    for email in (
        item.strip().lower()
        for item in os.getenv("RUNPOD_BILLING_EMAILS", "momen.sirri@brickvisual.com").split(",")
    )
    if email
}
RUNPOD_REST_API_BASE = os.getenv("RUNPOD_REST_API_BASE", "https://rest.runpod.io/v1").strip().rstrip("/")
RUNPOD_GRAPHQL_API_URL = os.getenv("RUNPOD_GRAPHQL_API_URL", "https://api.runpod.io/graphql").strip()
RUNPOD_BILLING_TIMEOUT_S = float(os.getenv("RUNPOD_BILLING_TIMEOUT_S", "20"))
RUNPOD_BILLING_TABLE_LIMIT = max(5, int(os.getenv("RUNPOD_BILLING_TABLE_LIMIT", "20")))
RUNPOD_MONTHLY_BUDGET_USD = max(0.0, float(os.getenv("RUNPOD_MONTHLY_BUDGET_USD", "200")))

ADMIN_OVERVIEW_DAYS = max(1, int(os.getenv("APP_ADMIN_OVERVIEW_DAYS", "30")))
ADMIN_TABLE_LIMIT = max(5, int(os.getenv("APP_ADMIN_TABLE_LIMIT", "25")))
ADMIN_DASHBOARD_TABLE_LIMIT = max(20, int(os.getenv("APP_ADMIN_DASHBOARD_TABLE_LIMIT", "120")))
ADMIN_DATE_RANGE_CHOICES = [
    ("Last 24h", "1"),
    ("7 Days", "7"),
    ("30 Days", "30"),
    ("All Time", "all"),
]
ADMIN_AFTER_HOURS_GROUP_CHOICES = [
    ("Per Day", "day"),
    ("Per Week", "week"),
    ("Per Month", "month"),
]
RUNPOD_BILLING_DATE_RANGE_CHOICES = [
    ("Last 24h", "1"),
    ("7 Days", "7"),
    ("30 Days", "30"),
]
DEFAULT_ADMIN_AFTER_HOURS_GROUP = "day"
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
    "referencegenerator": "Reference Generator",
    "referencegeneratorv02": "Reference Generator",
    "reference_generator_v02": "Reference Generator",
    "flux2kleinimageedit9bdistilled": "Qwen Edit",
    "flux2kleinimageedit": "Qwen Edit",
    "flux2klein": "Qwen Edit",
    "flux2_klein": "Qwen Edit",
    "qwenedit": "Qwen Edit",
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
WORKFLOW_STATUS_CONFIGS: list[tuple[str, str]] = [
    ("General_Enhancement", "General Enhancement"),
    ("seed", "Pro Upscaler"),
    (
        os.getenv("REFERENCE_GENERATOR_RUNPOD_ENVIRONMENT", "reference_generator"),
        "Reference Generator",
    ),
    (os.getenv("FLUX2_KLEIN_RUNPOD_ENVIRONMENT", "flux2_klein"), "Qwen Edit"),
]
WORKFLOW_STATUS_REFRESH_TRIGGER_ID = "workflow-status-refresh-trigger"
RUSH_HOUR_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

store = get_analytics_store()
auth_service = get_auth_service()


def _normalize_role(value: str | None) -> str:
    role = (value or "").strip().lower()
    return role if role in {"user", "admin", "ex"} else "user"


def _can_view_admin_analytics(role: str | None) -> bool:
    return _normalize_role(role) in ADMIN_ANALYTICS_ROLES


def _can_view_runpod_management(role: str | None) -> bool:
    return _normalize_role(role) in RUNPOD_MANAGEMENT_ROLES


def _can_view_runpod_billing(email: str | None) -> bool:
    return (email or "").strip().lower() in RUNPOD_BILLING_EMAILS


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

.admin-badge.neutral {
  background: rgba(143, 154, 173, 0.16);
  color: #c6d0df;
  border: 1px solid rgba(143, 154, 173, 0.32);
}

.admin-group-row td {
  position: sticky;
  top: 37px;
  z-index: 1;
  background: #151b24;
  color: #f0f4fb;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.admin-after-hours-card {
  margin-top: 14px;
}

.admin-rush-insights {
  display: grid;
  grid-template-columns: minmax(180px, 0.7fr) minmax(180px, 0.7fr) minmax(280px, 1.4fr) minmax(280px, 1.4fr);
  gap: 14px;
  align-items: stretch;
  margin: 12px 0 14px;
}

.admin-rush-card {
  background: linear-gradient(145deg, rgba(20, 27, 37, 0.92), rgba(14, 18, 24, 0.82));
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 14px;
  padding: 16px;
  min-height: 120px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.admin-rush-label {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #8fb9d8;
}

.admin-rush-value {
  margin-top: 10px;
  font-size: 22px;
  font-weight: 800;
  line-height: 1.15;
  color: #f6f8fc;
}

.admin-rush-sub {
  margin-top: 8px;
  font-size: 12px;
  color: #a9b3c4;
}

.admin-rush-table-card {
  padding: 12px;
}

.admin-rush-table-card .admin-table-wrap {
  max-height: 220px;
}

.runpod-spend-shell {
  gap: 18px !important;
  margin-top: 10px;
}

.runpod-spend-table .admin-table-wrap {
  max-height: 430px;
}

.runpod-budget-card.is-good {
  border-color: rgba(76, 217, 147, 0.34);
}

.runpod-budget-card.is-warn {
  border-color: rgba(247, 184, 75, 0.42);
  box-shadow: 0 0 28px rgba(247, 184, 75, 0.08);
}

.runpod-budget-card.is-bad {
  border-color: rgba(255, 106, 130, 0.48);
  box-shadow: 0 0 28px rgba(255, 106, 130, 0.1);
}

.admin-kpi-sub.is-warn {
  color: #f7c96f;
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

.admin-nested-tabs {
  margin-top: 0 !important;
}

.runpod-management-embed {
  padding-top: 14px;
  background: #030507;
}

.runpod-management-frame {
  display: block;
  width: 100%;
  min-height: 680px;
  height: calc(100vh - 235px);
  border: 0;
  border-radius: 12px;
  background: #030507;
}

.runpod-management-fallback {
  margin: 10px 0 0;
  color: #9db0ca;
  font-size: 13px;
}

.runpod-management-fallback a {
  color: #8fc7ff;
  font-weight: 700;
}

@media (max-width: 1200px) {
  .admin-rush-insights {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .admin-rush-insights {
    grid-template-columns: 1fr;
  }
}

.workflow-status-slot {
  margin: 6px 0 8px !important;
}

.workflow-status-slot > div {
  min-height: 0 !important;
}

.runpod-status-gadget {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(11, 16, 24, 0.74);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.18);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  cursor: pointer;
  user-select: none;
  transition: border-color 0.16s ease, background 0.16s ease, transform 0.16s ease;
}

.runpod-status-gadget:hover {
  border-color: rgba(255, 255, 255, 0.16);
  background: rgba(15, 21, 31, 0.86);
}

.runpod-status-gadget:active {
  transform: translateY(1px);
}

.runpod-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  color: #8f9aad;
  background: currentColor;
  box-shadow: 0 0 0 0 currentColor;
  animation: runpod-status-pulse 1.8s ease-out infinite;
}

.runpod-status-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  color: #dbe4f3;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1.1;
}

.runpod-status-alert {
  min-width: 18px;
}

.runpod-status-ok .runpod-status-dot {
  color: #53d38a;
}

.runpod-status-busy .runpod-status-dot {
  color: #f0b44f;
}

.runpod-status-error .runpod-status-dot {
  color: #ff6b81;
}

.runpod-status-error .runpod-status-alert {
  background: rgba(255, 107, 129, 0.18);
  color: #ffb7c1;
  border: 1px solid rgba(255, 107, 129, 0.26);
}

@keyframes runpod-status-pulse {
  0% {
    box-shadow: 0 0 0 0 currentColor;
  }
  70% {
    box-shadow: 0 0 0 7px rgba(0, 0, 0, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(0, 0, 0, 0);
  }
}

.workflow-status-refresh-trigger {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  overflow: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
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


async def _load_workflow_status_gadgets() -> tuple[str, ...]:
    return await fetch_multiple_status_gadgets(
        WORKFLOW_STATUS_CONFIGS,
        WORKFLOW_STATUS_REFRESH_TRIGGER_ID,
    )


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


def _workflow_status_refresh_bridge_html() -> str:
    return f"""
    <script>
    (() => {{
      window.momiRefreshWorkflowStatus = (targetId) => {{
        const host = document.getElementById(targetId || "{WORKFLOW_STATUS_REFRESH_TRIGGER_ID}");
        const button = host ? host.querySelector("button") : null;
        if (button) {{
          button.click();
        }}
      }};
    }})();
    </script>
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
        f"<div><b>Window:</b> {_format_admin_window_label(window_days)}</div>"
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
    if str(value or "").strip().lower() in {"all", "all time", "0"}:
        return 0
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        parsed = ADMIN_OVERVIEW_DAYS
    return max(1, parsed)


def _format_admin_window_label(days: int) -> str:
    return "all time" if int(days or 0) <= 0 else f"last {days} day(s)"


def _coerce_after_hours_group(value: str | None) -> str:
    allowed = {group_value for _, group_value in ADMIN_AFTER_HOURS_GROUP_CHOICES}
    parsed = str(value or DEFAULT_ADMIN_AFTER_HOURS_GROUP).strip().lower()
    return parsed if parsed in allowed else DEFAULT_ADMIN_AFTER_HOURS_GROUP


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


def _format_admin_dt_seconds(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _format_money(value: Any) -> str:
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"${amount:,.2f}"


def _format_runpod_billed_time(value_ms: Any) -> str:
    try:
        total_seconds = int(float(value_ms or 0) / 1000)
    except (TypeError, ValueError):
        total_seconds = 0
    if total_seconds <= 0:
        return "-"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _runpod_billing_bucket(days: int) -> str:
    if days <= 1:
        return "hour"
    if days <= 31:
        return "day"
    return "week"


def _runpod_billing_time_params(days: int) -> dict[str, str]:
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=max(1, days))
    return {
        "startTime": start_dt.isoformat().replace("+00:00", "Z"),
        "endTime": end_dt.isoformat().replace("+00:00", "Z"),
        "bucketSize": _runpod_billing_bucket(days),
    }


def _runpod_api_key() -> str:
    return os.getenv("RUNPOD_API_KEY", "").strip()


def _runpod_auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_runpod_api_key()}"}


def _normalize_runpod_billing_record(raw: dict[str, Any], product: str) -> dict[str, Any]:
    resource_id = raw.get("podId") or raw.get("endpointId") or raw.get("instanceId") or "-"
    return {
        "product": product,
        "amount": float(raw.get("amount") or 0.0),
        "time": str(raw.get("time") or ""),
        "time_billed_ms": int(raw.get("timeBilledMs") or raw.get("timeBilledSeconds") or 0)
        if raw.get("timeBilledMs") is not None
        else int(raw.get("timeBilledSeconds") or 0) * 1000,
        "disk_space_billed_gb": raw.get("diskSpaceBilledGb") or raw.get("diskSpaceBilledGB"),
        "resource_id": str(resource_id),
        "gpu_type_id": str(raw.get("gpuTypeId") or "-"),
    }


def _runpod_workflow_resource_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for environment, label in WORKFLOW_STATUS_CONFIGS:
        env_key = str(environment or "").strip().upper()
        resource_id = os.getenv(f"RUNPOD_POD_ID_{env_key}", "").strip()
        if resource_id:
            mapping[resource_id] = label
    return mapping


def _runpod_record_workflow(row: dict[str, Any], resource_map: dict[str, str] | None = None) -> str:
    mapping = resource_map if resource_map is not None else _runpod_workflow_resource_map()
    resource_id = str(row.get("resource_id") or "").strip()
    return mapping.get(resource_id, "Unmapped RunPod")


def _runpod_daily_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return text[:10] if len(text) >= 10 else text


def _fetch_runpod_rest_billing(client: httpx.Client, resource: str, days: int) -> list[dict[str, Any]]:
    product = "Pods" if resource == "pods" else "Serverless"
    params = _runpod_billing_time_params(days)
    if resource == "pods":
        params["grouping"] = "podId"
    elif resource == "endpoints":
        params["grouping"] = "endpointId"

    response = client.get(
        f"{RUNPOD_REST_API_BASE}/billing/{resource}",
        headers=_runpod_auth_headers(),
        params=params,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []
    return [
        _normalize_runpod_billing_record(item, product)
        for item in payload
        if isinstance(item, dict)
    ]


def _fetch_runpod_account_snapshot(client: httpx.Client) -> dict[str, Any]:
    query = """
    query MomiRunpodAccountSnapshot {
      myself {
        currentSpendPerHr
        clientBalance
        clientLifetimeSpend
        spendLimit
      }
    }
    """
    response = client.post(
        RUNPOD_GRAPHQL_API_URL,
        headers={**_runpod_auth_headers(), "Content-Type": "application/json"},
        json={"query": query},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        return {}
    myself = ((payload.get("data") or {}).get("myself") or {})
    return myself if isinstance(myself, dict) else {}


def _fetch_runpod_spend(days: int, *, include_account: bool = True) -> dict[str, Any]:
    if not _runpod_api_key():
        return {
            "ok": False,
            "errors": ["RUNPOD_API_KEY is not configured on the server."],
            "records": [],
            "account": {},
        }
    if not RUNPOD_REST_API_BASE:
        return {
            "ok": False,
            "errors": ["RUNPOD_REST_API_BASE is not configured."],
            "records": [],
            "account": {},
        }

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    account: dict[str, Any] = {}
    with httpx.Client(timeout=RUNPOD_BILLING_TIMEOUT_S) as client:
        for resource in ("pods", "endpoints"):
            try:
                records.extend(_fetch_runpod_rest_billing(client, resource, days))
            except Exception as err:
                errors.append(f"{resource}: {err}")
        if include_account:
            try:
                account = _fetch_runpod_account_snapshot(client)
            except Exception as err:
                errors.append(f"account snapshot: {err}")

    return {
        "ok": not errors or bool(records),
        "errors": errors,
        "records": records,
        "account": account,
    }


def _fetch_runpod_period_spends(days_values: list[int]) -> dict[int, dict[str, Any]]:
    period_spends: dict[int, dict[str, Any]] = {}
    for days in days_values:
        period_spends[days] = _fetch_runpod_spend(days, include_account=False)
    return period_spends


def _summarize_runpod_spend(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(float(row.get("amount") or 0.0) for row in records)
    pods_total = sum(float(row.get("amount") or 0.0) for row in records if row.get("product") == "Pods")
    serverless_total = sum(float(row.get("amount") or 0.0) for row in records if row.get("product") == "Serverless")
    billed_ms = sum(int(row.get("time_billed_ms") or 0) for row in records)
    pods_billed_ms = sum(int(row.get("time_billed_ms") or 0) for row in records if row.get("product") == "Pods")
    return {
        "total": total,
        "pods_total": pods_total,
        "serverless_total": serverless_total,
        "billed_ms": billed_ms,
        "pods_billed_ms": pods_billed_ms,
    }


def _runpod_period_total(period_spends: dict[int, dict[str, Any]], days: int) -> float:
    return float(_summarize_runpod_spend(period_spends.get(days, {}).get("records", [])).get("total") or 0.0)


def _days_in_current_month() -> int:
    now = datetime.now()
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)
    this_month = now.replace(day=1)
    return max(28, (next_month - this_month).days)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _project_runpod_monthly_spend(account: dict[str, Any], month_total: float) -> float:
    current_rate = _coerce_float(account.get("currentSpendPerHr"))
    if current_rate is None:
        return month_total
    return max(month_total, current_rate * 24 * _days_in_current_month())


def _runpod_budget_state(projected_monthly: float, budget: float) -> tuple[str, str]:
    if budget <= 0:
        return "neutral", "No monthly budget configured"
    ratio = projected_monthly / budget if budget else 0.0
    if ratio >= 1.0:
        return "bad", f"Over budget by {_format_money(projected_monthly - budget)}"
    if ratio >= 0.8:
        return "warn", f"{ratio * 100:.0f}% of monthly budget"
    return "good", f"{ratio * 100:.0f}% of monthly budget"


def _render_runpod_spend_summary_html(
    spend: dict[str, Any],
    days: int,
    period_spends: dict[int, dict[str, Any]] | None = None,
) -> str:
    summary = _summarize_runpod_spend(spend.get("records", []))
    period_spends = period_spends or {}
    account = spend.get("account", {}) or {}
    current_spend = account.get("currentSpendPerHr")
    balance = account.get("clientBalance")
    lifetime = account.get("clientLifetimeSpend")
    today_total = _runpod_period_total(period_spends, 1) if period_spends else summary["total"]
    week_total = _runpod_period_total(period_spends, 7) if period_spends else summary["total"]
    month_total = _runpod_period_total(period_spends, 30) if period_spends else summary["total"]
    projected_monthly = _project_runpod_monthly_spend(account, month_total)
    budget_class, budget_message = _runpod_budget_state(projected_monthly, RUNPOD_MONTHLY_BUDGET_USD)

    current_text = _format_money(current_spend) + "/hr" if current_spend is not None else "-"
    balance_text = _format_money(balance) if balance is not None else "-"
    lifetime_text = _format_money(lifetime) if lifetime is not None else "-"
    budget_text = _format_money(RUNPOD_MONTHLY_BUDGET_USD) if RUNPOD_MONTHLY_BUDGET_USD > 0 else "-"

    return f"""
    <div class="admin-kpi-grid">
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Today</div>
          <div class="admin-kpi-icon" aria-hidden="true">$</div>
        </div>
        <div class="admin-kpi-value">{html.escape(_format_money(today_total))}</div>
        <div class="admin-kpi-sub">Last 24 hours</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Week</div>
          <div class="admin-kpi-icon" aria-hidden="true">7d</div>
        </div>
        <div class="admin-kpi-value">{html.escape(_format_money(week_total))}</div>
        <div class="admin-kpi-sub">Last 7 days</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Month</div>
          <div class="admin-kpi-icon" aria-hidden="true">30</div>
        </div>
        <div class="admin-kpi-value">{html.escape(_format_money(month_total))}</div>
        <div class="admin-kpi-sub">Last 30 days</div>
      </div>
      <div class="admin-kpi-card runpod-budget-card is-{html.escape(budget_class)}">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Projected Month</div>
          <div class="admin-kpi-icon" aria-hidden="true">!</div>
        </div>
        <div class="admin-kpi-value">{html.escape(_format_money(projected_monthly))}</div>
        <div class="admin-kpi-sub is-{html.escape(budget_class)}">{html.escape(budget_message)} / budget {html.escape(budget_text)}</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Current Rate</div>
          <div class="admin-kpi-icon" aria-hidden="true">/h</div>
        </div>
        <div class="admin-kpi-value">{html.escape(current_text)}</div>
        <div class="admin-kpi-sub">Balance: {html.escape(balance_text)}</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Pods</div>
          <div class="admin-kpi-icon" aria-hidden="true">P</div>
        </div>
        <div class="admin-kpi-value">{html.escape(_format_money(summary["pods_total"]))}</div>
        <div class="admin-kpi-sub">Billed time: {html.escape(_format_runpod_billed_time(summary["pods_billed_ms"]))}</div>
      </div>
      <div class="admin-kpi-card">
        <div class="admin-kpi-head">
          <div class="admin-kpi-label">Serverless</div>
          <div class="admin-kpi-icon" aria-hidden="true">S</div>
        </div>
        <div class="admin-kpi-value">{html.escape(_format_money(summary["serverless_total"]))}</div>
        <div class="admin-kpi-sub">Lifetime: {html.escape(lifetime_text)}</div>
      </div>
    </div>
    """


def _build_runpod_spend_plot(spend: dict[str, Any]) -> go.Figure:
    records = spend.get("records", [])
    fig = go.Figure()
    if not records:
        fig.add_annotation(
            text="No RunPod billing data in selected range",
            showarrow=False,
            font={"size": 13, "color": "#92a0b5"},
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
        )
        fig.update_layout(**_base_plot_layout("Daily RunPod Spend"))
        return fig

    by_day_product: dict[str, dict[str, float]] = {}
    for row in records:
        day_key = _runpod_daily_key(row.get("time"))
        product = str(row.get("product") or "Other")
        by_day_product.setdefault(day_key, {})
        by_day_product[day_key][product] = by_day_product[day_key].get(product, 0.0) + float(row.get("amount") or 0.0)

    x_values = sorted(by_day_product)
    palette = {"Pods": "#3fa9f5", "Serverless": "#47d793"}
    for product in ("Pods", "Serverless"):
        fig.add_trace(
            go.Bar(
                x=x_values,
                y=[round(by_day_product.get(day_key, {}).get(product, 0.0), 4) for day_key in x_values],
                name=product,
                marker={"color": palette.get(product, "#ff9b3d")},
                hovertemplate="%{x}<br>%{fullData.name}: $%{y:.4f}<extra></extra>",
            )
        )

    fig.update_layout(**_base_plot_layout("Daily RunPod Spend"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(showgrid=False, tickfont={"color": "#9fb0c8"})
    fig.update_yaxes(title="USD", gridcolor="rgba(255,255,255,0.08)", zeroline=False)
    return fig


def _render_runpod_spend_table_html(spend: dict[str, Any]) -> str:
    rows = spend.get("records", [])
    resource_map = _runpod_workflow_resource_map()
    workflow_grouped: dict[str, dict[str, Any]] = {
        label: {
            "workflow_name": label,
            "amount": 0.0,
            "pods_amount": 0.0,
            "serverless_amount": 0.0,
            "time_billed_ms": 0,
        }
        for _environment, label in WORKFLOW_STATUS_CONFIGS
    }
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        workflow_name = _runpod_record_workflow(row, resource_map)
        workflow_item = workflow_grouped.setdefault(
            workflow_name,
            {
                "workflow_name": workflow_name,
                "amount": 0.0,
                "pods_amount": 0.0,
                "serverless_amount": 0.0,
                "time_billed_ms": 0,
            },
        )
        amount = float(row.get("amount") or 0.0)
        workflow_item["amount"] += amount
        workflow_item["time_billed_ms"] += int(row.get("time_billed_ms") or 0)
        if row.get("product") == "Pods":
            workflow_item["pods_amount"] += amount
        elif row.get("product") == "Serverless":
            workflow_item["serverless_amount"] += amount

        key = (
            str(row.get("product") or "-"),
            str(row.get("resource_id") or "-"),
            str(row.get("gpu_type_id") or "-"),
        )
        item = grouped.setdefault(
            key,
            {
                "product": key[0],
                "resource_id": key[1],
                "gpu_type_id": key[2],
                "workflow_name": workflow_name,
                "amount": 0.0,
                "time_billed_ms": 0,
                "last_time": "",
            },
        )
        item["amount"] += float(row.get("amount") or 0.0)
        item["time_billed_ms"] += int(row.get("time_billed_ms") or 0)
        item["last_time"] = max(str(item.get("last_time") or ""), str(row.get("time") or ""))
        if item.get("workflow_name") == "Unmapped RunPod" and workflow_name != "Unmapped RunPod":
            item["workflow_name"] = workflow_name

    workflow_ranked = sorted(workflow_grouped.values(), key=lambda item: float(item.get("amount") or 0.0), reverse=True)
    workflow_body = ""
    for row in workflow_ranked:
        workflow_body += (
            "<tr>"
            f"<td>{html.escape(str(row.get('workflow_name') or '-'))}</td>"
            f"<td>{html.escape(_format_money(row.get('amount')))}</td>"
            f"<td>{html.escape(_format_money(row.get('pods_amount')))}</td>"
            f"<td>{html.escape(_format_money(row.get('serverless_amount')))}</td>"
            f"<td>{html.escape(_format_runpod_billed_time(row.get('time_billed_ms')))}</td>"
            "</tr>"
        )

    if not workflow_body:
        workflow_body = "<tr><td colspan='5' class='admin-empty'>No workflow cost rows in this range.</td></tr>"

    ranked = sorted(grouped.values(), key=lambda item: float(item.get("amount") or 0.0), reverse=True)
    body = ""
    for row in ranked[:RUNPOD_BILLING_TABLE_LIMIT]:
        body += (
            "<tr>"
            f"<td>{html.escape(str(row.get('product') or '-'))}</td>"
            f"<td>{html.escape(str(row.get('workflow_name') or '-'))}</td>"
            f"<td class='admin-mono'>{html.escape(str(row.get('resource_id') or '-'))}</td>"
            f"<td>{html.escape(str(row.get('gpu_type_id') or '-'))}</td>"
            f"<td>{html.escape(_format_money(row.get('amount')))}</td>"
            f"<td>{html.escape(_format_runpod_billed_time(row.get('time_billed_ms')))}</td>"
            f"<td>{html.escape(_format_admin_dt(row.get('last_time')))}</td>"
            "</tr>"
        )

    if not body:
        body = "<tr><td colspan='7' class='admin-empty'>No RunPod billing rows in this range.</td></tr>"

    return f"""
    <div class="admin-table-card runpod-spend-table">
      <h3 class="admin-table-title">Per Workflow Cost</h3>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Workflow</th>
              <th>Total Spend</th>
              <th>Pods</th>
              <th>Serverless</th>
              <th>Billed Time</th>
            </tr>
          </thead>
          <tbody>{workflow_body}</tbody>
        </table>
      </div>
    </div>
    <div class="admin-table-card runpod-spend-table">
      <h3 class="admin-table-title">Top Expensive Pods / Endpoints</h3>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>Workflow</th>
              <th>Resource</th>
              <th>GPU</th>
              <th>Spend</th>
              <th>Billed Time</th>
              <th>Last Bucket</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </div>
    """


def _build_runpod_spend_dashboard(days: int) -> tuple[str, str, go.Figure | None, str]:
    spend = _fetch_runpod_spend(days)
    period_spends = _fetch_runpod_period_spends([1, 7, 30]) if spend.get("ok") else {}
    plot = _safe_plot_render(lambda: _build_runpod_spend_plot(spend))
    period_errors: list[str] = []
    for period_days, period_spend in period_spends.items():
        for error in period_spend.get("errors", [])[:1]:
            period_errors.append(f"{period_days}d {error}")
    error_text = "; ".join((spend.get("errors", []) + period_errors)[:2])
    if not spend.get("ok"):
        status = "<p class='admin-status-line'>RunPod spend is unavailable. "
        status += html.escape(error_text or "Check the server RunPod API configuration.")
        status += "</p>"
    elif error_text:
        status = (
            f"<p class='admin-status-line'>RunPod spend loaded for {html.escape(_format_admin_window_label(days))}. "
            f"Partial warning: {html.escape(error_text)}</p>"
        )
    else:
        status = f"<p class='admin-status-line'>RunPod spend loaded for {html.escape(_format_admin_window_label(days))}.</p>"

    return (
        status,
        _render_runpod_spend_summary_html(spend, days, period_spends),
        plot,
        _render_runpod_spend_table_html(spend),
    )


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
        <div class="admin-kpi-sub">Window: {html.escape(_format_admin_window_label(days))}</div>
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


def _empty_admin_plots() -> tuple[None, None, None, None]:
    return (None, None, None, None)


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


def _format_hour_window(hour: Any) -> str:
    try:
        value = max(0, min(23, int(hour)))
    except (TypeError, ValueError):
        value = 0
    return f"{value:02d}:00-{value:02d}:59"


def _build_rush_hour_heatmap(rush_hour: dict[str, Any]) -> go.Figure:
    fig = go.Figure()
    slots = rush_hour.get("slots", [])
    if not slots:
        fig.add_annotation(
            text="No rush-hour data in selected range",
            showarrow=False,
            font={"size": 13, "color": "#92a0b5"},
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
        )
        fig.update_layout(**_base_plot_layout("Rush Hour Heatmap"))
        return fig

    by_slot = {
        (int(row.get("weekday") or 0), int(row.get("hour") or 0)): row
        for row in slots
    }
    z_values: list[list[int]] = []
    hover_values: list[list[str]] = []
    for weekday_idx, weekday_name in enumerate(RUSH_HOUR_WEEKDAYS):
        z_row: list[int] = []
        hover_row: list[str] = []
        for hour in range(24):
            row = by_slot.get((weekday_idx, hour), {})
            total = int(row.get("total_tasks") or 0)
            completed = int(row.get("completed_tasks") or 0)
            failed = int(row.get("failed_tasks") or 0)
            active_users = int(row.get("active_users") or 0)
            avg_duration = _format_duration(row.get("avg_total_duration_ms"))
            fail_rate = (failed / total * 100.0) if total else 0.0
            z_row.append(total)
            hover_row.append(
                f"{weekday_name} {_format_hour_window(hour)}<br>"
                f"Tasks: {total}<br>"
                f"Completed: {completed}<br>"
                f"Failed: {failed} ({fail_rate:.1f}%)<br>"
                f"Avg duration: {html.escape(avg_duration)}<br>"
                f"Active users: {active_users}"
            )
        z_values.append(z_row)
        hover_values.append(hover_row)

    fig.add_trace(
        go.Heatmap(
            z=z_values,
            x=[f"{hour:02d}:00" for hour in range(24)],
            y=RUSH_HOUR_WEEKDAYS,
            customdata=hover_values,
            colorscale=[
                [0.0, "#10141a"],
                [0.25, "#175a76"],
                [0.55, "#2fbf8f"],
                [0.78, "#f7b84b"],
                [1.0, "#ff5c7a"],
            ],
            colorbar={
                "title": {"text": "Tasks", "font": {"color": "#d5dfef"}},
                "tickcolor": "#9fb0c8",
                "tickfont": {"color": "#9fb0c8"},
            },
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    fig.update_layout(**_base_plot_layout("Rush Hour Heatmap"))
    fig.update_layout(height=430)
    fig.update_xaxes(title="Hour of Day", showgrid=False, tickangle=-45, tickfont={"color": "#9fb0c8"})
    fig.update_yaxes(title="Day", showgrid=False, tickfont={"color": "#9fb0c8"})
    return fig


def _render_rush_hour_insights_html(rush_hour: dict[str, Any]) -> str:
    slots = rush_hour.get("slots", [])
    forecast = rush_hour.get("forecast", [])
    total_tasks = int(rush_hour.get("total_tasks") or 0)

    top_slots = sorted(
        slots,
        key=lambda row: (-int(row.get("total_tasks") or 0), int(row.get("weekday") or 0), int(row.get("hour") or 0)),
    )[:5]

    if top_slots:
        busiest = top_slots[0]
        busiest_weekday = RUSH_HOUR_WEEKDAYS[int(busiest.get("weekday") or 0)]
        busiest_text = f"{busiest_weekday} {_format_hour_window(busiest.get('hour'))}"
        busiest_tasks = int(busiest.get("total_tasks") or 0)
        duration_text = _format_duration(busiest.get("avg_total_duration_ms"))
    else:
        busiest_text = "-"
        busiest_tasks = 0
        duration_text = "-"

    top_items = ""
    for row in top_slots:
        weekday = RUSH_HOUR_WEEKDAYS[int(row.get("weekday") or 0)]
        total = int(row.get("total_tasks") or 0)
        failed = int(row.get("failed_tasks") or 0)
        fail_rate = (failed / total * 100.0) if total else 0.0
        top_items += (
            "<tr>"
            f"<td>{html.escape(weekday)}</td>"
            f"<td>{html.escape(_format_hour_window(row.get('hour')))}</td>"
            f"<td>{total}</td>"
            f"<td>{html.escape(_format_duration(row.get('avg_total_duration_ms')))}</td>"
            f"<td>{fail_rate:.1f}%</td>"
            "</tr>"
        )

    if not top_items:
        top_items = "<tr><td colspan='5' class='admin-empty'>No rush windows in this range.</td></tr>"

    forecast_items = ""
    for row in forecast[:5]:
        weekday = RUSH_HOUR_WEEKDAYS[int(row.get("weekday") or 0)]
        forecast_items += (
            "<tr>"
            f"<td>{html.escape(str(row.get('date') or '-'))}</td>"
            f"<td>{html.escape(weekday)}</td>"
            f"<td>{html.escape(_format_hour_window(row.get('hour')))}</td>"
            f"<td>{float(row.get('expected_tasks') or 0):.2f}</td>"
            "</tr>"
        )

    if not forecast_items:
        forecast_items = "<tr><td colspan='4' class='admin-empty'>Forecast needs more historical task volume.</td></tr>"

    return f"""
    <div class="admin-rush-insights">
      <div class="admin-rush-card">
        <div class="admin-rush-label">Busiest Window</div>
        <div class="admin-rush-value">{html.escape(busiest_text)}</div>
        <div class="admin-rush-sub">{busiest_tasks} task(s), avg {html.escape(duration_text)}</div>
      </div>
      <div class="admin-rush-card">
        <div class="admin-rush-label">Analyzed Tasks</div>
        <div class="admin-rush-value">{total_tasks}</div>
        <div class="admin-rush-sub">Based on the selected analytics window</div>
      </div>
      <div class="admin-table-card admin-rush-table-card">
        <h3 class="admin-table-title">Top Rush Windows</h3>
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>Day</th>
                <th>Hour</th>
                <th>Tasks</th>
                <th>Avg Duration</th>
                <th>Fail Rate</th>
              </tr>
            </thead>
            <tbody>{top_items}</tbody>
          </table>
        </div>
      </div>
      <div class="admin-table-card admin-rush-table-card">
        <h3 class="admin-table-title">Next Likely Rush Windows</h3>
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Day</th>
                <th>Hour</th>
                <th>Expected Tasks</th>
              </tr>
            </thead>
            <tbody>{forecast_items}</tbody>
          </table>
        </div>
      </div>
    </div>
    """


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


def _status_badge_html(status: Any) -> str:
    label = str(status or "unknown").strip() or "unknown"
    css_class = "success" if label.lower() == "completed" else "error" if label.lower() == "failed" else "neutral"
    return f"<span class='admin-badge {css_class}'>{html.escape(label.title())}</span>"


def _render_after_hours_table_html(rows: list[dict[str, Any]], search_query: str, group_by: str) -> str:
    query = (search_query or "").strip().lower()
    filtered = [
        row for row in rows
        if _match_search(str(row.get("user_email") or ""), query)
        or _match_search(str(row.get("user_prefix") or ""), query)
        or _match_search(str(row.get("user_display_name") or ""), query)
        or _match_search(str(row.get("workflow_name") or ""), query)
        or _match_search(_format_workflow_display_name(row.get("workflow_name")), query)
    ]

    group_label = {
        "week": "Week",
        "month": "Month",
    }.get(group_by, "Day")

    body = ""
    current_group = None
    for row in filtered:
        row_group = str(row.get("group_label") or "-")
        if row_group != current_group:
            current_group = row_group
            body += (
                "<tr class='admin-group-row'>"
                f"<td colspan='7'>{html.escape(group_label)}: {html.escape(row_group)}</td>"
                "</tr>"
            )

        workflow_display = _format_workflow_display_name(row.get("workflow_name"))
        user_name = row.get("user_display_name") or row.get("user_prefix") or row.get("user_email") or "-"
        body += (
            "<tr>"
            f"<td>{html.escape(_format_admin_dt_seconds(row.get('handled_at')))}</td>"
            f"<td>{html.escape(str(user_name))}</td>"
            f"<td class='admin-mono'>{html.escape(str(row.get('user_email') or '-'))}</td>"
            f"<td>{html.escape(workflow_display)}</td>"
            f"<td>{html.escape(_format_duration(row.get('total_duration_ms')))}</td>"
            f"<td>{_status_badge_html(row.get('status'))}</td>"
            f"<td class='admin-mono'>{html.escape(str(row.get('task_id') or '-'))}</td>"
            "</tr>"
        )

    if not body:
        body = "<tr><td colspan='7' class='admin-empty'>No matching after-hours tasks after 6:00 PM.</td></tr>"

    return f"""
    <div class="admin-table-card admin-after-hours-card">
      <h3 class="admin-table-title">Tasks Handled After 6:00 PM</h3>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Exact Time</th>
              <th>User Name</th>
              <th>User Email</th>
              <th>Workflow</th>
              <th>Duration</th>
              <th>Status</th>
              <th>Task ID</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </div>
    """


def _build_admin_dashboard(
    days: int, search_query: str, after_hours_group: str = DEFAULT_ADMIN_AFTER_HOURS_GROUP
) -> tuple[str, str, go.Figure | None, go.Figure | None, go.Figure | None, go.Figure | None, str, str, str, str]:
    dashboard = store.get_admin_dashboard(days=days, limit=ADMIN_DASHBOARD_TABLE_LIMIT)
    after_hours_group = _coerce_after_hours_group(after_hours_group)
    after_hours = store.get_admin_after_hours_tasks(
        days=days,
        group_by=after_hours_group,
        limit=ADMIN_DASHBOARD_TABLE_LIMIT,
    )
    rush_hour = store.get_admin_rush_hour_analytics(days=days)
    summary = dashboard.get("summary", {})
    trend_rows = dashboard.get("trend", [])
    workflow_rows = dashboard.get("workflows", [])
    users_rows = dashboard.get("top_users", [])
    failures_rows = dashboard.get("recent_failures", [])
    after_hours_rows = after_hours.get("items", [])
    trend_plot = _safe_plot_render(lambda: _build_trend_plot(trend_rows))
    workflow_plot = _safe_plot_render(lambda: _build_workflow_distribution_plot(workflow_rows))
    performance_plot = _safe_plot_render(lambda: _build_performance_plot(workflow_rows))
    rush_hour_plot = _safe_plot_render(lambda: _build_rush_hour_heatmap(rush_hour))

    if trend_plot is None or workflow_plot is None or performance_plot is None or rush_hour_plot is None:
        status_text = (
            f"<p class='admin-status-line'>Admin analytics is active. Window: {html.escape(_format_admin_window_label(days))}. "
            "Chart rendering is currently unavailable on this server runtime.</p>"
        )
    else:
        status_text = f"<p class='admin-status-line'>Admin analytics is active. Window: {html.escape(_format_admin_window_label(days))}.</p>"

    return (
        status_text,
        _build_kpi_cards_html(summary, days=days),
        trend_plot,
        workflow_plot,
        performance_plot,
        rush_hour_plot,
        _render_rush_hour_insights_html(rush_hour),
        _render_users_table_html(users_rows, search_query),
        _render_failures_table_html(failures_rows, search_query),
        _render_after_hours_table_html(after_hours_rows, search_query, after_hours_group),
    )


def _load_portal_data(request: gr.Request):
    history_base_url = _resolve_history_portal_base_url(request)
    email = getattr(request, "username", None)
    if not email:
        trend_plot, workflow_plot, performance_plot, rush_hour_plot = _empty_admin_plots()
        return (
            _topbar_html("-", "Unknown User", None),
            "<p class='admin-status-line'>Admin access is unavailable.</p>",
            "",
            trend_plot,
            workflow_plot,
            performance_plot,
            rush_hour_plot,
            _render_rush_hour_insights_html({}),
            _render_users_table_html([], ""),
            _render_failures_table_html([], ""),
            _render_after_hours_table_html([], "", DEFAULT_ADMIN_AFTER_HOURS_GROUP),
            _history_portal_html(history_base_url),
            "",
            gr.update(visible=False),
        )

    identity = auth_service.get_identity(email)
    history_url = _build_history_portal_sso_url(identity.email, history_base_url)

    user_role = _normalize_role(getattr(identity, "role", None))
    can_view_admin = _can_view_admin_analytics(user_role)
    can_view_runpod = _can_view_runpod_management(user_role)
    can_view_runpod_billing = _can_view_runpod_billing(identity.email)

    if can_view_admin:
        (
            admin_hint,
            admin_summary,
            trend_plot,
            workflow_plot,
            performance_plot,
            rush_hour_plot,
            rush_hour_insights_html,
            users_table_html,
            failures_table_html,
            after_hours_table_html,
        ) = _build_admin_dashboard(ADMIN_OVERVIEW_DAYS, "")
    else:
        admin_hint = "<p class='admin-status-line'>Admin analytics is restricted to admin and executive users.</p>"
        admin_summary = ""
        trend_plot, workflow_plot, performance_plot, rush_hour_plot = _empty_admin_plots()
        rush_hour_insights_html = _render_rush_hour_insights_html({})
        users_table_html = _render_users_table_html([], "")
        failures_table_html = _render_failures_table_html([], "")
        after_hours_table_html = _render_after_hours_table_html([], "", DEFAULT_ADMIN_AFTER_HOURS_GROUP)

    runpod_management_html = (
        _runpod_management_html(_build_runpod_management_url(identity.email, user_role))
        if can_view_runpod
        else ""
    )

    return (
        _topbar_html(identity.email, identity.display_name, identity.avatar_path),
        admin_hint,
        admin_summary,
        trend_plot,
        workflow_plot,
        performance_plot,
        rush_hour_plot,
        rush_hour_insights_html,
        users_table_html,
        failures_table_html,
        after_hours_table_html,
        _history_portal_html(history_url),
        runpod_management_html,
        gr.update(visible=can_view_admin or can_view_runpod or can_view_runpod_billing),
    )


def _refresh_admin(date_range: str, search_query: str, after_hours_group: str, request: gr.Request):
    email = getattr(request, "username", None)
    if not email:
        trend_plot, workflow_plot, performance_plot, rush_hour_plot = _empty_admin_plots()
        return (
            "<p class='admin-status-line'>Admin access is unavailable.</p>",
            "",
            trend_plot,
            workflow_plot,
            performance_plot,
            rush_hour_plot,
            _render_rush_hour_insights_html({}),
            _render_users_table_html([], ""),
            _render_failures_table_html([], ""),
            _render_after_hours_table_html([], "", _coerce_after_hours_group(after_hours_group)),
        )

    identity = auth_service.get_identity(email)
    if not _can_view_admin_analytics(getattr(identity, "role", None)):
        trend_plot, workflow_plot, performance_plot, rush_hour_plot = _empty_admin_plots()
        return (
            "<p class='admin-status-line'>Admin analytics is restricted to admin and executive users.</p>",
            "",
            trend_plot,
            workflow_plot,
            performance_plot,
            rush_hour_plot,
            _render_rush_hour_insights_html({}),
            _render_users_table_html([], ""),
            _render_failures_table_html([], ""),
            _render_after_hours_table_html([], "", _coerce_after_hours_group(after_hours_group)),
        )

    days = _coerce_days(date_range)
    return _build_admin_dashboard(days, search_query or "", after_hours_group)


def _refresh_runpod_spend(date_range: str, request: gr.Request):
    email = getattr(request, "username", None)
    if not email:
        return (
            "<p class='admin-status-line'>RunPod spend access is unavailable.</p>",
            "",
            None,
            "",
        )

    identity = auth_service.get_identity(email)
    if not _can_view_runpod_billing(identity.email):
        return (
            "<p class='admin-status-line'>RunPod spend is restricted to the configured owner.</p>",
            "",
            None,
            "",
        )

    days = _coerce_days(date_range)
    return _build_runpod_spend_dashboard(days)


with gr.Blocks(title=APP_TITLE, css=EMBEDDED_HIDE_CSS) as app:
    gr.HTML(_app_splash_html())
    gr.HTML(_embedded_mode_detector_html())
    gr.HTML(_workflow_status_refresh_bridge_html())

    user_header = gr.HTML(_topbar_html("-", "Loading", None), elem_classes=["app-shell-header"])
    workflow_status_refresh_trigger = gr.Button(
        "",
        elem_id=WORKFLOW_STATUS_REFRESH_TRIGGER_ID,
        elem_classes=["workflow-status-refresh-trigger"],
    )

    with gr.Tabs(elem_classes=["main-tabs"]):
        with gr.Tab("General Enhancement"):
            general_status_gadget = gr.HTML(
                build_placeholder_html(WORKFLOW_STATUS_REFRESH_TRIGGER_ID),
                elem_classes=["workflow-status-slot"],
            )
            General_Enhancement_interface.render()

        with gr.Tab("Pro Upscaler"):
            pro_upscaler_status_gadget = gr.HTML(
                build_placeholder_html(WORKFLOW_STATUS_REFRESH_TRIGGER_ID),
                elem_classes=["workflow-status-slot"],
            )
            fivek.render()

        with gr.Tab("Reference Generator"):
            reference_generator_status_gadget = gr.HTML(
                build_placeholder_html(WORKFLOW_STATUS_REFRESH_TRIGGER_ID),
                elem_classes=["workflow-status-slot"],
            )
            reference_generator_interface.render()

        with gr.Tab("Qwen Edit"):
            qwen_status_gadget = gr.HTML(
                build_placeholder_html(WORKFLOW_STATUS_REFRESH_TRIGGER_ID),
                elem_classes=["workflow-status-slot"],
            )
            flux2_klein_interface.render()

        with gr.Tab("History"):
            history_portal_shell = gr.HTML(_history_portal_html())

        with gr.Tab("Admin Analytics", visible=False) as admin_tab:
            with gr.Tabs(elem_classes=["admin-nested-tabs"]):
                with gr.Tab("Analytics"):
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
                            admin_after_hours_group = gr.Dropdown(
                                choices=ADMIN_AFTER_HOURS_GROUP_CHOICES,
                                value=DEFAULT_ADMIN_AFTER_HOURS_GROUP,
                                label="After-Hours Grouping",
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
                            rush_hour_plot = gr.Plot(
                                label="Rush Hour Heatmap",
                                elem_classes=["admin-chart-card"],
                            )

                        rush_hour_insights = gr.HTML("")

                        with gr.Row():
                            top_users_table = gr.HTML("")
                            recent_failures_table = gr.HTML("")

                        after_hours_table = gr.HTML("")

                with gr.Tab("RunPod Spend"):
                    with gr.Column(elem_classes=["runpod-spend-shell"]):
                        with gr.Row(elem_classes=["admin-dashboard-controls"]):
                            runpod_spend_date_range = gr.Dropdown(
                                choices=RUNPOD_BILLING_DATE_RANGE_CHOICES,
                                value="30",
                                label="Date Range",
                            )
                            refresh_runpod_spend_btn = gr.Button(
                                "Refresh",
                                variant="secondary",
                                elem_classes=["admin-refresh-btn"],
                            )

                        runpod_spend_status = gr.HTML("<p class='admin-status-line'>Loading RunPod spend...</p>")
                        runpod_spend_summary = gr.HTML("")
                        runpod_spend_plot = gr.Plot(label="Daily RunPod Spend", elem_classes=["admin-chart-card"])
                        runpod_spend_table = gr.HTML("")

                with gr.Tab("RunPod Management"):
                    runpod_management_shell = gr.HTML("")

    workflow_status_timer = gr.Timer(value=RUNPOD_STATUS_GADGET_REFRESH_S, active=True)

    app.load(
        fn=_load_workflow_status_gadgets,
        inputs=None,
        outputs=[
            general_status_gadget,
            pro_upscaler_status_gadget,
            reference_generator_status_gadget,
            qwen_status_gadget,
        ],
        queue=False,
        show_progress="hidden",
    )
    workflow_status_timer.tick(
        fn=_load_workflow_status_gadgets,
        inputs=None,
        outputs=[
            general_status_gadget,
            pro_upscaler_status_gadget,
            reference_generator_status_gadget,
            qwen_status_gadget,
        ],
        queue=False,
        show_progress="hidden",
    )
    workflow_status_refresh_trigger.click(
        fn=_load_workflow_status_gadgets,
        inputs=None,
        outputs=[
            general_status_gadget,
            pro_upscaler_status_gadget,
            reference_generator_status_gadget,
            qwen_status_gadget,
        ],
        queue=False,
        show_progress="hidden",
    )

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
            rush_hour_plot,
            rush_hour_insights,
            top_users_table,
            recent_failures_table,
            after_hours_table,
            history_portal_shell,
            runpod_management_shell,
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

    app.load(
        fn=_refresh_runpod_spend,
        inputs=[runpod_spend_date_range],
        outputs=[
            runpod_spend_status,
            runpod_spend_summary,
            runpod_spend_plot,
            runpod_spend_table,
        ],
        queue=False,
        show_progress="hidden",
    )

    refresh_admin_btn.click(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search, admin_after_hours_group],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            rush_hour_plot,
            rush_hour_insights,
            top_users_table,
            recent_failures_table,
            after_hours_table,
        ],
    )

    refresh_runpod_spend_btn.click(
        fn=_refresh_runpod_spend,
        inputs=[runpod_spend_date_range],
        outputs=[
            runpod_spend_status,
            runpod_spend_summary,
            runpod_spend_plot,
            runpod_spend_table,
        ],
    )

    runpod_spend_date_range.change(
        fn=_refresh_runpod_spend,
        inputs=[runpod_spend_date_range],
        outputs=[
            runpod_spend_status,
            runpod_spend_summary,
            runpod_spend_plot,
            runpod_spend_table,
        ],
    )

    admin_date_range.change(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search, admin_after_hours_group],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            rush_hour_plot,
            rush_hour_insights,
            top_users_table,
            recent_failures_table,
            after_hours_table,
        ],
    )

    admin_search.change(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search, admin_after_hours_group],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            rush_hour_plot,
            rush_hour_insights,
            top_users_table,
            recent_failures_table,
            after_hours_table,
        ],
    )

    admin_after_hours_group.change(
        fn=_refresh_admin,
        inputs=[admin_date_range, admin_search, admin_after_hours_group],
        outputs=[
            admin_status,
            admin_summary,
            tasks_trend_plot,
            workflow_distribution_plot,
            performance_plot,
            rush_hour_plot,
            rush_hour_insights,
            top_users_table,
            recent_failures_table,
            after_hours_table,
        ],
    )


def _require_portal_signing_secret() -> None:
    """Refuse to start when the portal proxies cannot be signed safely."""
    for warning in portal_auth.validate_signing_secret(HISTORY_PORTAL_SSO_SECRET):
        print(f"[momi] WARNING: {warning}")


def _create_server_app() -> FastAPI:
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
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0, verify=False) as client:
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
