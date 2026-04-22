from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

import bcrypt

from analytics_store import AnalyticsStore
from auth_service import COMPANY_DOMAIN, email_prefix, normalize_email


def _resolve_db_path(path_arg: str | None) -> Path:
    if path_arg:
        path = Path(path_arg)
    else:
        env_path = os.getenv("USER_DB_PATH", "users.db")
        path = Path(env_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


def _display_name_from_prefix(prefix: str) -> str:
    tokens = [x for x in prefix.replace("_", ".").replace("-", ".").split(".") if x]
    if not tokens:
        return "BrickVisual User"
    return " ".join(token.capitalize() for token in tokens)


def _validate_company_email(raw: str) -> str:
    email = normalize_email(raw)
    if not email or email.count("@") != 1:
        raise ValueError("Invalid email format.")
    if not email.endswith(f"@{COMPANY_DOMAIN}"):
        raise ValueError(f"Email must be @{COMPANY_DOMAIN}.")
    return email


def _read_password(password_arg: str | None, *, confirm: bool = True) -> str:
    if password_arg:
        return password_arg

    pwd = getpass.getpass("Password: ")
    if confirm:
        pwd2 = getpass.getpass("Confirm password: ")
        if pwd != pwd2:
            raise ValueError("Passwords do not match.")
    if not pwd:
        raise ValueError("Password cannot be empty.")
    return pwd


def _hash_password(plain: str) -> bytes:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _upsert_user(
    *,
    db_path: Path,
    email: str,
    password: str,
    role: str,
    active: bool,
    display_name: str | None,
    avatar_filename: str | None,
) -> None:
    prefix = email_prefix(email)
    display = display_name or _display_name_from_prefix(prefix)
    pwd_hash = _hash_password(password)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO users (
                email, pwd_hash, role, is_active,
                username_prefix, display_name, avatar_filename,
                created_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(email) DO UPDATE SET
                pwd_hash = excluded.pwd_hash,
                role = excluded.role,
                is_active = excluded.is_active,
                username_prefix = excluded.username_prefix,
                display_name = excluded.display_name,
                avatar_filename = COALESCE(excluded.avatar_filename, users.avatar_filename),
                last_seen_at = datetime('now')
            """,
            (
                email,
                pwd_hash,
                role,
                1 if active else 0,
                prefix,
                display,
                avatar_filename,
            ),
        )
        conn.commit()


def _set_password(*, db_path: Path, email: str, password: str) -> None:
    pwd_hash = _hash_password(password)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET pwd_hash = ?, last_seen_at = datetime('now') WHERE LOWER(email) = LOWER(?)",
            (pwd_hash, email),
        )
        conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"User not found: {email}")


def _set_role(*, db_path: Path, email: str, role: str) -> None:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET role = ?, last_seen_at = datetime('now') WHERE LOWER(email) = LOWER(?)",
            (role, email),
        )
        conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"User not found: {email}")


def _set_active(*, db_path: Path, email: str, active: bool) -> None:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET is_active = ?, last_seen_at = datetime('now') WHERE LOWER(email) = LOWER(?)",
            (1 if active else 0, email),
        )
        conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"User not found: {email}")


def _show_user(*, db_path: Path, email: str) -> None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT email, role, COALESCE(is_active,1) AS is_active,
                   username_prefix, display_name, avatar_filename,
                   created_at, last_login_at, last_seen_at
            FROM users
            WHERE LOWER(email) = LOWER(?)
            """,
            (email,),
        ).fetchone()

    if row is None:
        raise ValueError(f"User not found: {email}")

    print(f"Email:         {row['email']}")
    print(f"Role:          {row['role'] or 'user'}")
    print(f"Active:        {bool(row['is_active'])}")
    print(f"Prefix:        {row['username_prefix'] or '-'}")
    print(f"Display Name:  {row['display_name'] or '-'}")
    print(f"Avatar File:   {row['avatar_filename'] or '-'}")
    print(f"Created At:    {row['created_at'] or '-'}")
    print(f"Last Login:    {row['last_login_at'] or '-'}")
    print(f"Last Seen:     {row['last_seen_at'] or '-'}")


def _list_users(*, db_path: Path, role: str | None, active_only: bool) -> None:
    query = (
        "SELECT email, role, COALESCE(is_active,1) AS is_active, display_name, last_login_at "
        "FROM users WHERE 1=1"
    )
    params: list[object] = []

    if role:
        query += " AND LOWER(COALESCE(role,'user')) = LOWER(?)"
        params.append(role)
    if active_only:
        query += " AND COALESCE(is_active,1) = 1"

    query += " ORDER BY email"

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        print("No users found.")
        return

    print(f"Found {len(rows)} user(s):")
    for row in rows:
        print(
            f"- {row['email']} | role={row['role'] or 'user'} | active={bool(row['is_active'])} | "
            f"name={row['display_name'] or '-'} | last_login={row['last_login_at'] or '-'}"
        )


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help="Path to users.db (default: USER_DB_PATH or ./users.db)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage BrickVisual app users in users.db",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_upsert = sub.add_parser("upsert", help="Create or update a user (sets password)")
    _add_shared_args(p_upsert)
    p_upsert.add_argument("--email", required=True, help=f"Company email (@{COMPANY_DOMAIN})")
    p_upsert.add_argument("--password", help="Password (omit to prompt securely)")
    p_upsert.add_argument("--role", choices=["user", "admin"], default="user")
    p_upsert.add_argument("--inactive", action="store_true", help="Create/update as inactive")
    p_upsert.add_argument("--display-name", help="Override display name")
    p_upsert.add_argument("--avatar-filename", help="Store avatar filename (optional)")

    p_setpwd = sub.add_parser("set-password", help="Reset password for an existing user")
    _add_shared_args(p_setpwd)
    p_setpwd.add_argument("--email", required=True)
    p_setpwd.add_argument("--password", help="New password (omit to prompt securely)")

    p_role = sub.add_parser("set-role", help="Change user role")
    _add_shared_args(p_role)
    p_role.add_argument("--email", required=True)
    p_role.add_argument("--role", choices=["user", "admin"], required=True)

    p_activate = sub.add_parser("activate", help="Activate a user")
    _add_shared_args(p_activate)
    p_activate.add_argument("--email", required=True)

    p_deactivate = sub.add_parser("deactivate", help="Deactivate a user")
    _add_shared_args(p_deactivate)
    p_deactivate.add_argument("--email", required=True)

    p_show = sub.add_parser("show", help="Show details for one user")
    _add_shared_args(p_show)
    p_show.add_argument("--email", required=True)

    p_list = sub.add_parser("list", help="List users")
    _add_shared_args(p_list)
    p_list.add_argument("--role", choices=["user", "admin"], help="Filter by role")
    p_list.add_argument("--active-only", action="store_true", help="Show only active users")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        db_path = _resolve_db_path(getattr(args, "db", None))
        # Ensure schema exists/migrated before we operate.
        AnalyticsStore(db_path=db_path)

        cmd = args.command
        if cmd == "upsert":
            email = _validate_company_email(args.email)
            password = _read_password(args.password, confirm=True)
            _upsert_user(
                db_path=db_path,
                email=email,
                password=password,
                role=args.role,
                active=not args.inactive,
                display_name=args.display_name,
                avatar_filename=args.avatar_filename,
            )
            print(f"User upserted: {email} (role={args.role}, active={not args.inactive})")
            return 0

        if cmd == "set-password":
            email = _validate_company_email(args.email)
            password = _read_password(args.password, confirm=True)
            _set_password(db_path=db_path, email=email, password=password)
            print(f"Password updated: {email}")
            return 0

        if cmd == "set-role":
            email = _validate_company_email(args.email)
            _set_role(db_path=db_path, email=email, role=args.role)
            print(f"Role updated: {email} -> {args.role}")
            return 0

        if cmd == "activate":
            email = _validate_company_email(args.email)
            _set_active(db_path=db_path, email=email, active=True)
            print(f"Activated: {email}")
            return 0

        if cmd == "deactivate":
            email = _validate_company_email(args.email)
            _set_active(db_path=db_path, email=email, active=False)
            print(f"Deactivated: {email}")
            return 0

        if cmd == "show":
            email = _validate_company_email(args.email)
            _show_user(db_path=db_path, email=email)
            return 0

        if cmd == "list":
            _list_users(db_path=db_path, role=args.role, active_only=args.active_only)
            return 0

        parser.error(f"Unknown command: {cmd}")
        return 2

    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
