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


# Pegar la URL de la consola web en vez de la del API es el error mas comun al
# configurarlo a mano. Donde sabemos cual es la equivalente, se corrige sola; si
# no la sabemos, se dice claro en vez de guardar algo que no va a funcionar.
_CONSOLAS = ("console.", "dashboard.", "app.", "platform.")
_EQUIVALENTE = {
    "console.groq.com": "https://api.groq.com/openai/v1",
    "platform.openai.com": "https://api.openai.com/v1",
    "openrouter.ai": "https://openrouter.ai/api/v1",
    "console.together.ai": "https://api.together.xyz/v1",
}


def _normalizar_url(url: str | None) -> tuple[str | None, str | None, str | None]:
    """(url a usar, aviso de correccion, error). Solo uno de los dos ultimos."""
    if not url:
        return url, None, None
    host = url.split("//", 1)[-1].split("/")[0].lower()
    buena = _EQUIVALENTE.get(host)
    if buena and url.rstrip("/") != buena:
        return buena, f"{host} es la consola web; se usa su API: {buena}", None
    if host.startswith(_CONSOLAS) and not buena:
        return url, None, (f"{host} parece la consola web del proveedor, no su API. "
                           "Pon la URL del API o deja el campo vacio para la de por defecto.")
    return url, None, None


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
    base_url, aviso, error = _normalizar_url(body.base_url)
    if error:
        raise HTTPException(400, error)
    if body.provider is not None:
        db.set_setting(runtime.K_LLM_PROVIDER, body.provider)
    if body.model is not None:
        db.set_setting(runtime.K_LLM_MODEL, body.model.strip())
    if body.base_url is not None:
        db.set_setting(runtime.K_LLM_BASE, (base_url or "").strip().rstrip("/"))
    if body.api_key is not None:
        db.set_setting(runtime.K_LLM_KEY, body.api_key.strip())
    cfg = runtime.llm_config()
    return {"ok": True, "provider": cfg["provider"], "model": cfg["model"],
            "base_url": cfg["base_url"], "key_set": bool(cfg["api_key"]),
            "source": cfg["source"], "aviso": aviso}


@router.post("/llm/test")
async def test_llm(body: LLMIn | None = None):
    """Prueba la configuracion. Si se manda una en el cuerpo, se prueba ESA sin
    guardarla: si no, habria que guardar una clave para descubrir que no sirve."""
    cfg, aviso_url = None, None
    if body and (body.api_key or body.base_url or body.model or body.provider):
        guardada = runtime.llm_config()
        cfg = {"provider": body.provider or guardada["provider"],
               "api_key": body.api_key or guardada["api_key"],
               "model": (body.model if body.model is not None else guardada["model"]),
               "base_url": (body.base_url if body.base_url is not None else guardada["base_url"])}
        cfg["base_url"], aviso_url, error = _normalizar_url(cfg["base_url"])
        if error:
            return {"ok": False, "error": error}
    try:
        res = await llm.probar(cfg)
        if cfg and aviso_url:
            res["aviso"] = aviso_url
        return res
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
