"""Tutorial de DHCP (Option 43) en MikroTik, dentro de Ajustes.

El riesgo de un tutorial con comandos pegados a mano es que envejece en silencio:
el generador cambia una longitud o un nombre de option-set, el tutorial se queda
con los de antes, y alguien pega en un router produccion algo que ya no es lo que
el panel produce. Ya existe la prueba equivalente para `DEPLOY.md`
(`tests/test_dhcp_tr069.py::test_la_documentacion_no_se_desincroniza_del_generador`);
esta es la del panel.

La comprobacion no compara contra valores escritos aqui: **saca del propio HTML**
la URL del ACS, las redes y la codificacion del ejemplo, vuelve a generar el
script con `app.dhcp_tr069.generar()` y exige que coincida linea a linea. Asi el
tutorial puede usar los valores de ejemplo que quiera, pero no puede documentar
un comando que el codigo no genera.
"""
import html as htmlmod
import re
from pathlib import Path

import pytest

from app.dhcp_tr069 import generar

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = RAIZ / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")
DEPLOY = (RAIZ / "DEPLOY.md").read_text(encoding="utf-8")

AJUSTES = re.search(r'<section id="settings-page".*?</section>', HTML, re.S)


def _bloques_js():
    """app.js partido en bloques de primer nivel (ver test_propuesta_ia_modal.py)."""
    lineas = JS.splitlines()
    anclas = [i for i, l in enumerate(lineas) if re.match(r"[A-Za-z$_]", l or "")]
    for n, inicio in enumerate(anclas):
        fin = anclas[n + 1] if n + 1 < len(anclas) else len(lineas)
        yield inicio + 1, "\n".join(lineas[inicio:fin])


def _seccion_ajustes():
    assert AJUSTES, "no se encuentra la seccion #settings-page en index.html"
    return AJUSTES.group(0)


def _etiqueta(atributo, valor, dentro):
    m = re.search(rf'<[^>]*{atributo}="{valor}"[^>]*>', dentro)
    assert m, f'falta la etiqueta con {atributo}="{valor}" en Ajustes'
    return m.group(0)


def _panel_dhcp():
    """El HTML del panel del tutorial: de `id="tuto-dhcp"` al siguiente panel."""
    seccion = _seccion_ajustes()
    i = seccion.find('id="tuto-dhcp"')
    assert i != -1, 'falta el panel con id="tuto-dhcp" dentro de Ajustes'
    resto = seccion[i:]
    corte = re.search(r'data-spanel="', resto)
    return resto[:corte.start()] if corte else resto


def _texto(marca):
    """Texto plano de un trozo de HTML (sin etiquetas y sin entidades)."""
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", marca))


def _bloques_de_comandos(marca):
    """Las lineas de comando de los <pre> del tutorial, ya sin etiquetas."""
    lineas = []
    for pre in re.findall(r"<pre[^>]*>(.*?)</pre>", marca, re.S):
        lineas += [l.rstrip() for l in _texto(pre).splitlines()]
    return lineas


def _comandos(lineas):
    return [l.strip() for l in lineas if l.strip().startswith(("add ", "set "))]


def _deducir_ejemplo(lineas):
    """Que valores usa el ejemplo del tutorial, sacados del propio ejemplo.

    La URL NO se deduce del hexadecimal a proposito: si se decodificara el hex
    para volver a codificarlo, cambiar un byte del hex daria otra URL y el
    resultado volveria a cuadrar solo. Se toma del comentario `# ACS:` que emite
    el generador, del valor de la cadena plana, o de la URL que aparezca en el
    texto del tutorial."""
    texto = "\n".join(lineas)
    cmds = _comandos(lineas)
    url = None
    m = re.search(r"^#\s*ACS:\s*(\S+)", texto, re.M)
    if m:
        url = m.group(1)
    if not url:
        m = re.search(r"""value="'(https?://[^']+)'""", texto)
        if m:
            url = m.group(1)
    if not url:
        m = re.search(r"https?://[\w.\-:]+/?", _texto(_panel_dhcp()))
        if m:
            url = m.group(0)
    redes = []
    for c in cmds:
        m = re.search(r'set \[find address="([^"]+)"\]', c)
        if m and m.group(1) not in redes:
            redes.append(m.group(1))
    tlv = any(re.search(r"add code=43 name=\S*-tlv\b", c) for c in cmds)
    plana = any(re.search(r"add code=43 name=\S*-plana\b", c) for c in cmds)
    cod = "ambas" if tlv and plana else "plana" if plana else "tlv"
    return {"url": url, "redes": redes, "codificacion": cod,
            "incluir_125": any("code=125" in c for c in cmds),
            # el matcher solo existe en v7; con una sola codificacion la version
            # no cambia ni una linea del script, asi que 7 sirve siempre
            "routeros": "7"}


def _esperado(ej):
    r = generar(ej["url"], [{"cidr": c} for c in ej["redes"]], routeros=ej["routeros"],
                codificacion=ej["codificacion"], incluir_125=ej["incluir_125"])
    lineas = [l.split("   ;#")[0] for l in r["script"].splitlines()]
    obligatorias = [l for l in lineas if l.startswith(("add ", "set "))]
    # el bloque de deshacer va comentado en el script; un tutorial puede
    # presentarlo como comandos sueltos, asi que tambien vale
    permitidas = {l.lstrip("#").strip() for l in lineas} | set(obligatorias)
    return obligatorias, permitidas


# ---------- 5. la subpestana, el panel y el boton ----------

def test_ajustes_tiene_la_subpestana_del_tutorial():
    seccion = _seccion_ajustes()
    assert 'data-ssub="dhcp"' in seccion, "falta la subpestana data-ssub=\"dhcp\" en Ajustes"
    assert 'data-spanel="dhcp"' in seccion, "falta el panel data-spanel=\"dhcp\" en Ajustes"
    assert 'id="tuto-dhcp"' in _panel_dhcp()


def test_el_tutorial_es_solo_para_admin():
    """Lleva la URL del ACS: es informacion de servidor, como Conexion al ACS."""
    seccion = _seccion_ajustes()
    for atributo in ("data-ssub", "data-spanel"):
        etiqueta = _etiqueta(atributo, "dhcp", seccion)
        assert "admin-only" in etiqueta, f"{atributo}=dhcp deberia ser admin-only"
    assert "hidden" in _etiqueta("data-ssub", "dhcp", seccion), \
        "la subpestana debe nacer oculta, como acs e ia: abrirAjustes() la muestra al admin"


def test_el_manejador_de_subpestanas_de_ajustes_lo_alcanza():
    """ajustesSub() recorre `#settings-tabs .subtab` y `#settings-page [data-spanel]`:
    si el panel o la pestana quedan fuera de esos contenedores, pulsarla no hace nada."""
    tabs = re.search(r'<div class="subtabs" id="settings-tabs">(.*?)</div>',
                     _seccion_ajustes(), re.S)
    assert tabs and 'data-ssub="dhcp"' in tabs.group(1), \
        "la subpestana tiene que estar dentro de #settings-tabs"
    assert 'data-spanel="dhcp"' in _seccion_ajustes(), \
        "el panel tiene que estar dentro de #settings-page"
    assert '$$("#settings-page [data-spanel]")' in JS and '$$("#settings-tabs .subtab")' in JS


def test_hay_un_boton_para_copiar_los_comandos():
    assert HTML.count('id="tuto-dhcp-copiar"') == 1
    bloque = next((t for _n, t in _bloques_js() if "#tuto-dhcp-copiar" in t), None)
    assert bloque, "app.js no engancha nada a #tuto-dhcp-copiar"
    assert "clipboard" in bloque, "el boton deberia copiar al portapapeles"
    assert "tuto-dhcp" in bloque, "y copiar el bloque del tutorial, no otra cosa"


# ---------- 6. el tutorial no puede adelantarse al generador ----------

def test_el_tutorial_trae_comandos_de_verdad():
    cmds = _comandos(_bloques_de_comandos(_panel_dhcp()))
    assert len(cmds) >= 4, \
        f"el tutorial deberia traer el ejemplo completo (options, sets y el set de la red); hay {len(cmds)}"
    assert any(c.startswith("set [find address=") for c in cmds), \
        "falta la asignacion a la red, que es lo unico que toca la configuracion existente"


def test_el_tutorial_no_documenta_comandos_que_el_codigo_no_genera():
    """Cada `add`/`set` del tutorial tiene que salir de app/dhcp_tr069.py.

    Cambiar un byte del hexadecimal, el nombre de un option-set o el numero de
    enterprise hace fallar esta prueba: es justo lo que se quiere, porque nadie
    revisa un tutorial cuando toca el generador."""
    lineas = _bloques_de_comandos(_panel_dhcp())
    ej = _deducir_ejemplo(lineas)
    assert ej["url"], "no se encuentra la URL del ACS del ejemplo en el tutorial"
    assert ej["redes"], "el ejemplo tiene que asignar el conjunto a alguna red"
    obligatorias, permitidas = _esperado(ej)
    cmds = _comandos(lineas)
    sobran = [c for c in cmds if c not in permitidas]
    assert not sobran, ("el tutorial documenta comandos que el generador no produce "
                        "para " + ej["url"] + ":\n  " + "\n  ".join(sobran))
    faltan = [c for c in obligatorias if c not in cmds]
    assert not faltan, ("el generador produce comandos que el tutorial no documenta:\n  "
                        + "\n  ".join(faltan))


def test_el_tutorial_y_deploy_md_cuentan_lo_mismo():
    """Las dos copias del ejemplo (panel y DEPLOY.md) no pueden divergir.

    Solo se comparan si usan los mismos valores; si el panel decide usar otro
    ejemplo, cada uno se valida por separado contra el generador."""
    lineas = _bloques_de_comandos(_panel_dhcp())
    ej = _deducir_ejemplo(lineas)
    bloques = re.findall(r"```rsc\n(.*?)```", DEPLOY, re.S)
    doc = _comandos([l for b in bloques for l in b.splitlines()])
    if not ej["url"] or ej["url"] not in DEPLOY:
        pytest.skip(f"el panel usa otro ejemplo ({ej['url']}) que DEPLOY.md")
    panel = _comandos(lineas)
    assert doc == panel, "el mismo ejemplo con comandos distintos en DEPLOY.md y en el panel"


# ---------- 7. el aviso que cuesta una tarde ----------

def _avisos_del_limite(marca):
    """Los parrafos que hablan de la opcion 43 con el cliente TR-069 apagado.

    Se busca por parrafo y no en todo el panel a proposito: "7547" aparece
    tambien en el ejemplo de la URL del ACS y "apagado" en el comando de
    comprobacion del puerto, asi que buscar las palabras por separado en el
    panel entero seguiria pasando aunque se borrase el aviso."""
    return [t for t in (_texto(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", marca, re.S))
            if re.search(r"apagad|desactivad|no est\w* corriendo", t) and "43" in t]


def test_el_tutorial_avisa_de_que_el_dhcp_no_levanta_un_tr069_apagado():
    """El error de diagnostico mas caro de esta flota: la opcion 43 solo dice
    *a donde llamar* a un cliente TR-069 que ya esta corriendo. Con el cliente
    apagado (tipico en modo AP) no hay ajuste de DHCP que lo levante, y se puede
    perder una tarde en el router; la senal rapida es el puerto 7547 cerrado."""
    avisos = _avisos_del_limite(_panel_dhcp())
    assert avisos, ("el tutorial no avisa de que con el TR-069 apagado la opcion 43 "
                    "no sirve de nada")
    aviso = "\n".join(avisos)
    assert "modo AP" in aviso, "el aviso deberia decir donde pasa esto: el modo AP"
    assert "7547" in aviso, \
        "y dar la comprobacion rapida: el puerto 7547 cerrado = cliente TR-069 apagado"


def test_el_generador_de_aprovisionamiento_sigue_avisando_de_lo_mismo():
    """El aviso vive en dos sitios a proposito: quien genera el script tambien
    tiene que leerlo, no solo quien abre el tutorial."""
    panel = re.search(r'<div data-ppanel="dhcp".*?\n      </div>', HTML, re.S)
    assert panel, "no se encuentra el panel de Aprovisionamiento -> DHCP"
    avisos = _avisos_del_limite(panel.group(0))
    assert avisos, "el generador del script tambien tiene que llevar el aviso"
    aviso = "\n".join(avisos)
    assert "modo AP" in aviso and "7547" in aviso
