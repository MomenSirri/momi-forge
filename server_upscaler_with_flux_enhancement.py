from __future__ import annotations

import asyncio
import json
import logging
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

import gradio as gr
import numpy as np
from PIL import Image
from gradio_imageslider import ImageSlider

from auth_service import get_auth_service
from runpod_api_class import RunpodAPI
from task_tracking import TaskTracker, WorkflowContext, extract_artifacts_from_status
from utils import (
    PHASE_COMPLETED,
    PHASE_PREPARATION,
    PHASE_WRAP_UP,
    _append_trace_event,
    _apply_live_progress_text,
    _compute_overall_percent,
    _decode_output_image,
    _extract_error_message,
    _extract_progress_signal,
    _has_final_output_payload,
    _init_trace_file,
    _phase_trace_snapshot,
    _render_idle_status,
    _render_live_status,
    _resolve_workflow_path,
    _resolve_workflow_profile,
    _status_trace_snapshot,
    _stream_trace_snapshot,
    _extract_stream_progress_signals,
    _to_pil_image,
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
WORKFLOW_NAME = os.getenv("MOMI_WORKFLOW_NAME", "Pro Upscaler")
APP_DEBUG = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_QUIET = os.getenv("APP_QUIET", "1").strip().lower() in {"1", "true", "yes", "on"}

TERMINAL_FAILURES = {"FAILED", "ERROR", "TIMED_OUT"}
ACTIVE_STATES = {"IN_QUEUE", "IN_PROGRESS", "RUNNING"}
MAX_STATUS_POLLS = int(os.getenv("RUNPOD_MAX_STATUS_POLLS", "1800"))
FINALIZATION_HINT_GRACE_POLLS = int(
    os.getenv("RUNPOD_FINALIZATION_HINT_GRACE_POLLS", "120")
)
MAX_CONSECUTIVE_STATUS_ERRORS = int(
    os.getenv("RUNPOD_MAX_CONSECUTIVE_STATUS_ERRORS", "8")
)
RUNPOD_STATUS_POLL_INTERVAL_S = max(
    0.1,
    float(os.getenv("RUNPOD_STATUS_POLL_INTERVAL_S", "0.4")),
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
RUNPOD_STREAM_MAX_SEEN_CHUNKS = max(
    200,
    int(os.getenv("RUNPOD_STREAM_MAX_SEEN_CHUNKS", "3000")),
)
auth_service = get_auth_service()
APP_ENVIRONMENT = os.getenv("APP_ENVIRONMENT", "seed")
WORKFLOW_VERSION = os.getenv("WORKFLOW_VERSION_5K", "unknown")
WORKFLOW_CATEGORY = os.getenv("WORKFLOW_CATEGORY_5K", "upscaling")
WORKFLOW_TYPE = os.getenv("WORKFLOW_TYPE_5K", "image")
WORKFLOW_DEBUG_JSON_DIR = Path(
    os.getenv(
        "WORKFLOW_DEBUG_JSON_DIR",
        str(Path(__file__).resolve().parent / "trace_logs" / "workflow_debug"),
    )
)
SEEDVR_TILE_DIVISOR_DEFAULT = 900
SEEDVR_PREP_MAX_WIDTH_DEFAULT = 12800
SEEDVR_PREP_MAX_HEIGHT_DEFAULT = 12800
SEEDVR_TILE_INPUT_MAX_WIDTH_DEFAULT = 10240
SEEDVR_TILE_INPUT_MAX_HEIGHT_DEFAULT = 10240

BOTTOM_PROGRESS_LAYOUT_CSS = """
.bottom-progress-row {
  margin-top: 12px;
  margin-bottom: 12px;
}

.bottom-progress-row > div {
  width: 100%;
}
"""


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
    debug_path = WORKFLOW_DEBUG_JSON_DIR / f"upscaler_{safe_workflow}_{task_id}_{timestamp}.json"
    with open(debug_path, "w", encoding="utf-8") as outfile:
        json.dump(workflow_payload, outfile, indent=2)
    return debug_path


def _resize_keep_ratio_cap(
    width: int,
    height: int,
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    safe_width = max(int(width or 0), 1)
    safe_height = max(int(height or 0), 1)
    safe_max_width = max(int(max_width or 0), 1)
    safe_max_height = max(int(max_height or 0), 1)

    scale = min(
        safe_max_width / safe_width,
        safe_max_height / safe_height,
        1.0,
    )
    if scale >= 1.0:
        return safe_width, safe_height

    resized_width = max(1, int(round(safe_width * scale)))
    resized_height = max(1, int(round(safe_height * scale)))
    return resized_width, resized_height


def _estimate_seedvr_tile_workload(
    *,
    prompt: dict[str, Any],
    input_width: int,
    input_height: int,
    engine_choice: str,
    upscale_value: str,
) -> dict[str, int | str | None]:
    if str(engine_choice or "").strip().lower() == "super fast":
        return {
            "estimated_tile_columns": None,
            "estimated_tile_rows": None,
            "estimated_tile_count": None,
            "estimated_tile_source_width": None,
            "estimated_tile_source_height": None,
            "estimated_tile_divisor": None,
            "estimated_tile_note": "SeedVR tiled estimate is only shown for non-Super-Fast mode.",
        }

    def _read_int(node_id: str, key: str, fallback: int) -> int:
        try:
            value = int(prompt[node_id]["inputs"][key])
            return value if value > 0 else fallback
        except Exception:
            return fallback

    tile_divisor = _read_int("96:96", "value", SEEDVR_TILE_DIVISOR_DEFAULT)
    prep_max_width = _read_int("96:82", "width", SEEDVR_PREP_MAX_WIDTH_DEFAULT)
    prep_max_height = _read_int("96:82", "height", SEEDVR_PREP_MAX_HEIGHT_DEFAULT)
    tile_input_max_width = _read_int("96:89", "width", SEEDVR_TILE_INPUT_MAX_WIDTH_DEFAULT)
    tile_input_max_height = _read_int("96:89", "height", SEEDVR_TILE_INPUT_MAX_HEIGHT_DEFAULT)

    scale_by = 2 if str(upscale_value) == "x2" else 4
    prep_width, prep_height = _resize_keep_ratio_cap(
        input_width,
        input_height,
        max_width=prep_max_width,
        max_height=prep_max_height,
    )
    scaled_width = max(1, int(round(prep_width * scale_by)))
    scaled_height = max(1, int(round(prep_height * scale_by)))
    tile_source_width, tile_source_height = _resize_keep_ratio_cap(
        scaled_width,
        scaled_height,
        max_width=tile_input_max_width,
        max_height=tile_input_max_height,
    )

    columns = max(1, int(round(tile_source_width / tile_divisor)))
    rows = max(1, int(round(tile_source_height / tile_divisor)))
    tile_count = columns * rows

    return {
        "estimated_tile_columns": columns,
        "estimated_tile_rows": rows,
        "estimated_tile_count": tile_count,
        "estimated_tile_source_width": tile_source_width,
        "estimated_tile_source_height": tile_source_height,
        "estimated_tile_divisor": tile_divisor,
        "estimated_tile_note": None,
    }


async def fivek_generator(
    image: Any,
    engine_choice: str,
    enhancement: bool,
    upscale_value: str,
    flux_creativity_tilet: float,
    workflow_debug: bool,
    job_state: str | None,
    workflow: str,
    request: gr.Request,
):
    del job_state
    logger.info("Workflow %s called", workflow)

    user_email = getattr(request, "username", None)
    if not user_email:
        yield gr.update(), "❌ Authentication required. Please sign in again.", None
        return

    identity = auth_service.get_identity(user_email)
    is_admin_user = str(getattr(identity, "role", "") or "").strip().lower() == "admin"
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    source_page = "/tab/5k-upscaler-flux"

    try:
        prompt_path = _resolve_workflow_path()
        with open(prompt_path, "r", encoding="utf-8") as fh:
            prompt: dict[str, Any] = json.load(fh)
    except UnicodeDecodeError:
        with open(prompt_path, "r", encoding="cp1252") as fh:
            prompt = json.load(fh)
    except Exception as err:
        yield gr.update(), f"❌ Prompt load failed: {err}", None
        return

    try:
        input_pil = _to_pil_image(image)
        if input_pil.mode not in ("RGB", "RGBA"):
            input_pil = input_pil.convert("RGB")
        input_array = np.array(input_pil)
        image_base64 = save_input_image_as_base64(input_array)
    except Exception as err:
        yield gr.update(), f"❌ Input image error: {err}", None
        return

    feature_flags = {
        "enhancement_enabled": bool(enhancement),
        "engine_choice": engine_choice,
        "upscale_value": upscale_value,
    }
    settings_snapshot = {
        "flux_creativity_tilet": float(flux_creativity_tilet),
        "upscale_value": upscale_value,
        "engine_choice": engine_choice,
        "enhancement": bool(enhancement),
    }
    task_id = str(uuid.uuid4())
    workflow_context = WorkflowContext(
        key=str(workflow or WORKFLOW_NAME),
        name=str(workflow or WORKFLOW_NAME),
        version=WORKFLOW_VERSION,
        category=WORKFLOW_CATEGORY,
        workflow_type=WORKFLOW_TYPE,
    )
    tracker = TaskTracker(
        store=None,
        task_id=task_id,
        user_email=identity.email,
        user_prefix=identity.username_prefix,
        user_display_name=identity.display_name,
        user_role=identity.role,
        avatar_filename=identity.avatar_filename,
        workflow=workflow_context,
        source_page=source_page,
        browser_user_agent=user_agent,
        session_id=session_id,
        environment_name=APP_ENVIRONMENT,
        feature_flags=feature_flags,
        settings=settings_snapshot,
        input_meta={
            "width": int(input_pil.width),
            "height": int(input_pil.height),
            "resolution": f"{int(input_pil.width)}x{int(input_pil.height)}",
            "format": str(input_pil.mode),
        },
        request_summary={
            "workflow_profile_name": workflow,
            "engine_choice": engine_choice,
            "enhancement": bool(enhancement),
            "upscale_value": upscale_value,
        },
        prompt_type="image_upscale",
        created_by=identity.email,
    )

    main_image_name = "main_image_name"

    try:
        prompt["99"]["inputs"]["image"] = main_image_name
        prompt["80:29"]["inputs"]["noise_seed"] = random.randint(0, 999_999_999_999)
        prompt["80:84"]["inputs"]["value"] = float(flux_creativity_tilet)

        if engine_choice == "Super Fast":
            prompt["102"]["inputs"]["image"] = ["99", 0]
            prompt["97"]["inputs"]["images"] = ["104", 0]
            prompt["104"]["inputs"]["scale_by"] = 0.5 if upscale_value == "x2" else 1
        else:
            prompt["96:82"]["inputs"]["image"] = ["99", 0]
            prompt["97"]["inputs"]["images"] = ["81:13", 0]
            prompt["96:85"]["inputs"]["scale_by"] = 2 if upscale_value == "x2" else 4

            if enhancement:
                prompt["81:38"]["inputs"]["image"] = ["80:14", 0]
                prompt["80:83"]["inputs"]["image"] = ["77:78", 0]
            else:
                prompt["81:38"]["inputs"]["image"] = ["77:78", 0]
    except KeyError as err:
        tracker.fail(
            failure_reason="workflow_key_missing",
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield gr.update(), f"❌ Workflow key missing: {err}", None
        return
    except Exception as err:
        tracker.fail(
            failure_reason="workflow_update_error",
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield gr.update(), f"❌ Workflow update failed: {err}", None
        return

    seedvr_tile_estimate = _estimate_seedvr_tile_workload(
        prompt=prompt,
        input_width=int(input_pil.width),
        input_height=int(input_pil.height),
        engine_choice=engine_choice,
        upscale_value=upscale_value,
    )

    final_json = prepare_json(prompt, [{"name": main_image_name, "image": image_base64}])
    workflow_debug_path: Path | None = None

    should_save_debug_json = bool(
        os.getenv("SAVE_DEBUG_PROMPT_JSON", "0") == "1" or (workflow_debug and is_admin_user)
    )
    if should_save_debug_json:
        try:
            workflow_debug_path = _save_workflow_debug_json(
                final_json,
                workflow_name=str(workflow or WORKFLOW_NAME),
                task_id=task_id,
            )
            logger.info("Saved ComfyUI workflow JSON: %s", workflow_debug_path)
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)

    workflow_profile = _resolve_workflow_profile(workflow)
    logger.info(
        "Using workflow profile '%s' (upscale_node=%s, enhancement_node=%s, wrap_nodes=%s)",
        workflow_profile.get("name"),
        workflow_profile.get("upscale_node_id"),
        workflow_profile.get("enhancement_node_id"),
        workflow_profile.get("wrap_up_node_ids"),
    )

    api = RunpodAPI(environment='seed')

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
        yield gr.update(), f"❌ Job submission failed: {err}", None
        return

    task_url = f"{api.base_url}/status/{job_id}"
    tracker.attach_request(
        request_id=job_id,
        task_url=task_url,
        retry_count=0,
    )

    trace_file = _init_trace_file(job_id=job_id, workflow=workflow)
    _append_trace_event(
        trace_file,
        "job_submitted",
        {
            "job_id": job_id,
            "workflow": workflow,
            "workflow_profile": workflow_profile,
            "engine_choice": engine_choice,
            "enhancement": enhancement,
            "upscale_value": upscale_value,
            "trace_file": str(trace_file) if trace_file else None,
        },
    )

    if workflow_debug_path is not None:
        yield gr.update(), f"🚀 Job submitted…\n\nComfyUI workflow JSON: `{workflow_debug_path}`", job_id
    elif trace_file is not None:
        yield (
            gr.update(),
            f"🚀 Job submitted…\n\nDebug trace file: `{trace_file}`",
            job_id,
        )
    else:
        yield gr.update(), "🚀 Job submitted…", job_id

    live_logs: list[str] = []
    last_log_line: str | None = None
    completion_hint_seen_at: int | None = None
    current_node: str | None = None
    node_step_done: int | None = None
    node_step_total: int | None = None
    queue_remaining: str | None = None
    phase_tracker: dict[str, Any] = {
        "phase": PHASE_PREPARATION,
        "prep_ratio": 0.05,
        "upscale_done": 0,
        "upscale_total": None,
        "seedvr_frames_total": None,
        "upscale_ratio": 0.0,
        "seedvr_stage": None,
        "enhance_done": 0,
        "enhance_total": workflow_profile.get("enhancement_total_override"),
        "enhance_ratio": 0.0,
        "wrap_ratio": 0.0,
        "enhance_cycle_complete": False,
        "enhance_last_step": None,
        "enhance_last_total_steps": None,
        "enhance_peak_step": 0,
        "enhance_runtime_seen": False,
        "enhance_item_seen": False,
        "enhance_log_pass": 1,
        "enhance_log_last_step": None,
        "enhance_log_last_total": None,
        "upscale_node_id": workflow_profile.get("upscale_node_id"),
        "enhancement_node_id": workflow_profile.get("enhancement_node_id"),
        "wrap_up_node_ids": workflow_profile.get("wrap_up_node_ids", []),
        "wrap_up_milestones": workflow_profile.get("wrap_up_milestones", {}),
        "seedvr_runtime_enabled": workflow_profile.get("seedvr_runtime_enabled", False),
        "upscale_label": workflow_profile.get("upscale_label", "Upscaling"),
        "enhancement_label": workflow_profile.get("enhancement_label", "Enhancement"),
        "enhancement_total_from_upscale": workflow_profile.get(
            "enhancement_total_from_upscale", True
        ),
        "enhancement_total_override": workflow_profile.get(
            "enhancement_total_override"
        ),
        "estimated_tile_columns": seedvr_tile_estimate.get("estimated_tile_columns"),
        "estimated_tile_rows": seedvr_tile_estimate.get("estimated_tile_rows"),
        "estimated_tile_count": seedvr_tile_estimate.get("estimated_tile_count"),
        "estimated_tile_source_width": seedvr_tile_estimate.get("estimated_tile_source_width"),
        "estimated_tile_source_height": seedvr_tile_estimate.get("estimated_tile_source_height"),
        "estimated_tile_divisor": seedvr_tile_estimate.get("estimated_tile_divisor"),
        "estimated_tile_note": seedvr_tile_estimate.get("estimated_tile_note"),
    }
    last_overall_percent = 0
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
                    _append_trace_event(
                        trace_file,
                        "stream_poll",
                        {"poll_idx": poll_idx, "snapshot": _stream_trace_snapshot(stream_response)},
                    )
                    (
                        stream_progress_entries,
                        stream_state,
                    ) = _extract_stream_progress_signals(
                        stream_response,
                        seen_signatures=stream_seen_signatures,
                        seen_order=stream_seen_order,
                    )
                    if stream_progress_entries:
                        _append_trace_event(
                            trace_file,
                            "stream_progress_batch",
                            {
                                "poll_idx": poll_idx,
                                "entries": len(stream_progress_entries),
                                "tail": [entry[1] for entry in stream_progress_entries[-3:]],
                            },
                        )
                except Exception as err:
                    _append_trace_event(
                        trace_file,
                        "stream_poll_error",
                        {"poll_idx": poll_idx, "error": str(err)},
                    )
                finally:
                    stream_task = None

            if stream_task is None:
                stream_task = asyncio.create_task(api.stream(job_id))

        try:
            status = await api.status(job_id)
        except Exception as err:
            consecutive_status_errors += 1
            _append_trace_event(
                trace_file,
                "status_poll_error",
                {
                    "poll_idx": poll_idx,
                    "consecutive_errors": consecutive_status_errors,
                    "error": str(err),
                },
            )
            if consecutive_status_errors > MAX_CONSECUTIVE_STATUS_ERRORS:
                _append_trace_event(
                    trace_file,
                    "status_poll_error_terminal",
                    {"poll_idx": poll_idx, "error": str(err)},
                )
                tracker.fail(
                    failure_reason="status_poll_error",
                    error_message=str(err),
                    failure_stage="status_poll",
                    progress_percent=last_overall_percent,
                    worker_id=None,
                    metadata={
                        "poll_idx": poll_idx,
                        "consecutive_errors": consecutive_status_errors,
                    },
                )
                yield gr.update(), f"❌ Failed to check job status: {err}", None
                _cancel_stream_task()
                return

            yield (
                gr.update(),
                (
                    "⏳ Temporary connection issue while checking RunPod status.\n\n"
                    f"Retrying automatically ({consecutive_status_errors}/{MAX_CONSECUTIVE_STATUS_ERRORS}).\n\n"
                    f"`{err}`"
                ),
                job_id,
            )
            await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
            continue

        consecutive_status_errors = 0
        _append_trace_event(
            trace_file,
            "status_poll",
            {"poll_idx": poll_idx, "snapshot": _status_trace_snapshot(status)},
        )

        state = (status.get("status") or stream_state or "").upper()
        has_final_output = _has_final_output_payload(status)

        if state in ACTIVE_STATES and tracker.started_dt is None:
            tracker.mark_started(message="Execution started. Waiting for ComfyUI node updates...")

        if state == "CANCELLED":
            _append_trace_event(
                trace_file, "terminal_cancelled", {"poll_idx": poll_idx}
            )
            tracker.fail(
                failure_reason="cancelled",
                error_message="Job cancelled by user or worker.",
                failure_stage=str(phase_tracker.get("phase") or "processing"),
                progress_percent=last_overall_percent,
                worker_id=status.get("workerId"),
                status="cancelled",
            )
            yield gr.update(), "⚠️ Job cancelled.", None
            _cancel_stream_task()
            return

        if state in TERMINAL_FAILURES:
            error_message = _extract_error_message(status)
            _append_trace_event(
                trace_file,
                "terminal_failure",
                {
                    "poll_idx": poll_idx,
                    "state": state,
                    "error": error_message,
                },
            )
            tracker.fail(
                failure_reason=f"runpod_{state.lower()}",
                error_message=error_message,
                failure_stage=str(phase_tracker.get("phase") or "processing"),
                progress_percent=last_overall_percent,
                worker_id=status.get("workerId"),
                status="failed",
                metadata={"runpod_state": state},
            )
            yield gr.update(), f"❌ {error_message}", None
            _cancel_stream_task()
            return

        if state == "COMPLETED" or has_final_output:
            tracker.mark_stage(
                status="output_collecting",
                stage="output_collecting",
                message="ComfyUI execution finished. Collecting outputs...",
                progress_percent=max(last_overall_percent, 92),
            )
            try:
                result_image = await _decode_output_image(status)
                if result_image.mode not in ("RGB", "RGBA"):
                    result_image = result_image.convert("RGBA")

                tmp_dir = Path(tempfile.gettempdir())
                left_path = tmp_dir / f"{job_id}_left.png"
                right_path = tmp_dir / f"{job_id}_right.png"

                input_pil.save(left_path, "PNG")
                result_image.save(right_path, "PNG")
                tracker.mark_stage(
                    status="uploading",
                    stage="uploading",
                    message="Saving result artifacts...",
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

                phase_tracker["phase"] = PHASE_COMPLETED
                phase_tracker["wrap_ratio"] = 1.0
                enhance_total = phase_tracker.get("enhance_total")
                if isinstance(enhance_total, int) and enhance_total > 0:
                    phase_tracker["enhance_done"] = max(
                        int(phase_tracker.get("enhance_done") or 0),
                        enhance_total,
                    )
                    phase_tracker["enhance_ratio"] = 1.0
                _append_trace_event(
                    trace_file,
                    "terminal_success",
                    {
                        "poll_idx": poll_idx,
                        "state": state,
                        "phase_tracker": _phase_trace_snapshot(phase_tracker),
                        "result_left": str(left_path),
                        "result_right": str(right_path),
                    },
                )
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
                yield (str(left_path), str(right_path)), "✅ Done!", None
                _cancel_stream_task()
                return
            except Exception as err:
                # RunPod can briefly lag between progress text and final payload materialization.
                if has_final_output and state != "COMPLETED":
                    _append_trace_event(
                        trace_file,
                        "final_payload_lag",
                        {"poll_idx": poll_idx, "error": str(err), "state": state},
                    )
                    phase_tracker["phase"] = PHASE_WRAP_UP
                    phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.92)
                    tracker.emit_processing(
                        stage="output_collecting",
                        message="Finalizing output payload...",
                        progress_percent=max(last_overall_percent, 92),
                    )
                    yield gr.update(), "⏳ Finalizing output…", job_id
                    await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
                    continue
                _append_trace_event(
                    trace_file,
                    "decode_failure",
                    {
                        "poll_idx": poll_idx,
                        "error": str(err),
                        "snapshot": _status_trace_snapshot(status),
                    },
                )
                tracker.fail(
                    failure_reason="decode_error",
                    error_message=str(err),
                    failure_stage="output_collecting",
                    progress_percent=last_overall_percent,
                    worker_id=status.get("workerId"),
                )
                yield gr.update(), f"❌ Failed to decode image: {err}", None
                _cancel_stream_task()
                return

        fallback = state.lower().replace("_", " ") if state in ACTIVE_STATES else "processing"
        (
            status_runpod_progress,
            status_progress_text,
            status_hint_texts,
        ) = _extract_progress_signal(status)

        progress_events: list[tuple[str, int | float | None, str, list[str]]] = []
        for stream_progress, stream_text, stream_hints in stream_progress_entries:
            progress_events.append(("stream", stream_progress, stream_text, stream_hints))
        if status_progress_text:
            progress_events.append(
                ("status", status_runpod_progress, status_progress_text, status_hint_texts)
            )

        effective_runpod_progress = status_runpod_progress
        if effective_runpod_progress is None:
            for _, stream_progress, _, _ in reversed(progress_events):
                if isinstance(stream_progress, (int, float)):
                    effective_runpod_progress = stream_progress
                    break

        if progress_events:
            seen_progress_texts: set[str] = set()
            for (
                progress_source,
                event_runpod_progress,
                progress_text,
                hint_texts,
            ) in progress_events:
                if progress_text in seen_progress_texts:
                    continue
                seen_progress_texts.add(progress_text)

                _append_trace_event(
                    trace_file,
                    "progress_signal",
                    {
                        "poll_idx": poll_idx,
                        "source": progress_source,
                        "state": state,
                        "runpod_progress": event_runpod_progress,
                        "progress_text": progress_text,
                        "hint_tail": hint_texts[-3:],
                    },
                )

                if any("Job completed. Returning" in text for text in hint_texts):
                    if completion_hint_seen_at is None:
                        completion_hint_seen_at = poll_idx

                (
                    current_node,
                    node_step_done,
                    node_step_total,
                    queue_remaining,
                    live_logs,
                    last_log_line,
                ) = _apply_live_progress_text(
                    progress_text=progress_text,
                    current_node=current_node,
                    node_step_done=node_step_done,
                    node_step_total=node_step_total,
                    queue_remaining=queue_remaining,
                    live_logs=live_logs,
                    last_log_line=last_log_line,
                    phase_tracker=phase_tracker,
                )
                overall_percent = max(
                    last_overall_percent, _compute_overall_percent(phase_tracker)
                )
                last_overall_percent = overall_percent
                status_md = _render_live_status(
                    fallback=fallback,
                    runpod_progress=(
                        event_runpod_progress
                        if isinstance(event_runpod_progress, (int, float))
                        else effective_runpod_progress
                    ),
                    current_node=current_node,
                    node_step_done=node_step_done,
                    node_step_total=node_step_total,
                    queue_remaining=queue_remaining,
                    logs=live_logs,
                    phase_tracker=phase_tracker,
                    overall_percent=overall_percent,
                )
                _append_trace_event(
                    trace_file,
                    "phase_update",
                    {
                        "poll_idx": poll_idx,
                        "source": progress_source,
                        "overall_percent": overall_percent,
                        "current_node": current_node,
                        "node_step_done": node_step_done,
                        "node_step_total": node_step_total,
                        "queue_remaining": queue_remaining,
                        "phase_tracker": _phase_trace_snapshot(phase_tracker),
                        "selected_progress_text": progress_text,
                    },
                )
                stage_name = str(phase_tracker.get("phase") or "processing").lower().replace(" ", "_")
                progress_message = current_node or progress_text or fallback
                node_id = None
                if current_node:
                    node_id = current_node.split(" ", 1)[0]
                tracker.emit_processing(
                    stage=stage_name,
                    message=progress_message,
                    progress_percent=overall_percent,
                    node_id=node_id,
                    metadata={
                        "queue_remaining": queue_remaining,
                        "runpod_state": state,
                        "progress_source": progress_source,
                    },
                )
                yield gr.update(), status_md, job_id
        else:
            if any("Job completed. Returning" in text for text in status_hint_texts):
                if completion_hint_seen_at is None:
                    completion_hint_seen_at = poll_idx

            if completion_hint_seen_at is not None:
                phase_tracker["phase"] = PHASE_WRAP_UP
                phase_tracker["wrap_ratio"] = max(phase_tracker["wrap_ratio"], 0.92)
                overall_percent = max(
                    last_overall_percent, _compute_overall_percent(phase_tracker)
                )
                last_overall_percent = overall_percent
                status_md = _render_live_status(
                    fallback="finalizing output",
                    runpod_progress=effective_runpod_progress,
                    current_node=current_node,
                    node_step_done=node_step_done,
                    node_step_total=node_step_total,
                    queue_remaining=queue_remaining,
                    logs=live_logs,
                    phase_tracker=phase_tracker,
                    overall_percent=overall_percent,
                )
                _append_trace_event(
                    trace_file,
                    "phase_update_no_progress_text",
                    {
                        "poll_idx": poll_idx,
                        "overall_percent": overall_percent,
                        "reason": "completion_hint_seen",
                        "phase_tracker": _phase_trace_snapshot(phase_tracker),
                    },
                )
                tracker.emit_processing(
                    stage="wrap_up",
                    message="Finalizing output...",
                    progress_percent=overall_percent,
                    metadata={"runpod_state": state, "reason": "completion_hint_seen"},
                )
                yield gr.update(), status_md, job_id
            else:
                overall_percent = max(
                    last_overall_percent, _compute_overall_percent(phase_tracker)
                )
                last_overall_percent = overall_percent
                status_md = _render_live_status(
                    fallback=fallback,
                    runpod_progress=effective_runpod_progress,
                    current_node=current_node,
                    node_step_done=node_step_done,
                    node_step_total=node_step_total,
                    queue_remaining=queue_remaining,
                    logs=live_logs,
                    phase_tracker=phase_tracker,
                    overall_percent=overall_percent,
                )
                _append_trace_event(
                    trace_file,
                    "phase_update_no_progress_text",
                    {
                        "poll_idx": poll_idx,
                        "overall_percent": overall_percent,
                        "reason": "no_progress_text",
                        "phase_tracker": _phase_trace_snapshot(phase_tracker),
                    },
                )
                stage_name = str(phase_tracker.get("phase") or "processing").lower().replace(" ", "_")
                tracker.emit_processing(
                    stage=stage_name,
                    message=current_node or fallback,
                    progress_percent=overall_percent,
                    node_id=(current_node.split(" ", 1)[0] if current_node else None),
                    metadata={"runpod_state": state, "reason": "no_progress_text"},
                )
                yield gr.update(), status_md, job_id

        if (
            completion_hint_seen_at is not None
            and poll_idx - completion_hint_seen_at >= FINALIZATION_HINT_GRACE_POLLS
        ):
            _append_trace_event(
                trace_file,
                "completion_hint_timeout",
                {
                    "poll_idx": poll_idx,
                    "grace_polls": FINALIZATION_HINT_GRACE_POLLS,
                    "phase_tracker": _phase_trace_snapshot(phase_tracker),
                },
            )
            tracker.fail(
                failure_reason="status_lag_timeout",
                error_message="RunPod stayed IN_PROGRESS after completion hint.",
                failure_stage="wrap_up",
                progress_percent=last_overall_percent,
                worker_id=status.get("workerId"),
            )
            yield (
                gr.update(),
                "❌ RunPod stayed IN_PROGRESS after completion hint. Please retry or check endpoint status lag.",
                None,
            )
            _cancel_stream_task()
            return
        await asyncio.sleep(RUNPOD_STATUS_POLL_INTERVAL_S)

    _append_trace_event(
        trace_file,
        "polling_timeout",
        {"max_status_polls": MAX_STATUS_POLLS, "phase_tracker": _phase_trace_snapshot(phase_tracker)},
    )
    tracker.fail(
        failure_reason="polling_timeout",
        error_message="Timed out waiting for RunPod completion status.",
        failure_stage=str(phase_tracker.get("phase") or "processing"),
        progress_percent=last_overall_percent,
        worker_id=None,
    )
    yield gr.update(), "❌ Timed out waiting for RunPod completion status.", None
    _cancel_stream_task()


async def cancel_job(job_id: str | None) -> str:
    if not job_id:
        return "No active job to cancel."

    api = RunpodAPI(environment='seed')
    try:
        await api.cancel(job_id)
        return "⚠️ Cancellation requested."
    except Exception as err:
        logger.error("Cancel failed: %s", err)
        return f"❌ Cancel failed: {err}"


def _disable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=False)


def _enable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=True)


with gr.Blocks(title=APP_TITLE, css=BOTTOM_PROGRESS_LAYOUT_CSS) as fivek:
    gr.Markdown("## Momi Pro Upscaler")

    with gr.Row(variant="panel"):
        image_input = gr.Image(label="Input Image")
        image_output = ImageSlider(label="Result", type="filepath")

    with gr.Row():
        engine_choice = gr.Dropdown(
            choices=["Super Fast", "Normal"],
            label="Engine Choice",
            value="Normal",
            scale=1,
        )
        upscale_value = gr.Radio(
            choices=["x2", "x4"],
            label="Upscale Value",
            value="x2",
        )
        enhancement_toggle = gr.Checkbox(label="Enhancement", value=True, scale=1)
        workflow_debug_checkbox = gr.Checkbox(
            label="Workflow Debug (Admin only)",
            value=False,
            visible=False,
            info="Save the final manipulated workflow JSON sent to RunPod.",
            scale=1,
        )

    flux_creativity_tilet = gr.Slider(
        minimum=10,
        maximum=40,
        step=5,
        value=30,
        label="Creativity",
    )

    job_id_state = gr.State(None)

    with gr.Row(elem_classes=["bottom-progress-row"]):
        progress_panel = gr.HTML(_render_idle_status())

    with gr.Row(elem_classes=["bottom-action-row"]):
        enhance_btn = gr.Button("🌟 Generate", scale=3, variant="primary")
        cancel_btn = gr.Button("Cancel", variant="stop", scale=1)

    workflow_name = gr.State(WORKFLOW_NAME)

    def on_engine_change(engine: str):
        return gr.update(visible=engine != "Super Fast", value=engine != "Super Fast")

    engine_choice.change(fn=on_engine_change, inputs=engine_choice, outputs=enhancement_toggle)

    generate_event = enhance_btn.click(
        fn=_disable_generate_button,
        inputs=None,
        outputs=[enhance_btn],
        queue=False,
    )

    generate_event = generate_event.then(
        fn=fivek_generator,
        inputs=[
            image_input,
            engine_choice,
            enhancement_toggle,
            upscale_value,
            flux_creativity_tilet,
            workflow_debug_checkbox,
            job_id_state,
            workflow_name,
        ],
        outputs=[image_output, progress_panel, job_id_state],
        concurrency_limit=10,
        trigger_mode="once",
    )

    generate_event.then(
        fn=_enable_generate_button,
        inputs=None,
        outputs=[enhance_btn],
        queue=False,
    )

    cancel_btn.click(cancel_job, inputs=job_id_state, outputs=progress_panel).then(
        fn=_enable_generate_button,
        inputs=None,
        outputs=[enhance_btn],
        queue=False,
    )
    fivek.load(
        fn=_debug_checkbox_visibility_update,
        inputs=None,
        outputs=[workflow_debug_checkbox],
    )
if __name__ == "__main__":
    fivek.launch(
        server_name="0.0.0.0",
        server_port=8170,
        debug=APP_DEBUG,
        quiet=APP_QUIET,
        auth=auth_service.authenticate,
        auth_message="BrickVisual internal access only.",
    )
