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


# ---------------------------------------------------------------------------
# Perfil derivado: que instancia es la WAN, la LAN, cada radio WiFi y el PPPoE,
# deducido del arbol del propio equipo en vez de escrito a mano por marca.
# ---------------------------------------------------------------------------

def _net24(ip) -> str | None:
    """Los tres primeros octetos, para agrupar IPs de la misma LAN sin mascara."""
    if not isinstance(ip, str):
        return None
    partes = ip.split(".")
    return ".".join(partes[:3]) if len(partes) == 4 else None


def _instancias(paths, patron) -> list[str]:
    """Numeros de instancia presentes para un patron ('...Interface.(N).')."""
    rx = re.compile(patron)
    vistas = []
    for p in paths:
        m = rx.match(p)
        if m and m.group(1) not in vistas:
            vistas.append(m.group(1))
    return sorted(vistas, key=lambda x: int(x) if x.isdigit() else 0)


def _ref(valor) -> str | None:
    """Ultima instancia de una referencia TR-181 ('Device.WiFi.Radio.2.' -> '2')."""
    if not isinstance(valor, str):
        return None
    m = re.search(r"\.(\d+)\.?$", valor.strip())
    return m.group(1) if m else None


def _derive_tr181(vals: dict) -> dict:
    paths = list(vals)
    ev = {}

    # --- LAN: la interfaz cuya IP cae en la red del pool DHCP
    pools = _instancias(paths, r"^Device\.DHCPv4\.Server\.Pool\.(\d+)\.")
    pool = pools[0] if pools else None
    pool_net = _net24(vals.get(f"Device.DHCPv4.Server.Pool.{pool}.MinAddress")) if pool else None
    ifaces = _instancias(paths, r"^Device\.IP\.Interface\.(\d+)\.")
    ips = {i: vals.get(f"Device.IP.Interface.{i}.IPv4Address.1.IPAddress") for i in ifaces}
    lan = next((i for i in ifaces if pool_net and _net24(ips.get(i)) == pool_net), None)
    if lan:
        ev["lan"] = f"su IP esta en la red del pool DHCP ({pool_net}.0)"

    # --- WAN: 1) ruta por defecto activa  2) marca de servicio  3) la unica que no es LAN
    wan = None
    for r in _instancias(paths, r"^Device\.Routing\.Router\.\d+\.IPv4Forwarding\.(\d+)\."):
        base = next((p[: -len(".GatewayIPAddress")] for p in paths
                     if re.match(rf"^Device\.Routing\.Router\.\d+\.IPv4Forwarding\.{r}\.GatewayIPAddress$", p)), None)
        if not base:
            continue
        gw = vals.get(f"{base}.GatewayIPAddress")
        if str(vals.get(f"{base}.Enable")).lower() == "true" and gw not in (None, "", "0.0.0.0"):
            wan = _ref(vals.get(f"{base}.Interface"))
            if wan:
                ev["wan"] = f"la ruta por defecto activa (gw {gw}) apunta a esta interfaz"
                ev["wan_gateway_path"] = f"{base}.GatewayIPAddress"
                break
    if not wan:
        marcada = next((i for i in ifaces
                        if vals.get(f"Device.IP.Interface.{i}.X_TP_ServiceType") == "Internet"), None)
        if marcada:
            wan, ev["wan"] = marcada, "la interfaz esta marcada como servicio de Internet"
    if not wan:
        candidatas = [i for i in ifaces
                      if i != lan and _net24(ips.get(i)) and _net24(ips.get(i)) != pool_net
                      and not str(ips.get(i)).startswith("127.")]
        if len(candidatas) == 1:
            wan = candidatas[0]
            ev["wan"] = f"unica interfaz con IP fuera de la LAN ({ips.get(wan)})"

    # --- WiFi: banda de cada radio, y el primer SSID colgado de esa radio
    wifi = {}
    for radio in _instancias(paths, r"^Device\.WiFi\.Radio\.(\d+)\."):
        banda = str(vals.get(f"Device.WiFi.Radio.{radio}.OperatingFrequencyBand") or "")
        clave = "2g" if banda.startswith("2.4") else "5g" if banda.startswith("5") else None
        if not clave or clave in wifi:
            continue
        ssid = next((s for s in _instancias(paths, r"^Device\.WiFi\.SSID\.(\d+)\.")
                     if _ref(vals.get(f"Device.WiFi.SSID.{s}.LowerLayers")) == radio), None)
        if not ssid:
            continue
        ap = next((a for a in _instancias(paths, r"^Device\.WiFi\.AccessPoint\.(\d+)\.")
                   if _ref(vals.get(f"Device.WiFi.AccessPoint.{a}.SSIDReference")) == ssid), None)
        wifi[clave] = {"radio": radio, "ssid": ssid, "ap": ap}
        ev[f"wifi_{clave}"] = f"Radio.{radio} reporta {banda} y su primer SSID es el {ssid}"

    ppps = _instancias(paths, r"^Device\.PPP\.Interface\.(\d+)\.")
    return {"root": "Device", "lan": lan, "wan": wan, "wifi": wifi,
            "ppp": ppps[0] if ppps else None, "pool": pool, "evidencia": ev}


def _derive_tr098(vals: dict) -> dict:
    paths = list(vals)
    ev = {}
    # --- WAN: la conexion que el propio equipo declara como servicio por defecto
    wan_conn = None
    destino = vals.get("InternetGatewayDevice.Layer3Forwarding.DefaultConnectionService")
    if isinstance(destino, str) and "WANConnectionDevice" in destino:
        wan_conn = destino.rstrip(".")
        ev["wan"] = "el equipo la declara como conexion por defecto"

    # --- WiFi: banda por Standard y, si no lo reporta, por el canal
    wifi = {}
    for i in _instancias(paths, r"^InternetGatewayDevice\.LANDevice\.\d+\.WLANConfiguration\.(\d+)\."):
        base = f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{i}"
        std = str(vals.get(f"{base}.Standard") or "")
        canal = vals.get(f"{base}.Channel")
        clave = None
        if std:
            clave = "2g" if ("b" in std or "g" in std) else "5g" if "a" in std else None
        elif str(canal).isdigit():
            clave = "5g" if int(canal) >= 32 else "2g"
        if clave and clave not in wifi:
            wifi[clave] = {"wlan": i}
            ev[f"wifi_{clave}"] = f"WLANConfiguration.{i} reporta Standard '{std}' / canal {canal}"
    return {"root": "InternetGatewayDevice", "wan_conn": wan_conn, "wifi": wifi, "evidencia": ev}


def derive(doc: dict) -> dict:
    """Instancias reales de este equipo, deducidas de su arbol."""
    root = root_of(doc)
    vals = {p: v for p, v, _w in flatten(doc)}
    if root == "Device":
        return _derive_tr181(vals)
    if root == "InternetGatewayDevice":
        return _derive_tr098(vals)
    return {"root": None, "evidencia": {}}


S, B, U = "xsd:string", "xsd:boolean", "xsd:unsignedInt"


def derived_params(doc: dict) -> dict:
    """Conceptos del panel -> (path, tipo) usando las instancias deducidas."""
    d = derive(doc)
    out: dict[str, tuple[str, str]] = {}
    if d.get("root") == "Device":
        for banda, inst in (d.get("wifi") or {}).items():
            ssid, ap, radio = inst.get("ssid"), inst.get("ap"), inst.get("radio")
            if ssid:
                out[f"wifi_{banda}_ssid"] = (f"Device.WiFi.SSID.{ssid}.SSID", S)
                out[f"wifi_{banda}_enable"] = (f"Device.WiFi.SSID.{ssid}.Enable", B)
            if ap:
                out[f"wifi_{banda}_password"] = (f"Device.WiFi.AccessPoint.{ap}.Security.KeyPassphrase", S)
                out[f"wifi_{banda}_clients"] = (f"Device.WiFi.AccessPoint.{ap}.AssociatedDeviceNumberOfEntries", U)
            if radio:
                out[f"wifi_{banda}_channel"] = (f"Device.WiFi.Radio.{radio}.Channel", U)
        if d.get("lan"):
            out["lan_ip"] = (f"Device.IP.Interface.{d['lan']}.IPv4Address.1.IPAddress", S)
            out["lan_mask"] = (f"Device.IP.Interface.{d['lan']}.IPv4Address.1.SubnetMask", S)
        if d.get("wan"):
            base = f"Device.IP.Interface.{d['wan']}"
            out["wan_mode"] = (f"{base}.IPv4Address.1.AddressingType", S)
            out["wan_ip"] = (f"{base}.IPv4Address.1.IPAddress", S)
            out["wan_mask"] = (f"{base}.IPv4Address.1.SubnetMask", S)
            out["wan_mtu"] = (f"{base}.MaxMTUSize", U)
        gwp = (d.get("evidencia") or {}).get("wan_gateway_path")
        if gwp:
            out["wan_gateway"] = (gwp, S)
        if d.get("pool"):
            base = f"Device.DHCPv4.Server.Pool.{d['pool']}"
            out["dhcp_min"] = (f"{base}.MinAddress", S)
            out["dhcp_max"] = (f"{base}.MaxAddress", S)
            out["dhcp_enable"] = (f"{base}.Enable", B)
        if d.get("ppp"):
            base = f"Device.PPP.Interface.{d['ppp']}"
            out["pppoe_enable"] = (f"{base}.Enable", B)
            out["pppoe_user"] = (f"{base}.Username", S)
            out["pppoe_password"] = (f"{base}.Password", S)
            out["pppoe_status"] = (f"{base}.ConnectionStatus", S)
    elif d.get("root") == "InternetGatewayDevice":
        for banda, inst in (d.get("wifi") or {}).items():
            base = f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{inst['wlan']}"
            out[f"wifi_{banda}_ssid"] = (f"{base}.SSID", S)
            out[f"wifi_{banda}_password"] = (f"{base}.X_CUDY_Password", S)
            out[f"wifi_{banda}_enable"] = (f"{base}.Enable", B)
            out[f"wifi_{banda}_channel"] = (f"{base}.Channel", U)
            out[f"wifi_{banda}_hidden"] = (f"{base}.SSIDAdvertisementEnabled", B)
            out[f"wifi_{banda}_clients"] = (f"{base}.AssociatedDeviceNumberOfEntries", U)
        conn = d.get("wan_conn")
        if conn:
            out["wan_ip"] = (f"{conn}.ExternalIPAddress", S)
            out["wan_gateway"] = (f"{conn}.DefaultGateway", S)
            out["wan_mtu"] = (f"{conn}.MaxMTUSize", U)
            out["wan_dns"] = (f"{conn}.DNSServers", S)
            if "WANPPPConnection" in conn:
                out["pppoe_enable"] = (f"{conn}.Enable", B)
                out["pppoe_user"] = (f"{conn}.Username", S)
                out["pppoe_password"] = (f"{conn}.Password", S)
                out["pppoe_status"] = (f"{conn}.ConnectionStatus", S)
            else:
                out["wan_mode"] = (f"{conn}.AddressingType", S)
                out["wan_mask"] = (f"{conn}.SubnetMask", S)
    return out


def effective_params(pmap: dict, doc: dict) -> dict:
    """Rutas efectivas por concepto: manda el mapa escrito a mano, y solo se usa
    la deduccion donde ese mapa apunta a algo que este equipo NO tiene.

    Asi ningun equipo que hoy funciona cambia de comportamiento, y los que salian
    en blanco (instancias distintas a las del modelo de referencia) se rellenan."""
    from .parammap import resolve

    presentes = {p for p, _v, _w in flatten(doc)}
    derivadas = derived_params(doc)
    out = {}
    for key in set(pmap["params"]) | set(derivadas):
        mapeada = resolve(pmap, key)
        if mapeada and mapeada[0] in presentes:
            out[key] = mapeada
        elif key in derivadas:
            out[key] = derivadas[key]
        elif mapeada:
            out[key] = mapeada
    return out
