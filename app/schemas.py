"""Esquemas de entrada/salida (Pydantic)."""
from pydantic import BaseModel, Field
from typing import Optional


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    isp: Optional[str] = None


class WifiIn(BaseModel):
    band: str = Field("2g", pattern="^(2g|5g)$", description="2g o 5g")
    ssid: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8, description="WPA2/3, minimo 8")
    enable: Optional[bool] = None
    channel: Optional[int] = Field(None, ge=0, le=196)
    hidden: Optional[bool] = Field(None, description="True = ocultar SSID")


class IpIn(BaseModel):
    lan_ip: Optional[str] = None
    lan_mask: Optional[str] = None
    dhcp_enable: Optional[bool] = None
    dhcp_min: Optional[str] = None
    dhcp_max: Optional[str] = None
    dhcp_lease: Optional[int] = Field(None, ge=60)


class PppoeIn(BaseModel):
    enable: bool = True
    username: Optional[str] = None
    password: Optional[str] = None


class DnsIn(BaseModel):
    scope: str = Field("lan", pattern="^(lan|wan)$", description="lan=DNS por DHCP, wan=DNS del enlace")
    servers: list[str] = Field(..., min_length=1, max_length=4)


class TimeIn(BaseModel):
    timezone: Optional[str] = Field(None, description="TZ del equipo, p.ej. 'GMT-5' o POSIX TZ")
    ntp1: Optional[str] = None
    ntp2: Optional[str] = None


class FirmwarefromServer(BaseModel):
    file_name: str = Field(..., description="Nombre del archivo ya cargado en el FS de GenieACS")


class ScheduleRebootIn(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)


class ActionResult(BaseModel):
    ok: bool = True
    applied: bool = False    # True si se ejecuto via connection request
    queued: bool = False     # True si quedo encolada al proximo inform
    detail: Optional[str] = None


class UserIn(BaseModel):
    username: str
    password: str = Field(..., min_length=6)
    role: str = Field("isp", pattern="^(admin|isp)$")
    isp_tag: Optional[str] = None
