from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


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
