from __future__ import annotations

import base64
import html
import mimetypes
import os
from pathlib import Path
from typing import Any

# Reduce Gradio/HuggingFace telemetry chatter unless explicitly overridden.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import gradio as gr
import uvicorn

from General_Enhancement_v04 import General_Enhancement_interface
from admin_render import (
    ADMIN_AFTER_HOURS_GROUP_CHOICES,
    ADMIN_DATE_RANGE_CHOICES,
    ADMIN_OVERVIEW_DAYS,
    DEFAULT_ADMIN_AFTER_HOURS_GROUP,
    DEFAULT_ADMIN_DATE_RANGE,
    _build_admin_dashboard,
    _coerce_after_hours_group,
    _coerce_days,
    _empty_admin_plots,
    _render_after_hours_table_html,
    _render_failures_table_html,
    _render_rush_hour_insights_html,
    _render_users_table_html,
)
from auth_service import get_auth_service
from flux2_klein_image_edit_9b_distilled import flux2_klein_interface
import portal_proxy
from portal_proxy import (
    _build_history_portal_sso_url,
    _build_runpod_management_url,
    _can_view_admin_analytics,
    _can_view_runpod_billing,
    _can_view_runpod_management,
    _history_portal_html,
    _normalize_role,
    _resolve_history_portal_base_url,
    _runpod_management_html,
)
from reference_generator import reference_generator_interface
from runpod_billing import _build_runpod_spend_dashboard
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
APP_SSL_ENABLE_MODE = os.getenv("APP_SSL_ENABLE", "auto").strip().lower()
APP_SSL_CERTFILE = os.getenv("APP_SSL_CERTFILE", "").strip()
APP_SSL_KEYFILE = os.getenv("APP_SSL_KEYFILE", "").strip()
APP_SSL_KEYFILE_PASSWORD = os.getenv("APP_SSL_KEYFILE_PASSWORD", "").strip()
SPLASH_LOTTIE_IFRAME_SRC = os.getenv(
    "SPLASH_LOTTIE_IFRAME_SRC",
    "/splash-assets/player.html",
).strip()
SPLASH_ASSETS_DIR = Path(__file__).resolve().parent / "splash_assets"

RUNPOD_BILLING_DATE_RANGE_CHOICES = [
    ("Last 24h", "1"),
    ("7 Days", "7"),
    ("30 Days", "30"),
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

auth_service = get_auth_service()


EMBEDDED_HIDE_CSS = (Path(__file__).parent / "static" / "app.css").read_text(encoding="utf-8")


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

    admin_refresh_outputs = [
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
    ]
    for component, event_name in (
        (refresh_admin_btn, "click"),
        (admin_date_range, "change"),
        (admin_search, "change"),
        (admin_after_hours_group, "change"),
    ):
        getattr(component, event_name)(
            fn=_refresh_admin,
            inputs=[
                admin_date_range,
                admin_search,
                admin_after_hours_group,
            ],
            outputs=admin_refresh_outputs,
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

def _create_server_app():
    return portal_proxy.create_server_app(blocks=app)


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
