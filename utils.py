from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
import html
import io
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any

import aiohttp
import numpy as np
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def image_to_pil(image: Any) -> Image.Image:
    if image is None:
        raise ValueError("No image provided.")
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, np.ndarray):
        return Image.fromarray(image)
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    if isinstance(image, str):
        return Image.open(image)
    raise TypeError(f"Unsupported image type: {type(image)}")


def save_input_image_as_base64(image: Any, *, format: str = "JPEG") -> str:
    pil_image = image_to_pil(image)

    if pil_image.mode == "RGBA":
        pil_image = pil_image.convert("RGB")
    elif pil_image.mode not in ("RGB", "L"):
        pil_image = pil_image.convert("RGB")

    buffer = io.BytesIO()
    pil_image.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def prepare_json(workflow_data: dict[str, Any], images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"workflow": workflow_data}
    if images:
        payload["images"] = images
    return {"input": payload}


def prepare_json_with_video(
    workflow_data: dict[str, Any],
    images: list[dict[str, Any]] | None = None,
    videos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "input": {
            "workflow": workflow_data,
            "images": images or [],
            "videos": videos or [],
        }
    }

# ============================== Gradio App Helper Block ==============================
# Extracted from server_upscaler_with_flux_enhancement.py for cleaner app structure.

# ---- Progress and workflow parsing constants ----
COMFY_LOG_PATTERN = re.compile(r"^\[comfy-log\]\[(?P<phase>[^\]]+)\]\s*(?P<message>.*)$")
NODE_PROGRESS_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+(?P<done>\d+)/(?P<total>\d+)$"
)
RUNNING_NODE_PATTERN = re.compile(
    r"^Running node (?P<node>\d+(?::\d+)?):\s*(?P<label>.+)$"
)
EULER_PROGRESS_PATTERN = re.compile(r"EulerSampler:\s*(?P<pct>\d+)%\|")
QUEUE_REMAINING_PATTERN = re.compile(r"Queue remaining:\s*(?P<remaining>.+)$")
NODE_ID_PREFIX_PATTERN = re.compile(r"^(?P<node>\d+(?::\d+)?)\b")
FRACTION_PATTERN = re.compile(r"^(?P<idx>\d+)\s*/\s*(?P<total>\d+)$")
TOTAL_VALUE_PATTERN = re.compile(r"total\s*=\s*(?P<total>\d+)", re.IGNORECASE)
ENHANCE_ITEM_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+done=(?P<done>\d+)(?:\s+total=(?P<total>\d+))?$"
)
ENHANCE_STATE_PATTERN = re.compile(
    r"^(?:node=(?P<node>[^ ]+)\s+)?done=(?P<done>\d+)(?:\s+total=(?P<total>\d+))?$"
)
ENHANCE_STEP_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+item=(?P<item_done>\d+)(?:/(?P<item_total>\d+))?\s+step=(?P<step_done>\d+)/(?P<step_total>\d+)$"
)
ENHANCE_DONE_INLINE_PATTERN = re.compile(
    r"enhance_done=(?P<done>\d+)(?:/(?P<total>\d+))?",
    re.IGNORECASE,
)
PHASE_PREPARATION = "Preparation"
PHASE_UPSCALING = "Upscaling"
PHASE_ENHANCEMENT = "Enhancement"
PHASE_WRAP_UP = "Wrap-up"
PHASE_COMPLETED = "Completed"
SEEDVR_NODE_ID = "77:78"
ENHANCEMENT_NODE_ID = "80:12"
WRAP_UP_NODE_IDS = {"80:14", "81:38", "81:13", "97"}
DEFAULT_WRAP_UP_MILESTONES = {
    "80:14": 0.25,
    "81:38": 0.40,
    "81:13": 0.55,
    "97": 0.75,
}
PHASE_WEIGHT_PREPARATION = float(os.getenv("PHASE_WEIGHT_PREPARATION", "12"))
PHASE_WEIGHT_UPSCALING = float(os.getenv("PHASE_WEIGHT_UPSCALING", "46"))
PHASE_WEIGHT_ENHANCEMENT = float(os.getenv("PHASE_WEIGHT_ENHANCEMENT", "32"))
PHASE_WEIGHT_WRAP_UP = float(os.getenv("PHASE_WEIGHT_WRAP_UP", "10"))
ENHANCE_FALLBACK_COMPLETE_RATIO = float(
    os.getenv("ENHANCE_FALLBACK_COMPLETE_RATIO", "0.85")
)
PROGRESS_RECONCILE_MAX_MISSING = max(
    1,
    int(os.getenv("PROGRESS_RECONCILE_MAX_MISSING", "1")),
)
# Trace logging is disabled for production/client-facing runs.
RUNPOD_TRACE_DEBUG = False
RUNPOD_TRACE_DIR = Path(os.getenv("RUNPOD_TRACE_DIR", tempfile.gettempdir()))
DB_PATH = os.getenv("USER_DB_PATH", "users.db")
WORKFLOW_FILENAME = os.getenv("MOMI_WORKFLOW_FILE", "Seedvr_flux_upscaler_03.json")
MOMI_WORKFLOW_PROFILES_FILE = os.getenv("MOMI_WORKFLOW_PROFILES_FILE", "").strip()
MOMI_WORKFLOW_PROFILES_JSON = os.getenv("MOMI_WORKFLOW_PROFILES_JSON", "").strip()

BUILTIN_WORKFLOW_PROFILES: dict[str, dict[str, Any]] = {
    "default": {
        "upscale_node_id": None,
        "enhancement_node_id": None,
        "wrap_up_node_ids": [],
        "wrap_up_milestones": {},
        "seedvr_runtime_enabled": False,
        "upscale_label": "Upscaling",
        "enhancement_label": "Enhancement",
        "enhancement_total_from_upscale": True,
        "enhancement_total_override": None,
    },
    "5K_Upscale": {
        "upscale_node_id": SEEDVR_NODE_ID,
        "enhancement_node_id": ENHANCEMENT_NODE_ID,
        "wrap_up_node_ids": sorted(WRAP_UP_NODE_IDS),
        "wrap_up_milestones": DEFAULT_WRAP_UP_MILESTONES,
        "seedvr_runtime_enabled": True,
        "upscale_label": "SeedVR Upscaling",
        "enhancement_label": "Enhancement",
        "enhancement_total_from_upscale": True,
        "enhancement_total_override": None,
    },
    "Pro Upscaler": {
        "upscale_node_id": SEEDVR_NODE_ID,
        "enhancement_node_id": ENHANCEMENT_NODE_ID,
        "wrap_up_node_ids": sorted(WRAP_UP_NODE_IDS),
        "wrap_up_milestones": DEFAULT_WRAP_UP_MILESTONES,
        "seedvr_runtime_enabled": True,
        "upscale_label": "SeedVR Upscaling",
        "enhancement_label": "Enhancement",
        "enhancement_total_from_upscale": True,
        "enhancement_total_override": None,
    },
}


# ---- Workflow profile and config helpers ----
def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _deep_merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def _merge_profile_source(
    target: dict[str, dict[str, Any]],
    source: Any,
    source_name: str,
) -> None:
    if not isinstance(source, dict):
        logger.warning(
            "Ignoring workflow profile source '%s' because it is not a JSON object.",
            source_name,
        )
        return

    for profile_name, profile_cfg in source.items():
        if not isinstance(profile_cfg, dict):
            logger.warning(
                "Ignoring workflow profile '%s' in source '%s' because it is not an object.",
                profile_name,
                source_name,
            )
            continue

        key = str(profile_name)
        if key not in target:
            target[key] = {}
        _deep_merge_dict(target[key], profile_cfg)


def _load_custom_workflow_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}

    if MOMI_WORKFLOW_PROFILES_FILE:
        try:
            with open(MOMI_WORKFLOW_PROFILES_FILE, "r", encoding="utf-8") as fh:
                file_data = json.load(fh)
            _merge_profile_source(
                profiles,
                file_data,
                f"file:{MOMI_WORKFLOW_PROFILES_FILE}",
            )
        except Exception as err:
            logger.warning(
                "Could not load MOMI_WORKFLOW_PROFILES_FILE '%s': %s",
                MOMI_WORKFLOW_PROFILES_FILE,
                err,
            )

    if MOMI_WORKFLOW_PROFILES_JSON:
        try:
            env_data = json.loads(MOMI_WORKFLOW_PROFILES_JSON)
            _merge_profile_source(
                profiles,
                env_data,
                "env:MOMI_WORKFLOW_PROFILES_JSON",
            )
        except Exception as err:
            logger.warning("Could not parse MOMI_WORKFLOW_PROFILES_JSON: %s", err)

    return profiles


def _normalize_workflow_profile(raw_profile: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "upscale_node_id": None,
        "enhancement_node_id": None,
        "wrap_up_node_ids": [],
        "wrap_up_milestones": {},
        "seedvr_runtime_enabled": False,
        "upscale_label": "Upscaling",
        "enhancement_label": "Enhancement",
        "enhancement_total_from_upscale": True,
        "enhancement_total_override": None,
    }
    profile = _deep_merge_dict(deepcopy(defaults), raw_profile)

    def _normalize_node_id(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text if text else None

    profile["upscale_node_id"] = _normalize_node_id(profile.get("upscale_node_id"))
    profile["enhancement_node_id"] = _normalize_node_id(profile.get("enhancement_node_id"))

    raw_wrap_nodes = profile.get("wrap_up_node_ids")
    if isinstance(raw_wrap_nodes, str):
        wrap_nodes = [part.strip() for part in raw_wrap_nodes.split(",") if part.strip()]
    elif isinstance(raw_wrap_nodes, (list, tuple, set)):
        wrap_nodes = [str(part).strip() for part in raw_wrap_nodes if str(part).strip()]
    else:
        wrap_nodes = []

    # Keep order stable and remove duplicates.
    deduped_wrap_nodes = list(dict.fromkeys(wrap_nodes))
    profile["wrap_up_node_ids"] = deduped_wrap_nodes

    wrap_milestones: dict[str, float] = {}
    raw_wrap_milestones = profile.get("wrap_up_milestones")
    if isinstance(raw_wrap_milestones, dict):
        for node_id, ratio in raw_wrap_milestones.items():
            try:
                wrap_milestones[str(node_id)] = _clamp_ratio(float(ratio))
            except (TypeError, ValueError):
                continue

    if not wrap_milestones and deduped_wrap_nodes:
        count = len(deduped_wrap_nodes)
        for idx, node_id in enumerate(deduped_wrap_nodes, start=1):
            ratio = 0.20 + (idx / count) * 0.70
            wrap_milestones[node_id] = _clamp_ratio(ratio)

    profile["wrap_up_milestones"] = wrap_milestones
    profile["seedvr_runtime_enabled"] = _as_bool(
        profile.get("seedvr_runtime_enabled"),
        default=False,
    )
    profile["enhancement_total_from_upscale"] = _as_bool(
        profile.get("enhancement_total_from_upscale"),
        default=True,
    )
    override_raw = profile.get("enhancement_total_override")
    try:
        override_value = int(override_raw) if override_raw is not None else None
    except (TypeError, ValueError):
        override_value = None
    if override_value is not None and override_value <= 0:
        override_value = None
    profile["enhancement_total_override"] = override_value
    profile["upscale_label"] = str(profile.get("upscale_label") or "Upscaling")
    profile["enhancement_label"] = str(profile.get("enhancement_label") or "Enhancement")
    return profile


CUSTOM_WORKFLOW_PROFILES = _load_custom_workflow_profiles()


def _resolve_workflow_profile(workflow_name: str | None) -> dict[str, Any]:
    resolved = deepcopy(BUILTIN_WORKFLOW_PROFILES.get("default", {}))

    if workflow_name and workflow_name in BUILTIN_WORKFLOW_PROFILES:
        _deep_merge_dict(resolved, BUILTIN_WORKFLOW_PROFILES[workflow_name])

    if "default" in CUSTOM_WORKFLOW_PROFILES:
        _deep_merge_dict(resolved, CUSTOM_WORKFLOW_PROFILES["default"])

    if workflow_name and workflow_name in CUSTOM_WORKFLOW_PROFILES:
        _deep_merge_dict(resolved, CUSTOM_WORKFLOW_PROFILES[workflow_name])

    profile = _normalize_workflow_profile(resolved)
    profile["name"] = workflow_name or "default"
    return profile


# ---- I/O and storage helpers ----
def _create_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            pwd_hash BLOB NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage (
            email TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            file_path TEXT NOT NULL,
            workflow TEXT
        )
        """
    )
    conn.commit()
    return conn


def _resolve_workflow_path() -> Path:
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "api_workflow" / WORKFLOW_FILENAME,
        script_dir / "api_workflow" / "New_runpod" / WORKFLOW_FILENAME,
        script_dir.parent / "api_workflow" / WORKFLOW_FILENAME,
        script_dir.parent / "api_workflow" / "New_runpod" / WORKFLOW_FILENAME,
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Could not find workflow file '{WORKFLOW_FILENAME}' in the expected api_workflow folders."
    )


def _to_pil_image(image: Any) -> Image.Image:
    if image is None:
        raise ValueError("No input image provided.")
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, np.ndarray):
        return Image.fromarray(image)
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    if isinstance(image, str):
        return Image.open(image)
    raise TypeError(f"Unsupported image type: {type(image)}")


async def _read_url_image(url: str) -> Image.Image:
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            img_bytes = await response.read()
    return Image.open(io.BytesIO(img_bytes))


# ---- Status and progress rendering helpers ----
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
                parts.extend(str(item) for item in value if item)
            elif value:
                parts.append(str(value))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in parts:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return "\n".join(deduped)


def _has_final_output_payload(status: dict[str, Any]) -> bool:
    output = status.get("output")
    if not isinstance(output, dict):
        return False
    if output.get("status") == "success":
        return True
    if "message" in output or "images" in output:
        return True
    return False


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, value))


def _progress_bar(percent: int, width: int = 28) -> str:
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    return f"{'█' * filled}{'░' * (width - filled)}"


def _render_idle_status() -> str:
    return """
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:14px 16px;color:#e2e8f0;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
    <div style="font-weight:700;font-size:15px;">Ready</div>
    <div style="font-weight:700;font-size:18px;color:#22c55e;">0%</div>
  </div>
  <div style="margin-top:10px;height:10px;background:#1e293b;border-radius:999px;overflow:hidden;">
    <div style="height:10px;width:0%;background:linear-gradient(90deg,#22d3ee,#3b82f6);"></div>
  </div>
  <div style="margin-top:12px;font-size:13px;opacity:.9;">Upload an image and click Generate.</div>
</div>
"""


def _extract_node_id(text: str | None) -> str | None:
    if not text:
        return None
    match = NODE_ID_PREFIX_PATTERN.match(text.strip())
    if not match:
        return None
    return match.group("node")


def _compute_overall_percent(phase_tracker: dict[str, Any], *, completed: bool = False) -> int:
    if completed:
        return 100

    phase_name = phase_tracker["phase"]
    prep_ratio = float(phase_tracker["prep_ratio"])
    upscale_ratio = float(phase_tracker["upscale_ratio"])
    enhance_ratio = float(phase_tracker["enhance_ratio"])
    wrap_ratio = float(phase_tracker["wrap_ratio"])

    if phase_name in {PHASE_UPSCALING, PHASE_ENHANCEMENT, PHASE_WRAP_UP, PHASE_COMPLETED}:
        prep_ratio = 1.0

    if phase_name in {PHASE_ENHANCEMENT, PHASE_WRAP_UP, PHASE_COMPLETED}:
        upscale_ratio = max(upscale_ratio, 1.0 if phase_tracker.get("upscale_total") else upscale_ratio)

    if phase_name in {PHASE_WRAP_UP, PHASE_COMPLETED} and phase_tracker.get("enhance_total"):
        enhance_ratio = max(enhance_ratio, _clamp_ratio(phase_tracker["enhance_done"] / phase_tracker["enhance_total"]))

    prep_ratio = _clamp_ratio(prep_ratio)
    upscale_ratio = _clamp_ratio(upscale_ratio)
    enhance_ratio = _clamp_ratio(enhance_ratio)
    wrap_ratio = _clamp_ratio(wrap_ratio)

    weight_total = (
        PHASE_WEIGHT_PREPARATION
        + PHASE_WEIGHT_UPSCALING
        + PHASE_WEIGHT_ENHANCEMENT
        + PHASE_WEIGHT_WRAP_UP
    )
    if weight_total <= 0:
        return 0

    overall = (
        (prep_ratio * PHASE_WEIGHT_PREPARATION)
        + (upscale_ratio * PHASE_WEIGHT_UPSCALING)
        + (enhance_ratio * PHASE_WEIGHT_ENHANCEMENT)
        + (wrap_ratio * PHASE_WEIGHT_WRAP_UP)
    ) / weight_total

    return int(round(_clamp_ratio(overall) * 100))


def _render_live_status(
    fallback: str,
    runpod_progress: int | float | None,
    current_node: str | None,
    node_step_done: int | None,
    node_step_total: int | None,
    queue_remaining: str | None,
    logs: list[str],
    phase_tracker: dict[str, Any],
    overall_percent: int,
) -> str:
    del fallback, runpod_progress, queue_remaining
    phase_name = html.escape(str(phase_tracker.get("phase") or "Processing"))
    upscale_label = html.escape(str(phase_tracker.get("upscale_label") or "Upscaling"))
    enhancement_label = html.escape(str(phase_tracker.get("enhancement_label") or "Enhancement"))

    upscale_total = phase_tracker.get("upscale_total")
    if isinstance(upscale_total, int) and upscale_total > 0:
        upscale_text = html.escape(
            f"{int(phase_tracker.get('upscale_done') or 0)}/{upscale_total}"
        )
    else:
        upscale_text = "Starting..."

    enhance_total = phase_tracker.get("enhance_total")
    if isinstance(enhance_total, int) and enhance_total > 0:
        enhance_text = html.escape(
            f"{int(phase_tracker.get('enhance_done') or 0)}/{enhance_total}"
        )
    else:
        enhance_text = "Pending"

    tile_note = ""
    estimated_count = phase_tracker.get("estimated_tile_count")
    estimated_cols = phase_tracker.get("estimated_tile_columns")
    estimated_rows = phase_tracker.get("estimated_tile_rows")
    estimated_source_width = phase_tracker.get("estimated_tile_source_width")
    estimated_source_height = phase_tracker.get("estimated_tile_source_height")
    estimated_note = phase_tracker.get("estimated_tile_note")
    if (
        isinstance(estimated_count, int)
        and estimated_count > 0
        and isinstance(estimated_cols, int)
        and estimated_cols > 0
        and isinstance(estimated_rows, int)
        and estimated_rows > 0
    ):
        base_note = (
            f"Estimated tiled workload: {estimated_count} tile(s) "
            f"({estimated_cols} x {estimated_rows})."
        )
        if (
            isinstance(estimated_source_width, int)
            and estimated_source_width > 0
            and isinstance(estimated_source_height, int)
            and estimated_source_height > 0
        ):
            base_note += f" Pre-tile size: {estimated_source_width}x{estimated_source_height}."
        tile_note = base_note
    elif isinstance(estimated_note, str) and estimated_note.strip():
        tile_note = estimated_note.strip()
    safe_tile_note = html.escape(tile_note)
    tile_note_html = (
        f'<div style="margin-top:10px;font-size:12px;opacity:.78;">{safe_tile_note}</div>'
        if safe_tile_note
        else ""
    )

    del current_node, node_step_done, node_step_total, logs

    return f"""
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:14px 16px;color:#e2e8f0;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
    <div style="font-weight:700;font-size:15px;">Processing Your Request</div>
    <div style="font-weight:700;font-size:18px;color:#38bdf8;">{overall_percent}%</div>
  </div>
  <div style="margin-top:10px;height:10px;background:#1e293b;border-radius:999px;overflow:hidden;">
    <div style="height:10px;width:{overall_percent}%;background:linear-gradient(90deg,#22d3ee,#3b82f6);"></div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:12px;font-size:13px;">
    <div style="background:#111827;border:1px solid #1f2937;border-radius:10px;padding:8px 10px;">
      <div style="opacity:.75;font-size:11px;text-transform:uppercase;letter-spacing:.3px;">Phase</div>
      <div style="font-weight:600;margin-top:2px;">{phase_name}</div>
    </div>
    <div style="background:#111827;border:1px solid #1f2937;border-radius:10px;padding:8px 10px;">
      <div style="opacity:.75;font-size:11px;text-transform:uppercase;letter-spacing:.3px;">{upscale_label}</div>
      <div style="font-weight:600;margin-top:2px;">{upscale_text}</div>
    </div>
    <div style="background:#111827;border:1px solid #1f2937;border-radius:10px;padding:8px 10px;">
      <div style="opacity:.75;font-size:11px;text-transform:uppercase;letter-spacing:.3px;">{enhancement_label}</div>
      <div style="font-weight:600;margin-top:2px;">{enhance_text}</div>
    </div>
  </div>
  {tile_note_html}
</div>
"""


def _extract_progress_signal(
    status: dict[str, Any],
) -> tuple[int | float | None, str | None, list[str]]:
    progress = status.get("progress")
    output = status.get("output")

    runpod_progress: int | float | None = None
    if isinstance(progress, (int, float)):
        runpod_progress = progress

    text_candidates: list[str] = []
    if isinstance(progress, str) and progress.strip():
        text_candidates.append(progress.strip())

    if isinstance(output, str) and output.strip():
        text_candidates.append(output.strip())
    elif isinstance(output, dict):
        output_message = output.get("message")
        if isinstance(output_message, str) and output_message.strip():
            text_candidates.append(output_message.strip())

    chosen_text = _choose_progress_text(text_candidates)

    return runpod_progress, chosen_text, text_candidates


def _is_live_progress_text(text: str) -> bool:
    text = text.strip()
    if not text:
        return False

    lower = text.lower()
    if text.startswith("[comfy-log]"):
        return True
    if text.startswith("Running node "):
        return True
    if text.startswith("Still running"):
        return True
    if "queue remaining" in lower:
        return True
    if "execution finished" in lower:
        return True
    if "collecting outputs" in lower:
        return True
    if "fetching execution history" in lower:
        return True
    if "job completed. returning" in lower:
        return True
    return False


def _choose_progress_text(text_candidates: list[str]) -> str | None:
    comfy_lines = [line for line in text_candidates if line.startswith("[comfy-log]")]
    if comfy_lines:
        return comfy_lines[-1]

    live_lines = [line for line in text_candidates if _is_live_progress_text(line)]
    if live_lines:
        return live_lines[-1]

    if text_candidates:
        return text_candidates[-1]
    return None


def _collect_text_candidates(value: Any, *, _depth: int = 0) -> list[str]:
    if _depth > 4:
        return []

    candidates: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            candidates.append(text)
        return candidates

    if isinstance(value, list):
        for item in value:
            candidates.extend(_collect_text_candidates(item, _depth=_depth + 1))
        return candidates

    if isinstance(value, dict):
        for key in ("progress", "message", "log", "text", "output"):
            if key in value:
                candidates.extend(
                    _collect_text_candidates(value.get(key), _depth=_depth + 1)
                )
        return candidates

    return candidates


def _stream_chunk_signature(chunk: Any) -> str:
    try:
        return json.dumps(chunk, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(chunk)


def _extract_stream_progress_signals(
    stream_response: Any,
    *,
    seen_signatures: set[str],
    seen_order: list[str],
) -> tuple[list[tuple[int | float | None, str, list[str]]], str | None]:
    stream_status: str | None = None
    stream_chunks: list[Any] = []

    if isinstance(stream_response, dict):
        status_value = stream_response.get("status")
        if isinstance(status_value, str) and status_value.strip():
            stream_status = status_value.strip().upper()

        raw_stream = stream_response.get("stream")
        if isinstance(raw_stream, list):
            stream_chunks = raw_stream
        elif raw_stream is not None:
            stream_chunks = [raw_stream]
        elif any(
            key in stream_response
            for key in ("output", "message", "progress", "log", "text")
        ):
            stream_chunks = [stream_response]
    elif isinstance(stream_response, list):
        stream_chunks = stream_response
    else:
        return [], stream_status

    progress_entries: list[tuple[int | float | None, str, list[str]]] = []
    for chunk in stream_chunks:
        signature = _stream_chunk_signature(chunk)
        if signature in seen_signatures:
            continue

        seen_signatures.add(signature)
        seen_order.append(signature)
        while len(seen_order) > RUNPOD_STREAM_MAX_SEEN_CHUNKS:
            stale = seen_order.pop(0)
            seen_signatures.discard(stale)

        runpod_progress: int | float | None = None
        if isinstance(chunk, dict) and isinstance(chunk.get("progress"), (int, float)):
            runpod_progress = chunk.get("progress")

        text_candidates = _collect_text_candidates(chunk)
        chosen_text = _choose_progress_text(text_candidates)
        if chosen_text is None:
            continue

        progress_entries.append((runpod_progress, chosen_text, text_candidates))

    return progress_entries, stream_status


# ---- Trace and reconciliation helpers ----
def _init_trace_file(job_id: str, workflow: str) -> Path | None:
    if not RUNPOD_TRACE_DEBUG:
        return None

    try:
        RUNPOD_TRACE_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_workflow = re.sub(r"[^a-zA-Z0-9._-]", "_", workflow)[:64]
        filename = f"runpod_trace_{safe_workflow}_{job_id}_{timestamp}.jsonl"
        return RUNPOD_TRACE_DIR / filename
    except Exception as err:
        logger.warning("Could not initialize RunPod trace file: %s", err)
        return None


def _status_output_preview(output: Any) -> Any:
    if isinstance(output, dict):
        preview: dict[str, Any] = {}
        for key in ("status", "error"):
            if key in output and output.get(key) is not None:
                preview[key] = output.get(key)

        message = output.get("message")
        if isinstance(message, str):
            preview["message"] = message[:300]
        elif isinstance(message, list):
            preview["message_count"] = len(message)
            if message and isinstance(message[0], str):
                preview["message_first"] = message[0][:180]

        if output.get("images") is not None:
            images = output.get("images")
            preview["images_count"] = len(images) if isinstance(images, list) else None
        return preview

    if isinstance(output, str):
        return output[:300]
    return None


def _status_trace_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status.get("status"),
        "id": status.get("id"),
        "delayTime": status.get("delayTime"),
        "executionTime": status.get("executionTime"),
        "progress_type": type(status.get("progress")).__name__,
        "progress": (
            status.get("progress")[:300]
            if isinstance(status.get("progress"), str)
            else status.get("progress")
        ),
        "output_preview": _status_output_preview(status.get("output")),
    }


def _stream_trace_snapshot(stream_response: Any) -> dict[str, Any]:
    if isinstance(stream_response, dict):
        stream_value = stream_response.get("stream")
        if isinstance(stream_value, list):
            stream_count = len(stream_value)
            last_chunk_type = (
                type(stream_value[-1]).__name__ if stream_value else None
            )
        elif stream_value is None:
            stream_count = 0
            last_chunk_type = None
        else:
            stream_count = 1
            last_chunk_type = type(stream_value).__name__

        return {
            "status": stream_response.get("status"),
            "stream_count": stream_count,
            "stream_type": type(stream_value).__name__ if stream_value is not None else None,
            "last_chunk_type": last_chunk_type,
        }

    if isinstance(stream_response, list):
        return {
            "status": None,
            "stream_count": len(stream_response),
            "stream_type": "list",
            "last_chunk_type": type(stream_response[-1]).__name__ if stream_response else None,
        }

    return {
        "status": None,
        "stream_count": 0,
        "stream_type": type(stream_response).__name__,
        "last_chunk_type": None,
    }


def _phase_trace_snapshot(phase_tracker: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "phase",
        "prep_ratio",
        "upscale_ratio",
        "wrap_ratio",
        "upscale_done",
        "upscale_total",
        "seedvr_frames_total",
        "seedvr_stage",
        "enhance_done",
        "enhance_total",
        "enhance_ratio",
        "enhance_runtime_seen",
        "enhance_item_seen",
        "enhance_peak_step",
        "upscale_node_id",
        "enhancement_node_id",
        "wrap_up_node_ids",
        "upscale_label",
        "enhancement_label",
        "enhancement_total_from_upscale",
        "enhancement_total_override",
        "estimated_tile_columns",
        "estimated_tile_rows",
        "estimated_tile_count",
        "estimated_tile_source_width",
        "estimated_tile_source_height",
        "estimated_tile_divisor",
        "estimated_tile_note",
    )
    return {key: phase_tracker.get(key) for key in keys}


def _enhancement_is_complete(phase_tracker: dict[str, Any]) -> bool:
    total = phase_tracker.get("enhance_total")
    if not total:
        return False
    try:
        total_int = int(total)
    except (TypeError, ValueError):
        return False
    if total_int <= 0:
        return False
    return int(phase_tracker.get("enhance_done") or 0) >= total_int


def _maybe_promote_wrap_up_from_post_enhancement_node(
    phase_tracker: dict[str, Any],
    node_id: str | None,
    wrap_up_milestones: dict[str, float],
    *,
    reason: str,
) -> None:
    if phase_tracker.get("phase") != PHASE_ENHANCEMENT:
        return
    if not node_id:
        return
    enhancement_node_id = phase_tracker.get("enhancement_node_id")
    if enhancement_node_id and node_id == enhancement_node_id:
        return
    if not _enhancement_is_complete(phase_tracker):
        return

    phase_tracker["phase"] = PHASE_WRAP_UP
    phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
    phase_tracker["seedvr_stage"] = None
    phase_tracker["wrap_ratio"] = max(
        phase_tracker["wrap_ratio"], wrap_up_milestones.get(node_id, 0.30)
    )
    logger.info(
        "Promoted to wrap-up via %s on node %s after enhancement completion.",
        reason,
        node_id,
    )


def _maybe_set_enhancement_total_from_upscale(
    phase_tracker: dict[str, Any],
    total: Any,
) -> None:
    if not phase_tracker.get("enhancement_total_from_upscale", True):
        return
    if phase_tracker.get("enhancement_total_override") is not None:
        return
    if total is None:
        return
    try:
        total_int = int(total)
    except (TypeError, ValueError):
        return
    if total_int <= 0:
        return
    current_total = phase_tracker.get("enhance_total")
    if current_total is None:
        phase_tracker["enhance_total"] = total_int
        return
    try:
        current_int = int(current_total)
    except (TypeError, ValueError):
        phase_tracker["enhance_total"] = total_int
        return
    if current_int <= 0 or total_int > current_int:
        phase_tracker["enhance_total"] = total_int


def _map_done_to_total(
    done: int,
    source_total: int,
    target_total: int,
) -> int:
    if source_total <= 0 or target_total <= 0:
        return max(0, done)
    if source_total == target_total:
        return max(0, min(target_total, done))

    mapped = int(round((done / source_total) * target_total))
    return max(0, min(target_total, mapped))


def _is_near_complete(
    done: int,
    total: int,
    *,
    max_missing: int = PROGRESS_RECONCILE_MAX_MISSING,
) -> bool:
    if total <= 0:
        return False
    if done >= total:
        return True
    return (total - done) <= max_missing


def _reconcile_upscale_near_completion(
    phase_tracker: dict[str, Any],
    *,
    reason: str,
) -> None:
    total = phase_tracker.get("upscale_total")
    if not isinstance(total, int) or total <= 0:
        return

    done = int(phase_tracker.get("upscale_done") or 0)
    if done >= total:
        return
    if done <= 0:
        return
    if not _is_near_complete(done, total):
        return

    phase_tracker["upscale_done"] = total
    phase_tracker["upscale_ratio"] = max(float(phase_tracker.get("upscale_ratio") or 0.0), 1.0)
    _maybe_set_enhancement_total_from_upscale(phase_tracker, total)
    logger.info(
        "Upscale completion reconciled (%s): done=%s/%s -> %s/%s",
        reason,
        done,
        total,
        total,
        total,
    )


def _reconcile_enhancement_near_completion(
    phase_tracker: dict[str, Any],
    *,
    reason: str,
) -> None:
    total = phase_tracker.get("enhance_total")
    if not isinstance(total, int) or total <= 0:
        return

    done = int(phase_tracker.get("enhance_done") or 0)
    if done >= total:
        return
    if done <= 0:
        return
    if not _is_near_complete(done, total):
        return

    phase_tracker["enhance_done"] = total
    phase_tracker["enhance_ratio"] = 1.0
    phase_tracker["enhance_runtime_seen"] = True
    phase_tracker["enhance_item_seen"] = True
    logger.info(
        "Enhancement completion reconciled (%s): done=%s/%s -> %s/%s",
        reason,
        done,
        total,
        total,
        total,
    )


def _append_trace_event(trace_file: Path | None, event: str, payload: dict[str, Any]) -> None:
    if trace_file is None:
        return

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "payload": payload,
    }
    try:
        with open(trace_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as err:
        logger.warning("Trace write failed (%s): %s", trace_file, err)


def _maybe_finalize_enhancement_cycle(
    phase_tracker: dict[str, Any],
    *,
    reason: str,
) -> None:
    if phase_tracker.get("enhance_runtime_seen", False):
        return

    total_steps = phase_tracker.get("enhance_last_total_steps")
    if not isinstance(total_steps, int) or total_steps <= 0:
        return

    peak_step = int(phase_tracker.get("enhance_peak_step") or 0)
    threshold = max(1, int(round(total_steps * _clamp_ratio(ENHANCE_FALLBACK_COMPLETE_RATIO))))
    cycle_complete = bool(phase_tracker.get("enhance_cycle_complete", False))

    if not cycle_complete and peak_step >= threshold:
        if phase_tracker.get("enhance_total"):
            phase_tracker["enhance_done"] = min(
                phase_tracker["enhance_done"] + 1,
                phase_tracker["enhance_total"],
            )
        else:
            phase_tracker["enhance_done"] += 1

        if phase_tracker.get("enhance_total"):
            phase_tracker["enhance_ratio"] = _clamp_ratio(
                phase_tracker["enhance_done"] / phase_tracker["enhance_total"]
            )

        logger.info(
            "Enhancement fallback increment (%s): peak=%s threshold=%s total_steps=%s enhance_done=%s",
            reason,
            peak_step,
            threshold,
            total_steps,
            phase_tracker.get("enhance_done"),
        )

    phase_tracker["enhance_cycle_complete"] = False
    phase_tracker["enhance_peak_step"] = 0
    phase_tracker["enhance_last_step"] = None
    phase_tracker["enhance_last_total_steps"] = None


# ---- Phase tracker update helpers ----
def _update_phase_tracker_from_progress_text(
    progress_text: str,
    phase_tracker: dict[str, Any],
) -> None:
    if (
        phase_tracker.get("enhance_total") is None
        and phase_tracker.get("enhancement_total_override") is not None
    ):
        phase_tracker["enhance_total"] = int(phase_tracker["enhancement_total_override"])

    text_lower = progress_text.lower()
    upscale_node_id = phase_tracker.get("upscale_node_id")
    enhancement_node_id = phase_tracker.get("enhancement_node_id")
    wrap_up_node_ids = set(phase_tracker.get("wrap_up_node_ids") or [])
    wrap_up_milestones = phase_tracker.get("wrap_up_milestones") or {}
    seedvr_runtime_enabled = bool(phase_tracker.get("seedvr_runtime_enabled", False))
    seedvr_frames_total = phase_tracker.get("seedvr_frames_total")

    # Preparation milestones before hitting the main processing nodes.
    if phase_tracker["phase"] == PHASE_PREPARATION:
        if "starting job and validating input" in text_lower:
            phase_tracker["prep_ratio"] = max(phase_tracker["prep_ratio"], 0.10)
        elif "connected to comfyui worker" in text_lower:
            phase_tracker["prep_ratio"] = max(phase_tracker["prep_ratio"], 0.25)
        elif "workflow queued" in text_lower:
            phase_tracker["prep_ratio"] = max(phase_tracker["prep_ratio"], 0.45)
        elif "execution started" in text_lower:
            phase_tracker["prep_ratio"] = max(phase_tracker["prep_ratio"], 0.70)

    inline_enhance = ENHANCE_DONE_INLINE_PATTERN.search(progress_text)
    if inline_enhance:
        done_value = int(inline_enhance.group("done"))
        total_raw = inline_enhance.group("total")
        total_value = int(total_raw) if total_raw and total_raw.isdigit() else None

        if total_value and total_value > 0:
            phase_tracker["enhance_total"] = total_value

        if phase_tracker.get("enhance_total"):
            phase_tracker["enhance_done"] = min(
                max(phase_tracker["enhance_done"], done_value),
                phase_tracker["enhance_total"],
            )
            phase_tracker["enhance_ratio"] = _clamp_ratio(
                phase_tracker["enhance_done"] / phase_tracker["enhance_total"]
            )
        else:
            phase_tracker["enhance_done"] = max(
                phase_tracker["enhance_done"], done_value
            )

        phase_tracker["enhance_runtime_seen"] = True
        phase_tracker["enhance_item_seen"] = True
        if phase_tracker.get("phase") not in {PHASE_WRAP_UP, PHASE_COMPLETED}:
            phase_tracker["phase"] = PHASE_ENHANCEMENT
            phase_tracker["prep_ratio"] = 1.0
            phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
            phase_tracker["seedvr_stage"] = None

    parsed = COMFY_LOG_PATTERN.match(progress_text)
    if parsed:
        comfy_phase = parsed.group("phase").strip().lower()
        phase_message = parsed.group("message").strip()

        if seedvr_runtime_enabled and comfy_phase == "seedvr-frames":
            total_match = TOTAL_VALUE_PATTERN.search(phase_message)
            if total_match:
                total_frames = int(total_match.group("total"))
                if total_frames > 0:
                    phase_tracker["seedvr_frames_total"] = total_frames
                    phase_tracker["upscale_total"] = total_frames
                    _maybe_set_enhancement_total_from_upscale(
                        phase_tracker, total_frames
                    )
                    phase_tracker["phase"] = PHASE_UPSCALING
                    phase_tracker["prep_ratio"] = 1.0
                    seedvr_frames_total = total_frames

        elif seedvr_runtime_enabled and comfy_phase in {"seedvr-encode", "seedvr-upscale", "seedvr-decode"}:
            fraction = FRACTION_PATTERN.match(phase_message)
            if fraction:
                idx = int(fraction.group("idx"))
                raw_total = int(fraction.group("total"))
                if raw_total > 0:
                    canonical_total = (
                        int(seedvr_frames_total)
                        if isinstance(seedvr_frames_total, int) and seedvr_frames_total > 0
                        else raw_total
                    )
                    mapped_idx = _map_done_to_total(idx, raw_total, canonical_total)
                    phase_tracker["phase"] = PHASE_UPSCALING
                    phase_tracker["prep_ratio"] = 1.0
                    phase_tracker["upscale_total"] = canonical_total
                    _maybe_set_enhancement_total_from_upscale(phase_tracker, canonical_total)

                    if comfy_phase == "seedvr-encode":
                        # Encode is a preparation sub-step for SeedVR; do not count it as
                        # completed upscale items.
                        phase_tracker["seedvr_stage"] = "VAE encode (prep)"
                    elif comfy_phase == "seedvr-upscale":
                        phase_tracker["seedvr_stage"] = "SeedVR upscale"
                        phase_tracker["upscale_done"] = min(mapped_idx, canonical_total)
                        phase_tracker["upscale_ratio"] = _clamp_ratio(
                            mapped_idx / canonical_total
                        )
                    else:
                        # Decode is a wrap-up sub-step for SeedVR; do not count it as
                        # additional upscale items.
                        phase_tracker["seedvr_stage"] = "VAE decode (wrap-up)"
                        if int(phase_tracker.get("upscale_done") or 0) >= canonical_total:
                            phase_tracker["upscale_ratio"] = max(
                                phase_tracker["upscale_ratio"], 1.0
                            )

        elif comfy_phase == "enhance-frames":
            total_match = TOTAL_VALUE_PATTERN.search(phase_message)
            if total_match:
                enhance_total = int(total_match.group("total"))
                if enhance_total > 0:
                    phase_tracker["enhance_total"] = enhance_total

        elif comfy_phase == "enhance-node":
            node_match = re.search(r"node=(?P<node>[^ ]+)", phase_message)
            if node_match:
                phase_tracker["enhancement_node_id"] = node_match.group("node")

        elif comfy_phase == "enhance-state":
            state_match = ENHANCE_STATE_PATTERN.match(phase_message)
            if state_match:
                node_id = state_match.group("node")
                done_value = int(state_match.group("done"))
                total_raw = state_match.group("total")
                total_value = int(total_raw) if total_raw and total_raw.isdigit() else None

                if node_id:
                    phase_tracker["enhancement_node_id"] = node_id
                if total_value and total_value > 0:
                    phase_tracker["enhance_total"] = total_value

                if phase_tracker.get("enhance_total"):
                    phase_tracker["enhance_done"] = min(
                        max(phase_tracker["enhance_done"], done_value),
                        phase_tracker["enhance_total"],
                    )
                    phase_tracker["enhance_ratio"] = _clamp_ratio(
                        phase_tracker["enhance_done"] / phase_tracker["enhance_total"]
                    )
                else:
                    phase_tracker["enhance_done"] = max(
                        phase_tracker["enhance_done"], done_value
                    )

                phase_tracker["enhance_runtime_seen"] = True
                phase_tracker["enhance_item_seen"] = True
                if phase_tracker.get("phase") not in {PHASE_WRAP_UP, PHASE_COMPLETED}:
                    phase_tracker["phase"] = PHASE_ENHANCEMENT
                    phase_tracker["prep_ratio"] = 1.0
                    phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                    phase_tracker["seedvr_stage"] = None

        elif comfy_phase == "enhance-item":
            item_match = ENHANCE_ITEM_PATTERN.match(phase_message)
            if item_match:
                node_id = item_match.group("node")
                done_value = int(item_match.group("done"))
                total_raw = item_match.group("total")
                total_value = int(total_raw) if total_raw and total_raw.isdigit() else None

                phase_tracker["phase"] = PHASE_ENHANCEMENT
                phase_tracker["prep_ratio"] = 1.0
                phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                phase_tracker["seedvr_stage"] = None
                phase_tracker["enhance_runtime_seen"] = True
                phase_tracker["enhance_item_seen"] = True
                if node_id:
                    phase_tracker["enhancement_node_id"] = node_id

                if total_value and total_value > 0:
                    phase_tracker["enhance_total"] = total_value

                if phase_tracker.get("enhance_total"):
                    phase_tracker["enhance_done"] = min(
                        max(phase_tracker["enhance_done"], done_value),
                        phase_tracker["enhance_total"],
                    )
                    phase_tracker["enhance_ratio"] = _clamp_ratio(
                        phase_tracker["enhance_done"] / phase_tracker["enhance_total"]
                    )
                else:
                    phase_tracker["enhance_done"] = max(
                        phase_tracker["enhance_done"], done_value
                    )

        elif comfy_phase == "enhance-step":
            step_match = ENHANCE_STEP_PATTERN.match(phase_message)
            if step_match:
                node_id = step_match.group("node")
                item_total_raw = step_match.group("item_total")
                item_total = (
                    int(item_total_raw)
                    if item_total_raw and item_total_raw.isdigit()
                    else None
                )

                phase_tracker["phase"] = PHASE_ENHANCEMENT
                phase_tracker["prep_ratio"] = 1.0
                phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                phase_tracker["seedvr_stage"] = None
                phase_tracker["enhance_runtime_seen"] = True
                if node_id:
                    phase_tracker["enhancement_node_id"] = node_id
                if item_total and item_total > 0 and phase_tracker.get("enhance_total") is None:
                    phase_tracker["enhance_total"] = item_total

        elif comfy_phase == "enhance-sample":
            phase_tracker["phase"] = PHASE_ENHANCEMENT
            phase_tracker["prep_ratio"] = 1.0
            phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
            phase_tracker["seedvr_stage"] = None
            phase_tracker["enhance_runtime_seen"] = True

            done_value: int | None = None
            total_value: int | None = None
            fraction = FRACTION_PATTERN.match(phase_message)
            if fraction:
                done_value = int(fraction.group("idx"))
                total_value = int(fraction.group("total"))
            elif phase_message.isdigit():
                done_value = int(phase_message)

            if total_value is not None and total_value > 0:
                phase_tracker["enhance_total"] = total_value
            elif (
                phase_tracker.get("enhance_total") is None
                and phase_tracker.get("upscale_total")
            ):
                phase_tracker["enhance_total"] = phase_tracker["upscale_total"]

            if done_value is not None and not phase_tracker.get("enhance_item_seen", False):
                enhance_total = phase_tracker.get("enhance_total")
                if enhance_total:
                    phase_tracker["enhance_done"] = min(
                        max(phase_tracker["enhance_done"], done_value),
                        enhance_total,
                    )
                    phase_tracker["enhance_ratio"] = _clamp_ratio(
                        phase_tracker["enhance_done"] / enhance_total
                    )
                else:
                    phase_tracker["enhance_done"] = max(
                        phase_tracker["enhance_done"], done_value
                    )

        if comfy_phase == "node":
            node_id = _extract_node_id(phase_message)
            if phase_tracker.get("phase") == PHASE_UPSCALING:
                if (not upscale_node_id) or (node_id and node_id != upscale_node_id):
                    _reconcile_upscale_near_completion(
                        phase_tracker, reason="node-transition"
                    )
            if phase_tracker.get("phase") == PHASE_ENHANCEMENT:
                if (not enhancement_node_id) or (node_id and node_id != enhancement_node_id):
                    _reconcile_enhancement_near_completion(
                        phase_tracker, reason="node-transition"
                    )
            if (
                enhancement_node_id
                and node_id != enhancement_node_id
                and phase_tracker.get("phase") == PHASE_ENHANCEMENT
            ):
                _maybe_finalize_enhancement_cycle(phase_tracker, reason="node-transition")

            if upscale_node_id and node_id == upscale_node_id:
                phase_tracker["phase"] = PHASE_UPSCALING
                phase_tracker["prep_ratio"] = 1.0
            elif enhancement_node_id and node_id == enhancement_node_id:
                phase_tracker["phase"] = PHASE_ENHANCEMENT
                phase_tracker["prep_ratio"] = 1.0
                phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                phase_tracker["seedvr_stage"] = None
                if phase_tracker.get("upscale_total"):
                    _maybe_set_enhancement_total_from_upscale(
                        phase_tracker, phase_tracker["upscale_total"]
                    )
            elif node_id in wrap_up_node_ids:
                phase_tracker["phase"] = PHASE_WRAP_UP
                phase_tracker["prep_ratio"] = 1.0
                phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                phase_tracker["seedvr_stage"] = None
                phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.20)
            else:
                _maybe_promote_wrap_up_from_post_enhancement_node(
                    phase_tracker,
                    node_id,
                    wrap_up_milestones,
                    reason="node-log",
                )

        elif comfy_phase == "progress":
            node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
            if node_progress:
                node_id = node_progress.group("node")
                done = int(node_progress.group("done"))
                total = int(node_progress.group("total"))

                if phase_tracker.get("phase") == PHASE_UPSCALING:
                    if (not upscale_node_id) or (node_id != upscale_node_id):
                        _reconcile_upscale_near_completion(
                            phase_tracker, reason="progress-transition"
                        )
                if phase_tracker.get("phase") == PHASE_ENHANCEMENT:
                    if (not enhancement_node_id) or (node_id != enhancement_node_id):
                        _reconcile_enhancement_near_completion(
                            phase_tracker, reason="progress-transition"
                        )

                if upscale_node_id and node_id == upscale_node_id and total > 0:
                    phase_tracker["phase"] = PHASE_UPSCALING
                    phase_tracker["prep_ratio"] = 1.0
                    # For SeedVR runtime-enabled profiles, we rely on seedvr-upscale runtime
                    # logs for item counting and ignore generic internal node progress.
                    if not seedvr_runtime_enabled:
                        # Some workflows expose internal work units (e.g. 0..100) instead of
                        # per-image progress. Use this only as a fallback ratio.
                        phase_tracker["upscale_ratio"] = max(
                            phase_tracker["upscale_ratio"], _clamp_ratio(done / total)
                        )

                elif enhancement_node_id and node_id == enhancement_node_id and total > 0:
                    phase_tracker["phase"] = PHASE_ENHANCEMENT
                    phase_tracker["prep_ratio"] = 1.0
                    phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                    phase_tracker["seedvr_stage"] = None
                    if phase_tracker.get("enhance_total") is None and phase_tracker.get("upscale_total"):
                        _maybe_set_enhancement_total_from_upscale(
                            phase_tracker, phase_tracker["upscale_total"]
                        )

                    # Fallback path only: if runtime enhancement logs are not available.
                    if not phase_tracker.get("enhance_runtime_seen", False):
                        prev_total = phase_tracker.get("enhance_last_total_steps")
                        prev_step = phase_tracker.get("enhance_last_step")
                        peak_step = int(phase_tracker.get("enhance_peak_step") or 0)

                        if prev_total != total:
                            phase_tracker["enhance_cycle_complete"] = False
                            phase_tracker["enhance_peak_step"] = done
                            peak_step = done

                        if prev_step is not None and done < prev_step:
                            phase_tracker["enhance_last_total_steps"] = total
                            _maybe_finalize_enhancement_cycle(
                                phase_tracker, reason="step-reset"
                            )
                            phase_tracker["enhance_peak_step"] = done
                            peak_step = done

                        phase_tracker["enhance_peak_step"] = max(peak_step, done)

                        if done >= total and not phase_tracker.get("enhance_cycle_complete", False):
                            phase_tracker["enhance_done"] += 1
                            phase_tracker["enhance_cycle_complete"] = True
                            phase_tracker["enhance_peak_step"] = 0
                        elif done < total:
                            phase_tracker["enhance_cycle_complete"] = False

                        phase_tracker["enhance_last_step"] = done
                        phase_tracker["enhance_last_total_steps"] = total

                    if phase_tracker.get("enhance_total"):
                        phase_tracker["enhance_done"] = min(
                            phase_tracker["enhance_done"], phase_tracker["enhance_total"]
                        )
                        phase_tracker["enhance_ratio"] = _clamp_ratio(
                            phase_tracker["enhance_done"] / phase_tracker["enhance_total"]
                        )
                elif node_id in wrap_up_node_ids:
                    phase_tracker["phase"] = PHASE_WRAP_UP
                    phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                    phase_tracker["seedvr_stage"] = None
                    phase_tracker["wrap_ratio"] = max(
                        phase_tracker["wrap_ratio"], wrap_up_milestones.get(node_id, 0.20)
                    )
                else:
                    _maybe_promote_wrap_up_from_post_enhancement_node(
                        phase_tracker,
                        node_id,
                        wrap_up_milestones,
                        reason="progress-log",
                    )

        elif comfy_phase == "executed":
            node_id = _extract_node_id(phase_message)
            if phase_tracker.get("phase") == PHASE_UPSCALING:
                if (not upscale_node_id) or (node_id and node_id != upscale_node_id):
                    _reconcile_upscale_near_completion(
                        phase_tracker, reason="executed-transition"
                    )
            if phase_tracker.get("phase") == PHASE_ENHANCEMENT:
                if (not enhancement_node_id) or (node_id and node_id != enhancement_node_id):
                    _reconcile_enhancement_near_completion(
                        phase_tracker, reason="executed-transition"
                    )
            if (
                enhancement_node_id
                and node_id != enhancement_node_id
                and phase_tracker.get("phase") == PHASE_ENHANCEMENT
            ):
                _maybe_finalize_enhancement_cycle(
                    phase_tracker, reason="executed-transition"
                )

            if upscale_node_id and node_id == upscale_node_id:
                phase_tracker["phase"] = PHASE_UPSCALING
                if phase_tracker.get("upscale_total"):
                    phase_tracker["upscale_done"] = phase_tracker["upscale_total"]
                    phase_tracker["upscale_ratio"] = 1.0
                    _maybe_set_enhancement_total_from_upscale(
                        phase_tracker, phase_tracker["upscale_total"]
                    )
                    phase_tracker["seedvr_stage"] = "VAE decode"
            elif enhancement_node_id and node_id == enhancement_node_id:
                phase_tracker["phase"] = PHASE_ENHANCEMENT
                phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                phase_tracker["seedvr_stage"] = None
                # Fallback path only: if runtime enhancement logs are not available.
                if not phase_tracker.get("enhance_runtime_seen", False):
                    # If sampler progress already reached N/N for this image, avoid double counting.
                    if not phase_tracker.get("enhance_cycle_complete", False):
                        if phase_tracker.get("enhance_total"):
                            phase_tracker["enhance_done"] = min(
                                phase_tracker["enhance_done"] + 1, phase_tracker["enhance_total"]
                            )
                        else:
                            phase_tracker["enhance_done"] += 1

                    phase_tracker["enhance_cycle_complete"] = False
                    phase_tracker["enhance_peak_step"] = 0
                    phase_tracker["enhance_last_step"] = None
                    phase_tracker["enhance_last_total_steps"] = None

                if phase_tracker.get("enhance_total"):
                    phase_tracker["enhance_ratio"] = _clamp_ratio(
                        phase_tracker["enhance_done"] / phase_tracker["enhance_total"]
                    )
            elif node_id in wrap_up_node_ids:
                phase_tracker["phase"] = PHASE_WRAP_UP
                phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
                phase_tracker["seedvr_stage"] = None
                phase_tracker["wrap_ratio"] = max(
                    phase_tracker["wrap_ratio"], wrap_up_milestones.get(node_id, 0.20)
                )
            else:
                _maybe_promote_wrap_up_from_post_enhancement_node(
                    phase_tracker,
                    node_id,
                    wrap_up_milestones,
                    reason="executed-log",
                )

        elif comfy_phase == "execution" and "finished" in phase_message.lower():
            _reconcile_upscale_near_completion(phase_tracker, reason="execution-finished")
            _reconcile_enhancement_near_completion(phase_tracker, reason="execution-finished")
            phase_tracker["phase"] = PHASE_WRAP_UP
            phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.30)
        elif comfy_phase == "status" and "queue_remaining=0" in phase_message:
            _reconcile_upscale_near_completion(phase_tracker, reason="queue-empty")
            _reconcile_enhancement_near_completion(phase_tracker, reason="queue-empty")
            phase_tracker["phase"] = PHASE_WRAP_UP
            phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.95)

    # Non-structured milestones (still useful with delayed or plain progress messages).
    if "fetching execution history" in text_lower:
        _reconcile_upscale_near_completion(phase_tracker, reason="fetch-history")
        _reconcile_enhancement_near_completion(phase_tracker, reason="fetch-history")
        phase_tracker["phase"] = PHASE_WRAP_UP
        phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.45)
    elif "processing output nodes and collecting images" in text_lower:
        _reconcile_upscale_near_completion(phase_tracker, reason="process-outputs")
        _reconcile_enhancement_near_completion(phase_tracker, reason="process-outputs")
        phase_tracker["phase"] = PHASE_WRAP_UP
        phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.65)
    elif "collecting images from node" in text_lower:
        _reconcile_upscale_near_completion(phase_tracker, reason="collect-images")
        _reconcile_enhancement_near_completion(phase_tracker, reason="collect-images")
        phase_tracker["phase"] = PHASE_WRAP_UP
        phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.85)
    elif "finalizing output" in text_lower:
        _reconcile_upscale_near_completion(phase_tracker, reason="finalizing-output")
        _reconcile_enhancement_near_completion(phase_tracker, reason="finalizing-output")
        phase_tracker["phase"] = PHASE_WRAP_UP
        phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.92)

    running_node = RUNNING_NODE_PATTERN.match(progress_text)
    if running_node:
        node_id = running_node.group("node").strip()
        if phase_tracker.get("phase") == PHASE_UPSCALING:
            if (not upscale_node_id) or (node_id != upscale_node_id):
                _reconcile_upscale_near_completion(
                    phase_tracker, reason="running-transition"
                )
        if phase_tracker.get("phase") == PHASE_ENHANCEMENT:
            if (not enhancement_node_id) or (node_id != enhancement_node_id):
                _reconcile_enhancement_near_completion(
                    phase_tracker, reason="running-transition"
                )
        if (
            enhancement_node_id
            and node_id != enhancement_node_id
            and phase_tracker.get("phase") == PHASE_ENHANCEMENT
        ):
            _maybe_finalize_enhancement_cycle(phase_tracker, reason="running-transition")

        if upscale_node_id and node_id == upscale_node_id:
            phase_tracker["phase"] = PHASE_UPSCALING
            phase_tracker["prep_ratio"] = 1.0
        elif enhancement_node_id and node_id == enhancement_node_id:
            phase_tracker["phase"] = PHASE_ENHANCEMENT
            phase_tracker["prep_ratio"] = 1.0
            phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
            phase_tracker["seedvr_stage"] = None
            if phase_tracker.get("upscale_total"):
                _maybe_set_enhancement_total_from_upscale(
                    phase_tracker, phase_tracker["upscale_total"]
                )
        elif node_id in wrap_up_node_ids:
            phase_tracker["phase"] = PHASE_WRAP_UP
            phase_tracker["upscale_ratio"] = max(phase_tracker["upscale_ratio"], 1.0)
            phase_tracker["seedvr_stage"] = None
            phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.20)
        else:
            _maybe_promote_wrap_up_from_post_enhancement_node(
                phase_tracker,
                node_id,
                wrap_up_milestones,
                reason="running-node",
            )
        if phase_tracker["phase"] == PHASE_PREPARATION:
            phase_tracker["prep_ratio"] = max(phase_tracker["prep_ratio"], 0.80)


# ---- Live-log formatting helpers ----
def _format_live_log_line(progress_text: str, phase_tracker: dict[str, Any]) -> str | None:
    parsed = COMFY_LOG_PATTERN.match(progress_text)
    if not parsed:
        lower_text = progress_text.lower()
        if lower_text.startswith("still running..."):
            return "Still running..."
        if "fetching execution history" in lower_text:
            return "Preparing final output..."
        if "processing output nodes and collecting images" in lower_text:
            return "Collecting generated images..."
        if "collecting images from node" in lower_text:
            return "Collecting output image..."

        euler_match = EULER_PROGRESS_PATTERN.search(progress_text)
        if euler_match:
            return f"Sampler progress: {euler_match.group('pct')}%"

        running_node = RUNNING_NODE_PATTERN.match(progress_text)
        if running_node:
            label = running_node.group("label").strip()
            if "save image" in label.lower():
                return "Saving final image..."
            if "vae encode" in label.lower() or "vae decode" in label.lower():
                return None
            return f"Running {label}..."

        return progress_text

    comfy_phase = parsed.group("phase").strip().lower()
    phase_message = parsed.group("message").strip()
    enhancement_node_id = phase_tracker.get("enhancement_node_id")
    upscale_node_id = phase_tracker.get("upscale_node_id")
    wrap_up_node_ids = set(phase_tracker.get("wrap_up_node_ids") or [])
    upscale_label = str(phase_tracker.get("upscale_label") or "Upscaling")
    enhancement_label = str(phase_tracker.get("enhancement_label") or "Enhancement")

    if comfy_phase == "ws" and "receive timeout" in phase_message.lower():
        return "Still running..."

    if comfy_phase == "seedvr-frames":
        total_match = TOTAL_VALUE_PATTERN.search(phase_message)
        if total_match:
            total = int(total_match.group("total"))
            return f"{upscale_label}: detected {total} frame(s)."
        return f"{upscale_label}: preparing frames..."

    if comfy_phase == "seedvr-upscale":
        fraction = FRACTION_PATTERN.match(phase_message)
        if fraction:
            idx = int(fraction.group("idx"))
            total = int(fraction.group("total"))
            return f"{upscale_label}: upscaling {idx}/{total}"
        return f"{upscale_label}: running..."

    if comfy_phase in {"seedvr-encode", "seedvr-decode"}:
        return None

    if comfy_phase == "enhance-state":
        state_match = ENHANCE_STATE_PATTERN.match(phase_message)
        if state_match:
            done = int(state_match.group("done"))
            total_raw = state_match.group("total")
            if total_raw and total_raw.isdigit():
                total = int(total_raw)
                return f"{enhancement_label}: {done}/{total}"
            return f"{enhancement_label}: {done} done"
        return f"{enhancement_label}: updating..."

    if comfy_phase == "enhance-item":
        item_match = ENHANCE_ITEM_PATTERN.match(phase_message)
        if item_match:
            done = int(item_match.group("done"))
            total_raw = item_match.group("total")
            if total_raw and total_raw.isdigit():
                total = int(total_raw)
                return f"{enhancement_label}: completed {done}/{total}"
            return f"{enhancement_label}: completed item {done}"
        return f"{enhancement_label}: completed an item."

    if comfy_phase == "enhance-step":
        step_match = ENHANCE_STEP_PATTERN.match(phase_message)
        if step_match:
            item_done = int(step_match.group("item_done"))
            item_total_raw = step_match.group("item_total")
            step_done = int(step_match.group("step_done"))
            step_total = int(step_match.group("step_total"))
            if item_total_raw and item_total_raw.isdigit():
                item_total = int(item_total_raw)
                return (
                    f"{enhancement_label}: item {item_done}/{item_total}, "
                    f"sampling step {step_done}/{step_total}"
                )
            return f"{enhancement_label}: sampling step {step_done}/{step_total}"
        return f"{enhancement_label}: sampling..."

    if comfy_phase == "enhance-sample":
        fraction = FRACTION_PATTERN.match(phase_message)
        if fraction:
            done = int(fraction.group("idx"))
            total = int(fraction.group("total"))
            return f"{enhancement_label}: {done}/{total}"
        return f"{enhancement_label}: sampling..."

    if comfy_phase == "progress":
        node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
        if node_progress:
            node_id = node_progress.group("node")
            done = int(node_progress.group("done"))
            total = int(node_progress.group("total"))

            if upscale_node_id and node_id == upscale_node_id:
                # Keep client logs focused on SeedVR-specific markers to avoid duplicate counters.
                return None

            if enhancement_node_id and node_id == enhancement_node_id:
                enhance_total = phase_tracker.get("enhance_total")
                enhance_done = int(phase_tracker.get("enhance_done") or 0)
                if isinstance(enhance_total, int) and enhance_total > 0:
                    current_item = min(max(1, enhance_done + 1), enhance_total)
                    return (
                        f"{enhancement_label}: item {current_item}/{enhance_total}, "
                        f"sampling step {done}/{total}"
                    )
                return f"{enhancement_label}: sampling step {done}/{total}"

            if node_id in wrap_up_node_ids:
                return f"Finalizing output: {done}/{total}"

    if comfy_phase == "execution" and "finished" in phase_message.lower():
        return "Execution finished. Collecting output..."

    if comfy_phase == "status" and "queue_remaining=0" in phase_message:
        return "Finalizing output..."

    if comfy_phase in {"node", "executed"}:
        return None

    return progress_text


def _apply_live_progress_text(
    progress_text: str,
    current_node: str | None,
    node_step_done: int | None,
    node_step_total: int | None,
    queue_remaining: str | None,
    live_logs: list[str],
    last_log_line: str | None,
    phase_tracker: dict[str, Any],
) -> tuple[str | None, int | None, int | None, str | None, list[str], str | None]:
    upscale_node_id = phase_tracker.get("upscale_node_id")
    enhancement_node_id = phase_tracker.get("enhancement_node_id")
    upscale_label = str(phase_tracker.get("upscale_label") or "Upscaling")
    enhancement_label = str(phase_tracker.get("enhancement_label") or "Enhancement")
    _update_phase_tracker_from_progress_text(progress_text, phase_tracker=phase_tracker)
    if progress_text.startswith("[comfy-log]"):
        parsed = COMFY_LOG_PATTERN.match(progress_text)
        if parsed:
            phase = parsed.group("phase").strip().lower()
            phase_message = parsed.group("message").strip()

            if phase == "node":
                current_node = phase_message
                node_step_done = None
                node_step_total = None
            elif phase == "seedvr-upscale":
                current_node = f"{upscale_node_id or 'upscale'}: {upscale_label}"
            elif phase in {"seedvr-encode", "seedvr-decode"}:
                node_step_done = None
                node_step_total = None
            elif phase == "enhance-node":
                node_match = re.search(r"node=(?P<node>[^ ]+)", phase_message)
                if node_match:
                    enhance_node = node_match.group("node")
                    current_node = f"{enhance_node}: {enhancement_label} sampling"
                    phase_tracker["enhancement_node_id"] = enhance_node
                node_step_done = None
                node_step_total = None
            elif phase == "enhance-state":
                state_match = ENHANCE_STATE_PATTERN.match(phase_message)
                if state_match:
                    enhance_node = state_match.group("node") or enhancement_node_id or "enhance"
                    phase_tracker["enhancement_node_id"] = enhance_node
                    current_node = f"{enhance_node}: {enhancement_label} progress"
                node_step_done = None
                node_step_total = None
            elif phase == "enhance-item":
                item_match = ENHANCE_ITEM_PATTERN.match(phase_message)
                if item_match:
                    enhance_node = item_match.group("node")
                    current_node = f"{enhance_node}: {enhancement_label} item completed"
                    phase_tracker["enhancement_node_id"] = enhance_node
                node_step_done = None
                node_step_total = None
            elif phase == "enhance-step":
                step_match = ENHANCE_STEP_PATTERN.match(phase_message)
                if step_match:
                    enhance_node = step_match.group("node")
                    phase_tracker["enhancement_node_id"] = enhance_node
                    current_node = f"{enhance_node}: {enhancement_label} sampling"
                    node_step_done = int(step_match.group("step_done"))
                    node_step_total = int(step_match.group("step_total"))
            elif phase == "enhance-sample":
                current_node = f"{enhancement_node_id or 'enhance'}: {enhancement_label} sampling"
                fraction = FRACTION_PATTERN.match(phase_message)
                if fraction:
                    node_step_done = int(fraction.group("idx"))
                    node_step_total = int(fraction.group("total"))
                elif phase_message.isdigit():
                    node_step_done = int(phase_message)
                    node_step_total = None
            elif phase == "progress":
                node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
                if node_progress:
                    node_id = node_progress.group("node")
                    done_value = int(node_progress.group("done"))
                    total_value = int(node_progress.group("total"))
                    if (
                        phase_tracker.get("seedvr_runtime_enabled", False)
                        and upscale_node_id
                        and node_id == upscale_node_id
                    ):
                        # Hide generic node-progress for SeedVR node to prevent triplicate counters.
                        node_step_done = None
                        node_step_total = None
                    else:
                        node_step_done = done_value
                        node_step_total = total_value
                        if current_node is None or not current_node.startswith(node_id):
                            current_node = node_id
            elif phase == "status":
                if "queue_remaining=" in phase_message:
                    queue_remaining = phase_message.split("queue_remaining=", 1)[1].strip()
            elif phase == "execution" and "finished" in phase_message.lower():
                current_node = "Execution finished, collecting outputs"
                node_step_done = None
                node_step_total = None
    else:
        running_node = RUNNING_NODE_PATTERN.match(progress_text)
        if running_node:
            current_node = f"{running_node.group('node')}: {running_node.group('label')}"
            node_step_done = None
            node_step_total = None

        queue_match = QUEUE_REMAINING_PATTERN.search(progress_text)
        if queue_match:
            queue_remaining = queue_match.group("remaining").strip()

    display_line = _format_live_log_line(progress_text, phase_tracker=phase_tracker)

    if display_line and display_line != last_log_line:
        live_logs.append(display_line)
        live_logs = live_logs[-12:]
        last_log_line = display_line

    return (
        current_node,
        node_step_done,
        node_step_total,
        queue_remaining,
        live_logs,
        last_log_line,
    )


# ---- Output decoding helper ----
async def _decode_output_image(status: dict[str, Any]) -> Image.Image:
    output = status.get("output") or {}
    if not isinstance(output, dict):
        raise ValueError("Job completed without a valid output payload.")

    if output.get("error"):
        raise ValueError(_extract_error_message(status))

    message = output.get("message")
    if isinstance(message, str):
        message = [message]
    if isinstance(message, list):
        for item in message:
            if not isinstance(item, str):
                continue

            if item.startswith(("http://", "https://")):
                return await _read_url_image(item)

            # Worker can now return raw base64 entries directly in output.message.
            base64_value = item.split(",", 1)[1] if item.startswith("data:") else item
            try:
                decoded = base64.b64decode(base64_value, validate=True)
                return Image.open(io.BytesIO(decoded))
            except (binascii.Error, ValueError):
                continue

    images = output.get("images") or []
    if isinstance(images, list):
        for item in images:
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            img_type = (item.get("type") or "").lower()

            if isinstance(data, str) and (
                img_type in {"s3_url", "url"} or data.startswith(("http://", "https://"))
            ):
                return await _read_url_image(data)

            if isinstance(data, str) and img_type in {"base64", "b64"}:
                if data.startswith("data:"):
                    data = data.split(",", 1)[1]
                return Image.open(io.BytesIO(base64.b64decode(data)))

    raise ValueError("No decodable image found in RunPod output.")

