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

from fastapi import APIRouter, Depends, HTTPException, Query
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
