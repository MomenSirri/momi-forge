from __future__ import annotations

import asyncio
import html
import os
from datetime import datetime

from runpod_api_class import RunpodAPI

RUNPOD_STATUS_GADGET_REFRESH_S = max(
    5.0,
    float(os.getenv("RUNPOD_STATUS_GADGET_REFRESH_S", "8")),
)
RUNPOD_STATUS_GADGET_TIMEOUT_S = max(
    2,
    int(os.getenv("RUNPOD_STATUS_GADGET_TIMEOUT_S", "6")),
)


def build_placeholder_html(refresh_target_id: str) -> str:
    return _render_gadget_html(
        tone="neutral",
        idle=None,
        running=None,
        queued=None,
        tooltip_lines=["Refreshing RunPod status...", "Click to refresh"],
        refresh_target_id=refresh_target_id,
    )


async def fetch_status_gadget_html(
    environment: str,
    label: str,
    refresh_target_id: str,
) -> str:
    refreshed_at = datetime.now().astimezone().strftime("%H:%M:%S")

    try:
        api = RunpodAPI(environment=environment)
        result = await api.check_health(timeout=RUNPOD_STATUS_GADGET_TIMEOUT_S, retries=1)
    except Exception as error:
        return _render_gadget_html(
            tone="error",
            idle=None,
            running=None,
            queued=None,
            tooltip_lines=[
                label,
                "Status unavailable",
                str(error),
                f"Last refresh: {refreshed_at}",
                "Click to refresh",
            ],
            alert="!",
            refresh_target_id=refresh_target_id,
        )

    if not result.get("ok"):
        return _render_gadget_html(
            tone="error",
            idle=None,
            running=None,
            queued=None,
            tooltip_lines=[
                label,
                "Status unavailable",
                str(result.get("error") or "Unknown RunPod error"),
                f"Last refresh: {refreshed_at}",
                "Click to refresh",
            ],
            alert="!",
            refresh_target_id=refresh_target_id,
        )

    raw = result.get("raw", {}) or {}
    workers = raw.get("workers", {}) or {}
    jobs = raw.get("jobs", {}) or {}

    idle = _coerce_count(result.get("idle_workers"))
    running = _coerce_count(result.get("running_workers"))
    queued = _coerce_count(result.get("jobs_in_queue"))
    in_progress = _coerce_count(result.get("jobs_in_progress"))
    ready = _coerce_count(workers.get("ready"))
    initializing = _coerce_count(workers.get("initializing"))
    throttled = _coerce_count(workers.get("throttled"))
    unhealthy = _coerce_count(workers.get("unhealthy"))

    tone = _resolve_tone(
        idle=idle,
        running=running,
        queued=queued,
        unhealthy=unhealthy,
    )

    tooltip_lines = [
        label,
        f"Idle workers: {idle}",
        f"Running workers: {running}",
        f"Queued jobs: {queued}",
        f"In-progress jobs: {in_progress}",
        f"Ready workers: {ready}",
        f"Initializing workers: {initializing}",
        f"Throttled workers: {throttled}",
        f"Unhealthy workers: {unhealthy}",
        f"Completed jobs: {_coerce_count(jobs.get('completed'))}",
        f"Failed jobs: {_coerce_count(jobs.get('failed'))}",
        f"Last refresh: {refreshed_at}",
        "Click to refresh",
    ]

    return _render_gadget_html(
        tone=tone,
        idle=idle,
        running=running,
        queued=queued,
        tooltip_lines=tooltip_lines,
        alert="!" if tone == "error" else "",
        refresh_target_id=refresh_target_id,
    )


async def fetch_multiple_status_gadgets(
    configs: list[tuple[str, str]],
    refresh_target_id: str,
) -> tuple[str, ...]:
    return tuple(
        await asyncio.gather(
            *(
                fetch_status_gadget_html(environment, label, refresh_target_id)
                for environment, label in configs
            )
        )
    )


def _resolve_tone(*, idle: int, running: int, queued: int, unhealthy: int) -> str:
    if unhealthy > 0:
        return "error"
    if idle > 0 and queued == 0:
        return "ok"
    if queued > 0 or running > 0:
        return "busy"
    return "error"


def _coerce_count(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def _render_gadget_html(
    *,
    tone: str,
    idle: int | None,
    running: int | None,
    queued: int | None,
    tooltip_lines: list[str],
    refresh_target_id: str,
    alert: str = "",
) -> str:
    safe_tooltip = html.escape("\n".join(tooltip_lines), quote=True)
    idle_text = _badge_value(idle)
    running_text = _badge_value(running)
    queued_text = _badge_value(queued)
    alert_chip = (
        f"<span class='runpod-status-chip runpod-status-alert'>{html.escape(alert)}</span>"
        if alert
        else ""
    )

    return f"""
    <div
      class="runpod-status-gadget runpod-status-{tone}"
      title="{safe_tooltip}"
      role="status"
      aria-live="polite"
      onclick="window.momiRefreshWorkflowStatus && window.momiRefreshWorkflowStatus('{html.escape(refresh_target_id, quote=True)}')"
    >
      <span class="runpod-status-dot" aria-hidden="true"></span>
      <span class="runpod-status-chip">I {idle_text}</span>
      <span class="runpod-status-chip">R {running_text}</span>
      <span class="runpod-status-chip">Q {queued_text}</span>
      {alert_chip}
    </div>
    """


def _badge_value(value: int | None) -> str:
    if value is None:
        return "-"
    return str(value)
