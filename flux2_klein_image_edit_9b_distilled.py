from __future__ import annotations

import asyncio
from dataclasses import dataclass
import html
import json
import logging
import os
import random
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("RUNPOD_POD_ID_FLUX2_KLEIN", "usdkzwazqh749m")

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

_app_log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _app_log_level, logging.INFO))
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("gradio").setLevel(logging.WARNING)

APP_TITLE = "Momi Forge"
WORKFLOW_NAME = os.getenv(
    "FLUX2_KLEIN_WORKFLOW_NAME",
    "flux2_klein_image_edit_9b_distilled_02",
)
WORKFLOW_FILE = os.getenv(
    "FLUX2_KLEIN_WORKFLOW_FILE",
    f"{WORKFLOW_NAME}.json",
)
REALISTIC_WORKFLOW_NAME = os.getenv(
    "FLUX2_KLEIN_REALISTIC_WORKFLOW_NAME",
    "flux2_klein_realistic",
)
REALISTIC_WORKFLOW_FILE = os.getenv(
    "FLUX2_KLEIN_REALISTIC_WORKFLOW_FILE",
    f"{REALISTIC_WORKFLOW_NAME}.json",
)
WORKFLOW_VERSION = os.getenv("WORKFLOW_VERSION_FLUX2_KLEIN", "distilled")
WORKFLOW_CATEGORY = os.getenv("WORKFLOW_CATEGORY_FLUX2_KLEIN", "image_edit")
WORKFLOW_TYPE = os.getenv("WORKFLOW_TYPE_FLUX2_KLEIN", "image")
RUNPOD_ENVIRONMENT = os.getenv("FLUX2_KLEIN_RUNPOD_ENVIRONMENT", "flux2_klein")
APP_ENVIRONMENT = os.getenv("FLUX2_KLEIN_APP_ENVIRONMENT", RUNPOD_ENVIRONMENT)
APP_DEBUG = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_QUIET = os.getenv("APP_QUIET", "1").strip().lower() in {"1", "true", "yes", "on"}
RUNPOD_STATUS_POLL_INTERVAL_S = max(
    0.1,
    float(os.getenv("RUNPOD_STATUS_POLL_INTERVAL_S", "0.4")),
)
MAX_STATUS_POLLS = int(os.getenv("RUNPOD_MAX_STATUS_POLLS", "1800"))
PROMPT_LIBRARY_PATH = Path(
    os.getenv(
        "FLUX2_KLEIN_PROMPT_LIBRARY_PATH",
        str(Path(__file__).resolve().parent / "prompt_library.json"),
    )
)
PROMPT_LIBRARY_ALL_CATEGORY = "all"

TERMINAL_FAILURES = {"FAILED", "ERROR", "TIMED_OUT", "CANCELLED"}
MODE_EDIT = "Edit"
MODE_REFERENCE_TRANSFER = "Reference Transfer"
MODE_CONSISTENCY = "Consistency"
MODE_RAW_ENHANCEMENT = "Raw Enhancement"
MODE_REALISTIC = "Realistic"
MODE_CHOICES = [
    MODE_EDIT,
    MODE_REFERENCE_TRANSFER,
    MODE_CONSISTENCY,
    MODE_RAW_ENHANCEMENT,
    MODE_REALISTIC,
]
IMAGE_COUNT_CHOICES = ["1", "2", "3"]
MODE_TO_FIXED_IMAGE_COUNT = {
    MODE_REFERENCE_TRANSFER: 2,
    MODE_CONSISTENCY: 1,
    MODE_RAW_ENHANCEMENT: 1,
    MODE_REALISTIC: 1,
}
MODE_TO_LORA = {
    MODE_REFERENCE_TRANSFER: "Klein_ref_transfer_02.safetensors",
    MODE_CONSISTENCY: "Klein-consistency.safetensors",
    MODE_RAW_ENHANCEMENT: "Klein_9B_bvfinish_v01.safetensors",
}

NODE_IMAGE_1 = "76"
NODE_IMAGE_2 = "121"
NODE_IMAGE_3 = "165"
NODE_CFG_GUIDER = "145"
NODE_POSITIVE_1 = "150"
NODE_NEGATIVE_1 = "148"
NODE_POSITIVE_2 = "159"
NODE_NEGATIVE_2 = "157"
NODE_POSITIVE_3 = "164"
NODE_NEGATIVE_3 = "162"
NODE_BASE_MODEL = "142"
NODE_POSITIVE_TEXT = "154"
NODE_NEGATIVE_TEXT = "161"
NODE_LORA = "167"
NODE_QWEN = "168"
NODE_STRING_FUNCTION = "169"
NODE_NOISE = "141"
NODE_MAIN_IMAGE_SCALE = "151"
NODE_REFERENCE_IMAGE_SCALE = "160"
NODE_THIRD_IMAGE_SCALE = "166"
NODE_PADDED_IMAGE_1 = "174"
NODE_PADDED_IMAGE_2 = "178"
NODE_PADDED_IMAGE_3 = "180"
NODE_FINAL_CROP = "182"
NODE_SAVE_IMAGE = "137"
NODE_VAE_DECODE = "140"

REALISTIC_NODE_IMAGE = "76"
REALISTIC_NODE_POSITIVE_TEXT = "163"
REALISTIC_NODE_NOISE = "176"
REALISTIC_NODE_LORA = "179"

SAMPLER_PROGRESS_PATTERN = re.compile(r"node=(?P<node>[^ ]+)\s+(?P<done>\d+)/(?P<total>\d+)")
FRACTION_PATTERN = re.compile(r"(?P<done>\d+)/(?P<total>\d+)")
RUNNING_NODE_PATTERN = re.compile(r"Running node (?P<node>\d+(?::\d+)?):\s*(?P<label>.+)$")

REFERENCE_TRANSFER_QWEN_PROMPT = """Your task is to describe the image in three parts:

Mood: one word (e.g., sunset, night, overcast, rainy)

Sky: two words

Lighting: two words

Format: mood, sky sky, light light

Example: sunset, clear desaturated, golden soft"""

RAW_ENHANCEMENT_QWEN_PROMPT = """You are generating captions for training a LoRA that enhances raw architectural renders into high-quality, photorealistic architectural visualizations.

Your task is to describe the final enhanced image as a polished architectural result, not the editing process.

Instructions:

1. Describe the architectural scene clearly and concisely:
   - building type, such as modern villa, apartment complex, office interior
   - view type, such as exterior, interior, aerial, street-level, courtyard, lobby
   - key materials, such as concrete, glass, wood, stone
   - environment, such as landscaped garden, urban street, vegetation, furniture
   - lighting and time of day, such as soft daylight, overcast, dusk, warm interior lighting
   - sky color, such as blue sky, white sky, black starless sky

2. Always describe the image as a high-quality final architectural visualization, using consistent phrases such as:
   - polished architectural visualization
   - photoreal finish
   - natural color grading
   - realistic materials
   - believable lighting
   - refined vegetation
   - premium archviz quality

3. Do not describe editing actions or software processes.
   Avoid phrases like:
   - photoshop enhanced
   - increased contrast
   - boosted vibrance
   - color corrected

4. Do not mention that the image was previously raw or unfinished.

5. Keep the caption as a single comma-separated sentence.

6. Keep the length moderate, around 12–20 words.

7. Maintain consistent wording across captions for the final-look qualities, while varying the scene description.

8. Always include the trigger token at the beginning:

bvfinish

Output format:

Return only the caption. Do not include explanations.

Example outputs:

bvfinish, modern villa exterior, concrete and wood facade, landscaped garden, soft daylight, polished architectural visualization, photoreal finish, natural color grading, realistic materials, believable lighting

bvfinish, office lobby interior, stone flooring, wood wall panels, reception desk, warm indirect lighting, premium architectural visualization, realistic materials, natural color grading, refined lighting

bvfinish, residential apartment courtyard, balconies, vegetation, pedestrian path, overcast daylight, polished archviz render, photoreal finish, balanced color, realistic foliage"""

MODE_HINTS = {
    MODE_EDIT: """**Edit Mode**

Edit Mode is the general-purpose mode for adding, removing, or changing elements. It can work with up to three inputs. It is ideal for tasks such as adding or replacing people, adding accessories, changing the mood, or changing the style.

Use natural instruction language and keep negative prompts to a minimum.""",
    MODE_REFERENCE_TRANSFER: """**Reference Transfer**

Add your main image in the first image slot and your reference image in the second image slot. No prompt is needed in this mode.""",
    MODE_CONSISTENCY: """**Consistency**

This is an edit mode that works with only one image and keeps the result highly consistent with the original.

It is best used when you want to make controlled changes without strongly altering the image structure.

It usually works well for simple improvements such as color, lighting, adding details, removing small elements, and style adjustments.""",
    MODE_RAW_ENHANCEMENT: """**Raw Enhancement**

This mode helps improve your raw render in terms of color and detail. It aims to make the image as realistic as possible.""",
    MODE_REALISTIC: """**Realistic**

Use one image and describe the desired realistic result in the prompt. Adjust **Realistic Mode** to control the strength of the realism LoRA.""",
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


def _load_prompt_library(path: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    with open(path, "r", encoding="utf-8") as prompt_library_file:
        payload = json.load(prompt_library_file)

    raw_categories = payload.get("categories") if isinstance(payload, dict) else None
    raw_presets = payload.get("presets") if isinstance(payload, dict) else None
    if not isinstance(raw_categories, list) or not isinstance(raw_presets, list):
        raise ValueError("Prompt library JSON must contain 'categories' and 'presets' lists.")

    categories: list[dict[str, str]] = []
    category_ids: set[str] = set()
    for raw_category in raw_categories:
        if not isinstance(raw_category, dict):
            raise ValueError("Every prompt library category must be an object.")
        category_id = str(raw_category.get("id") or "").strip()
        category_title = str(raw_category.get("title") or "").strip()
        if not category_id or not category_title:
            raise ValueError("Prompt library categories require non-empty 'id' and 'title' fields.")
        if category_id in category_ids:
            raise ValueError(f"Duplicate prompt library category id: {category_id}")
        category_ids.add(category_id)
        categories.append({"id": category_id, "title": category_title})

    presets: list[dict[str, Any]] = []
    preset_ids: set[str] = set()
    required_fields = ("id", "title", "category", "description", "prompt")
    for raw_preset in raw_presets:
        if not isinstance(raw_preset, dict):
            raise ValueError("Every prompt library preset must be an object.")
        preset = {field: str(raw_preset.get(field) or "").strip() for field in required_fields}
        missing_fields = [field for field in required_fields if not preset[field]]
        if missing_fields:
            raise ValueError(
                f"Prompt library preset is missing required fields: {', '.join(missing_fields)}"
            )
        if preset["id"] in preset_ids:
            raise ValueError(f"Duplicate prompt library preset id: {preset['id']}")
        if preset["category"] not in category_ids:
            raise ValueError(
                f"Prompt library preset '{preset['id']}' uses unknown category "
                f"'{preset['category']}'."
            )
        raw_tags = raw_preset.get("tags") or []
        if not isinstance(raw_tags, list):
            raise ValueError(f"Prompt library preset '{preset['id']}' tags must be a list.")
        preset["tags"] = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
        preset_ids.add(preset["id"])
        presets.append(preset)

    category_order = {category["id"]: index for index, category in enumerate(categories)}
    presets.sort(key=lambda preset: (category_order[preset["category"]], preset["title"].casefold()))
    return categories, presets


PROMPT_LIBRARY_CATEGORIES, PROMPT_LIBRARY_PRESETS = _load_prompt_library(PROMPT_LIBRARY_PATH)
PROMPT_LIBRARY_CATEGORY_CHOICES = [
    ("All Categories", PROMPT_LIBRARY_ALL_CATEGORY),
    *((category["title"], category["id"]) for category in PROMPT_LIBRARY_CATEGORIES),
]
PROMPT_LIBRARY_PRESETS_BY_ID = {
    preset["id"]: preset for preset in PROMPT_LIBRARY_PRESETS
}
PROMPT_LIBRARY_PRESET_CHOICES = [
    (preset["title"], preset["id"]) for preset in PROMPT_LIBRARY_PRESETS
]


def _prompt_library_presets_for_category(category_id: str) -> list[dict[str, Any]]:
    normalized_category = str(category_id or PROMPT_LIBRARY_ALL_CATEGORY).strip()
    return [
        preset
        for preset in PROMPT_LIBRARY_PRESETS
        if (
            normalized_category == PROMPT_LIBRARY_ALL_CATEGORY
            or preset["category"] == normalized_category
        )
    ]


def _prompt_library_category_update(category_id: str):
    matches = _prompt_library_presets_for_category(category_id)
    selected_preset = matches[0] if matches else None
    return (
        gr.update(
            choices=[(preset["title"], preset["id"]) for preset in matches],
            value=selected_preset["id"] if selected_preset else None,
        ),
        selected_preset["prompt"] if selected_preset else "",
    )


def _apply_prompt_library_preset(preset_id: str | None):
    preset = PROMPT_LIBRARY_PRESETS_BY_ID.get(str(preset_id or ""))
    return preset["prompt"] if preset is not None else ""

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
        prefix="flux2_klein",
    )


def _resolve_flux2_klein_workflow_path(workflow: str | None = None) -> Path:
    is_realistic_workflow = workflow == REALISTIC_WORKFLOW_NAME
    configured_path_env = (
        "FLUX2_KLEIN_REALISTIC_WORKFLOW_PATH"
        if is_realistic_workflow
        else "FLUX2_KLEIN_WORKFLOW_PATH"
    )
    configured_path = os.getenv(configured_path_env, "").strip()
    if configured_path:
        path = Path(configured_path)
        if path.exists():
            return path

    workflow_file = REALISTIC_WORKFLOW_FILE if is_realistic_workflow else WORKFLOW_FILE
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
        f"Could not find workflow file '{workflow_file}' in the expected api_workflow folders."
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
        "Choose a mode, upload the required image inputs, and run the workflow.",
        percent=0,
    )


def _describe_progress_status(
    state: str,
    progress_text: str | None,
    *,
    has_final_output: bool,
    runpod_progress: int | float | None = None,
) -> tuple[str, str, int]:
    normalized_state = (state or "IN_PROGRESS").upper()
    percent = 5 if normalized_state == "IN_QUEUE" else 12
    if normalized_state == "IN_QUEUE":
        title = "Preparing image"
        message = "Preparing image"
    else:
        title = "Sampling / generating image"
        message = "Sampling / generating image"
    text = (progress_text or "").strip()

    if text:
        sampler_match = SAMPLER_PROGRESS_PATTERN.search(text)
        if sampler_match:
            done = int(sampler_match.group("done"))
            total = max(int(sampler_match.group("total")), 1)
            percent = max(percent, min(92, int(round(20 + (done / total) * 65))))
            title = "Sampling / generating image"
            message = "Sampling / generating image"
        else:
            fraction_match = FRACTION_PATTERN.search(text)
            if fraction_match and normalized_state != "IN_QUEUE":
                done = int(fraction_match.group("done"))
                total = max(int(fraction_match.group("total")), 1)
                percent = max(percent, min(90, int(round(18 + (done / total) * 62))))
                title = "Sampling / generating image"
                message = "Sampling / generating image"

        running_match = RUNNING_NODE_PATTERN.match(text)
        if running_match:
            percent = max(percent, 18)
        elif "execution finished" in text.lower():
            title = "Image generated"
            message = "Image generated"
            percent = max(percent, 94)
        elif (
            "fetching execution history" in text.lower()
            or "collecting image" in text.lower()
            or "collecting output" in text.lower()
        ):
            title = "Saving output"
            message = "Saving output"
            percent = max(percent, 95)
        elif "job completed. returning" in text.lower():
            title = "Saving output"
            message = "Saving output"
            percent = max(percent, 96)

    if isinstance(runpod_progress, (int, float)):
        percent = max(percent, max(0, min(int(float(runpod_progress)), 100)))

    if has_final_output:
        title = "Saving output"
        message = "Saving output"
        percent = max(percent, 96)

    return title, message, percent


def _render_progress_status(
    state: str,
    progress_text: str | None,
    *,
    has_final_output: bool,
    runpod_progress: int | float | None = None,
) -> str:
    title, message, percent = _describe_progress_status(
        state,
        progress_text,
        has_final_output=has_final_output,
        runpod_progress=runpod_progress,
    )
    return _render_status_panel(title, message, percent=percent)


def _processing_stage_name(state: str, *, has_final_output: bool) -> str:
    normalized_state = (state or "").upper()
    if has_final_output:
        return "wrap_up"
    if normalized_state == "IN_QUEUE":
        return "queued"
    if normalized_state in {"IN_PROGRESS", "RUNNING"}:
        return "processing"
    return normalized_state.lower() or "processing"


def _disable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=False)


def _enable_generate_button() -> dict[str, Any]:
    return gr.update(interactive=True)


def _connect(
    prompt: dict[str, Any],
    target_node: str,
    input_name: str,
    source_node: str,
    output_idx: int = 0,
) -> None:
    prompt[target_node]["inputs"][input_name] = [source_node, output_idx]


def _effective_image_count(mode: str, image_count: str | int | None) -> int:
    if mode in MODE_TO_FIXED_IMAGE_COUNT:
        return MODE_TO_FIXED_IMAGE_COUNT[mode]
    try:
        count = int(image_count or 1)
    except (TypeError, ValueError):
        count = 1
    return max(1, min(count, 3))


def _apply_conditioning_routing(prompt: dict[str, Any], image_count: int) -> None:
    if image_count <= 1:
        _connect(prompt, NODE_CFG_GUIDER, "positive", NODE_POSITIVE_1)
        _connect(prompt, NODE_CFG_GUIDER, "negative", NODE_NEGATIVE_1)
        return

    if image_count == 2:
        _connect(prompt, NODE_CFG_GUIDER, "positive", NODE_POSITIVE_2)
        _connect(prompt, NODE_CFG_GUIDER, "negative", NODE_NEGATIVE_2)
        _connect(prompt, NODE_POSITIVE_2, "conditioning", NODE_POSITIVE_1)
        _connect(prompt, NODE_NEGATIVE_2, "conditioning", NODE_NEGATIVE_1)
        return

    _connect(prompt, NODE_CFG_GUIDER, "positive", NODE_POSITIVE_3)
    _connect(prompt, NODE_CFG_GUIDER, "negative", NODE_NEGATIVE_3)
    _connect(prompt, NODE_POSITIVE_2, "conditioning", NODE_POSITIVE_1)
    _connect(prompt, NODE_NEGATIVE_2, "conditioning", NODE_NEGATIVE_1)
    _connect(prompt, NODE_POSITIVE_3, "conditioning", NODE_POSITIVE_2)
    _connect(prompt, NODE_NEGATIVE_3, "conditioning", NODE_NEGATIVE_2)


def _apply_mode_routing(prompt: dict[str, Any], *, mode: str, prompt_text: str) -> None:
    cleaned_prompt = str(prompt_text or "").strip()
    prompt[NODE_NEGATIVE_TEXT]["inputs"]["text"] = ""
    _connect(prompt, NODE_QWEN, "image", NODE_MAIN_IMAGE_SCALE)

    if mode == MODE_EDIT:
        _connect(prompt, NODE_CFG_GUIDER, "model", NODE_BASE_MODEL)
        prompt[NODE_POSITIVE_TEXT]["inputs"]["text"] = cleaned_prompt
        return

    prompt[NODE_LORA]["inputs"]["lora_name"] = MODE_TO_LORA[mode]
    _connect(prompt, NODE_CFG_GUIDER, "model", NODE_LORA)

    if mode == MODE_REFERENCE_TRANSFER:
        _connect(prompt, NODE_QWEN, "image", NODE_REFERENCE_IMAGE_SCALE)
        prompt[NODE_QWEN]["inputs"]["custom_prompt"] = REFERENCE_TRANSFER_QWEN_PROMPT
        prompt[NODE_STRING_FUNCTION]["inputs"]["text_a"] = "Change the mood and lighting of Image 1 to "
        prompt[NODE_STRING_FUNCTION]["inputs"]["text_b"] = [NODE_QWEN, 0]
        prompt[NODE_STRING_FUNCTION]["inputs"]["text_c"] = (
            " to match Image 2, specifically the light direction, shadows, and contrast, "
            "while keeping all details in Image 1 exactly the same."
        )
        prompt[NODE_POSITIVE_TEXT]["inputs"]["text"] = [NODE_STRING_FUNCTION, 0]
        return

    if mode == MODE_CONSISTENCY:
        prompt[NODE_QWEN]["inputs"]["custom_prompt"] = ""
        prompt[NODE_POSITIVE_TEXT]["inputs"]["text"] = cleaned_prompt
        return

    prompt[NODE_QWEN]["inputs"]["custom_prompt"] = RAW_ENHANCEMENT_QWEN_PROMPT
    prompt[NODE_POSITIVE_TEXT]["inputs"]["text"] = [NODE_QWEN, 0]


def _has_workflow_node(prompt: dict[str, Any], node_id: str) -> bool:
    return isinstance(prompt.get(node_id), dict) and isinstance(prompt[node_id].get("inputs"), dict)


def _set_node_input_if_present(
    prompt: dict[str, Any],
    node_id: str,
    input_name: str,
    value: Any,
) -> None:
    if _has_workflow_node(prompt, node_id):
        prompt[node_id]["inputs"][input_name] = value


def _has_padding_crop_nodes(prompt: dict[str, Any]) -> bool:
    required_nodes = {
        "147",
        "170",
        "177",
        "179",
        "181",
        NODE_PADDED_IMAGE_1,
        NODE_PADDED_IMAGE_2,
        NODE_PADDED_IMAGE_3,
        NODE_FINAL_CROP,
    }
    return all(_has_workflow_node(prompt, node_id) for node_id in required_nodes)


def _apply_padding_crop_routing(prompt: dict[str, Any], *, mode: str, image_count: int) -> None:
    _connect(prompt, NODE_MAIN_IMAGE_SCALE, "image", NODE_IMAGE_1)
    _connect(prompt, "170", "image", NODE_MAIN_IMAGE_SCALE)
    _connect(prompt, NODE_PADDED_IMAGE_1, "image", NODE_IMAGE_1)
    _connect(prompt, "147", "image", NODE_PADDED_IMAGE_1)
    _connect(prompt, "149", "pixels", NODE_PADDED_IMAGE_1)

    if image_count >= 2:
        _connect(prompt, NODE_REFERENCE_IMAGE_SCALE, "image", NODE_IMAGE_2)
        _connect(prompt, "177", "image", NODE_REFERENCE_IMAGE_SCALE)
        _connect(prompt, NODE_PADDED_IMAGE_2, "image", NODE_IMAGE_2)
        _connect(prompt, "158", "pixels", NODE_PADDED_IMAGE_2)
    if image_count >= 3:
        _connect(prompt, NODE_THIRD_IMAGE_SCALE, "image", NODE_IMAGE_3)
        _connect(prompt, "179", "image", NODE_THIRD_IMAGE_SCALE)
        _connect(prompt, NODE_PADDED_IMAGE_3, "image", NODE_IMAGE_3)
        _connect(prompt, "163", "pixels", NODE_PADDED_IMAGE_3)

    if mode == MODE_REFERENCE_TRANSFER and image_count >= 2:
        _connect(prompt, NODE_QWEN, "image", NODE_PADDED_IMAGE_2)
    else:
        _connect(prompt, NODE_QWEN, "image", NODE_PADDED_IMAGE_1)

    if _has_workflow_node(prompt, "181"):
        _connect(prompt, "181", "image", NODE_IMAGE_1)
    if _has_workflow_node(prompt, NODE_FINAL_CROP):
        _connect(prompt, NODE_FINAL_CROP, "image", NODE_VAE_DECODE)
        _set_node_input_if_present(prompt, NODE_FINAL_CROP, "multiple_of", 1)
    if _has_workflow_node(prompt, NODE_SAVE_IMAGE):
        _connect(prompt, NODE_SAVE_IMAGE, "images", NODE_FINAL_CROP)


def _apply_flux2_klein_workflow_updates(
    prompt: dict[str, Any],
    *,
    mode: str,
    image_count: int,
    prompt_text: str,
    image_names: list[str],
    realistic_strength: float = 0.5,
    workflow: str | None = None,
) -> None:
    if mode == MODE_REALISTIC:
        prompt[REALISTIC_NODE_IMAGE]["inputs"]["image"] = image_names[0]
        prompt[REALISTIC_NODE_POSITIVE_TEXT]["inputs"]["text"] = str(prompt_text or "").strip()
        prompt[REALISTIC_NODE_NOISE]["inputs"]["noise_seed"] = random.randint(0, 999_999_999_999)
        prompt[REALISTIC_NODE_LORA]["inputs"]["strength_model"] = max(
            0.0,
            min(float(realistic_strength), 1.0),
        )
        return

    prompt[NODE_IMAGE_1]["inputs"]["image"] = image_names[0]
    if len(image_names) > 1:
        prompt[NODE_IMAGE_2]["inputs"]["image"] = image_names[1]
    if len(image_names) > 2:
        prompt[NODE_IMAGE_3]["inputs"]["image"] = image_names[2]

    prompt[NODE_NOISE]["inputs"]["noise_seed"] = random.randint(0, 999_999_999_999)
    _apply_conditioning_routing(prompt, image_count)
    _apply_mode_routing(prompt, mode=mode, prompt_text=prompt_text)
    if _has_padding_crop_nodes(prompt):
        _apply_padding_crop_routing(prompt, mode=mode, image_count=image_count)


def _crop_to_dimensions(image: Image.Image, width: int, height: int) -> Image.Image:
    target_width = max(1, int(width))
    target_height = max(1, int(height))
    if image.width == target_width and image.height == target_height:
        return image

    if image.width < target_width or image.height < target_height:
        canvas = Image.new(image.mode, (target_width, target_height))
        offset_x = max((target_width - image.width) // 2, 0)
        offset_y = max((target_height - image.height) // 2, 0)
        canvas.paste(image, (offset_x, offset_y))
        return canvas

    left = max((image.width - target_width) // 2, 0)
    top = max((image.height - target_height) // 2, 0)
    return image.crop((left, top, left + target_width, top + target_height))


def _save_temp_image(image: Image.Image, *, prefix: str) -> Path:
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix).strip("_") or "image"
    with tempfile.NamedTemporaryFile(
        prefix=f"{safe_prefix}_",
        suffix=".png",
        delete=False,
    ) as tmp:
        image.save(tmp.name, format="PNG")
        return Path(tmp.name)


def _validate_mode_inputs(
    *,
    mode: str,
    image_count: int,
    image_1: Any,
    image_2: Any,
    image_3: Any,
) -> None:
    if image_1 is None:
        raise ValueError("Image 1 is required.")
    if image_count >= 2 and image_2 is None:
        if mode == MODE_REFERENCE_TRANSFER:
            raise ValueError("Reference Transfer mode requires two image inputs.")
        raise ValueError("Image 2 is required for the selected Edit image count.")
    if image_count >= 3 and image_3 is None:
        raise ValueError("Image 3 is required for the selected Edit image count.")


def _mode_controls_update(mode: str, image_count: str | int | None):
    effective_count = _effective_image_count(mode, image_count)
    return (
        gr.update(visible=mode == MODE_EDIT, value=str(effective_count)),
        gr.update(visible=mode != MODE_REFERENCE_TRANSFER),
        gr.update(visible=mode == MODE_REALISTIC),
        gr.update(visible=True),
        gr.update(visible=effective_count >= 2),
        gr.update(visible=effective_count >= 3),
        MODE_HINTS.get(mode, MODE_HINTS[MODE_EDIT]),
    )


def _edit_count_update(mode: str, image_count: str | int | None):
    effective_count = _effective_image_count(mode, image_count)
    return (
        gr.update(visible=True),
        gr.update(visible=effective_count >= 2),
        gr.update(visible=effective_count >= 3),
    )


class Flux2PreparationError(RuntimeError):
    def __init__(self, title: str, message: str) -> None:
        super().__init__(message)
        self.title = title


@dataclass
class Flux2PreparedInputs:
    workflow_key: str
    image_count: int
    prompt: dict[str, Any]
    pil_images: list[Image.Image]
    input_paths: list[Path]
    image_names: list[str]
    image_payload: list[dict[str, Any]]
    uses_padding_crop: bool
    task_id: str
    feature_flags: dict[str, Any]
    settings_snapshot: dict[str, Any]


@dataclass
class Flux2PreparedJob:
    inputs: Flux2PreparedInputs
    payload: dict[str, Any]
    workflow_debug_path: Path | None


@dataclass
class Flux2SubmissionResult:
    job_id: str | None
    error_message: str | None = None
    uncertain: bool = False


@dataclass
class Flux2FinalizedOutput:
    result_image: Image.Image | None = None
    left_path: Path | None = None
    right_path: Path | None = None
    artifacts: dict[str, Any] | None = None
    error_message: str | None = None


@dataclass
class Flux2PollEvent:
    kind: str
    status: dict[str, Any]
    title: str
    message: str
    progress_percent: int
    stage: str
    poll_idx: int
    finalized: Flux2FinalizedOutput | None = None
    runpod_progress: int | float | None = None
    progress_text: str | None = None


def _prepare_flux2_inputs(
    *,
    mode: str,
    edit_image_count: str | int | None,
    image_1: Any,
    image_2: Any,
    image_3: Any,
    prompt_text: str,
    realistic_strength: float,
    workflow: str,
) -> Flux2PreparedInputs:
    workflow_key = (
        REALISTIC_WORKFLOW_NAME
        if mode == MODE_REALISTIC
        else str(workflow or WORKFLOW_NAME)
    )
    image_count = _effective_image_count(mode, edit_image_count)
    try:
        _validate_mode_inputs(
            mode=mode,
            image_count=image_count,
            image_1=image_1,
            image_2=image_2,
            image_3=image_3,
        )
    except Exception as err:
        raise Flux2PreparationError("Input Error", str(err)) from err

    try:
        prompt_path = _resolve_flux2_klein_workflow_path(workflow_key)
        try:
            with open(prompt_path, "r", encoding="utf-8") as file:
                prompt: dict[str, Any] = json.load(file)
        except UnicodeDecodeError:
            with open(prompt_path, "r", encoding="cp1252") as file:
                prompt = json.load(file)
    except Exception as err:
        raise Flux2PreparationError(
            "Workflow Error",
            f"Prompt load failed: {err}",
        ) from err

    selected_images = [image_1]
    if image_count >= 2:
        selected_images.append(image_2)
    if image_count >= 3:
        selected_images.append(image_3)

    try:
        pil_images: list[Image.Image] = []
        for image in selected_images:
            pil_image = _to_pil_image(image)
            if pil_image.mode not in ("RGB", "RGBA"):
                pil_image = pil_image.convert("RGB")
            pil_images.append(pil_image)
        input_paths = [
            _save_temp_image(image, prefix=f"flux2_input_{idx + 1}")
            for idx, image in enumerate(pil_images)
        ]
        image_names = [
            f"flux2_klein_input_{idx}.jpg"
            for idx in range(1, len(pil_images) + 1)
        ]
        image_payload = [
            {
                "name": image_name,
                "image": save_input_image_as_base64(pil_image),
            }
            for image_name, pil_image in zip(image_names, pil_images)
        ]
    except Exception as err:
        raise Flux2PreparationError(
            "Input Error",
            f"Image preparation failed: {err}",
        ) from err

    uses_padding_crop = _has_padding_crop_nodes(prompt)
    return Flux2PreparedInputs(
        workflow_key=workflow_key,
        image_count=image_count,
        prompt=prompt,
        pil_images=pil_images,
        input_paths=input_paths,
        image_names=image_names,
        image_payload=image_payload,
        uses_padding_crop=uses_padding_crop,
        task_id=str(uuid.uuid4()),
        feature_flags={
            "mode": mode,
            "image_count": image_count,
            "uses_qwenvl": mode in {MODE_REFERENCE_TRANSFER, MODE_RAW_ENHANCEMENT},
            "padding_crop": uses_padding_crop,
            "padding_multiple": 32 if uses_padding_crop else None,
        },
        settings_snapshot={
            "mode": mode,
            "image_count": image_count,
            "prompt_text": str(prompt_text or ""),
            "realistic_strength": float(realistic_strength),
        },
    )


def _build_flux2_payload(
    prepared: Flux2PreparedInputs,
    *,
    mode: str,
    prompt_text: str,
    realistic_strength: float,
    workflow_debug: bool,
    is_admin_user: bool,
) -> Flux2PreparedJob:
    _apply_flux2_klein_workflow_updates(
        prepared.prompt,
        mode=mode,
        image_count=prepared.image_count,
        prompt_text=prompt_text,
        image_names=prepared.image_names,
        realistic_strength=realistic_strength,
        workflow=prepared.workflow_key,
    )
    payload = prepare_json(prepared.prompt, prepared.image_payload)
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
    return Flux2PreparedJob(
        inputs=prepared,
        payload=payload,
        workflow_debug_path=workflow_debug_path,
    )


def _create_flux2_task_tracker(
    prepared: Flux2PreparedInputs,
    *,
    identity: Any,
    user_agent: str | None,
    session_id: str,
    prompt_text: str,
    realistic_strength: float,
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
        source_page="/tab/flux2-klein-image-edit-9b-distilled",
        browser_user_agent=user_agent,
        session_id=session_id,
        environment_name=APP_ENVIRONMENT,
        feature_flags=prepared.feature_flags,
        settings=prepared.settings_snapshot,
        input_meta={
            "width": int(prepared.pil_images[0].width),
            "height": int(prepared.pil_images[0].height),
            "resolution": (
                f"{int(prepared.pil_images[0].width)}"
                f"x{int(prepared.pil_images[0].height)}"
            ),
            "format": str(prepared.pil_images[0].mode),
            "image_count": prepared.image_count,
        },
        request_summary={
            "mode": prepared.settings_snapshot["mode"],
            "image_count": prepared.image_count,
            "prompt_text": str(prompt_text or ""),
            "realistic_strength": float(realistic_strength),
        },
        prompt_type="image_edit",
        created_by=identity.email,
    )


async def _submit_flux2_job(
    api: RunpodAPI,
    payload: dict[str, Any],
) -> Flux2SubmissionResult:
    try:
        response = await api.run(payload)
        return Flux2SubmissionResult(job_id=str(response["id"]))
    except RunpodSubmissionUncertainError as err:
        return Flux2SubmissionResult(
            job_id=None,
            error_message=(
                f"{err}\n\nPlease check the Jobs page before trying again; "
                "RunPod may already have accepted this request."
            ),
            uncertain=True,
        )
    except RunpodSubmissionError as err:
        return Flux2SubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )
    except Exception as err:
        return Flux2SubmissionResult(
            job_id=None,
            error_message=f"Job submission failed: {err}",
        )


async def _finalize_flux2_output(
    status: dict[str, Any],
    prepared: Flux2PreparedInputs,
) -> Flux2FinalizedOutput:
    try:
        result_image = await _decode_output_image(status)
        if prepared.uses_padding_crop:
            result_image = _crop_to_dimensions(
                result_image,
                int(prepared.pil_images[0].width),
                int(prepared.pil_images[0].height),
            )
        return Flux2FinalizedOutput(
            result_image=result_image,
            left_path=prepared.input_paths[0],
            right_path=_save_temp_image(result_image, prefix="flux2_output"),
            artifacts=extract_artifacts_from_status(status),
        )
    except Exception as err:
        return Flux2FinalizedOutput(error_message=str(err))


async def _poll_flux2_job(
    api: RunpodAPI,
    job_id: str,
    prepared: Flux2PreparedInputs,
):
    for poll_idx in range(MAX_STATUS_POLLS):
        try:
            status = await api.status(job_id)
        except Exception as err:
            yield Flux2PollEvent(
                kind="status_error",
                status={},
                title="RunPod Error",
                message=f"Failed to check job status: {err}",
                progress_percent=0,
                stage="processing",
                poll_idx=poll_idx,
            )
            return

        state = (status.get("status") or "UNKNOWN").upper()
        has_final_output = _has_final_output_payload(status)
        if state in TERMINAL_FAILURES:
            yield Flux2PollEvent(
                kind="terminal_failure",
                status=status,
                title="RunPod Error",
                message=_extract_error_message(status),
                progress_percent=0,
                stage="processing",
                poll_idx=poll_idx,
            )
            return

        if state == "COMPLETED" or has_final_output:
            finalized = await _finalize_flux2_output(status, prepared)
            if finalized.error_message:
                yield Flux2PollEvent(
                    kind="decode_error",
                    status=status,
                    title="Decode Error",
                    message=f"Failed to decode image: {finalized.error_message}",
                    progress_percent=96,
                    stage="output_collecting",
                    poll_idx=poll_idx,
                    finalized=finalized,
                )
            else:
                yield Flux2PollEvent(
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

        runpod_progress, progress_text, _ = _extract_progress_signal(status)
        title, message, progress_percent = _describe_progress_status(
            state,
            progress_text,
            has_final_output=has_final_output,
            runpod_progress=runpod_progress,
        )
        yield Flux2PollEvent(
            kind="progress",
            status=status,
            title=title,
            message=message,
            progress_percent=progress_percent,
            stage=_processing_stage_name(
                state,
                has_final_output=has_final_output,
            ),
            poll_idx=poll_idx,
            runpod_progress=runpod_progress,
            progress_text=progress_text,
        )
        await asyncio.sleep(RUNPOD_STATUS_POLL_INTERVAL_S)

    yield Flux2PollEvent(
        kind="timeout",
        status={},
        title="Timeout",
        message="Timed out waiting for RunPod completion status.",
        progress_percent=0,
        stage="processing",
        poll_idx=MAX_STATUS_POLLS,
    )


def _record_flux2_poll_event(
    tracker: TaskTracker,
    event: Flux2PollEvent,
    *,
    mode: str,
) -> None:
    if event.kind == "progress":
        tracker.emit_processing(
            stage=event.stage,
            message=event.message,
            progress_percent=event.progress_percent,
            node_id=None,
            metadata={
                "poll_idx": event.poll_idx,
                "runpod_state": event.status.get("status"),
                "mode": mode,
                "progress_text": event.progress_text,
                "runpod_progress": event.runpod_progress,
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
            raise ValueError("Completed Flux2 event is missing finalized output.")
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
            output_count=max(int(artifacts.get("output_count") or 0), 1),
            output_width=finalized.result_image.width,
            output_height=finalized.result_image.height,
            worker_id=artifacts.get("worker_id"),
            result_summary={
                "left_path": str(finalized.left_path),
                "right_path": str(finalized.right_path),
                "runpod_state": event.status.get("status"),
                "mode": mode,
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
        error_message=event.message,
        failure_stage=event.stage,
        progress_percent=event.progress_percent,
        worker_id=event.status.get("workerId"),
        metadata=(
            {"runpod_state": event.status.get("status")}
            if event.kind == "terminal_failure"
            else None
        ),
    )


def _render_flux2_poll_event(
    event: Flux2PollEvent,
    *,
    job_id: str,
) -> tuple[Any, str, str | None]:
    if event.kind == "progress":
        return (
            gr.update(),
            _render_status_panel(
                event.title,
                event.message,
                percent=event.progress_percent,
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
            raise ValueError("Completed Flux2 event is missing output paths.")
        return (
            (str(finalized.left_path), str(finalized.right_path)),
            _render_status_panel(event.title, event.message, percent=100),
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


async def flux2_klein_generate(
    mode: str,
    edit_image_count: str,
    image_1: Any,
    image_2: Any,
    image_3: Any,
    prompt_text: str,
    realistic_strength: float,
    workflow_debug: bool,
    job_state: str | None,
    workflow: str,
    request: gr.Request,
):
    del job_state
    logger.info("Workflow %s called in mode %s", workflow, mode)
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
    user_role = str(getattr(identity, "role", "") or "").strip().lower()
    user_agent = _request_header(request, "user-agent")
    session_id = auth_service.session_key(identity.email, user_agent)
    try:
        prepared_inputs = _prepare_flux2_inputs(
            mode=mode,
            edit_image_count=edit_image_count,
            image_1=image_1,
            image_2=image_2,
            image_3=image_3,
            prompt_text=prompt_text,
            realistic_strength=realistic_strength,
            workflow=workflow,
        )
    except Flux2PreparationError as err:
        yield (
            gr.update(),
            _render_status_panel(err.title, str(err), accent="#f87171"),
            None,
        )
        return

    tracker = _create_flux2_task_tracker(
        prepared_inputs,
        identity=identity,
        user_agent=user_agent,
        session_id=session_id,
        prompt_text=prompt_text,
        realistic_strength=realistic_strength,
    )
    try:
        prepared_job = _build_flux2_payload(
            prepared_inputs,
            mode=mode,
            prompt_text=prompt_text,
            realistic_strength=realistic_strength,
            workflow_debug=workflow_debug,
            is_admin_user=user_role == "admin",
        )
    except Exception as err:
        title = "Workflow Error"
        message = (
            f"Workflow key missing: {err}"
            if isinstance(err, KeyError)
            else f"Workflow update failed: {err}"
        )
        tracker.fail(
            failure_reason=(
                "workflow_key_missing"
                if isinstance(err, KeyError)
                else "workflow_update_error"
            ),
            error_message=str(err),
            failure_stage="preparation",
            progress_percent=0,
            worker_id=None,
        )
        yield gr.update(), _render_status_panel(title, message, accent="#f87171"), None
        return

    api = RunpodAPI(environment=RUNPOD_ENVIRONMENT)
    submission = await _submit_flux2_job(api, prepared_job.payload)
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
    submitted_message = "Preparing image"
    if prepared_job.workflow_debug_path is not None:
        submitted_message += (
            f"\n\nDebug JSON saved: {prepared_job.workflow_debug_path}"
        )
    yield (
        gr.update(),
        _render_status_panel(
            "Preparing image",
            submitted_message,
            percent=3,
        ),
        job_id,
    )

    async for event in _poll_flux2_job(api, job_id, prepared_inputs):
        _record_flux2_poll_event(tracker, event, mode=mode)
        yield _render_flux2_poll_event(event, job_id=job_id)
        if event.kind != "progress":
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


def _build_flux2_klein_interface(
    *,
    heading: str,
    workflow_name_value: str,
) -> gr.Blocks:
    with gr.Blocks(title=APP_TITLE, css=BOTTOM_PROGRESS_LAYOUT_CSS) as interface:
        gr.Markdown(f"## {heading}")

        workflow_name = gr.State(workflow_name_value)
        job_id_state = gr.State(None)

        with gr.Row(variant="panel"):
            with gr.Column(scale=2):
                mode_dropdown = gr.Dropdown(
                    choices=MODE_CHOICES,
                    value=MODE_EDIT,
                    label="Mode",
                )
                image_count_dropdown = gr.Dropdown(
                    choices=IMAGE_COUNT_CHOICES,
                    value="1",
                    label="Image Count",
                    info="Only used in Edit mode.",
                )
                with gr.Row():
                    prompt_input = gr.Textbox(
                        label="Prompt",
                        placeholder="Describe the edit or target result...",
                        lines=5,
                        scale=3,
                    )
                    with gr.Column(
                        scale=2,
                        min_width=190,
                        visible=False,
                    ) as realistic_controls_column:
                        prompt_library_category = gr.Dropdown(
                            choices=PROMPT_LIBRARY_CATEGORY_CHOICES,
                            value=PROMPT_LIBRARY_ALL_CATEGORY,
                            label="Category",
                        )
                        prompt_library_preset = gr.Dropdown(
                            choices=PROMPT_LIBRARY_PRESET_CHOICES,
                            value=PROMPT_LIBRARY_PRESETS[0]["id"],
                            label="Preset",
                        )
                        realistic_strength_slider = gr.Slider(
                            minimum=0.0,
                            maximum=1.0,
                            value=0.5,
                            step=0.05,
                            label="Realistic Mode",
                            info="Controls the realistic LoRA strength.",
                        )
                mode_hint = gr.Markdown(MODE_HINTS[MODE_EDIT])
                workflow_debug_checkbox = gr.Checkbox(
                    label="Workflow Debug (Admin only)",
                    value=False,
                    visible=False,
                    info="Save the final manipulated workflow JSON sent to RunPod.",
                )

            with gr.Column(scale=3):
                result_slider = ImageSlider(label="Primary Input vs Result", type="filepath")

        with gr.Row():
            image_input_1 = gr.Image(label="Image 1", type="pil")
            image_input_2 = gr.Image(label="Image 2", type="pil", visible=False)
            image_input_3 = gr.Image(label="Image 3", type="pil", visible=False)

        with gr.Row(elem_classes=["bottom-progress-row"]):
            progress_panel = gr.HTML(_render_idle_status())

        with gr.Row(elem_classes=["bottom-action-row"]):
            generate_btn = gr.Button("🌟 Generate", scale=3, variant="primary")
            cancel_btn = gr.Button("Cancel", variant="stop", scale=1)

        mode_dropdown.change(
            fn=_mode_controls_update,
            inputs=[mode_dropdown, image_count_dropdown],
            outputs=[
                image_count_dropdown,
                prompt_input,
                realistic_controls_column,
                image_input_1,
                image_input_2,
                image_input_3,
                mode_hint,
            ],
        )
        image_count_dropdown.change(
            fn=_edit_count_update,
            inputs=[mode_dropdown, image_count_dropdown],
            outputs=[image_input_1, image_input_2, image_input_3],
        )

        prompt_library_category.change(
            fn=_prompt_library_category_update,
            inputs=prompt_library_category,
            outputs=[prompt_library_preset, prompt_input],
            queue=False,
            show_progress="hidden",
        )
        prompt_library_preset.change(
            fn=_apply_prompt_library_preset,
            inputs=prompt_library_preset,
            outputs=prompt_input,
            queue=False,
            show_progress="hidden",
        )

        generate_event = generate_btn.click(
            fn=_disable_generate_button,
            inputs=None,
            outputs=[generate_btn],
            queue=False,
        )

        generate_event = generate_event.then(
            fn=flux2_klein_generate,
            inputs=[
                mode_dropdown,
                image_count_dropdown,
                image_input_1,
                image_input_2,
                image_input_3,
                prompt_input,
                realistic_strength_slider,
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

        interface.load(
            fn=_debug_checkbox_visibility_update,
            inputs=None,
            outputs=[workflow_debug_checkbox],
        )

    return interface


flux2_klein_interface = _build_flux2_klein_interface(
    heading="Qwen Edit",
    workflow_name_value=WORKFLOW_NAME,
)


if __name__ == "__main__":
    flux2_klein_interface.launch(
        server_name="0.0.0.0",
        server_port=8171,
        debug=APP_DEBUG,
        quiet=APP_QUIET,
        auth=auth_service.authenticate,
        auth_message="BrickVisual internal access only.",
    )
