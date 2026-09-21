"""Gestion de firmware / actualizaciones.

- Cargar firmware al ACS por archivo (multipart) o por URL (descarga server-side).
- Listar y borrar los firmwares cargados.
- Enviar una actualizacion de forma masiva (por tag/modelo/lista) o 1 a 1
  (el 1 a 1 tambien esta en POST /devices/{id}/firmware).

Las operaciones masivas ENCOLAN la descarga (connection_request=False): se
aplican en el proximo reporte de cada equipo, para no lanzar N connection
requests sincronos contra toda la flota.

Los archivos se procesan por bloques (sin cargarlos enteros en memoria), con
tamano maximo (GENIEACS_API_MAX_UPLOAD_MB) y SHA256 en la respuesta.
"""
import hashlib
import logging
import os
import tempfile
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from typing import Optional

from ..bulk import resolve_targets
from ..config import get_settings
from ..deps import CurrentUser, current_user, require_admin
from ..genieacs import genie
from ..netguard import BlockedURL, check_public_url, parse_networks

log = logging.getLogger("genieacs_api.firmware")

router = APIRouter(prefix="/firmware", tags=["firmware"])

FILE_TYPE = "1 Firmware Upgrade Image"
# tipos soportados (alias corto -> string TR-069)
FT = {
    "firmware": "1 Firmware Upgrade Image",
    "config": "3 Vendor Configuration File",
    "web": "2 Web Content",
}
CHUNK = 1024 * 1024
MAX_REDIRECTS = 5


def _ft(alias: str) -> str:
    return FT.get((alias or "firmware").lower(), FILE_TYPE)


def _file_type(f: dict) -> str:
    return (f.get("metadata") or {}).get("fileType") or f.get("fileType") or ""


def is_firmware(file_type: str) -> bool:
    return file_type.startswith("1")


async def file_type_of(file_name: str) -> str | None:
    """fileType TR-069 de un archivo ya cargado (por su metadata), o None si no existe."""
    for f in await genie.list_files():
        if (f.get("_id") or f.get("filename")) == file_name:
            return _file_type(f) or FILE_TYPE
    return None


def max_bytes() -> int:
    return get_settings().max_upload_mb * 1024 * 1024


class _Spool:
    """Archivo temporal con limite de tamano y SHA256 calculado al escribir."""

    def __init__(self):
        self.f = tempfile.SpooledTemporaryFile(max_size=8 * CHUNK)
        self.size = 0
        self.h = hashlib.sha256()
        self.limit = max_bytes()

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.limit:
            raise HTTPException(413, f"Archivo demasiado grande (maximo {get_settings().max_upload_mb} MB)")
        self.h.update(chunk)
        self.f.write(chunk)

    async def chunks(self):
        self.f.seek(0)
        while True:
            b = self.f.read(CHUNK)
            if not b:
                break
            yield b

    def close(self) -> None:
        self.f.close()


async def _store(name: str, spool: _Spool, file_type: str, oui: str, product_class: str,
                 version: str) -> str:
    if spool.size == 0:
        raise HTTPException(400, "Archivo vacio")
    await genie.upload_file(name, spool.chunks(), file_type, oui=oui,
                            product_class=product_class, version=version, size=spool.size)
    digest = spool.h.hexdigest()
    log.info("archivo cargado al ACS: %s (%d bytes, sha256=%s, tipo=%s)", name, spool.size, digest, file_type)
    return digest


class UploadUrlIn(BaseModel):
    url: str = Field(..., max_length=2048, description="URL directa del archivo")
    file_name: Optional[str] = Field(None, max_length=255, description="Nombre con que se guarda (por defecto, el del URL)")
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
    """Lista los archivos cargados en el ACS. Un ISP solo ve firmwares (tipo 1):
    los archivos de configuracion de proveedor son de uso del admin."""
    files = await genie.list_files()
    if user.is_admin:
        return files
    return [f for f in files if is_firmware(_file_type(f))]


@router.post("/upload", dependencies=[Depends(require_admin)])
async def upload_firmware(
    file: UploadFile = File(...),
    file_type: str = Form("firmware"),
    product_class: str = Form(""),
    oui: str = Form(""),
    version: str = Form(""),
):
    spool = _Spool()
    try:
        while True:
            chunk = await file.read(CHUNK)
            if not chunk:
                break
            spool.write(chunk)
        name = os.path.basename(file.filename or "") or "firmware.bin"
        digest = await _store(name, spool, _ft(file_type), oui, product_class, version)
    finally:
        spool.close()
    return {"ok": True, "file_name": name, "size": spool.size, "sha256": digest, "type": _ft(file_type)}


@router.post("/upload-url", dependencies=[Depends(require_admin)])
async def upload_firmware_url(body: UploadUrlIn):
    """Descarga el firmware desde un URL (server-side) y lo guarda en el ACS.

    Solo destinos publicos (o redes de GENIEACS_API_FIRMWARE_ALLOWED_NETWORKS);
    cada redireccion se valida de nuevo."""
    allowed = parse_networks(get_settings().firmware_allowed_networks)
    url = body.url.strip()
    spool = _Spool()
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=False) as c:
            for _ in range(MAX_REDIRECTS + 1):
                await check_public_url(url, allowed)
                async with c.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise HTTPException(400, f"No se pudo descargar (HTTP {r.status_code})")
                    declared = r.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > spool.limit:
                        raise HTTPException(413, f"Archivo demasiado grande (maximo {get_settings().max_upload_mb} MB)")
                    async for chunk in r.aiter_bytes(CHUNK):
                        spool.write(chunk)
                    break
            else:
                raise HTTPException(400, "Demasiadas redirecciones")
        # nombre del URL original (tras un redirect suele ser un nombre de CDN)
        name = os.path.basename(body.file_name or urlparse(body.url.strip()).path) or "firmware.bin"
        digest = await _store(name, spool, _ft(body.file_type), body.oui, body.product_class, body.version)
    except BlockedURL as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(400, f"Error descargando: {type(e).__name__}")
    finally:
        spool.close()
    return {"ok": True, "file_name": name, "size": spool.size, "sha256": digest,
            "source": body.url, "type": _ft(body.file_type)}


@router.delete("/{file_name:path}", dependencies=[Depends(require_admin)])
async def delete_firmware(file_name: str):
    await genie.delete_file(file_name)
    return {"ok": True, "deleted": file_name}


@router.post("/push", dependencies=[Depends(require_admin)])
async def push_firmware_bulk(body: PushIn, user: CurrentUser = Depends(current_user)):
    """Envia una actualizacion a varios equipos (masivo). Encola la descarga."""
    if not (body.all or body.tag or body.model or body.device_ids):
        raise HTTPException(400, "Indica un objetivo: all, tag, model o device_ids")
    ftype = await file_type_of(body.file_name)   # firmware o config, segun el archivo
    if ftype is None:
        raise HTTPException(404, "Archivo no encontrado en el ACS")
    targets = await resolve_targets(user, tag=body.tag, model=body.model, device_ids=body.device_ids)
    if not targets:
        return {"ok": True, "sent": 0, "detail": "Ningun equipo coincide con el filtro"}
    ok, fail = 0, []
    for dev_id in targets:
        try:
            await genie.download(dev_id, body.file_name, file_type=ftype, connection_request=False)
            ok += 1
        except Exception as e:
            log.warning("push %s a %s fallo: %s", body.file_name, dev_id, e)
            fail.append({"device": dev_id, "error": str(e)})
    kind = "Configuracion" if ftype.startswith("3") else "Actualizacion"
    return {"ok": True, "sent": ok, "failed": len(fail), "errors": fail[:20],
            "detail": f"{kind} encolada en {ok} equipo(s); se aplica en su proximo reporte."}
