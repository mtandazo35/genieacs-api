"""Cliente de modelos de lenguaje, con proveedor intercambiable.

Se usa para UNA cosa: proponer el mapeo de un modelo de CPE nuevo (que ruta del
arbol TR-069 corresponde a cada concepto del panel) cuando las reglas
deterministas de treeprofile.py no llegan. La propuesta NUNCA se aplica sola:
hay que verificarla contra el equipo (se lee la ruta y se comprueba que existe
y devuelve algo con sentido) antes de guardarla en el catalogo.

Proveedores: Groq y cualquier otro compatible con el API de OpenAI (OpenRouter,
Together, vLLM local) comparten cliente; se cambian con el .env. Si no hay clave
configurada, la funcion queda desactivada y el panel no ofrece el boton.

Lo que se manda son RUTAS Y TIPOS, nunca valores: el mapeo no necesita saber el
SSID ni la clave del abonado, y asi no sale de la red ningun dato de clientes.
"""
import json
import logging

import httpx

from . import runtime

log = logging.getLogger("genieacs_api.llm")

# Presets por proveedor: solo cambia la URL base y el modelo por defecto.
PROVEEDORES = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct"),
    "local": ("http://127.0.0.1:8000/v1", "local"),
}

# Limite de rutas que se mandan: los arboles TR-181 pasan de 4600 parametros y
# los modelos de Groq tienen ventanas cortas. Se mandan solo las escribibles y
# las de estado, que son donde estan los conceptos del panel.
MAX_RUTAS = 1200


class LLMNoConfigurado(RuntimeError):
    pass


def configurado() -> bool:
    return bool(runtime.llm_config()["api_key"])


def _destino(cfg: dict | None = None) -> tuple[str, str, str]:
    """(base_url, modelo, clave) efectivos: lo del panel manda sobre el .env.

    Con `cfg` se prueba una configuracion que todavia no esta guardada."""
    cfg = cfg or runtime.llm_config()
    if not cfg.get("api_key"):
        raise LLMNoConfigurado(
            "No hay proveedor de IA configurado: ponlo en Ajustes > Inteligencia artificial "
            "(o con GENIEACS_API_LLM_API_KEY en el .env).")
    base, modelo = PROVEEDORES.get(cfg["provider"], PROVEEDORES["groq"])
    return (cfg["base_url"] or base), (cfg["model"] or modelo), cfg["api_key"]


def _mensaje_del_proveedor(r) -> str:
    """Lo que dice el proveedor, tal cual. Adivinar la causa nos costo una tarde."""
    try:
        data = r.json()
    except ValueError:
        return (r.text or "")[:200]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)[:200]
    return str(err or data)[:200]


async def listar_modelos(cfg: dict | None = None) -> list[str]:
    """Modelos que el proveedor dice tener. Valida clave y URL sin gastar tokens."""
    base, _modelo, clave = _destino(cfg)
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {clave}"})
    if r.status_code != 200:
        raise ProveedorRechaza(r.status_code, _mensaje_del_proveedor(r), base)
    datos = r.json()
    filas = datos.get("data") if isinstance(datos, dict) else datos
    return sorted(str(m.get("id")) for m in (filas or []) if isinstance(m, dict) and m.get("id"))


class ProveedorRechaza(RuntimeError):
    def __init__(self, status: int, mensaje: str, base: str):
        self.status, self.mensaje, self.base = status, mensaje, base
        super().__init__(f"HTTP {status}: {mensaje}")


async def probar(cfg: dict | None = None) -> dict:
    """Comprueba la configuracion contra el proveedor y dice que modelos hay.

    Primero pide la lista de modelos: eso valida la clave y la URL sin gastar
    tokens y permite decir exactamente que modelos se pueden usar."""
    base, preset, _clave = _destino(cfg)
    # el del campo Modelo, no el que rellena el preset: si el usuario lo fijo y no
    # existe hay que decirlo, y si lo dejo vacio se elige uno que si exista
    del_usuario = ((cfg or runtime.llm_config()).get("model") or "").strip()
    try:
        modelos = await listar_modelos(cfg)
    except ProveedorRechaza as e:
        motivo = {401: "la clave no es valida", 403: "la clave no tiene permiso"}.get(
            e.status, f"{e.base} no respondio como API (HTTP {e.status})")
        return {"ok": False, "proveedor": e.base, "error": f"{motivo}: {e.mensaje}"}

    if del_usuario and del_usuario not in modelos:
        muestra = ", ".join(modelos[:6]) or "ninguno"
        return {"ok": False, "proveedor": base, "modelo": del_usuario, "modelos": modelos,
                "error": f"el proveedor no tiene el modelo '{del_usuario}'. Disponibles: {muestra}"}

    elegido = del_usuario or _preferido(modelos, preset)
    if not elegido:
        return {"ok": False, "proveedor": base, "modelos": modelos,
                "error": "la clave funciona, pero el proveedor no ofrece ningun modelo"}

    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{base}/chat/completions",
                         json={"model": elegido, "max_tokens": 1,
                               "messages": [{"role": "user", "content": "ok"}]},
                         headers={"Authorization": f"Bearer {_destino(cfg)[2]}"})
    if r.status_code == 200:
        return {"ok": True, "modelo": elegido, "proveedor": base, "modelos": modelos}
    return {"ok": False, "modelo": elegido, "proveedor": base, "modelos": modelos,
            "error": f"el modelo '{elegido}' no acepto la peticion: {_mensaje_del_proveedor(r)}"}


# preferencia cuando el usuario no fija modelo: primero los de instrucciones
# conocidos, y si no, el primero que ofrezca el proveedor
_PREFERIDOS = ("llama-3.3-70b-versatile", "llama-3.1-8b-instant",
               "openai/gpt-oss-120b", "gpt-4o-mini")


def _preferido(modelos: list[str], preset: str = "") -> str | None:
    for m in (preset, *_PREFERIDOS):
        if m and m in modelos:
            return m
    utiles = [m for m in modelos if not any(x in m.lower() for x in ("whisper", "tts", "guard", "embed"))]
    return (utiles or modelos or [None])[0]


def _prompt(conceptos: list[str], rutas: list[str], modelo_cpe: str) -> list[dict]:
    sistema = (
        "Eres un ingeniero TR-069. Recibes la lista de rutas del arbol de datos de un CPE "
        "y una lista de conceptos de un panel de gestion. Devuelves, para cada concepto, la "
        "ruta EXACTA de la lista que le corresponde, o null si ese CPE no lo expone. "
        "No inventes rutas: solo puedes usar rutas que aparezcan literalmente en la lista. "
        "Responde solo JSON: {\"mapeo\": {\"concepto\": \"ruta o null\"}, \"dudas\": [\"...\"]}"
    )
    usuario = (f"Modelo del CPE: {modelo_cpe}\n\nConceptos:\n" + "\n".join(conceptos)
               + "\n\nRutas del equipo:\n" + "\n".join(rutas[:MAX_RUTAS]))
    return [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]


async def proponer_mapeo(conceptos: list[str], rutas: list[str], modelo_cpe: str,
                         timeout: float = 90.0) -> dict:
    """Propuesta del modelo: {concepto: ruta|None}. Solo rutas que existan.

    Devuelve tambien las que el modelo se invento (descartadas) para poder
    valorar cuanto se puede confiar en el."""
    base, modelo, clave = _destino()
    cuerpo = {"model": modelo, "messages": _prompt(conceptos, rutas, modelo_cpe),
              "temperature": 0, "response_format": {"type": "json_object"}}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{base}/chat/completions", json=cuerpo,
                         headers={"Authorization": f"Bearer {clave}"})
    if r.status_code != 200:
        raise RuntimeError(f"El proveedor de IA respondio HTTP {r.status_code}")
    try:
        contenido = r.json()["choices"][0]["message"]["content"]
        data = json.loads(contenido)
    except (KeyError, IndexError, ValueError) as e:
        raise RuntimeError(f"Respuesta del modelo ilegible: {type(e).__name__}")

    existentes = set(rutas)
    mapeo, inventadas = {}, []
    for concepto, ruta in (data.get("mapeo") or {}).items():
        if concepto not in conceptos or not ruta:
            continue
        if ruta in existentes:
            mapeo[concepto] = ruta
        else:
            inventadas.append((concepto, ruta))
    if inventadas:
        log.warning("el modelo propuso %d rutas inexistentes para %s: %s",
                    len(inventadas), modelo_cpe, inventadas[:5])
    return {"mapeo": mapeo, "descartadas": inventadas,
            "dudas": data.get("dudas") or [], "modelo": modelo, "proveedor": base}
