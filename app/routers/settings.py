"""Gestor de conexion al ACS (solo admin).

Permite cambiar a que GenieACS apunta la API (NBI URL) sin editar ficheros ni
reiniciar: el valor se guarda en la BD y el cliente lo lee en cada llamada.
"""
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from .. import db, llm, runtime
from ..config import get_settings as app_settings
from ..deps import require_admin
from ..netguard import BlockedURL, check_allowed_url, parse_networks

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_admin)])


async def _nbi_problem(url: str) -> str | None:
    """None si la URL del NBI es aceptable; si no, el motivo."""
    try:
        await check_allowed_url(url, parse_networks(app_settings().allowed_nbi_networks))
    except BlockedURL as e:
        return f"{e}. Redes permitidas: GENIEACS_API_ALLOWED_NBI_NETWORKS"
    return None


class SettingsIn(BaseModel):
    nbi_url: Optional[str] = Field(None, description="URL del NBI de GenieACS, p.ej. http://10.99.99.5:7557")
    nbi_timeout: Optional[float] = Field(None, ge=1, le=300)
    default_connection_request: Optional[bool] = None


class LLMIn(BaseModel):
    provider: Optional[str] = Field(None, pattern="^(groq|openai|openrouter|local)$")
    api_key: Optional[str] = Field(None, max_length=300,
                                   description="vacio = borrar la clave guardada")
    model: Optional[str] = Field(None, max_length=120)
    base_url: Optional[str] = Field(None, max_length=200)


class TestIn(BaseModel):
    nbi_url: Optional[str] = None   # si no se envia, prueba la URL efectiva actual


@router.get("")
async def get_settings():
    return runtime.effective()


@router.put("")
async def update_settings(body: SettingsIn):
    if body.nbi_url is not None:
        url = body.nbi_url.strip().rstrip("/")
        problem = await _nbi_problem(url)
        if problem:
            return {"ok": False, "error": problem}
        db.set_setting(runtime.K_NBI_URL, url)
    if body.nbi_timeout is not None:
        db.set_setting(runtime.K_NBI_TIMEOUT, str(body.nbi_timeout))
    if body.default_connection_request is not None:
        db.set_setting(runtime.K_DEFAULT_CR, "true" if body.default_connection_request else "false")
    return {"ok": True, **runtime.effective()}


@router.get("/llm")
async def get_llm():
    """Configuracion del proveedor de IA. La clave nunca se devuelve."""
    cfg = runtime.llm_config()
    return {"provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"],
            "key_set": bool(cfg["api_key"]), "source": cfg["source"],
            "proveedores": sorted(llm.PROVEEDORES)}


@router.put("/llm")
async def set_llm(body: LLMIn):
    """Guarda el proveedor de IA. Una clave vacia borra la guardada."""
    if body.base_url and not body.base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "La URL del proveedor debe empezar por http:// o https://")
    if body.provider is not None:
        db.set_setting(runtime.K_LLM_PROVIDER, body.provider)
    if body.model is not None:
        db.set_setting(runtime.K_LLM_MODEL, body.model.strip())
    if body.base_url is not None:
        db.set_setting(runtime.K_LLM_BASE, body.base_url.strip().rstrip("/"))
    if body.api_key is not None:
        db.set_setting(runtime.K_LLM_KEY, body.api_key.strip())
    cfg = runtime.llm_config()
    return {"ok": True, "provider": cfg["provider"], "model": cfg["model"],
            "key_set": bool(cfg["api_key"]), "source": cfg["source"]}


@router.post("/llm/test")
async def test_llm():
    """Hace la peticion mas barata posible para ver si la clave funciona."""
    try:
        return await llm.probar()
    except llm.LLMNoConfigurado as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"No se pudo contactar con el proveedor ({type(e).__name__})"}


@router.post("/test")
async def test_connection(body: TestIn):
    """Verifica que el NBI responde (consulta trivial de dispositivos)."""
    url = (body.nbi_url.strip().rstrip("/") if body.nbi_url else runtime.nbi_url())
    problem = await _nbi_problem(url)
    if problem:
        return {"ok": False, "url": url, "error": problem}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(base_url=url, timeout=8.0, follow_redirects=False) as c:
            r = await c.get("/devices/", params={"query": "{}", "projection": "_id"})
        ms = round((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, list):
                return {"ok": False, "url": url, "error": "Responde, pero no parece un NBI de GenieACS"}
            n = len(data)
            return {"ok": True, "url": url, "latency_ms": ms, "devices": n,
                    "detail": f"Conectado. {n} dispositivo(s) visibles."}
        return {"ok": False, "url": url, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        # solo el tipo de error: no reflejar contenido de destinos arbitrarios
        return {"ok": False, "url": url, "error": type(e).__name__}
