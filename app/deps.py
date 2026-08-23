"""Dependencias FastAPI: usuario actual (JWT) y control de acceso multi-tenant."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from . import db
from .genieacs import genie
from .security import decode_token

oauth2 = OAuth2PasswordBearer(tokenUrl="auth/login")


class CurrentUser:
    def __init__(self, username: str, role: str, isp: str | None):
        self.username = username
        self.role = role
        self.isp = isp

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def current_user(token: str = Depends(oauth2)) -> CurrentUser:
    data = decode_token(token)
    if not data or "sub" not in data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalido o expirado")
    u = db.get_user(data["sub"])
    if not u or not u["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario inexistente o desactivado")
    return CurrentUser(data["sub"], data.get("role", "isp"), data.get("isp"))


async def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requiere rol admin")
    return user


def tenant_query(user: CurrentUser, base: dict | None = None) -> dict:
    """Inyecta el filtro por tag del ISP en una query de GenieACS."""
    q = dict(base or {})
    if not user.is_admin:
        q["_tags"] = user.isp
    return q


async def authorized_device(device_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    """Verifica que el dispositivo exista y que el usuario tenga acceso a el.

    Admin -> cualquiera. ISP -> solo si el CPE lleva su tag.
    Devuelve el documento del device (con projection basica)."""
    projection = ["_id", "_tags", "_deviceId",
                  "InternetGatewayDevice.DeviceInfo.Manufacturer",
                  "Device.DeviceInfo.Manufacturer"]   # ambas raices para elegir TR-098/TR-181
    dev = await genie.get_device(device_id, projection)
    if not dev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado")
    if not user.is_admin:
        tags = dev.get("_tags") or []
        if user.isp not in tags:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Sin acceso a este dispositivo")
    return dev
