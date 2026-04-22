from __future__ import annotations

import asyncio
import base64
import binascii
import html
import io
import json
import logging
import math
import os
import random
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Reduce Gradio/HuggingFace telemetry chatter unless explicitly overridden.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import aiohttp
import gradio as gr
import numpy as np
from PIL import Image
from gradio_imageslider import ImageSlider

from auth_service import get_auth_service
from runpod_api_class import RunpodAPI
from task_tracking import TaskTracker, WorkflowContext, extract_artifacts_from_status
from utils import (
    _extract_progress_signal,
    _extract_stream_progress_signals,
    _has_final_output_payload,
    _render_idle_status,
    prepare_json,
    save_input_image_as_base64,
)


_app_log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _app_log_level, logging.INFO))
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("gradio").setLevel(logging.WARNING)

APP_TITLE = "Momi Forge"
WORKFLOW_NAME = os.getenv("GENERAL_WORKFLOW_NAME", "General Enhancement")
APP_DEBUG = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_QUIET = os.getenv("APP_QUIET", "1").strip().lower() in {"1", "true", "yes", "on"}
APP_SERVER_NAME = os.getenv("APP_SERVER_NAME", "0.0.0.0")
APP_SERVER_PORT = int(os.getenv("APP_SERVER_PORT", "8170"))

RUNPOD_STATUS_POLL_INTERVAL_S = max(
    0.1,
    float(os.getenv("RUNPOD_STATUS_POLL_INTERVAL_S", "0.4")),
)
MAX_STATUS_POLLS = int(os.getenv("RUNPOD_MAX_STATUS_POLLS", "1800"))
FINALIZATION_HINT_GRACE_POLLS = int(
    os.getenv("RUNPOD_FINALIZATION_HINT_GRACE_POLLS", "120")
)
MAX_CONSECUTIVE_STATUS_ERRORS = int(
    os.getenv("RUNPOD_MAX_CONSECUTIVE_STATUS_ERRORS", "8")
)
RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S = max(
    0.1,
    float(
        os.getenv(
            "RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S",
            str(RUNPOD_STATUS_POLL_INTERVAL_S),
        )
    ),
)
RUNPOD_STREAM_ENABLED = os.getenv("RUNPOD_STREAM_ENABLED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TERMINAL_FAILURES = {"FAILED", "ERROR", "TIMED_OUT"}
ACTIVE_STATES = {"IN_QUEUE", "IN_PROGRESS", "RUNNING"}

GENERAL_WORKFLOW_FILE = os.getenv("GENERAL_WORKFLOW_FILE", "").strip()
GENERAL_WORKFLOW_PATH = os.getenv("GENERAL_WORKFLOW_PATH", "").strip()
SAVE_DEBUG_PROMPT_JSON = os.getenv("SAVE_DEBUG_PROMPT_JSON", "0") == "1"
WORKFLOW_DEBUG_JSON_DIR = Path(
    os.getenv(
        "WORKFLOW_DEBUG_JSON_DIR",
        str(Path(__file__).resolve().parent / "trace_logs" / "workflow_debug"),
    )
)


# Workflow nodes used by the Gradio routing logic (workflow_api_flux_dev_1.19).
NODE_IMAGE_INPUT = "63"
NODE_MASK_INPUT = "86"
NODE_MASK_ROUTER = "13"
NODE_MASK_ROUTE_DRAWN = "88"
NODE_MASK_ROUTE_EMPTY = "85"

NODE_SD_SAMPLER = "32"
NODE_SD_LORA = "37"
NODE_SD_PASS = "66"
NODE_SD_DECODE = "64"

NODE_ADV_PREP = "79"
NODE_ADV_PASS = "69"
NODE_FLUX_RANDOM_NOISE = "26"
NODE_FLUX_SCHEDULER = "23"
NODE_FLUX_SAMPLER = "22"
NODE_FLUX_DECODE = "21"
NODE_IMAGE_BATCH = "12"
NODE_FLUX_BLEND = "74"

NODE_BODY_RESIZE = "53"
NODE_BODY_SAMPLER_1 = "52"  # body
NODE_BODY_SAMPLER_2 = "54"  # face

NODE_STITCH = "82"
NODE_SAVE_IMAGE = "83"
NODE_QWEN_PROMPT = "33"
NODE_QWEN_MERGE = "30"
NODE_PROMPT_TEXT = "35"
TILE_DIVISOR_PX = 900

COMFY_LOG_PATTERN = re.compile(r"^\[comfy-log\]\[(?P<phase>[^\]]+)\]\s*(?P<message>.*)$")
NODE_PROGRESS_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+(?P<done>\d+)/(?P<total>\d+)$"
)
ENHANCE_ITEM_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+done=(?P<done>\d+)(?:\s+total=(?P<total>\d+))?$"
)
ENHANCE_STATE_PATTERN = re.compile(
    r"^(?:node=(?P<node>[^ ]+)\s+)?done=(?P<done>\d+)(?:\s+total=(?P<total>\d+))?$"
)
ENHANCE_STEP_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+item=(?P<item_done>\d+)(?:/(?P<item_total>\d+))?\s+step=(?P<step_done>\d+)/(?P<step_total>\d+)$"
)
FRACTION_PATTERN = re.compile(r"^(?P<done>\d+)(?:/(?P<total>\d+))?$")
ENHANCE_SAMPLE_NODE_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+(?P<done>\d+)(?:/(?P<total>\d+))?$"
)
RUNNING_NODE_PATTERN = re.compile(
    r"^Running node (?P<node>\d+(?::\d+)?):\s*(?P<label>.+)$"
)
NODE_ID_PREFIX_PATTERN = re.compile(r"^(?P<node>\d+(?::\d+)?)\b")

PHASE_PREPARATION = "Preparation"
PHASE_WRAP_UP = "Wrap-up"
PHASE_COMPLETED = "Completed"

STAGE_GENERAL = "general"
STAGE_ADVANCE = "advance"
STAGE_BODY = "body"
STAGE_FACE = "face"

COUNT_MODE_CYCLE = "cycle"
COUNT_MODE_ITEM_COUNTER = "item_counter"
COUNT_MODE_FRACTION_DIRECT = "fraction_direct"

STAGE_ORDER = [STAGE_GENERAL, STAGE_ADVANCE, STAGE_BODY, STAGE_FACE]
STAGE_LABELS = {
    STAGE_GENERAL: "General Enhancement",
    STAGE_ADVANCE: "Advance Details",
    STAGE_BODY: "Body Enhancement",
    STAGE_FACE: "Face Enhancement",
}
STAGE_UNIT_LABELS = {
    STAGE_GENERAL: "tile",
    STAGE_ADVANCE: "tile",
    STAGE_BODY: "person",
    STAGE_FACE: "face",
}
# Runtime event semantics:
# - [comfy-log][progress] node=N a/b: sampler step progress for the *current* item.
# - [comfy-log][enhance-step] node=N item=i step=a/b: same sampler-step signal, with item context.
# - [comfy-log][enhance-item]/[enhance-state] node=N done=i: completed item count for that node.
# - [enhance_done=...] suffix in free text: summary/debug hint only; not authoritative per-node state.
# Therefore, totals/items should come from enhance-item/state (or cycle fallback), not from a/b directly.
SAMPLER_NODE_TO_STAGE = {
    NODE_SD_SAMPLER: STAGE_GENERAL,
    NODE_FLUX_SAMPLER: STAGE_ADVANCE,
    NODE_BODY_SAMPLER_1: STAGE_BODY,
    NODE_BODY_SAMPLER_2: STAGE_FACE,
}
NODE_STAGE_HINTS = {
    NODE_MASK_ROUTER: STAGE_GENERAL,
    NODE_SD_PASS: STAGE_GENERAL,
    NODE_SD_SAMPLER: STAGE_GENERAL,
    NODE_SD_DECODE: STAGE_GENERAL,
    NODE_ADV_PREP: STAGE_ADVANCE,
    NODE_ADV_PASS: STAGE_ADVANCE,
    NODE_FLUX_SAMPLER: STAGE_ADVANCE,
    NODE_FLUX_DECODE: STAGE_ADVANCE,
    NODE_BODY_RESIZE: STAGE_BODY,
    NODE_BODY_SAMPLER_1: STAGE_BODY,
    NODE_BODY_SAMPLER_2: STAGE_FACE,
    NODE_STITCH: STAGE_FACE,
}
NODE_STATUS_HINTS = {
    NODE_MASK_ROUTER: "General Enhancement - preparing masked tiles",
    NODE_SD_PASS: "General Enhancement - preparing tiles",
    NODE_SD_SAMPLER: "General Enhancement - sampling tiles",
    NODE_SD_DECODE: "General Enhancement - decoding tiles",
    NODE_ADV_PREP: "Advance Details - preparing tiles",
    NODE_ADV_PASS: "Advance Details - preparing tiles",
    NODE_FLUX_SAMPLER: "Advance Details - sampling tiles",
    NODE_FLUX_DECODE: "Advance Details - decoding tiles",
    NODE_BODY_RESIZE: "Body Enhancement - preparing detections",
    NODE_BODY_SAMPLER_1: "Body Enhancement - sampling detected persons",
    NODE_BODY_SAMPLER_2: "Face Enhancement - sampling detected faces",
    NODE_STITCH: "Compositing result...",
    NODE_SAVE_IMAGE: "Saving final image...",
}

STAGE_WRAP_MILESTONES = {
    NODE_SD_DECODE: 0.20,
    NODE_FLUX_DECODE: 0.45,
    NODE_STITCH: 0.80,
    NODE_SAVE_IMAGE: 0.93,
}

SAMPLING_PROGRESS_CEILING = max(
    70,
    min(
        98,
        int(os.getenv("GENERAL_PROGRESS_SAMPLING_CEILING", "92")),
    ),
)


auth_service = get_auth_service()
APP_ENVIRONMENT = os.getenv("APP_ENVIRONMENT", "seed")
WORKFLOW_VERSION = os.getenv("WORKFLOW_VERSION_GENERAL", "1.19")
WORKFLOW_CATEGORY = os.getenv("WORKFLOW_CATEGORY_GENERAL", "enhancement")
WORKFLOW_TYPE = os.getenv("WORKFLOW_TYPE_GENERAL", "image")


def _request_header(request: gr.Request, key: str) -> str | None:
    headers = getattr(request, "headers", None) or {}
    return headers.get(key) or headers.get(key.lower()) or headers.get(key.title())


def _is_admin_identity(email: str | None) -> bool:
    normalized_email = (email or "").strip()
    if not normalized_email:
        return False
    identity = auth_service.get_identity(normalized_email)
    return str(getattr(identity, "role", "") or "").strip().lower() == "admin"


def _debug_checkbox_visibility_update(request: gr.Request):
    return gr.update(visible=_is_admin_identity(getattr(request, "username", None)), value=False)


def _save_workflow_debug_json(
    payload: dict[str, Any],
    *,
    workflow_name: str,
    task_id: str,
) -> Path:
    workflow_payload: Any = payload
    if isinstance(payload, dict):
        input_payload = payload.get("input")
        if isinstance(input_payload, dict) and isinstance(input_payload.get("workflow"), dict):
            workflow_payload = input_payload["workflow"]

    WORKFLOW_DEBUG_JSON_DIR.mkdir(parents=True, exist_ok=True)
    safe_workflow = re.sub(r"[^a-zA-Z0-9_-]+", "_", (workflow_name or WORKFLOW_NAME)).strip("_") or "workflow"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_path = WORKFLOW_DEBUG_JSON_DIR / f"general_{safe_workflow}_{task_id}_{timestamp}.json"
    with open(debug_path, "w", encoding="utf-8") as outfile:
        json.dump(workflow_payload, outfile, indent=2)
    return debug_path


def _resolve_general_workflow_path() -> Path:
    if GENERAL_WORKFLOW_PATH:
        configured = Path(GENERAL_WORKFLOW_PATH)
        if configured.exists():
            return configured

    script_dir = Path(__file__).resolve().parent
    candidate_files = [
        GENERAL_WORKFLOW_FILE,
        "workflow_api_flux_dev_1.19.json",
        "workflow_api_flux_dev_1.19 .json",
        "workflow_api_flux_dev_1.17.json",
        "workflow_api_flux_dev_1.17 .json",
        "workflow_api_flux.json",
    ]
    candidate_files = [name for name in candidate_files if name]

    candidate_dirs = [
        script_dir / "api_workflow",
        script_dir / "api_workflow" / "New_runpod",
        script_dir.parent / "api_workflow",
        script_dir.parent / "api_workflow" / "New_runpod",
    ]

    for folder in candidate_dirs:
        for filename in candidate_files:
            path = folder / filename
            if path.exists():
                return path

    raise FileNotFoundError(
        "Could not find the General Enhancement workflow file in the expected api_workflow folders."
    )


def _to_numpy_image(image: Any) -> np.ndarray:
    if image is None:
        raise ValueError("No image provided.")

    if isinstance(image, Image.Image):
        return np.array(image)

    if isinstance(image, np.ndarray):
        return image

    if isinstance(image, (bytes, bytearray)):
        return np.array(Image.open(io.BytesIO(image)))

    if isinstance(image, str):
        return np.array(Image.open(image))

    raise TypeError(f"Unsupported image type: {type(image)}")


def _normalize_mask(mask_array: np.ndarray) -> np.ndarray:
    if mask_array.ndim == 3:
        if mask_array.shape[2] >= 4:
            mask = mask_array[:, :, 3]
        else:
            mask = mask_array[:, :, 0]
    else:
        mask = mask_array

    mask = np.asarray(mask)
    if mask.dtype != np.uint8:
        mask = np.clip(mask, 0, 255).astype(np.uint8)
    return mask


def _extract_editor_background_and_mask(
    image_editor_value: Any,
) -> tuple[np.ndarray, np.ndarray, bool]:
    if not isinstance(image_editor_value, dict):
        raise ValueError("Image editor payload is invalid.")

    background_raw = image_editor_value.get("background")
    layers_raw = image_editor_value.get("layers") or []

    if background_raw is None:
        raise ValueError("No input image provided.")

    background = _to_numpy_image(background_raw)
    height, width = background.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    has_drawn_mask = False

    target_color = np.array([204, 50, 50], dtype=np.int16)
    tolerance = 24

    if isinstance(layers_raw, list):
        for layer in layers_raw:
            layer_np = _to_numpy_image(layer)
            if layer_np.ndim < 3:
                continue

            rgb = layer_np[:, :, :3].astype(np.int16)
            if layer_np.shape[2] >= 4:
                alpha = layer_np[:, :, 3] > 0
            else:
                alpha = np.any(layer_np[:, :, :3] > 0, axis=-1)

            painted = (np.abs(rgb - target_color) <= tolerance).all(axis=-1) & alpha
            if np.any(painted):
                mask[painted] = 255
                has_drawn_mask = True

    return background, mask, has_drawn_mask


async def _read_url_image(url: str) -> Image.Image:
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.read()
    return Image.open(io.BytesIO(data))


async def _decode_output_image(status: dict[str, Any]) -> Image.Image:
    output = status.get("output") or {}
    if not isinstance(output, dict):
        raise ValueError("Job completed without a valid output payload.")

    message = output.get("message")
    if isinstance(message, str):
        message = [message]

    if isinstance(message, list):
        for entry in message:
            if not isinstance(entry, str):
                continue

            if entry.startswith(("http://", "https://")):
                return await _read_url_image(entry)

            b64 = entry.split(",", 1)[1] if entry.startswith("data:") else entry
            try:
                decoded = base64.b64decode(b64, validate=True)
                return Image.open(io.BytesIO(decoded))
            except (binascii.Error, ValueError):
                continue

    images = output.get("images") or []
    if isinstance(images, list):
        for item in images:
            if not isinstance(item, dict):
                continue

            data = item.get("data")
            item_type = str(item.get("type") or "").lower()

            if isinstance(data, str) and (
                item_type in {"s3_url", "url"} or data.startswith(("http://", "https://"))
            ):
                return await _read_url_image(data)

            if isinstance(data, str) and item_type in {"base64", "b64"}:
                b64 = data.split(",", 1)[1] if data.startswith("data:") else data
                return Image.open(io.BytesIO(base64.b64decode(b64)))

    raise ValueError("No decodable image found in RunPod output.")


def _extract_error_message(status: dict[str, Any]) -> str:
    parts: list[str] = []
    state = (status.get("status") or "UNKNOWN").upper()
    parts.append(f"RunPod status: {state}")

    for key in ("error", "message"):
        value = status.get(key)
        if value:
            parts.append(str(value))

    output = status.get("output") or {}
    if isinstance(output, dict):
        for key in ("error", "message"):
            value = output.get(key)
            if value and not isinstance(value, list):
                parts.append(str(value))

        for key in ("details", "errors"):
            value = output.get(key)
            if isinstance(value, list):
                parts.extend(str(v) for v in value if v)
            elif value:
                parts.append(str(value))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in parts:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return "\n".join(deduped)


def _extract_node_id(text: str | None) -> str | None:
    if not text:
        return None
    match = NODE_ID_PREFIX_PATTERN.match(text.strip())
    if not match:
        return None
    return match.group("node")


def _estimate_tile_count(width: int, height: int) -> tuple[int, int, int]:
    safe_width = max(int(width or 0), 1)
    safe_height = max(int(height or 0), 1)
    # Keep this aligned with workflow node `SimpleMath+` (a/b), which
    # returns `INT` via Python's round(result), not ceil().
    columns = max(1, int(round(safe_width / TILE_DIVISOR_PX)))
    rows = max(1, int(round(safe_height / TILE_DIVISOR_PX)))
    return columns, rows, columns * rows


def _create_stage_state(
    *,
    enabled: bool,
    label: str,
    unit_label: str,
    node_id: str,
    total: int | None,
    dynamic_total: bool,
    count_mode: str = COUNT_MODE_CYCLE,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "label": label,
        "unit_label": unit_label,
        "node_id": node_id,
        "count_mode": count_mode,
        "total": total if enabled else 0,
        "dynamic_total": dynamic_total,
        "done": 0,
        "started": False,
        "finished": not enabled,
        "step_done": None,
        "step_total": None,
        "last_step": None,
        "last_total": None,
        "cycle_complete": False,
        "peak_step": 0,
        "step_item": None,
        "runtime_done_events_seen": False,
    }


def _init_progress_tracker(
    *,
    image_width: int,
    image_height: int,
    general_enhance: bool,
    advance_details: bool,
    body_enhance: bool,
) -> dict[str, Any]:
    columns, rows, tile_count = _estimate_tile_count(image_width, image_height)
    return {
        "phase": PHASE_PREPARATION,
        "current_stage": None,
        "current_status": "Preparing workflow...",
        "wrap_ratio": 0.0,
        "tile_columns": columns,
        "tile_rows": rows,
        "tile_count": tile_count,
        "stages": {
            STAGE_GENERAL: _create_stage_state(
                enabled=general_enhance,
                label=STAGE_LABELS[STAGE_GENERAL],
                unit_label=STAGE_UNIT_LABELS[STAGE_GENERAL],
                node_id=NODE_SD_SAMPLER,
                total=tile_count if general_enhance else 0,
                dynamic_total=False,
                count_mode=COUNT_MODE_ITEM_COUNTER,
            ),
            STAGE_ADVANCE: _create_stage_state(
                enabled=advance_details,
                label=STAGE_LABELS[STAGE_ADVANCE],
                unit_label=STAGE_UNIT_LABELS[STAGE_ADVANCE],
                node_id=NODE_FLUX_SAMPLER,
                total=tile_count if advance_details else 0,
                dynamic_total=False,
                count_mode=COUNT_MODE_ITEM_COUNTER,
            ),
            STAGE_BODY: _create_stage_state(
                enabled=body_enhance,
                label=STAGE_LABELS[STAGE_BODY],
                unit_label=STAGE_UNIT_LABELS[STAGE_BODY],
                node_id=NODE_BODY_SAMPLER_1,
                total=None,
                dynamic_total=True,
                count_mode=COUNT_MODE_CYCLE,
            ),
            STAGE_FACE: _create_stage_state(
                enabled=body_enhance,
                label=STAGE_LABELS[STAGE_FACE],
                unit_label=STAGE_UNIT_LABELS[STAGE_FACE],
                node_id=NODE_BODY_SAMPLER_2,
                total=None,
                dynamic_total=True,
                count_mode=COUNT_MODE_CYCLE,
            ),
        },
    }


def _effective_stage_total(stage: dict[str, Any]) -> int:
    if not stage.get("enabled"):
        return 0

    mode = stage.get("count_mode")
    total = stage.get("total")
    if isinstance(total, int):
        return max(total, 0)

    provisional = int(stage.get("done") or 0)
    step_item = stage.get("step_item")
    if isinstance(step_item, int) and step_item > 0:
        provisional = max(provisional, step_item)
    elif stage.get("started") and not stage.get("finished"):
        if mode == COUNT_MODE_FRACTION_DIRECT:
            provisional = max(provisional, 1)
        else:
            provisional = max(provisional + (0 if stage.get("cycle_complete") else 1), 1)
    else:
        provisional = max(provisional, 1)
    return provisional


def _stage_total_for_overall(stage: dict[str, Any]) -> int:
    if not stage.get("enabled"):
        return 0
    total = stage.get("total")
    if isinstance(total, int):
        return max(total, 0)
    return max(_effective_stage_total(stage), 1)


def _stage_completed_units(stage: dict[str, Any]) -> float:
    if not stage.get("enabled"):
        return 0.0

    mode = stage.get("count_mode")
    completed = float(stage.get("done") or 0)
    step_done = stage.get("step_done")
    step_total = stage.get("step_total")

    if mode == COUNT_MODE_ITEM_COUNTER:
        step_item = stage.get("step_item")
        if (
            stage.get("started")
            and not stage.get("finished")
            and isinstance(step_done, int)
            and isinstance(step_total, int)
            and step_total > 0
            and isinstance(step_item, int)
            and step_item > int(stage.get("done") or 0)
        ):
            completed += max(0.0, min(1.0, step_done / step_total))
        return completed

    if mode == COUNT_MODE_FRACTION_DIRECT:
        return completed

    if (
        stage.get("started")
        and not stage.get("finished")
        and isinstance(step_done, int)
        and isinstance(step_total, int)
        and step_total > 0
        and not stage.get("cycle_complete")
    ):
        completed += max(0.0, min(1.0, step_done / step_total))
    return completed


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _compute_general_overall_percent(
    tracker: dict[str, Any],
    *,
    completed: bool = False,
) -> int:
    if completed:
        return 100

    enabled_stages = [
        tracker["stages"][stage_key]
        for stage_key in STAGE_ORDER
        if tracker["stages"][stage_key].get("enabled")
    ]
    if not enabled_stages:
        wrap_ratio = _clamp_ratio(tracker.get("wrap_ratio") or 0.0)
        if tracker.get("phase") == PHASE_WRAP_UP or wrap_ratio > 0:
            return max(1, min(99, int(round(wrap_ratio * 99))))
        return 0

    total_units = sum(_stage_total_for_overall(stage) for stage in enabled_stages)
    completed_units = sum(_stage_completed_units(stage) for stage in enabled_stages)

    sampling_ratio = (
        _clamp_ratio(completed_units / total_units)
        if total_units > 0
        else 0.0
    )
    wrap_ratio = _clamp_ratio(tracker.get("wrap_ratio") or 0.0)
    wrap_span = max(1, 99 - SAMPLING_PROGRESS_CEILING)

    sampling_percent = sampling_ratio * SAMPLING_PROGRESS_CEILING
    if tracker.get("phase") == PHASE_WRAP_UP or wrap_ratio > 0:
        percent = int(round(sampling_percent + (wrap_ratio * wrap_span)))
        if tracker.get("phase") == PHASE_WRAP_UP:
            percent = max(percent, min(SAMPLING_PROGRESS_CEILING, 99))
    else:
        percent = int(round(sampling_percent))

    return max(0, min(99, percent))


def _stage_display_value(stage: dict[str, Any]) -> str:
    if not stage.get("enabled"):
        return "Off"

    total = stage.get("total")
    done = int(stage.get("done") or 0)
    if isinstance(total, int):
        if total <= 0:
            return "0/0" if stage.get("finished") else "Pending"
        return f"{done}/{total}"

    if not stage.get("started") and done == 0:
        return "Pending"

    effective_total = _effective_stage_total(stage)
    if effective_total > 0:
        return f"{done}/{effective_total}"
    return f"{done} done"


def _stage_current_index(stage: dict[str, Any]) -> int:
    mode = stage.get("count_mode")
    current_index = int(stage.get("done") or 0)

    if mode == COUNT_MODE_ITEM_COUNTER:
        step_item = stage.get("step_item")
        if isinstance(step_item, int) and step_item > 0:
            return max(step_item, current_index, 1)
        return max(current_index, 1)

    if mode == COUNT_MODE_FRACTION_DIRECT:
        if stage.get("started") and current_index <= 0:
            return 1
        return max(current_index, 1)

    if stage.get("started") and not stage.get("finished") and not stage.get("cycle_complete"):
        current_index += 1
    return max(current_index, 1)


def _stage_sampling_status(stage: dict[str, Any]) -> str:
    label = stage["label"]
    unit_label = stage["unit_label"]
    effective_total = _effective_stage_total(stage)
    current_index = _stage_current_index(stage)

    if effective_total > 0:
        prefix = f"{label} - {unit_label} {min(current_index, effective_total)} of {effective_total}"
    else:
        prefix = f"{label} - {unit_label} {current_index}"

    step_done = stage.get("step_done")
    step_total = stage.get("step_total")
    if isinstance(step_done, int) and isinstance(step_total, int) and step_total > 0:
        prefix += f" (sampling step {step_done} of {step_total})"
    return prefix


def _render_general_notice_panel(
    title: str,
    message: str,
    *,
    percent: int = 0,
    accent: str = "#38bdf8",
) -> str:
    safe_title = html.escape(title)
    safe_message = html.escape(message).replace("\n", "<br>")
    safe_percent = max(0, min(100, int(percent)))
    return f"""
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:14px 16px;color:#e2e8f0;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
    <div style="font-weight:700;font-size:15px;">{safe_title}</div>
    <div style="font-weight:700;font-size:18px;color:{accent};">{safe_percent}%</div>
  </div>
  <div style="margin-top:10px;height:10px;background:#1e293b;border-radius:999px;overflow:hidden;">
    <div style="height:10px;width:{safe_percent}%;background:linear-gradient(90deg,#22d3ee,#3b82f6);"></div>
  </div>
  <div style="margin-top:12px;font-size:13px;line-height:1.5;">{safe_message}</div>
</div>
"""


def _render_general_progress_panel(
    tracker: dict[str, Any],
    *,
    overall_percent: int,
) -> str:
    safe_phase = html.escape(str(tracker.get("phase") or PHASE_PREPARATION))
    safe_status = html.escape(str(tracker.get("current_status") or "Processing..."))
    stage_cards: list[str] = [
        f"""
    <div style="background:#111827;border:1px solid #1f2937;border-radius:10px;padding:8px 10px;">
      <div style="opacity:.75;font-size:11px;text-transform:uppercase;letter-spacing:.3px;">Phase</div>
      <div style="font-weight:600;margin-top:2px;">{safe_phase}</div>
    </div>
"""
    ]

    for stage_key in STAGE_ORDER:
        stage = tracker["stages"][stage_key]
        if not stage.get("enabled"):
            continue
        label = html.escape(stage["label"])
        value = html.escape(_stage_display_value(stage))
        stage_cards.append(
            f"""
    <div style="background:#111827;border:1px solid #1f2937;border-radius:10px;padding:8px 10px;">
      <div style="opacity:.75;font-size:11px;text-transform:uppercase;letter-spacing:.3px;">{label}</div>
      <div style="font-weight:600;margin-top:2px;">{value}</div>
    </div>
"""
        )

    tile_note = ""
    if tracker["stages"][STAGE_GENERAL].get("enabled") or tracker["stages"][STAGE_ADVANCE].get("enabled"):
        tile_note = (
            f"Estimated tiled workload: {tracker['tile_count']} tile(s) "
            f"({tracker['tile_columns']} x {tracker['tile_rows']})."
        )

    safe_tile_note = html.escape(tile_note)
    return f"""
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:14px 16px;color:#e2e8f0;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
    <div style="font-weight:700;font-size:15px;">Processing Your Request</div>
    <div style="font-weight:700;font-size:18px;color:#38bdf8;">{overall_percent}%</div>
  </div>
  <div style="margin-top:10px;height:10px;background:#1e293b;border-radius:999px;overflow:hidden;">
    <div style="height:10px;width:{overall_percent}%;background:linear-gradient(90deg,#22d3ee,#3b82f6);"></div>
  </div>
  <div style="margin-top:12px;font-size:13px;font-weight:600;">{safe_status}</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-top:12px;font-size:13px;">
    {''.join(stage_cards)}
  </div>
  <div style="margin-top:10px;font-size:12px;opacity:.78;">{safe_tile_note}</div>
</div>
"""


def _reconcile_stage_cycle(
    stage: dict[str, Any],
    *,
    mark_finished: bool = False,
    near_complete_ratio: float = 0.85,
) -> None:
    if not stage.get("enabled"):
        return

    last_total = stage.get("last_total")
    peak_step = int(stage.get("peak_step") or 0)
    allow_near_complete_reconcile = not (
        stage.get("count_mode") == COUNT_MODE_ITEM_COUNTER
        and stage.get("runtime_done_events_seen")
    )
    if (
        allow_near_complete_reconcile
        and stage.get("started")
        and not stage.get("cycle_complete")
        and isinstance(last_total, int)
        and last_total > 0
        and peak_step >= max(1, int(math.ceil(last_total * near_complete_ratio)))
    ):
        stage["done"] += 1

    if mark_finished:
        if stage.get("dynamic_total"):
            current_total = stage.get("total")
            stage["total"] = max(
                int(current_total or 0),
                int(stage.get("done") or 0),
            )
        else:
            fixed_total = stage.get("total")
            if isinstance(fixed_total, int):
                done_value = max(0, int(stage.get("done") or 0))
                if fixed_total > 0 and done_value >= (fixed_total - 1):
                    stage["done"] = fixed_total
                else:
                    stage["done"] = min(done_value, fixed_total)
        stage["finished"] = True

    fixed_total = stage.get("total")
    if isinstance(fixed_total, int) and fixed_total >= 0:
        stage["done"] = min(int(stage.get("done") or 0), fixed_total)

    stage["step_done"] = None
    stage["step_total"] = None
    stage["last_step"] = None
    stage["last_total"] = None
    stage["cycle_complete"] = False
    stage["peak_step"] = 0
    stage["step_item"] = None


def _transition_to_stage(tracker: dict[str, Any], stage_key: str | None) -> None:
    current_stage = tracker.get("current_stage")
    if stage_key == current_stage:
        return

    if current_stage in tracker["stages"]:
        _reconcile_stage_cycle(tracker["stages"][current_stage], mark_finished=True)

    if stage_key is None:
        tracker["current_stage"] = None
        tracker["phase"] = PHASE_WRAP_UP
        return

    stage = tracker["stages"].get(stage_key)
    if not stage or not stage.get("enabled"):
        return

    stage["started"] = True
    stage["finished"] = False
    tracker["current_stage"] = stage_key
    tracker["phase"] = stage["label"]


def _mark_wrap_progress(tracker: dict[str, Any], ratio: float) -> None:
    tracker["wrap_ratio"] = max(
        _clamp_ratio(tracker.get("wrap_ratio") or 0.0),
        _clamp_ratio(ratio),
    )


def _set_wrap_status(
    tracker: dict[str, Any],
    message: str,
    *,
    min_wrap_ratio: float | None = None,
) -> None:
    _transition_to_stage(tracker, None)
    tracker["phase"] = PHASE_WRAP_UP
    tracker["current_status"] = message
    if min_wrap_ratio is not None:
        _mark_wrap_progress(tracker, min_wrap_ratio)


def _set_stage_status_from_node(
    tracker: dict[str, Any],
    node_id: str | None,
) -> None:
    if not node_id:
        return

    wrap_milestone = STAGE_WRAP_MILESTONES.get(node_id)
    if wrap_milestone is not None:
        _mark_wrap_progress(tracker, wrap_milestone)

    if node_id == NODE_SAVE_IMAGE:
        _set_wrap_status(
            tracker,
            NODE_STATUS_HINTS[NODE_SAVE_IMAGE],
            min_wrap_ratio=wrap_milestone,
        )
        return

    stage_key = NODE_STAGE_HINTS.get(node_id)
    if not stage_key:
        return

    stage = tracker["stages"].get(stage_key)
    if not stage or not stage.get("enabled"):
        return

    _transition_to_stage(tracker, stage_key)
    tracker["current_status"] = NODE_STATUS_HINTS.get(node_id, stage["label"])


def _set_stage_runtime_total(stage: dict[str, Any], total: int | None) -> None:
    if not isinstance(total, int) or total <= 0:
        return
    stage["total"] = total


def _sync_general_total_with_advance_runtime(tracker: dict[str, Any]) -> None:
    general_stage = tracker["stages"].get(STAGE_GENERAL)
    advance_stage = tracker["stages"].get(STAGE_ADVANCE)
    if (
        not general_stage
        or not advance_stage
        or not general_stage.get("enabled")
        or not advance_stage.get("enabled")
        or not general_stage.get("runtime_done_events_seen")
    ):
        return

    advance_total = advance_stage.get("total")
    general_total = general_stage.get("total")
    advance_done = max(0, int(advance_stage.get("done") or 0))

    if not isinstance(advance_total, int) or advance_total <= 0:
        return

    if not isinstance(general_total, int) or general_total <= 0 or advance_total > general_total:
        general_stage["total"] = advance_total
        general_total = advance_total

    # Both stages operate over the same tile grid; if Advance reports more completed
    # tiles, General cannot be behind in wrap-up/status snapshots.
    general_stage["done"] = min(
        max(int(general_stage.get("done") or 0), advance_done),
        int(general_total),
    )


def _observe_stage_done_count(
    tracker: dict[str, Any],
    *,
    stage_key: str,
    done: int,
    total: int | None = None,
) -> None:
    stage = tracker["stages"][stage_key]
    if not stage.get("enabled"):
        return

    _transition_to_stage(tracker, stage_key)
    stage["started"] = True
    stage["finished"] = False
    first_runtime_done_event = not stage.get("runtime_done_events_seen")
    stage["runtime_done_events_seen"] = True

    _set_stage_runtime_total(stage, total)

    done_value = max(0, int(done))
    current_total = stage.get("total")
    if (
        total is None
        and isinstance(current_total, int)
        and done_value > current_total
    ):
        stage["total"] = done_value

    if first_runtime_done_event:
        stage["done"] = done_value
    else:
        stage["done"] = max(int(stage.get("done") or 0), done_value)
    fixed_total = stage.get("total")
    if isinstance(fixed_total, int) and fixed_total >= 0:
        stage["done"] = min(stage["done"], fixed_total)
    stage["step_item"] = stage["done"]
    stage["step_done"] = None
    stage["step_total"] = None
    stage["cycle_complete"] = True
    if stage_key == STAGE_ADVANCE:
        _sync_general_total_with_advance_runtime(tracker)

    tracker["current_status"] = _stage_sampling_status(stage)


def _observe_stage_item_step(
    tracker: dict[str, Any],
    *,
    stage_key: str,
    item_done: int,
    item_total: int | None,
    step_done: int,
    step_total: int,
) -> None:
    stage = tracker["stages"][stage_key]
    if not stage.get("enabled"):
        return

    _transition_to_stage(tracker, stage_key)
    stage["started"] = True
    stage["finished"] = False

    _set_stage_runtime_total(stage, item_total)

    stage["step_item"] = max(1, int(item_done))
    current_total = stage.get("total")
    if (
        item_total is None
        and isinstance(current_total, int)
        and stage["step_item"] > current_total
    ):
        stage["total"] = stage["step_item"]

    stage["step_done"] = max(0, int(step_done))
    stage["step_total"] = max(1, int(step_total))
    stage["last_step"] = stage["step_done"]
    stage["last_total"] = stage["step_total"]
    stage["peak_step"] = max(int(stage.get("peak_step") or 0), stage["step_done"])

    if stage["runtime_done_events_seen"]:
        stage["cycle_complete"] = (
            stage["step_item"] <= int(stage.get("done") or 0)
            and stage["step_done"] >= stage["step_total"]
        )
    else:
        stage["cycle_complete"] = stage["step_done"] >= stage["step_total"]
        if stage["cycle_complete"]:
            stage["done"] = max(int(stage.get("done") or 0), stage["step_item"])
            fixed_total = stage.get("total")
            if isinstance(fixed_total, int) and fixed_total >= 0:
                stage["done"] = min(stage["done"], fixed_total)

    tracker["current_status"] = _stage_sampling_status(stage)


def _observe_sampler_progress(
    tracker: dict[str, Any],
    *,
    stage_key: str,
    step_done: int,
    step_total: int,
) -> None:
    stage = tracker["stages"][stage_key]
    if not stage.get("enabled"):
        return

    _transition_to_stage(tracker, stage_key)
    stage["started"] = True
    stage["finished"] = False

    last_total = stage.get("last_total")
    last_step = stage.get("last_step")

    if isinstance(last_total, int) and last_total > 0 and last_total != step_total:
        _reconcile_stage_cycle(stage, mark_finished=False)
        last_step = None

    if isinstance(last_step, int) and step_done < last_step:
        _reconcile_stage_cycle(stage, mark_finished=False)

    stage["step_done"] = step_done
    stage["step_total"] = step_total
    stage["last_step"] = step_done
    stage["last_total"] = step_total
    stage["peak_step"] = max(int(stage.get("peak_step") or 0), step_done)

    mode = stage.get("count_mode")
    if mode == COUNT_MODE_FRACTION_DIRECT:
        _set_stage_runtime_total(stage, step_total)
        stage["done"] = max(
            int(stage.get("done") or 0),
            max(0, min(step_done, step_total)),
        )
        if stage_key == STAGE_ADVANCE:
            _sync_general_total_with_advance_runtime(tracker)
        stage["cycle_complete"] = True
    elif mode == COUNT_MODE_ITEM_COUNTER and stage.get("runtime_done_events_seen"):
        stage["cycle_complete"] = step_done >= step_total
    elif step_total > 0 and step_done >= step_total:
        if not stage.get("cycle_complete"):
            stage["done"] += 1
        stage["cycle_complete"] = True
        stage["peak_step"] = 0
    else:
        stage["cycle_complete"] = False

    if mode == COUNT_MODE_ITEM_COUNTER:
        inferred_item = int(stage.get("done") or 0)
        if not stage.get("cycle_complete"):
            inferred_item += 1
        stage["step_item"] = max(1, inferred_item)
    else:
        stage["step_item"] = None

    fixed_total = stage.get("total")
    if (
        mode != COUNT_MODE_FRACTION_DIRECT
        and isinstance(fixed_total, int)
        and fixed_total > 0
        and int(stage.get("done") or 0) > fixed_total
    ):
        stage["total"] = int(stage.get("done") or 0)
        fixed_total = stage["total"]

    if isinstance(fixed_total, int) and fixed_total >= 0:
        stage["done"] = min(int(stage.get("done") or 0), fixed_total)

    tracker["current_status"] = _stage_sampling_status(stage)


def _update_progress_tracker_from_text(
    progress_text: str,
    tracker: dict[str, Any],
) -> None:
    text = progress_text.strip()
    if not text:
        return

    lower = text.lower()
    if tracker["phase"] == PHASE_PREPARATION:
        if "starting job and validating input" in lower:
            tracker["current_status"] = "Starting job and validating input..."
        elif "connected to comfyui worker" in lower:
            tracker["current_status"] = "Connected to ComfyUI worker."
        elif "workflow queued" in lower:
            tracker["current_status"] = "Workflow queued. Waiting for execution..."
        elif "execution started" in lower:
            tracker["current_status"] = "Execution started."

    parsed = COMFY_LOG_PATTERN.match(text)
    if parsed:
        comfy_phase = parsed.group("phase").strip().lower()
        phase_message = parsed.group("message").strip()

        if comfy_phase in {"enhance-item", "enhance-state"}:
            pattern = ENHANCE_ITEM_PATTERN if comfy_phase == "enhance-item" else ENHANCE_STATE_PATTERN
            state_match = pattern.match(phase_message)
            if state_match:
                node_id = (state_match.groupdict().get("node") or NODE_SD_SAMPLER).strip()
                done = int(state_match.group("done"))
                total_raw = state_match.groupdict().get("total")
                total = int(total_raw) if total_raw and total_raw.isdigit() else None
                stage_key = SAMPLER_NODE_TO_STAGE.get(node_id)
                if stage_key and tracker["stages"][stage_key].get("enabled"):
                    _observe_stage_done_count(
                        tracker,
                        stage_key=stage_key,
                        done=done,
                        total=total,
                    )
                    return

        if comfy_phase == "enhance-step":
            step_match = ENHANCE_STEP_PATTERN.match(phase_message)
            if step_match:
                node_id = step_match.group("node")
                stage_key = SAMPLER_NODE_TO_STAGE.get(node_id)
                if stage_key and tracker["stages"][stage_key].get("enabled"):
                    item_total_raw = step_match.group("item_total")
                    _observe_stage_item_step(
                        tracker,
                        stage_key=stage_key,
                        item_done=int(step_match.group("item_done")),
                        item_total=int(item_total_raw) if item_total_raw and item_total_raw.isdigit() else None,
                        step_done=int(step_match.group("step_done")),
                        step_total=int(step_match.group("step_total")),
                    )
                    return

        if comfy_phase == "enhance-sample":
            # Prefer node-qualified samples; plain "N" / "N/M" is ambiguous when
            # multiple enhancement sampler nodes are active in one workflow.
            node_sample = ENHANCE_SAMPLE_NODE_PATTERN.match(phase_message)
            if node_sample:
                node_id = node_sample.group("node")
                stage_key = SAMPLER_NODE_TO_STAGE.get(node_id)
                if stage_key and tracker["stages"][stage_key].get("enabled"):
                    done = int(node_sample.group("done"))
                    total_raw = node_sample.group("total")
                    _observe_stage_done_count(
                        tracker,
                        stage_key=stage_key,
                        done=done,
                        total=int(total_raw) if total_raw and total_raw.isdigit() else None,
                    )
                    return

        if comfy_phase == "progress":
            node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
            if node_progress:
                node_id = node_progress.group("node")
                done = int(node_progress.group("done"))
                total = int(node_progress.group("total"))
                stage_key = SAMPLER_NODE_TO_STAGE.get(node_id)
                if stage_key and tracker["stages"][stage_key].get("enabled"):
                    _observe_sampler_progress(
                        tracker,
                        stage_key=stage_key,
                        step_done=done,
                        step_total=total,
                    )
                    return

        if comfy_phase in {"node", "executed"}:
            node_id = _extract_node_id(phase_message)
            if comfy_phase == "executed":
                stage_key = SAMPLER_NODE_TO_STAGE.get(node_id or "")
                if stage_key and tracker["stages"][stage_key].get("enabled"):
                    _reconcile_stage_cycle(
                        tracker["stages"][stage_key],
                        mark_finished=False,
                    )
            _set_stage_status_from_node(tracker, node_id)
            return

        if comfy_phase == "execution" and "finished" in phase_message.lower():
            _set_wrap_status(
                tracker,
                "Execution finished. Collecting output...",
                min_wrap_ratio=0.90,
            )
            return

        if comfy_phase == "status" and "queue_remaining=0" in phase_message.lower():
            _set_wrap_status(
                tracker,
                "Finalizing output...",
                min_wrap_ratio=0.88,
            )
            return

    running_node = RUNNING_NODE_PATTERN.match(text)
    if running_node:
        _set_stage_status_from_node(tracker, running_node.group("node").strip())
        return

    if lower.startswith("still running"):
        if tracker.get("current_status"):
            return
        tracker["current_status"] = "Still running..."
        return

    if "fetching execution history" in lower:
        _set_wrap_status(tracker, "Preparing final output...", min_wrap_ratio=0.94)
    elif "processing output nodes and collecting images" in lower:
        _set_wrap_status(tracker, "Collecting generated images...", min_wrap_ratio=0.96)
    elif "collecting images from node" in lower:
        _set_wrap_status(tracker, "Collecting output image...", min_wrap_ratio=0.97)
    elif "job completed. returning" in lower:
        _set_wrap_status(tracker, "Finalizing output...", min_wrap_ratio=0.99)


def _connect(prompt: dict[str, Any], target_node: str, input_name: str, source_node: str, output_idx: int = 0) -> None:
    prompt[target_node]["inputs"][input_name] = [source_node, output_idx]


def _set_mask_source(prompt: dict[str, Any], source_mask_node: str) -> None:
    prompt[NODE_MASK_ROUTER]["inputs"]["mask"] = [source_mask_node, 0]


def _disconnect_qwen_caption_path(prompt: dict[str, Any]) -> None:
    # Disconnect node 33 (Qwen) from downstream prompt composition for body-only mode.
    merge_inputs = prompt.get(NODE_QWEN_MERGE, {}).get("inputs")
    if isinstance(merge_inputs, dict):
        merge_inputs["text_c"] = ""


def _apply_branch_routing(
    prompt: dict[str, Any],
    *,
    general_enhance: bool,
    advance_details: bool,
    body_enhance: bool,
) -> None:
    # Routing follows workflow_api_flux_dev_1.19 rules:
    # - "A -> B" means node A input points to node B output[0].
    # - Case 7 provided as "63 -> 83" is applied as SaveImage(83) <- 63.

    # Default: keep Qwen merge connected unless a specific case disconnects it.
    merge_inputs = prompt.get(NODE_QWEN_MERGE, {}).get("inputs")
    if isinstance(merge_inputs, dict):
        merge_inputs["text_c"] = [NODE_QWEN_PROMPT, 0]

    if general_enhance and not advance_details and not body_enhance:
        # Case 1: Only General Enhancement
        _connect(prompt, NODE_SD_PASS, "image", NODE_ADV_PREP)        # 66 -> 79
        _connect(prompt, NODE_IMAGE_BATCH, "images", NODE_SD_DECODE)  # 12 -> 64
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_STITCH)      # 83 -> 82
        return

    if (not general_enhance) and advance_details and (not body_enhance):
        # Case 2: Only Advance Details
        _connect(prompt, NODE_ADV_PASS, "image", NODE_ADV_PREP)       # 69 -> 79
        _connect(prompt, NODE_IMAGE_BATCH, "images", NODE_FLUX_DECODE)  # 12 -> 21
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_STITCH)      # 83 -> 82
        return

    if (not general_enhance) and (not advance_details) and body_enhance:
        # Case 3: Only Body Enhancement
        _connect(prompt, NODE_BODY_RESIZE, "image", NODE_IMAGE_INPUT)   # 53 -> 63
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_BODY_SAMPLER_2)  # 83 -> 54
        _disconnect_qwen_caption_path(prompt)  # disconnect node 33 from active path
        return

    if general_enhance and advance_details and (not body_enhance):
        # Case 4: General Enhancement + Advance Details
        _connect(prompt, NODE_SD_PASS, "image", NODE_ADV_PREP)         # 66 -> 79
        _connect(prompt, NODE_ADV_PASS, "image", NODE_SD_DECODE)       # 69 -> 64
        _connect(prompt, NODE_IMAGE_BATCH, "images", NODE_FLUX_DECODE)  # 12 -> 21
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_STITCH)       # 83 -> 82
        return

    if general_enhance and (not advance_details) and body_enhance:
        # Case 5: General Enhancement + Body Enhancement
        _connect(prompt, NODE_SD_PASS, "image", NODE_ADV_PREP)         # 66 -> 79
        _connect(prompt, NODE_IMAGE_BATCH, "images", NODE_SD_DECODE)   # 12 -> 64
        _connect(prompt, NODE_BODY_RESIZE, "image", NODE_STITCH)       # 53 -> 82
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_BODY_SAMPLER_2)  # 83 -> 54
        return

    if (not general_enhance) and advance_details and body_enhance:
        # Case 6: Advance Details + Body Enhancement
        _connect(prompt, NODE_ADV_PASS, "image", NODE_ADV_PREP)        # 69 -> 79
        _connect(prompt, NODE_IMAGE_BATCH, "images", NODE_FLUX_DECODE)  # 12 -> 21
        _connect(prompt, NODE_BODY_RESIZE, "image", NODE_STITCH)       # 53 -> 82
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_BODY_SAMPLER_2)  # 83 -> 54
        return

    if general_enhance and advance_details and body_enhance:
        # All enabled: chain General -> Advance -> Body
        _connect(prompt, NODE_SD_PASS, "image", NODE_ADV_PREP)
        _connect(prompt, NODE_ADV_PASS, "image", NODE_SD_DECODE)
        _connect(prompt, NODE_IMAGE_BATCH, "images", NODE_FLUX_DECODE)
        _connect(prompt, NODE_BODY_RESIZE, "image", NODE_STITCH)
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_BODY_SAMPLER_2)
        return

    # Case 7: None selected -> save original image.
    _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_IMAGE_INPUT)


def _apply_general_workflow_updates(
    prompt: dict[str, Any],
    *,
    image_b64: str,
    mask_b64: str,
    has_drawn_mask: bool,
    general_enhance: bool,
    advance_details: bool,
    additional_detail_pass: float,
    sharpen: float,
    body_enhance: bool,
    body_enhancement_denoise: float,
    face_enhancement_denoise: float,
    details: float,
    general_denoise: float,
    custom_prompt: str,
) -> None:
    prompt[NODE_IMAGE_INPUT]["inputs"]["image"] = image_b64
    prompt[NODE_MASK_INPUT]["inputs"]["image"] = mask_b64

    if has_drawn_mask:
        _set_mask_source(prompt, NODE_MASK_ROUTE_DRAWN)  # 13 mask -> 88
    else:
        _set_mask_source(prompt, NODE_MASK_ROUTE_EMPTY)  # 13 mask -> 85

    cleaned_prompt = str(custom_prompt or "").strip()

    prompt[NODE_PROMPT_TEXT]["inputs"]["text_a"] = cleaned_prompt
    prompt[NODE_QWEN_PROMPT]["inputs"]["custom_prompt"] = cleaned_prompt

    # Seed mapping for workflow_api_flux_dev_1.19
    prompt[NODE_SD_SAMPLER]["inputs"]["seed"] = random.randint(0, 10**12)
    prompt[NODE_FLUX_RANDOM_NOISE]["inputs"]["noise_seed"] = random.randint(0, 10**12)
    prompt[NODE_BODY_SAMPLER_1]["inputs"]["seed"] = random.randint(0, 10**12)
    prompt[NODE_BODY_SAMPLER_2]["inputs"]["seed"] = random.randint(0, 10**12)

    # Parameter mapping for workflow_api_flux_dev_1.19
    prompt[NODE_SD_LORA]["inputs"]["strength_model"] = float(details)
    prompt[NODE_SD_SAMPLER]["inputs"]["denoise"] = float(general_denoise)
    prompt[NODE_FLUX_SCHEDULER]["inputs"]["denoise"] = float(additional_detail_pass)
    prompt[NODE_FLUX_BLEND]["inputs"]["blend_factor"] = float(sharpen)
    prompt[NODE_BODY_SAMPLER_1]["inputs"]["denoise"] = float(body_enhancement_denoise)
    prompt[NODE_BODY_SAMPLER_2]["inputs"]["denoise"] = float(face_enhancement_denoise)

    _apply_branch_routing(
        prompt,
        general_enhance=general_enhance,
        advance_details=advance_details,
        body_enhance=body_enhance,
    )


async def enhance_image(
    image_editor_value: Any,
    general_enhance: bool,
    advance_details: bool,
    additional_detail_pass: float,
    sharpen: float,
    body_enhance: bool,
    body_enhancement_denoise: float,
    face_enhancement_denoise: float,
    details: float,
    general_denoise: float,
    custom_prompt: str,
    workflow_debug: bool,
    workflow: str,
    request: gr.Request,
):
    logger.info("Workflow %s called", workflow)

    user_email = getattr(request, "username", None)
    if not user_email:
        yield (
            gr.update(),
            _render_general_notice_panel(
                "Authentication Required",
                "Please sign in with your BrickVisual account.",
                accent="#f87171",
            ),
            None,
        )
        return

    identity = auth_service.get_identity(user_email)
    is_admin_user = str(getattr(identity, "role", "") or "").strip().lower() == "admin"
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    source_page = "/tab/general-enhancement-v04"

    try:
        background_np, mask_np, has_drawn_mask = _extract_editor_background_and_mask(image_editor_value)
    except Exception as err:
        yield (
            gr.update(),
            _render_general_notice_panel("Input Error", str(err), accent="#f87171"),
            None,
        )
        return

    image_height, image_width = background_np.shape[:2]
    progress_tracker = _init_progress_tracker(
        image_width=image_width,
        image_height=image_height,
        general_enhance=general_enhance,
        advance_details=advance_details,
        body_enhance=body_enhance,
    )

    feature_flags = {
        "general_enhance": bool(general_enhance),
        "advance_details": bool(advance_details),
        "body_enhance": bool(body_enhance),
    }
    settings_snapshot = {
        "details": float(details),
        "general_denoise": float(general_denoise),
        "additional_detail_pass": float(additional_detail_pass),
        "sharpen": float(sharpen),
        "body_enhancement_denoise": float(body_enhancement_denoise),
        "face_enhancement_denoise": float(face_enhancement_denoise),
        "custom_prompt": str(custom_prompt or ""),
    }
    task_id = str(uuid.uuid4())
    workflow_name = str(workflow or WORKFLOW_NAME)
    tracker = TaskTracker(
        store=None,
        task_id=task_id,
        user_email=identity.email,
        user_prefix=identity.username_prefix,
        user_display_name=identity.display_name,
        user_role=identity.role,
        avatar_filename=identity.avatar_filename,
        workflow=WorkflowContext(
            key=workflow_name,
            name=workflow_name,
            version=WORKFLOW_VERSION,
            category=WORKFLOW_CATEGORY,
            workflow_type=WORKFLOW_TYPE,
        ),
        source_page=source_page,
        browser_user_agent=user_agent,
        session_id=session_id,
        environment_name=APP_ENVIRONMENT,
        feature_flags=feature_flags,
        settings=settings_snapshot,
        input_meta={
            "width": int(image_width),
            "height": int(image_height),
            "resolution": f"{int(image_width)}x{int(image_height)}",
            "format": str(background_np.dtype),
        },
        request_summary={
            "has_drawn_mask": bool(has_drawn_mask),
            "general_enhance": bool(general_enhance),
            "advance_details": bool(advance_details),
            "body_enhance": bool(body_enhance),
        },
        prompt_type="general_enhancement",
        created_by=identity.email,
    )

    try:
        image_b64 = save_input_image_as_base64(background_np)
        mask_b64 = save_input_image_as_base64(mask_np)
    except Exception as err:
        tracker.fail(
            failure_reason="input_encode_error",
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield (
            gr.update(),
            _render_general_notice_panel(
                "Encoding Error",
                f"Failed to encode image/mask: {err}",
                accent="#f87171",
            ),
            None,
        )
        return

    try:
        prompt_path = _resolve_general_workflow_path()
        with open(prompt_path, "r", encoding="utf-8") as fh:
            prompt: dict[str, Any] = json.load(fh)
    except UnicodeDecodeError:
        with open(prompt_path, "r", encoding="cp1252") as fh:
            prompt = json.load(fh)
    except Exception as err:
        tracker.fail(
            failure_reason="workflow_load_error",
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield (
            gr.update(),
            _render_general_notice_panel(
                "Workflow Error",
                f"Prompt load failed: {err}",
                accent="#f87171",
            ),
            None,
        )
        return

    try:
        _apply_general_workflow_updates(
            prompt,
            image_b64=image_b64,
            mask_b64=mask_b64,
            has_drawn_mask=has_drawn_mask,
            general_enhance=general_enhance,
            advance_details=advance_details,
            additional_detail_pass=float(additional_detail_pass),
            sharpen=float(sharpen),
            body_enhance=body_enhance,
            body_enhancement_denoise=float(body_enhancement_denoise),
            face_enhancement_denoise=float(face_enhancement_denoise),
            details=float(details),
            general_denoise=float(general_denoise),
            custom_prompt=str(custom_prompt or ""),
        )
    except KeyError as err:
        tracker.fail(
            failure_reason="workflow_key_missing",
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield (
            gr.update(),
            _render_general_notice_panel(
                "Workflow Error",
                f"Workflow key missing: {err}",
                accent="#f87171",
            ),
            None,
        )
        return
    except Exception as err:
        tracker.fail(
            failure_reason="workflow_update_error",
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield (
            gr.update(),
            _render_general_notice_panel(
                "Workflow Error",
                f"Workflow update failed: {err}",
                accent="#f87171",
            ),
            None,
        )
        return

    final_json = prepare_json(prompt, images=[])
    workflow_debug_path: Path | None = None

    should_save_debug_json = bool(SAVE_DEBUG_PROMPT_JSON or (workflow_debug and is_admin_user))
    if should_save_debug_json:
        try:
            workflow_debug_path = _save_workflow_debug_json(
                final_json,
                workflow_name=workflow_name,
                task_id=task_id,
            )
            logger.info("Saved ComfyUI workflow JSON: %s", workflow_debug_path)
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)

    api = RunpodAPI(environment="General_Enhancement")

    try:
        run_resp = await api.run(final_json)
        job_id = run_resp["id"]
    except Exception as err:
        tracker.fail(
            failure_reason="submission_error",
            error_message=str(err),
            failure_stage="created",
            progress_percent=0,
            worker_id=None,
            metadata={"step": "run_submission"},
        )
        yield (
            gr.update(),
            _render_general_notice_panel(
                "RunPod Error",
                f"Job submission failed: {err}",
                accent="#f87171",
            ),
            None,
        )
        return

    tracker.attach_request(
        request_id=job_id,
        task_url=f"{api.base_url}/status/{job_id}",
        retry_count=0,
    )

    if workflow_debug_path is not None:
        progress_tracker["current_status"] = (
            f"Job submitted. Debug JSON saved: {workflow_debug_path}"
        )
    else:
        progress_tracker["current_status"] = "Job submitted. Waiting for worker updates..."
    yield (
        gr.update(),
        _render_general_progress_panel(progress_tracker, overall_percent=0),
        job_id,
    )

    last_overall_percent = 0
    completion_hint_seen_at: int | None = None
    consecutive_status_errors = 0
    stream_seen_signatures: set[str] = set()
    stream_seen_order: list[str] = []
    stream_task: asyncio.Task[dict[str, Any]] | None = None

    def _cancel_stream_task() -> None:
        nonlocal stream_task
        if stream_task is not None and not stream_task.done():
            stream_task.cancel()

    for poll_idx in range(MAX_STATUS_POLLS):
        stream_progress_entries: list[tuple[int | float | None, str, list[str]]] = []
        stream_state: str | None = None

        if RUNPOD_STREAM_ENABLED:
            if stream_task is not None and stream_task.done():
                try:
                    stream_response = stream_task.result()
                    stream_progress_entries, stream_state = _extract_stream_progress_signals(
                        stream_response,
                        seen_signatures=stream_seen_signatures,
                        seen_order=stream_seen_order,
                    )
                except Exception as err:
                    logger.debug("Stream poll failed: %s", err)
                finally:
                    stream_task = None

            if stream_task is None:
                stream_task = asyncio.create_task(api.stream(job_id))

        try:
            status = await api.status(job_id)
        except Exception as err:
            consecutive_status_errors += 1
            if consecutive_status_errors > MAX_CONSECUTIVE_STATUS_ERRORS:
                tracker.fail(
                    failure_reason="status_poll_error",
                    error_message=str(err),
                    failure_stage="status_poll",
                    progress_percent=last_overall_percent,
                    worker_id=None,
                    metadata={
                        "consecutive_errors": consecutive_status_errors,
                        "poll_idx": poll_idx,
                    },
                )
                yield (
                    gr.update(),
                    _render_general_notice_panel(
                        "RunPod Error",
                        f"Failed to check job status: {err}",
                        percent=last_overall_percent,
                        accent="#f87171",
                    ),
                    None,
                )
                _cancel_stream_task()
                return

            yield (
                gr.update(),
                _render_general_notice_panel(
                    "Temporary Connection Issue",
                    (
                        "Retrying automatically while checking RunPod status.\n\n"
                        f"{err}"
                    ),
                    percent=last_overall_percent,
                    accent="#f59e0b",
                ),
                job_id,
            )
            await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
            continue

        consecutive_status_errors = 0

        state = (status.get("status") or stream_state or "").upper()
        has_final_output = _has_final_output_payload(status)

        if state in ACTIVE_STATES and tracker.started_dt is None:
            tracker.mark_started(message="Execution started. Waiting for ComfyUI updates...")

        if state == "CANCELLED":
            tracker.fail(
                failure_reason="cancelled",
                error_message="Job cancelled by user or worker.",
                failure_stage=str(progress_tracker.get("phase") or "processing"),
                progress_percent=last_overall_percent,
                worker_id=status.get("workerId"),
                status="cancelled",
            )
            yield (
                gr.update(),
                _render_general_notice_panel(
                    "Cancelled",
                    "Job cancelled.",
                    percent=last_overall_percent,
                    accent="#f59e0b",
                ),
                None,
            )
            _cancel_stream_task()
            return

        if state in TERMINAL_FAILURES:
            error_message = _extract_error_message(status)
            tracker.fail(
                failure_reason=f"runpod_{state.lower()}",
                error_message=error_message,
                failure_stage=str(progress_tracker.get("phase") or "processing"),
                progress_percent=last_overall_percent,
                worker_id=status.get("workerId"),
                status="failed",
                metadata={"runpod_state": state},
            )
            yield (
                gr.update(),
                _render_general_notice_panel(
                    "RunPod Error",
                    error_message,
                    percent=last_overall_percent,
                    accent="#f87171",
                ),
                None,
            )
            _cancel_stream_task()
            return

        if state == "COMPLETED" or has_final_output:
            tracker.mark_stage(
                status="output_collecting",
                stage="output_collecting",
                message="Collecting output images from ComfyUI history...",
                progress_percent=max(last_overall_percent, 92),
            )
            try:
                result_image = await _decode_output_image(status)
                if result_image.mode not in ("RGB", "RGBA"):
                    result_image = result_image.convert("RGBA")

                left_image = Image.fromarray(background_np)
                if left_image.mode not in ("RGB", "RGBA"):
                    left_image = left_image.convert("RGB")

                tmp_dir = Path(tempfile.gettempdir())
                left_path = tmp_dir / f"{job_id}_left.png"
                right_path = tmp_dir / f"{job_id}_right.png"

                left_image.save(left_path, "PNG")
                result_image.save(right_path, "PNG")
                tracker.mark_stage(
                    status="uploading",
                    stage="uploading",
                    message="Saving result and thumbnail artifacts...",
                    progress_percent=97,
                )

                artifacts = extract_artifacts_from_status(status)
                thumbnail_path = tracker.add_thumbnail(image=result_image, output_index=0)
                preview_path = tracker.add_preview(image=result_image, output_index=0)
                tracker.add_output_record(
                    output_index=0,
                    result_url=artifacts.get("result_url"),
                    thumbnail_url=thumbnail_path,
                    preview_url=preview_path,
                    file_name=artifacts.get("output_filename") or right_path.name,
                    width=result_image.width,
                    height=result_image.height,
                )

                _transition_to_stage(progress_tracker, None)
                progress_tracker["phase"] = PHASE_COMPLETED
                progress_tracker["current_status"] = "Completed."
                tracker.complete(
                    result_url=artifacts.get("result_url"),
                    thumbnail_url=thumbnail_path,
                    preview_url=preview_path,
                    output_filename=artifacts.get("output_filename") or right_path.name,
                    output_count=max(int(artifacts.get("output_count") or 0), 1),
                    output_width=result_image.width,
                    output_height=result_image.height,
                    worker_id=artifacts.get("worker_id"),
                    result_summary={
                        "left_path": str(left_path),
                        "right_path": str(right_path),
                        "runpod_state": state,
                    },
                )
                yield (
                    (str(left_path), str(right_path)),
                    _render_general_progress_panel(
                        progress_tracker,
                        overall_percent=100,
                    ),
                    None,
                )
                _cancel_stream_task()
                return
            except Exception as err:
                if has_final_output and state != "COMPLETED":
                    _set_wrap_status(
                        progress_tracker,
                        "Finalizing output...",
                        min_wrap_ratio=0.95,
                    )
                    tracker.emit_processing(
                        stage="output_collecting",
                        message="Finalizing output payload...",
                        progress_percent=max(last_overall_percent, 92),
                    )
                    overall_percent = max(
                        last_overall_percent,
                        _compute_general_overall_percent(progress_tracker),
                    )
                    last_overall_percent = overall_percent
                    yield (
                        gr.update(),
                        _render_general_progress_panel(
                            progress_tracker,
                            overall_percent=overall_percent,
                        ),
                        job_id,
                    )
                    await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
                    continue

                yield (
                    gr.update(),
                    _render_general_notice_panel(
                        "Decode Error",
                        f"Failed to decode image: {err}",
                        percent=last_overall_percent,
                        accent="#f87171",
                    ),
                    None,
                )
                tracker.fail(
                    failure_reason="decode_error",
                    error_message=str(err),
                    failure_stage="output_collecting",
                    progress_percent=last_overall_percent,
                    worker_id=status.get("workerId"),
                )
                _cancel_stream_task()
                return

        (
            _status_runpod_progress,
            status_progress_text,
            status_hint_texts,
        ) = _extract_progress_signal(status)

        progress_events: list[str] = []
        seen_progress_texts: set[str] = set()
        for _stream_progress, stream_text, _stream_hints in stream_progress_entries:
            if stream_text not in seen_progress_texts:
                seen_progress_texts.add(stream_text)
                progress_events.append(stream_text)
        if status_progress_text and status_progress_text not in seen_progress_texts:
            seen_progress_texts.add(status_progress_text)
            progress_events.append(status_progress_text)

        combined_hint_texts: list[str] = list(status_hint_texts)
        for _, _, hint_texts in stream_progress_entries:
            combined_hint_texts.extend(hint_texts)

        if any("Job completed. Returning" in text for text in combined_hint_texts):
            if completion_hint_seen_at is None:
                completion_hint_seen_at = poll_idx

        if progress_events:
            for progress_text in progress_events:
                _update_progress_tracker_from_text(progress_text, progress_tracker)
        else:
            if completion_hint_seen_at is not None:
                _set_wrap_status(
                    progress_tracker,
                    "Finalizing output...",
                    min_wrap_ratio=0.92,
                )
            elif state in ACTIVE_STATES and progress_tracker["phase"] == PHASE_PREPARATION:
                progress_tracker["current_status"] = "Waiting for next ComfyUI update..."

        overall_percent = max(
            last_overall_percent,
            _compute_general_overall_percent(progress_tracker),
        )
        last_overall_percent = overall_percent
        tracker.emit_processing(
            stage=str(
                progress_tracker.get("current_stage")
                or progress_tracker.get("phase")
                or "processing"
            )
            .lower()
            .replace(" ", "_"),
            message=str(progress_tracker.get("current_status") or "Processing..."),
            progress_percent=overall_percent,
            node_id=_extract_node_id(str(progress_tracker.get("current_status") or "")),
            metadata={
                "runpod_state": state,
                "phase": progress_tracker.get("phase"),
                "current_stage": progress_tracker.get("current_stage"),
            },
        )
        yield (
            gr.update(),
            _render_general_progress_panel(
                progress_tracker,
                overall_percent=overall_percent,
            ),
            job_id,
        )

        if (
            completion_hint_seen_at is not None
            and poll_idx - completion_hint_seen_at >= FINALIZATION_HINT_GRACE_POLLS
        ):
            tracker.fail(
                failure_reason="status_lag_timeout",
                error_message="RunPod stayed IN_PROGRESS after completion hint.",
                failure_stage="wrap_up",
                progress_percent=last_overall_percent,
                worker_id=status.get("workerId"),
            )
            yield (
                gr.update(),
                _render_general_notice_panel(
                    "RunPod Status Lag",
                    "RunPod stayed IN_PROGRESS after a completion hint. Please retry or check endpoint status lag.",
                    percent=last_overall_percent,
                    accent="#f87171",
                ),
                None,
            )
            _cancel_stream_task()
            return

        await asyncio.sleep(RUNPOD_STATUS_POLL_INTERVAL_S)

    yield (
        gr.update(),
        _render_general_notice_panel(
            "Timed Out",
            "Timed out waiting for RunPod completion status.",
            percent=last_overall_percent,
            accent="#f87171",
        ),
        None,
    )
    tracker.fail(
        failure_reason="polling_timeout",
        error_message="Timed out waiting for RunPod completion status.",
        failure_stage=str(progress_tracker.get("phase") or "processing"),
        progress_percent=last_overall_percent,
        worker_id=None,
    )
    _cancel_stream_task()


async def cancel_job(job_id: str | None) -> str:
    if not job_id:
        return _render_general_notice_panel(
            "Nothing To Cancel",
            "No active job to cancel.",
            accent="#f59e0b",
        )

    api = RunpodAPI(environment="General_Enhancement")
    try:
        await api.cancel(job_id)
        return _render_general_notice_panel(
            "Cancellation Requested",
            "Cancellation requested.",
            accent="#f59e0b",
        )
    except Exception as err:
        logger.error("Cancel failed: %s", err)
        return _render_general_notice_panel(
            "Cancel Failed",
            f"Cancel failed: {err}",
            accent="#f87171",
        )


def _disable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=False)


def _enable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=True)


def update_general_enhance_controls(general_enhance: bool):
    return [
        gr.update(visible=general_enhance),
        gr.update(visible=general_enhance),
    ]


def update_advance_detail_controls(advance_details: bool):
    return [
        gr.update(visible=advance_details),
        gr.update(visible=advance_details),
    ]


def update_body_enhance_controls(body_enhance: bool):
    return [
        gr.update(visible=body_enhance),
        gr.update(visible=body_enhance),
    ]


script_name = os.path.splitext(os.path.basename(__file__))[0]
my_theme = gr.Theme.from_hub("snehilsanyal/scikit-learn")
BOTTOM_PROGRESS_LAYOUT_CSS = """
.bottom-progress-row {
    margin-top: 12px;
    margin-bottom: 12px;
}

.bottom-progress-row > div {
    width: 100%;
}
"""

with gr.Blocks(theme=my_theme, title=APP_TITLE, css=BOTTOM_PROGRESS_LAYOUT_CSS) as General_Enhancement_interface:
    workflow = gr.State(value=script_name)

    with gr.Row():
        with gr.Column():
            custom_prompt = gr.Textbox(
                label="Custom Prompt",
                placeholder="Enter prompt text here",
                lines=7,
            )

        with gr.Column():
            general_enhance = gr.Checkbox(label="Enable general enhancement", value=True)
            workflow_debug_checkbox = gr.Checkbox(
                label="Workflow Debug (Admin only)",
                value=False,
                visible=False,
                info="Save the final manipulated workflow JSON sent to RunPod.",
            )
            details = gr.Slider(label="Details", minimum=0.0, maximum=2.0, value=1.0, step=0.05)
            general_denoise = gr.Slider(
                label="General enhance",
                minimum=0.0,
                maximum=0.45,
                value=0.1,
                step=0.01,
            )

            advance_details = gr.Checkbox(label="Advance Details", value=False)
            additional_detail_pass = gr.Slider(
                label="Additional detail pass",
                minimum=0.0,
                maximum=0.7,
                value=0.35,
                step=0.01,
                visible=False,
            )
            sharpen = gr.Slider(
                label="Sharpen",
                minimum=0.0,
                maximum=1.0,
                value=0.4,
                step=0.01,
                visible=False,
            )

            body_enhance = gr.Checkbox(label="Enable Body Enhancement", value=False)
            body_enhancement_denoise = gr.Slider(
                label="Body Enhancement",
                minimum=0.0,
                maximum=0.3,
                value=0.2,
                step=0.01,
                visible=False,
            )
            face_enhancement_denoise = gr.Slider(
                label="Face Enhancement",
                minimum=0.0,
                maximum=0.3,
                value=0.2,
                step=0.01,
                visible=False,
            )

            general_enhance.change(
                fn=update_general_enhance_controls,
                inputs=[general_enhance],
                outputs=[details, general_denoise],
            )
            advance_details.change(
                fn=update_advance_detail_controls,
                inputs=[advance_details],
                outputs=[additional_detail_pass, sharpen],
            )
            body_enhance.change(
                fn=update_body_enhance_controls,
                inputs=[body_enhance],
                outputs=[body_enhancement_denoise, face_enhancement_denoise],
            )

    with gr.Row():
        image_editor = gr.ImageEditor(
            label="Load Image",
            layers=False,
            sources=["upload", "clipboard"],
            show_download_button=False,
            interactive=True,
            brush=gr.Brush(default_size=75, colors=["#cc3232"], color_mode="fixed"),
            type="pil",
        )
        result_image = ImageSlider(label="Result", type="filepath")

    job_state = gr.State(None)

    with gr.Row(elem_classes=["bottom-progress-row"]):
        status = gr.HTML(_render_idle_status())

    with gr.Row(elem_classes=["bottom-action-row"]):
        generate_button = gr.Button("Generate", scale=3, variant="primary")
        cancel_btn = gr.Button("Cancel", variant="stop", scale=1)

    generate_event = generate_button.click(
        fn=_disable_generate_button,
        inputs=None,
        outputs=[generate_button],
        queue=False,
    )

    generate_event = generate_event.then(
        fn=enhance_image,
        inputs=[
            image_editor,
            general_enhance,
            advance_details,
            additional_detail_pass,
            sharpen,
            body_enhance,
            body_enhancement_denoise,
            face_enhancement_denoise,
            details,
            general_denoise,
            custom_prompt,
            workflow_debug_checkbox,
            workflow,
        ],
        outputs=[result_image, status, job_state],
        concurrency_limit=10,
        trigger_mode="once",
    )

    generate_event.then(
        fn=_enable_generate_button,
        inputs=None,
        outputs=[generate_button],
        queue=False,
    )

    cancel_btn.click(fn=cancel_job, inputs=[job_state], outputs=[status]).then(
        fn=_enable_generate_button,
        inputs=None,
        outputs=[generate_button],
        queue=False,
    )
    General_Enhancement_interface.load(
        fn=_debug_checkbox_visibility_update,
        inputs=None,
        outputs=[workflow_debug_checkbox],
    )


if __name__ == "__main__":
    General_Enhancement_interface.launch(
        server_name=APP_SERVER_NAME,
        server_port=APP_SERVER_PORT,
        debug=APP_DEBUG,
        quiet=APP_QUIET,
        auth=auth_service.authenticate,
        auth_message="BrickVisual internal access only.",
    )
