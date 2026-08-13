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
from typing import Any

from workflow_progress import clamp_ratio

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

# ---- Workflow progress configuration ----
SEEDVR_NODE_ID = "77:78"
ENHANCEMENT_NODE_ID = "80:12"
WRAP_UP_NODE_IDS = {"80:14", "81:38", "81:13", "97"}
DEFAULT_WRAP_UP_MILESTONES = {
    "80:14": 0.25,
    "81:38": 0.40,
    "81:13": 0.55,
    "97": 0.75,
}
# Trace logging stays off for production/client-facing runs; set
# RUNPOD_TRACE_DEBUG=1 to capture per-poll JSONL traces when diagnosing stalls.
RUNPOD_TRACE_DEBUG = os.getenv("RUNPOD_TRACE_DEBUG", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RUNPOD_TRACE_DIR = Path(
    os.getenv("RUNPOD_TRACE_DIR", str(Path(__file__).resolve().parent / "trace_logs"))
)
TRACE_FILENAME_GLOB = "runpod_trace_*.jsonl"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s.", name, raw, default)
        return default


# One trace file per job, so the boot service would otherwise accumulate them
# forever. Mirrors the "keep the latest N" retention the startup supervisor uses
# for its own logs. Set to 0 to keep every trace file.
RUNPOD_TRACE_RETENTION_FILES = _int_env("RUNPOD_TRACE_RETENTION_FILES", 200)
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
                wrap_milestones[str(node_id)] = clamp_ratio(float(ratio))
            except (TypeError, ValueError):
                continue

    if not wrap_milestones and deduped_wrap_nodes:
        count = len(deduped_wrap_nodes)
        for idx, node_id in enumerate(deduped_wrap_nodes, start=1):
            ratio = 0.20 + (idx / count) * 0.70
            wrap_milestones[node_id] = clamp_ratio(ratio)

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
def _prune_trace_files(*, keep: int | None = None) -> list[Path]:
    """Delete all but the newest ``keep`` trace files. Never raises.

    Only files matching TRACE_FILENAME_GLOB directly inside RUNPOD_TRACE_DIR are
    considered, so anything else living in that folder is left untouched.
    """
    limit = RUNPOD_TRACE_RETENTION_FILES if keep is None else keep
    if limit <= 0:
        return []

    removed: list[Path] = []
    try:
        traces: list[tuple[float, Path]] = []
        for path in RUNPOD_TRACE_DIR.glob(TRACE_FILENAME_GLOB):
            try:
                if path.is_file():
                    traces.append((path.stat().st_mtime, path))
            except OSError:
                continue

        if len(traces) <= limit:
            return []

        traces.sort(key=lambda entry: entry[0], reverse=True)
        for _, path in traces[limit:]:
            try:
                path.unlink()
            except OSError as err:
                logger.warning("Could not delete stale trace file %s: %s", path, err)
                continue
            removed.append(path)
    except Exception as err:
        logger.warning("Trace log pruning failed in %s: %s", RUNPOD_TRACE_DIR, err)
        return removed

    if removed:
        logger.info(
            "Pruned %s stale trace file(s) in %s (keeping newest %s).",
            len(removed),
            RUNPOD_TRACE_DIR,
            limit,
        )
    return removed


def _init_trace_file(job_id: str, workflow: str) -> Path | None:
    if not RUNPOD_TRACE_DEBUG:
        return None

    try:
        RUNPOD_TRACE_DIR.mkdir(parents=True, exist_ok=True)
        # Sweep before naming the new file so it is never a deletion candidate.
        _prune_trace_files()
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


def _append_trace_event(
    trace_file: Path | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    if trace_file is None:
        return

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "payload": payload,
    }
    try:
        with open(trace_file, "a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as err:
        logger.warning("Trace write failed (%s): %s", trace_file, err)


# ---- Live-log formatting helpers ----
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
