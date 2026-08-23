"""genieacs-api: API de negocio multi-tenant sobre GenieACS.

Expone endpoints limpios (WiFi, IP, PPPoE, DNS, hora, firmware, reinicios
programados) para que paneles/facturacion no tengan que hablar TR-069 crudo.
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import auth, config, devices, system

app = FastAPI(
    title="GenieACS API",
    description="Gestion de CPEs (WiFi, IP, PPPoE, DNS, actualizaciones, hora, "
                "reinicios programados) multi-tenant por ISP, sobre GenieACS.",
    version="1.0.0",
)


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(config.router)
app.include_router(system.router)

# Front-end para usuario final (SPA vanilla). Se monta al final para que las
# rutas de la API y /docs tengan precedencia; el resto sirve la app web.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")
