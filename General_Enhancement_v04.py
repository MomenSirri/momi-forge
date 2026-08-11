from __future__ import annotations

import asyncio
import base64
import binascii
import html
import io
import json
import logging
import os
import random
import tempfile
import uuid
from dataclasses import dataclass, field
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
from runpod_api_class import (
    RunpodAPI,
    RunpodSubmissionError,
    RunpodSubmissionUncertainError,
)
from task_tracking import TaskTracker, WorkflowContext, extract_artifacts_from_status
from workflow_ui import (
    debug_checkbox_visibility_update as _debug_checkbox_visibility_update,
    request_header as _request_header,
    save_workflow_debug_json,
)
from workflow_progress import (
    COUNT_MODE_CYCLE,
    COUNT_MODE_ITEM_COUNTER,
    PHASE_COMPLETED,
    PHASE_PREPARATION,
    ProgressTracker,
    StageSpec,
    extract_node_id,
)
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

STAGE_GENERAL = "general"
STAGE_ADVANCE = "advance"
STAGE_BODY = "body"
STAGE_FACE = "face"

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


def _save_workflow_debug_json(
    payload: dict[str, Any],
    *,
    workflow_name: str,
    task_id: str,
) -> Path:
    return save_workflow_debug_json(
        payload,
        workflow_name=workflow_name or WORKFLOW_NAME,
        task_id=task_id,
        prefix="general",
    )


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


def _estimate_tile_count(width: int, height: int) -> tuple[int, int, int]:
    safe_width = max(int(width or 0), 1)
    safe_height = max(int(height or 0), 1)
    # Keep this aligned with workflow node `SimpleMath+` (a/b), which
    # returns `INT` via Python's round(result), not ceil().
    columns = max(1, int(round(safe_width / TILE_DIVISOR_PX)))
    rows = max(1, int(round(safe_height / TILE_DIVISOR_PX)))
    return columns, rows, columns * rows


def _init_progress_tracker(
    *,
    image_width: int,
    image_height: int,
    general_enhance: bool,
    advance_details: bool,
    body_enhance: bool,
) -> ProgressTracker:
    columns, rows, tile_count = _estimate_tile_count(image_width, image_height)
    enabled_by_stage = {
        STAGE_GENERAL: bool(general_enhance),
        STAGE_ADVANCE: bool(advance_details),
        STAGE_BODY: bool(body_enhance),
        STAGE_FACE: bool(body_enhance),
    }
    node_by_stage = {
        STAGE_GENERAL: NODE_SD_SAMPLER,
        STAGE_ADVANCE: NODE_FLUX_SAMPLER,
        STAGE_BODY: NODE_BODY_SAMPLER_1,
        STAGE_FACE: NODE_BODY_SAMPLER_2,
    }
    specs = [
        StageSpec(
            stage_key,
            STAGE_LABELS[stage_key],
            total=(
                tile_count
                if stage_key in {STAGE_GENERAL, STAGE_ADVANCE}
                else None
            ),
            enabled=enabled_by_stage[stage_key],
            unit_label=STAGE_UNIT_LABELS[stage_key],
            node_id=node_by_stage[stage_key],
            dynamic_total=stage_key in {STAGE_BODY, STAGE_FACE},
            count_mode=(
                COUNT_MODE_ITEM_COUNTER
                if stage_key in {STAGE_GENERAL, STAGE_ADVANCE}
                else COUNT_MODE_CYCLE
            ),
        )
        for stage_key in STAGE_ORDER
    ]
    return ProgressTracker.for_general(
        specs=specs,
        tile_columns=columns,
        tile_rows=rows,
        node_stage_hints=NODE_STAGE_HINTS,
        node_status_hints=NODE_STATUS_HINTS,
        sampler_node_to_stage=SAMPLER_NODE_TO_STAGE,
        wrap_milestones=STAGE_WRAP_MILESTONES,
        save_node_id=NODE_SAVE_IMAGE,
        sampling_ceiling=SAMPLING_PROGRESS_CEILING,
        sync_stage_keys=(STAGE_GENERAL, STAGE_ADVANCE),
        advance_stage_key=STAGE_ADVANCE,
    )


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


class GeneralPreparationError(RuntimeError):
    def __init__(
        self,
        title: str,
        message: str,
        *,
        failure_reason: str,
    ) -> None:
        super().__init__(message)
        self.title = title
        self.failure_reason = failure_reason


class GeneralRequestError(RuntimeError):
    def __init__(self, title: str, message: str) -> None:
        super().__init__(message)
        self.title = title


@dataclass
class GeneralInputSource:
    background_np: np.ndarray
    mask_np: np.ndarray
    has_drawn_mask: bool
    task_id: str
    workflow_key: str
    feature_flags: dict[str, Any]
    settings_snapshot: dict[str, Any]
    progress_tracker: ProgressTracker


@dataclass
class GeneralPreparedInputs:
    source: GeneralInputSource
    prompt: dict[str, Any]
    image_b64: str
    mask_b64: str


@dataclass
class GeneralPreparedJob:
    inputs: GeneralPreparedInputs
    payload: dict[str, Any]
    workflow_debug_path: Path | None


@dataclass
class GeneralRequestContext:
    source: GeneralInputSource
    job: GeneralPreparedJob
    tracker: TaskTracker


@dataclass
class GeneralSubmissionResult:
    job_id: str | None
    error_message: str | None = None
    uncertain: bool = False


@dataclass
class GeneralFinalizedOutput:
    result_image: Image.Image | None = None
    left_path: Path | None = None
    right_path: Path | None = None
    artifacts: dict[str, Any] | None = None
    error_message: str | None = None


@dataclass
class GeneralPollEvent:
    kind: str
    status: dict[str, Any]
    title: str
    message: str
    progress_percent: int
    stage: str
    poll_idx: int
    finalized: GeneralFinalizedOutput | None = None
    tracker_error_message: str | None = None


@dataclass
class GeneralPollState:
    progress_tracker: ProgressTracker
    last_overall_percent: int = 0
    completion_hint_seen_at: int | None = None
    consecutive_status_errors: int = 0
    stream_seen_signatures: set[str] = field(default_factory=set)
    stream_seen_order: list[str] = field(default_factory=list)
    stream_task: asyncio.Task[dict[str, Any]] | None = None

    def cancel_stream(self) -> None:
        if self.stream_task is not None and not self.stream_task.done():
            self.stream_task.cancel()


def _prepare_general_source(
    *,
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
    workflow: str,
) -> GeneralInputSource:
    try:
        background_np, mask_np, has_drawn_mask = (
            _extract_editor_background_and_mask(image_editor_value)
        )
    except Exception as err:
        raise GeneralPreparationError(
            "Input Error",
            str(err),
            failure_reason="input_error",
        ) from err

    image_height, image_width = background_np.shape[:2]
    return GeneralInputSource(
        background_np=background_np,
        mask_np=mask_np,
        has_drawn_mask=has_drawn_mask,
        task_id=str(uuid.uuid4()),
        workflow_key=str(workflow or WORKFLOW_NAME),
        feature_flags={
            "general_enhance": bool(general_enhance),
            "advance_details": bool(advance_details),
            "body_enhance": bool(body_enhance),
        },
        settings_snapshot={
            "details": float(details),
            "general_denoise": float(general_denoise),
            "additional_detail_pass": float(additional_detail_pass),
            "sharpen": float(sharpen),
            "body_enhancement_denoise": float(body_enhancement_denoise),
            "face_enhancement_denoise": float(face_enhancement_denoise),
            "custom_prompt": str(custom_prompt or ""),
        },
        progress_tracker=_init_progress_tracker(
            image_width=image_width,
            image_height=image_height,
            general_enhance=general_enhance,
            advance_details=advance_details,
            body_enhance=body_enhance,
        ),
    )


def _prepare_general_inputs(
    source: GeneralInputSource,
) -> GeneralPreparedInputs:
    try:
        image_b64 = save_input_image_as_base64(source.background_np)
        mask_b64 = save_input_image_as_base64(source.mask_np)
    except Exception as err:
        raise GeneralPreparationError(
            "Encoding Error",
            f"Failed to encode image/mask: {err}",
            failure_reason="input_encode_error",
        ) from err

    try:
        prompt_path = _resolve_general_workflow_path()
        try:
            with open(prompt_path, "r", encoding="utf-8") as file:
                prompt: dict[str, Any] = json.load(file)
        except UnicodeDecodeError:
            with open(prompt_path, "r", encoding="cp1252") as file:
                prompt = json.load(file)
    except Exception as err:
        raise GeneralPreparationError(
            "Workflow Error",
            f"Prompt load failed: {err}",
            failure_reason="workflow_load_error",
        ) from err
    return GeneralPreparedInputs(
        source=source,
        prompt=prompt,
        image_b64=image_b64,
        mask_b64=mask_b64,
    )


def _build_general_payload(
    prepared: GeneralPreparedInputs,
    *,
    workflow_debug: bool,
    is_admin_user: bool,
) -> GeneralPreparedJob:
    source = prepared.source
    flags = source.feature_flags
    settings = source.settings_snapshot
    _apply_general_workflow_updates(
        prepared.prompt,
        image_b64=prepared.image_b64,
        mask_b64=prepared.mask_b64,
        has_drawn_mask=source.has_drawn_mask,
        general_enhance=bool(flags["general_enhance"]),
        advance_details=bool(flags["advance_details"]),
        additional_detail_pass=float(settings["additional_detail_pass"]),
        sharpen=float(settings["sharpen"]),
        body_enhance=bool(flags["body_enhance"]),
        body_enhancement_denoise=float(
            settings["body_enhancement_denoise"]
        ),
        face_enhancement_denoise=float(
            settings["face_enhancement_denoise"]
        ),
        details=float(settings["details"]),
        general_denoise=float(settings["general_denoise"]),
        custom_prompt=str(settings["custom_prompt"]),
    )
    payload = prepare_json(prepared.prompt, images=[])
    workflow_debug_path: Path | None = None
    if SAVE_DEBUG_PROMPT_JSON or (workflow_debug and is_admin_user):
        try:
            workflow_debug_path = _save_workflow_debug_json(
                payload,
                workflow_name=source.workflow_key,
                task_id=source.task_id,
            )
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)
    return GeneralPreparedJob(
        inputs=prepared,
        payload=payload,
        workflow_debug_path=workflow_debug_path,
    )


def _create_general_task_tracker(
    source: GeneralInputSource,
    *,
    identity: Any,
    user_agent: str | None,
    session_id: str,
) -> TaskTracker:
    height, width = source.background_np.shape[:2]
    return TaskTracker(
        store=None,
        task_id=source.task_id,
        user_email=identity.email,
        user_prefix=identity.username_prefix,
        user_display_name=identity.display_name,
        user_role=identity.role,
        avatar_filename=identity.avatar_filename,
        workflow=WorkflowContext(
            key=source.workflow_key,
            name=source.workflow_key,
            version=WORKFLOW_VERSION,
            category=WORKFLOW_CATEGORY,
            workflow_type=WORKFLOW_TYPE,
        ),
        source_page="/tab/general-enhancement-v04",
        browser_user_agent=user_agent,
        session_id=session_id,
        environment_name=APP_ENVIRONMENT,
        feature_flags=source.feature_flags,
        settings=source.settings_snapshot,
        input_meta={
            "width": int(width),
            "height": int(height),
            "resolution": f"{int(width)}x{int(height)}",
            "format": str(source.background_np.dtype),
        },
        request_summary={
            "has_drawn_mask": bool(source.has_drawn_mask),
            **source.feature_flags,
        },
        prompt_type="general_enhancement",
        created_by=identity.email,
    )


def _prepare_general_request(
    *,
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
    identity: Any,
    user_agent: str | None,
    session_id: str,
) -> GeneralRequestContext:
    source = _prepare_general_source(
        image_editor_value=image_editor_value,
        general_enhance=general_enhance,
        advance_details=advance_details,
        additional_detail_pass=additional_detail_pass,
        sharpen=sharpen,
        body_enhance=body_enhance,
        body_enhancement_denoise=body_enhancement_denoise,
        face_enhancement_denoise=face_enhancement_denoise,
        details=details,
        general_denoise=general_denoise,
        custom_prompt=custom_prompt,
        workflow=workflow,
    )
    tracker = _create_general_task_tracker(
        source,
        identity=identity,
        user_agent=user_agent,
        session_id=session_id,
    )
    try:
        prepared = _prepare_general_inputs(source)
        job = _build_general_payload(
            prepared,
            workflow_debug=workflow_debug,
            is_admin_user=(
                str(getattr(identity, "role", "") or "")
                .strip()
                .lower()
                == "admin"
            ),
        )
    except GeneralPreparationError as err:
        tracker.fail(
            failure_reason=err.failure_reason,
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        raise
    except Exception as err:
        failure_reason = (
            "workflow_key_missing"
            if isinstance(err, KeyError)
            else "workflow_update_error"
        )
        message = (
            f"Workflow key missing: {err}"
            if isinstance(err, KeyError)
            else f"Workflow update failed: {err}"
        )
        tracker.fail(
            failure_reason=failure_reason,
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        raise GeneralRequestError("Workflow Error", message) from err
    return GeneralRequestContext(source=source, job=job, tracker=tracker)


async def _submit_general_job(
    api: RunpodAPI,
    payload: dict[str, Any],
) -> GeneralSubmissionResult:
    try:
        response = await api.run(payload)
        return GeneralSubmissionResult(job_id=str(response["id"]))
    except RunpodSubmissionUncertainError as err:
        return GeneralSubmissionResult(
            job_id=None,
            error_message=(
                f"{err}\n\nPlease check the Jobs page before trying again; "
                "RunPod may already have accepted this request."
            ),
            uncertain=True,
        )
    except RunpodSubmissionError as err:
        return GeneralSubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )
    except Exception as err:
        return GeneralSubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )


async def _finalize_general_output(
    status: dict[str, Any],
    *,
    background_np: np.ndarray,
    job_id: str,
) -> GeneralFinalizedOutput:
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
        return GeneralFinalizedOutput(
            result_image=result_image,
            left_path=left_path,
            right_path=right_path,
            artifacts=extract_artifacts_from_status(status),
        )
    except Exception as err:
        return GeneralFinalizedOutput(error_message=str(err))


async def _advance_general_stream(
    api: RunpodAPI,
    job_id: str,
    state: GeneralPollState,
    *,
    stream_enabled: bool,
) -> tuple[list[tuple[int | float | None, str, list[str]]], str | None]:
    entries: list[tuple[int | float | None, str, list[str]]] = []
    stream_state: str | None = None
    if not stream_enabled:
        return entries, stream_state
    if state.stream_task is not None and state.stream_task.done():
        try:
            response = state.stream_task.result()
            entries, stream_state = _extract_stream_progress_signals(
                response,
                seen_signatures=state.stream_seen_signatures,
                seen_order=state.stream_seen_order,
            )
        except Exception as err:
            logger.debug("Stream poll failed: %s", err)
        finally:
            state.stream_task = None
    if state.stream_task is None:
        state.stream_task = asyncio.create_task(api.stream(job_id))
    return entries, stream_state


def _update_general_poll_progress(
    status: dict[str, Any],
    stream_entries: list[tuple[int | float | None, str, list[str]]],
    state: GeneralPollState,
    *,
    poll_idx: int,
    runpod_state: str,
) -> GeneralPollEvent:
    _, status_progress_text, status_hint_texts = _extract_progress_signal(status)
    progress_events: list[str] = []
    seen_progress_texts: set[str] = set()
    for _, stream_text, _ in stream_entries:
        if stream_text and stream_text not in seen_progress_texts:
            seen_progress_texts.add(stream_text)
            progress_events.append(stream_text)
    if status_progress_text and status_progress_text not in seen_progress_texts:
        progress_events.append(status_progress_text)

    hint_texts = list(status_hint_texts)
    for _, _, stream_hints in stream_entries:
        hint_texts.extend(stream_hints)
    if any("Job completed. Returning" in text for text in hint_texts):
        if state.completion_hint_seen_at is None:
            state.completion_hint_seen_at = poll_idx

    if progress_events:
        for progress_text in progress_events:
            state.progress_tracker.observe_text(progress_text)
    elif state.completion_hint_seen_at is not None:
        state.progress_tracker.start_wrap(
            "Finalizing output...",
            min_wrap_ratio=0.92,
        )
    elif (
        runpod_state in ACTIVE_STATES
        and state.progress_tracker["phase"] == PHASE_PREPARATION
    ):
        state.progress_tracker["current_status"] = (
            "Waiting for next ComfyUI update..."
        )

    overall_percent = max(
        state.last_overall_percent,
        state.progress_tracker.overall_percent(),
    )
    state.last_overall_percent = overall_percent
    message = str(
        state.progress_tracker.get("current_status") or "Processing..."
    )
    stage = str(
        state.progress_tracker.get("current_stage")
        or state.progress_tracker.get("phase")
        or "processing"
    ).lower().replace(" ", "_")
    return GeneralPollEvent(
        kind="progress",
        status=status,
        title="Processing",
        message=message,
        progress_percent=overall_percent,
        stage=stage,
        poll_idx=poll_idx,
    )


async def _general_terminal_event(
    status: dict[str, Any],
    *,
    runpod_state: str,
    has_final_output: bool,
    background_np: np.ndarray,
    job_id: str,
    poll_idx: int,
    state: GeneralPollState,
) -> GeneralPollEvent | None:
    if runpod_state == "CANCELLED":
        return GeneralPollEvent(
            kind="cancelled",
            status=status,
            title="Cancelled",
            message="Job cancelled.",
            progress_percent=state.last_overall_percent,
            stage=str(state.progress_tracker.get("phase") or "processing"),
            poll_idx=poll_idx,
            tracker_error_message="Job cancelled by user or worker.",
        )
    if runpod_state in TERMINAL_FAILURES:
        message = _extract_error_message(status)
        return GeneralPollEvent(
            kind="terminal_failure",
            status=status,
            title="RunPod Error",
            message=message,
            progress_percent=state.last_overall_percent,
            stage=str(state.progress_tracker.get("phase") or "processing"),
            poll_idx=poll_idx,
            tracker_error_message=message,
        )
    if runpod_state != "COMPLETED" and not has_final_output:
        return None

    finalized = await _finalize_general_output(
        status,
        background_np=background_np,
        job_id=job_id,
    )
    if finalized.error_message and has_final_output and runpod_state != "COMPLETED":
        state.progress_tracker.start_wrap(
            "Finalizing output...",
            min_wrap_ratio=0.95,
        )
        state.last_overall_percent = max(
            state.last_overall_percent,
            state.progress_tracker.overall_percent(),
        )
        return GeneralPollEvent(
            kind="finalizing",
            status=status,
            title="Finalizing output",
            message="Finalizing output...",
            progress_percent=state.last_overall_percent,
            stage="output_collecting",
            poll_idx=poll_idx,
        )
    if finalized.error_message:
        return GeneralPollEvent(
            kind="decode_error",
            status=status,
            title="Decode Error",
            message=f"Failed to decode image: {finalized.error_message}",
            progress_percent=state.last_overall_percent,
            stage="output_collecting",
            poll_idx=poll_idx,
            finalized=finalized,
            tracker_error_message=finalized.error_message,
        )
    return GeneralPollEvent(
        kind="completed",
        status=status,
        title="Completed",
        message="Completed.",
        progress_percent=100,
        stage="completed",
        poll_idx=poll_idx,
        finalized=finalized,
    )


async def _poll_general_job(
    api: RunpodAPI,
    job_id: str,
    *,
    background_np: np.ndarray,
    state: GeneralPollState,
    stream_enabled: bool = RUNPOD_STREAM_ENABLED,
):
    try:
        for poll_idx in range(MAX_STATUS_POLLS):
            stream_entries, stream_state = await _advance_general_stream(
                api,
                job_id,
                state,
                stream_enabled=stream_enabled,
            )
            try:
                status = await api.status(job_id)
            except Exception as err:
                state.consecutive_status_errors += 1
                if state.consecutive_status_errors > MAX_CONSECUTIVE_STATUS_ERRORS:
                    yield GeneralPollEvent(
                        kind="status_error",
                        status={},
                        title="RunPod Error",
                        message=f"Failed to check job status: {err}",
                        progress_percent=state.last_overall_percent,
                        stage="status_poll",
                        poll_idx=poll_idx,
                        tracker_error_message=str(err),
                    )
                    return
                yield GeneralPollEvent(
                    kind="retry",
                    status={},
                    title="Temporary Connection Issue",
                    message=(
                        "Retrying automatically while checking RunPod status."
                        f"\n\n{err}"
                    ),
                    progress_percent=state.last_overall_percent,
                    stage="status_poll",
                    poll_idx=poll_idx,
                )
                await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
                continue

            state.consecutive_status_errors = 0
            runpod_state = (status.get("status") or stream_state or "").upper()
            terminal_event = await _general_terminal_event(
                status,
                runpod_state=runpod_state,
                has_final_output=_has_final_output_payload(status),
                background_np=background_np,
                job_id=job_id,
                poll_idx=poll_idx,
                state=state,
            )
            if terminal_event is not None:
                yield terminal_event
                if terminal_event.kind != "finalizing":
                    return
                await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
                continue

            yield _update_general_poll_progress(
                status,
                stream_entries,
                state,
                poll_idx=poll_idx,
                runpod_state=runpod_state,
            )
            if (
                state.completion_hint_seen_at is not None
                and poll_idx - state.completion_hint_seen_at
                >= FINALIZATION_HINT_GRACE_POLLS
            ):
                yield GeneralPollEvent(
                    kind="status_lag",
                    status=status,
                    title="RunPod Status Lag",
                    message=(
                        "RunPod stayed IN_PROGRESS after a completion hint. "
                        "Please retry or check endpoint status lag."
                    ),
                    progress_percent=state.last_overall_percent,
                    stage="wrap_up",
                    poll_idx=poll_idx,
                    tracker_error_message=(
                        "RunPod stayed IN_PROGRESS after completion hint."
                    ),
                )
                return
            await asyncio.sleep(RUNPOD_STATUS_POLL_INTERVAL_S)

        yield GeneralPollEvent(
            kind="timeout",
            status={},
            title="Timed Out",
            message="Timed out waiting for RunPod completion status.",
            progress_percent=state.last_overall_percent,
            stage=str(state.progress_tracker.get("phase") or "processing"),
            poll_idx=MAX_STATUS_POLLS,
            tracker_error_message=(
                "Timed out waiting for RunPod completion status."
            ),
        )
    finally:
        state.cancel_stream()


def _record_general_completed(
    tracker: TaskTracker,
    event: GeneralPollEvent,
    *,
    progress_tracker: ProgressTracker,
) -> None:
    finalized = event.finalized
    if (
        finalized is None
        or finalized.result_image is None
        or finalized.left_path is None
        or finalized.right_path is None
    ):
        raise ValueError("Completed General event is missing finalized output.")
    artifacts = finalized.artifacts or {}
    tracker.mark_stage(
        status="uploading",
        stage="uploading",
        message="Saving result and thumbnail artifacts...",
        progress_percent=97,
    )
    thumbnail_path = tracker.add_thumbnail(
        image=finalized.result_image,
        output_index=0,
    )
    preview_path = tracker.add_preview(
        image=finalized.result_image,
        output_index=0,
    )
    output_filename = (
        artifacts.get("output_filename") or finalized.right_path.name
    )
    tracker.add_output_record(
        output_index=0,
        result_url=artifacts.get("result_url"),
        thumbnail_url=thumbnail_path,
        preview_url=preview_path,
        file_name=output_filename,
        width=finalized.result_image.width,
        height=finalized.result_image.height,
    )
    progress_tracker.mark_completed()
    progress_tracker["current_status"] = "Completed."
    tracker.complete(
        result_url=artifacts.get("result_url"),
        thumbnail_url=thumbnail_path,
        preview_url=preview_path,
        output_filename=output_filename,
        output_count=max(int(artifacts.get("output_count") or 0), 1),
        output_width=finalized.result_image.width,
        output_height=finalized.result_image.height,
        worker_id=artifacts.get("worker_id"),
        result_summary={
            "left_path": str(finalized.left_path),
            "right_path": str(finalized.right_path),
            "runpod_state": event.status.get("status"),
        },
    )


def _record_general_poll_event(
    tracker: TaskTracker,
    event: GeneralPollEvent,
    *,
    progress_tracker: ProgressTracker,
) -> None:
    runpod_state = str(event.status.get("status") or "").upper()
    if runpod_state in ACTIVE_STATES and tracker.started_dt is None:
        tracker.mark_started(
            message="Execution started. Waiting for ComfyUI updates..."
        )
    if event.kind == "retry":
        return
    if event.kind in {"progress", "finalizing"}:
        if event.kind == "finalizing":
            tracker.mark_stage(
                status="output_collecting",
                stage="output_collecting",
                message="Collecting output images from ComfyUI history...",
                progress_percent=max(event.progress_percent, 92),
            )
        tracker.emit_processing(
            stage=event.stage,
            message=event.message,
            progress_percent=event.progress_percent,
            node_id=extract_node_id(event.message),
            metadata={
                "runpod_state": event.status.get("status"),
                "phase": progress_tracker.get("phase"),
                "current_stage": progress_tracker.get("current_stage"),
            },
        )
        return
    if event.kind == "completed":
        tracker.mark_stage(
            status="output_collecting",
            stage="output_collecting",
            message="Collecting output images from ComfyUI history...",
            progress_percent=max(event.progress_percent, 92),
        )
        _record_general_completed(
            tracker,
            event,
            progress_tracker=progress_tracker,
        )
        return

    failure_reason = {
        "cancelled": "cancelled",
        "status_error": "status_poll_error",
        "terminal_failure": (
            f"runpod_{str(event.status.get('status') or 'unknown').lower()}"
        ),
        "decode_error": "decode_error",
        "status_lag": "status_lag_timeout",
        "timeout": "polling_timeout",
    }.get(event.kind, event.kind)
    tracker.fail(
        failure_reason=failure_reason,
        error_message=event.tracker_error_message or event.message,
        failure_stage=event.stage,
        progress_percent=event.progress_percent,
        worker_id=event.status.get("workerId"),
        status="cancelled" if event.kind == "cancelled" else "failed",
        metadata=(
            {"runpod_state": event.status.get("status")}
            if event.kind == "terminal_failure"
            else None
        ),
    )


def _render_general_poll_event(
    event: GeneralPollEvent,
    state: GeneralPollState,
    *,
    job_id: str,
) -> tuple[Any, str, str | None]:
    if event.kind in {"progress", "finalizing"}:
        return (
            gr.update(),
            _render_general_progress_panel(
                state.progress_tracker,
                overall_percent=event.progress_percent,
            ),
            job_id,
        )
    if event.kind == "retry":
        return (
            gr.update(),
            _render_general_notice_panel(
                event.title,
                event.message,
                percent=event.progress_percent,
                accent="#f59e0b",
            ),
            job_id,
        )
    if event.kind == "completed":
        finalized = event.finalized
        if (
            finalized is None
            or finalized.left_path is None
            or finalized.right_path is None
        ):
            raise ValueError("Completed General event is missing output paths.")
        return (
            (str(finalized.left_path), str(finalized.right_path)),
            _render_general_progress_panel(
                state.progress_tracker,
                overall_percent=100,
            ),
            None,
        )
    return (
        gr.update(),
        _render_general_notice_panel(
            event.title,
            event.message,
            percent=event.progress_percent,
            accent="#f59e0b" if event.kind == "cancelled" else "#f87171",
        ),
        None,
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
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    try:
        context = _prepare_general_request(
            image_editor_value=image_editor_value,
            general_enhance=general_enhance,
            advance_details=advance_details,
            additional_detail_pass=additional_detail_pass,
            sharpen=sharpen,
            body_enhance=body_enhance,
            body_enhancement_denoise=body_enhancement_denoise,
            face_enhancement_denoise=face_enhancement_denoise,
            details=details,
            general_denoise=general_denoise,
            custom_prompt=custom_prompt,
            workflow_debug=workflow_debug,
            workflow=workflow,
            identity=identity,
            user_agent=user_agent,
            session_id=session_id,
        )
    except (GeneralPreparationError, GeneralRequestError) as err:
        yield (
            gr.update(),
            _render_general_notice_panel(
                err.title,
                str(err),
                accent="#f87171",
            ),
            None,
        )
        return

    source = context.source
    tracker = context.tracker
    prepared_job = context.job
    api = RunpodAPI(environment="General_Enhancement")
    submission = await _submit_general_job(api, prepared_job.payload)
    if submission.job_id is None:
        tracker.fail(
            failure_reason=(
                "submission_uncertain"
                if submission.uncertain
                else "submission_error"
            ),
            error_message=str(submission.error_message or "Submission failed."),
            failure_stage="created",
            progress_percent=0,
            worker_id=None,
            metadata={"step": "run_submission"},
        )
        yield (
            gr.update(),
            _render_general_notice_panel(
                "RunPod Submission Uncertain"
                if submission.uncertain
                else "RunPod Error",
                str(submission.error_message or "Job submission failed."),
                accent="#f59e0b" if submission.uncertain else "#f87171",
            ),
            None,
        )
        return

    job_id = submission.job_id
    tracker.attach_request(
        request_id=job_id,
        task_url=f"{api.base_url}/status/{job_id}",
        retry_count=0,
    )
    if prepared_job.workflow_debug_path is not None:
        source.progress_tracker["current_status"] = (
            "Job submitted. Debug JSON saved: "
            f"{prepared_job.workflow_debug_path}"
        )
    else:
        source.progress_tracker["current_status"] = (
            "Job submitted. Waiting for worker updates..."
        )
    yield (
        gr.update(),
        _render_general_progress_panel(
            source.progress_tracker,
            overall_percent=0,
        ),
        job_id,
    )

    poll_state = GeneralPollState(progress_tracker=source.progress_tracker)
    async for event in _poll_general_job(
        api,
        job_id,
        background_np=source.background_np,
        state=poll_state,
    ):
        _record_general_poll_event(
            tracker,
            event,
            progress_tracker=source.progress_tracker,
        )
        yield _render_general_poll_event(
            event,
            poll_state,
            job_id=job_id,
        )
        if event.kind not in {"progress", "retry", "finalizing"}:
            return


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


def _load_theme():
    try:
        return gr.Theme.from_hub("snehilsanyal/scikit-learn")
    except Exception as error:
        logger.warning("Falling back to bundled Gradio theme: %s", error)
        return gr.themes.Soft()


script_name = os.path.splitext(os.path.basename(__file__))[0]
my_theme = _load_theme()
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
