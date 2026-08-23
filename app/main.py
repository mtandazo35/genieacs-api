"""genieacs-api: API de negocio multi-tenant sobre GenieACS.

Expone endpoints limpios (WiFi, IP, PPPoE, DNS, hora, firmware, reinicios
programados) para que paneles/facturacion no tengan que hablar TR-069 crudo.
"""
import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import db
from .db import init_db
from .routers import auth, backup, config, devices, firmware, settings, system
from .security import decode_token


class NoCacheStatic(StaticFiles):
    """Sirve los estaticos con no-cache para que el navegador siempre tome
    la ultima version del panel tras cada despliegue."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

app = FastAPI(
    title="GenieACS API",
    description="Gestion de CPEs (WiFi, IP, PPPoE, DNS, actualizaciones, hora, "
                "reinicios programados) multi-tenant por ISP, sobre GenieACS.",
    version="1.0.0",
)


@app.on_event("startup")
async def _startup():
    init_db()
    # bucle de auto-restauracion de config tras factory reset
    asyncio.create_task(backup.enforce_loop())


_AUDIT_PREFIXES = ("/devices", "/firmware", "/settings", "/auth/users")


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """Registra toda operacion de escritura (POST/PUT/DELETE) en el log de auditoria."""
    response = await call_next(request)
    try:
        path = request.url.path
        if request.method in ("POST", "PUT", "DELETE") and any(path.startswith(p) for p in _AUDIT_PREFIXES):
            user = "?"
            auth_h = request.headers.get("authorization", "")
            if auth_h.startswith("Bearer "):
                data = decode_token(auth_h[7:])
                if data:
                    user = data.get("sub", "?")
            # Starlette ya entrega el path decodificado una vez → parts[1] es el _id real
            parts = path.strip("/").split("/")
            device_id, action = None, parts[0]
            if parts[0] == "devices" and len(parts) >= 3:
                device_id = parts[1]; action = "/".join(parts[2:])
            elif parts[0] == "devices" and len(parts) == 2:
                device_id = parts[1]; action = "device"
            elif len(parts) >= 2:
                action = "/".join(parts[:2])
            db.add_audit(user, device_id, action, request.method, path, response.status_code)
    except Exception:
        pass
    return response


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(config.router)
app.include_router(system.router)
app.include_router(firmware.router)
app.include_router(backup.router)
app.include_router(settings.router)

# Front-end para usuario final (SPA vanilla). Se monta al final para que las
# rutas de la API y /docs tengan precedencia; el resto sirve la app web.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", NoCacheStatic(directory=_STATIC_DIR, html=True), name="ui")
