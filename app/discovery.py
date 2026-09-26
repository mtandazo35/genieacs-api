"""Descubrimiento de equipos nuevos.

Un CPE recien vinculado llega al ACS **sin tag**, y como la multi-tenencia se
apoya en los tags, ningun usuario ISP lo ve: se queda ahi hasta que alguien mira
con el admin. Esto lo resuelve mirando de que IP informa cada equipo sin tag y
cruzandola con una tabla de rangos por ISP.

Arranca en **modo sugerencia**: propone el ISP y no toca nada. Cuando las
sugerencias demuestren que aciertan, se activa la asignacion automatica.
Un tag mal puesto le daria a un ISP acceso a equipos de otro, asi que el
automatismo se gana la confianza antes de tener permiso.
"""
import asyncio
import ipaddress
import logging
import re

from . import db, runtime
from .genieacs import genie

log = logging.getLogger("genieacs_api.discovery")

# tags que no significan tenencia (los pone el propio panel)
TAGS_INTERNOS = ("sched-reboot",)
_RE_REBOOT = re.compile(r"^reboot@\d{1,2}:\d{2}$")

K_AUTO = "discovery_auto"          # "true" = asignar sola; por defecto, solo sugerir
K_INTERVALO = "discovery_interval"  # segundos entre barridos


def tags_de_tenencia(tags) -> list[str]:
    """Tags que marcan a que ISP pertenece el equipo (el resto son del panel)."""
    return [t for t in (tags or []) if t not in TAGS_INTERNOS and not _RE_REBOOT.match(t)]


def automatico() -> bool:
    return (db.get_setting(K_AUTO) or "false").lower() == "true"


def intervalo() -> int:
    try:
        return max(30, int(db.get_setting(K_INTERVALO) or 60))
    except ValueError:
        return 60


def ip_del_equipo(doc: dict) -> str | None:
    """IP con la que el CPE habla con el ACS: la del ConnectionRequestURL.

    Es la que GenieACS usa para alcanzarlo, y existe en TR-098 y TR-181."""
    from .treeprofile import derived_params, flatten

    vals = {p: v for p, v, _w in flatten(doc)}
    for path, valor in vals.items():
        if path.endswith("ManagementServer.ConnectionRequestURL") and isinstance(valor, str):
            # el host va hasta el ':' del puerto; IPv6 llega entre corchetes
            m = re.match(r"^https?://(\[[0-9a-fA-F:]+\]|[^\[\]:/]+)", valor)
            if m:
                return m.group(1).strip("[]")
    # respaldo: la IP WAN que deduce el perfil
    wan = derived_params(doc).get("wan_ip")
    return vals.get(wan[0]) if wan else None


def isp_para(ip: str | None, reglas: list[dict]) -> tuple[str | None, str | None]:
    """(isp_tag, motivo) segun la tabla de rangos. Gana el prefijo mas especifico;
    si dos reglas igual de especificas discrepan, no se asigna nada."""
    if not ip:
        return None, "el equipo no reporta una IP utilizable"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None, f"IP no valida ({ip})"
    encajan = []
    for r in reglas:
        try:
            red = ipaddress.ip_network(r["cidr"], strict=False)
        except ValueError:
            continue
        if addr in red:
            encajan.append((red.prefixlen, r["isp_tag"], red))
    if not encajan:
        return None, f"{ip} no esta en ningun rango configurado"
    encajan.sort(key=lambda x: -x[0])
    mejor = encajan[0]
    empates = {t for pl, t, _n in encajan if pl == mejor[0]}
    if len(empates) > 1:
        return None, f"{ip} encaja en rangos de varios ISP ({', '.join(sorted(empates))})"
    return mejor[1], f"{ip} esta en {mejor[2]}"


async def escanear() -> list[dict]:
    """Equipos sin tag de tenencia, con el ISP que les corresponderia."""
    reglas = db.list_owner_rules()
    filas = await genie.query_devices({}, ["_id", "_tags", "_lastInform", "_deviceId",
                                           "Device.ManagementServer.ConnectionRequestURL",
                                           "InternetGatewayDevice.ManagementServer.ConnectionRequestURL",
                                           "Device.IP.Interface", "Device.DHCPv4.Server.Pool",
                                           "InternetGatewayDevice.WANDevice"])
    pendientes = []
    for d in filas:
        if tags_de_tenencia(d.get("_tags")):
            continue
        ip = ip_del_equipo(d)
        tag, motivo = isp_para(ip, reglas)
        did = d.get("_deviceId") or {}
        pendientes.append({"id": d["_id"], "ip": ip, "isp_tag": tag, "motivo": motivo,
                           "model": did.get("_ProductClass"), "manufacturer": did.get("_Manufacturer"),
                           "last_inform": d.get("_lastInform")})
    return pendientes


async def asignar(device_id: str, isp_tag: str, quien: str = "sistema", motivo: str = "") -> None:
    await genie.add_tag(device_id, isp_tag)
    db.add_audit(quien, device_id, f"Asigno el equipo a {isp_tag} ({motivo})".strip(),
                 "POST", "/discovery/assign", 200)
    log.info("equipo %s asignado a %s (%s)", device_id, isp_tag, motivo or "manual")


async def ciclo() -> dict:
    """Un barrido. En modo sugerencia solo cuenta; en automatico, asigna."""
    pendientes = await escanear()
    asignados = 0
    if automatico():
        for p in pendientes:
            if p["isp_tag"]:
                try:
                    await asignar(p["id"], p["isp_tag"], motivo=p["motivo"])
                    asignados += 1
                except Exception:
                    log.exception("no se pudo asignar %s a %s", p["id"], p["isp_tag"])
    return {"pendientes": len(pendientes), "asignados": asignados,
            "modo": "automatico" if automatico() else "sugerencia"}


async def bucle():
    """Barrido periodico. Nunca tumba el servicio: los errores se registran."""
    while True:
        await asyncio.sleep(intervalo())
        try:
            res = await ciclo()
            if res["asignados"]:
                log.info("descubrimiento: %(asignados)d equipo(s) asignados", res)
        except Exception:
            log.exception("el barrido de descubrimiento fallo (NBI %s)", runtime.nbi_url())
