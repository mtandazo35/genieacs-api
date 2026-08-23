"""Cliente del NBI de GenieACS.

Encapsula las trampas del protocolo:
- El _id de algunos CPE ya viene con %20/%2E literales -> hay que re-encodear
  (los % pasan a %25) para meterlo en el path de la URL.
- connection_request aplica la tarea al instante; sin el, en el proximo inform.
- refreshObject en la raiz de ciertos clientes (Cudy) exige objectName SIN punto
  final o vacio; con punto da "Invalid parameter path".
"""
from __future__ import annotations

import json
from urllib.parse import quote
from typing import Any, Optional

import httpx

from . import runtime


class GenieACSError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"GenieACS {status}: {message}")


def encode_device_id(device_id: str) -> str:
    """Codifica el _id para usarlo como segmento de URL.

    safe='' escapa tambien los % que ya trae el id (%20 -> %2520)."""
    return quote(device_id, safe="")


class GenieACS:
    """La URL/timeout/CR se leen en cada llamada desde runtime (BD>env),
    para poder cambiar de ACS sin reiniciar el servicio."""

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=runtime.nbi_url(), timeout=runtime.nbi_timeout())

    # ---- consultas -------------------------------------------------------
    async def query_devices(
        self, query: dict | None = None, projection: list[str] | None = None
    ) -> list[dict]:
        params: dict[str, str] = {"query": json.dumps(query or {})}
        if projection:
            params["projection"] = ",".join(projection)
        async with await self._client() as c:
            r = await c.get("/devices/", params=params)
        if r.status_code != 200:
            raise GenieACSError(r.status_code, r.text)
        return r.json()

    async def get_device(
        self, device_id: str, projection: list[str] | None = None
    ) -> Optional[dict]:
        rows = await self.query_devices({"_id": device_id}, projection)
        return rows[0] if rows else None

    async def device_exists(self, device_id: str) -> bool:
        rows = await self.query_devices({"_id": device_id}, ["_id"])
        return bool(rows)

    # ---- tareas ----------------------------------------------------------
    async def _post_task(
        self, device_id: str, task: dict, connection_request: Optional[bool] = None
    ) -> dict:
        cr = runtime.default_connection_request() if connection_request is None else connection_request
        path = f"/devices/{encode_device_id(device_id)}/tasks"
        params = {"connection_request": ""} if cr else {}
        async with await self._client() as c:
            r = await c.post(path, params=params, json=task)
        # 200 = ejecutada via connection request; 202 = encolada al proximo inform
        if r.status_code not in (200, 202):
            raise GenieACSError(r.status_code, r.text)
        return {
            "applied": r.status_code == 200,
            "queued": r.status_code == 202,
            "task": r.json() if r.content else None,
        }

    async def set_parameter_values(
        self, device_id: str, values: list[list], connection_request=None
    ) -> dict:
        return await self._post_task(
            device_id,
            {"name": "setParameterValues", "parameterValues": values},
            connection_request,
        )

    async def get_parameter_values(
        self, device_id: str, names: list[str], connection_request=None
    ) -> dict:
        return await self._post_task(
            device_id,
            {"name": "getParameterValues", "parameterNames": names},
            connection_request,
        )

    async def refresh_object(
        self, device_id: str, object_name: str = "InternetGatewayDevice", connection_request=None
    ) -> dict:
        return await self._post_task(
            device_id,
            {"name": "refreshObject", "objectName": object_name},
            connection_request,
        )

    async def reboot(self, device_id: str, connection_request=None) -> dict:
        return await self._post_task(device_id, {"name": "reboot"}, connection_request)

    async def factory_reset(self, device_id: str, connection_request=None) -> dict:
        return await self._post_task(device_id, {"name": "factoryReset"}, connection_request)

    async def download(
        self, device_id: str, file_name: str, file_type="1 Firmware Upgrade Image",
        connection_request=None,
    ) -> dict:
        return await self._post_task(
            device_id,
            {"name": "download", "file": file_name, "fileType": file_type},
            connection_request,
        )

    # ---- tags ------------------------------------------------------------
    async def add_tag(self, device_id: str, tag: str) -> None:
        path = f"/devices/{encode_device_id(device_id)}/tags/{quote(tag, safe='')}"
        async with await self._client() as c:
            r = await c.post(path)
        if r.status_code not in (200, 201):
            raise GenieACSError(r.status_code, r.text)

    async def remove_tag(self, device_id: str, tag: str) -> None:
        path = f"/devices/{encode_device_id(device_id)}/tags/{quote(tag, safe='')}"
        async with await self._client() as c:
            r = await c.delete(path)
        if r.status_code not in (200, 204):
            raise GenieACSError(r.status_code, r.text)

    # ---- archivos (firmware) --------------------------------------------
    async def upload_file(
        self, file_name: str, content: bytes, file_type="1 Firmware Upgrade Image",
        oui: str = "", product_class: str = "", version: str = "",
    ) -> None:
        headers = {"fileType": file_type}
        if oui:
            headers["oui"] = oui
        if product_class:
            headers["productClass"] = product_class
        if version:
            headers["version"] = version
        async with await self._client() as c:
            r = await c.put(f"/files/{quote(file_name, safe='')}", content=content, headers=headers)
        if r.status_code not in (200, 201):
            raise GenieACSError(r.status_code, r.text)

    async def list_files(self) -> list[dict]:
        async with await self._client() as c:
            r = await c.get("/files/")
        if r.status_code != 200:
            raise GenieACSError(r.status_code, r.text)
        return r.json()

    # ---- provisions / presets -------------------------------------------
    async def put_provision(self, name: str, script: str) -> None:
        async with await self._client() as c:
            r = await c.put(f"/provisions/{quote(name, safe='')}", content=script.encode())
        if r.status_code not in (200, 201):
            raise GenieACSError(r.status_code, r.text)

    async def put_preset(self, name: str, preset: dict) -> None:
        async with await self._client() as c:
            r = await c.put(f"/presets/{quote(name, safe='')}", json=preset)
        if r.status_code not in (200, 201):
            raise GenieACSError(r.status_code, r.text)


genie = GenieACS()
