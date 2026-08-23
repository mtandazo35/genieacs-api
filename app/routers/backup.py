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

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..deps import authorized_device
from ..genieacs import genie
from ..parammap import CONFIG_KEYS, pick_map, resolve

router = APIRouter(prefix="/devices/{device_id}", tags=["backup"])


class AutoRestoreIn(BaseModel):
    enabled: bool


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
    except Exception:
        pass
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
    return {"exists": True, "autorestore": bool(row.get("autorestore")),
            "updated_at": row.get("updated_at"),
            "params": {p: v[0] for p, v in cfg.items()}}


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
async def enforce_once() -> int:
    """Revisa los equipos con auto-restauracion y reaplica lo que difiera."""
    fixed = 0
    for row in db.list_autorestore():
        dev_id = row["device_id"]
        try:
            desired = json.loads(row["config"])
            doc = await genie.get_device(dev_id, list(desired.keys()))
            if not doc:
                continue
            drift = []
            for path, (val, xsd) in desired.items():
                cur = _read_path(doc, path)
                if cur is None:
                    continue
                if str(cur) != str(val):
                    drift.append([path, val] + ([xsd] if xsd else []))
            if drift:
                await genie.set_parameter_values(dev_id, drift, connection_request=False)
                fixed += 1
        except Exception:
            continue
    return fixed


async def enforce_loop(interval: int = 600):
    while True:
        await asyncio.sleep(interval)
        try:
            await enforce_once()
        except Exception:
            pass
