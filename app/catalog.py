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
from .treeprofile import derive, flatten, root_of

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
    recordar_arbol(doc)          # el arbol crudo tambien se guarda, por modelo
    return key, perfil


def rutas_de(doc: dict) -> dict:
    """{ruta: escribible} del arbol de un equipo. Sin valores, a proposito:
    el SSID y la clave del abonado no tienen nada que hacer en una base de
    arboles que existe para saber QUE parametros expone un modelo."""
    return {path: bool(w) for path, _v, w in flatten(doc)}


def recordar_arbol(doc: dict) -> dict:
    """Guarda el arbol de este equipo en la base del modelo y devuelve el resumen.

    Se UNEN las rutas en vez de sobrescribir: un equipo recien adoptado trae 30
    parametros y otro del mismo modelo ya refrescado trae 3500; la union tambien
    recoge lo que un equipo expone y otro no porque tiene la funcion apagada."""
    key = clave(doc)
    del_equipo = rutas_de(doc)
    res = db.merge_model_tree(key, root_of(doc), del_equipo, doc.get("_id") or None)
    if res["nuevas"]:
        log.info("arbol de modelo %s: %d rutas (+%d)", key, res["n_params"], res["nuevas"])
    return {"key": key, "del_equipo": len(del_equipo), **res}


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
