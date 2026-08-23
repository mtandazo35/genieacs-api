"""Listado y estado de dispositivos (filtrado por ISP)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Any, Optional

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
    "firmware", "uptime", "cpu", "wan_mode", "wan_ip", "wan_gateway", "pppoe_enable", "pppoe_user",
    "lan_ip", "dhcp_min", "dhcp_max",
    "wifi_2g_ssid", "wifi_2g_password", "wifi_2g_enable", "wifi_2g_channel",
    "wifi_5g_ssid", "wifi_5g_password", "wifi_5g_enable", "wifi_5g_channel",
]


@router.get("")
async def list_devices(user: CurrentUser = Depends(current_user)):
    """Lista los CPE visibles para el usuario (admin=todos, ISP=por su tag)."""
    projection = ["_id", "_tags", "_lastInform", "_deviceId",
                  "InternetGatewayDevice.DeviceInfo.SoftwareVersion"]
    rows = await genie.query_devices(tenant_query(user), projection)
    out = []
    for d in rows:
        info = (d.get("InternetGatewayDevice", {}).get("DeviceInfo", {}))
        # _deviceId lo rellena GenieACS desde el Inform (siempre presente,
        # sobrevive a un BOOTSTRAP que limpia el resto del arbol)
        did = d.get("_deviceId", {})
        out.append({
            "id": d["_id"],
            "tags": d.get("_tags", []),
            "last_inform": d.get("_lastInform"),
            "manufacturer": did.get("_Manufacturer"),
            "model": did.get("_ProductClass"),
            "serial": did.get("_SerialNumber"),
            "firmware": (info.get("SoftwareVersion") or {}).get("_value"),
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

    # la instancia activa a veces no trae IP/gateway: usar la de cualquier instancia que si la tenga
    ip_fallback = first_nonempty(ipconns, "ExternalIPAddress")
    gw_fallback = first_nonempty(ipconns, "DefaultGateway")

    for inst in pppconns:
        if _val(inst, "ConnectionStatus") in ("Connected", "Up"):
            return {"mode": "PPPoE", "ip": _val(inst, "ExternalIPAddress") or ip_fallback,
                    "gw": _val(inst, "DefaultGateway") or gw_fallback,
                    "ppp_user": _val(inst, "Username"), "ppp": True}
    for inst in ipconns:
        if _val(inst, "ConnectionStatus") in ("Connected", "Up"):
            return {"mode": _val(inst, "AddressingType"),
                    "ip": _val(inst, "ExternalIPAddress") or ip_fallback,
                    "gw": _val(inst, "DefaultGateway") or gw_fallback, "ppp": False}
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
    pmap = pick_map(dev)
    paths = [resolve(pmap, k)[0] for k in _STATUS_KEYS if resolve(pmap, k)]
    full = await genie.get_device(device_id, paths + ["_tags", "_lastInform", "_deviceId"])
    did = full.get("_deviceId", {})
    result = {"id": device_id, "tags": full.get("_tags", []),
              "last_inform": full.get("_lastInform"),
              "manufacturer": did.get("_Manufacturer"),
              "model": did.get("_ProductClass"),
              "serial": did.get("_SerialNumber")}
    for k in _STATUS_KEYS:
        r = resolve(pmap, k)
        if r:
            result[k] = _read(full, r[0])
    # sobreescribir WAN con la conexion realmente activa (no la instancia .1 fija)
    try:
        aw = await _active_wan(device_id)
        if aw:
            result["wan_mode"] = aw["mode"]
            result["wan_ip"] = aw["ip"]
            result["wan_gateway"] = aw["gw"]
            if aw.get("ppp"):
                result["pppoe_enable"] = True
                if aw.get("ppp_user"):
                    result["pppoe_user"] = aw["ppp_user"]
    except Exception:
        pass
    return result


@router.post("/{device_id}/refresh")
async def refresh(device_id: str, dev=Depends(authorized_device)):
    """Fuerza al CPE a re-enviar todo su arbol (GetParameterNames de la raiz)."""
    pmap = pick_map(dev)
    # OJO: root SIN punto final (clientes tipo Cudy fallan con el punto).
    res = await genie.refresh_object(device_id, pmap["root"])
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
