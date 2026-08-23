"""BD de usuarios en SQLite (stdlib, sin ORM).

Un usuario pertenece a un ISP (isp_tag) o es admin (ve toda la flota).
La multi-tenencia se apoya en los tags de GenieACS: cada CPE de un ISP debe
llevar el tag == isp_tag del usuario.
"""
import sqlite3
from contextlib import contextmanager

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('admin','isp')),
    isp_tag     TEXT,                 -- NULL para admin
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS device_config (
    device_id   TEXT PRIMARY KEY,
    config      TEXT,                          -- JSON {path: [value, type]}
    autorestore INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(get_settings().db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(_SCHEMA)


def get_user(username: str) -> dict | None:
    """Devuelve el usuario exista o no activo (el chequeo de activo se hace
    en el login y en la validacion del token)."""
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def create_user(username: str, password_hash: str, role: str, isp_tag: str | None) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO users (username, password, role, isp_tag) VALUES (?,?,?,?)",
            (username, password_hash, role, isp_tag),
        )


def list_users() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, username, role, isp_tag, active, created_at FROM users ORDER BY id"
        ).fetchall()]


def set_active(username: str, active: bool) -> None:
    with connect() as c:
        c.execute("UPDATE users SET active=? WHERE username=?", (1 if active else 0, username))


def update_password(username: str, password_hash: str) -> None:
    with connect() as c:
        c.execute("UPDATE users SET password=? WHERE username=?", (password_hash, username))


def delete_user(username: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM users WHERE username=?", (username,))


def count_active_admins() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1").fetchone()["n"]


# ---- respaldo de configuracion por equipo ----
def save_device_config(device_id: str, config_json: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO device_config (device_id, config, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(device_id) DO UPDATE SET config=excluded.config, updated_at=datetime('now')",
            (device_id, config_json),
        )


def get_device_config(device_id: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM device_config WHERE device_id=?", (device_id,)).fetchone()
        return dict(row) if row else None


def set_autorestore(device_id: str, enabled: bool) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO device_config (device_id, autorestore, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(device_id) DO UPDATE SET autorestore=excluded.autorestore",
            (device_id, 1 if enabled else 0),
        )


def list_autorestore() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT device_id, config FROM device_config WHERE autorestore=1 AND config IS NOT NULL"
        ).fetchall()]


# ---- settings (clave/valor) ----
def get_setting(key: str) -> str | None:
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
