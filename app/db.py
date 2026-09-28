"""BD de usuarios en SQLite (stdlib, sin ORM).

Un usuario pertenece a un ISP (isp_tag) o es admin (ve toda la flota).
La multi-tenencia se apoya en los tags de GenieACS: cada CPE de un ISP debe
llevar el tag == isp_tag del usuario.
"""
import json
import logging
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
CREATE TABLE IF NOT EXISTS model_tree (
    key          TEXT PRIMARY KEY,           -- fabricante|clase|modelo|firmware
    root         TEXT,                       -- Device (TR-181) o InternetGatewayDevice (TR-098)
    paths        TEXT NOT NULL,              -- JSON {ruta: escribible}; RUTAS, nunca valores
    n_params     INTEGER NOT NULL DEFAULT 0, -- tamano de la union
    last_device  TEXT,                       -- ultimo equipo que aporto
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS model_tree_device (
    key        TEXT NOT NULL,                -- modelo al que aporto
    device_id  TEXT NOT NULL,
    n_params   INTEGER NOT NULL DEFAULT 0,   -- cuanto arbol tenia ESE equipo
    seen_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (key, device_id)
);
CREATE TABLE IF NOT EXISTS ia_evento (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL DEFAULT (datetime('now')),
    user      TEXT,
    tipo      TEXT NOT NULL,       -- propuesta | confirmacion | prueba
    model_key TEXT,                -- modelo de CPE: fabricante|clase|modelo|firmware
    device_id TEXT,                -- equipo del que salio el arbol
    proveedor TEXT,                -- URL base del proveedor (nunca la clave)
    modelo_ia TEXT,                -- modelo de lenguaje que respondio
    detalle   TEXT,                -- JSON: conceptos y rutas; NUNCA valores
    ok        INTEGER NOT NULL DEFAULT 1,
    ms        INTEGER,             -- lo que tardo el proveedor
    error     TEXT
);
CREATE INDEX IF NOT EXISTS ix_ia_evento_tipo ON ia_evento (tipo, id DESC);
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


log = logging.getLogger("genieacs_api.db")


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
                         profile_json: str, device_seen: str | None = None,
                         devices: int = 1) -> None:
    """Guarda el perfil deducido de un modelo. No pisa las correcciones manuales.

    `devices` cuenta equipos distintos vistos con este perfil; para eso se guarda
    la lista de ids vistos dentro del propio JSON del perfil, no aqui."""
    with connect() as c:
        c.execute(
            "INSERT INTO model_profile (key, manufacturer, product_class, model, firmware, "
            "profile, devices, updated_at) VALUES (?,?,?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET profile=excluded.profile, "
            "manufacturer=excluded.manufacturer, product_class=excluded.product_class, "
            "model=excluded.model, firmware=excluded.firmware, updated_at=datetime('now')",
            (key, manufacturer, product_class, model, firmware, profile_json, devices),
        )


def set_model_devices(key: str, n: int) -> None:
    with connect() as c:
        c.execute("UPDATE model_profile SET devices=? WHERE key=?", (n, key))


def set_model_overrides(key: str, overrides_json: str | None) -> None:
    with connect() as c:
        c.execute("UPDATE model_profile SET overrides=?, updated_at=datetime('now') WHERE key=?",
                  (overrides_json, key))


# ---------------------------------------------------------------------------
# Arboles por modelo. La union de rutas de todos los equipos de ese modelo:
# un equipo recien adoptado (arbol a medias) no puede borrar lo que ya se sabe.

# ---------------------------------------------------------------------------
# Registro de la IA. Aparte de audit_log a proposito: alli se anota la peticion
# (lo escribe un middleware), y aqui la RESPUESTA del modelo, que es lo que
# interesa auditar cuando una propuesta acaba cambiando el perfil de un modelo.

# cuantas entradas se conservan: lo justo para auditar lo reciente sin que la
# tabla crezca para siempre en una flota que homologa a diario
IA_EVENTOS_MAX = 5000


def log_ia(tipo: str, user: str | None = None, model_key: str | None = None,
           device_id: str | None = None, proveedor: str | None = None,
           modelo_ia: str | None = None, detalle: str | None = None,
           ok: bool = True, ms: int | None = None, error: str | None = None) -> None:
    """Anota lo que hizo la IA. No puede tumbar la peticion: si falla el
    registro, la propuesta que YA se pidio al proveedor sigue siendo valida."""
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO ia_evento (user, tipo, model_key, device_id, proveedor, "
                "modelo_ia, detalle, ok, ms, error) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user, tipo, model_key, device_id, proveedor, modelo_ia, detalle,
                 1 if ok else 0, ms, error))
            c.execute("DELETE FROM ia_evento WHERE id <= "
                      "(SELECT MAX(id) FROM ia_evento) - ?", (IA_EVENTOS_MAX,))
    except sqlite3.Error:
        log.exception("no se pudo registrar el evento de IA (%s)", tipo)


def list_ia_eventos(limit: int = 100, tipo: str | None = None) -> list[dict]:
    sql = "SELECT * FROM ia_evento"
    params: list = []
    if tipo:
        sql += " WHERE tipo=?"
        params.append(tipo)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params)]


def get_model_tree(key: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM model_tree WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None


def merge_model_tree(key: str, root: str | None, rutas: dict, device_id: str | None) -> dict:
    """Une las rutas de un equipo con las del modelo, en UNA transaccion.

    Leer en una conexion, unir en Python y escribir en otra deja una ventana en
    la que dos lecturas simultaneas del mismo modelo pierden una de las dos
    uniones. Hoy no es alcanzable (el servicio corre con un worker y esto es
    sincrono), pero se convierte en perdida de datos el dia que alguien ponga
    --workers N o un segundo proceso. BEGIN IMMEDIATE lo cierra."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        fila = c.execute("SELECT paths, root FROM model_tree WHERE key=?", (key,)).fetchone()
        antes = {}
        if fila:
            try:
                data = json.loads(fila["paths"] or "{}")
                # una fila corrupta (restauracion a medias, edicion a mano) no puede
                # tumbar la ficha de todos los equipos de ese modelo: se rehace
                antes = data if isinstance(data, dict) else {}
            except (ValueError, TypeError):
                log.warning("arbol corrupto en %s; se rehace", key)
        union = dict(antes)
        for ruta, escribible in rutas.items():
            union[ruta] = bool(union.get(ruta)) or bool(escribible)
        if not fila or union != antes:
            # la raiz solo se pisa si el equipo trae una: un doc sin raiz no
            # puede borrar la que ya se sabia
            c.execute(
                "INSERT INTO model_tree (key, root, paths, n_params, last_device, updated_at) "
                "VALUES (?,?,?,?,?,datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET root=COALESCE(excluded.root, model_tree.root), "
                "paths=excluded.paths, n_params=excluded.n_params, "
                "last_device=excluded.last_device, updated_at=datetime('now')",
                (key, root, json.dumps(union, sort_keys=True), len(union), device_id))
        if device_id:
            c.execute(
                "INSERT INTO model_tree_device (key, device_id, n_params, seen_at) "
                "VALUES (?,?,?,datetime('now')) "
                "ON CONFLICT(key, device_id) DO UPDATE SET n_params=excluded.n_params, "
                "seen_at=datetime('now')",
                (key, device_id, len(rutas)))
    return {"n_params": len(union), "nuevas": len(union) - len(antes)}


def borrar_model_tree(key: str) -> bool:
    """Olvida el arbol de un modelo y quien aporto. Se vuelve a aprender solo.

    La union solo crece: si entra una ruta por un equipo que reportaba mal su
    firmware, se queda para siempre. Esta es la forma de rehacerla."""
    with connect() as c:
        n = c.execute("DELETE FROM model_tree WHERE key=?", (key,)).rowcount
        c.execute("DELETE FROM model_tree_device WHERE key=?", (key,))
    return bool(n)


def model_tree_devices(key: str) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT device_id, n_params, seen_at FROM model_tree_device WHERE key=? "
            "ORDER BY n_params DESC, seen_at DESC", (key,))]


def model_trees_completos() -> list[dict]:
    """Arbol + perfil + correcciones de cada modelo, para exportar.

    Sin nada de equipos: el catalogo es del modelo, no de la flota."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT t.key, t.root, t.paths, t.n_params, "
            "       p.manufacturer, p.product_class, p.model, p.firmware, "
            "       p.profile, p.overrides "
            "FROM model_tree t LEFT JOIN model_profile p ON p.key=t.key "
            "ORDER BY t.key")]


def list_model_trees() -> list[dict]:
    """Un resumen por modelo, sin arrastrar las rutas (son cientos por fila)."""
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT t.key, t.root, t.n_params, t.last_device, t.created_at, t.updated_at, "
            "       p.manufacturer, p.product_class, p.model, p.firmware, "
            "       (SELECT COUNT(*) FROM model_tree_device d WHERE d.key=t.key) AS devices, "
            "       (SELECT MAX(n_params) FROM model_tree_device d WHERE d.key=t.key) AS max_equipo "
            "FROM model_tree t LEFT JOIN model_profile p ON p.key=t.key "
            "ORDER BY p.manufacturer, p.model, p.firmware")]


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
