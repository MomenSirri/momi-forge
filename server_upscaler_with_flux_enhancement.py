from __future__ import annotations

import asyncio
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
    PHASE_COMPLETED,
    PHASE_WRAP_UP,
    ProgressTracker,
)
from utils import (
    _append_trace_event,
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
        prefix="upscaler",
    )


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


class UpscalerPreparationError(RuntimeError):
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


class UpscalerRequestError(RuntimeError):
    pass


@dataclass
class UpscalerPreparedInputs:
    prompt: dict[str, Any]
    input_pil: Image.Image
    image_base64: str
    task_id: str
    workflow_key: str
    feature_flags: dict[str, Any]
    settings_snapshot: dict[str, Any]


@dataclass
class UpscalerPreparedJob:
    inputs: UpscalerPreparedInputs
    payload: dict[str, Any]
    workflow_debug_path: Path | None
    workflow_profile: dict[str, Any]
    tile_estimate: dict[str, int | str | None]


@dataclass
class UpscalerRequestContext:
    inputs: UpscalerPreparedInputs
    job: UpscalerPreparedJob
    tracker: TaskTracker


@dataclass
class UpscalerSubmissionResult:
    job_id: str | None
    error_message: str | None = None
    uncertain: bool = False


@dataclass
class UpscalerFinalizedOutput:
    result_image: Image.Image | None = None
    left_path: Path | None = None
    right_path: Path | None = None
    artifacts: dict[str, Any] | None = None
    error_message: str | None = None


@dataclass
class UpscalerPollEvent:
    kind: str
    status: dict[str, Any]
    message: str
    progress_percent: int
    stage: str
    poll_idx: int
    status_md: str | None = None
    node_id: str | None = None
    progress_source: str | None = None
    finalized: UpscalerFinalizedOutput | None = None
    tracker_error_message: str | None = None


@dataclass
class UpscalerPollState:
    phase_tracker: ProgressTracker
    trace_file: Path | None
    live_logs: list[str] = field(default_factory=list)
    last_log_line: str | None = None
    completion_hint_seen_at: int | None = None
    current_node: str | None = None
    node_step_done: int | None = None
    node_step_total: int | None = None
    queue_remaining: str | None = None
    last_overall_percent: int = 0
    consecutive_status_errors: int = 0
    stream_seen_signatures: set[str] = field(default_factory=set)
    stream_seen_order: list[str] = field(default_factory=list)
    stream_task: asyncio.Task[dict[str, Any]] | None = None

    def cancel_stream(self) -> None:
        if self.stream_task is not None and not self.stream_task.done():
            self.stream_task.cancel()


def _prepare_upscaler_inputs(
    *,
    image: Any,
    engine_choice: str,
    enhancement: bool,
    upscale_value: str,
    flux_creativity_tilet: float,
    workflow: str,
) -> UpscalerPreparedInputs:
    try:
        prompt_path = _resolve_workflow_path()
        try:
            with open(prompt_path, "r", encoding="utf-8") as file:
                prompt: dict[str, Any] = json.load(file)
        except UnicodeDecodeError:
            with open(prompt_path, "r", encoding="cp1252") as file:
                prompt = json.load(file)
    except Exception as err:
        raise UpscalerPreparationError(
            "Prompt load failed",
            str(err),
            failure_reason="workflow_load_error",
        ) from err

    try:
        input_pil = _to_pil_image(image)
        if input_pil.mode not in ("RGB", "RGBA"):
            input_pil = input_pil.convert("RGB")
        image_base64 = save_input_image_as_base64(np.array(input_pil))
    except Exception as err:
        raise UpscalerPreparationError(
            "Input image error",
            str(err),
            failure_reason="input_image_error",
        ) from err

    return UpscalerPreparedInputs(
        prompt=prompt,
        input_pil=input_pil,
        image_base64=image_base64,
        task_id=str(uuid.uuid4()),
        workflow_key=str(workflow or WORKFLOW_NAME),
        feature_flags={
            "enhancement_enabled": bool(enhancement),
            "engine_choice": engine_choice,
            "upscale_value": upscale_value,
        },
        settings_snapshot={
            "flux_creativity_tilet": float(flux_creativity_tilet),
            "upscale_value": upscale_value,
            "engine_choice": engine_choice,
            "enhancement": bool(enhancement),
        },
    )


def _apply_upscaler_workflow_updates(
    prompt: dict[str, Any],
    *,
    engine_choice: str,
    enhancement: bool,
    upscale_value: str,
    flux_creativity_tilet: float,
    main_image_name: str,
) -> None:
    prompt["99"]["inputs"]["image"] = main_image_name
    prompt["80:29"]["inputs"]["noise_seed"] = random.randint(
        0,
        999_999_999_999,
    )
    prompt["80:84"]["inputs"]["value"] = float(flux_creativity_tilet)
    if engine_choice == "Super Fast":
        prompt["102"]["inputs"]["image"] = ["99", 0]
        prompt["97"]["inputs"]["images"] = ["104", 0]
        prompt["104"]["inputs"]["scale_by"] = (
            0.5 if upscale_value == "x2" else 1
        )
        return

    prompt["96:82"]["inputs"]["image"] = ["99", 0]
    prompt["97"]["inputs"]["images"] = ["81:13", 0]
    prompt["96:85"]["inputs"]["scale_by"] = (
        2 if upscale_value == "x2" else 4
    )
    if enhancement:
        prompt["81:38"]["inputs"]["image"] = ["80:14", 0]
        prompt["80:83"]["inputs"]["image"] = ["77:78", 0]
    else:
        prompt["81:38"]["inputs"]["image"] = ["77:78", 0]


def _build_upscaler_payload(
    prepared: UpscalerPreparedInputs,
    *,
    workflow_debug: bool,
    is_admin_user: bool,
) -> UpscalerPreparedJob:
    flags = prepared.feature_flags
    settings = prepared.settings_snapshot
    main_image_name = "main_image_name"
    _apply_upscaler_workflow_updates(
        prepared.prompt,
        engine_choice=str(flags["engine_choice"]),
        enhancement=bool(flags["enhancement_enabled"]),
        upscale_value=str(flags["upscale_value"]),
        flux_creativity_tilet=float(settings["flux_creativity_tilet"]),
        main_image_name=main_image_name,
    )
    tile_estimate = _estimate_seedvr_tile_workload(
        prompt=prepared.prompt,
        input_width=int(prepared.input_pil.width),
        input_height=int(prepared.input_pil.height),
        engine_choice=str(flags["engine_choice"]),
        upscale_value=str(flags["upscale_value"]),
    )
    payload = prepare_json(
        prepared.prompt,
        [{"name": main_image_name, "image": prepared.image_base64}],
    )
    workflow_debug_path: Path | None = None
    if os.getenv("SAVE_DEBUG_PROMPT_JSON", "0") == "1" or (
        workflow_debug and is_admin_user
    ):
        try:
            workflow_debug_path = _save_workflow_debug_json(
                payload,
                workflow_name=prepared.workflow_key,
                task_id=prepared.task_id,
            )
            logger.info(
                "Saved ComfyUI workflow JSON: %s",
                workflow_debug_path,
            )
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)

    workflow_profile = _resolve_workflow_profile(prepared.workflow_key)
    logger.info(
        "Using workflow profile '%s' "
        "(upscale_node=%s, enhancement_node=%s, wrap_nodes=%s)",
        workflow_profile.get("name"),
        workflow_profile.get("upscale_node_id"),
        workflow_profile.get("enhancement_node_id"),
        workflow_profile.get("wrap_up_node_ids"),
    )
    return UpscalerPreparedJob(
        inputs=prepared,
        payload=payload,
        workflow_debug_path=workflow_debug_path,
        workflow_profile=workflow_profile,
        tile_estimate=tile_estimate,
    )


def _create_upscaler_task_tracker(
    prepared: UpscalerPreparedInputs,
    *,
    identity: Any,
    user_agent: str | None,
    session_id: str,
    workflow_profile_name: str,
) -> TaskTracker:
    return TaskTracker(
        store=None,
        task_id=prepared.task_id,
        user_email=identity.email,
        user_prefix=identity.username_prefix,
        user_display_name=identity.display_name,
        user_role=identity.role,
        avatar_filename=identity.avatar_filename,
        workflow=WorkflowContext(
            key=prepared.workflow_key,
            name=prepared.workflow_key,
            version=WORKFLOW_VERSION,
            category=WORKFLOW_CATEGORY,
            workflow_type=WORKFLOW_TYPE,
        ),
        source_page="/tab/5k-upscaler-flux",
        browser_user_agent=user_agent,
        session_id=session_id,
        environment_name=APP_ENVIRONMENT,
        feature_flags=prepared.feature_flags,
        settings=prepared.settings_snapshot,
        input_meta={
            "width": int(prepared.input_pil.width),
            "height": int(prepared.input_pil.height),
            "resolution": (
                f"{int(prepared.input_pil.width)}"
                f"x{int(prepared.input_pil.height)}"
            ),
            "format": str(prepared.input_pil.mode),
        },
        request_summary={
            "workflow_profile_name": workflow_profile_name,
            "engine_choice": prepared.feature_flags["engine_choice"],
            "enhancement": bool(
                prepared.feature_flags["enhancement_enabled"]
            ),
            "upscale_value": prepared.feature_flags["upscale_value"],
        },
        prompt_type="image_upscale",
        created_by=identity.email,
    )


def _prepare_upscaler_request(
    *,
    image: Any,
    engine_choice: str,
    enhancement: bool,
    upscale_value: str,
    flux_creativity_tilet: float,
    workflow_debug: bool,
    workflow: str,
    identity: Any,
    user_agent: str | None,
    session_id: str,
) -> UpscalerRequestContext:
    prepared = _prepare_upscaler_inputs(
        image=image,
        engine_choice=engine_choice,
        enhancement=enhancement,
        upscale_value=upscale_value,
        flux_creativity_tilet=flux_creativity_tilet,
        workflow=workflow,
    )
    tracker = _create_upscaler_task_tracker(
        prepared,
        identity=identity,
        user_agent=user_agent,
        session_id=session_id,
        workflow_profile_name=workflow,
    )
    try:
        job = _build_upscaler_payload(
            prepared,
            workflow_debug=workflow_debug,
            is_admin_user=(
                str(getattr(identity, "role", "") or "")
                .strip()
                .lower()
                == "admin"
            ),
        )
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
        raise UpscalerRequestError(message) from err
    return UpscalerRequestContext(
        inputs=prepared,
        job=job,
        tracker=tracker,
    )


async def _submit_upscaler_job(
    api: RunpodAPI,
    payload: dict[str, Any],
) -> UpscalerSubmissionResult:
    try:
        response = await api.run(payload)
        return UpscalerSubmissionResult(job_id=str(response["id"]))
    except RunpodSubmissionUncertainError as err:
        return UpscalerSubmissionResult(
            job_id=None,
            error_message=(
                f"{err}\n\nPlease check the Jobs page before trying again; "
                "RunPod may already have accepted this request."
            ),
            uncertain=True,
        )
    except RunpodSubmissionError as err:
        return UpscalerSubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )
    except Exception as err:
        return UpscalerSubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )


async def _finalize_upscaler_output(
    status: dict[str, Any],
    *,
    input_pil: Image.Image,
    job_id: str,
) -> UpscalerFinalizedOutput:
    try:
        result_image = await _decode_output_image(status)
        if result_image.mode not in ("RGB", "RGBA"):
            result_image = result_image.convert("RGBA")
        tmp_dir = Path(tempfile.gettempdir())
        left_path = tmp_dir / f"{job_id}_left.png"
        right_path = tmp_dir / f"{job_id}_right.png"
        input_pil.save(left_path, "PNG")
        result_image.save(right_path, "PNG")
        return UpscalerFinalizedOutput(
            result_image=result_image,
            left_path=left_path,
            right_path=right_path,
            artifacts=extract_artifacts_from_status(status),
        )
    except Exception as err:
        return UpscalerFinalizedOutput(error_message=str(err))


async def _advance_upscaler_stream(
    api: RunpodAPI,
    job_id: str,
    state: UpscalerPollState,
    *,
    poll_idx: int,
    stream_enabled: bool,
) -> tuple[list[tuple[int | float | None, str, list[str]]], str | None]:
    entries: list[tuple[int | float | None, str, list[str]]] = []
    stream_state: str | None = None
    if not stream_enabled:
        return entries, stream_state
    if state.stream_task is not None and state.stream_task.done():
        try:
            response = state.stream_task.result()
            _append_trace_event(
                state.trace_file,
                "stream_poll",
                {
                    "poll_idx": poll_idx,
                    "snapshot": _stream_trace_snapshot(response),
                },
            )
            entries, stream_state = _extract_stream_progress_signals(
                response,
                seen_signatures=state.stream_seen_signatures,
                seen_order=state.stream_seen_order,
            )
            if entries:
                _append_trace_event(
                    state.trace_file,
                    "stream_progress_batch",
                    {
                        "poll_idx": poll_idx,
                        "entries": len(entries),
                        "tail": [entry[1] for entry in entries[-3:]],
                    },
                )
        except Exception as err:
            _append_trace_event(
                state.trace_file,
                "stream_poll_error",
                {"poll_idx": poll_idx, "error": str(err)},
            )
        finally:
            state.stream_task = None
    if state.stream_task is None:
        state.stream_task = asyncio.create_task(api.stream(job_id))
    return entries, stream_state


def _render_upscaler_live_status(
    state: UpscalerPollState,
    *,
    fallback: str,
    runpod_progress: int | float | None,
) -> str:
    return _render_live_status(
        fallback=fallback,
        runpod_progress=runpod_progress,
        current_node=state.current_node,
        node_step_done=state.node_step_done,
        node_step_total=state.node_step_total,
        queue_remaining=state.queue_remaining,
        logs=state.live_logs,
        phase_tracker=state.phase_tracker,
        overall_percent=state.last_overall_percent,
    )


def _apply_upscaler_progress_text(
    state: UpscalerPollState,
    *,
    progress_text: str,
) -> None:
    (
        state.current_node,
        state.node_step_done,
        state.node_step_total,
        state.queue_remaining,
        state.live_logs,
        state.last_log_line,
    ) = state.phase_tracker.apply_live_text(
        progress_text=progress_text,
        current_node=state.current_node,
        node_step_done=state.node_step_done,
        node_step_total=state.node_step_total,
        queue_remaining=state.queue_remaining,
        live_logs=state.live_logs,
        last_log_line=state.last_log_line,
    )
    state.last_overall_percent = max(
        state.last_overall_percent,
        state.phase_tracker.overall_percent(),
    )


def _upscaler_progress_events(
    status: dict[str, Any],
    stream_entries: list[tuple[int | float | None, str, list[str]]],
    state: UpscalerPollState,
    *,
    poll_idx: int,
    runpod_state: str,
) -> list[UpscalerPollEvent]:
    runpod_progress, status_text, status_hints = _extract_progress_signal(status)
    signals = [
        ("stream", progress, text, hints)
        for progress, text, hints in stream_entries
    ]
    if status_text:
        signals.append(("status", runpod_progress, status_text, status_hints))
    effective_progress = runpod_progress
    if effective_progress is None:
        for _, progress, _, _ in reversed(signals):
            if isinstance(progress, (int, float)):
                effective_progress = progress
                break

    fallback = (
        runpod_state.lower().replace("_", " ")
        if runpod_state in ACTIVE_STATES
        else "processing"
    )
    events: list[UpscalerPollEvent] = []
    seen_texts: set[str] = set()
    for source, signal_progress, progress_text, hints in signals:
        if progress_text in seen_texts:
            continue
        seen_texts.add(progress_text)
        _append_trace_event(
            state.trace_file,
            "progress_signal",
            {
                "poll_idx": poll_idx,
                "source": source,
                "state": runpod_state,
                "runpod_progress": signal_progress,
                "progress_text": progress_text,
                "hint_tail": hints[-3:],
            },
        )
        if any("Job completed. Returning" in text for text in hints):
            if state.completion_hint_seen_at is None:
                state.completion_hint_seen_at = poll_idx
        _apply_upscaler_progress_text(state, progress_text=progress_text)
        display_progress = (
            signal_progress
            if isinstance(signal_progress, (int, float))
            else effective_progress
        )
        status_md = _render_upscaler_live_status(
            state,
            fallback=fallback,
            runpod_progress=display_progress,
        )
        _append_trace_event(
            state.trace_file,
            "phase_update",
            {
                "poll_idx": poll_idx,
                "source": source,
                "overall_percent": state.last_overall_percent,
                "current_node": state.current_node,
                "node_step_done": state.node_step_done,
                "node_step_total": state.node_step_total,
                "queue_remaining": state.queue_remaining,
                "phase_tracker": _phase_trace_snapshot(state.phase_tracker),
                "selected_progress_text": progress_text,
            },
        )
        stage = str(
            state.phase_tracker.get("phase") or "processing"
        ).lower().replace(" ", "_")
        events.append(
            UpscalerPollEvent(
                kind="progress",
                status=status,
                message=state.current_node or progress_text or fallback,
                progress_percent=state.last_overall_percent,
                stage=stage,
                poll_idx=poll_idx,
                status_md=status_md,
                node_id=(
                    state.current_node.split(" ", 1)[0]
                    if state.current_node
                    else None
                ),
                progress_source=source,
            )
        )
    if events:
        return events
    return [
        _upscaler_no_signal_event(
            status,
            state,
            poll_idx=poll_idx,
            runpod_state=runpod_state,
            runpod_progress=effective_progress,
            status_hints=status_hints,
            fallback=fallback,
        )
    ]


def _upscaler_no_signal_event(
    status: dict[str, Any],
    state: UpscalerPollState,
    *,
    poll_idx: int,
    runpod_state: str,
    runpod_progress: int | float | None,
    status_hints: list[str],
    fallback: str,
) -> UpscalerPollEvent:
    if any("Job completed. Returning" in text for text in status_hints):
        if state.completion_hint_seen_at is None:
            state.completion_hint_seen_at = poll_idx
    if state.completion_hint_seen_at is not None:
        state.phase_tracker["phase"] = PHASE_WRAP_UP
        state.phase_tracker["wrap_ratio"] = max(
            state.phase_tracker["wrap_ratio"],
            0.92,
        )
        reason = "completion_hint_seen"
        display_fallback = "finalizing output"
        stage = "wrap_up"
        message = "Finalizing output..."
    else:
        reason = "no_progress_text"
        display_fallback = fallback
        stage = str(
            state.phase_tracker.get("phase") or "processing"
        ).lower().replace(" ", "_")
        message = state.current_node or fallback
    state.last_overall_percent = max(
        state.last_overall_percent,
        state.phase_tracker.overall_percent(),
    )
    status_md = _render_upscaler_live_status(
        state,
        fallback=display_fallback,
        runpod_progress=runpod_progress,
    )
    _append_trace_event(
        state.trace_file,
        "phase_update_no_progress_text",
        {
            "poll_idx": poll_idx,
            "overall_percent": state.last_overall_percent,
            "reason": reason,
            "phase_tracker": _phase_trace_snapshot(state.phase_tracker),
        },
    )
    return UpscalerPollEvent(
        kind="progress",
        status=status,
        message=message,
        progress_percent=state.last_overall_percent,
        stage=stage,
        poll_idx=poll_idx,
        status_md=status_md,
        node_id=(
            state.current_node.split(" ", 1)[0]
            if state.current_node
            else None
        ),
        progress_source=reason,
    )


async def _upscaler_terminal_event(
    status: dict[str, Any],
    *,
    runpod_state: str,
    has_final_output: bool,
    input_pil: Image.Image,
    job_id: str,
    poll_idx: int,
    state: UpscalerPollState,
) -> UpscalerPollEvent | None:
    if runpod_state == "CANCELLED":
        _append_trace_event(
            state.trace_file,
            "terminal_cancelled",
            {"poll_idx": poll_idx},
        )
        return UpscalerPollEvent(
            kind="cancelled",
            status=status,
            message="Job cancelled.",
            progress_percent=state.last_overall_percent,
            stage=str(state.phase_tracker.get("phase") or "processing"),
            poll_idx=poll_idx,
            tracker_error_message="Job cancelled by user or worker.",
        )
    if runpod_state in TERMINAL_FAILURES:
        message = _extract_error_message(status)
        _append_trace_event(
            state.trace_file,
            "terminal_failure",
            {
                "poll_idx": poll_idx,
                "state": runpod_state,
                "error": message,
            },
        )
        return UpscalerPollEvent(
            kind="terminal_failure",
            status=status,
            message=message,
            progress_percent=state.last_overall_percent,
            stage=str(state.phase_tracker.get("phase") or "processing"),
            poll_idx=poll_idx,
            tracker_error_message=message,
        )
    if runpod_state != "COMPLETED" and not has_final_output:
        return None

    finalized = await _finalize_upscaler_output(
        status,
        input_pil=input_pil,
        job_id=job_id,
    )
    if finalized.error_message and has_final_output and runpod_state != "COMPLETED":
        _append_trace_event(
            state.trace_file,
            "final_payload_lag",
            {
                "poll_idx": poll_idx,
                "error": finalized.error_message,
                "state": runpod_state,
            },
        )
        state.phase_tracker["phase"] = PHASE_WRAP_UP
        state.phase_tracker["wrap_ratio"] = max(
            state.phase_tracker["wrap_ratio"],
            0.92,
        )
        return UpscalerPollEvent(
            kind="finalizing",
            status=status,
            message="Finalizing output payload...",
            progress_percent=max(state.last_overall_percent, 92),
            stage="output_collecting",
            poll_idx=poll_idx,
        )
    if finalized.error_message:
        _append_trace_event(
            state.trace_file,
            "decode_failure",
            {
                "poll_idx": poll_idx,
                "error": finalized.error_message,
                "snapshot": _status_trace_snapshot(status),
            },
        )
        return UpscalerPollEvent(
            kind="decode_error",
            status=status,
            message=f"Failed to decode image: {finalized.error_message}",
            progress_percent=state.last_overall_percent,
            stage="output_collecting",
            poll_idx=poll_idx,
            finalized=finalized,
            tracker_error_message=finalized.error_message,
        )
    return UpscalerPollEvent(
        kind="completed",
        status=status,
        message="Done!",
        progress_percent=100,
        stage="completed",
        poll_idx=poll_idx,
        finalized=finalized,
    )


async def _poll_upscaler_job(
    api: RunpodAPI,
    job_id: str,
    *,
    input_pil: Image.Image,
    state: UpscalerPollState,
    stream_enabled: bool = RUNPOD_STREAM_ENABLED,
):
    try:
        for poll_idx in range(MAX_STATUS_POLLS):
            entries, stream_state = await _advance_upscaler_stream(
                api,
                job_id,
                state,
                poll_idx=poll_idx,
                stream_enabled=stream_enabled,
            )
            try:
                status = await api.status(job_id)
            except Exception as err:
                state.consecutive_status_errors += 1
                _append_trace_event(
                    state.trace_file,
                    "status_poll_error",
                    {
                        "poll_idx": poll_idx,
                        "consecutive_errors": state.consecutive_status_errors,
                        "error": str(err),
                    },
                )
                if state.consecutive_status_errors > MAX_CONSECUTIVE_STATUS_ERRORS:
                    _append_trace_event(
                        state.trace_file,
                        "status_poll_error_terminal",
                        {"poll_idx": poll_idx, "error": str(err)},
                    )
                    yield UpscalerPollEvent(
                        kind="status_error",
                        status={},
                        message=f"Failed to check job status: {err}",
                        progress_percent=state.last_overall_percent,
                        stage="status_poll",
                        poll_idx=poll_idx,
                        tracker_error_message=str(err),
                    )
                    return
                yield UpscalerPollEvent(
                    kind="retry",
                    status={},
                    message=(
                        "Temporary connection issue while checking RunPod status."
                        f"\n\nRetrying automatically "
                        f"({state.consecutive_status_errors}/"
                        f"{MAX_CONSECUTIVE_STATUS_ERRORS}).\n\n"
                        f"`{err}`"
                    ),
                    progress_percent=state.last_overall_percent,
                    stage="status_poll",
                    poll_idx=poll_idx,
                )
                await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
                continue

            state.consecutive_status_errors = 0
            _append_trace_event(
                state.trace_file,
                "status_poll",
                {
                    "poll_idx": poll_idx,
                    "snapshot": _status_trace_snapshot(status),
                },
            )
            runpod_state = (status.get("status") or stream_state or "").upper()
            terminal = await _upscaler_terminal_event(
                status,
                runpod_state=runpod_state,
                has_final_output=_has_final_output_payload(status),
                input_pil=input_pil,
                job_id=job_id,
                poll_idx=poll_idx,
                state=state,
            )
            if terminal is not None:
                yield terminal
                if terminal.kind != "finalizing":
                    return
                await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
                continue

            for event in _upscaler_progress_events(
                status,
                entries,
                state,
                poll_idx=poll_idx,
                runpod_state=runpod_state,
            ):
                yield event
            if (
                state.completion_hint_seen_at is not None
                and poll_idx - state.completion_hint_seen_at
                >= FINALIZATION_HINT_GRACE_POLLS
            ):
                _append_trace_event(
                    state.trace_file,
                    "completion_hint_timeout",
                    {
                        "poll_idx": poll_idx,
                        "grace_polls": FINALIZATION_HINT_GRACE_POLLS,
                        "phase_tracker": _phase_trace_snapshot(
                            state.phase_tracker
                        ),
                    },
                )
                yield UpscalerPollEvent(
                    kind="status_lag",
                    status=status,
                    message=(
                        "RunPod stayed IN_PROGRESS after completion hint. "
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

        _append_trace_event(
            state.trace_file,
            "polling_timeout",
            {
                "max_status_polls": MAX_STATUS_POLLS,
                "phase_tracker": _phase_trace_snapshot(state.phase_tracker),
            },
        )
        yield UpscalerPollEvent(
            kind="timeout",
            status={},
            message="Timed out waiting for RunPod completion status.",
            progress_percent=state.last_overall_percent,
            stage=str(state.phase_tracker.get("phase") or "processing"),
            poll_idx=MAX_STATUS_POLLS,
            tracker_error_message=(
                "Timed out waiting for RunPod completion status."
            ),
        )
    finally:
        state.cancel_stream()


def _record_upscaler_completed(
    tracker: TaskTracker,
    event: UpscalerPollEvent,
    *,
    phase_tracker: ProgressTracker,
    trace_file: Path | None,
) -> None:
    finalized = event.finalized
    if (
        finalized is None
        or finalized.result_image is None
        or finalized.left_path is None
        or finalized.right_path is None
    ):
        raise ValueError("Completed Upscaler event is missing finalized output.")
    artifacts = finalized.artifacts or {}
    tracker.mark_stage(
        status="uploading",
        stage="uploading",
        message="Saving result artifacts...",
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
            "poll_idx": event.poll_idx,
            "state": event.status.get("status"),
            "phase_tracker": _phase_trace_snapshot(phase_tracker),
            "result_left": str(finalized.left_path),
            "result_right": str(finalized.right_path),
        },
    )
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


def _record_upscaler_poll_event(
    tracker: TaskTracker,
    event: UpscalerPollEvent,
    state: UpscalerPollState,
) -> None:
    runpod_state = str(event.status.get("status") or "").upper()
    if runpod_state in ACTIVE_STATES and tracker.started_dt is None:
        tracker.mark_started(
            message="Execution started. Waiting for ComfyUI node updates..."
        )
    if event.kind == "retry":
        return
    if event.kind in {"progress", "finalizing"}:
        if event.kind == "finalizing":
            tracker.mark_stage(
                status="output_collecting",
                stage="output_collecting",
                message="ComfyUI execution finished. Collecting outputs...",
                progress_percent=max(event.progress_percent, 92),
            )
        tracker.emit_processing(
            stage=event.stage,
            message=event.message,
            progress_percent=event.progress_percent,
            node_id=event.node_id,
            metadata={
                "queue_remaining": state.queue_remaining,
                "runpod_state": event.status.get("status"),
                "progress_source": event.progress_source,
            },
        )
        return
    if event.kind == "completed":
        tracker.mark_stage(
            status="output_collecting",
            stage="output_collecting",
            message="ComfyUI execution finished. Collecting outputs...",
            progress_percent=max(event.progress_percent, 92),
        )
        _record_upscaler_completed(
            tracker,
            event,
            phase_tracker=state.phase_tracker,
            trace_file=state.trace_file,
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


def _render_upscaler_poll_event(
    event: UpscalerPollEvent,
    *,
    job_id: str,
) -> tuple[Any, str, str | None]:
    if event.kind == "progress":
        return gr.update(), str(event.status_md or event.message), job_id
    if event.kind == "retry":
        return gr.update(), f"⏳ {event.message}", job_id
    if event.kind == "finalizing":
        return gr.update(), "⏳ Finalizing output…", job_id
    if event.kind == "completed":
        finalized = event.finalized
        if (
            finalized is None
            or finalized.left_path is None
            or finalized.right_path is None
        ):
            raise ValueError(
                "Completed Upscaler event is missing output paths."
            )
        return (
            (str(finalized.left_path), str(finalized.right_path)),
            "✅ Done!",
            None,
        )
    if event.kind == "cancelled":
        return gr.update(), "⚠️ Job cancelled.", None
    return gr.update(), f"❌ {event.message}", None


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
        yield gr.update(), (
            "❌ Authentication required. Please sign in again."
        ), None
        return

    identity = auth_service.get_identity(user_email)
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    try:
        context = _prepare_upscaler_request(
            image=image,
            engine_choice=engine_choice,
            enhancement=enhancement,
            upscale_value=upscale_value,
            flux_creativity_tilet=flux_creativity_tilet,
            workflow_debug=workflow_debug,
            workflow=workflow,
            identity=identity,
            user_agent=user_agent,
            session_id=session_id,
        )
    except UpscalerPreparationError as err:
        yield gr.update(), f"❌ {err.title}: {err}", None
        return
    except UpscalerRequestError as err:
        yield gr.update(), f"❌ {err}", None
        return

    prepared = context.inputs
    prepared_job = context.job
    tracker = context.tracker
    api = RunpodAPI(environment="seed")
    submission = await _submit_upscaler_job(api, prepared_job.payload)
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
        prefix = "⚠️" if submission.uncertain else "❌"
        yield (
            gr.update(),
            f"{prefix} {submission.error_message or 'Job submission failed.'}",
            None,
        )
        return

    job_id = submission.job_id
    tracker.attach_request(
        request_id=job_id,
        task_url=f"{api.base_url}/status/{job_id}",
        retry_count=0,
    )
    trace_file = _init_trace_file(job_id=job_id, workflow=workflow)
    _append_trace_event(
        trace_file,
        "job_submitted",
        {
            "job_id": job_id,
            "workflow": workflow,
            "workflow_profile": prepared_job.workflow_profile,
            "engine_choice": engine_choice,
            "enhancement": enhancement,
            "upscale_value": upscale_value,
            "trace_file": str(trace_file) if trace_file else None,
        },
    )
    if prepared_job.workflow_debug_path is not None:
        submitted_message = (
            "🚀 Job submitted…\n\nComfyUI workflow JSON: "
            f"`{prepared_job.workflow_debug_path}`"
        )
    elif trace_file is not None:
        submitted_message = (
            f"🚀 Job submitted…\n\nDebug trace file: `{trace_file}`"
        )
    else:
        submitted_message = "🚀 Job submitted…"
    yield gr.update(), submitted_message, job_id

    poll_state = UpscalerPollState(
        phase_tracker=ProgressTracker.for_phases(
            workflow_profile=prepared_job.workflow_profile,
            tile_estimate=prepared_job.tile_estimate,
        ),
        trace_file=trace_file,
    )
    async for event in _poll_upscaler_job(
        api,
        job_id,
        input_pil=prepared.input_pil,
        state=poll_state,
    ):
        _record_upscaler_poll_event(tracker, event, poll_state)
        yield _render_upscaler_poll_event(event, job_id=job_id)
        if event.kind not in {"progress", "retry", "finalizing"}:
            return


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
