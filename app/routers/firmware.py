"""Gestion de firmware / actualizaciones.

- Cargar firmware al ACS por archivo (multipart) o por URL (descarga server-side).
- Listar y borrar los firmwares cargados.
- Enviar una actualizacion de forma masiva (por tag/modelo/lista) o 1 a 1
  (el 1 a 1 tambien esta en POST /devices/{id}/firmware).

Las operaciones masivas ENCOLAN la descarga (connection_request=False): se
aplican en el proximo reporte de cada equipo, para no lanzar N connection
requests sincronos contra toda la flota.
"""
import os
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from typing import Optional

from ..bulk import resolve_targets
from ..deps import CurrentUser, current_user, require_admin
from ..genieacs import genie

router = APIRouter(prefix="/firmware", tags=["firmware"])

FILE_TYPE = "1 Firmware Upgrade Image"
# tipos soportados (alias corto -> string TR-069)
FT = {
    "firmware": "1 Firmware Upgrade Image",
    "config": "3 Vendor Configuration File",
    "web": "2 Web Content",
}


def _ft(alias: str) -> str:
    return FT.get((alias or "firmware").lower(), FILE_TYPE)


async def file_type_of(file_name: str) -> str:
    """Devuelve el fileType TR-069 de un archivo ya cargado (por su metadata)."""
    for f in await genie.list_files():
        if (f.get("_id") or f.get("filename")) == file_name:
            return (f.get("metadata") or {}).get("fileType") or FILE_TYPE
    return FILE_TYPE


class UploadUrlIn(BaseModel):
    url: str = Field(..., description="URL directa del archivo")
    file_name: Optional[str] = Field(None, description="Nombre con que se guarda (por defecto, el del URL)")
    file_type: str = Field("firmware", description="firmware | config | web")
    product_class: str = ""
    oui: str = ""
    version: str = ""


class PushIn(BaseModel):
    file_name: str
    tag: Optional[str] = None          # admin: filtrar por ISP
    model: Optional[str] = None        # filtrar por modelo (ProductClass)
    device_ids: Optional[list[str]] = None   # lista explicita
    all: bool = False                  # admin: toda la flota (sin filtros)


@router.get("")
async def list_firmware(user: CurrentUser = Depends(current_user)):
    """Lista los firmwares cargados en el ACS (cualquier usuario autenticado)."""
    return await genie.list_files()


@router.post("/upload", dependencies=[Depends(require_admin)])
async def upload_firmware(
    file: UploadFile = File(...),
    file_type: str = Form("firmware"),
    product_class: str = Form(""),
    oui: str = Form(""),
    version: str = Form(""),
):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Archivo vacio")
    await genie.upload_file(file.filename, content, _ft(file_type),
                            oui=oui, product_class=product_class, version=version)
    return {"ok": True, "file_name": file.filename, "size": len(content), "type": _ft(file_type)}


@router.post("/upload-url", dependencies=[Depends(require_admin)])
async def upload_firmware_url(body: UploadUrlIn):
    """Descarga el firmware desde un URL (server-side) y lo guarda en el ACS."""
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL invalida")
    name = body.file_name or os.path.basename(urlparse(body.url).path) or "firmware.bin"
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as c:
            r = await c.get(body.url)
        if r.status_code != 200:
            raise HTTPException(400, f"No se pudo descargar (HTTP {r.status_code})")
        content = r.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error descargando: {type(e).__name__}: {e}")
    if not content:
        raise HTTPException(400, "El URL no devolvio contenido")
    await genie.upload_file(name, content, _ft(body.file_type),
                            oui=body.oui, product_class=body.product_class, version=body.version)
    return {"ok": True, "file_name": name, "size": len(content), "source": body.url, "type": _ft(body.file_type)}


@router.delete("/{file_name:path}", dependencies=[Depends(require_admin)])
async def delete_firmware(file_name: str):
    await genie.delete_file(file_name)
    return {"ok": True, "deleted": file_name}


@router.post("/push", dependencies=[Depends(require_admin)])
async def push_firmware_bulk(body: PushIn, user: CurrentUser = Depends(current_user)):
    """Envia una actualizacion a varios equipos (masivo). Encola la descarga."""
    if not (body.all or body.tag or body.model or body.device_ids):
        raise HTTPException(400, "Indica un objetivo: all, tag, model o device_ids")
    targets = await resolve_targets(user, tag=body.tag, model=body.model, device_ids=body.device_ids)
    if not targets:
        return {"ok": True, "sent": 0, "detail": "Ningun equipo coincide con el filtro"}
    ftype = await file_type_of(body.file_name)   # firmware o config, segun el archivo
    ok, fail = 0, []
    for dev_id in targets:
        try:
            await genie.download(dev_id, body.file_name, file_type=ftype, connection_request=False)
            ok += 1
        except Exception as e:
            fail.append({"device": dev_id, "error": str(e)})
    kind = "Configuracion" if ftype.startswith("3") else "Actualizacion"
    return {"ok": True, "sent": ok, "failed": len(fail), "errors": fail[:20],
            "detail": f"{kind} encolada en {ok} equipo(s); se aplica en su proximo reporte."}
