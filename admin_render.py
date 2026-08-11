from __future__ import annotations

from datetime import datetime, timezone
import html
import os
import re
from typing import Any, Callable

import plotly.graph_objects as go

from analytics_store import get_analytics_store


ADMIN_OVERVIEW_DAYS = max(
    1,
    int(os.getenv("APP_ADMIN_OVERVIEW_DAYS", "30")),
)
ADMIN_DASHBOARD_TABLE_LIMIT = max(
    20,
    int(os.getenv("APP_ADMIN_DASHBOARD_TABLE_LIMIT", "120")),
)
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
DEFAULT_ADMIN_AFTER_HOURS_GROUP = "day"
ADMIN_DATE_RANGE_VALUES = {
    value for _, value in ADMIN_DATE_RANGE_CHOICES
}
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
RUSH_HOUR_WEEKDAYS = [
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
]

store = get_analytics_store()


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


