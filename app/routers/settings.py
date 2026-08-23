"""Gestor de conexion al ACS (solo admin).

Permite cambiar a que GenieACS apunta la API (NBI URL) sin editar ficheros ni
reiniciar: el valor se guarda en la BD y el cliente lo lee en cada llamada.
"""
import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional

from .. import db, runtime
from ..deps import require_admin

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_admin)])


class SettingsIn(BaseModel):
    nbi_url: Optional[str] = Field(None, description="URL del NBI de GenieACS, p.ej. http://10.99.99.5:7557")
    nbi_timeout: Optional[float] = Field(None, ge=1, le=300)
    default_connection_request: Optional[bool] = None


class TestIn(BaseModel):
    nbi_url: Optional[str] = None   # si no se envia, prueba la URL efectiva actual


@router.get("")
async def get_settings():
    return runtime.effective()


@router.put("")
async def update_settings(body: SettingsIn):
    if body.nbi_url is not None:
        url = body.nbi_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "La URL debe empezar por http:// o https://"}
        db.set_setting(runtime.K_NBI_URL, url)
    if body.nbi_timeout is not None:
        db.set_setting(runtime.K_NBI_TIMEOUT, str(body.nbi_timeout))
    if body.default_connection_request is not None:
        db.set_setting(runtime.K_DEFAULT_CR, "true" if body.default_connection_request else "false")
    return {"ok": True, **runtime.effective()}


@router.post("/test")
async def test_connection(body: TestIn):
    """Verifica que el NBI responde (consulta trivial de dispositivos)."""
    url = (body.nbi_url.strip().rstrip("/") if body.nbi_url else runtime.nbi_url())
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL invalida"}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(base_url=url, timeout=8.0) as c:
            r = await c.get("/devices/", params={"query": "{}", "projection": "_id"})
        ms = round((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            n = len(r.json())
            return {"ok": True, "url": url, "latency_ms": ms, "devices": n,
                    "detail": f"Conectado. {n} dispositivo(s) visibles."}
        return {"ok": False, "url": url, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "url": url, "error": type(e).__name__ + ": " + str(e)}
