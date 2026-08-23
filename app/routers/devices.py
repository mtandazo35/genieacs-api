"""Listado y estado de dispositivos (filtrado por ISP)."""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Any, Optional

from .. import db
from ..bulk import resolve_targets
from ..deps import CurrentUser, authorized_device, current_user, tenant_query
from ..genieacs import genie
from ..parammap import pick_map, resolve

router = APIRouter(prefix="/devices", tags=["devices"])


class BulkReadIn(BaseModel):
    tag: Optional[str] = None
    model: Optional[str] = None
    device_ids: Optional[list[str]] = None
    all: bool = False


class ParamIn(BaseModel):
    path: str
    value: Any
    type: Optional[str] = None   # xsd:string / xsd:boolean / xsd:unsignedInt ...


class LabelIn(BaseModel):
    name: Optional[str] = None
    customer: Optional[str] = None
    notes: Optional[str] = None


class PingIn(BaseModel):
    host: str
    count: int = 4


class TraceIn(BaseModel):
    host: str
    max_hops: int = 20
    tries: int = 3


async def _run_diag(device_id: str, prefix: str, inputs: dict, wait_s: int = 60) -> str:
    """Lanza un diagnostico TR-069 (Requested) y espera a que el CPE lo complete."""
    values = [[f"{prefix}.{k}", v, t] for k, (v, t) in inputs.items()]
    values.append([f"{prefix}.DiagnosticsState", "Requested", "xsd:string"])
    await genie.set_parameter_values(device_id, values)   # el connection request lanza el test
    state = "Requested"
    for _ in range(max(1, wait_s // 5)):
        await asyncio.sleep(5)
        try:
            await genie.refresh_object(device_id, prefix)   # traer estado/resultados frescos del CPE
        except Exception:
            pass
        doc = await genie.get_device(device_id, [prefix])
        state = _read(doc or {}, f"{prefix}.DiagnosticsState")
        if state and state not in ("Requested", "None"):
            break
    return state or "Timeout"


# raices de arbol TR-069 que exponemos completas
_TREE_ROOTS = ("InternetGatewayDevice", "Device", "VirtualParameters")


def _flatten(node: dict, prefix: str) -> list[dict]:
    out = []
    for k, v in node.items():
        if k.startswith("_"):
            continue
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            if "_value" in v:
                out.append({"path": p, "value": v.get("_value"),
                            "writable": bool(v.get("_writable")), "type": v.get("_type")})
            else:
                out.extend(_flatten(v, p))
    return out

_STATUS_KEYS = [
    "firmware", "uptime", "cpu", "mac", "wan_mode", "wan_ip", "wan_gateway",
    "pppoe_enable", "pppoe_user", "pppoe_password", "pppoe_status",
    "remote_enable", "remote_port", "admin_user",
    "lan_ip", "dhcp_min", "dhcp_max",
    "wifi_2g_ssid", "wifi_2g_password", "wifi_2g_enable", "wifi_2g_channel", "wifi_2g_clients",
    "wifi_5g_ssid", "wifi_5g_password", "wifi_5g_enable", "wifi_5g_channel", "wifi_5g_clients",
]


@router.get("")
async def list_devices(user: CurrentUser = Depends(current_user)):
    """Lista los CPE visibles para el usuario (admin=todos, ISP=por su tag)."""
    projection = ["_id", "_tags", "_lastInform", "_deviceId",
                  "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
                  "InternetGatewayDevice.DeviceInfo.ModelName",
                  "Device.DeviceInfo.SoftwareVersion", "Device.DeviceInfo.ModelName"]
    rows = await genie.query_devices(tenant_query(user), projection)
    meta = db.all_device_meta()

    def _v(d, path):
        node = d
        for p in path.split("."):
            if not isinstance(node, dict) or p not in node:
                return None
            node = node[p]
        return node.get("_value") if isinstance(node, dict) else None

    out = []
    for d in rows:
        did = d.get("_deviceId", {})   # lo rellena GenieACS desde el Inform (sobrevive a BOOTSTRAP)
        m = meta.get(d["_id"], {})
        model = (_v(d, "Device.DeviceInfo.ModelName")
                 or _v(d, "InternetGatewayDevice.DeviceInfo.ModelName")
                 or did.get("_ProductClass"))
        firmware = (_v(d, "Device.DeviceInfo.SoftwareVersion")
                    or _v(d, "InternetGatewayDevice.DeviceInfo.SoftwareVersion"))
        out.append({
            "id": d["_id"],
            "name": m.get("name"),
            "customer": m.get("customer"),
            "tags": d.get("_tags", []),
            "last_inform": d.get("_lastInform"),
            "manufacturer": did.get("_Manufacturer"),
            "model": model,
            "serial": did.get("_SerialNumber"),
            "firmware": firmware,
        })
    return out


def _read(dev: dict, path: str):
    node = dev
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node.get("_value") if isinstance(node, dict) else None


_WAN_PREFIX = "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1"


def _val(node: dict, key: str):
    x = node.get(key) if isinstance(node, dict) else None
    return x.get("_value") if isinstance(x, dict) else None


async def _active_wan(device_id: str):
    """Devuelve la conexion WAN realmente activa (ConnectionStatus=Connected),
    no la instancia .1 fija. Este tipo de CPE tiene varias WANIPConnection."""
    doc = await genie.get_device(device_id, [_WAN_PREFIX])
    base = doc or {}
    for part in _WAN_PREFIX.split("."):
        base = base.get(part) if isinstance(base, dict) else None
    if not isinstance(base, dict):
        return None

    def instances(coll):
        node = base.get(coll)
        return [v for k, v in node.items() if not k.startswith("_") and isinstance(v, dict)] if isinstance(node, dict) else []

    ipconns = instances("WANIPConnection")
    pppconns = instances("WANPPPConnection")

    def first_nonempty(conns, key):
        for c in conns:
            v = _val(c, key)
            if v not in (None, ""):
                return v
        return None

    # la instancia activa a veces no trae IP/gateway/MAC: usar la de otra instancia que si la tenga
    ip_fallback = first_nonempty(ipconns, "ExternalIPAddress")
    gw_fallback = first_nonempty(ipconns, "DefaultGateway")

    def mac_of(inst):
        m = _val(inst, "MACAddress")
        if m and m not in ("00:00:00:00:00:00",):
            return m
        for c in ipconns + pppconns:   # buscar una MAC valida en cualquier conexion
            v = _val(c, "MACAddress")
            if v and v != "00:00:00:00:00:00":
                return v
        return None

    for inst in pppconns:
        if _val(inst, "ConnectionStatus") in ("Connected", "Up"):
            return {"mode": "PPPoE", "ip": _val(inst, "ExternalIPAddress") or ip_fallback,
                    "gw": _val(inst, "DefaultGateway") or gw_fallback,
                    "ppp_user": _val(inst, "Username"), "ppp": True, "mac": mac_of(inst)}
    for inst in ipconns:
        if _val(inst, "ConnectionStatus") in ("Connected", "Up"):
            return {"mode": _val(inst, "AddressingType"),
                    "ip": _val(inst, "ExternalIPAddress") or ip_fallback,
                    "gw": _val(inst, "DefaultGateway") or gw_fallback, "ppp": False, "mac": mac_of(inst)}
    return None


async def wan_connections(device_id: str) -> list[dict]:
    """Lista todas las conexiones WAN que reporta el equipo (para saber cuantas
    tiene y cual esta activa)."""
    doc = await genie.get_device(device_id, [_WAN_PREFIX])
    base = doc or {}
    for part in _WAN_PREFIX.split("."):
        base = base.get(part) if isinstance(base, dict) else None
    out = []
    if not isinstance(base, dict):
        return out
    for coll, kind in (("WANIPConnection", "ip"), ("WANPPPConnection", "ppp")):
        node = base.get(coll)
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            st = _val(v, "ConnectionStatus")
            out.append({
                "instance": f"{coll}.{k}",
                "type": "PPPoE" if kind == "ppp" else _val(v, "AddressingType"),
                "status": st,
                "ip": _val(v, "ExternalIPAddress"),
                "gateway": _val(v, "DefaultGateway"),
                "enable": _val(v, "Enable"),
                "active": st in ("Connected", "Up"),
            })
    return out


_HOSTS_PREFIX = "InternetGatewayDevice.LANDevice.1.Hosts.Host"


async def lan_hosts(device_id: str) -> list[dict]:
    """Lista los clientes conectados (LAN hosts). Sirve para TR-098 y TR-181."""
    full = await genie.get_device(device_id)
    if isinstance(full, dict) and "Device" in full and "InternetGatewayDevice" not in full:
        prefix = "Device.Hosts.Host"
    else:
        prefix = _HOSTS_PREFIX
    base = full or {}
    for part in prefix.split("."):
        base = base.get(part) if isinstance(base, dict) else None
    out = []
    if not isinstance(base, dict):
        return out
    for k, v in base.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        iface = _val(v, "InterfaceType") or _val(v, "Layer1Interface") or ""
        out.append({
            "hostname": _val(v, "HostName"),
            "ip": _val(v, "IPAddress"),
            "mac": _val(v, "MACAddress") or _val(v, "PhysAddress"),
            "active": _val(v, "Active"),
            "source": _val(v, "AddressSource"),
            "iface": "WiFi" if ("11" in str(iface) or "WiFi" in str(iface)) else ("Ethernet" if "Ethernet" in str(iface) else iface),
        })
    # activos primero
    out.sort(key=lambda h: (not h.get("active"), str(h.get("ip") or "")))
    return out


async def active_wan_prefix(device_id: str) -> str:
    """Path de la WANIPConnection activa (Connected). Para escribir la WAN en la
    instancia correcta, no en la .1 fija. Cae a .1 si no hay ninguna conectada."""
    default = _WAN_PREFIX + ".WANIPConnection.1"
    doc = await genie.get_device(device_id, [_WAN_PREFIX])
    base = doc or {}
    for part in _WAN_PREFIX.split("."):
        base = base.get(part) if isinstance(base, dict) else None
    node = base.get("WANIPConnection") if isinstance(base, dict) else None
    if not isinstance(node, dict):
        return default
    first = None
    for k, v in node.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        if first is None:
            first = k
        if _val(v, "ConnectionStatus") in ("Connected", "Up"):
            return f"{_WAN_PREFIX}.WANIPConnection.{k}"
    return f"{_WAN_PREFIX}.WANIPConnection.{first}" if first else default


@router.get("/{device_id}/status")
async def device_status(device_id: str, dev=Depends(authorized_device)):
    """Estado resumido leyendo la cache del ACS (no consulta al CPE)."""
    # arbol completo: sirve para detectar el modelo de datos (TR-098/TR-181) y leer cualquier path
    full = await genie.get_device(device_id)
    pmap = pick_map(full)
    did = full.get("_deviceId", {})
    meta = db.get_device_meta(device_id)
    model_name = (_read(full, "Device.DeviceInfo.ModelName")
                  or _read(full, "InternetGatewayDevice.DeviceInfo.ModelName")
                  or did.get("_ProductClass"))
    result = {"id": device_id, "tags": full.get("_tags", []),
              "last_inform": full.get("_lastInform"),
              "manufacturer": did.get("_Manufacturer"),
              "model": model_name,
              "serial": did.get("_SerialNumber"),
              "name": meta.get("name"), "customer": meta.get("customer"), "notes": meta.get("notes")}
    for k in _STATUS_KEYS:
        r = resolve(pmap, k)
        if r:
            result[k] = _read(full, r[0])
    # sobreescribir WAN con la conexion realmente activa (solo TR-098; TR-181 usa otro modelo)
    if pmap.get("root") == "InternetGatewayDevice":
        try:
            aw = await _active_wan(device_id)
            if aw:
                result["wan_mode"] = aw["mode"]
                result["wan_ip"] = aw["ip"]
                result["wan_gateway"] = aw["gw"]
                if aw.get("mac"):
                    result["mac"] = aw["mac"]   # MAC WAN real (la de la conexion activa)
                if aw.get("ppp"):
                    result["pppoe_enable"] = True
                    if aw.get("ppp_user"):
                        result["pppoe_user"] = aw["ppp_user"]
        except Exception:
            pass
    else:
        # TR-181: PPPoE segun ConnectionStatus del PPP.Interface
        if result.get("pppoe_status") and result["pppoe_status"] != "Unconfigured":
            result["pppoe_enable"] = result["pppoe_status"] in ("Connected", "Connecting", "Up")
    return result


@router.post("/{device_id}/refresh")
async def refresh(device_id: str, object: str = "", dev=Depends(authorized_device)):
    """Fuerza al CPE a re-enviar su arbol. Sin 'object' refresca la raiz;
    con 'object' refresca solo ese subarbol (mas rapido)."""
    pmap = pick_map(dev)
    # OJO: root SIN punto final (clientes tipo Cudy fallan con el punto).
    obj = object or pmap["root"]
    res = await genie.refresh_object(device_id, obj)
    return {"ok": True, **res}


@router.post("/{device_id}/read")
async def read_status(device_id: str, dev=Depends(authorized_device)):
    """Lee del CPE los parametros de estado (getParameterValues).

    Mas liviano que refrescar todo el arbol: pide solo lo que muestra la ficha.
    Tras un BOOTSTRAP estos valores desaparecen de la cache; esto los repuebla."""
    pmap = pick_map(dev)
    names = []
    for k in _STATUS_KEYS:
        r = resolve(pmap, k)
        if r:
            names.append(r[0])
    res = await genie.get_parameter_values(device_id, names)
    return {"ok": True, **res}


@router.get("/{device_id}/params")
async def list_params(device_id: str, search: str = "", writable_only: bool = False,
                      dev=Depends(authorized_device)):
    """Todos los parametros del equipo que el ACS conoce (arbol completo).

    Para traer TODO lo que el modelo expone, primero usar POST /refresh."""
    full = await genie.get_device(device_id)
    params: list[dict] = []
    for root in _TREE_ROOTS:
        if isinstance(full.get(root), dict):
            params += _flatten(full[root], root)
    if search:
        s = search.lower()
        params = [p for p in params if s in p["path"].lower()
                  or (p["value"] is not None and s in str(p["value"]).lower())]
    if writable_only:
        params = [p for p in params if p["writable"]]
    params.sort(key=lambda x: x["path"])
    return {"count": len(params), "params": params}


@router.put("/{device_id}/param")
async def set_param(device_id: str, body: ParamIn, dev=Depends(authorized_device)):
    """Escribe un parametro arbitrario del arbol (avanzado)."""
    triple = [body.path, body.value] + ([body.type] if body.type else [])
    res = await genie.set_parameter_values(device_id, [triple])
    from .backup import merge_device_config
    merge_device_config(device_id, [triple])   # mantener el respaldo al dia
    return {"ok": True, **res}


@router.get("/{device_id}/label")
async def get_label(device_id: str, dev=Depends(authorized_device)):
    return db.get_device_meta(device_id)


@router.put("/{device_id}/label")
async def set_label(device_id: str, body: LabelIn, dev=Depends(authorized_device)):
    db.set_device_meta(device_id, (body.name or "").strip() or None,
                       (body.customer or "").strip() or None,
                       (body.notes or "").strip() or None)
    return {"ok": True, **db.get_device_meta(device_id)}


@router.get("/{device_id}/audit")
async def device_audit(device_id: str, dev=Depends(authorized_device)):
    """Historial de cambios de este equipo."""
    return db.list_audit(device_id=device_id, limit=200)


@router.get("/{device_id}/hosts")
async def get_hosts(device_id: str, dev=Depends(authorized_device)):
    """Clientes conectados (LAN hosts) del equipo."""
    hosts = await lan_hosts(device_id)
    return {"count": len(hosts), "hosts": hosts}


@router.post("/{device_id}/diag/ping")
async def diag_ping(device_id: str, body: PingIn, dev=Depends(authorized_device)):
    """Ping desde el propio equipo (IPPingDiagnostics)."""
    prefix = "InternetGatewayDevice.IPPingDiagnostics"
    state = await _run_diag(device_id, prefix, {
        "Host": (body.host, "xsd:string"),
        "NumberOfRepetitions": (max(1, min(body.count, 20)), "xsd:unsignedInt"),
    })
    doc = await genie.get_device(device_id, [prefix])
    g = lambda k: _read(doc or {}, f"{prefix}.{k}")
    return {"state": state, "host": body.host,
            "success": g("SuccessCount"), "failure": g("FailureCount"),
            "avg_ms": g("AverageResponseTime"), "min_ms": g("MinimumResponseTime"),
            "max_ms": g("MaximumResponseTime")}


@router.post("/{device_id}/diag/traceroute")
async def diag_traceroute(device_id: str, body: TraceIn, dev=Depends(authorized_device)):
    """Traceroute desde el propio equipo (TraceRouteDiagnostics)."""
    prefix = "InternetGatewayDevice.TraceRouteDiagnostics"
    state = await _run_diag(device_id, prefix, {
        "Host": (body.host, "xsd:string"),
        "MaxHopCount": (max(1, min(body.max_hops, 30)), "xsd:unsignedInt"),
        "NumberOfTries": (max(1, min(body.tries, 5)), "xsd:unsignedInt"),
    }, wait_s=75)
    doc = await genie.get_device(device_id, [prefix])
    base = doc or {}
    for part in prefix.split("."):
        base = base.get(part) if isinstance(base, dict) else None
    hops = []
    node = base.get("RouteHops") if isinstance(base, dict) else None
    if isinstance(node, dict):
        for k, v in sorted(node.items(), key=lambda kv: (kv[0].isdigit() and int(kv[0]) or 0)):
            if k.startswith("_") or not isinstance(v, dict):
                continue
            host = _val(v, "HopHost") or _val(v, "Host")
            addr = _val(v, "HopHostAddress") or _val(v, "HostAddress")
            rtt = _val(v, "HopRTTimes") or _val(v, "RTTimes")
            hops.append({"host": host or addr or "*", "address": addr, "rtt": rtt})
    return {"state": state, "host": body.host,
            "response_ms": _read(doc or {}, f"{prefix}.ResponseTime"), "hops": hops}


@router.post("/read-bulk")
async def read_status_bulk(body: BulkReadIn, user: CurrentUser = Depends(current_user)):
    """Lectura masiva: pide los datos de estado a varios equipos a la vez.

    Encola la lectura (se resuelve en el proximo reporte de cada equipo) para no
    lanzar N connection requests sincronos. Respeta la tenencia (ISP = su tag)."""
    targets = await resolve_targets(user, tag=body.tag, model=body.model, device_ids=body.device_ids)
    if not targets:
        return {"ok": True, "sent": 0, "detail": "Ningun equipo coincide"}
    # nombres de estado a partir del primer equipo (mismo mapa para todos por ahora)
    sample = await genie.get_device(targets[0], ["_deviceId"])
    pmap = pick_map(sample or {})
    names = [resolve(pmap, k)[0] for k in _STATUS_KEYS if resolve(pmap, k)]
    ok, fail = 0, 0
    for dev_id in targets:
        try:
            await genie.get_parameter_values(dev_id, names, connection_request=False)
            ok += 1
        except Exception:
            fail += 1
    return {"ok": True, "sent": ok, "failed": fail,
            "detail": f"Lectura encolada en {ok} equipo(s); se actualiza en su proximo reporte."}
