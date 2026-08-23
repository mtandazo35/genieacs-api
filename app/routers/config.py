"""Configuracion del CPE: WiFi, IP/DHCP, DNS, PPPoE, hora/fecha."""
from fastapi import APIRouter, Depends, HTTPException

from ..deps import authorized_device
from ..genieacs import genie
from ..parammap import pick_map, resolve
from ..schemas import ActionResult, DnsIn, IpIn, PppoeIn, TimeIn, WanIn, WifiIn
from .backup import merge_device_config

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


@router.put("/wan", response_model=ActionResult)
async def set_wan(device_id: str, body: WanIn, dev=Depends(authorized_device)):
    """Configura la WAN (DHCP o IP estatica) sobre la conexion WAN ACTIVA.

    OJO: cambiar mal la WAN puede dejar al equipo sin internet y sin contacto
    con el ACS. En estatico se exigen ip, mascara y gateway."""
    from .devices import active_wan_prefix
    prefix = await active_wan_prefix(device_id)
    if body.mode == "dhcp":
        values = [[f"{prefix}.AddressingType", "DHCP", "xsd:string"]]
    else:
        if not (body.ip and body.mask and body.gateway):
            raise HTTPException(400, "En modo estatico se requieren ip, mask y gateway")
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
