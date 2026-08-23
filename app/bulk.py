"""Resolucion de equipos objetivo para operaciones en lote (masivas).

Respeta la multi-tenencia: un usuario ISP solo alcanza sus equipos (por tag),
un admin puede filtrar por tag / modelo / lista de IDs, o toda la flota.
"""
from .deps import CurrentUser
from .genieacs import genie


async def resolve_targets(user: CurrentUser, tag: str | None = None,
                          model: str | None = None,
                          device_ids: list[str] | None = None) -> list[str]:
    q: dict = {}
    if not user.is_admin:
        q["_tags"] = user.isp            # ISP: siempre acotado a su tag
    elif tag:
        q["_tags"] = tag                 # admin: filtro opcional por tag
    if model:
        q["_deviceId._ProductClass"] = model
    if device_ids:
        q["_id"] = {"$in": device_ids}
    rows = await genie.query_devices(q, ["_id"])
    return [r["_id"] for r in rows]
