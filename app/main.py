"""genieacs-api: API de negocio multi-tenant sobre GenieACS.

Expone endpoints limpios (WiFi, IP, PPPoE, DNS, hora, firmware, reinicios
programados) para que paneles/facturacion no tengan que hablar TR-069 crudo.
"""
from fastapi import FastAPI

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
