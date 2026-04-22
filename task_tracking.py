from __future__ import annotations

import base64
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from analytics_store import AnalyticsStore, get_analytics_store

logger = logging.getLogger(__name__)

MIN_PROGRESS_EVENT_INTERVAL_S = max(
    0.1,
    float(os.getenv("TRACKING_PROGRESS_EVENT_INTERVAL_S", "0.5")),
)


@dataclass(frozen=True)
class WorkflowContext:
    key: str
    name: str
    version: str | None = None
    category: str | None = None
    workflow_type: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _ms_delta(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _safe_output_url(status_payload: dict[str, Any]) -> str | None:
    output = status_payload.get("output") if isinstance(status_payload, dict) else None
    if not isinstance(output, dict):
        return None

    message = output.get("message")
    if isinstance(message, str):
        message = [message]

    if isinstance(message, list):
        for item in message:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                return item

    images = output.get("images")
    if isinstance(images, list):
        for entry in images:
            if not isinstance(entry, dict):
                continue
            data = entry.get("data")
            if isinstance(data, str) and data.startswith(("http://", "https://")):
                return data

    return None


def _infer_output_filename(url: str | None) -> str | None:
    if not url:
        return None
    name = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return name or None


class TaskTracker:
    """Writes normalized lifecycle + analytics records for one task/job."""

    def __init__(
        self,
        *,
        store: AnalyticsStore | None,
        task_id: str,
        user_email: str,
        user_prefix: str,
        user_display_name: str,
        user_role: str,
        avatar_filename: str | None,
        workflow: WorkflowContext,
        source_page: str,
        browser_user_agent: str | None,
        session_id: str | None,
        environment_name: str,
        feature_flags: dict[str, Any] | None,
        settings: dict[str, Any] | None,
        input_meta: dict[str, Any] | None,
        request_summary: dict[str, Any] | None,
        prompt_type: str | None = None,
        created_by: str | None = None,
    ) -> None:
        self.store = store or get_analytics_store()
        self.task_id = str(task_id)
        self.workflow = workflow
        self.user_email = user_email
        self.user_prefix = user_prefix
        self.request_id: str | None = None

        self.submitted_dt = _utc_now()
        self.started_dt: datetime | None = None
        self.finished_dt: datetime | None = None

        self.last_progress_signature: str | None = None
        self.last_progress_at: datetime | None = None
        self.last_status: str | None = None

        self.store.register_workflow(
            workflow_key=workflow.key,
            display_name=workflow.name,
            version=workflow.version,
            category=workflow.category,
            workflow_type=workflow.workflow_type,
            is_active=True,
        )

        self.store.create_task(
            {
                "task_id": self.task_id,
                "request_id": None,
                "user_email": user_email,
                "user_prefix": user_prefix,
                "user_display_name": user_display_name,
                "user_role": user_role,
                "avatar_filename": avatar_filename,
                "workflow_key": workflow.key,
                "workflow_name": workflow.name,
                "workflow_version": workflow.version,
                "workflow_category": workflow.category,
                "workflow_type": workflow.workflow_type,
                "status": "created",
                "submitted_at": self.submitted_dt.isoformat(timespec="seconds"),
                "source_page": source_page,
                "browser_user_agent": browser_user_agent,
                "session_id": session_id,
                "environment_name": environment_name,
                "feature_flags": feature_flags,
                "settings": settings,
                "request_summary": request_summary,
                "prompt_type": prompt_type,
                "input_width": input_meta.get("width") if input_meta else None,
                "input_height": input_meta.get("height") if input_meta else None,
                "input_resolution": input_meta.get("resolution") if input_meta else None,
                "input_format": input_meta.get("format") if input_meta else None,
                "input_size_bytes": input_meta.get("size_bytes") if input_meta else None,
                "latest_stage": "created",
                "latest_message": "Task created.",
                "latest_progress_percent": 0,
                "created_by": created_by or user_email,
                "updated_by": user_email,
            }
        )
        self.store.upsert_task_settings(
            task_id=self.task_id,
            feature_flags=feature_flags,
            settings=settings,
            prompt_type=prompt_type,
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type="created",
            status="created",
            stage="created",
            message="Task created.",
            progress_percent=0,
        )
        self.last_status = "created"

    def attach_request(
        self,
        *,
        request_id: str,
        task_url: str | None,
        retry_count: int = 0,
    ) -> None:
        self.request_id = request_id
        self.store.update_task(
            self.task_id,
            {
                "request_id": request_id,
                "task_url": task_url,
                "retry_count": int(retry_count),
                "status": "queued",
                "latest_stage": "queued",
                "latest_message": "Task queued on RunPod.",
                "latest_progress_percent": 0,
            },
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type="queued",
            status="queued",
            stage="queued",
            message="Task queued on RunPod.",
            progress_percent=0,
            metadata={"request_id": request_id, "task_url": task_url},
        )
        self.last_status = "queued"

    def mark_started(self, *, message: str | None = None) -> None:
        if self.started_dt is None:
            self.started_dt = _utc_now()
        queue_ms = _ms_delta(self.submitted_dt, self.started_dt)
        self.store.update_task(
            self.task_id,
            {
                "status": "started",
                "started_at": self.started_dt.isoformat(timespec="seconds"),
                "queue_duration_ms": queue_ms,
                "latest_stage": "started",
                "latest_message": message or "Execution started.",
                "latest_progress_percent": 1,
            },
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type="started",
            status="started",
            stage="started",
            message=message or "Execution started.",
            progress_percent=1,
            metadata={"queue_duration_ms": queue_ms},
        )
        self.last_status = "started"

    def emit_processing(
        self,
        *,
        stage: str,
        message: str,
        progress_percent: float | int | None,
        node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        now = _utc_now()
        signature_payload = {
            "stage": stage,
            "message": message,
            "node_id": node_id,
            "progress_percent": float(progress_percent) if progress_percent is not None else None,
        }
        signature = json.dumps(signature_payload, sort_keys=True)

        if not force and self.last_progress_signature == signature and self.last_progress_at is not None:
            elapsed = (now - self.last_progress_at).total_seconds()
            if elapsed < MIN_PROGRESS_EVENT_INTERVAL_S:
                return

        if self.started_dt is None:
            self.mark_started(message="Execution started.")

        self.store.update_task(
            self.task_id,
            {
                "status": "processing",
                "latest_stage": stage,
                "latest_message": message,
                "latest_progress_percent": progress_percent,
            },
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type="processing",
            status="processing",
            stage=stage,
            message=message,
            node_id=node_id,
            progress_percent=progress_percent,
            metadata=metadata,
        )
        self.last_progress_signature = signature
        self.last_progress_at = now
        self.last_status = "processing"

    def mark_stage(
        self,
        *,
        status: str,
        stage: str,
        message: str,
        progress_percent: float | int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if status in {"processing", "started"} and self.started_dt is None:
            self.mark_started(message="Execution started.")

        self.store.update_task(
            self.task_id,
            {
                "status": status,
                "latest_stage": stage,
                "latest_message": message,
                "latest_progress_percent": progress_percent,
            },
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type=status,
            status=status,
            stage=stage,
            message=message,
            progress_percent=progress_percent,
            metadata=metadata,
        )
        self.last_status = status

    def add_thumbnail(self, *, image: Image.Image, output_index: int = 0) -> str:
        return self.store.save_thumbnail(task_id=self.task_id, image=image, output_index=output_index)

    def add_preview(self, *, image: Image.Image, output_index: int = 0) -> str:
        return self.store.save_preview(task_id=self.task_id, image=image, output_index=output_index)

    def add_output_record(
        self,
        *,
        output_index: int,
        result_url: str | None,
        thumbnail_url: str | None,
        preview_url: str | None,
        file_name: str | None,
        width: int | None,
        height: int | None,
    ) -> None:
        self.store.add_output(
            task_id=self.task_id,
            output_index=output_index,
            result_url=result_url,
            thumbnail_url=thumbnail_url,
            preview_url=preview_url,
            file_name=file_name,
            width=width,
            height=height,
        )

    def complete(
        self,
        *,
        result_url: str | None,
        thumbnail_url: str | None,
        preview_url: str | None,
        output_filename: str | None,
        output_count: int,
        output_width: int | None,
        output_height: int | None,
        worker_id: str | None,
        result_summary: dict[str, Any] | None,
        stage_message: str = "Task completed successfully.",
    ) -> None:
        if self.started_dt is None:
            self.started_dt = self.submitted_dt
        self.finished_dt = _utc_now()

        total_ms = _ms_delta(self.submitted_dt, self.finished_dt)
        queue_ms = _ms_delta(self.submitted_dt, self.started_dt)
        processing_ms = _ms_delta(self.started_dt, self.finished_dt)

        self.store.update_task(
            self.task_id,
            {
                "status": "completed",
                "outcome": "success",
                "finished_at": self.finished_dt.isoformat(timespec="seconds"),
                "total_duration_ms": total_ms,
                "queue_duration_ms": queue_ms,
                "processing_duration_ms": processing_ms,
                "result_url": result_url,
                "thumbnail_url": thumbnail_url,
                "preview_url": preview_url,
                "output_filename": output_filename,
                "output_count": int(output_count),
                "output_width": output_width,
                "output_height": output_height,
                "worker_id": worker_id,
                "latest_stage": "completed",
                "latest_message": stage_message,
                "latest_progress_percent": 100,
                "result_summary": result_summary,
            },
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type="completed",
            status="completed",
            stage="completed",
            message=stage_message,
            progress_percent=100,
            metadata={
                "total_duration_ms": total_ms,
                "queue_duration_ms": queue_ms,
                "processing_duration_ms": processing_ms,
                "worker_id": worker_id,
                "output_count": int(output_count),
            },
        )
        self.last_status = "completed"

    def fail(
        self,
        *,
        failure_reason: str,
        error_message: str,
        failure_stage: str,
        progress_percent: float | int | None,
        worker_id: str | None,
        status: str = "failed",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.started_dt is None:
            self.started_dt = self.submitted_dt
        self.finished_dt = _utc_now()

        total_ms = _ms_delta(self.submitted_dt, self.finished_dt)
        queue_ms = _ms_delta(self.submitted_dt, self.started_dt)
        processing_ms = _ms_delta(self.started_dt, self.finished_dt)

        self.store.update_task(
            self.task_id,
            {
                "status": status,
                "outcome": "failed" if status == "failed" else status,
                "finished_at": self.finished_dt.isoformat(timespec="seconds"),
                "total_duration_ms": total_ms,
                "queue_duration_ms": queue_ms,
                "processing_duration_ms": processing_ms,
                "failure_reason": failure_reason,
                "error_message": error_message,
                "failure_stage": failure_stage,
                "worker_id": worker_id,
                "latest_stage": failure_stage,
                "latest_message": error_message,
                "latest_progress_percent": progress_percent,
            },
        )
        self.store.add_event(
            task_id=self.task_id,
            event_type=status,
            status=status,
            stage=failure_stage,
            message=error_message,
            progress_percent=progress_percent,
            metadata={
                "failure_reason": failure_reason,
                "worker_id": worker_id,
                "total_duration_ms": total_ms,
                **(metadata or {}),
            },
        )
        self.last_status = status


def extract_artifacts_from_status(status_payload: dict[str, Any]) -> dict[str, Any]:
    result_url = _safe_output_url(status_payload)
    output_filename = _infer_output_filename(result_url)

    output = status_payload.get("output") if isinstance(status_payload, dict) else None
    output_count = 0
    if isinstance(output, dict):
        images = output.get("images")
        message = output.get("message")
        if isinstance(images, list):
            output_count = len(images)
        elif isinstance(message, list):
            output_count = len(message)
        elif isinstance(message, str):
            output_count = 1

    worker_id = status_payload.get("workerId") if isinstance(status_payload, dict) else None

    return {
        "result_url": result_url,
        "output_filename": output_filename,
        "output_count": output_count,
        "worker_id": worker_id,
    }


def decode_first_image_dimensions(status_payload: dict[str, Any]) -> tuple[int | None, int | None]:
    output = status_payload.get("output") if isinstance(status_payload, dict) else None
    if not isinstance(output, dict):
        return None, None

    message = output.get("message")
    if isinstance(message, str):
        message = [message]

    if isinstance(message, list):
        for entry in message:
            if not isinstance(entry, str):
                continue
            if entry.startswith(("http://", "https://")):
                # URL dimensions are resolved from decoded image path later.
                continue
            b64 = entry.split(",", 1)[1] if entry.startswith("data:") else entry
            try:
                raw = base64.b64decode(b64, validate=True)
                with Image.open(io.BytesIO(raw)) as img:  # type: ignore[name-defined]
                    return img.width, img.height
            except Exception:
                continue

    return None, None
