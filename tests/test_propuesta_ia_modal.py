"""La propuesta de mapeo por IA, dentro de un modal y no en dialogos del navegador.

Antes se pedia el id del equipo con un `prompt()` (habia que ir a la lista de
equipos, copiarlo y pegarlo) y se preguntaba por cada sugerencia con un
`confirm()`: si la IA devolvia seis rutas, eran seis dialogos seguidos, sin ver
el conjunto y sin poder descartar una sola.

Ahora es un `<dialog id="ia-modal">` con el equipo elegido de una lista y las
sugerencias con casilla. Estas pruebas leen `app/static/*` como texto, igual que
las demas pruebas de panel del repo: comprueban el contrato de ids y que el
flujo ya no depende de dialogos nativos.

Ojo con la regla de `prompt`/`confirm`: **solo** se prohiben en el flujo de la
IA. En el panel quedan varios usos legitimos —el `prompt()` de la contrasena
nueva de un usuario y el `confirm()` de su borrado (manejador de `[data-uact]`),
y los avisos antes de aplicar cambios a un equipo—, asi que la prueba acota la
prohibicion a los bloques de codigo de la IA en vez de vetar la palabra en todo
app.js, y hay otra prueba que vigila que los de usuarios sigan existiendo.
"""
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")

# El contrato: id -> etiqueta que tiene que llevarlo ("" = cualquiera).
CONTRATO = {
    "ia-modal": "dialog",        # dialogo nativo: Esc, foco atrapado y fondo, gratis
    "ia-modal-titulo": "",       # de que modelo se esta proponiendo el mapeo
    "ia-dev": "select",          # el equipo, elegido de los que ya estan cargados
    "ia-proponer": "button",     # lanza la consulta al proveedor
    "ia-estado": "",             # "Consultando...", errores, cuantas sugerencias
    "ia-sugerencias": "",        # la lista con casillas
    "ia-guardar": "button",      # guarda solo lo marcado
    "ia-cerrar": "button",
}

# Con que reconocemos que un trozo de app.js pertenece al flujo de la IA.
MARCAS_IA = ("abrirPropuestaIa", "/homologacion/proponer", "/homologacion/confirmar",
             "#ia-modal", "#ia-dev", "#ia-proponer", "#ia-sugerencias", "#ia-guardar",
             "#ia-estado", "#ia-cerrar", "data-ia")


def _bloques():
    """app.js partido en sus bloques de primer nivel.

    En este fichero todo lo anidado va indentado, asi que una linea que empieza
    en la columna 0 por letra, `$` o `_` abre un bloque nuevo (funcion,
    manejador o constante). Es la forma barata de saber a que manejador
    pertenece una linea sin escribir un parser de JavaScript."""
    lineas = JS.splitlines()
    anclas = [i for i, l in enumerate(lineas) if re.match(r"[A-Za-z$_]", l or "")]
    for n, inicio in enumerate(anclas):
        fin = anclas[n + 1] if n + 1 < len(anclas) else len(lineas)
        yield inicio + 1, "\n".join(lineas[inicio:fin])


def _codigo(marca):
    """Todos los bloques de primer nivel que mencionan `marca`, juntos.

    Se devuelven todos y no el primero porque el flujo esta repartido a
    proposito (abrir el modal, pintar las sugerencias, proponer, guardar): lo
    que se comprueba es que la pieza este en alguno de ellos, no en cual."""
    trozos = [t for _linea, t in _bloques() if marca in t]
    if not trozos:
        pytest.fail(f"app.js no tiene ningun bloque con {marca!r}: "
                    "el flujo de la propuesta por IA no esta donde dice el contrato")
    return "\n".join(trozos)


def _etiqueta(id_):
    m = re.search(rf'<(\w+)[^>]*\bid="{re.escape(id_)}"', HTML)
    return m.group(1) if m else None


# ---------- 1. los ids del contrato, y el cruce JS <-> HTML ----------

@pytest.mark.parametrize("id_", list(CONTRATO))
def test_el_html_trae_cada_id_del_contrato(id_):
    veces = HTML.count('id="' + id_ + '"')
    assert veces == 1, f"#{id_} tiene que aparecer una vez exacta en index.html (hay {veces})"
    esperada = CONTRATO[id_]
    if esperada:
        assert _etiqueta(id_) == esperada, f"#{id_} deberia ser un <{esperada}>"


def test_el_javascript_no_lee_ningun_id_que_no_exista():
    """Un `$("#…")` que no existe deja el script entero muerto al cargar.

    No es hipotetico: `$(...)` devuelve null y la primera llamada a
    `.addEventListener` en el nivel superior aborta la carga de app.js, asi que
    el panel se queda en blanco y no solo la funcion que falla."""
    ids_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', HTML))
    faltan = sorted({u for u in re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', JS)
                     if u not in ids_html})
    assert not faltan, f"el JS lee ids que no estan en el HTML: {faltan}"


@pytest.mark.parametrize("id_", list(CONTRATO))
def test_ningun_id_del_modal_se_queda_suelto(id_):
    """El otro lado del cruce: un id en el HTML que nadie usa es un boton muerto.

    Excepcion: un boton con `data-close` lo cierra el manejador generico de
    modales, asi que no tiene por que aparecer por su id."""
    etiqueta = re.search(r'<[^>]*\bid="' + re.escape(id_) + r'"[^>]*>', HTML)
    if etiqueta and "data-close" in etiqueta.group(0):
        return
    assert f"#{id_}" in JS, f"#{id_} esta en el HTML y app.js no lo usa para nada"


def test_el_modal_usa_el_dialogo_nativo_como_los_demas():
    marca = re.search(r'<dialog id="ia-modal"[^>]*>', HTML)
    assert marca, 'falta el <dialog id="ia-modal">'
    clases = re.search(r'class="([^"]*)"', marca.group(0))
    assert clases and "modal" in clases.group(1).split(), \
        'el dialogo tiene que llevar class="modal": el cierre por Esc, el clic en el ' \
        "fondo y el ::backdrop se enganchan a dialog.modal"
    abre = _codigo("abrirPropuestaIa")
    assert "abrirModal(" in abre or "showModal" in abre, \
        "abrirPropuestaIa deberia abrir el dialogo con abrirModal()/showModal()"


def test_el_modal_se_puede_cerrar_sin_guardar():
    """Vale por el manejador generico `[data-close]`, como los demas modales."""
    boton = re.search(r'<[^>]*id="ia-cerrar"[^>]*>', HTML)
    assert boton, 'falta el boton id="ia-cerrar"'
    if "data-close" in boton.group(0):
        assert "[data-close]" in JS and "cerrarModal" in _codigo("[data-close]")
    else:
        assert "cerrarModal" in _codigo("#ia-cerrar"), \
            "o data-close en el boton, o su propio manejador con cerrarModal()"


# ---------- 2. ni prompt() ni confirm() en el flujo de la IA ----------

def test_el_flujo_de_la_ia_ya_no_usa_dialogos_del_navegador():
    """Ningun bloque de la IA puede llamar a `prompt(` ni a `confirm(`.

    Se acota a los bloques del flujo de la IA a proposito: en app.js quedan
    usos legitimos (la contrasena nueva de un usuario, su borrado, los avisos
    antes de tocar un equipo) y vetar la palabra entera seria prohibir de mas."""
    culpables = []
    for linea, texto in _bloques():
        if not any(m in texto for m in MARCAS_IA):
            continue
        for uso in re.finditer(r"\b(prompt|confirm)\(", texto):
            n = linea + texto[:uso.start()].count("\n")
            culpables.append(f"app.js:{n}: {uso.group(1)}( en el flujo de la IA")
    assert not culpables, "\n".join(culpables)


def test_los_dialogos_nativos_legitimos_siguen_en_su_sitio():
    """La prueba de arriba no vale nada si alguien borra tambien estos.

    El reseteo de contrasena (`prompt`) y el borrado de usuario (`confirm`) son
    acciones de admin puntuales y siguen siendo dialogos nativos a proposito."""
    bloque = _codigo("[data-uact]")
    assert "prompt(" in bloque, "el reseteo de contrasena de un usuario usa prompt() a proposito"
    assert "confirm(" in bloque, "el borrado de usuario pide confirmacion a proposito"
    assert not [m for m in MARCAS_IA if m in bloque], \
        "el manejador de usuarios no deberia mezclarse con el flujo de la IA"


# ---------- 3. el equipo sale de los que ya estan cargados ----------

def test_el_modal_elige_el_equipo_de_los_que_ya_hay_cargados():
    """Antes habia que ir a la lista de equipos y copiar el id a mano."""
    bloque = _codigo("abrirPropuestaIa")
    assert "S.devices" in bloque, \
        "abrirPropuestaIa deberia poblar el <select> con S.devices, no pedir el id"
    assert "#ia-dev" in bloque, "abrirPropuestaIa deberia rellenar #ia-dev"
    assert any(x in bloque for x in ("<option", 'createElement("option")', "new Option")), \
        "el <select> se rellena con las opciones de los equipos de ese modelo"


def test_el_boton_de_la_tarjeta_abre_el_modal_con_la_clave_del_modelo():
    assert re.search(r"function abrirPropuestaIa\(\s*\w+", JS), \
        "falta `function abrirPropuestaIa(key)` en app.js"
    bloque = _codigo("data-ia")
    assert "abrirPropuestaIa(" in bloque, \
        "la tarjeta del perfil deberia llamar a abrirPropuestaIa(key)"
    assert "dataset.ia" in bloque, "y pasarle la clave del modelo de data-ia"


def test_se_propone_para_el_equipo_elegido_en_el_modal():
    bloque = _codigo("/homologacion/proponer")
    assert "#ia-dev" in bloque, \
        "la peticion a /homologacion/proponer deberia usar el equipo elegido en #ia-dev"
    assert "device_id" in bloque
    assert "#ia-estado" in bloque, "el modal tiene que decir que esta consultando"


# ---------- 4. se guarda solo lo que esta marcado ----------

def test_las_sugerencias_llevan_casilla():
    bloque = _codigo("#ia-sugerencias")
    assert 'type="checkbox"' in bloque, \
        "cada sugerencia necesita su casilla: la gracia es poder descartar una sola"


def test_solo_se_guardan_las_sugerencias_marcadas():
    bloque = _codigo("#ia-guardar")
    assert ":checked" in bloque, \
        "el guardado deberia recorrer solo las casillas marcadas (:checked)"
    assert "/homologacion/confirmar" in bloque, \
        "guardar tiene que llamar a /homologacion/confirmar desde el modal"


def test_nada_se_confirma_fuera_de_las_casillas_marcadas():
    """Ninguna llamada a /homologacion/confirmar puede vivir lejos del `:checked`.

    Proponer consulta al proveedor; aplicar es otro boton. Si el propio
    `proponer` confirmara, la IA escribiria sola en el catalogo de perfiles."""
    for linea, texto in _bloques():
        if "/homologacion/confirmar" not in texto:
            continue
        assert ":checked" in texto, \
            f"app.js:{linea}: se confirma un mapeo sin filtrar por las casillas marcadas"


def test_el_modal_avisa_si_el_equipo_elegido_es_de_otro_firmware():
    """El desplegable ignora el firmware (otro FW sigue siendo el mismo modelo),
    pero el perfil se guarda por fabricante|clase|modelo|FIRMWARE: elegir un
    equipo de otro FW corrige un perfil distinto del que se pulso. Hay que
    decirlo, y el FW tiene que verse en cada opcion."""
    assert "FW ${d.firmware" in JS                 # el firmware, en la etiqueta
    assert "function iaAvisoFirmware()" in JS
    assert "iaPerfilPulsado" in JS
    # el aviso se recalcula al cambiar de equipo, no solo al abrir
    assert '$("#ia-dev").addEventListener("change"' in JS
