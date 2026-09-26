"""Equipos nuevos: bandeja de sin asignar y rangos por ISP (solo admin)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from .. import db, discovery
from ..deps import CurrentUser, require_admin
from ..genieacs import genie

router = APIRouter(prefix="/discovery", tags=["discovery"], dependencies=[Depends(require_admin)])


class ReglaIn(BaseModel):
    cidr: str = Field(..., max_length=64, description="p.ej. 100.125.125.0/24")
    isp_tag: str = Field(..., min_length=1, max_length=64)
    comment: Optional[str] = Field(None, max_length=200)


class AsignarIn(BaseModel):
    device_id: str = Field(..., max_length=256)
    isp_tag: str = Field(..., min_length=1, max_length=64)


class ModoIn(BaseModel):
    auto: bool
    interval: Optional[int] = Field(None, ge=30, le=3600)


@router.get("")
async def pendientes():
    """Equipos sin tag de ISP, con la sugerencia de a quien pertenecen."""
    return {"modo": "automatico" if discovery.automatico() else "sugerencia",
            "intervalo": discovery.intervalo(),
            "equipos": await discovery.escanear()}


@router.get("/rules")
async def reglas():
    return db.list_owner_rules()


@router.post("/rules", status_code=201)
async def crear_regla(body: ReglaIn):
    import ipaddress
    try:
        red = ipaddress.ip_network(body.cidr, strict=False)
    except ValueError:
        raise HTTPException(400, "Rango no valido; usa notacion CIDR (p.ej. 10.0.0.0/24)")
    db.add_owner_rule(str(red), body.isp_tag.strip(), (body.comment or "").strip() or None)
    return {"ok": True, "cidr": str(red), "isp_tag": body.isp_tag}


@router.delete("/rules/{rule_id}")
async def borrar_regla(rule_id: int):
    if not db.get_owner_rule(rule_id):
        raise HTTPException(404, "Regla no encontrada")
    db.delete_owner_rule(rule_id)
    return {"ok": True}


@router.post("/assign")
async def asignar(body: AsignarIn, request: Request, admin: CurrentUser = Depends(require_admin)):
    """Asigna un equipo a un ISP (confirmar una sugerencia, o hacerlo a mano)."""
    if not await genie.device_exists(body.device_id):
        raise HTTPException(404, "Equipo no encontrado en el ACS")
    await discovery.asignar(body.device_id, body.isp_tag.strip(), quien=admin.username,
                            motivo="confirmado en el panel")
    return {"ok": True}


@router.put("/mode")
async def modo(body: ModoIn):
    """Cambia entre sugerir y asignar sola, y el intervalo del barrido."""
    db.set_setting(discovery.K_AUTO, "true" if body.auto else "false")
    if body.interval:
        db.set_setting(discovery.K_INTERVALO, str(body.interval))
    return {"ok": True, "modo": "automatico" if body.auto else "sugerencia",
            "intervalo": discovery.intervalo()}
