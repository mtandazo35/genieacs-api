"""Respaldo de configuracion y auto-restauracion tras factory reset.

Idea: guardamos la configuracion "deseada" de cada equipo (SSID, claves, IP LAN,
DHCP, DNS, PPPoE, WAN, hora...). Si el cliente resetea de fabrica, el equipo se
re-vincula al ACS en su BOOTSTRAP y un bucle en la API detecta que sus valores
volvieron a los de fabrica (drift) y reaplica la config guardada.

Se hace 100% desde la API (sin provisions/extensiones en el ACS) para no exponer
al cwmp a preconditions fragiles.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..config import get_settings
from ..deps import authorized_device
from ..genieacs import genie
from ..parammap import CONFIG_KEYS, pick_map, resolve, writeonly_paths

log = logging.getLogger("genieacs_api.backup")

router = APIRouter(prefix="/devices/{device_id}", tags=["backup"])

# rutas cuyo valor es una credencial: nunca se devuelven por la API
_SECRET_RE = re.compile(r"(password|passphrase|presharedkey|wepkey|secret)", re.IGNORECASE)
MASK = "********"


def is_secret_path(path: str) -> bool:
    return bool(_SECRET_RE.search(path))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AutoRestoreIn(BaseModel):
    enabled: bool


def merge_device_config(device_id: str, pairs: list) -> None:
    """Fusiona cambios recien aplicados en el respaldo, para que nunca quede viejo.

    pairs: lista de [path, value] o [path, value, type]. Solo actualiza si el
    equipo YA tiene respaldo o auto-restauracion activa (si no, no crea nada)."""
    row = db.get_device_config(device_id)
    if not row or (not row.get("config") and not row.get("autorestore")):
        return
    try:
        cfg = json.loads(row["config"]) if row.get("config") else {}
    except ValueError:
        log.exception("respaldo corrupto de %s; se reescribe", device_id)
        cfg = {}
    for p in pairs:
        if not p or p[1] is None:
            continue
        path = p[0]
        value = p[1]
        typ = p[2] if len(p) > 2 else None
        cfg[path] = [value, typ]
    db.save_device_config(device_id, json.dumps(cfg))


def _read_path(doc: dict, path: str):
    node = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node.get("_value") if isinstance(node, dict) else None


def _config_paths(pmap: dict):
    """[(path, type)] de los parametros restaurables que soporta el modelo."""
    out = []
    for k in CONFIG_KEYS:
        r = resolve(pmap, k)
        if r:
            out.append(r)   # (path, xsd_type)
    return out


async def _snapshot(device_id: str, pmap: dict) -> dict:
    paths = _config_paths(pmap)
    doc = await genie.get_device(device_id, [p for p, _ in paths])
    cfg = {}
    # Preservar valores write-only ya capturados (claves WiFi/PPPoE del TP-Link):
    # no se pueden releer del equipo, asi que se arrastran del respaldo previo
    # para que un /backup manual no los borre.
    wo = writeonly_paths(pmap)
    if wo:
        prev = db.get_device_config(device_id)
        if prev and prev.get("config"):
            try:
                prev_cfg = json.loads(prev["config"])
            except ValueError:
                log.exception("respaldo previo corrupto de %s", device_id)
                prev_cfg = {}
            for p in wo:
                v = prev_cfg.get(p)
                if v and v[0] not in (None, ""):
                    cfg[p] = v
    for path, xsd in paths:
        val = _read_path(doc or {}, path)
        if val is not None and val != "":
            cfg[path] = [val, xsd]
    return cfg


@router.post("/backup")
async def make_backup(device_id: str, dev=Depends(authorized_device)):
    """Lee la config actual del equipo y la guarda como respaldo."""
    pmap = pick_map(dev)
    # pedir valores frescos antes de fotografiar
    paths = [p for p, _ in _config_paths(pmap)]
    try:
        await genie.get_parameter_values(device_id, paths)
    except Exception as e:
        # sin lectura fresca se fotografia lo que tenga el ACS en cache
        log.warning("backup %s: no se pudo pedir valores frescos: %s", device_id, e)
    await asyncio.sleep(1.5)
    cfg = await _snapshot(device_id, pmap)
    db.save_device_config(device_id, json.dumps(cfg))
    return {"ok": True, "saved": len(cfg), "detail": f"Respaldo guardado ({len(cfg)} parametros)."}


@router.get("/backup")
async def get_backup(device_id: str, dev=Depends(authorized_device)):
    row = db.get_device_config(device_id)
    if not row or not row.get("config"):
        return {"exists": False, "autorestore": bool(row and row.get("autorestore"))}
    cfg = json.loads(row["config"])
    # las claves (WiFi/PPPoE/admin) se guardan para restaurar, pero no se devuelven
    return {"exists": True, "autorestore": bool(row.get("autorestore")),
            "updated_at": row.get("updated_at"),
            "suspended_until": row.get("suspended_until"),
            "last_error": row.get("last_error"),
            "params": {p: (MASK if is_secret_path(p) and v[0] not in (None, "") else v[0])
                       for p, v in cfg.items()}}


@router.post("/restore")
async def restore_now(device_id: str, dev=Depends(authorized_device)):
    """Reaplica la config guardada al equipo ahora."""
    row = db.get_device_config(device_id)
    if not row or not row.get("config"):
        return {"ok": False, "detail": "No hay respaldo guardado"}
    cfg = json.loads(row["config"])
    values = [[p, v[0]] + ([v[1]] if v[1] else []) for p, v in cfg.items()]
    res = await genie.set_parameter_values(device_id, values)
    return {"ok": True, "applied": res["applied"], "queued": res["queued"],
            "detail": f"Restaurando {len(values)} parametros."}


@router.post("/autorestore")
async def toggle_autorestore(device_id: str, body: AutoRestoreIn, dev=Depends(authorized_device)):
    # asegurar que haya respaldo al activar
    if body.enabled:
        row = db.get_device_config(device_id)
        if not row or not row.get("config"):
            pmap = pick_map(dev)
            cfg = await _snapshot(device_id, pmap)
            db.save_device_config(device_id, json.dumps(cfg))
    db.set_autorestore(device_id, body.enabled)
    return {"ok": True, "autorestore": body.enabled}


# ---- bucle de enforcement (auto-restauracion) ---------------------------
# Protecciones:
# - Solo se reintenta si el equipo reporto (_lastInform) DESPUES del ultimo
#   intento: si esta apagado, la tarea anterior sigue pendiente en el ACS y
#   encolar otra cada 10 min solo acumula tareas.
# - Si el drift persiste tras N intentos (el CPE rechaza o revierte el valor),
#   se suspende unas horas y se registra el motivo, en vez de insistir sin fin.
async def enforce_device(row: dict, now: datetime | None = None) -> str:
    """Revisa un equipo. Devuelve: skipped | ok | applied | suspended | waiting."""
    s = get_settings()
    now = now or _utcnow()
    dev_id = row["device_id"]
    susp = _parse_ts(row.get("suspended_until"))
    if susp and now < susp:
        return "skipped"
    desired = json.loads(row["config"])
    doc = await genie.get_device(dev_id, list(desired.keys()) + ["_lastInform"])
    if not doc:
        return "skipped"
    last_attempt = _parse_ts(row.get("last_attempt"))
    last_inform = _parse_ts(doc.get("_lastInform"))
    if last_attempt and (last_inform is None or last_inform <= last_attempt):
        return "waiting"
    drift = []
    for path, (val, xsd) in desired.items():
        cur = _read_path(doc, path)
        if cur is None:
            continue
        if str(cur) != str(val):
            drift.append([path, val] + ([xsd] if xsd else []))
    attempts = row.get("attempts") or 0
    if not drift:
        if attempts or row.get("suspended_until") or last_attempt:
            db.set_autorestore_state(dev_id, 0, None, None, None)
        return "ok"
    if attempts >= s.autorestore_max_attempts:
        until = now + timedelta(hours=s.autorestore_suspend_hours)
        paths = ", ".join(d[0] for d in drift[:5])
        msg = f"El equipo no conserva {len(drift)} parametro(s) tras {attempts} intentos: {paths}"
        log.warning("auto-restauracion de %s suspendida hasta %s: %s", dev_id, until.isoformat(), msg)
        db.set_autorestore_state(dev_id, 0, None, until.isoformat(), msg)
        return "suspended"
    await genie.set_parameter_values(dev_id, drift, connection_request=False)
    db.set_autorestore_state(dev_id, attempts + 1, now.isoformat(), None, None)
    return "applied"


async def enforce_once() -> int:
    """Revisa los equipos con auto-restauracion y reaplica lo que difiera."""
    fixed = 0
    for row in db.list_autorestore():
        try:
            if await enforce_device(row) == "applied":
                fixed += 1
        except Exception as e:
            log.exception("auto-restauracion de %s fallo", row["device_id"])
            db.set_autorestore_state(row["device_id"], row.get("attempts") or 0, row.get("last_attempt"),
                                     row.get("suspended_until"), f"{type(e).__name__}: {e}"[:300])
    return fixed


async def enforce_loop(interval: int = 600):
    while True:
        await asyncio.sleep(interval)
        try:
            n = await enforce_once()
            if n:
                log.info("auto-restauracion: config reaplicada en %d equipo(s)", n)
        except Exception:
            log.exception("bucle de auto-restauracion fallo")
