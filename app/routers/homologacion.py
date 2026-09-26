"""Homologacion asistida: proponer el mapeo de un modelo nuevo y VERIFICARLO.

Las reglas deterministas de treeprofile.py resuelven la mayoria de los casos.
Para lo que quede (parametros propietarios raros, modelos que no siguen el
estandar), aqui se le pide una propuesta a un modelo de lenguaje.

La propuesta no se aplica sola. Se verifica contra el arbol del equipo:
- la ruta tiene que existir (si no, se descarta antes de llegar aqui),
- se lee su valor actual y se muestra, para que una persona confirme,
- solo al confirmar se guarda como correccion en el catalogo del modelo.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import catalog, llm
from ..deps import require_admin
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


@router.post("/proponer")
async def proponer(body: ProponerIn):
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
    try:
        propuesta = await llm.proponer_mapeo(faltan, rutas, str(identidad.get("model")))
    except llm.LLMNoConfigurado as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("la propuesta de mapeo fallo")
        raise HTTPException(502, f"El proveedor de IA no respondio: {type(e).__name__}")

    valores = {p: v for p, v, _w in parametros}
    sugerencias = [{"concept": c, "path": ruta, "valor_actual": valores.get(ruta)}
                   for c, ruta in propuesta["mapeo"].items()]
    return {"ok": True, "key": key, "faltan": faltan,
            "sugerencias": sugerencias, "descartadas": propuesta["descartadas"],
            "dudas": propuesta["dudas"], "modelo_ia": propuesta["modelo"],
            "detail": "Propuesta sin aplicar: confirma cada ruta para guardarla en el catalogo"}


@router.post("/confirmar")
async def confirmar(body: ConfirmarIn):
    """Guarda una sugerencia como correccion del catalogo (tras revisarla)."""
    if not catalog.db.get_model_profile(body.key):
        raise HTTPException(404, "Perfil de modelo no encontrado")
    over = catalog.set_override(body.key, body.concept, body.path)
    return {"ok": True, "overrides": over}
