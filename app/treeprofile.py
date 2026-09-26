"""Lectura del arbol real del equipo (lo que el ACS tiene guardado).

El panel traduce conceptos (wifi_2g_ssid, lan_ip...) a rutas TR-069 con mapas
escritos a mano por modelo (parammap.py). Cuando un modelo no encaja, el campo
sale vacio aunque el dato ESTE en el ACS. Este modulo mira el arbol y deduce.

De momento resuelve la primera pregunta: **cuanto arbol hay**. El ACS solo
guarda lo que el equipo reporto; hasta que no se hace un GetParameterNames
(boton "Actualizar" del panel) faltan casi todos los parametros, y sin ellos
ninguna deduccion es posible. Medido en la flota: los equipos recien vinculados
traen 28-45 parametros, y los refrescados 296 (TR-098) o 4653 (TR-181).

Funciones puras: entra el documento de GenieACS, sale informacion. Sin IO.
"""
import re

# Senales que solo aparecen con el arbol completo, y de las que depende deducir
# la ficha del equipo. La ruta por defecto es la que identifica la WAN sin
# saber de que marca es el equipo.
_SIGNALS = {
    "Device": {
        "ruta_defecto": r"^Device\.Routing\.Router\.\d+\.IPv4Forwarding\.\d+\.GatewayIPAddress$",
        "bandas_wifi": r"^Device\.WiFi\.Radio\.\d+\.OperatingFrequencyBand$",
        "pool_dhcp": r"^Device\.DHCPv4\.Server\.Pool\.\d+\.MinAddress$",
    },
    "InternetGatewayDevice": {
        "ruta_defecto": r"^InternetGatewayDevice\.Layer3Forwarding\.DefaultConnectionService$",
        "bandas_wifi": r"WLANConfiguration\.\d+\.Standard$",
        "pool_dhcp": r"LANHostConfigManagement\.MinAddress$",
    },
}

# Las dos que hacen falta para deducir WAN y WiFi; el pool es util pero no corta
_REQUIRED = ("ruta_defecto", "bandas_wifi")

ETIQUETAS = {
    "ruta_defecto": "la ruta por defecto (identifica la WAN)",
    "bandas_wifi": "las bandas de las radios WiFi",
    "pool_dhcp": "el rango DHCP de la LAN",
}

ROOTS = ("Device", "InternetGatewayDevice")


def root_of(doc: dict) -> str | None:
    """Raiz del modelo de datos que reporta el equipo (TR-181 o TR-098)."""
    for r in ROOTS:
        if isinstance(doc.get(r), dict):
            return r
    return None


def flatten(doc: dict) -> list[tuple[str, object, bool]]:
    """[(path, valor, escribible)] de todo el arbol del equipo."""
    out: list[tuple[str, object, bool]] = []

    def walk(node, prefix):
        if not isinstance(node, dict):
            return
        if "_value" in node:
            out.append((prefix, node.get("_value"), bool(node.get("_writable"))))
            return
        for k, v in node.items():
            if not k.startswith("_"):
                walk(v, f"{prefix}.{k}" if prefix else k)

    for r in ROOTS:
        if isinstance(doc.get(r), dict):
            walk(doc[r], r)
    return out


def coverage(doc: dict) -> dict:
    """Cuanto arbol tiene el ACS de este equipo y que senales le faltan.

    complete=False significa que hay que pulsar "Actualizar" (refreshObject):
    no es que el equipo no lo soporte, es que nadie se lo ha preguntado."""
    root = root_of(doc)
    params = flatten(doc)
    signals = _SIGNALS.get(root or "", {})
    present = {}
    for name, pattern in signals.items():
        rx = re.compile(pattern)
        present[name] = any(rx.search(p) for p, _v, _w in params)
    missing = [n for n in _REQUIRED if signals and not present.get(n)]
    complete = bool(root) and not missing
    if complete:
        hint = None
    elif missing:
        hint = (f"El ACS solo tiene {len(params)} parametros de este equipo: falta "
                + " y ".join(ETIQUETAS[m] for m in missing)
                + '. Pulsa "Actualizar" para que el equipo reporte su arbol completo.')
    else:
        hint = "El equipo no ha reportado todavia un modelo de datos reconocible."
    return {
        "root": root,
        "params": len(params),
        "writable": sum(1 for _p, _v, w in params if w),
        "complete": complete,
        "missing": missing,
        "signals": present,
        "hint": hint,
    }
