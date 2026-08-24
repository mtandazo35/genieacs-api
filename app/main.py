"""genieacs-api: API de negocio multi-tenant sobre GenieACS.

Expone endpoints limpios (WiFi, IP, PPPoE, DNS, hora, firmware, reinicios
programados) para que paneles/facturacion no tengan que hablar TR-069 crudo.
"""
import asyncio
import json
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


_AUDIT_PREFIXES = ("/devices", "/firmware", "/settings", "/auth/users", "/auth/me/password")
_AUDIT_SKIP = ("read", "refresh")   # solo lectura: no ensucian el historial


def _audit_detail(method, parts, b):
    """Frase legible con el detalle del cambio."""
    b = b or {}
    seg = parts[2] if parts[0] == "devices" and len(parts) >= 3 else ""
    if seg == "access":
        bits = []
        if b.get("remote_enable") is True:
            p = (b.get("remote_protocol") or "") + (":" + str(b["remote_port"]) if b.get("remote_port") else "")
            bits.append(f"Acceso remoto ACTIVADO ({p})")
        elif b.get("remote_enable") is False:
            bits.append("Acceso remoto desactivado")
        if b.get("admin_password"): bits.append("cambio de clave admin del equipo")
        if b.get("admin_user"): bits.append(f"usuario admin -> {b['admin_user']}")
        return "; ".join(bits) or "Acceso"
    if seg == "wifi":
        band = b.get("band", ""); bits = []
        if b.get("ssid"): bits.append(f"SSID {band} -> {b['ssid']}")
        if b.get("password"): bits.append(f"cambio clave WiFi {band}")
        if b.get("enable") is not None: bits.append(f"radio {band} {'ON' if b['enable'] else 'OFF'}")
        if b.get("channel"): bits.append(f"canal {band} -> {b['channel']}")
        return "WiFi: " + ", ".join(bits) if bits else "WiFi"
    if seg == "wan":
        m = b.get("mode")
        if m == "pppoe": return f"WAN -> PPPoE (usuario {b.get('username', '')})"
        if m == "static": return f"WAN -> IP estatica {b.get('ip', '')}"
        if m == "dhcp": return "WAN -> DHCP"
        return "WAN"
    if seg == "ip": return "Cambio Red LAN"
    if seg == "dns": return f"DNS ({b.get('scope', '')}) -> {', '.join(b.get('servers', []) or [])}"
    if seg == "time": return "Cambio hora/NTP"
    if seg == "reboot": return "Reinicio inmediato"
    if seg == "factory-reset": return "Factory reset"
    if seg == "schedule-reboot":
        return "Quito reinicio programado" if method == "DELETE" else f"Reinicio programado {b.get('hour', 0):02d}:{b.get('minute', 0):02d}"
    if seg == "firmware": return f"Envio firmware/config: {b.get('file_name', '')}"
    if seg == "label": return "Edito nombre/cliente"
    if seg == "param": return f"Parametro avanzado: {b.get('path', '')}"
    if seg == "ipv6-config": return f"IPv6 {'ACTIVADO' if b.get('enable') else 'configurado'} ({b.get('type', '')})"
    if seg == "backup": return "Guardo respaldo"
    if seg == "restore": return "Restauro configuracion"
    if seg == "autorestore": return f"Auto-restauracion {'ON' if b.get('enabled') else 'OFF'}"
    if seg == "diag":
        d = parts[3] if len(parts) > 3 else ""
        return (f"Ping a {b.get('host', '')}" if d == "ping" else f"Traceroute a {b.get('host', '')}")
    if parts[0] == "firmware":
        if len(parts) >= 2 and parts[1] == "upload": return "Subio firmware/archivo"
        if len(parts) >= 2 and parts[1] == "upload-url": return "Cargo firmware por URL"
        if len(parts) >= 2 and parts[1] == "push": return f"Envio masivo: {b.get('file_name', '')}"
        if method == "DELETE": return "Borro archivo"
    if parts[0] == "auth" and len(parts) >= 2 and parts[1] == "users":
        if method == "POST" and len(parts) == 2: return f"Creo usuario {b.get('username', '')}"
        if method == "DELETE": return f"Elimino usuario {parts[2] if len(parts) > 2 else ''}"
        if len(parts) > 3 and parts[3] == "password": return f"Reseteo clave de {parts[2]}"
        if len(parts) > 3 and parts[3] == "active": return f"{'Activo' if b.get('active') else 'Desactivo'} usuario {parts[2]}"
    if parts[0] == "auth" and "me" in parts and "password" in parts: return "Cambio su propia contrasena"
    if parts[0] == "settings": return "Cambio la conexion al ACS"
    return f"{method} {seg or '/'.join(parts[:2])}"


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """Registra cada cambio (POST/PUT/DELETE) con detalle legible."""
    method, path = request.method, request.url.path
    do_audit = method in ("POST", "PUT", "DELETE") and any(path.startswith(p) for p in _AUDIT_PREFIXES)
    body_bytes = b""
    if do_audit:
        body_bytes = await request.body()
        async def _receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        request._receive = _receive
    response = await call_next(request)
    if do_audit:
        try:
            parts = path.strip("/").split("/")
            seg = parts[2] if parts[0] == "devices" and len(parts) >= 3 else ""
            if seg in _AUDIT_SKIP or path.endswith("/read-bulk"):
                return response
            user = "?"
            auth_h = request.headers.get("authorization", "")
            if auth_h.startswith("Bearer "):
                data = decode_token(auth_h[7:])
                if data: user = data.get("sub", "?")
            device_id = parts[1] if parts[0] == "devices" and len(parts) >= 2 else None
            body = {}
            if body_bytes and request.headers.get("content-type", "").startswith("application/json"):
                try: body = json.loads(body_bytes)
                except Exception: body = {}
            db.add_audit(user, device_id, _audit_detail(method, parts, body), method, path, response.status_code)
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
