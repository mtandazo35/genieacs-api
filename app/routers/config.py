"""Configuracion del CPE: WiFi, IP/DHCP, DNS, PPPoE, hora/fecha."""
import ipaddress

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


@router.get("/wan")
async def get_wan(device_id: str, dev=Depends(authorized_device)):
    """Lista las conexiones WAN del equipo (cuantas tiene y cual esta activa)."""
    from .devices import wan_connections
    conns = await wan_connections(device_id)
    return {"count": len(conns), "connections": conns}


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

    # --- DHCP / estático: por ahora solo TR-098 ---
    if not is_tr098:
        raise HTTPException(400, "DHCP/estático por TR-181 aún no está soportado; usa la pestaña Avanzado. "
                                 "PPPoE sí está soportado.")
    prefix = await active_wan_prefix(device_id)
    if body.mode == "dhcp":
        values = [[f"{prefix}.AddressingType", "DHCP", "xsd:string"]]
    else:
        if not (body.ip and body.mask and body.gateway):
            raise HTTPException(400, "En modo estatico se requieren ip, mask y gateway")
        # validacion de seguridad: IP/gateway validos y en la misma subred
        try:
            net = ipaddress.IPv4Network(f"{body.ip}/{body.mask}", strict=False)
            ip_addr = ipaddress.IPv4Address(body.ip)
            gw_addr = ipaddress.IPv4Address(body.gateway)
        except Exception:
            raise HTTPException(400, "IP, mascara o gateway invalidos")
        if gw_addr not in net:
            raise HTTPException(400, f"El gateway {body.gateway} no esta en la misma red que la IP "
                                     f"{body.ip}/{body.mask} ({net}). Corrige los datos o perderas el enlace.")
        if ip_addr == gw_addr:
            raise HTTPException(400, "La IP y el gateway no pueden ser iguales")
        # evitar cambiar a una red distinta de la actual (perderia el enlace con el ACS)
        try:
            from .devices import _active_wan
            cur = await _active_wan(device_id)
            cur_ip = cur.get("ip") if cur else None
            if cur_ip and ipaddress.IPv4Address(cur_ip) not in net:
                raise HTTPException(400,
                    f"La nueva IP {body.ip}/{body.mask} esta en otra red distinta a la actual del "
                    f"equipo ({cur_ip}). Perderias el enlace con el ACS. Usa una IP de la red actual "
                    f"o hazlo localmente en el equipo.")
        except HTTPException:
            raise
        except Exception:
            pass
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
