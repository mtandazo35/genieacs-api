"""BD de usuarios en SQLite (stdlib, sin ORM).

Un usuario pertenece a un ISP (isp_tag) o es admin (ve toda la flota).
La multi-tenencia se apoya en los tags de GenieACS: cada CPE de un ISP debe
llevar el tag == isp_tag del usuario.
"""
import os
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
CREATE TABLE IF NOT EXISTS device_meta (
    device_id TEXT PRIMARY KEY,
    name      TEXT,
    customer  TEXT,
    notes     TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS owner_rule (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cidr       TEXT NOT NULL,      -- rango de IPs de los CPE de ese ISP
    isp_tag    TEXT NOT NULL,      -- tag que se le pone al equipo
    comment    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS model_profile (
    key          TEXT PRIMARY KEY,           -- fabricante|clase|modelo|firmware
    manufacturer TEXT,
    product_class TEXT,
    model        TEXT,
    firmware     TEXT,
    profile      TEXT,                       -- JSON: instancias deducidas + evidencia
    overrides    TEXT,                       -- JSON: concepto -> path corregido a mano
    devices      INTEGER NOT NULL DEFAULT 0, -- equipos vistos con este perfil
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL DEFAULT (datetime('now')),
    user      TEXT,
    device_id TEXT,
    action    TEXT,
    method    TEXT,
    path      TEXT,
    status    INTEGER
);
"""

# Columnas anadidas despues de la primera version: se agregan en BDs existentes.
_MIGRATIONS = {
    "users": {"token_version": "INTEGER NOT NULL DEFAULT 0"},
    "audit_log": {"client_ip": "TEXT", "user_agent": "TEXT", "request_id": "TEXT"},
    "device_config": {
        "attempts": "INTEGER NOT NULL DEFAULT 0",   # reaplicaciones seguidas sin corregir el drift
        "last_attempt": "TEXT",                      # ISO UTC del ultimo reintento
        "suspended_until": "TEXT",                   # ISO UTC; auto-restauracion en pausa
        "last_error": "TEXT",
    },
}


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
        for table, cols in _MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    # guarda claves de CPE (WiFi/PPPoE/admin): solo legible por el servicio
    try:
        os.chmod(get_settings().db_path, 0o600)
    except OSError:
        pass


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
    # desactivar/reactivar invalida los tokens emitidos antes
    with connect() as c:
        c.execute("UPDATE users SET active=?, token_version=token_version+1 WHERE username=?",
                  (1 if active else 0, username))


def update_password(username: str, password_hash: str) -> None:
    # cambiar la clave cierra todas las sesiones abiertas de ese usuario
    with connect() as c:
        c.execute("UPDATE users SET password=?, token_version=token_version+1 WHERE username=?",
                  (password_hash, username))


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
            "ON CONFLICT(device_id) DO UPDATE SET autorestore=excluded.autorestore, "
            "attempts=0, suspended_until=NULL, last_error=NULL",   # re-activar = empezar de cero
            (device_id, 1 if enabled else 0),
        )


def list_autorestore() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT device_id, config, attempts, last_attempt, suspended_until "
            "FROM device_config WHERE autorestore=1 AND config IS NOT NULL"
        ).fetchall()]


def set_autorestore_state(device_id: str, attempts: int, last_attempt: str | None,
                          suspended_until: str | None, last_error: str | None) -> None:
    with connect() as c:
        c.execute(
            "UPDATE device_config SET attempts=?, last_attempt=?, suspended_until=?, last_error=? "
            "WHERE device_id=?",
            (attempts, last_attempt, suspended_until, last_error, device_id),
        )


# ---- nombre/cliente por equipo ----
def get_device_meta(device_id: str) -> dict:
    with connect() as c:
        row = c.execute("SELECT name, customer, notes FROM device_meta WHERE device_id=?", (device_id,)).fetchone()
        return dict(row) if row else {"name": None, "customer": None, "notes": None}


def set_device_meta(device_id: str, name, customer, notes) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO device_meta (device_id, name, customer, notes, updated_at) VALUES (?,?,?,?,datetime('now')) "
            "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, customer=excluded.customer, "
            "notes=excluded.notes, updated_at=datetime('now')",
            (device_id, name, customer, notes),
        )


def all_device_meta() -> dict:
    with connect() as c:
        return {r["device_id"]: {"name": r["name"], "customer": r["customer"]}
                for r in c.execute("SELECT device_id, name, customer FROM device_meta").fetchall()}


# ---- auditoria (registro de cambios) ----
def add_audit(user, device_id, action, method, path, status,
              client_ip=None, user_agent=None, request_id=None) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO audit_log (user, device_id, action, method, path, status, "
            "client_ip, user_agent, request_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (user, device_id, action, method, path, status,
             client_ip, (user_agent or "")[:300] or None, request_id),
        )


def list_audit(device_id=None, limit=300) -> list[dict]:
    with connect() as c:
        if device_id:
            rows = c.execute("SELECT * FROM audit_log WHERE device_id=? ORDER BY id DESC LIMIT ?",
                             (device_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---- rangos de IP por ISP (descubrimiento de equipos nuevos) ----
def list_owner_rules() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM owner_rule ORDER BY isp_tag, cidr").fetchall()]


def get_owner_rule(rule_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM owner_rule WHERE id=?", (rule_id,)).fetchone()
        return dict(row) if row else None


def add_owner_rule(cidr: str, isp_tag: str, comment: str | None = None) -> None:
    with connect() as c:
        c.execute("INSERT INTO owner_rule (cidr, isp_tag, comment) VALUES (?,?,?)",
                  (cidr, isp_tag, comment))


def delete_owner_rule(rule_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM owner_rule WHERE id=?", (rule_id,))


# ---- catalogo de perfiles por modelo+firmware ----
def get_model_profile(key: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM model_profile WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None


def upsert_model_profile(key: str, manufacturer, product_class, model, firmware,
                         profile_json: str, device_seen: str | None = None) -> None:
    """Guarda el perfil deducido de un modelo. No pisa las correcciones manuales.

    `devices` cuenta equipos distintos vistos con este perfil; para eso se guarda
    la lista de ids vistos dentro del propio JSON del perfil, no aqui."""
    with connect() as c:
        c.execute(
            "INSERT INTO model_profile (key, manufacturer, product_class, model, firmware, "
            "profile, devices, updated_at) VALUES (?,?,?,?,?,?,1,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET profile=excluded.profile, "
            "manufacturer=excluded.manufacturer, product_class=excluded.product_class, "
            "model=excluded.model, firmware=excluded.firmware, updated_at=datetime('now')",
            (key, manufacturer, product_class, model, firmware, profile_json),
        )


def set_model_devices(key: str, n: int) -> None:
    with connect() as c:
        c.execute("UPDATE model_profile SET devices=? WHERE key=?", (n, key))


def set_model_overrides(key: str, overrides_json: str | None) -> None:
    with connect() as c:
        c.execute("UPDATE model_profile SET overrides=?, updated_at=datetime('now') WHERE key=?",
                  (overrides_json, key))


def list_model_profiles() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM model_profile ORDER BY manufacturer, model, firmware").fetchall()]


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
