from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(os.getenv("USER_DB_PATH", "users.db"))
DEFAULT_APP_VERSION = os.getenv("APP_VERSION", "dev")
DEFAULT_BACKEND_VERSION = os.getenv("BACKEND_VERSION", "runpod")
THUMBNAIL_MAX_EDGE = max(64, int(os.getenv("TASK_THUMBNAIL_MAX_EDGE", "360")))
PREVIEW_MAX_EDGE = max(256, int(os.getenv("TASK_PREVIEW_MAX_EDGE", "1440")))
PREVIEW_WEBP_QUALITY = max(50, min(95, int(os.getenv("TASK_PREVIEW_WEBP_QUALITY", "82"))))
THUMBNAIL_WARN_GB = max(1.0, float(os.getenv("TASK_THUMBNAIL_WARN_GB", "50")))
THUMBNAIL_DISK_CAP_GB = max(THUMBNAIL_WARN_GB, float(os.getenv("TASK_THUMBNAIL_DISK_CAP_GB", "75")))
THUMBNAIL_CLEANUP_MIN_INTERVAL_SEC = max(5, int(os.getenv("TASK_THUMBNAIL_CLEANUP_MIN_INTERVAL_SEC", "60")))
DEFAULT_FAVORITE_CATEGORIES: list[tuple[str, str, str, int]] = [
    ("inspiration", "Inspiration", "#1D9BF0", 10),
    ("best_results", "Best Results", "#00B894", 20),
    ("client_ready", "Client-ready", "#FDBA2D", 30),
    ("personal_picks", "Personal Picks", "#A855F7", 40),
    ("tests_keep", "Tests Worth Keeping", "#64748B", 50),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dump(data: Any) -> str | None:
    if data is None:
        return None
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps({"raw": str(data)}, ensure_ascii=False)


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


class AnalyticsStore:
    """Centralized persistence layer for auth profiles, task lifecycle and analytics."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        app_version: str | None = None,
        backend_version: str | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        if not self.db_path.is_absolute():
            self.db_path = Path(__file__).resolve().parent / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.app_version = app_version or DEFAULT_APP_VERSION
        self.backend_version = backend_version or DEFAULT_BACKEND_VERSION
        self.thumbnail_dir = Path(os.getenv("TASK_THUMBNAIL_DIR", "thumbnails"))
        if not self.thumbnail_dir.is_absolute():
            self.thumbnail_dir = Path(__file__).resolve().parent / self.thumbnail_dir
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir = Path(os.getenv("TASK_PREVIEW_DIR", "image_load_card"))
        if not self.preview_dir.is_absolute():
            self.preview_dir = Path(__file__).resolve().parent / self.preview_dir
        self.preview_dir.mkdir(parents=True, exist_ok=True)

        self.thumbnail_warn_bytes = int(THUMBNAIL_WARN_GB * 1024 * 1024 * 1024)
        self.thumbnail_cap_bytes = int(THUMBNAIL_DISK_CAP_GB * 1024 * 1024 * 1024)
        self.thumbnail_cleanup_min_interval_sec = THUMBNAIL_CLEANUP_MIN_INTERVAL_SEC
        self._thumbnail_cleanup_last_ts = 0.0
        self._thumbnail_warning_last_ts = 0.0
        self._thumbnail_warning_active = False

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self.ensure_schema()

    def _table_columns(self, table_name: str) -> set[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    def _ensure_column(self, table_name: str, column_name: str, ddl: str) -> None:
        existing = self._table_columns(table_name)
        if column_name in existing:
            return
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    pwd_hash BLOB NOT NULL
                )
                """
            )

            # Backward-compatible profile fields on existing auth table.
            self._ensure_column("users", "username_prefix", "TEXT")
            self._ensure_column("users", "display_name", "TEXT")
            self._ensure_column("users", "avatar_filename", "TEXT")
            self._ensure_column("users", "role", "TEXT DEFAULT 'user'")
            self._ensure_column("users", "is_active", "INTEGER DEFAULT 1")
            self._ensure_column("users", "created_at", "TEXT")
            self._ensure_column("users", "last_login_at", "TEXT")
            self._ensure_column("users", "last_seen_at", "TEXT")
            self._ensure_column("users", "metadata_json", "TEXT")

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    version TEXT,
                    category TEXT,
                    workflow_type TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    user_email TEXT NOT NULL,
                    user_prefix TEXT,
                    user_display_name TEXT,
                    user_role TEXT,
                    avatar_filename TEXT,
                    workflow_key TEXT NOT NULL,
                    workflow_name TEXT NOT NULL,
                    workflow_version TEXT,
                    workflow_category TEXT,
                    workflow_type TEXT,
                    status TEXT NOT NULL,
                    outcome TEXT,
                    submitted_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    total_duration_ms INTEGER,
                    queue_duration_ms INTEGER,
                    processing_duration_ms INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    task_url TEXT,
                    result_url TEXT,
                    thumbnail_url TEXT,
                    preview_url TEXT,
                    output_filename TEXT,
                    output_count INTEGER,
                    input_width INTEGER,
                    input_height INTEGER,
                    input_resolution TEXT,
                    input_format TEXT,
                    input_size_bytes INTEGER,
                    output_width INTEGER,
                    output_height INTEGER,
                    worker_id TEXT,
                    server_id TEXT,
                    environment_name TEXT,
                    app_version TEXT,
                    backend_version TEXT,
                    prompt_type TEXT,
                    source_page TEXT,
                    session_id TEXT,
                    browser_user_agent TEXT,
                    feature_flags_json TEXT,
                    settings_json TEXT,
                    request_summary_json TEXT,
                    result_summary_json TEXT,
                    error_message TEXT,
                    failure_reason TEXT,
                    failure_stage TEXT,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT,
                    updated_by TEXT,
                    latest_stage TEXT,
                    latest_message TEXT,
                    latest_progress_percent REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(workflow_key) REFERENCES workflows(workflow_key)
                )
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT,
                    stage TEXT,
                    message TEXT,
                    node_id TEXT,
                    progress_percent REAL,
                    event_at TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                )
                """
            )

            # Backward-compatible history curation columns.
            self._ensure_column("tasks", "display_title", "TEXT")
            self._ensure_column("tasks", "notes", "TEXT")
            self._ensure_column("tasks", "tags_json", "TEXT")
            self._ensure_column("tasks", "is_pinned", "INTEGER DEFAULT 0")
            self._ensure_column("tasks", "is_deleted", "INTEGER DEFAULT 0")
            self._ensure_column("tasks", "deleted_at", "TEXT")
            self._ensure_column("tasks", "preview_url", "TEXT")

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    output_index INTEGER NOT NULL DEFAULT 0,
                    result_url TEXT,
                    thumbnail_url TEXT,
                    preview_url TEXT,
                    file_name TEXT,
                    width INTEGER,
                    height INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_column("task_outputs", "preview_url", "TEXT")

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS favorite_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    category_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    color TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 100,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_email, category_key)
                )
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_favorites (
                    task_id TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    favorite_category_key TEXT,
                    notes TEXT,
                    is_pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, user_email),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                )
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_settings (
                    task_id TEXT PRIMARY KEY,
                    feature_flags_json TEXT,
                    settings_json TEXT,
                    prompt_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                )
                """
            )

            now = _utc_now_iso()
            self._conn.execute(
                "UPDATE users SET created_at = COALESCE(created_at, ?) WHERE created_at IS NULL",
                (now,),
            )
            self._ensure_indexes()
            self._conn.commit()

    def _ensure_indexes(self) -> None:
        statements = [
            "CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks(user_email, submitted_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_workflow_created ON tasks(workflow_key, submitted_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_finished_at ON tasks(finished_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_failure_reason ON tasks(failure_reason)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_user_workflow ON tasks(user_email, workflow_key)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_worker ON tasks(worker_id)",
            "CREATE INDEX IF NOT EXISTS idx_events_task_time ON task_events(task_id, event_at)",
            "CREATE INDEX IF NOT EXISTS idx_events_type_time ON task_events(event_type, event_at)",
            "CREATE INDEX IF NOT EXISTS idx_outputs_task ON task_outputs(task_id, output_index)",
            "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
            "CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_favorites_user_flag ON task_favorites(user_email, is_favorite, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_favorites_user_category ON task_favorites(user_email, favorite_category_key)",
            "CREATE INDEX IF NOT EXISTS idx_categories_user_active ON favorite_categories(user_email, is_active, sort_order)",
        ]
        for sql in statements:
            self._conn.execute(sql)

    def register_workflow(
        self,
        *,
        workflow_key: str,
        display_name: str,
        version: str | None = None,
        category: str | None = None,
        workflow_type: str | None = None,
        is_active: bool = True,
    ) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO workflows (
                    workflow_key, display_name, version, category, workflow_type,
                    is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    version = excluded.version,
                    category = excluded.category,
                    workflow_type = excluded.workflow_type,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    workflow_key,
                    display_name,
                    version,
                    category,
                    workflow_type,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def update_user_profile(
        self,
        *,
        email: str,
        username_prefix: str,
        display_name: str,
        avatar_filename: str | None,
        role: str = "user",
        is_active: bool = True,
        metadata: dict[str, Any] | None = None,
        login: bool = False,
    ) -> bool:
        now = _utc_now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT email FROM users WHERE LOWER(email) = LOWER(?)",
                (email,),
            ).fetchone()
            if row is None:
                return False

            self._conn.execute(
                """
                UPDATE users
                SET
                    username_prefix = ?,
                    display_name = ?,
                    avatar_filename = ?,
                    role = ?,
                    is_active = ?,
                    metadata_json = COALESCE(?, metadata_json),
                    created_at = COALESCE(created_at, ?),
                    last_seen_at = ?,
                    last_login_at = CASE WHEN ? THEN ? ELSE last_login_at END
                WHERE LOWER(email) = LOWER(?)
                """,
                (
                    username_prefix,
                    display_name,
                    avatar_filename,
                    role,
                    1 if is_active else 0,
                    _json_dump(metadata),
                    now,
                    now,
                    1 if login else 0,
                    now,
                    email,
                ),
            )
            self._conn.commit()
            return True

    def get_user_profile(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    email,
                    username_prefix,
                    display_name,
                    avatar_filename,
                    role,
                    is_active,
                    created_at,
                    last_login_at,
                    last_seen_at,
                    metadata_json
                FROM users
                WHERE LOWER(email) = LOWER(?)
                """,
                (email,),
            ).fetchone()

        if row is None:
            return None

        return {
            "email": row["email"],
            "username_prefix": row["username_prefix"],
            "display_name": row["display_name"],
            "avatar_filename": row["avatar_filename"],
            "role": row["role"] or "user",
            "is_active": bool(row["is_active"] if row["is_active"] is not None else 1),
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
            "last_seen_at": row["last_seen_at"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
        }

    def create_task(self, task: dict[str, Any]) -> None:
        now = _utc_now_iso()
        payload = {
            "task_id": task.get("task_id"),
            "request_id": task.get("request_id"),
            "user_email": task.get("user_email"),
            "user_prefix": task.get("user_prefix"),
            "user_display_name": task.get("user_display_name"),
            "user_role": task.get("user_role", "user"),
            "avatar_filename": task.get("avatar_filename"),
            "workflow_key": task.get("workflow_key"),
            "workflow_name": task.get("workflow_name"),
            "workflow_version": task.get("workflow_version"),
            "workflow_category": task.get("workflow_category"),
            "workflow_type": task.get("workflow_type"),
            "status": task.get("status", "created"),
            "outcome": task.get("outcome"),
            "submitted_at": task.get("submitted_at") or now,
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "total_duration_ms": _safe_int(task.get("total_duration_ms")),
            "queue_duration_ms": _safe_int(task.get("queue_duration_ms")),
            "processing_duration_ms": _safe_int(task.get("processing_duration_ms")),
            "retry_count": _safe_int(task.get("retry_count")) or 0,
            "task_url": task.get("task_url"),
            "result_url": task.get("result_url"),
            "thumbnail_url": task.get("thumbnail_url"),
            "preview_url": task.get("preview_url"),
            "output_filename": task.get("output_filename"),
            "output_count": _safe_int(task.get("output_count")),
            "input_width": _safe_int(task.get("input_width")),
            "input_height": _safe_int(task.get("input_height")),
            "input_resolution": task.get("input_resolution"),
            "input_format": task.get("input_format"),
            "input_size_bytes": _safe_int(task.get("input_size_bytes")),
            "output_width": _safe_int(task.get("output_width")),
            "output_height": _safe_int(task.get("output_height")),
            "worker_id": task.get("worker_id"),
            "server_id": task.get("server_id"),
            "environment_name": task.get("environment_name"),
            "app_version": task.get("app_version") or self.app_version,
            "backend_version": task.get("backend_version") or self.backend_version,
            "prompt_type": task.get("prompt_type"),
            "source_page": task.get("source_page"),
            "session_id": task.get("session_id"),
            "browser_user_agent": task.get("browser_user_agent"),
            "feature_flags_json": _json_dump(task.get("feature_flags")),
            "settings_json": _json_dump(task.get("settings")),
            "request_summary_json": _json_dump(task.get("request_summary")),
            "result_summary_json": _json_dump(task.get("result_summary")),
            "error_message": task.get("error_message"),
            "failure_reason": task.get("failure_reason"),
            "failure_stage": task.get("failure_stage"),
            "is_archived": 1 if task.get("is_archived") else 0,
            "created_by": task.get("created_by") or task.get("user_email"),
            "updated_by": task.get("updated_by") or task.get("user_email"),
            "latest_stage": task.get("latest_stage"),
            "latest_message": task.get("latest_message"),
            "latest_progress_percent": _safe_float(task.get("latest_progress_percent")),
            "created_at": task.get("created_at") or now,
            "updated_at": task.get("updated_at") or now,
        }

        if not payload["task_id"]:
            raise ValueError("task_id is required")
        if not payload["user_email"]:
            raise ValueError("user_email is required")
        if not payload["workflow_key"]:
            raise ValueError("workflow_key is required")
        if not payload["workflow_name"]:
            raise ValueError("workflow_name is required")

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, request_id, user_email, user_prefix, user_display_name, user_role, avatar_filename,
                    workflow_key, workflow_name, workflow_version, workflow_category, workflow_type,
                    status, outcome, submitted_at, started_at, finished_at,
                    total_duration_ms, queue_duration_ms, processing_duration_ms, retry_count,
                    task_url, result_url, thumbnail_url, preview_url, output_filename, output_count,
                    input_width, input_height, input_resolution, input_format, input_size_bytes,
                    output_width, output_height, worker_id, server_id, environment_name,
                    app_version, backend_version, prompt_type, source_page, session_id, browser_user_agent,
                    feature_flags_json, settings_json, request_summary_json, result_summary_json,
                    error_message, failure_reason, failure_stage, is_archived,
                    created_by, updated_by, latest_stage, latest_message, latest_progress_percent,
                    created_at, updated_at
                ) VALUES (
                    :task_id, :request_id, :user_email, :user_prefix, :user_display_name, :user_role, :avatar_filename,
                    :workflow_key, :workflow_name, :workflow_version, :workflow_category, :workflow_type,
                    :status, :outcome, :submitted_at, :started_at, :finished_at,
                    :total_duration_ms, :queue_duration_ms, :processing_duration_ms, :retry_count,
                    :task_url, :result_url, :thumbnail_url, :preview_url, :output_filename, :output_count,
                    :input_width, :input_height, :input_resolution, :input_format, :input_size_bytes,
                    :output_width, :output_height, :worker_id, :server_id, :environment_name,
                    :app_version, :backend_version, :prompt_type, :source_page, :session_id, :browser_user_agent,
                    :feature_flags_json, :settings_json, :request_summary_json, :result_summary_json,
                    :error_message, :failure_reason, :failure_stage, :is_archived,
                    :created_by, :updated_by, :latest_stage, :latest_message, :latest_progress_percent,
                    :created_at, :updated_at
                )
                """,
                payload,
            )
            self._conn.commit()

    def update_task(self, task_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return

        normalized: dict[str, Any] = {}
        json_fields = {
            "feature_flags": "feature_flags_json",
            "settings": "settings_json",
            "request_summary": "request_summary_json",
            "result_summary": "result_summary_json",
        }
        for key, value in updates.items():
            if key in json_fields:
                normalized[json_fields[key]] = _json_dump(value)
            else:
                normalized[key] = value

        normalized["updated_at"] = _utc_now_iso()
        normalized["task_id"] = task_id

        assignments = ", ".join([f"{key} = :{key}" for key in normalized.keys() if key != "task_id"])
        sql = f"UPDATE tasks SET {assignments} WHERE task_id = :task_id"

        with self._lock:
            self._conn.execute(sql, normalized)
            self._conn.commit()

    def upsert_task_settings(
        self,
        *,
        task_id: str,
        feature_flags: dict[str, Any] | None,
        settings: dict[str, Any] | None,
        prompt_type: str | None,
    ) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO task_settings (
                    task_id, feature_flags_json, settings_json, prompt_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    feature_flags_json = excluded.feature_flags_json,
                    settings_json = excluded.settings_json,
                    prompt_type = excluded.prompt_type,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    _json_dump(feature_flags),
                    _json_dump(settings),
                    prompt_type,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def add_event(
        self,
        *,
        task_id: str,
        event_type: str,
        status: str | None = None,
        stage: str | None = None,
        message: str | None = None,
        node_id: str | None = None,
        progress_percent: float | int | None = None,
        metadata: dict[str, Any] | None = None,
        event_at: str | None = None,
    ) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO task_events (
                    task_id, event_type, status, stage, message, node_id,
                    progress_percent, event_at, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event_type,
                    status,
                    stage,
                    message,
                    node_id,
                    _safe_float(progress_percent),
                    event_at or now,
                    _json_dump(metadata),
                    now,
                ),
            )
            self._conn.commit()

    def add_output(
        self,
        *,
        task_id: str,
        output_index: int,
        result_url: str | None,
        thumbnail_url: str | None,
        preview_url: str | None,
        file_name: str | None,
        width: int | None,
        height: int | None,
    ) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO task_outputs (
                    task_id, output_index, result_url, thumbnail_url, preview_url, file_name, width, height, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    int(output_index),
                    result_url,
                    thumbnail_url,
                    preview_url,
                    file_name,
                    _safe_int(width),
                    _safe_int(height),
                    now,
                ),
            )
            self._conn.commit()

    @staticmethod
    def _cache_sort_key(path: Path) -> tuple[float, float]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return (float("inf"), float("inf"))
        atime = float(getattr(stat, "st_atime", 0.0) or 0.0)
        mtime = float(getattr(stat, "st_mtime", 0.0) or 0.0)
        last_used = atime if atime > 0 else mtime
        return (last_used, mtime)

    @staticmethod
    def _cache_group_key(path: Path) -> str:
        stem = str(path.stem)
        if "_" not in stem:
            return stem
        left, right = stem.rsplit("_", 1)
        if right.isdigit() and left:
            return f"{left}_{right}"
        return stem

    def _cache_entries(self) -> tuple[list[dict[str, Any]], int]:
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        for cache_kind, cache_dir in (("thumbnail", self.thumbnail_dir), ("preview", self.preview_dir)):
            if not cache_dir.exists():
                continue
            for file_path in cache_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                try:
                    stat = file_path.stat()
                except FileNotFoundError:
                    continue
                size = int(stat.st_size)
                last_used, mtime = self._cache_sort_key(file_path)
                entries.append(
                    {
                        "path": file_path,
                        "kind": cache_kind,
                        "size": size,
                        "last_used": last_used,
                        "mtime": mtime,
                        "group_key": self._cache_group_key(file_path),
                    }
                )
                total_bytes += size
        return entries, total_bytes

    def _enforce_media_disk_budget(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and self._thumbnail_cleanup_last_ts > 0
            and (now - self._thumbnail_cleanup_last_ts) < self.thumbnail_cleanup_min_interval_sec
        ):
            return

        self._thumbnail_cleanup_last_ts = now
        entries, total_bytes = self._cache_entries()

        if total_bytes >= self.thumbnail_warn_bytes:
            should_log_warning = (
                not self._thumbnail_warning_active
                or (now - self._thumbnail_warning_last_ts) >= 300
            )
            if should_log_warning:
                logger.warning(
                    "Local cache warning: %.2f GB used (warn %.2f GB, cap %.2f GB).",
                    total_bytes / (1024**3),
                    self.thumbnail_warn_bytes / (1024**3),
                    self.thumbnail_cap_bytes / (1024**3),
                )
                self._thumbnail_warning_last_ts = now
            self._thumbnail_warning_active = True
        elif total_bytes < int(self.thumbnail_warn_bytes * 0.9):
            self._thumbnail_warning_active = False

        if total_bytes <= self.thumbnail_cap_bytes:
            return

        grouped: dict[str, dict[str, Any]] = {}
        for entry in entries:
            bucket = grouped.setdefault(
                entry["group_key"],
                {
                    "entries": [],
                    "size": 0,
                    "last_used": float("inf"),
                    "mtime": float("inf"),
                },
            )
            bucket["entries"].append(entry)
            bucket["size"] += entry["size"]
            bucket["last_used"] = min(bucket["last_used"], entry["last_used"])
            bucket["mtime"] = min(bucket["mtime"], entry["mtime"])

        target_bytes = int(self.thumbnail_cap_bytes * 0.9)
        groups = sorted(grouped.values(), key=lambda item: (item["last_used"], item["mtime"]))

        reclaimed = 0
        deleted_files = 0
        deleted_groups = 0
        for bucket in groups:
            if total_bytes <= target_bytes:
                break
            bucket_deleted = False
            for entry in bucket["entries"]:
                file_path: Path = entry["path"]
                try:
                    file_path.unlink(missing_ok=True)
                    reclaimed += int(entry["size"])
                    deleted_files += 1
                    total_bytes -= int(entry["size"])
                    bucket_deleted = True
                except Exception as err:
                    logger.warning("Failed to remove cached preview %s during LRU cleanup: %s", file_path, err)
            if bucket_deleted:
                deleted_groups += 1

        logger.warning(
            "Cache cap exceeded. LRU cleanup removed %d file(s) across %d item group(s), reclaimed %.2f GB, current %.2f GB.",
            deleted_files,
            deleted_groups,
            reclaimed / (1024**3),
            max(total_bytes, 0) / (1024**3),
        )

    def _enforce_thumbnail_disk_budget(self, *, force: bool = False) -> None:
        # Backward-compatible alias used by older call-sites.
        self._enforce_media_disk_budget(force=force)

    def save_thumbnail(self, *, task_id: str, image: Image.Image, output_index: int = 0) -> str:
        resampling = getattr(Image, "Resampling", Image)
        thumb_image = image.convert("RGB")
        thumb_image.thumbnail((THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), resampling.LANCZOS)
        thumb_path = self.thumbnail_dir / f"{task_id}_{output_index}.jpg"
        thumb_image.save(thumb_path, format="JPEG", quality=85, optimize=True)
        self._enforce_media_disk_budget()
        return str(thumb_path)

    def save_preview(self, *, task_id: str, image: Image.Image, output_index: int = 0) -> str:
        resampling = getattr(Image, "Resampling", Image)
        preview_image = image.convert("RGB")
        preview_image.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE), resampling.LANCZOS)
        preview_path = self.preview_dir / f"{task_id}_{output_index}.webp"
        preview_image.save(preview_path, format="WEBP", quality=PREVIEW_WEBP_QUALITY, method=6)
        self._enforce_media_disk_budget()
        return str(preview_path)

    def list_user_history(self, email: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    task_id,
                    request_id,
                    workflow_name,
                    status,
                    outcome,
                    submitted_at,
                    finished_at,
                    total_duration_ms,
                    result_url,
                    thumbnail_url,
                    preview_url,
                    output_count,
                    failure_reason,
                    error_message
                FROM tasks
                WHERE LOWER(user_email) = LOWER(?)
                ORDER BY submitted_at DESC
                LIMIT ?
                """,
                (email, int(limit)),
            ).fetchall()

        return [dict(row) for row in rows]

    def ensure_default_favorite_categories(self, user_email: str) -> None:
        now = _utc_now_iso()
        with self._lock:
            for category_key, display_name, color, sort_order in DEFAULT_FAVORITE_CATEGORIES:
                self._conn.execute(
                    """
                    INSERT INTO favorite_categories (
                        user_email, category_key, display_name, color, sort_order, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(user_email, category_key) DO UPDATE SET
                        display_name = excluded.display_name,
                        color = COALESCE(favorite_categories.color, excluded.color),
                        sort_order = excluded.sort_order,
                        is_active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (user_email, category_key, display_name, color, sort_order, now, now),
                )
            self._conn.commit()

    def list_favorite_categories(self, user_email: str) -> list[dict[str, Any]]:
        self.ensure_default_favorite_categories(user_email)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT category_key, display_name, color, sort_order, is_active
                FROM favorite_categories
                WHERE LOWER(user_email) = LOWER(?) AND is_active = 1
                ORDER BY sort_order, display_name
                """,
                (user_email,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_favorite_category(
        self,
        *,
        user_email: str,
        category_key: str,
        display_name: str,
        color: str | None = None,
        sort_order: int = 100,
    ) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO favorite_categories (
                    user_email, category_key, display_name, color, sort_order, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(user_email, category_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    color = excluded.color,
                    sort_order = excluded.sort_order,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    user_email,
                    category_key,
                    display_name,
                    color,
                    int(sort_order),
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def set_task_favorite(
        self,
        *,
        task_id: str,
        user_email: str,
        is_favorite: bool,
        favorite_category_key: str | None = None,
        notes: str | None = None,
        is_pinned: bool | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._lock:
            task_row = self._conn.execute(
                """
                SELECT task_id
                FROM tasks
                WHERE task_id = ? AND LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
                """,
                (task_id, user_email),
            ).fetchone()
            if task_row is None:
                raise ValueError("History item was not found for this user.")

            self._conn.execute(
                """
                INSERT INTO task_favorites (
                    task_id, user_email, is_favorite, favorite_category_key, notes, is_pinned, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, user_email) DO UPDATE SET
                    is_favorite = excluded.is_favorite,
                    favorite_category_key = excluded.favorite_category_key,
                    notes = COALESCE(excluded.notes, task_favorites.notes),
                    is_pinned = COALESCE(excluded.is_pinned, task_favorites.is_pinned),
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    user_email,
                    1 if is_favorite else 0,
                    favorite_category_key,
                    notes,
                    1 if is_pinned else 0,
                    now,
                    now,
                ),
            )
            self._conn.commit()

        return self.get_task_favorite(task_id=task_id, user_email=user_email)

    def get_task_favorite(self, *, task_id: str, user_email: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    task_id,
                    user_email,
                    COALESCE(is_favorite, 0) AS is_favorite,
                    favorite_category_key,
                    notes,
                    COALESCE(is_pinned, 0) AS is_pinned,
                    updated_at
                FROM task_favorites
                WHERE task_id = ? AND LOWER(user_email) = LOWER(?)
                """,
                (task_id, user_email),
            ).fetchone()
        if row is None:
            return {
                "task_id": task_id,
                "user_email": user_email,
                "is_favorite": False,
                "favorite_category_key": None,
                "notes": None,
                "is_pinned": False,
                "updated_at": None,
            }
        record = dict(row)
        record["is_favorite"] = bool(record.get("is_favorite"))
        record["is_pinned"] = bool(record.get("is_pinned"))
        return record

    def get_history_item(self, *, user_email: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    t.task_id,
                    t.request_id,
                    t.workflow_name,
                    COALESCE(t.workflow_category, w.category, 'Uncategorized') AS workflow_category,
                    t.status,
                    t.submitted_at,
                    t.total_duration_ms,
                    t.result_url,
                    t.thumbnail_url,
                    t.preview_url,
                    t.output_filename,
                    t.output_width,
                    t.output_height,
                    t.error_message,
                    t.failure_reason,
                    t.latest_message,
                    COALESCE(tf.is_favorite, 0) AS is_favorite,
                    tf.favorite_category_key,
                    COALESCE(tf.is_pinned, 0) AS is_pinned
                FROM tasks t
                LEFT JOIN task_favorites tf
                    ON tf.task_id = t.task_id AND LOWER(tf.user_email) = LOWER(?)
                LEFT JOIN workflows w
                    ON w.workflow_key = t.workflow_key
                WHERE t.task_id = ? AND LOWER(t.user_email) = LOWER(?) AND COALESCE(t.is_deleted, 0) = 0
                LIMIT 1
                """,
                (user_email, task_id, user_email),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["is_favorite"] = bool(item.get("is_favorite"))
        item["is_pinned"] = bool(item.get("is_pinned"))
        return item

    def query_history_gallery(
        self,
        *,
        user_email: str,
        search: str | None = None,
        workflow_name: str | None = None,
        workflow_category: str | None = None,
        status: str | None = None,
        favorites_only: bool = False,
        favorite_category_key: str | None = None,
        sort: str = "newest",
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 36,
    ) -> dict[str, Any]:
        self.ensure_default_favorite_categories(user_email)
        page = max(1, int(page))
        page_size = max(1, min(120, int(page_size)))

        where_clauses = [
            "LOWER(t.user_email) = LOWER(?)",
            "COALESCE(t.is_deleted, 0) = 0",
        ]
        params: list[Any] = [user_email]

        if search:
            term = f"%{search.strip().lower()}%"
            where_clauses.append(
                "("
                "LOWER(COALESCE(t.display_title, '')) LIKE ? OR "
                "LOWER(COALESCE(t.workflow_name, '')) LIKE ? OR "
                "LOWER(COALESCE(t.task_id, '')) LIKE ? OR "
                "LOWER(COALESCE(t.request_id, '')) LIKE ?"
                ")"
            )
            params.extend([term, term, term, term])

        if workflow_name:
            where_clauses.append("t.workflow_name = ?")
            params.append(workflow_name)

        if workflow_category:
            where_clauses.append("COALESCE(t.workflow_category, w.category, 'Uncategorized') = ?")
            params.append(workflow_category)

        if status:
            where_clauses.append("LOWER(COALESCE(t.status, '')) = LOWER(?)")
            params.append(status)

        if favorites_only:
            where_clauses.append("COALESCE(tf.is_favorite, 0) = 1")

        if favorite_category_key:
            where_clauses.append("COALESCE(tf.favorite_category_key, '') = ?")
            params.append(favorite_category_key)

        if date_from:
            where_clauses.append("COALESCE(t.submitted_at, t.created_at) >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("COALESCE(t.submitted_at, t.created_at) <= ?")
            params.append(date_to)

        order_by = {
            "newest": "COALESCE(t.submitted_at, t.created_at) DESC",
            "oldest": "COALESCE(t.submitted_at, t.created_at) ASC",
            "duration_desc": "COALESCE(t.total_duration_ms, 0) DESC, COALESCE(t.submitted_at, t.created_at) DESC",
            "duration_asc": "COALESCE(t.total_duration_ms, 0) ASC, COALESCE(t.submitted_at, t.created_at) DESC",
        }.get(sort, "COALESCE(t.submitted_at, t.created_at) DESC")

        where_sql = " AND ".join(where_clauses)
        offset = (page - 1) * page_size

        with self._lock:
            total_row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM tasks t
                LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
                LEFT JOIN task_favorites tf
                    ON tf.task_id = t.task_id AND LOWER(tf.user_email) = LOWER(?)
                WHERE {where_sql}
                """,
                [user_email, *params],
            ).fetchone()

            rows = self._conn.execute(
                f"""
                SELECT
                    t.task_id,
                    t.request_id,
                    t.workflow_name,
                    COALESCE(t.workflow_category, w.category, 'Uncategorized') AS workflow_category,
                    t.workflow_type,
                    t.status,
                    COALESCE(t.submitted_at, t.created_at) AS created_at,
                    t.total_duration_ms,
                    t.result_url,
                    t.thumbnail_url,
                    t.preview_url,
                    t.output_filename,
                    t.output_count,
                    t.output_width,
                    t.output_height,
                    t.failure_reason,
                    t.error_message,
                    COALESCE(tf.is_favorite, 0) AS is_favorite,
                    tf.favorite_category_key,
                    COALESCE(tf.is_pinned, 0) AS is_pinned
                FROM tasks t
                LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
                LEFT JOIN task_favorites tf
                    ON tf.task_id = t.task_id AND LOWER(tf.user_email) = LOWER(?)
                WHERE {where_sql}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                [user_email, *params, page_size, offset],
            ).fetchall()

            workflow_rows = self._conn.execute(
                """
                SELECT workflow_name, COUNT(*) AS total
                FROM tasks
                WHERE LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
                GROUP BY workflow_name
                ORDER BY total DESC, workflow_name
                """,
                (user_email,),
            ).fetchall()

            category_rows = self._conn.execute(
                """
                SELECT COALESCE(t.workflow_category, w.category, 'Uncategorized') AS workflow_category, COUNT(*) AS total
                FROM tasks t
                LEFT JOIN workflows w ON w.workflow_key = t.workflow_key
                WHERE LOWER(t.user_email) = LOWER(?) AND COALESCE(t.is_deleted, 0) = 0
                GROUP BY COALESCE(t.workflow_category, w.category, 'Uncategorized')
                ORDER BY total DESC, workflow_category
                """,
                (user_email,),
            ).fetchall()

            status_rows = self._conn.execute(
                """
                SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS total
                FROM tasks
                WHERE LOWER(user_email) = LOWER(?) AND COALESCE(is_deleted, 0) = 0
                GROUP BY COALESCE(status, 'unknown')
                ORDER BY total DESC
                """,
                (user_email,),
            ).fetchall()

            fav_total_row = self._conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM task_favorites
                WHERE LOWER(user_email) = LOWER(?) AND COALESCE(is_favorite, 0) = 1
                """,
                (user_email,),
            ).fetchone()

        items: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["is_favorite"] = bool(record.get("is_favorite"))
            record["is_pinned"] = bool(record.get("is_pinned"))
            items.append(record)

        total_items = int(total_row["total"] if total_row else 0)
        total_pages = max(1, (total_items + page_size - 1) // page_size)

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "workflow_facets": [dict(row) for row in workflow_rows],
            "workflow_category_facets": [dict(row) for row in category_rows],
            "status_facets": [dict(row) for row in status_rows],
            "favorite_categories": self.list_favorite_categories(user_email),
            "favorites_total": int(fav_total_row["total"] if fav_total_row else 0),
        }

    def get_admin_overview(self, *, days: int = 30, limit: int = 10) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        since_iso = since.isoformat(timespec="seconds")

        with self._lock:
            totals_row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,
                    AVG(total_duration_ms) AS avg_total_duration_ms
                FROM tasks
                WHERE submitted_at >= ?
                """,
                (since_iso,),
            ).fetchone()

            workflows = self._conn.execute(
                """
                SELECT
                    workflow_name,
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,
                    AVG(total_duration_ms) AS avg_total_duration_ms
                FROM tasks
                WHERE submitted_at >= ?
                GROUP BY workflow_name
                ORDER BY total_tasks DESC
                LIMIT ?
                """,
                (since_iso, int(limit)),
            ).fetchall()

            users = self._conn.execute(
                """
                SELECT
                    user_email,
                    user_display_name,
                    COUNT(*) AS total_tasks,
                    AVG(total_duration_ms) AS avg_total_duration_ms,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks
                FROM tasks
                WHERE submitted_at >= ?
                GROUP BY user_email, user_display_name
                ORDER BY total_tasks DESC
                LIMIT ?
                """,
                (since_iso, int(limit)),
            ).fetchall()

            failures = self._conn.execute(
                """
                SELECT
                    submitted_at,
                    user_email,
                    workflow_name,
                    failure_reason,
                    error_message,
                    task_id,
                    request_id
                FROM tasks
                WHERE submitted_at >= ? AND status = 'failed'
                ORDER BY submitted_at DESC
                LIMIT ?
                """,
                (since_iso, int(limit)),
            ).fetchall()

        totals = dict(totals_row) if totals_row else {}
        total_tasks = int(totals.get("total_tasks") or 0)
        completed_tasks = int(totals.get("completed_tasks") or 0)
        failed_tasks = int(totals.get("failed_tasks") or 0)
        success_rate = (completed_tasks / total_tasks * 100.0) if total_tasks else 0.0

        return {
            "window_days": int(days),
            "summary": {
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "failed_tasks": failed_tasks,
                "success_rate_percent": round(success_rate, 2),
                "avg_total_duration_ms": _safe_int(totals.get("avg_total_duration_ms")),
            },
            "top_workflows": [dict(row) for row in workflows],
            "top_users": [dict(row) for row in users],
            "recent_failures": [dict(row) for row in failures],
        }

    def get_admin_dashboard(self, *, days: int = 30, limit: int = 50) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        since_iso = since.isoformat(timespec="seconds")
        safe_limit = max(10, int(limit))

        with self._lock:
            summary_row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,
                    AVG(total_duration_ms) AS avg_total_duration_ms,
                    COUNT(DISTINCT LOWER(user_email)) AS active_users
                FROM tasks
                WHERE submitted_at >= ?
                """,
                (since_iso,),
            ).fetchone()

            trend_rows = self._conn.execute(
                """
                SELECT
                    substr(COALESCE(submitted_at, created_at), 1, 10) AS day,
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks
                FROM tasks
                WHERE submitted_at >= ?
                GROUP BY substr(COALESCE(submitted_at, created_at), 1, 10)
                ORDER BY day ASC
                """,
                (since_iso,),
            ).fetchall()

            workflow_rows = self._conn.execute(
                """
                SELECT
                    workflow_name,
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,
                    AVG(total_duration_ms) AS avg_total_duration_ms
                FROM tasks
                WHERE submitted_at >= ?
                GROUP BY workflow_name
                ORDER BY total_tasks DESC, workflow_name ASC
                """,
                (since_iso,),
            ).fetchall()

            top_users_rows = self._conn.execute(
                """
                SELECT
                    user_email,
                    user_display_name,
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,
                    AVG(total_duration_ms) AS avg_total_duration_ms
                FROM tasks
                WHERE submitted_at >= ?
                GROUP BY user_email, user_display_name
                ORDER BY total_tasks DESC, user_email ASC
                LIMIT ?
                """,
                (since_iso, safe_limit),
            ).fetchall()

            failures_rows = self._conn.execute(
                """
                SELECT
                    submitted_at,
                    user_email,
                    workflow_name,
                    status,
                    failure_reason,
                    error_message,
                    task_id,
                    request_id
                FROM tasks
                WHERE submitted_at >= ? AND status = 'failed'
                ORDER BY submitted_at DESC
                LIMIT ?
                """,
                (since_iso, safe_limit),
            ).fetchall()

        summary = dict(summary_row) if summary_row else {}
        total_tasks = int(summary.get("total_tasks") or 0)
        completed_tasks = int(summary.get("completed_tasks") or 0)
        failed_tasks = int(summary.get("failed_tasks") or 0)
        success_rate = (completed_tasks / total_tasks * 100.0) if total_tasks else 0.0

        return {
            "window_days": int(days),
            "summary": {
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "failed_tasks": failed_tasks,
                "active_users": int(summary.get("active_users") or 0),
                "success_rate_percent": round(success_rate, 2),
                "avg_total_duration_ms": _safe_int(summary.get("avg_total_duration_ms")),
            },
            "trend": [dict(row) for row in trend_rows],
            "workflows": [dict(row) for row in workflow_rows],
            "top_users": [dict(row) for row in top_users_rows],
            "recent_failures": [dict(row) for row in failures_rows],
        }


_store_singleton: AnalyticsStore | None = None


def get_analytics_store() -> AnalyticsStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = AnalyticsStore()
    return _store_singleton
