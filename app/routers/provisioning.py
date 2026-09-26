"""Generador del script de MikroTik para entregar el ACS por DHCP (solo admin).

El panel no se conecta a ningun router ni guarda credenciales: genera el texto
y tu lo pegas. Ver app/dhcp_tr069.py para el detalle de las codificaciones.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Optional

from .. import runtime
from ..dhcp_tr069 import DhcpInvalido, generar
from ..deps import require_admin

router = APIRouter(prefix="/provisioning", tags=["provisioning"],
                   dependencies=[Depends(require_admin)])


class RedIn(BaseModel):
    cidr: str = Field(..., max_length=64, description="p.ej. 100.125.125.0/24")
    gateway: Optional[str] = Field(None, max_length=64)
    pool_from: Optional[str] = Field(None, max_length=64)
    pool_to: Optional[str] = Field(None, max_length=64)
    comment: Optional[str] = Field(None, max_length=120)


class DhcpIn(BaseModel):
    acs_url: Optional[str] = Field(None, max_length=200,
                                   description="por defecto, el CWMP del ACS configurado")
    networks: list[RedIn] = Field(..., min_length=1, max_length=50)
    routeros: str = Field("7", pattern="^(6|7)$")
    encoding: str = Field("tlv", pattern="^(tlv|plana|ambas)$")
    include_125: bool = True
    download: bool = False


def _acs_por_defecto() -> str:
    """El CWMP vive en el mismo host que el NBI, en el 7547."""
    nbi = runtime.nbi_url()
    host = nbi.split("//", 1)[-1].split(":")[0].split("/")[0]
    return f"http://{host}:7547/"


@router.get("/dhcp/defaults")
async def valores_por_defecto():
    return {"acs_url": _acs_por_defecto(), "routeros": "7", "encoding": "tlv"}


@router.post("/dhcp/script")
async def script_dhcp(body: DhcpIn):
    """Devuelve el script listo para pegar en el MikroTik (no aplica nada)."""
    try:
        res = generar(body.acs_url or _acs_por_defecto(),
                      [r.model_dump() for r in body.networks],
                      routeros=body.routeros, codificacion=body.encoding,
                      incluir_125=body.include_125)
    except DhcpInvalido as e:
        raise HTTPException(400, str(e))
    if body.download:
        return Response(res["script"], media_type="text/plain",
                        headers={"Content-Disposition": 'attachment; filename="tr069-dhcp.rsc"'})
    return res
