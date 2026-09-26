"""Catalogo de perfiles por modelo + firmware (solo admin).

Cada vez que se abre la ficha de un equipo, el panel deduce de su arbol que
instancia es la WAN, la LAN y cada radio, y guarda ese perfil bajo la clave
fabricante|clase|modelo|firmware. Asi el siguiente equipo del mismo modelo ya
sale completo, y aqui se puede ver de que evidencia salio cada deduccion.

Si alguna deduccion no acierta en un modelo concreto, se corrige la ruta de ese
concepto sin tocar codigo: la correccion manda sobre el mapa y sobre lo deducido.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from .. import catalog, db
from ..deps import require_admin
from ..genieacs import genie

router = APIRouter(prefix="/profiles", tags=["profiles"], dependencies=[Depends(require_admin)])


class OverrideIn(BaseModel):
    concept: str = Field(..., min_length=1, max_length=64)
    path: Optional[str] = Field(None, max_length=512, description="null borra la correccion")


def _salida(fila: dict, equipos: int | None = None) -> dict:
    try:
        perfil = json.loads(fila.get("profile") or "{}")
    except ValueError:
        perfil = {}
    try:
        over = json.loads(fila.get("overrides") or "{}")
    except ValueError:
        over = {}
    return {"key": fila["key"], "manufacturer": fila.get("manufacturer"),
            "product_class": fila.get("product_class"), "model": fila.get("model"),
            "firmware": fila.get("firmware"), "profile": perfil, "overrides": over,
            "devices": equipos if equipos is not None else fila.get("devices"),
            "updated_at": fila.get("updated_at")}


@router.get("")
async def list_profiles():
    """Perfiles aprendidos, con cuantos equipos de la flota usan cada uno."""
    filas = db.list_model_profiles()
    conteo: dict[str, int] = {}
    try:
        rows = await genie.query_devices({}, ["_id", "_deviceId",
                                              "Device.DeviceInfo.ModelName",
                                              "Device.DeviceInfo.SoftwareVersion",
                                              "InternetGatewayDevice.DeviceInfo.ModelName",
                                              "InternetGatewayDevice.DeviceInfo.SoftwareVersion"])
        for d in rows:
            conteo[catalog.clave(d)] = conteo.get(catalog.clave(d), 0) + 1
    except Exception:
        conteo = {}
    return [_salida(f, conteo.get(f["key"])) for f in filas]


@router.get("/{key:path}")
async def get_profile(key: str):
    fila = db.get_model_profile(key)
    if not fila:
        raise HTTPException(404, "Perfil no encontrado")
    return _salida(fila)


@router.put("/{key:path}/override")
async def set_override(key: str, body: OverrideIn):
    """Corrige (o borra, con path vacio) la ruta de un concepto para ese modelo."""
    if not db.get_model_profile(key):
        raise HTTPException(404, "Perfil no encontrado")
    over = catalog.set_override(key, body.concept, (body.path or "").strip() or None)
    return {"ok": True, "overrides": over}
