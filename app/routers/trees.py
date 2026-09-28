"""Base de datos de arboles por modelo (solo admin).

Cada vez que se lee un equipo se guarda la UNION de las rutas de su arbol bajo
la clave fabricante|clase|modelo|firmware. Sirve para tres cosas:

- saber que parametros expone un modelo sin tener un equipo delante,
- ver si el arbol de un equipo concreto esta a medias (y cuanto le falta),
- dar a la IA y a las deducciones una referencia del modelo, no de un equipo.

Se guardan RUTAS y si son escribibles, nunca valores: en un arbol real van el
SSID y la clave WiFi del abonado, y aqui no hacen falta para nada.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from .. import catalog, db
from ..deps import require_admin
from ..genieacs import genie

router = APIRouter(prefix="/trees", tags=["trees"], dependencies=[Depends(require_admin)])

LIMITE_POR_DEFECTO = 500


def _rutas_guardadas(fila: dict) -> dict | None:
    """Las rutas, o None si la fila no se puede leer.

    Devolver {} disfrazaba una fila corrupta de modelo sin rutas, y encima
    junto a un n_params que seguia diciendo 4653."""
    try:
        data = json.loads(fila.get("paths") or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@router.get("")
async def listar():
    """Un resumen por modelo. Sin las rutas: son cientos o miles por fila."""
    filas = db.list_model_trees()
    for f in filas:
        # un equipo con menos rutas que la union tiene el arbol a medias, no es
        # otro modelo. None = todavia no se sabe (ningun equipo registrado):
        # mentir con un "false" es peor que decir que no consta
        f["incompleto"] = (None if f.get("max_equipo") is None
                           else f["max_equipo"] < f["n_params"])
    return filas


# limites del archivo que se importa: un catalogo real de un ISP anda en
# decenas de modelos y unos miles de rutas cada uno; lo de mas arriba es un
# archivo equivocado (o un intento de llenar la memoria del servidor)
MAX_MODELOS = 2000
MAX_RUTAS_POR_MODELO = 50000
FORMATO = 1


@router.get("/export")
async def exportar():
    """El catalogo aprendido, para llevarlo a otra instalacion.

    Lleva rutas, perfil deducido y correcciones a mano. NO lleva equipos (ni
    ids, ni seriales, ni quien aporto que rama) ni ninguna credencial: para eso
    ya esta el respaldo de la BD, que es otra cosa y no se mueve de sitio."""
    modelos, ilegibles = [], 0
    for f in db.model_trees_completos():
        rutas = _rutas_guardadas(f)
        if rutas is None:
            # fila ilegible: no se exporta basura, pero se dice cuantas hubo en
            # vez de entregar un catalogo mas corto sin explicacion
            ilegibles += 1
            continue
        modelos.append({
            "key": f["key"], "root": f.get("root"),
            "manufacturer": f.get("manufacturer"), "product_class": f.get("product_class"),
            "model": f.get("model"), "firmware": f.get("firmware"),
            "paths": rutas,
            "profile": _perfil_sin_evidencia(_json_o_none(f.get("profile"))),
            "overrides": _json_o_none(f.get("overrides")) or {},
        })
    return {"formato": FORMATO, "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "modelos": modelos, "ilegibles": ilegibles}


def _perfil_sin_evidencia(perfil):
    """El perfil sin la evidencia: las instancias deducidas viajan, el porque no.

    La evidencia cita valores del equipo del que se dedujo (la IP del gateway,
    la WAN, la red del pool DHCP). Eso no puede salir en un archivo que se lleva
    a otra instalacion, y alli no sirve de nada: la evidencia de aqui no explica
    nada de la flota de alla, que volvera a deducir la suya al leer un equipo."""
    if not isinstance(perfil, dict):
        return perfil
    return {k: v for k, v in perfil.items() if k != "evidencia"}


def _json_o_none(texto):
    try:
        return json.loads(texto) if texto else None
    except ValueError:
        return None


class CatalogoIn(BaseModel):
    formato: int = Field(..., description="version del archivo; hoy 1")
    modelos: list[dict] = Field(..., max_length=MAX_MODELOS)


@router.post("/import")
async def importar(body: CatalogoIn):
    """Mete un catalogo exportado en esta instalacion, sin pisar lo local.

    - las rutas se UNEN con las que ya hubiera, como cuando se aprenden solas;
    - el perfil deducido solo se escribe si aqui no habia ninguno: lo deducido
      de un equipo real de esta flota manda sobre lo que traiga un archivo;
    - una correccion a mano del archivo solo entra si ese concepto esta libre.
    """
    if body.formato != FORMATO:
        raise HTTPException(422, f"Formato {body.formato} desconocido (esta version lee el {FORMATO})")

    res = {"modelos": 0, "nuevos": 0, "rutas_nuevas": 0,
           "perfiles_nuevos": 0, "correcciones": 0, "ignorados": []}
    for m in body.modelos:
        key = str(m.get("key") or "").strip()
        rutas = m.get("paths")
        if not key or not isinstance(rutas, dict) or not rutas:
            res["ignorados"].append(key or "(sin clave)")
            continue
        if len(rutas) > MAX_RUTAS_POR_MODELO:
            res["ignorados"].append(f"{key} (demasiadas rutas: {len(rutas)})")
            continue
        # solo rutas y si son escribibles; cualquier otra cosa del archivo se tira
        limpias = {str(r): bool(w) for r, w in rutas.items() if isinstance(r, str) and r}
        if not limpias:
            res["ignorados"].append(key)
            continue

        era_nuevo = db.get_model_tree(key) is None
        union = db.merge_model_tree(key, m.get("root") or None, limpias, None)
        res["modelos"] += 1
        res["nuevos"] += 1 if era_nuevo else 0
        res["rutas_nuevas"] += union["nuevas"]

        fila_local = db.get_model_profile(key)
        # "no hay fila" no es "no hay perfil": si la fila existe vacia (p.ej. la
        # creo una importacion anterior para poder colgar las correcciones), un
        # archivo que SI trae perfil tiene que poder rellenarla
        perfil_local = _json_o_none(fila_local.get("profile")) if fila_local else None
        # un archivo de una version anterior puede traer evidencia con IPs de
        # otra red: no se guarda, aqui no explica nada
        del_archivo = _perfil_sin_evidencia(m.get("profile")) or None
        del_archivo = del_archivo if isinstance(del_archivo, dict) else None
        if not fila_local or (del_archivo and not perfil_local):
            partes = key.split(catalog.SEPARADOR)
            fabricante, clase, modelo, firmware = (partes + ["", "", "", ""])[:4]
            db.upsert_model_profile(
                key, m.get("manufacturer") or fabricante, m.get("product_class") or clase,
                m.get("model") or modelo, m.get("firmware") or firmware,
                json.dumps(del_archivo or {}, sort_keys=True, ensure_ascii=False),
                # un modelo que solo entro por un archivo no tiene equipos aqui:
                # decir que hay uno es inventarse flota
                devices=0)
            # solo cuenta como perfil si de verdad se trajo uno: un {} no es un perfil
            res["perfiles_nuevos"] += 1 if del_archivo else 0
        # las correcciones del archivo no pisan las de aqui
        locales = catalog.overrides(key)
        for concepto, ruta in (m.get("overrides") or {}).items():
            if isinstance(concepto, str) and isinstance(ruta, str) and ruta \
                    and concepto not in locales:
                catalog.set_override(key, concepto, ruta)
                res["correcciones"] += 1
    return res


@router.delete("/{key:path}")
async def olvidar(key: str):
    """Borra el arbol de un modelo para que se vuelva a aprender desde cero.

    La union solo crece y el flag de escribible se queda pegado: si entra basura
    (un equipo que reportaba mal su firmware, una prueba), esta es la salida."""
    if not db.borrar_model_tree(key):
        raise HTTPException(404, "De ese modelo no hay arbol guardado")
    return {"ok": True, "key": key}


@router.get("/{key:path}")
async def detalle(key: str,
                  q: Optional[str] = Query(None, max_length=200,
                                           description="filtra rutas que contengan este texto"),
                  escribibles: bool = False,
                  limit: int = Query(LIMITE_POR_DEFECTO, ge=0, le=20000,
                                     description="0 = todas (para descargar)"),
                  comparar_con: Optional[str] = Query(
                      None, max_length=200,
                      description="id de un equipo: dice que rutas del modelo le faltan")):
    fila = db.get_model_tree(key)
    if not fila:
        raise HTTPException(404, "De ese modelo todavia no hay arbol guardado")
    rutas = _rutas_guardadas(fila)
    if rutas is None:
        # se rehace sola en cuanto se abra la ficha de un equipo del modelo
        return {"key": key, "root": fila.get("root"), "ilegible": True,
                "n_params": 0, "total": 0, "truncado": False, "paths": [],
                "updated_at": fila.get("updated_at"),
                "detail": "El arbol guardado de este modelo no se puede leer; "
                          "se rehara al abrir la ficha de un equipo de ese modelo"}
    items = sorted(rutas.items())
    if escribibles:
        items = [(p, w) for p, w in items if w]
    if q:
        aguja = q.lower()
        items = [(p, w) for p, w in items if aguja in p.lower()]
    total = len(items)
    mostradas = items if limit == 0 else items[:limit]

    salida = {"key": key, "root": fila.get("root"), "n_params": fila.get("n_params"),
              "updated_at": fila.get("updated_at"),
              # `total` es DESPUES de filtrar: con q= puede ser mucho menor que n_params
              "total": total, "truncado": total > len(mostradas),
              "paths": [{"path": p, "writable": w} for p, w in mostradas]}
    # limit=0 es la descarga a un archivo: ahi no va NINGUN serial de la flota
    # (ni la lista ni el ultimo que aporto), que cruzan ISP y no hacen falta
    # para saber que expone un modelo
    if limit != 0:
        salida["equipos"] = db.model_tree_devices(key)
        salida["last_device"] = fila.get("last_device")

    if comparar_con:
        try:
            doc = await genie.get_device(comparar_con)
        except Exception as e:                       # el ACS puede no tenerlo
            raise HTTPException(502, f"No se pudo leer ese equipo del ACS: {e}")
        if not doc:
            raise HTTPException(404, "Ese equipo no esta en el ACS")
        suyas = set(catalog.rutas_de(doc))
        faltan = sorted(set(rutas) - suyas)
        salida["comparacion"] = {
            "device_id": comparar_con, "tiene": len(suyas),
            "del_modelo": len(rutas), "faltan": len(faltan),
            # una muestra basta para ver de que parte del arbol se trata
            "ejemplos": faltan[:50],
        }
    return salida
