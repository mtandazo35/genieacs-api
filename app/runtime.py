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
