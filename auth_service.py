from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt
from PIL import Image, ImageDraw

from analytics_store import AnalyticsStore, get_analytics_store

logger = logging.getLogger(__name__)


COMPANY_DOMAIN = os.getenv("COMPANY_EMAIL_DOMAIN", "brickvisual.com").strip().lower()


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def email_prefix(email: str) -> str:
    return normalize_email(email).split("@", 1)[0]


def is_company_email(email: str) -> bool:
    normalized = normalize_email(email)
    return normalized.endswith(f"@{COMPANY_DOMAIN}") and normalized.count("@") == 1


def _title_from_prefix(prefix: str) -> str:
    parts = [token for token in prefix.replace("_", ".").replace("-", ".").split(".") if token]
    if not parts:
        return "BrickVisual User"
    return " ".join(part[:1].upper() + part[1:] for part in parts)


@dataclass(frozen=True)
class AuthIdentity:
    email: str
    username_prefix: str
    display_name: str
    role: str
    avatar_filename: str | None
    avatar_path: str | None


class BrickAuthService:
    def __init__(
        self,
        *,
        store: AnalyticsStore | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.store = store or get_analytics_store()
        self.db_path = Path(db_path) if db_path else self.store.db_path

        image_dir_env = os.getenv("BRICKER_IMAGE_DIR", "bricker_image")
        image_dir = Path(image_dir_env)
        if not image_dir.is_absolute():
            image_dir = Path(__file__).resolve().parent / image_dir
        self.image_dir = image_dir
        self.image_dir.mkdir(parents=True, exist_ok=True)

        self.default_avatar_filename = os.getenv("DEFAULT_AVATAR_FILENAME", "default_avatar.png")
        self.default_avatar_path = self.image_dir / self.default_avatar_filename
        self._ensure_default_avatar()

        self.admin_emails = {
            normalize_email(item)
            for item in os.getenv("APP_ADMIN_EMAILS", "").split(",")
            if normalize_email(item)
        }

        self._avatar_index = self._build_avatar_index()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_default_avatar(self) -> None:
        if self.default_avatar_path.exists():
            return
        try:
            img = Image.new("RGB", (256, 256), (12, 20, 36))
            draw = ImageDraw.Draw(img)
            draw.ellipse((28, 28, 228, 228), fill=(30, 64, 116))
            draw.ellipse((86, 74, 170, 158), fill=(88, 182, 255))
            draw.rounded_rectangle((70, 154, 186, 220), radius=40, fill=(88, 182, 255))
            img.save(self.default_avatar_path, format="PNG")
        except Exception as err:
            logger.warning("Could not create default avatar placeholder: %s", err)

    def _build_avatar_index(self) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        for file_path in self.image_dir.glob("*.*"):
            if file_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            mapping[file_path.stem.lower()] = file_path
        return mapping

    def _avatar_path_for_prefix(self, prefix: str) -> Path:
        match = self._avatar_index.get(prefix.lower())
        if match and match.exists():
            return match
        if self.default_avatar_path.exists():
            return self.default_avatar_path
        return self.default_avatar_path

    def _load_user_row(self, email: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT
                    email,
                    pwd_hash,
                    COALESCE(is_active, 1) AS is_active,
                    role,
                    username_prefix,
                    display_name,
                    avatar_filename
                FROM users
                WHERE LOWER(email) = LOWER(?)
                """,
                (email,),
            ).fetchone()

    @staticmethod
    def _password_ok(password: str, pwd_hash: bytes | str | memoryview | None) -> bool:
        if pwd_hash is None:
            return False
        if isinstance(pwd_hash, memoryview):
            hash_bytes = bytes(pwd_hash)
        elif isinstance(pwd_hash, str):
            hash_bytes = pwd_hash.encode("utf-8")
        else:
            hash_bytes = bytes(pwd_hash)

        try:
            return bcrypt.checkpw(password.encode("utf-8"), hash_bytes)
        except Exception:
            return False

    def _resolve_role(self, email: str, db_role: str | None) -> str:
        normalized = normalize_email(email)
        if db_role:
            role = db_role.strip().lower()
            if role in {"admin", "user"}:
                return role
        if normalized in self.admin_emails:
            return "admin"
        return "user"

    def _build_identity_from_row(self, email: str, row: sqlite3.Row | None) -> AuthIdentity:
        normalized_email = normalize_email(email)
        prefix = (
            str(row["username_prefix"]).strip() if row and row["username_prefix"] else email_prefix(normalized_email)
        )
        display_name = (
            str(row["display_name"]).strip() if row and row["display_name"] else _title_from_prefix(prefix)
        )
        role = self._resolve_role(normalized_email, str(row["role"]).strip() if row and row["role"] else None)

        avatar_file: str | None = None
        if row and row["avatar_filename"]:
            avatar_file = str(row["avatar_filename"])
            candidate = self.image_dir / avatar_file
            if not candidate.exists():
                avatar_file = None

        if not avatar_file:
            avatar_path = self._avatar_path_for_prefix(prefix)
            avatar_file = avatar_path.name if avatar_path else None
        else:
            avatar_path = self.image_dir / avatar_file

        return AuthIdentity(
            email=normalized_email,
            username_prefix=prefix,
            display_name=display_name,
            role=role,
            avatar_filename=avatar_file,
            avatar_path=str(avatar_path) if avatar_path else None,
        )

    def authenticate(self, username: str, password: str) -> bool:
        email = normalize_email(username)
        if not is_company_email(email):
            return False

        row = self._load_user_row(email)
        if row is None:
            return False

        if not bool(row["is_active"]):
            return False

        if not self._password_ok(password=password, pwd_hash=row["pwd_hash"]):
            return False

        identity = self._build_identity_from_row(email, row)
        self.store.update_user_profile(
            email=identity.email,
            username_prefix=identity.username_prefix,
            display_name=identity.display_name,
            avatar_filename=identity.avatar_filename,
            role=identity.role,
            is_active=True,
            metadata={"auth_provider": "gradio-basic", "company_domain": COMPANY_DOMAIN},
            login=True,
        )
        return True

    def get_identity(self, email: str) -> AuthIdentity:
        normalized = normalize_email(email)
        row = self._load_user_row(normalized)
        identity = self._build_identity_from_row(normalized, row)

        # Keep profile fields synchronized for analytics and dashboard joins.
        self.store.update_user_profile(
            email=identity.email,
            username_prefix=identity.username_prefix,
            display_name=identity.display_name,
            avatar_filename=identity.avatar_filename,
            role=identity.role,
            is_active=True,
            metadata={"avatar_source": "bricker_image"},
            login=False,
        )
        return identity

    def session_key(self, email: str, user_agent: str | None = None) -> str:
        digest_source = f"{normalize_email(email)}|{(user_agent or '').strip()}"
        return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:24]


_auth_singleton: BrickAuthService | None = None


def get_auth_service() -> BrickAuthService:
    global _auth_singleton
    if _auth_singleton is None:
        _auth_singleton = BrickAuthService()
    return _auth_singleton
