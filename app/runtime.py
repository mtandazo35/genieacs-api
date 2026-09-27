"""Configuracion efectiva en tiempo de ejecucion.

Prioridad: valor guardado en la BD (editable desde el panel) > valor del .env.
Permite cambiar a que ACS se conecta la API sin reiniciar el servicio.
"""
from . import db
from .config import get_settings

# claves en la tabla settings
K_NBI_URL = "nbi_url"
K_NBI_TIMEOUT = "nbi_timeout"
K_DEFAULT_CR = "default_connection_request"
# proveedor de IA (homologacion asistida); la clave se guarda aqui, nunca se devuelve
K_LLM_PROVIDER = "llm_provider"
K_LLM_KEY = "llm_api_key"
K_LLM_MODEL = "llm_model"
K_LLM_BASE = "llm_base_url"


def nbi_url() -> str:
    return (db.get_setting(K_NBI_URL) or get_settings().nbi_url).rstrip("/")


def nbi_timeout() -> float:
    v = db.get_setting(K_NBI_TIMEOUT)
    return float(v) if v else get_settings().nbi_timeout


def default_connection_request() -> bool:
    v = db.get_setting(K_DEFAULT_CR)
    if v is None:
        return get_settings().default_connection_request
    return v.lower() == "true"


def llm_config() -> dict:
    """Proveedor de IA efectivo. La clave NO sale de aqui hacia la API."""
    s = get_settings()
    return {
        "provider": db.get_setting(K_LLM_PROVIDER) or s.llm_provider,
        "api_key": db.get_setting(K_LLM_KEY) or s.llm_api_key,
        "model": db.get_setting(K_LLM_MODEL) or s.llm_model,
        "base_url": db.get_setting(K_LLM_BASE) or s.llm_base_url,
        "source": "panel" if db.get_setting(K_LLM_KEY) else ("env" if s.llm_api_key else "sin configurar"),
    }


def effective() -> dict:
    """Estado actual + de donde sale cada valor (bd/env)."""
    s = get_settings()
    return {
        "nbi_url": nbi_url(),
        "nbi_timeout": nbi_timeout(),
        "default_connection_request": default_connection_request(),
        "source": {
            "nbi_url": "bd" if db.get_setting(K_NBI_URL) else "env",
            "nbi_timeout": "bd" if db.get_setting(K_NBI_TIMEOUT) else "env",
            "default_connection_request": "bd" if db.get_setting(K_DEFAULT_CR) else "env",
        },
        "env_default_nbi_url": s.nbi_url.rstrip("/"),
    }
