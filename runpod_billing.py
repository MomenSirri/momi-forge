from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import os
from typing import Any

import httpx
import plotly.graph_objects as go

from admin_render import (
    _base_plot_layout,
    _format_admin_dt,
    _format_admin_window_label,
    _safe_plot_render,
)


RUNPOD_REST_API_BASE = os.getenv(
    "RUNPOD_REST_API_BASE",
    "https://rest.runpod.io/v1",
).strip().rstrip("/")
RUNPOD_GRAPHQL_API_URL = os.getenv(
    "RUNPOD_GRAPHQL_API_URL",
    "https://api.runpod.io/graphql",
).strip()
RUNPOD_BILLING_TIMEOUT_S = float(
    os.getenv("RUNPOD_BILLING_TIMEOUT_S", "20")
)
RUNPOD_BILLING_TABLE_LIMIT = max(
    5,
    int(os.getenv("RUNPOD_BILLING_TABLE_LIMIT", "20")),
)
RUNPOD_MONTHLY_BUDGET_USD = max(
    0.0,
    float(os.getenv("RUNPOD_MONTHLY_BUDGET_USD", "200")),
)
WORKFLOW_STATUS_CONFIGS: list[tuple[str, str]] = [
    ("General_Enhancement", "General Enhancement"),
    ("seed", "Pro Upscaler"),
    (
        os.getenv(
            "REFERENCE_GENERATOR_RUNPOD_ENVIRONMENT",
            "reference_generator",
        ),
        "Reference Generator",
    ),
    (
        os.getenv(
            "FLUX2_KLEIN_RUNPOD_ENVIRONMENT",
            "flux2_klein",
        ),
        "Qwen Edit",
    ),
]


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
