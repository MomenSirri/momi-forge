from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import random
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import aiohttp
import gradio as gr
import numpy as np
from PIL import Image
from gradio_imageslider import ImageSlider

from runpod_api_class import RunpodAPI
from utils import prepare_json, save_input_image_as_base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_TITLE = "Momi Forge"
RUNPOD_ENV = os.getenv("RUNPOD_TARGET_ENV", "SEED")
DB_PATH = os.getenv("USER_DB_PATH", "users.db")
WORKFLOW_FILENAME = os.getenv("MOMI_WORKFLOW_FILE", "Seedvr_flux_upscaler_03.json")

TERMINAL_FAILURES = {"FAILED", "ERROR", "TIMED_OUT"}
ACTIVE_STATES = {"IN_QUEUE", "IN_PROGRESS", "RUNNING"}


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


conn = _create_db_connection()


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
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                return await _read_url_image(item)

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


async def fivek_generator(
    image: Any,
    engine_choice: str,
    enhancement: bool,
    upscale_value: str,
    flux_creativity_tilet: float,
    job_state: str | None,
    workflow: str,
    request: gr.Request,
):
    del job_state
    logger.info("Workflow %s called", workflow)

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
        yield gr.update(), f"❌ Workflow key missing: {err}", None
        return
    except Exception as err:
        yield gr.update(), f"❌ Workflow update failed: {err}", None
        return

    final_json = prepare_json(prompt, [{"name": main_image_name, "image": image_base64}])

    if os.getenv("SAVE_DEBUG_PROMPT_JSON", "0") == "1":
        debug_path = Path(__file__).resolve().parent / "updated_prompt_5k_nunchaku.json"
        try:
            with open(debug_path, "w", encoding="utf-8") as outfile:
                json.dump(final_json, outfile, indent=2)
        except Exception as err:
            logger.warning("Could not save debug prompt JSON: %s", err)

    api = RunpodAPI(environment='seed')

    try:
        run_resp = await api.run(final_json)
        job_id = run_resp["id"]
    except Exception as err:
        yield gr.update(), f"❌ Job submission failed: {err}", None
        return

    yield gr.update(), "🚀 Job submitted…", job_id

    while True:
        try:
            status = await api.status(job_id)
        except Exception as err:
            yield gr.update(), f"❌ Failed to check job status: {err}", None
            return

        state = (status.get("status") or "").upper()

        if state == "CANCELLED":
            yield gr.update(), "⚠️ Job cancelled.", None
            return

        if state in TERMINAL_FAILURES:
            yield gr.update(), f"❌ {_extract_error_message(status)}", None
            return

        if state == "COMPLETED":
            try:
                result_image = await _decode_output_image(status)
                if result_image.mode not in ("RGB", "RGBA"):
                    result_image = result_image.convert("RGBA")

                tmp_dir = Path(tempfile.gettempdir())
                left_path = tmp_dir / f"{job_id}_left.png"
                right_path = tmp_dir / f"{job_id}_right.png"

                input_pil.save(left_path, "PNG")
                result_image.save(right_path, "PNG")

                user = getattr(request, "username", None)
                if user:
                    conn.execute(
                        "INSERT INTO usage(email, file_path, workflow) VALUES (?, ?, ?)",
                        (user, str(right_path), workflow),
                    )
                    conn.commit()

                yield (str(left_path), str(right_path)), "✅ Done!", None
                return
            except Exception as err:
                yield gr.update(), f"❌ Failed to decode image: {err}", None
                return

        progress = status.get("progress")
        if progress is not None:
            yield gr.update(), f"⏳ {state.lower().replace('_', ' ')}… {progress}%", job_id
        else:
            fallback = state.lower().replace("_", " ") if state in ACTIVE_STATES else "processing"
            yield gr.update(), f"⏳ {fallback}…", job_id
        await asyncio.sleep(1)


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


with gr.Blocks(title=APP_TITLE) as fivek:
    gr.Markdown(f"## {APP_TITLE} – 5K Enhance/Upscale")

    with gr.Row(variant="panel"):
        image_input = gr.Image(label="Input Image")
        image_output = ImageSlider(label="Result", type="filepath")

    with gr.Row():
        status_box = gr.Markdown("")
        job_id_state = gr.State(None)

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

    flux_creativity_tilet = gr.Slider(
        minimum=10,
        maximum=40,
        step=5,
        value=30,
        label="Creativity",
    )

    with gr.Row():
        enhance_btn = gr.Button("🌟 Generate", scale=3, variant="primary")
        cancel_btn = gr.Button("Cancel", variant="stop", scale=1)

    workflow_name = gr.State("5K_Upscale")

    def on_engine_change(engine: str):
        return gr.update(visible=engine != "Super Fast", value=engine != "Super Fast")

    engine_choice.change(fn=on_engine_change, inputs=engine_choice, outputs=enhancement_toggle)

    enhance_btn.click(
        fn=fivek_generator,
        inputs=[
            image_input,
            engine_choice,
            enhancement_toggle,
            upscale_value,
            flux_creativity_tilet,
            job_id_state,
            workflow_name,
        ],
        outputs=[image_output, status_box, job_id_state],
        concurrency_limit=None,
        trigger_mode="multiple",
    )

    cancel_btn.click(cancel_job, inputs=job_id_state, outputs=status_box)
if __name__ == "__main__":
    fivek.launch(
        server_name="0.0.0.0",
        server_port=8188,
        debug=True,
    )