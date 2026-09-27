"""Homologacion asistida: proponer el mapeo de un modelo nuevo y VERIFICARLO.

Las reglas deterministas de treeprofile.py resuelven la mayoria de los casos.
Para lo que quede (parametros propietarios raros, modelos que no siguen el
estandar), aqui se le pide una propuesta a un modelo de lenguaje.

La propuesta no se aplica sola. Se verifica contra el arbol del equipo:
- la ruta tiene que existir (si no, se descarta antes de llegar aqui),
- se lee su valor actual y se muestra, para que una persona confirme,
- solo al confirmar se guarda como correccion en el catalogo del modelo.
"""
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import catalog, db, llm, runtime
from ..deps import CurrentUser, require_admin
from ..genieacs import genie
from ..parammap import pick_map
from ..treeprofile import effective_params, flatten

log = logging.getLogger("genieacs_api.homologacion")

router = APIRouter(prefix="/homologacion", tags=["homologacion"],
                   dependencies=[Depends(require_admin)])


class ProponerIn(BaseModel):
    device_id: str = Field(..., max_length=256)


class ConfirmarIn(BaseModel):
    key: str = Field(..., max_length=256)
    concept: str = Field(..., max_length=64)
    path: str = Field(..., max_length=512)


@router.get("/estado")
async def estado():
    """Si hay proveedor de IA configurado (si no, el panel no ofrece el boton)."""
    return {"disponible": llm.configurado()}


@router.get("/log")
async def registro(limit: int = Query(100, ge=1, le=500),
                   tipo: str | None = Query(None, max_length=32)):
    """Que ha hecho la IA: cada propuesta con lo que respondio, cada confirmacion
    y cada prueba de conexion. El registro de auditoria general solo ve la
    peticion; aqui esta la respuesta."""
    filas = []
    for f in db.list_ia_eventos(limit, tipo):
        try:
            f["detalle"] = json.loads(f["detalle"]) if f.get("detalle") else {}
        except ValueError:
            f["detalle"] = {}
        f["ok"] = bool(f["ok"])
        filas.append(f)
    return filas


@router.post("/proponer")
async def proponer(body: ProponerIn, user: CurrentUser = Depends(require_admin)):
    """Pide al modelo el mapeo de los conceptos que hoy quedan sin resolver.

    Solo salen del servidor RUTAS y tipos, nunca valores: el mapeo no necesita
    saber el SSID ni la clave del abonado."""
    if not llm.configurado():
        raise HTTPException(400, "No hay proveedor de IA configurado (GENIEACS_API_LLM_API_KEY)")
    doc = await genie.get_device(body.device_id)
    if not doc:
        raise HTTPException(404, "Equipo no encontrado en el ACS")

    pmap = pick_map(doc)
    parametros = flatten(doc)
    presentes = {p for p, _v, _w in parametros}
    eff = effective_params(pmap, doc)
    # conceptos que hoy NO se resuelven: para eso se pide ayuda
    faltan = sorted(k for k, (path, _t) in eff.items() if path not in presentes)
    if not faltan:
        return {"ok": True, "faltan": [], "detail": "Este modelo ya se resuelve sin ayuda de la IA"}

    rutas = sorted(p for p, _v, w in parametros if w or p.split(".")[-1] in (
        "ExternalIPAddress", "ConnectionStatus", "MACAddress", "SSID", "UpTime"))
    # registrar el perfil del modelo: sin el, no habria donde guardar la correccion
    key, _perfil = catalog.recordar(doc)
    identidad = catalog.identidad(doc)
    cfg = runtime.llm_config()
    # con el preset, base_url viene vacio: el destino real lo sabe llm.py
    destino = cfg.get("base_url") or llm.PROVEEDORES.get(
        cfg.get("provider"), llm.PROVEEDORES["groq"])[0]
    empezo = time.monotonic()
    try:
        propuesta = await llm.proponer_mapeo(faltan, rutas, str(identidad.get("model")))
    except llm.LLMNoConfigurado as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("la propuesta de mapeo fallo")
        db.log_ia("propuesta", user=user.username, model_key=key, device_id=body.device_id,
                  proveedor=destino, modelo_ia=cfg.get("model"),
                  detalle=json.dumps({"pedidos": faltan, "rutas_enviadas": len(rutas)}),
                  ok=False, ms=int((time.monotonic() - empezo) * 1000),
                  error=f"{type(e).__name__}: {e}"[:300])
        raise HTTPException(502, f"El proveedor de IA no respondio: {type(e).__name__}")

    valores = {p: v for p, v, _w in parametros}
    sugerencias = [{"concept": c, "path": ruta, "valor_actual": valores.get(ruta)}
                   for c, ruta in propuesta["mapeo"].items()]
    # queda constancia de lo que propuso: conceptos y rutas, sin los valores que
    # se muestran al revisar (ahi van el SSID y la clave del abonado)
    db.log_ia("propuesta", user=user.username, model_key=key, device_id=body.device_id,
              proveedor=propuesta.get("proveedor") or destino,
              modelo_ia=propuesta.get("modelo"),
              detalle=json.dumps({"pedidos": faltan, "rutas_enviadas": len(rutas),
                                  "propuesto": propuesta["mapeo"],
                                  "descartadas": propuesta["descartadas"],
                                  "dudas": propuesta["dudas"]}, ensure_ascii=False),
              ms=int((time.monotonic() - empezo) * 1000))
    return {"ok": True, "key": key, "faltan": faltan,
            "sugerencias": sugerencias, "descartadas": propuesta["descartadas"],
            "dudas": propuesta["dudas"], "modelo_ia": propuesta["modelo"],
            "detail": "Propuesta sin aplicar: confirma cada ruta para guardarla en el catalogo"}


@router.post("/confirmar")
async def confirmar(body: ConfirmarIn, user: CurrentUser = Depends(require_admin)):
    """Guarda una sugerencia como correccion del catalogo (tras revisarla)."""
    if not catalog.db.get_model_profile(body.key):
        raise HTTPException(404, "Perfil de modelo no encontrado")
    over = catalog.set_override(body.key, body.concept, body.path)
    # lo que de verdad cambio el catalogo: una propuesta sin confirmar no cuenta
    db.log_ia("confirmacion", user=user.username, model_key=body.key,
              detalle=json.dumps({"concepto": body.concept, "ruta": body.path},
                                 ensure_ascii=False))
    return {"ok": True, "overrides": over}
