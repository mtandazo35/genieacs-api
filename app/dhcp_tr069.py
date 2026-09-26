"""Generador del script de MikroTik para entregar el ACS por DHCP (Option 43).

Un CPE que no recibe la URL del ACS nunca aparece en el panel. La via estandar
es el DHCP: TR-069 Anexo G dice que el CPE se anuncia con `dslforum.org` en la
opcion 60 y el servidor responde con la opcion 43 en formato TLV. El parque real
no lo respeta igual, asi que hay tres codificaciones:

- **TLV** (lo estandar): subopcion 1 + longitud + URL.
- **Cadena plana**: la URL tal cual; lo que entienden muchos equipos baratos.
- **Opcion 125**: enterprise 3561 (BBF) envolviendo la misma subopcion.

La opcion 43 solo admite UN valor por cliente, asi que o TLV o plana. En
RouterOS **v7** hay *matchers* y se puede dar TLV a quien se anuncia como
`dslforum.org` y plana al resto; en **v6** no existen, y hay que elegir.

El script resultante **no crea redes**: solo asigna el option-set a redes que ya
existen, y trae su bloque para deshacer exactamente lo que anadio.
"""
import ipaddress
from datetime import date

MARCA = "tr069-genieacs"          # prefijo y comentario de todo lo que se crea
ENTERPRISE_BBF = "00000de9"       # 3561, el enterprise del Broadband Forum
MAX_OPCION = 255


class DhcpInvalido(ValueError):
    pass


def _hex(texto: str) -> str:
    return texto.encode("ascii", errors="strict").hex()


def option43_tlv(url: str) -> str:
    """Opcion 43 en TLV: subopcion 1 (URL del ACS) + longitud + valor."""
    datos = url.encode("ascii")
    if len(datos) > MAX_OPCION - 2:
        raise DhcpInvalido(f"La URL del ACS no cabe en la opcion 43 ({len(datos)} bytes, maximo 253)")
    return f"0x01{len(datos):02x}{datos.hex()}"


def option125(url: str) -> str:
    """Opcion 125 (RFC 3925): enterprise + longitud + la misma subopcion 1."""
    datos = url.encode("ascii")
    sub = f"01{len(datos):02x}{datos.hex()}"
    total = len(sub) // 2
    if total > MAX_OPCION - 5:
        raise DhcpInvalido("La URL del ACS no cabe en la opcion 125")
    return f"0x{ENTERPRISE_BBF}{total:02x}{sub}"


def validar_url(url: str) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise DhcpInvalido("La URL del ACS debe empezar por http:// o https://")
    try:
        _hex(url)
    except UnicodeEncodeError:
        raise DhcpInvalido("La URL del ACS solo puede llevar caracteres ASCII")
    if len(url) > 200:
        raise DhcpInvalido("URL del ACS demasiado larga")
    return url


def validar_red(red: dict) -> dict:
    """Comprueba que la red, el gateway y el pool son coherentes."""
    try:
        net = ipaddress.ip_network(red["cidr"], strict=False)
    except (KeyError, ValueError):
        raise DhcpInvalido(f"Red no valida: {red.get('cidr')!r} (usa notacion CIDR)")
    out = {"cidr": str(net), "comment": (red.get("comment") or "").strip()}
    gw = red.get("gateway")
    if gw:
        try:
            gw_addr = ipaddress.ip_address(gw)
        except ValueError:
            raise DhcpInvalido(f"Gateway no valido en {net}: {gw!r}")
        if gw_addr not in net:
            raise DhcpInvalido(f"El gateway {gw} no pertenece a la red {net}")
        out["gateway"] = str(gw_addr)
    desde, hasta = red.get("pool_from"), red.get("pool_to")
    if desde or hasta:
        if not (desde and hasta):
            raise DhcpInvalido(f"En {net} hay que indicar el rango completo del pool (desde y hasta)")
        try:
            a, b = ipaddress.ip_address(desde), ipaddress.ip_address(hasta)
        except ValueError:
            raise DhcpInvalido(f"Rango de pool no valido en {net}")
        if a not in net or b not in net:
            raise DhcpInvalido(f"El pool {desde}-{hasta} se sale de la red {net}")
        if b < a:
            raise DhcpInvalido(f"El pool de {net} esta al reves ({desde} > {hasta})")
        if gw and ipaddress.ip_address(gw) >= a and ipaddress.ip_address(gw) <= b:
            raise DhcpInvalido(f"El gateway {gw} esta dentro del pool {desde}-{hasta}")
        out["pool_from"], out["pool_to"] = str(a), str(b)
    return out


def generar(acs_url: str, redes: list[dict], routeros: str = "7",
            codificacion: str = "tlv", incluir_125: bool = True) -> dict:
    """Devuelve {script, avisos} listo para pegar en el MikroTik.

    codificacion: 'tlv' (estandar), 'plana' (equipos que no entienden TLV) o
    'ambas' (solo v7: TLV a quien se anuncia dslforum.org, plana al resto)."""
    url = validar_url(acs_url)
    if routeros not in ("6", "7"):
        raise DhcpInvalido("Version de RouterOS no soportada (usa 6 o 7)")
    if codificacion not in ("tlv", "plana", "ambas"):
        raise DhcpInvalido("Codificacion no soportada (tlv, plana o ambas)")
    if not redes:
        raise DhcpInvalido("Indica al menos una red donde entregar la opcion")

    avisos = []
    if codificacion == "ambas" and routeros == "6":
        raise DhcpInvalido(
            "RouterOS 6 no tiene matchers, asi que no puede dar TLV a unos equipos y cadena "
            "plana a otros: elige una de las dos codificaciones.")
    redes_ok, vistas = [], set()
    for r in redes:
        v = validar_red(r)
        if v["cidr"] in vistas:
            raise DhcpInvalido(f"La red {v['cidr']} esta repetida")
        vistas.add(v["cidr"])
        redes_ok.append(v)

    tlv, plana, o125 = option43_tlv(url), url, option125(url)
    L = [f"# {MARCA}: entregar el ACS por DHCP (Option 43) - generado {date.today().isoformat()}",
         f"# ACS: {url}",
         f"# RouterOS {routeros} | codificacion: {codificacion}",
         "# Pegalo en una terminal del router. Al final hay un bloque para deshacerlo.",
         ""]

    # --- opciones
    L.append("/ip dhcp-server option")
    if codificacion in ("tlv", "ambas"):
        L.append(f'add code=43 name={MARCA}-tlv value={tlv} comment="{MARCA} (subopcion 1 = URL del ACS)"')
    if codificacion in ("plana", "ambas"):
        L.append(f"add code=43 name={MARCA}-plana value=\"'{plana}'\" comment=\"{MARCA} (URL sin TLV)\"")
    if incluir_125:
        L.append(f'add code=125 name={MARCA}-125 value={o125} comment="{MARCA} (enterprise 3561)"')

    # --- conjuntos
    L.append("")
    L.append("/ip dhcp-server option sets")
    extra = f",{MARCA}-125" if incluir_125 else ""
    if codificacion in ("tlv", "ambas"):
        L.append(f'add name={MARCA}-tlv options={MARCA}-tlv{extra} comment="{MARCA}"')
    if codificacion in ("plana", "ambas"):
        L.append(f'add name={MARCA}-plana options={MARCA}-plana{extra} comment="{MARCA}"')
    set_por_defecto = f"{MARCA}-plana" if codificacion in ("plana", "ambas") else f"{MARCA}-tlv"

    # --- matcher (solo v7): TLV para quien se anuncia como dslforum.org
    if codificacion == "ambas":
        L += ["", "# El CPE que se anuncia como dslforum.org entiende TLV; al resto se le da la URL plana.",
              "/ip dhcp-server matcher",
              f'add name={MARCA}-dslforum code=60 matching-type=substring value="dslforum.org" '
              f'option-set={MARCA}-tlv server=all comment="{MARCA}"']

    # --- asignacion a las redes que YA existen (este script no crea redes)
    L += ["", "# Se asigna a redes existentes; el script no crea ninguna red nueva.",
          "/ip dhcp-server network"]
    for r in redes_ok:
        L.append(f'set [find address="{r["cidr"]}"] dhcp-option-set={set_por_defecto}')
        if r.get("comment"):
            L[-1] += f'   ;# {r["comment"]}'

    # --- verificacion
    L += ["", "# --- Comprobar ---------------------------------------------------------",
          f'# /ip dhcp-server option print where name~"{MARCA}"',
          "# /ip dhcp-server network print detail",
          "# /ip dhcp-server lease print detail    (mira la opcion 60 que anuncia cada CPE)",
          "",
          "# --- Deshacer (borra solo lo que anadio este script) -------------------",
          "# /ip dhcp-server network"]
    for r in redes_ok:
        L.append(f'#   set [find address="{r["cidr"]}"] dhcp-option-set=""')
    if codificacion == "ambas":
        L.append(f'# /ip dhcp-server matcher remove [find comment="{MARCA}"]')
    L += [f'# /ip dhcp-server option sets remove [find comment="{MARCA}"]',
          f'# /ip dhcp-server option remove [find comment~"{MARCA}"]']

    # --- avisos
    if routeros == "6" and codificacion == "tlv":
        avisos.append("En RouterOS 6 no hay matchers: los equipos que no entiendan TLV se quedaran "
                      "sin recibir la URL. Si tienes parque mixto, valora la codificacion plana.")
    if codificacion == "plana":
        avisos.append("La cadena plana no es lo que dice el estandar: los equipos que siguen TR-069 "
                      "al pie de la letra pueden ignorarla.")
    if incluir_125:
        avisos.append("Se incluye tambien la opcion 125; los equipos que no la piden simplemente la ignoran.")
    avisos.append("El script asigna el conjunto a redes que ya existen: si alguna no existe, ese "
                  "'set' no hara nada (compruebalo con el print de verificacion).")
    if url.startswith("https://"):
        avisos.append("Muchos CPE no validan TLS o no soportan HTTPS en el ACS; si no aparecen, prueba con http://.")

    return {"script": "\n".join(L) + "\n", "avisos": avisos,
            "valores": {"option43_tlv": tlv, "option43_plana": plana, "option125": o125}}
