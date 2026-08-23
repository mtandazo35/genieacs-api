"""Listado y estado de dispositivos (filtrado por ISP)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

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

_STATUS_KEYS = [
    "firmware", "uptime", "cpu", "wan_ip", "pppoe_enable", "pppoe_user",
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
