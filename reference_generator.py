from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("RUNPOD_POD_ID_REFERENCE_GENERATOR", "05wm3fdysmkq7m")

import gradio as gr
from PIL import Image
from gradio_imageslider import ImageSlider

from auth_service import get_auth_service
from runpod_api_class import RunpodAPI
from task_tracking import TaskTracker, WorkflowContext, extract_artifacts_from_status
from utils import (
    _decode_output_image,
    _extract_error_message,
    _extract_progress_signal,
    _extract_stream_progress_signals,
    _has_final_output_payload,
    _to_pil_image,
    prepare_json,
    save_input_image_as_base64,
)
from workflow_ui import (
    debug_checkbox_visibility_update as _debug_checkbox_visibility_update,
    request_header as _request_header,
    save_workflow_debug_json,
)
from workflow_progress import ProgressTracker, StageSpec

_app_log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _app_log_level, logging.INFO))
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("gradio").setLevel(logging.WARNING)

APP_TITLE = "Momi Forge"
TAB_TITLE = "Reference Generator"
WORKFLOW_NAME = os.getenv(
    "REFERENCE_GENERATOR_WORKFLOW_NAME",
    "Reference_generator_v02",
)
WORKFLOW_VERSION = os.getenv("WORKFLOW_VERSION_REFERENCE_GENERATOR", "v02")
WORKFLOW_CATEGORY = os.getenv(
    "WORKFLOW_CATEGORY_REFERENCE_GENERATOR",
    "reference_generation",
)
WORKFLOW_TYPE = os.getenv("WORKFLOW_TYPE_REFERENCE_GENERATOR", "image")
RUNPOD_ENVIRONMENT = os.getenv(
    "REFERENCE_GENERATOR_RUNPOD_ENVIRONMENT",
    "reference_generator",
)
APP_ENVIRONMENT = os.getenv("REFERENCE_GENERATOR_APP_ENVIRONMENT", RUNPOD_ENVIRONMENT)
APP_DEBUG = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_QUIET = os.getenv("APP_QUIET", "1").strip().lower() in {"1", "true", "yes", "on"}
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
MAX_STATUS_POLLS = int(os.getenv("RUNPOD_MAX_STATUS_POLLS", "1800"))
MAX_CONSECUTIVE_STATUS_ERRORS = int(
    os.getenv("RUNPOD_MAX_CONSECUTIVE_STATUS_ERRORS", "8")
)
REFERENCE_GENERATOR_MAX_UPLOAD_EDGE = max(
    256,
    int(os.getenv("REFERENCE_GENERATOR_MAX_UPLOAD_EDGE", "2048")),
)
REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES = max(
    1024 * 1024,
    int(
        os.getenv(
            "REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES",
            os.getenv("RUNPOD_MAX_REQUEST_BYTES", str(10 * 1024 * 1024)),
        )
    ),
)

TERMINAL_FAILURES = {"FAILED", "ERROR", "TIMED_OUT", "CANCELLED"}
NODE_MAIN_IMAGE_INPUT = "42"
NODE_REFERENCE_IMAGE_INPUT = "43"
NODE_IPADAPTER_ADVANCED = "30"
NODE_PIPEKSAMPLER_BASE = "12"
NODE_APPLY_CONTROLNET = "20"
NODE_ENHANCEMENT_IMAGE_ROUTER = "153"
NODE_ENHANCEMENT_DIRECT_SOURCE = "147"
NODE_COLOR_MATCH_SOURCE = "149"
NODE_ENHANCEMENT_BYPASS_SOURCE = "182"

STAGE_PREPARATION = "preparation"
STAGE_CONDITIONING = "conditioning"
STAGE_BASE_SAMPLING = "base_sampling"
STAGE_UPSCALE = "upscale"
STAGE_ENHANCEMENT = "enhancement"
STAGE_COLOR_MATCH = "color_match"
STAGE_SAVE = "save"

STAGE_ORDER = [
    STAGE_PREPARATION,
    STAGE_CONDITIONING,
    STAGE_BASE_SAMPLING,
    STAGE_UPSCALE,
    STAGE_ENHANCEMENT,
    STAGE_COLOR_MATCH,
    STAGE_SAVE,
]

STAGE_LABELS = {
    STAGE_PREPARATION: "Preparation",
    STAGE_CONDITIONING: "Reference Setup",
    STAGE_BASE_SAMPLING: "Base Sampling",
    STAGE_UPSCALE: "Upscale Pass",
    STAGE_ENHANCEMENT: "Enhancement",
    STAGE_COLOR_MATCH: "Color Match",
    STAGE_SAVE: "Saving Output",
}

DISPLAY_STAGE_LABELS = {
    STAGE_PREPARATION: "Preparing images",
    STAGE_CONDITIONING: "Applying reference guidance",
    STAGE_BASE_SAMPLING: "Generating image",
    STAGE_UPSCALE: "Refining image",
    STAGE_ENHANCEMENT: "Refining image",
    STAGE_COLOR_MATCH: "Refining image",
    STAGE_SAVE: "Saving output",
}

STAGE_WEIGHTS = {
    STAGE_PREPARATION: 8.0,
    STAGE_CONDITIONING: 16.0,
    STAGE_BASE_SAMPLING: 24.0,
    STAGE_UPSCALE: 15.0,
    STAGE_ENHANCEMENT: 22.0,
    STAGE_COLOR_MATCH: 5.0,
    STAGE_SAVE: 10.0,
}

NODE_STAGE_HINTS = {
    "42": STAGE_PREPARATION,
    "43": STAGE_PREPARATION,
    "35": STAGE_PREPARATION,
    "36": STAGE_PREPARATION,
    "44": STAGE_CONDITIONING,
    "19": STAGE_CONDITIONING,
    "20": STAGE_CONDITIONING,
    "22": STAGE_CONDITIONING,
    "30": STAGE_CONDITIONING,
    "14": STAGE_CONDITIONING,
    "17": STAGE_CONDITIONING,
    "29": STAGE_CONDITIONING,
    "12": STAGE_BASE_SAMPLING,
    "16": STAGE_UPSCALE,
    "182": STAGE_UPSCALE,
    "151": STAGE_UPSCALE,
    "136": STAGE_ENHANCEMENT,
    "147": STAGE_ENHANCEMENT,
    "149": STAGE_COLOR_MATCH,
    "153": STAGE_SAVE,
}

NODE_STATUS_HINTS = {
    "42": "Loading main image...",
    "43": "Loading reference image...",
    "35": "Resizing main image...",
    "36": "Resizing reference image...",
    "44": "Preparing depth guidance...",
    "19": "Building edge guidance...",
    "20": "Applying structure guidance...",
    "22": "Preparing reference features...",
    "30": "Applying color reference...",
    "14": "Encoding latent input...",
    "17": "Combining guidance...",
    "29": "Preparing styled prompt...",
    "12": "Running base sampler...",
    "16": "Running upscale sampler...",
    "182": "Resizing enhanced result...",
    "151": "Normalizing enhancement input...",
    "136": "Running enhancement sampler...",
    "147": "Decoding enhanced image...",
    "149": "Matching colors...",
    "153": "Saving final image...",
}

NODE_STAGE_PROGRESS_HINTS = {
    "42": 0.18,
    "43": 0.36,
    "35": 0.68,
    "36": 0.86,
    "44": 0.24,
    "19": 0.38,
    "20": 0.54,
    "22": 0.68,
    "30": 0.84,
    "14": 0.94,
    "17": 0.98,
    "29": 0.12,
    "12": 0.08,
    "16": 0.18,
    "182": 0.68,
    "151": 0.88,
    "136": 0.08,
    "147": 0.92,
    "149": 0.75,
    "153": 0.8,
}

BOTTOM_PROGRESS_LAYOUT_CSS = """
.bottom-progress-row {
  margin-top: 12px;
  margin-bottom: 12px;
}

.bottom-progress-row > div {
  width: 100%;
}
"""

auth_service = get_auth_service()


def _save_workflow_debug_json(
    payload: dict[str, Any],
    *,
    workflow_name: str,
    task_id: str,
) -> Path:
    return save_workflow_debug_json(
        payload,
        workflow_name=workflow_name,
        task_id=task_id,
        prefix="reference_generator",
    )


def _format_byte_size(value: int) -> str:
    mb = value / (1024 * 1024)
    return f"{mb:.1f} MB"


def _json_payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _resize_for_runpod_transport(image: Image.Image) -> Image.Image:
    width, height = image.size
    longest_edge = max(width, height)
    if longest_edge <= REFERENCE_GENERATOR_MAX_UPLOAD_EDGE:
        return image

    scale = REFERENCE_GENERATOR_MAX_UPLOAD_EDGE / float(longest_edge)
    target_size = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return image.resize(target_size, Image.Resampling.LANCZOS)


def _resolve_reference_generator_workflow_path() -> Path:
    configured_path = os.getenv("REFERENCE_GENERATOR_WORKFLOW_PATH", "").strip()
    if configured_path:
        path = Path(configured_path)
        if path.exists():
            return path

    workflow_file = os.getenv(
        "REFERENCE_GENERATOR_WORKFLOW_FILE",
        "refrence_generator.json",
    ).strip()
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "api_workflow" / workflow_file,
        script_dir / "api_workflow" / "New_runpod" / workflow_file,
        script_dir.parent / "api_workflow" / workflow_file,
        script_dir.parent / "api_workflow" / "New_runpod" / workflow_file,
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        "Could not find the Reference Generator workflow file. "
        "Set REFERENCE_GENERATOR_WORKFLOW_PATH or place "
        f"'{workflow_file}' under an api_workflow folder."
    )


def _render_status_panel(
    title: str,
    message: str,
    *,
    percent: int | None = None,
    accent: str = "#38bdf8",
) -> str:
    safe_title = html.escape(title)
    safe_message = html.escape(message).replace("\n", "<br>")
    progress_html = ""
    if percent is not None:
        clamped = max(0, min(int(percent), 100))
        progress_html = f"""
  <div style="margin-top:10px;height:10px;background:#1e293b;border-radius:999px;overflow:hidden;">
    <div style="height:10px;width:{clamped}%;background:linear-gradient(90deg,{accent},#3b82f6);"></div>
  </div>
  <div style="margin-top:8px;font-size:12px;opacity:.8;">{clamped}%</div>
"""
    return f"""
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:14px 16px;color:#e2e8f0;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="font-weight:700;font-size:15px;color:{accent};">{safe_title}</div>
  <div style="margin-top:8px;font-size:13px;line-height:1.45;">{safe_message}</div>
  {progress_html}
</div>
"""


def _render_idle_status() -> str:
    return _render_status_panel(
        "Ready",
        "Upload a main image and a reference image, adjust the sliders, and run the workflow.",
        percent=0,
    )


def _init_reference_progress_tracker(
    *,
    enhancement_enabled: bool,
    color_match_enabled: bool,
) -> ProgressTracker:
    specs = [
        StageSpec(
            stage_key,
            STAGE_LABELS[stage_key],
            weight=STAGE_WEIGHTS[stage_key],
            enabled=(
                bool(enhancement_enabled)
                if stage_key == STAGE_ENHANCEMENT
                else bool(enhancement_enabled and color_match_enabled)
                if stage_key == STAGE_COLOR_MATCH
                else True
            ),
        )
        for stage_key in STAGE_ORDER
    ]
    return ProgressTracker.for_reference(
        specs=specs,
        node_stage_hints=NODE_STAGE_HINTS,
        node_status_hints=NODE_STATUS_HINTS,
        node_progress_hints=NODE_STAGE_PROGRESS_HINTS,
        save_stage_key=STAGE_SAVE,
        save_node_id=NODE_ENHANCEMENT_IMAGE_ROUTER,
    )


def _reference_display_stage(tracker: dict[str, Any], *, queued: bool = False) -> str:
    if queued:
        return DISPLAY_STAGE_LABELS[STAGE_PREPARATION]
    current_stage = str(tracker.get("current_stage") or STAGE_PREPARATION)
    return DISPLAY_STAGE_LABELS.get(current_stage, "Generating image")


def _render_reference_progress_panel(
    tracker: dict[str, Any],
    *,
    overall_percent: int,
    queued: bool = False,
) -> str:
    title = _reference_display_stage(tracker, queued=queued)
    message = title
    accent = "#f59e0b" if queued else "#38bdf8"
    return _render_status_panel(title, message, percent=overall_percent, accent=accent)


def _disable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=False)


def _enable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=True)


def _update_color_match_visibility(enhancement_enabled: bool):
    if enhancement_enabled:
        return gr.update(visible=True)
    return gr.update(visible=False, value=False)


def _connect(
    prompt: dict[str, Any],
    target_node: str,
    input_name: str,
    source_node: str,
    output_idx: int = 0,
) -> None:
    prompt[target_node]["inputs"][input_name] = [source_node, output_idx]


def _apply_reference_workflow_updates(
    prompt: dict[str, Any],
    *,
    main_image_b64: str,
    reference_image_b64: str,
    color_strength: float,
    creativity: float,
    structure_strength: float,
    enhancement_enabled: bool,
    color_match_enabled: bool,
) -> None:
    prompt[NODE_MAIN_IMAGE_INPUT]["inputs"]["image"] = main_image_b64
    prompt[NODE_REFERENCE_IMAGE_INPUT]["inputs"]["image"] = reference_image_b64

    prompt[NODE_IPADAPTER_ADVANCED]["inputs"]["weight"] = float(color_strength)
    prompt[NODE_PIPEKSAMPLER_BASE]["inputs"]["denoise"] = float(creativity)
    prompt[NODE_APPLY_CONTROLNET]["inputs"]["strength"] = float(structure_strength)

    if not enhancement_enabled:
        _connect(
            prompt,
            NODE_ENHANCEMENT_IMAGE_ROUTER,
            "images",
            NODE_ENHANCEMENT_BYPASS_SOURCE,
        )
        return

    source_node = NODE_COLOR_MATCH_SOURCE if color_match_enabled else NODE_ENHANCEMENT_DIRECT_SOURCE
    _connect(
        prompt,
        NODE_ENHANCEMENT_IMAGE_ROUTER,
        "images",
        source_node,
    )


def _save_temp_image(image: Image.Image, *, prefix: str) -> Path:
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix).strip("_") or "image"
    with tempfile.NamedTemporaryFile(
        prefix=f"{safe_prefix}_",
        suffix=".png",
        delete=False,
    ) as tmp:
        image.save(tmp.name, format="PNG")
        return Path(tmp.name)


async def reference_generator_generate(
    main_image: Any,
    reference_image: Any,
    color_strength: float,
    creativity: float,
    structure_strength: float,
    enhancement_enabled: bool,
    color_match_enabled: bool,
    workflow_debug: bool,
    job_state: str | None,
    workflow: str,
    request: gr.Request,
):
    del job_state
    logger.info("Workflow %s called", workflow)

    user_email = getattr(request, "username", None)
    if not user_email:
        yield (
            gr.update(),
            _render_status_panel(
                "Authentication Required",
                "Please sign in again before running the workflow.",
                accent="#f87171",
            ),
            None,
        )
        return

    identity = auth_service.get_identity(user_email)
    is_admin_user = str(getattr(identity, "role", "") or "").strip().lower() == "admin"
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    source_page = "/tab/reference-generator"

    if main_image is None or reference_image is None:
        yield (
            gr.update(),
            _render_status_panel(
                "Input Error",
                "Both Main Image and Reference Image are required.",
                accent="#f87171",
            ),
            None,
        )
        return

    try:
        prompt_path = _resolve_reference_generator_workflow_path()
        with open(prompt_path, "r", encoding="utf-8") as fh:
            prompt: dict[str, Any] = json.load(fh)
    except UnicodeDecodeError:
        with open(prompt_path, "r", encoding="cp1252") as fh:
            prompt = json.load(fh)
    except Exception as err:
        yield (
            gr.update(),
            _render_status_panel("Workflow Error", f"Prompt load failed: {err}", accent="#f87171"),
            None,
        )
        return

    try:
        main_pil = _to_pil_image(main_image)
        reference_pil = _to_pil_image(reference_image)
        if main_pil.mode not in ("RGB", "RGBA"):
            main_pil = main_pil.convert("RGB")
        if reference_pil.mode not in ("RGB", "RGBA"):
            reference_pil = reference_pil.convert("RGB")
    except Exception as err:
        yield (
            gr.update(),
            _render_status_panel("Input Error", f"Image preparation failed: {err}", accent="#f87171"),
            None,
        )
        return

    try:
        main_transport_pil = _resize_for_runpod_transport(main_pil)
        reference_transport_pil = _resize_for_runpod_transport(reference_pil)
        main_image_b64 = save_input_image_as_base64(main_transport_pil)
        reference_image_b64 = save_input_image_as_base64(reference_transport_pil)
    except Exception as err:
        yield (
            gr.update(),
            _render_status_panel("Encoding Error", f"Failed to encode input images: {err}", accent="#f87171"),
            None,
        )
        return

    feature_flags = {
        "enhancement_enabled": bool(enhancement_enabled),
        "color_match_enabled": bool(color_match_enabled),
    }
    settings_snapshot = {
        "color_strength": float(color_strength),
        "creativity": float(creativity),
        "structure_strength": float(structure_strength),
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
            "width": int(main_pil.width),
            "height": int(main_pil.height),
            "resolution": f"{int(main_pil.width)}x{int(main_pil.height)}",
            "format": str(main_pil.mode),
            "reference_width": int(reference_pil.width),
            "reference_height": int(reference_pil.height),
        },
        request_summary={
            "enhancement_enabled": bool(enhancement_enabled),
            "color_match_enabled": bool(color_match_enabled),
        },
        prompt_type="reference_generation",
        created_by=identity.email,
    )

    try:
        _apply_reference_workflow_updates(
            prompt,
            main_image_b64=main_image_b64,
            reference_image_b64=reference_image_b64,
            color_strength=float(color_strength),
            creativity=float(creativity),
            structure_strength=float(structure_strength),
            enhancement_enabled=bool(enhancement_enabled),
            color_match_enabled=bool(color_match_enabled and enhancement_enabled),
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
            _render_status_panel("Workflow Error", f"Workflow key missing: {err}", accent="#f87171"),
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
            _render_status_panel("Workflow Error", f"Workflow update failed: {err}", accent="#f87171"),
            None,
        )
        return

    final_json = prepare_json(prompt, images=[])
    payload_size = _json_payload_size_bytes(final_json)
    if payload_size > REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES:
        error_message = (
            "The RunPod request is too large to submit "
            f"({_format_byte_size(payload_size)}). "
            f"The current limit is {_format_byte_size(REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES)}. "
            "Use smaller main/reference images or lower REFERENCE_GENERATOR_MAX_UPLOAD_EDGE."
        )
        tracker.fail(
            failure_reason="request_payload_too_large",
            error_message=error_message,
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
            metadata={
                "payload_size_bytes": payload_size,
                "max_payload_bytes": REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES,
                "transport_main_size": list(main_transport_pil.size),
                "transport_reference_size": list(reference_transport_pil.size),
            },
        )
        yield (
            gr.update(),
            _render_status_panel("Input Too Large", error_message, accent="#f87171"),
            None,
        )
        return

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
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)

    api = RunpodAPI(environment=RUNPOD_ENVIRONMENT)
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
        )
        yield (
            gr.update(),
            _render_status_panel("RunPod Error", f"Job submission failed: {err}", accent="#f87171"),
            None,
        )
        return

    tracker.attach_request(
        request_id=job_id,
        task_url=f"{api.base_url}/status/{job_id}",
        retry_count=0,
    )

    submitted_message = "Preparing images"
    if workflow_debug_path is not None:
        submitted_message += f"\n\nDebug JSON saved: {workflow_debug_path}"
    yield gr.update(), _render_status_panel("Preparing images", submitted_message, percent=3), job_id

    left_path = _save_temp_image(main_pil, prefix="reference_generator_main")
    progress_tracker = _init_reference_progress_tracker(
        enhancement_enabled=bool(enhancement_enabled),
        color_match_enabled=bool(color_match_enabled and enhancement_enabled),
    )
    progress_tracker["current_status"] = "Job submitted. Waiting for worker updates..."
    last_progress_text: str | None = None
    last_runpod_progress: int | float | None = None
    last_overall_percent = 3
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
        if RUNPOD_STREAM_ENABLED:
            if stream_task is not None and stream_task.done():
                try:
                    stream_response = stream_task.result()
                    stream_progress_entries, _ = _extract_stream_progress_signals(
                        stream_response,
                        seen_signatures=stream_seen_signatures,
                        seen_order=stream_seen_order,
                    )
                except Exception as err:
                    logger.debug("RunPod stream poll failed: %s", err)
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
                    failure_reason="status_error",
                    error_message=str(err),
                    failure_stage="processing",
                    progress_percent=int(last_runpod_progress or 0),
                    worker_id=None,
                )
                yield (
                    gr.update(),
                    _render_status_panel("RunPod Error", f"Failed to check job status: {err}", accent="#f87171"),
                    None,
                )
                _cancel_stream_task()
                return

            yield (
                gr.update(),
                _render_status_panel(
                    "Temporary Connection Issue",
                    (
                        "Retrying while checking RunPod status.\n\n"
                        f"{consecutive_status_errors}/{MAX_CONSECUTIVE_STATUS_ERRORS}\n\n{err}"
                    ),
                    percent=last_overall_percent,
                    accent="#f59e0b",
                ),
                job_id,
            )
            await asyncio.sleep(RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S)
            continue

        consecutive_status_errors = 0
        state = (status.get("status") or "UNKNOWN").upper()
        has_final_output = _has_final_output_payload(status)

        if state in TERMINAL_FAILURES:
            error_message = _extract_error_message(status)
            tracker.fail(
                failure_reason=f"runpod_{state.lower()}",
                error_message=error_message,
                failure_stage="processing",
                progress_percent=int(last_runpod_progress or 0),
                worker_id=status.get("workerId"),
                metadata={"runpod_state": state},
            )
            yield (
                gr.update(),
                _render_status_panel("RunPod Error", error_message, accent="#f87171"),
                None,
            )
            _cancel_stream_task()
            return

        if state == "COMPLETED" or has_final_output:
            try:
                result_image = await _decode_output_image(status)
            except Exception as err:
                tracker.fail(
                    failure_reason="decode_error",
                    error_message=str(err),
                    failure_stage="output_collecting",
                    progress_percent=max(int(last_runpod_progress or 0), 96),
                    worker_id=status.get("workerId"),
                )
                yield (
                    gr.update(),
                    _render_status_panel("Decode Error", f"Failed to decode image: {err}", accent="#f87171"),
                    None,
                )
                _cancel_stream_task()
                return

            right_path = _save_temp_image(result_image, prefix="reference_generator_output")
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
                    "enhancement_enabled": bool(enhancement_enabled),
                    "color_match_enabled": bool(color_match_enabled),
                },
            )
            yield (
                (str(left_path), str(right_path)),
                _render_status_panel("Image generated", "Image generated", percent=100),
                None,
            )
            _cancel_stream_task()
            return

        status_runpod_progress, status_progress_text, _ = _extract_progress_signal(status)
        progress_events: list[tuple[int | float | None, str]] = []
        for stream_progress, stream_text, _stream_hints in stream_progress_entries:
            progress_events.append((stream_progress, stream_text))
        if status_progress_text:
            progress_events.append((status_runpod_progress, status_progress_text))

        effective_progress = status_runpod_progress
        if not isinstance(effective_progress, (int, float)):
            for stream_progress, _ in reversed(progress_events):
                if isinstance(stream_progress, (int, float)):
                    effective_progress = stream_progress
                    break

        for event_progress, progress_text in progress_events:
            if progress_text:
                last_progress_text = progress_text
                progress_tracker.observe_text(progress_text)
            if isinstance(event_progress, (int, float)):
                effective_progress = event_progress

        if isinstance(effective_progress, (int, float)):
            last_runpod_progress = effective_progress

        if has_final_output:
            progress_tracker.set_stage_progress(
                STAGE_SAVE,
                0.98,
                message="Finalizing output...",
                node_id=NODE_ENHANCEMENT_IMAGE_ROUTER,
            )
        elif state == "IN_QUEUE":
            progress_tracker["current_status"] = "Waiting for an available worker..."
        elif not progress_events and state in {"IN_PROGRESS", "RUNNING"}:
            if progress_tracker.get("current_stage") == STAGE_PREPARATION:
                progress_tracker["current_status"] = "Waiting for next workflow update..."
            elif last_progress_text:
                progress_tracker["current_status"] = last_progress_text

        overall_percent = max(
            last_overall_percent,
            progress_tracker.overall_percent(
                runpod_progress=last_runpod_progress,
            ),
        )
        last_overall_percent = overall_percent

        if state == "IN_QUEUE":
            status_panel = _render_reference_progress_panel(
                progress_tracker,
                overall_percent=max(overall_percent, 5),
                queued=True,
            )
            progress_percent = max(overall_percent, 5)
        else:
            status_panel = _render_reference_progress_panel(
                progress_tracker,
                overall_percent=overall_percent,
                queued=False,
            )
            progress_percent = overall_percent

        tracker.emit_processing(
            stage=str(progress_tracker.get("current_stage") or progress_tracker.get("phase") or "processing").lower(),
            message=str(progress_tracker.get("current_status") or last_progress_text or state.replace("_", " ").title()),
            progress_percent=progress_percent,
            metadata={
                "poll_idx": poll_idx,
                "runpod_state": state,
                "workflow": workflow,
                "phase": progress_tracker.get("phase"),
                "current_stage": progress_tracker.get("current_stage"),
                "last_node_id": progress_tracker.get("last_node_id"),
            },
        )
        yield gr.update(), status_panel, job_id
        await asyncio.sleep(RUNPOD_STATUS_POLL_INTERVAL_S)

    tracker.fail(
        failure_reason="timeout",
        error_message="Timed out waiting for RunPod completion status.",
        failure_stage="processing",
        progress_percent=int(last_runpod_progress or 0),
        worker_id=None,
    )
    yield (
        gr.update(),
        _render_status_panel("Timeout", "Timed out waiting for RunPod completion status.", accent="#f87171"),
        None,
    )
    _cancel_stream_task()


async def cancel_job(job_id: str | None) -> str:
    if not job_id:
        return _render_status_panel("Nothing To Cancel", "No active job ID was found.", accent="#f59e0b")

    api = RunpodAPI(environment=RUNPOD_ENVIRONMENT)
    try:
        await api.cancel(job_id)
    except Exception as err:
        return _render_status_panel("Cancel Error", f"Failed to cancel job: {err}", accent="#f87171")
    return _render_status_panel("Cancelled", "Job cancellation requested.", accent="#f59e0b")


with gr.Blocks(title=APP_TITLE, css=BOTTOM_PROGRESS_LAYOUT_CSS) as reference_generator_interface:
    gr.Markdown(f"## {TAB_TITLE}")

    workflow_name = gr.State(WORKFLOW_NAME)
    job_id_state = gr.State(None)

    with gr.Row(variant="panel"):
        main_image_input = gr.Image(label="Main Image", type="pil")
        reference_image_input = gr.Image(label="Reference Image", type="pil")

    with gr.Row(variant="panel"):
        with gr.Column(scale=2):
            workflow_debug_checkbox = gr.Checkbox(
                label="Workflow Debug (Admin only)",
                value=False,
                visible=False,
                info="Save the final manipulated workflow JSON sent to RunPod.",
            )
            color_strength = gr.Slider(
                minimum=0.0,
                maximum=1.0,
                step=0.01,
                value=0.9,
                label="Color Strength",
            )
            creativity = gr.Slider(
                minimum=0.0,
                maximum=1.0,
                step=0.01,
                value=0.5,
                label="Creativity",
            )
            structure_strength = gr.Slider(
                minimum=0.0,
                maximum=1.0,
                step=0.01,
                value=0.8,
                label="Structure Strength",
            )
            enhancement_toggle = gr.Checkbox(label="Enhancement", value=True)
            color_match_toggle = gr.Checkbox(
                label="Color Match",
                value=False,
                visible=True,
            )

        with gr.Column(scale=3):
            result_slider = ImageSlider(label="Result", type="filepath")

    enhancement_toggle.change(
        fn=_update_color_match_visibility,
        inputs=[enhancement_toggle],
        outputs=[color_match_toggle],
    )

    with gr.Row(elem_classes=["bottom-progress-row"]):
        progress_panel = gr.HTML(_render_idle_status())

    with gr.Row(elem_classes=["bottom-action-row"]):
        generate_btn = gr.Button("🌟 Generate", scale=3, variant="primary")
        cancel_btn = gr.Button("Cancel", variant="stop", scale=1)

    generate_event = generate_btn.click(
        fn=_disable_generate_button,
        inputs=None,
        outputs=[generate_btn],
        queue=False,
    )

    generate_event = generate_event.then(
        fn=reference_generator_generate,
        inputs=[
            main_image_input,
            reference_image_input,
            color_strength,
            creativity,
            structure_strength,
            enhancement_toggle,
            color_match_toggle,
            workflow_debug_checkbox,
            job_id_state,
            workflow_name,
        ],
        outputs=[result_slider, progress_panel, job_id_state],
        concurrency_limit=10,
        trigger_mode="once",
    )

    generate_event.then(
        fn=_enable_generate_button,
        inputs=None,
        outputs=[generate_btn],
        queue=False,
    )

    cancel_btn.click(cancel_job, inputs=job_id_state, outputs=progress_panel).then(
        fn=_enable_generate_button,
        inputs=None,
        outputs=[generate_btn],
        queue=False,
    )

    reference_generator_interface.load(
        fn=_debug_checkbox_visibility_update,
        inputs=None,
        outputs=[workflow_debug_checkbox],
    )


if __name__ == "__main__":
    reference_generator_interface.launch(
        server_name="0.0.0.0",
        server_port=8172,
        debug=APP_DEBUG,
        quiet=APP_QUIET,
        auth=auth_service.authenticate,
        auth_message="BrickVisual internal access only.",
    )
