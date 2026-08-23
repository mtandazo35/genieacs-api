"""Listado y estado de dispositivos (filtrado por ISP)."""
from fastapi import APIRouter, Depends

from ..deps import CurrentUser, authorized_device, current_user, tenant_query
from ..genieacs import genie
from ..parammap import pick_map, resolve

router = APIRouter(prefix="/devices", tags=["devices"])

_STATUS_KEYS = ["firmware", "uptime", "cpu", "wan_ip", "wifi_2g_ssid", "wifi_5g_ssid",
                "lan_ip", "pppoe_enable", "pppoe_user"]


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


@router.get("/{device_id}/status")
async def device_status(device_id: str, dev=Depends(authorized_device)):
    """Estado resumido leyendo la cache del ACS (no consulta al CPE)."""
    pmap = pick_map(dev)
    paths = [resolve(pmap, k)[0] for k in _STATUS_KEYS if resolve(pmap, k)]
    full = await genie.get_device(device_id, paths + ["_tags", "_lastInform"])
    result = {"id": device_id, "tags": full.get("_tags", []), "last_inform": full.get("_lastInform")}
    for k in _STATUS_KEYS:
        r = resolve(pmap, k)
        if r:
            result[k] = _read(full, r[0])
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
