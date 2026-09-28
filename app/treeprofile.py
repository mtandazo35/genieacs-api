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
        cada = next((v for p, v, _w in params if p.endswith("ManagementServer.PeriodicInformInterval")), None)
        cuando = (f" Si no responde al momento, lo hara en su proximo reporte (cada {cada} s)."
                  if cada else "")
        hint = (f"El ACS solo tiene {len(params)} parametros de este equipo: falta "
                + " y ".join(ETIQUETAS[m] for m in missing)
                + '. Pulsa "Actualizar" para que el equipo reporte su arbol completo.' + cuando)
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



# ---------------------------------------------------------------------------
# Todas las WAN, no solo la activa.
#
# `derive()` elige UNA interfaz (la de la ruta por defecto) porque la ficha
# necesita saber por donde sale el equipo. Esto es otra pregunta: que WAN tiene
# CONFIGURADAS, con su VLAN y su estado, que es lo que se compara contra lo que
# el ISP quiso aprovisionar.

def _vlan_de(vals: dict, iface_path: str) -> tuple[str | None, str | None]:
    """(vlan, nombre de la terminacion) siguiendo LowerLayers hacia abajo.

    En TR-181 la interfaz IP cuelga de una VLANTermination que es quien sabe el
    VLANID: Device.IP.Interface.7 -> Device.Ethernet.VLANTermination.3 -> 100."""
    bajo = str(vals.get(f"{iface_path}.LowerLayers") or "").rstrip(".")
    if "VLANTermination" not in bajo:
        return None, None
    vid = vals.get(f"{bajo}.VLANID")
    # 0 (y -1, que usa TR-098) son "sin etiquetar": pintarlo como VLAN 0 hace
    # buscar en el router una VLAN que no existe
    return _vlan_util(vid), vals.get(f"{bajo}.Name")


def _vlan_util(vid) -> str | None:
    v = str(vid or "").strip()
    return None if v in ("", "0", "-1") else v


def _cliente_dhcp(vals: dict, paths: list, iface_path: str) -> dict:
    """El cliente DHCP de esa interfaz: de ahi salen gateway y DNS de verdad."""
    for n in _instancias(paths, r"^Device\.DHCPv4\.Client\.(\d+)\."):
        if str(vals.get(f"Device.DHCPv4.Client.{n}.Interface") or "").rstrip(".") == iface_path:
            return {"estado_cliente": vals.get(f"Device.DHCPv4.Client.{n}.Status"),
                    "gateway": _ip_util(vals.get(f"Device.DHCPv4.Client.{n}.IPRouters")),
                    "dns": _ip_util(vals.get(f"Device.DHCPv4.Client.{n}.DNSServers"))}
    return {}


def _ip_util(v) -> str | None:
    """Descarta los 0.0.0.0 y las listas de ceros que reporta un equipo sin enlace."""
    if v in (None, ""):
        return None
    utiles = [x.strip() for x in str(v).split(",") if x.strip() not in ("", "0.0.0.0")]
    return ", ".join(utiles) or None


def wans(doc: dict) -> list[dict]:
    """Las interfaces de lado WAN con lo que se sabe de cada una.

    Se excluye la LAN (la del pool DHCP) y el bucle local; lo demas se devuelve
    aunque este caido o sin IP, porque una WAN configurada y caida es
    justamente lo que hay que ver."""
    vals = {p: v for p, v, _w in flatten(doc)}
    raiz = root_of(doc)
    if raiz == "Device":
        return _wans_tr181(vals)
    if raiz == "InternetGatewayDevice":
        return _wans_tr098(vals)
    return []


def _wans_tr181(vals: dict) -> list[dict]:
    paths = list(vals)
    perfil = _derive_tr181(vals)
    lan, activa = perfil.get("lan"), perfil.get("wan")
    salida = []
    for i in _instancias(paths, r"^Device\.IP\.Interface\.(\d+)\."):
        if i == lan:
            continue
        base = f"Device.IP.Interface.{i}"
        ip = vals.get(f"{base}.IPv4Address.1.IPAddress")
        if str(ip or "").startswith("127."):
            continue
        tipo = str(vals.get(f"{base}.IPv4Address.1.AddressingType") or "")
        bajo = str(vals.get(f"{base}.LowerLayers") or "")
        # IPCP es PPP negociando la IP: para el operador eso es una WAN PPPoE
        modo = ("PPPoE" if tipo == "IPCP" or "PPP.Interface" in bajo
                else "DHCP" if tipo == "DHCP"
                else "Estatica" if tipo == "Static" else tipo or None)
        vlan, nombre_vlan = _vlan_de(vals, base)
        fila = {"path": base, "instancia": i,
                "nombre": vals.get(f"{base}.Name") or nombre_vlan or None,
                "vlan": vlan, "modo": modo, "ip": _ip_util(ip),
                "estado": vals.get(f"{base}.Status"),
                "activa": i == activa, "sobre": bajo.rstrip(".") or None}
        # solo la IP, sin estado ni modo ni de que cuelga: el ACS no termino de
        # leer esa rama. Decirlo es mejor que pintar una WAN con todo vacio
        fila["incompleta"] = not any((modo, fila["estado"], fila["sobre"]))
        fila.update(_cliente_dhcp(vals, paths, base))
        if modo == "PPPoE":
            ppp = _ref(bajo) if "PPP.Interface" in bajo else perfil.get("ppp")
            if ppp:
                fila["usuario"] = vals.get(f"Device.PPP.Interface.{ppp}.Username") or None
                fila["estado"] = (vals.get(f"Device.PPP.Interface.{ppp}.ConnectionStatus")
                                  or fila["estado"])
        salida.append(fila)
    # primero la que lleva el trafico, luego las que estan arriba
    salida.sort(key=lambda f: (not f["activa"], str(f.get("estado")) != "Up",
                               int(f["instancia"])))
    return salida


def _wans_tr098(vals: dict) -> list[dict]:
    """En TR-098 cada WANIPConnection/WANPPPConnection ES una WAN."""
    paths = list(vals)
    activa = str(vals.get("InternetGatewayDevice.Layer3Forwarding.DefaultConnectionService")
                 or "").rstrip(".")
    salida = []
    patron = (r"^(InternetGatewayDevice\.WANDevice\.\d+\.WANConnectionDevice\.\d+"
              r"\.WAN(?:IP|PPP)Connection\.\d+)\.")
    vistos = []
    for p in paths:
        m = re.match(patron, p)
        if m and m.group(1) not in vistos:
            vistos.append(m.group(1))
    for base in vistos:
        es_ppp = "WANPPPConnection" in base
        fila = {"path": base, "instancia": base.rsplit(".", 1)[-1],
                "nombre": vals.get(f"{base}.Name") or None,
                "vlan": _vlan_tr098(vals, base),
                "modo": "PPPoE" if es_ppp else (vals.get(f"{base}.AddressingType") or "DHCP"),
                "ip": _ip_util(vals.get(f"{base}.ExternalIPAddress")),
                "estado": vals.get(f"{base}.ConnectionStatus"),
                "gateway": _ip_util(vals.get(f"{base}.DefaultGateway")),
                "dns": _ip_util(vals.get(f"{base}.DNSServers")),
                "activa": base == activa, "sobre": None}
        if es_ppp:
            fila["usuario"] = vals.get(f"{base}.Username") or None
        salida.append(fila)
    salida.sort(key=lambda f: (not f["activa"], str(f.get("estado")) != "Connected"))
    return salida


def _vlan_tr098(vals: dict, base: str) -> str | None:
    """El VLANID lo marca cada fabricante en su propio parametro; -1 y 0 son
    "sin etiquetar" (TR-098 usa -1 para eso)."""
    return _vlan_util(_primero(vals, [f"{base}.X_TP_VLANIDMark", f"{base}.X_VLANID",
                                      f"{base}.VLANIDMark"]))


def _primero(vals: dict, rutas: list):
    for r in rutas:
        v = vals.get(r)
        if v not in (None, ""):
            return str(v)
    return None

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


def effective_params(pmap: dict, doc: dict, overrides: dict | None = None) -> dict:
    """Rutas efectivas por concepto, por orden de prioridad:

    1. la correccion manual guardada para ese modelo (si la hay),
    2. el mapa escrito a mano, cuando apunta a algo que este equipo SI tiene,
    3. la deduccion del arbol,
    4. el mapa, aunque el equipo no lo tenga (ultimo recurso, compatibilidad).

    Asi ningun equipo que hoy funciona cambia de comportamiento, y los que salian
    en blanco (porque numeran las instancias distinto) se rellenan."""
    from .parammap import resolve

    presentes = {p for p, _v, _w in flatten(doc)}
    derivadas = derived_params(doc)
    overrides = overrides or {}
    out = {}
    for key in set(pmap["params"]) | set(derivadas) | set(overrides):
        mapeada = resolve(pmap, key)
        tipo = (mapeada or derivadas.get(key) or (None, S))[1]
        if key in overrides:
            out[key] = (overrides[key], tipo)
        elif mapeada and mapeada[0] in presentes:
            out[key] = mapeada
        elif key in derivadas:
            out[key] = derivadas[key]
        elif mapeada:
            out[key] = mapeada
    return out


# ---------------------------------------------------------------------------
# Escritura de la WAN en TR-181.
#
# Aprendido del TP-Link EX511 (y del fiasco del acceso remoto): no basta con el
# parametro estandar, hay que mandar tambien el propietario `X_TP_*` en la misma
# tarea o el equipo revierte. Y solo se escribe lo que el arbol del equipo TIENE:
# mandar rutas inexistentes provoca faults en el ACS.
# ---------------------------------------------------------------------------

class WanNoSoportada(ValueError):
    """La WAN no se puede configurar en este equipo con los datos disponibles."""


def wan_write_values(doc: dict, mode: str, ip=None, mask=None, gateway=None,
                     dns=None, mtu=None) -> tuple[list, list]:
    """(valores a escribir, notas) para poner la WAN TR-181 en DHCP o estatica.

    Lanza WanNoSoportada con el motivo cuando falta informacion para hacerlo
    con seguridad (tipicamente: el arbol todavia no esta refrescado)."""
    if root_of(doc) != "Device":
        raise WanNoSoportada("Este equipo no reporta el modelo de datos TR-181")
    vals = {p: v for p, v, _w in flatten(doc)}
    d = derive(doc)
    iface = d.get("wan")
    if not iface:
        raise WanNoSoportada(
            'No se ha podido identificar la interfaz WAN de este equipo. '
            'Pulsa "Actualizar" en la ficha para que reporte su arbol completo.')
    base = f"Device.IP.Interface.{iface}"
    notas = [f"interfaz WAN {base} ({(d.get('evidencia') or {}).get('wan', 'deducida')})"]
    values = []

    def poner(path, valor, tipo="xsd:string"):
        if path in vals:                      # solo lo que el equipo expone
            values.append([path, valor, tipo])
            return True
        return False

    def sin_modo():
        """Sin el parametro de direccionamiento no hay nada que escribir. Si el
        arbol esta a medias, el motivo es ese y tiene arreglo; si esta completo,
        es que el modelo no lo soporta."""
        if not coverage(doc)["complete"]:
            return WanNoSoportada(
                "El ACS todavia no tiene el arbol completo de este equipo, asi que no conoce "
                'los parametros de su WAN. Pulsa "Actualizar" en la ficha y reintenta.')
        return WanNoSoportada("El equipo no expone el modo de direccionamiento de su WAN")

    if mode == "dhcp":
        if not poner(f"{base}.IPv4Address.1.AddressingType", "DHCP"):
            raise sin_modo()
        if poner(f"{base}.X_TP_ConnType", "DHCP"):
            notas.append("se manda tambien X_TP_ConnType (si no, el equipo revierte)")
    elif mode == "static":
        if not (ip and mask and gateway):
            raise WanNoSoportada("En modo estatico se requieren ip, mask y gateway")
        if not poner(f"{base}.IPv4Address.1.AddressingType", "Static"):
            raise sin_modo()
        poner(f"{base}.IPv4Address.1.IPAddress", ip)
        poner(f"{base}.IPv4Address.1.SubnetMask", mask)
        if poner(f"{base}.X_TP_ConnType", "Static"):
            notas.append("se manda tambien X_TP_ConnType (si no, el equipo revierte)")
        ruta = _ruta_de_la_interfaz(vals, iface)
        if not ruta:
            raise WanNoSoportada(
                "No se encuentra la ruta por defecto de la WAN en este equipo, asi que no se "
                'puede fijar el gateway sin dejarlo incomunicado. Pulsa "Actualizar" y reintenta.')
        values.append([f"{ruta}.GatewayIPAddress", gateway, "xsd:string"])
        poner(f"{ruta}.Enable", True, "xsd:boolean")
        poner(f"{base}.X_TP_SetDefaultGateway", True, "xsd:boolean")
        notas.append(f"gateway en {ruta}")
        if dns:
            destino = _dns_activo(vals)
            if destino:
                values.append([destino, ",".join(dns), "xsd:string"])
                notas.append(f"DNS en {destino}")
            else:
                notas.append("el equipo no expone DNS configurable: se ignora ese campo")
    else:
        raise WanNoSoportada(f"Modo de WAN no soportado: {mode}")

    if mtu:
        poner(f"{base}.MaxMTUSize", mtu, "xsd:unsignedInt")
    return values, notas


def _ruta_de_la_interfaz(vals: dict, iface: str) -> str | None:
    """Entrada de la tabla de rutas que apunta a esa interfaz."""
    for path, valor in vals.items():
        m = re.match(r"^(Device\.Routing\.Router\.\d+\.IPv4Forwarding\.\d+)\.Interface$", path)
        if m and _ref(valor) == iface:
            return m.group(1)
    return None


def _dns_activo(vals: dict) -> str | None:
    """El reenviador DNS habilitado, o el primero que exista."""
    candidatos = []
    for path in vals:
        m = re.match(r"^(Device\.DNS\.Relay\.Forwarding\.\d+)\.DNSServer$", path)
        if m:
            candidatos.append(m.group(1))
    for base in sorted(candidatos):
        if str(vals.get(f"{base}.Enable")).lower() == "true":
            return f"{base}.DNSServer"
    return f"{sorted(candidatos)[0]}.DNSServer" if candidatos else None


def wan_current_ip(doc: dict) -> str | None:
    """IP WAN actual segun el perfil derivado (para la guarda de 'misma red')."""
    dp = derived_params(doc).get("wan_ip")
    if not dp:
        return None
    return {p: v for p, v, _w in flatten(doc)}.get(dp[0])


# ---------------------------------------------------------------------------
# Que sabe hacer CADA modelo, deducido de su arbol.
#
# El panel enseñaba las mismas pestanas y campos para todos, y el usuario
# descubria al pulsar que ese equipo no lo soportaba. Aqui se mira si el
# parametro existe (y si es escribible) para cada funcion.
#
# Tres estados, y el tercero importa: con el arbol a medias no se puede decir
# "no soportado", solo "todavia no se sabe".
# ---------------------------------------------------------------------------

SI, NO, QUIZA = "si", "no", "desconocido"

# funcion -> (conceptos que la sustentan, descripcion para el panel)
FUNCIONES = {
    "wifi_2g":      (["wifi_2g_ssid"], "WiFi 2.4 GHz"),
    "wifi_5g":      (["wifi_5g_ssid"], "WiFi 5 GHz"),
    "wifi_canal":   (["wifi_2g_channel", "wifi_5g_channel"], "Cambiar el canal"),
    "wifi_oculta":  (["wifi_2g_hidden", "wifi_5g_hidden"], "Ocultar el SSID"),
    "clientes_wifi": (["wifi_2g_clients", "wifi_5g_clients"], "Clientes conectados por radio"),
    "lan":          (["lan_ip"], "Red LAN"),
    "dhcp":         (["dhcp_min", "dhcp_max"], "Servidor DHCP"),
    "wan_dhcp":     (["wan_mode"], "WAN por DHCP"),
    "wan_estatica": (["wan_ip", "wan_mask"], "WAN con IP fija"),
    "wan_pppoe":    (["pppoe_user"], "WAN por PPPoE"),
    "dns":          (["lan_dns", "wan_dns"], "Servidores DNS"),
    "hora":         (["tz", "ntp1"], "Hora y NTP"),
    "acceso_remoto": (["remote_enable"], "Acceso remoto por WAN"),
    "usuario_admin": (["admin_user"], "Usuario/clave del equipo"),
}

# funcion -> patron de ruta (cosas que no son conceptos del mapa)
FUNCIONES_POR_RUTA = {
    "ipv6":        (r"(IPv6Enable|X_TP_IPv6AddrType|IPv6Address\.\d+\.IPAddress)$", "IPv6"),
    "diag_ping":   (r"(IPPingDiagnostics|IPPing)\.DiagnosticsState$", "Ping desde el equipo"),
    "diag_trace":  (r"TraceRoute\.?(Diagnostics)?\.DiagnosticsState$", "Traceroute desde el equipo"),
    "clientes_lan": (r"Hosts\.Host\.\d+\.", "Lista de clientes de la LAN"),
}

# lo que no depende del arbol: son llamadas TR-069, las soporta cualquier CPE
FUNCIONES_RPC = {
    "reinicio": "Reiniciar",
    "factory_reset": "Restaurar de fabrica",
    "firmware": "Actualizar firmware",
    "reinicio_programado": "Reinicio programado",
}


def capabilities(doc: dict, pmap: dict | None = None) -> dict:
    """Que puede hacer este modelo, con el porque de cada respuesta."""
    from .parammap import pick_map

    pmap = pmap or pick_map(doc)
    completo = coverage(doc)["complete"]
    params = flatten(doc)
    presentes = {p for p, _v, _w in params}
    escribibles = {p for p, _v, w in params if w}
    eff = effective_params(pmap, doc)
    out = {}

    def anotar(clave, etiqueta, rutas):
        """rutas: las que sustentan la funcion en ESTE equipo."""
        hay = [r for r in rutas if r in presentes]
        if hay:
            estado, detalle = SI, hay[0]
        elif completo:
            estado, detalle = NO, "el equipo no lo expone en su arbol"
        else:
            estado, detalle = QUIZA, "falta refrescar el arbol para saberlo"
        out[clave] = {"nombre": etiqueta, "estado": estado,
                      "escribible": any(r in escribibles for r in hay), "detalle": detalle}

    for clave, (conceptos, etiqueta) in FUNCIONES.items():
        rutas = [eff[c][0] for c in conceptos if c in eff]
        anotar(clave, etiqueta, rutas)

    for clave, (patron, etiqueta) in FUNCIONES_POR_RUTA.items():
        rx = re.compile(patron)
        rutas = [p for p in presentes if rx.search(p)]
        anotar(clave, etiqueta, rutas)

    for clave, etiqueta in FUNCIONES_RPC.items():
        out[clave] = {"nombre": etiqueta, "estado": SI, "escribible": True,
                      "detalle": "es una orden TR-069, no depende del arbol"}

    out["_resumen"] = {
        "soportadas": sorted(k for k, v in out.items() if not k.startswith("_") and v["estado"] == SI),
        "no_soportadas": sorted(k for k, v in out.items() if not k.startswith("_") and v["estado"] == NO),
        "por_saber": sorted(k for k, v in out.items() if not k.startswith("_") and v["estado"] == QUIZA),
        "arbol_completo": completo,
    }
    return out
