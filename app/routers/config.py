"""Configuracion del CPE: WiFi, IP/DHCP, DNS, PPPoE, hora/fecha."""
import ipaddress
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import authorized_device
from ..genieacs import genie
from ..parammap import pick_map, resolve
from ..treeprofile import WanNoSoportada, wan_current_ip, wan_write_values, coverage, wans
from ..schemas import AccessIn, ActionResult, DnsIn, IpIn, PppoeIn, TimeIn, WanIn, WifiIn
from .backup import merge_device_config

log = logging.getLogger("genieacs_api.config")

router = APIRouter(prefix="/devices/{device_id}", tags=["config"])


def _pv(pmap: dict, key: str, value):
    """Construye la tripleta [path, value, xsd:type] o None si el modelo no lo soporta."""
    r = resolve(pmap, key)
    if not r:
        return None
    path, xsd = r
    return [path, value, xsd]


async def _apply(device_id: str, pmap: dict, pairs: list[tuple[str, object]]) -> ActionResult:
    values = []
    unsupported = []
    for key, val in pairs:
        if val is None:
            continue
        pv = _pv(pmap, key, val)
        if pv is None:
            unsupported.append(key)
        else:
            values.append(pv)
    if not values:
        raise HTTPException(400, f"Nada que aplicar. No soportado por el modelo: {unsupported}")
    res = await genie.set_parameter_values(device_id, values)
    merge_device_config(device_id, values)   # mantener el respaldo al dia
    detail = None if not unsupported else f"Ignorados (no soportados): {unsupported}"
    return ActionResult(applied=res["applied"], queued=res["queued"], detail=detail)


@router.put("/wifi", response_model=ActionResult)
async def set_wifi(device_id: str, body: WifiIn, dev=Depends(authorized_device)):
    pmap = pick_map(dev)
    p = body.band  # 2g / 5g
    pairs = [
        (f"wifi_{p}_ssid", body.ssid),
        (f"wifi_{p}_password", body.password),
        (f"wifi_{p}_enable", body.enable),
        (f"wifi_{p}_channel", body.channel),
        (f"wifi_{p}_hidden", (not body.hidden) if body.hidden is not None else None),  # hidden->Advertisement invertido
    ]
    return await _apply(device_id, pmap, pairs)


@router.put("/ip", response_model=ActionResult)
async def set_ip(device_id: str, body: IpIn, dev=Depends(authorized_device)):
    pmap = pick_map(dev)
    pairs = [
        ("lan_ip", body.lan_ip),
        ("lan_mask", body.lan_mask),
        ("dhcp_enable", body.dhcp_enable),
        ("dhcp_min", body.dhcp_min),
        ("dhcp_max", body.dhcp_max),
        ("dhcp_lease", body.dhcp_lease),
    ]
    return await _apply(device_id, pmap, pairs)


@router.get("/wan")
async def get_wan(device_id: str, dev=Depends(authorized_device)):
    """TODAS las WAN configuradas del equipo, con su VLAN, modo y estado.

    Antes solo miraba el arbol TR-098, asi que en un equipo TR-181 la pestana
    WAN salia vacia. Se deduce del arbol, no por marca."""
    doc = await genie.get_device(device_id)
    conns = wans(doc or {})
    return {"count": len(conns), "connections": conns,
            # con el arbol sin refrescar no hay WAN que leer, y eso no es lo
            # mismo que un equipo sin WAN: el panel lo dice de otra manera
            "arbol": coverage(doc or {})}


def _validar_estatico(body, ip_actual: str | None, device_id: str) -> None:
    """Guardas de la WAN estatica, iguales en TR-098 y TR-181: datos validos,
    gateway en la misma subred, y no saltar a otra red (perderia el ACS)."""
    if not (body.ip and body.mask and body.gateway):
        raise HTTPException(400, "En modo estatico se requieren ip, mask y gateway")
    try:
        net = ipaddress.IPv4Network(f"{body.ip}/{body.mask}", strict=False)
        ip_addr = ipaddress.IPv4Address(body.ip)
        gw_addr = ipaddress.IPv4Address(body.gateway)
    except ValueError:
        raise HTTPException(400, "IP, mascara o gateway invalidos")
    if gw_addr not in net:
        raise HTTPException(400, f"El gateway {body.gateway} no esta en la misma red que la IP "
                                 f"{body.ip}/{body.mask} ({net}). Corrige los datos o perderas el enlace.")
    if ip_addr == gw_addr:
        raise HTTPException(400, "La IP y el gateway no pueden ser iguales")
    if ip_actual:
        try:
            fuera = ipaddress.IPv4Address(ip_actual) not in net
        except ValueError:
            fuera = False
        if fuera:
            raise HTTPException(400,
                f"La nueva IP {body.ip}/{body.mask} esta en otra red distinta a la actual del "
                f"equipo ({ip_actual}). Perderias el enlace con el ACS. Usa una IP de la red actual "
                f"o hazlo localmente en el equipo.")
    else:
        log.warning("WAN %s: sin IP actual conocida, se aplica sin la guarda de misma red", device_id)


@router.put("/wan", response_model=ActionResult)
async def set_wan(device_id: str, body: WanIn, dev=Depends(authorized_device)):
    """Configura la WAN (DHCP o IP estatica) sobre la conexion WAN ACTIVA.

    OJO: cambiar mal la WAN puede dejar al equipo sin internet y sin contacto
    con el ACS. En estatico se exigen ip, mascara y gateway."""
    from .devices import active_wan_prefix
    is_tr098 = pick_map(dev).get("root") == "InternetGatewayDevice"

    # --- PPPoE (TR-098 y TR-181) ---
    if body.mode == "pppoe":
        if not body.username:
            raise HTTPException(400, "PPPoE requiere usuario")
        ppp = ("InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1"
               if is_tr098 else "Device.PPP.Interface.1")
        values = [[f"{ppp}.Enable", True, "xsd:boolean"],
                  [f"{ppp}.Username", body.username, "xsd:string"]]
        if body.password:
            values.append([f"{ppp}.Password", body.password, "xsd:string"])
        res = await genie.set_parameter_values(device_id, values)
        merge_device_config(device_id, values)
        return ActionResult(applied=res["applied"], queued=res["queued"],
                            detail="WAN PPPoE configurada (se conecta si hay servidor PPPoE)")

    # --- DHCP / estatico en TR-181: sobre la interfaz WAN que deduce el perfil ---
    if not is_tr098:
        full = await genie.get_device(device_id)
        actual = wan_current_ip(full or {})
        if body.mode == "static":
            _validar_estatico(body, actual, device_id)
        try:
            values, notas = wan_write_values(full or {}, body.mode, ip=body.ip, mask=body.mask,
                                             gateway=body.gateway, dns=body.dns, mtu=body.mtu)
        except WanNoSoportada as e:
            raise HTTPException(400, str(e))
        log.info("WAN TR-181 en %s: %s", device_id, "; ".join(notas))
        res = await genie.set_parameter_values(device_id, values)
        merge_device_config(device_id, values)
        modo = "DHCP" if body.mode == "dhcp" else f"IP estatica {body.ip}"
        return ActionResult(applied=res["applied"], queued=res["queued"],
                            detail=f"WAN {modo} aplicada ({notas[0]})")
    prefix = await active_wan_prefix(device_id)
    if body.mode == "dhcp":
        values = [[f"{prefix}.AddressingType", "DHCP", "xsd:string"]]
    else:
        try:
            from .devices import _active_wan
            cur = await _active_wan(device_id)
            actual = cur.get("ip") if cur else None
        except Exception as e:
            log.warning("WAN %s: no se pudo leer la WAN actual (%s)", device_id, e)
            actual = None
        _validar_estatico(body, actual, device_id)
        values = [
            [f"{prefix}.AddressingType", "Static", "xsd:string"],
            [f"{prefix}.ExternalIPAddress", body.ip, "xsd:string"],
            [f"{prefix}.SubnetMask", body.mask, "xsd:string"],
            [f"{prefix}.DefaultGateway", body.gateway, "xsd:string"],
        ]
        if body.dns:
            values.append([f"{prefix}.DNSServers", ",".join(body.dns), "xsd:string"])
        if body.mtu:
            values.append([f"{prefix}.MaxMTUSize", body.mtu, "xsd:unsignedInt"])
    res = await genie.set_parameter_values(device_id, values)
    merge_device_config(device_id, values)
    inst = ".".join(prefix.split(".")[-2:])   # p.ej. WANIPConnection.2
    return ActionResult(applied=res["applied"], queued=res["queued"],
                        detail=f"WAN aplicada en {inst}")


@router.put("/pppoe", response_model=ActionResult)
async def set_pppoe(device_id: str, body: PppoeIn, dev=Depends(authorized_device)):
    pmap = pick_map(dev)
    pairs = [
        ("pppoe_enable", body.enable),
        ("pppoe_user", body.username),
        ("pppoe_password", body.password),
    ]
    return await _apply(device_id, pmap, pairs)


class Ipv6In(BaseModel):
    enable: Optional[bool] = None
    type: Optional[str] = None    # Auto / DHCPv6 / SLAAC / PPPoE / Static


@router.get("/ipv6-config")
async def get_ipv6_config(device_id: str, dev=Depends(authorized_device)):
    """Estado de la config IPv6 (WAN). Solo modelos que la exponen (TR-181/TP-Link)."""
    if pick_map(dev).get("root") != "Device":
        return {"supported": False, "reason": "Este modelo no expone config IPv6 por TR-069 (usa Avanzado)."}
    from .devices import tr181_wan_interface, _read
    pref = await tr181_wan_interface(device_id)
    if not pref:
        return {"supported": False, "reason": "No se identificó la interfaz WAN IPv6."}
    doc = await genie.get_device(device_id, [pref])
    return {"supported": True, "interface": pref,
            "enabled": _read(doc or {}, f"{pref}.IPv6Enable"),
            "type": _read(doc or {}, f"{pref}.X_TP_IPv6AddrType"),
            "types": ["Auto", "DHCPv6", "SLAAC", "PPPoE", "Static"]}


@router.put("/ipv6-config", response_model=ActionResult)
async def set_ipv6_config(device_id: str, body: Ipv6In, dev=Depends(authorized_device)):
    """Activa/configura IPv6 en la WAN (habilitar + método)."""
    if pick_map(dev).get("root") != "Device":
        raise HTTPException(400, "Este modelo no soporta config IPv6 por esta vía; usa la pestaña Avanzado.")
    from .devices import tr181_wan_interface
    pref = await tr181_wan_interface(device_id)
    if not pref:
        raise HTTPException(400, "No se identificó la interfaz WAN para IPv6.")
    values = []
    if body.type:
        values.append([f"{pref}.X_TP_IPv6AddrType", body.type, "xsd:string"])
    if body.enable is not None:
        values.append([f"{pref}.IPv6Enable", body.enable, "xsd:boolean"])
    if not values:
        raise HTTPException(400, "Nada que aplicar")
    res = await genie.set_parameter_values(device_id, values)
    return ActionResult(applied=res["applied"], queued=res["queued"],
                        detail=f"IPv6 {'activado' if body.enable else 'configurado'} en {pref.split('.')[-1]}")


@router.put("/access", response_model=ActionResult)
async def set_access(device_id: str, body: AccessIn, dev=Depends(authorized_device)):
    """Acceso remoto (WAN) y usuario/clave admin del equipo."""
    pmap = pick_map(dev)
    pairs = [
        ("remote_enable", body.remote_enable),
        ("admin_user", body.admin_user),
        ("admin_password", body.admin_password),
    ]
    # acceso remoto: algunos equipos (TP-Link/TR-181) exigen puerto y protocolo al habilitar
    if body.remote_enable:
        port = body.remote_port if body.remote_port else 8080
        pairs.append(("remote_port", port))
        if resolve(pmap, "remote_protocol"):
            proto = body.remote_protocol or "HTTPS"
            pairs.append(("remote_protocol", proto))
            # TP-Link exige tambien los X_TP_* o revierte el cambio
            if resolve(pmap, "remote_protocol_x"):
                pairs.append(("remote_protocol_x", proto))
            if resolve(pmap, "remote_port_x"):
                pairs.append(("remote_port_x", str(port)))
    elif body.remote_port:
        pairs.append(("remote_port", body.remote_port))
    # al fijar clave admin, habilitar la cuenta si el modelo lo requiere
    if body.admin_password and resolve(pmap, "admin_enable"):
        pairs.append(("admin_enable", True))
    return await _apply(device_id, pmap, pairs)


@router.put("/dns", response_model=ActionResult)
async def set_dns(device_id: str, body: DnsIn, dev=Depends(authorized_device)):
    pmap = pick_map(dev)
    key = "lan_dns" if body.scope == "lan" else "wan_dns"
    servers = ",".join(body.servers)
    return await _apply(device_id, pmap, [(key, servers)])


@router.put("/time", response_model=ActionResult)
async def set_time(device_id: str, body: TimeIn, dev=Depends(authorized_device)):
    pmap = pick_map(dev)
    pairs = [("tz", body.timezone), ("ntp1", body.ntp1), ("ntp2", body.ntp2)]
    return await _apply(device_id, pmap, pairs)
