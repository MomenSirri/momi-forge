from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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
from runpod_api_class import (
    RunpodAPI,
    RunpodSubmissionError,
    RunpodSubmissionUncertainError,
)
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


class ReferencePreparationError(RuntimeError):
    def __init__(self, title: str, message: str) -> None:
        super().__init__(message)
        self.title = title


class ReferencePayloadTooLargeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        payload_size: int,
        main_size: tuple[int, int],
        reference_size: tuple[int, int],
    ) -> None:
        super().__init__(message)
        self.metadata = {
            "payload_size_bytes": payload_size,
            "max_payload_bytes": REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES,
            "transport_main_size": list(main_size),
            "transport_reference_size": list(reference_size),
        }


class ReferenceRequestError(RuntimeError):
    def __init__(self, title: str, message: str) -> None:
        super().__init__(message)
        self.title = title


@dataclass
class ReferencePreparedInputs:
    workflow_key: str
    prompt: dict[str, Any]
    main_pil: Image.Image
    reference_pil: Image.Image
    main_transport_pil: Image.Image
    reference_transport_pil: Image.Image
    main_image_b64: str
    reference_image_b64: str
    task_id: str
    feature_flags: dict[str, Any]
    settings_snapshot: dict[str, Any]


@dataclass
class ReferencePreparedJob:
    inputs: ReferencePreparedInputs
    payload: dict[str, Any]
    workflow_debug_path: Path | None


@dataclass
class ReferenceRequestContext:
    inputs: ReferencePreparedInputs
    job: ReferencePreparedJob
    tracker: TaskTracker


@dataclass
class ReferenceSubmissionResult:
    job_id: str | None
    error_message: str | None = None
    uncertain: bool = False


@dataclass
class ReferenceFinalizedOutput:
    result_image: Image.Image | None = None
    left_path: Path | None = None
    right_path: Path | None = None
    artifacts: dict[str, Any] | None = None
    error_message: str | None = None


@dataclass
class ReferencePollEvent:
    kind: str
    status: dict[str, Any]
    title: str
    message: str
    progress_percent: int
    stage: str
    poll_idx: int
    finalized: ReferenceFinalizedOutput | None = None
    queued: bool = False
    tracker_error_message: str | None = None


@dataclass
class ReferencePollState:
    progress_tracker: ProgressTracker
    last_progress_text: str | None = None
    last_runpod_progress: int | float | None = None
    last_overall_percent: int = 3
    consecutive_status_errors: int = 0
    stream_seen_signatures: set[str] = field(default_factory=set)
    stream_seen_order: list[str] = field(default_factory=list)
    stream_task: asyncio.Task[dict[str, Any]] | None = None

    def cancel_stream(self) -> None:
        if self.stream_task is not None and not self.stream_task.done():
            self.stream_task.cancel()


def _prepare_reference_inputs(
    *,
    main_image: Any,
    reference_image: Any,
    color_strength: float,
    creativity: float,
    structure_strength: float,
    enhancement_enabled: bool,
    color_match_enabled: bool,
    workflow: str,
) -> ReferencePreparedInputs:
    if main_image is None or reference_image is None:
        raise ReferencePreparationError(
            "Input Error",
            "Both Main Image and Reference Image are required.",
        )

    try:
        prompt_path = _resolve_reference_generator_workflow_path()
        try:
            with open(prompt_path, "r", encoding="utf-8") as file:
                prompt: dict[str, Any] = json.load(file)
        except UnicodeDecodeError:
            with open(prompt_path, "r", encoding="cp1252") as file:
                prompt = json.load(file)
    except Exception as err:
        raise ReferencePreparationError(
            "Workflow Error",
            f"Prompt load failed: {err}",
        ) from err

    try:
        main_pil = _to_pil_image(main_image)
        reference_pil = _to_pil_image(reference_image)
        if main_pil.mode not in ("RGB", "RGBA"):
            main_pil = main_pil.convert("RGB")
        if reference_pil.mode not in ("RGB", "RGBA"):
            reference_pil = reference_pil.convert("RGB")
    except Exception as err:
        raise ReferencePreparationError(
            "Input Error",
            f"Image preparation failed: {err}",
        ) from err

    try:
        main_transport_pil = _resize_for_runpod_transport(main_pil)
        reference_transport_pil = _resize_for_runpod_transport(reference_pil)
        main_image_b64 = save_input_image_as_base64(main_transport_pil)
        reference_image_b64 = save_input_image_as_base64(reference_transport_pil)
    except Exception as err:
        raise ReferencePreparationError(
            "Encoding Error",
            f"Failed to encode input images: {err}",
        ) from err

    return ReferencePreparedInputs(
        workflow_key=str(workflow or WORKFLOW_NAME),
        prompt=prompt,
        main_pil=main_pil,
        reference_pil=reference_pil,
        main_transport_pil=main_transport_pil,
        reference_transport_pil=reference_transport_pil,
        main_image_b64=main_image_b64,
        reference_image_b64=reference_image_b64,
        task_id=str(uuid.uuid4()),
        feature_flags={
            "enhancement_enabled": bool(enhancement_enabled),
            "color_match_enabled": bool(color_match_enabled),
        },
        settings_snapshot={
            "color_strength": float(color_strength),
            "creativity": float(creativity),
            "structure_strength": float(structure_strength),
        },
    )


def _build_reference_payload(
    prepared: ReferencePreparedInputs,
    *,
    color_strength: float,
    creativity: float,
    structure_strength: float,
    enhancement_enabled: bool,
    color_match_enabled: bool,
    workflow_debug: bool,
    is_admin_user: bool,
) -> ReferencePreparedJob:
    _apply_reference_workflow_updates(
        prepared.prompt,
        main_image_b64=prepared.main_image_b64,
        reference_image_b64=prepared.reference_image_b64,
        color_strength=float(color_strength),
        creativity=float(creativity),
        structure_strength=float(structure_strength),
        enhancement_enabled=bool(enhancement_enabled),
        color_match_enabled=bool(color_match_enabled and enhancement_enabled),
    )
    payload = prepare_json(prepared.prompt, images=[])
    payload_size = _json_payload_size_bytes(payload)
    if payload_size > REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES:
        message = (
            "The RunPod request is too large to submit "
            f"({_format_byte_size(payload_size)}). "
            f"The current limit is "
            f"{_format_byte_size(REFERENCE_GENERATOR_MAX_PAYLOAD_BYTES)}. "
            "Use smaller main/reference images or lower "
            "REFERENCE_GENERATOR_MAX_UPLOAD_EDGE."
        )
        raise ReferencePayloadTooLargeError(
            message,
            payload_size=payload_size,
            main_size=prepared.main_transport_pil.size,
            reference_size=prepared.reference_transport_pil.size,
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
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)
    return ReferencePreparedJob(
        inputs=prepared,
        payload=payload,
        workflow_debug_path=workflow_debug_path,
    )


def _create_reference_task_tracker(
    prepared: ReferencePreparedInputs,
    *,
    identity: Any,
    user_agent: str | None,
    session_id: str,
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
        source_page="/tab/reference-generator",
        browser_user_agent=user_agent,
        session_id=session_id,
        environment_name=APP_ENVIRONMENT,
        feature_flags=prepared.feature_flags,
        settings=prepared.settings_snapshot,
        input_meta={
            "width": int(prepared.main_pil.width),
            "height": int(prepared.main_pil.height),
            "resolution": (
                f"{int(prepared.main_pil.width)}"
                f"x{int(prepared.main_pil.height)}"
            ),
            "format": str(prepared.main_pil.mode),
            "reference_width": int(prepared.reference_pil.width),
            "reference_height": int(prepared.reference_pil.height),
        },
        request_summary=prepared.feature_flags,
        prompt_type="reference_generation",
        created_by=identity.email,
    )


def _prepare_reference_request(
    *,
    main_image: Any,
    reference_image: Any,
    color_strength: float,
    creativity: float,
    structure_strength: float,
    enhancement_enabled: bool,
    color_match_enabled: bool,
    workflow_debug: bool,
    workflow: str,
    identity: Any,
    user_agent: str | None,
    session_id: str,
) -> ReferenceRequestContext:
    prepared = _prepare_reference_inputs(
        main_image=main_image,
        reference_image=reference_image,
        color_strength=color_strength,
        creativity=creativity,
        structure_strength=structure_strength,
        enhancement_enabled=enhancement_enabled,
        color_match_enabled=color_match_enabled,
        workflow=workflow,
    )
    tracker = _create_reference_task_tracker(
        prepared,
        identity=identity,
        user_agent=user_agent,
        session_id=session_id,
    )
    try:
        job = _build_reference_payload(
            prepared,
            color_strength=color_strength,
            creativity=creativity,
            structure_strength=structure_strength,
            enhancement_enabled=enhancement_enabled,
            color_match_enabled=color_match_enabled,
            workflow_debug=workflow_debug,
            is_admin_user=(
                str(getattr(identity, "role", "") or "")
                .strip()
                .lower()
                == "admin"
            ),
        )
    except Exception as err:
        if isinstance(err, ReferencePayloadTooLargeError):
            failure_reason = "request_payload_too_large"
            title = "Input Too Large"
            metadata = err.metadata
            message = str(err)
        else:
            failure_reason = (
                "workflow_key_missing"
                if isinstance(err, KeyError)
                else "workflow_update_error"
            )
            title = "Workflow Error"
            metadata = None
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
            metadata=metadata,
        )
        raise ReferenceRequestError(title, message) from err
    return ReferenceRequestContext(inputs=prepared, job=job, tracker=tracker)


async def _submit_reference_job(
    api: RunpodAPI,
    payload: dict[str, Any],
) -> ReferenceSubmissionResult:
    try:
        response = await api.run(payload)
        return ReferenceSubmissionResult(job_id=str(response["id"]))
    except RunpodSubmissionUncertainError as err:
        return ReferenceSubmissionResult(
            job_id=None,
            error_message=(
                f"{err}\n\nPlease check the Jobs page before trying again; "
                "RunPod may already have accepted this request."
            ),
            uncertain=True,
        )
    except RunpodSubmissionError as err:
        return ReferenceSubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )
    except Exception as err:
        return ReferenceSubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )


async def _finalize_reference_output(
    status: dict[str, Any],
    *,
    left_path: Path,
) -> ReferenceFinalizedOutput:
    try:
        result_image = await _decode_output_image(status)
        return ReferenceFinalizedOutput(
            result_image=result_image,
            left_path=left_path,
            right_path=_save_temp_image(
                result_image,
                prefix="reference_generator_output",
            ),
            artifacts=extract_artifacts_from_status(status),
        )
    except Exception as err:
        return ReferenceFinalizedOutput(error_message=str(err))


async def _advance_reference_stream(
    api: RunpodAPI,
    job_id: str,
    state: ReferencePollState,
    *,
    stream_enabled: bool,
) -> list[tuple[int | float | None, str, list[str]]]:
    entries: list[tuple[int | float | None, str, list[str]]] = []
    if not stream_enabled:
        return entries
    if state.stream_task is not None and state.stream_task.done():
        try:
            response = state.stream_task.result()
            entries, _ = _extract_stream_progress_signals(
                response,
                seen_signatures=state.stream_seen_signatures,
                seen_order=state.stream_seen_order,
            )
        except Exception as err:
            logger.debug("RunPod stream poll failed: %s", err)
        finally:
            state.stream_task = None
    if state.stream_task is None:
        state.stream_task = asyncio.create_task(api.stream(job_id))
    return entries


def _update_reference_poll_progress(
    status: dict[str, Any],
    stream_entries: list[tuple[int | float | None, str, list[str]]],
    state: ReferencePollState,
) -> ReferencePollEvent:
    runpod_progress, status_progress_text, _ = _extract_progress_signal(status)
    progress_events = [
        (stream_progress, stream_text)
        for stream_progress, stream_text, _ in stream_entries
    ]
    if status_progress_text:
        progress_events.append((runpod_progress, status_progress_text))

    effective_progress = runpod_progress
    if not isinstance(effective_progress, (int, float)):
        for stream_progress, _ in reversed(progress_events):
            if isinstance(stream_progress, (int, float)):
                effective_progress = stream_progress
                break
    for event_progress, progress_text in progress_events:
        if progress_text:
            state.last_progress_text = progress_text
            state.progress_tracker.observe_text(progress_text)
        if isinstance(event_progress, (int, float)):
            effective_progress = event_progress
    if isinstance(effective_progress, (int, float)):
        state.last_runpod_progress = effective_progress

    runpod_state = (status.get("status") or "UNKNOWN").upper()
    if runpod_state == "IN_QUEUE":
        state.progress_tracker["current_status"] = (
            "Waiting for an available worker..."
        )
    elif not progress_events and runpod_state in {"IN_PROGRESS", "RUNNING"}:
        if state.progress_tracker.get("current_stage") == STAGE_PREPARATION:
            state.progress_tracker["current_status"] = (
                "Waiting for next workflow update..."
            )
        elif state.last_progress_text:
            state.progress_tracker["current_status"] = state.last_progress_text

    overall_percent = max(
        state.last_overall_percent,
        state.progress_tracker.overall_percent(
            runpod_progress=state.last_runpod_progress,
        ),
    )
    state.last_overall_percent = overall_percent
    queued = runpod_state == "IN_QUEUE"
    return ReferencePollEvent(
        kind="progress",
        status=status,
        title=_reference_display_stage(
            state.progress_tracker,
            queued=queued,
        ),
        message=str(
            state.progress_tracker.get("current_status")
            or state.last_progress_text
            or runpod_state.replace("_", " ").title()
        ),
        progress_percent=max(overall_percent, 5) if queued else overall_percent,
        stage=str(
            state.progress_tracker.get("current_stage")
            or state.progress_tracker.get("phase")
            or "processing"
        ).lower(),
        poll_idx=0,
        queued=queued,
    )


async def _poll_reference_job(
    api: RunpodAPI,
    job_id: str,
    *,
    left_path: Path,
    state: ReferencePollState,
    stream_enabled: bool = RUNPOD_STREAM_ENABLED,
):
    try:
        for poll_idx in range(MAX_STATUS_POLLS):
            stream_entries = await _advance_reference_stream(
                api,
                job_id,
                state,
                stream_enabled=stream_enabled,
            )
            try:
                status = await api.status(job_id)
            except Exception as err:
                state.consecutive_status_errors += 1
                if (
                    state.consecutive_status_errors
                    > MAX_CONSECUTIVE_STATUS_ERRORS
                ):
                    yield ReferencePollEvent(
                        kind="status_error",
                        status={},
                        title="RunPod Error",
                        message=f"Failed to check job status: {err}",
                        progress_percent=int(
                            state.last_runpod_progress or 0
                        ),
                        stage="processing",
                        poll_idx=poll_idx,
                        tracker_error_message=str(err),
                    )
                    return
                yield ReferencePollEvent(
                    kind="retry",
                    status={},
                    title="Temporary Connection Issue",
                    message=(
                        "Retrying while checking RunPod status.\n\n"
                        f"{state.consecutive_status_errors}/"
                        f"{MAX_CONSECUTIVE_STATUS_ERRORS}\n\n{err}"
                    ),
                    progress_percent=state.last_overall_percent,
                    stage="processing",
                    poll_idx=poll_idx,
                )
                await asyncio.sleep(
                    RUNPOD_STATUS_ERROR_RETRY_INTERVAL_S
                )
                continue

            state.consecutive_status_errors = 0
            runpod_state = (status.get("status") or "UNKNOWN").upper()
            has_final_output = _has_final_output_payload(status)
            if runpod_state in TERMINAL_FAILURES:
                message = _extract_error_message(status)
                yield ReferencePollEvent(
                    kind="terminal_failure",
                    status=status,
                    title="RunPod Error",
                    message=message,
                    progress_percent=int(
                        state.last_runpod_progress or 0
                    ),
                    stage="processing",
                    poll_idx=poll_idx,
                    tracker_error_message=message,
                )
                return
            if runpod_state == "COMPLETED" or has_final_output:
                finalized = await _finalize_reference_output(
                    status,
                    left_path=left_path,
                )
                if finalized.error_message:
                    yield ReferencePollEvent(
                        kind="decode_error",
                        status=status,
                        title="Decode Error",
                        message=(
                            "Failed to decode image: "
                            f"{finalized.error_message}"
                        ),
                        progress_percent=max(
                            int(state.last_runpod_progress or 0),
                            96,
                        ),
                        stage="output_collecting",
                        poll_idx=poll_idx,
                        finalized=finalized,
                        tracker_error_message=finalized.error_message,
                    )
                else:
                    yield ReferencePollEvent(
                        kind="completed",
                        status=status,
                        title="Image generated",
                        message="Image generated",
                        progress_percent=100,
                        stage="completed",
                        poll_idx=poll_idx,
                        finalized=finalized,
                    )
                return

            event = _update_reference_poll_progress(
                status,
                stream_entries,
                state,
            )
            event.poll_idx = poll_idx
            yield event
            await asyncio.sleep(RUNPOD_STATUS_POLL_INTERVAL_S)

        yield ReferencePollEvent(
            kind="timeout",
            status={},
            title="Timeout",
            message="Timed out waiting for RunPod completion status.",
            progress_percent=int(state.last_runpod_progress or 0),
            stage="processing",
            poll_idx=MAX_STATUS_POLLS,
            tracker_error_message=(
                "Timed out waiting for RunPod completion status."
            ),
        )
    finally:
        state.cancel_stream()


def _record_reference_poll_event(
    tracker: TaskTracker,
    event: ReferencePollEvent,
    *,
    progress_tracker: ProgressTracker,
    enhancement_enabled: bool,
    color_match_enabled: bool,
    workflow: str,
) -> None:
    if event.kind == "retry":
        return
    if event.kind == "progress":
        tracker.emit_processing(
            stage=event.stage,
            message=event.message,
            progress_percent=event.progress_percent,
            metadata={
                "poll_idx": event.poll_idx,
                "runpod_state": event.status.get("status"),
                "workflow": workflow,
                "phase": progress_tracker.get("phase"),
                "current_stage": progress_tracker.get("current_stage"),
                "last_node_id": progress_tracker.get("last_node_id"),
            },
        )
        return
    if event.kind == "completed":
        finalized = event.finalized
        if (
            finalized is None
            or finalized.result_image is None
            or finalized.left_path is None
            or finalized.right_path is None
        ):
            raise ValueError(
                "Completed Reference event is missing finalized output."
            )
        artifacts = finalized.artifacts or {}
        thumbnail_path = tracker.add_thumbnail(
            image=finalized.result_image,
            output_index=0,
        )
        preview_path = tracker.add_preview(
            image=finalized.result_image,
            output_index=0,
        )
        output_filename = (
            artifacts.get("output_filename")
            or finalized.right_path.name
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
        tracker.complete(
            result_url=artifacts.get("result_url"),
            thumbnail_url=thumbnail_path,
            preview_url=preview_path,
            output_filename=output_filename,
            output_count=max(
                int(artifacts.get("output_count") or 0),
                1,
            ),
            output_width=finalized.result_image.width,
            output_height=finalized.result_image.height,
            worker_id=artifacts.get("worker_id"),
            result_summary={
                "left_path": str(finalized.left_path),
                "right_path": str(finalized.right_path),
                "runpod_state": event.status.get("status"),
                "enhancement_enabled": bool(enhancement_enabled),
                "color_match_enabled": bool(color_match_enabled),
            },
        )
        return

    failure_reason = {
        "status_error": "status_error",
        "terminal_failure": (
            f"runpod_{str(event.status.get('status') or 'unknown').lower()}"
        ),
        "decode_error": "decode_error",
        "timeout": "timeout",
    }.get(event.kind, event.kind)
    tracker.fail(
        failure_reason=failure_reason,
        error_message=event.tracker_error_message or event.message,
        failure_stage=event.stage,
        progress_percent=event.progress_percent,
        worker_id=event.status.get("workerId"),
        metadata=(
            {"runpod_state": event.status.get("status")}
            if event.kind == "terminal_failure"
            else None
        ),
    )


def _render_reference_poll_event(
    event: ReferencePollEvent,
    state: ReferencePollState,
    *,
    job_id: str,
) -> tuple[Any, str, str | None]:
    if event.kind == "progress":
        return (
            gr.update(),
            _render_reference_progress_panel(
                state.progress_tracker,
                overall_percent=event.progress_percent,
                queued=event.queued,
            ),
            job_id,
        )
    if event.kind == "retry":
        return (
            gr.update(),
            _render_status_panel(
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
            raise ValueError(
                "Completed Reference event is missing output paths."
            )
        return (
            (str(finalized.left_path), str(finalized.right_path)),
            _render_status_panel(
                event.title,
                event.message,
                percent=100,
            ),
            None,
        )
    return (
        gr.update(),
        _render_status_panel(
            event.title,
            event.message,
            accent="#f87171",
        ),
        None,
    )


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
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    try:
        context = _prepare_reference_request(
            main_image=main_image,
            reference_image=reference_image,
            color_strength=color_strength,
            creativity=creativity,
            structure_strength=structure_strength,
            enhancement_enabled=enhancement_enabled,
            color_match_enabled=color_match_enabled,
            workflow_debug=workflow_debug,
            workflow=workflow,
            identity=identity,
            user_agent=user_agent,
            session_id=session_id,
        )
    except ReferencePreparationError as err:
        yield (
            gr.update(),
            _render_status_panel(err.title, str(err), accent="#f87171"),
            None,
        )
        return
    except ReferenceRequestError as err:
        yield (
            gr.update(),
            _render_status_panel(err.title, str(err), accent="#f87171"),
            None,
        )
        return

    prepared_inputs = context.inputs
    prepared_job = context.job
    tracker = context.tracker

    api = RunpodAPI(environment=RUNPOD_ENVIRONMENT)
    submission = await _submit_reference_job(api, prepared_job.payload)
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
        )
        yield (
            gr.update(),
            _render_status_panel(
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
    submitted_message = "Preparing images"
    if prepared_job.workflow_debug_path is not None:
        submitted_message += (
            f"\n\nDebug JSON saved: {prepared_job.workflow_debug_path}"
        )
    yield (
        gr.update(),
        _render_status_panel(
            "Preparing images",
            submitted_message,
            percent=3,
        ),
        job_id,
    )

    left_path = _save_temp_image(
        prepared_inputs.main_pil,
        prefix="reference_generator_main",
    )
    poll_state = ReferencePollState(
        progress_tracker=_init_reference_progress_tracker(
            enhancement_enabled=bool(enhancement_enabled),
            color_match_enabled=bool(
                color_match_enabled and enhancement_enabled
            ),
        )
    )
    poll_state.progress_tracker["current_status"] = (
        "Job submitted. Waiting for worker updates..."
    )
    async for event in _poll_reference_job(
        api,
        job_id,
        left_path=left_path,
        state=poll_state,
    ):
        _record_reference_poll_event(
            tracker,
            event,
            progress_tracker=poll_state.progress_tracker,
            enhancement_enabled=enhancement_enabled,
            color_match_enabled=color_match_enabled,
            workflow=workflow,
        )
        yield _render_reference_poll_event(
            event,
            poll_state,
            job_id=job_id,
        )
        if event.kind not in {"progress", "retry"}:
            return


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
