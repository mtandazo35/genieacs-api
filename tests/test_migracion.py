"""Actualizar una instalacion existente no debe perder datos ni dejar a nadie fuera."""
import json
import os
import sqlite3
import sys

from conftest import ROOT

# esquema de la version 1.3.0 (antes de token_version, IP en auditoria, etc.)
OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','isp')), isp_tag TEXT,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE device_config (device_id TEXT PRIMARY KEY, config TEXT,
    autorestore INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE device_meta (device_id TEXT PRIMARY KEY, name TEXT, customer TEXT, notes TEXT, updated_at TEXT);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')),
    user TEXT, device_id TEXT, action TEXT, method TEXT, path TEXT, status INTEGER);
"""


def test_bd_de_la_version_anterior_se_migra_sin_perder_nada(env, fake, tmp_path):
    from fastapi.testclient import TestClient
    from app.config import get_settings
    from app.security import hash_password
    path = get_settings().db_path
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    # usuario con clave de 6 caracteres creado con la version anterior
    c.execute("INSERT INTO users (username, password, role) VALUES ('admin', ?, 'admin')",
              (hash_password("abc123"),))
    c.execute("INSERT INTO device_config VALUES ('dev1', ?, 1, datetime('now'))",
              (json.dumps({"X.SSID": ["Casa", None]}),))
    c.execute("INSERT INTO audit_log (user, action) VALUES ('admin', 'cambio viejo')")
    c.commit(); c.close()

    from app.main import app
    with TestClient(app) as client:
        # la politica de 12 caracteres aplica a claves NUEVAS: el admin sigue entrando
        r = client.post("/auth/login", data={"username": "admin", "password": "abc123"})
        assert r.status_code == 200
        h = {"Authorization": "Bearer " + r.json()["access_token"]}
        assert client.get("/auth/me", headers=h).status_code == 200
        audit = client.get("/auth/audit", headers=h).json()
        assert any(a["action"] == "cambio viejo" for a in audit)
    from app import db
    row = db.get_device_config("dev1")
    assert row["autorestore"] == 1 and row["attempts"] == 0 and "Casa" in row["config"]


def test_respaldo_de_bd_conserva_solo_las_ultimas_n(env, monkeypatch, tmp_path):
    sys.path.insert(0, ROOT)
    import manage
    from app import db
    db.init_db()
    stamps = iter(f"genieacs_api-2026010{i}-000000.db" for i in range(1, 6))
    monkeypatch.setattr(manage.time, "strftime", lambda fmt: next(stamps))
    dest = tmp_path / "bk"
    for _ in range(5):
        manage.backup_db(str(dest), keep=3)
    files = sorted(os.listdir(dest))
    assert files == [f"genieacs_api-2026010{i}-000000.db" for i in (3, 4, 5)]
    # cada copia es una BD valida y completa
    c = sqlite3.connect(dest / files[-1])
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='users'").fetchone()[0] == 1
    c.close()
