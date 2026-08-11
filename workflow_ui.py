"""Gradio helpers shared by every Momi Forge workflow tab.

Each workflow module (General Enhancement, Pro Upscaler, Reference Generator,
Qwen Edit) needs the same three things: the caller's request headers, whether
the caller is an admin, and a debug dump of the payload it sent to RunPod.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import gradio as gr

from auth_service import get_auth_service

WORKFLOW_DEBUG_JSON_DIR = Path(
    os.getenv(
        "WORKFLOW_DEBUG_JSON_DIR",
        str(Path(__file__).resolve().parent / "trace_logs" / "workflow_debug"),
    )
)


def request_header(request: gr.Request, key: str) -> str | None:
    headers = getattr(request, "headers", None) or {}
    return headers.get(key) or headers.get(key.lower()) or headers.get(key.title())


def is_admin_identity(email: str | None) -> bool:
    normalized_email = (email or "").strip()
    if not normalized_email:
        return False
    identity = get_auth_service().get_identity(normalized_email)
    return str(getattr(identity, "role", "") or "").strip().lower() == "admin"


def debug_checkbox_visibility_update(request: gr.Request):
    return gr.update(
        visible=is_admin_identity(getattr(request, "username", None)),
        value=False,
    )


def save_workflow_debug_json(
    payload: dict[str, Any],
    *,
    workflow_name: str,
    task_id: str,
    prefix: str,
    debug_dir: Path | None = None,
) -> Path:
    """Write the ComfyUI workflow we submitted to disk for debugging."""
    workflow_payload: Any = payload
    if isinstance(payload, dict):
        input_payload = payload.get("input")
        if isinstance(input_payload, dict) and isinstance(input_payload.get("workflow"), dict):
            workflow_payload = input_payload["workflow"]

    target_dir = debug_dir or WORKFLOW_DEBUG_JSON_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_workflow = re.sub(r"[^a-zA-Z0-9_-]+", "_", workflow_name or "").strip("_") or "workflow"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_path = target_dir / f"{prefix}_{safe_workflow}_{task_id}_{timestamp}.json"
    with open(debug_path, "w", encoding="utf-8") as outfile:
        json.dump(workflow_payload, outfile, indent=2)
    return debug_path
