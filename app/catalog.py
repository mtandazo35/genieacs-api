"""Catalogo de perfiles por modelo + firmware.

El perfil (que instancia es la WAN, la LAN, cada radio...) se deduce del arbol
de cada equipo, pero es una propiedad del MODELO, no del equipo: dos routers
iguales con el mismo firmware se comportan igual. Aqui se guarda lo deducido
para poder consultarlo, auditar la evidencia y, cuando una deduccion no acierte,
corregir a mano una ruta concreta sin tocar codigo.

Las correcciones manuales (`overrides`) mandan sobre todo lo demas.
"""
import json
import logging

from . import db
from .treeprofile import derive, flatten

log = logging.getLogger("genieacs_api.catalog")

SEPARADOR = "|"


def _dato(doc: dict, sufijo: str):
    for path, value, _w in flatten(doc):
        if path.endswith(sufijo):
            return value
    return None


def identidad(doc: dict) -> dict:
    """Fabricante, clase, modelo y firmware tal como los reporta el equipo."""
    did = doc.get("_deviceId") or {}
    modelo = _dato(doc, "DeviceInfo.ModelName") or did.get("_ProductClass")
    return {
        "manufacturer": did.get("_Manufacturer"),
        "product_class": did.get("_ProductClass"),
        "model": modelo,
        "firmware": _dato(doc, "DeviceInfo.SoftwareVersion"),
    }


def clave(doc: dict) -> str:
    i = identidad(doc)
    return SEPARADOR.join(str(i[k] or "?") for k in
                          ("manufacturer", "product_class", "model", "firmware"))


def recordar(doc: dict) -> tuple[str, dict]:
    """Guarda (o actualiza) el perfil deducido de este modelo. Devuelve (clave, perfil).

    Solo escribe cuando lo deducido cambia, para no tocar la BD en cada lectura."""
    key = clave(doc)
    perfil = derive(doc)
    nuevo = json.dumps(perfil, sort_keys=True, ensure_ascii=False)
    fila = db.get_model_profile(key)
    if not fila or fila.get("profile") != nuevo:
        i = identidad(doc)
        db.upsert_model_profile(key, i["manufacturer"], i["product_class"],
                                i["model"], i["firmware"], nuevo)
        log.info("perfil de modelo %s: %s", "actualizado" if fila else "aprendido", key)
    return key, perfil


def overrides(key: str) -> dict:
    """Correcciones manuales guardadas para ese modelo: concepto -> path."""
    fila = db.get_model_profile(key)
    if not fila or not fila.get("overrides"):
        return {}
    try:
        data = json.loads(fila["overrides"])
    except ValueError:
        log.exception("overrides corruptos en el perfil %s; se ignoran", key)
        return {}
    return data if isinstance(data, dict) else {}


def set_override(key: str, concepto: str, path: str | None) -> dict:
    """Fija (o borra, con path=None) la ruta de un concepto para ese modelo."""
    actuales = overrides(key)
    if path:
        actuales[concepto] = path
    else:
        actuales.pop(concepto, None)
    db.set_model_overrides(key, json.dumps(actuales, ensure_ascii=False) if actuales else None)
    return actuales
