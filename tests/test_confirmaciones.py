"""Ninguna pregunta del panel la hace el navegador: todas son modales.

El usuario vio un dialogo nativo del navegador (ese recuadro gris pegado arriba,
con el dominio escrito y sin los colores del panel) y pidio que todas las
preguntas fueran modales del panel. Eran diez sitios: nueve `confirm()` y el
`prompt()` de la contrasena nueva de un usuario --que ademas la ensenaba en
pantalla en texto plano, porque el `prompt()` nativo no tiene campo de
contrasena--.

El contrato nuevo son dos dialogos y dos funciones:

* `<dialog id="confirm-modal">` y `confirmar({titulo, texto, ok, peligro})`,
  que devuelve una promesa `true`/`false`.
* `<dialog id="pedir-modal">` y `pedirTexto({titulo, texto, tipo, placeholder,
  minimo})`, que devuelve el texto escrito o `null`.

Estas pruebas leen `app/static/*` como texto, igual que las demas pruebas de
panel del repo, y reutilizan de `test_propuesta_ia_modal.py` la tecnica de
partir `app.js` en bloques de primer nivel para saber a que manejador pertenece
cada linea sin escribir un parser de JavaScript.

Lo que vigilan, en orden de importancia:

1. Que no vuelva ningun dialogo nativo a `app.js` (el corazon del fichero).
2. Que sustituir un `confirm()` no haya dejado la accion destructiva
   ejecutandose sin preguntar nada, que es la forma facil de hacerlo mal.
3. Que la contrasena se pida en un campo `type="password"`.
4. Que las dos funciones resuelvan siempre su promesa, tambien cuando el dialogo
   se cierra con Esc: si no, la interfaz se queda muerta esperando.
"""
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- el contrato

# id -> etiqueta que tiene que llevarlo ("" = cualquiera).
CONFIRM = {
    "confirm-modal": "dialog",       # dialogo nativo: Esc, foco atrapado y ::backdrop
    "confirm-modal-titulo": "",
    "confirm-modal-texto": "",
    "confirm-modal-ok": "button",
    "confirm-modal-cancelar": "button",
}
PEDIR = {
    "pedir-modal": "dialog",
    "pedir-modal-titulo": "",
    "pedir-modal-texto": "",
    "pedir-modal-input": "input",    # aqui se escribe; con type segun `tipo`
    "pedir-modal-ok": "button",
    "pedir-modal-cancelar": "button",
}
CONTRATO = {**CONFIRM, **PEDIR}


class Sitio:
    """Un sitio del panel que tenia un dialogo nativo y ahora tiene que preguntar.

    `marca` localiza el trozo de `app.js`: o un bloque de primer nivel, o un
    metodo del objeto `actions` (los de equipo estan todos dentro del mismo
    bloque, asi que hay que cortar mas fino). `llamada` es la peticion que la
    pregunta tiene que estar bloqueando, y `peligro` si la accion destruye algo
    y el boton debe salir en rojo.
    """

    def __init__(self, marca, llamada, peligro=False, metodo=None, desde=None):
        self.marca, self.llamada, self.peligro = marca, llamada, peligro
        self.metodo, self.desde = metodo, desde


ACCIONES = {
    # --- destructivas: piden confirmacion y el boton va marcado como peligro ---
    "restaurar el respaldo en el equipo": Sitio(
        marca="function backupRestore", llamada="/restore", peligro=True),
    "reiniciar el equipo": Sitio(
        metodo="reboot", marca="reboot", llamada="/reboot", peligro=True),
    "mandar firmware a un equipo": Sitio(
        metodo="fw", marca="fw", llamada="/firmware", peligro=True),
    "mandar firmware a varios equipos a la vez": Sitio(
        marca="/firmware/push", llamada='"/firmware/push"', peligro=True),
    "borrar un usuario": Sitio(
        marca="[data-uact]", desde='act === "del"', llamada='"DELETE"', peligro=True),
    "borrar un archivo de firmware": Sitio(
        marca="[data-fwdel]", llamada='"DELETE"', peligro=True),
    "borrar la clave del proveedor de IA": Sitio(
        marca="#llm-clear", llamada='api("/settings/llm"', peligro=True),
    # --- avisos: se pregunta, pero no se destruye nada ---
    "aplicar la WAN": Sitio(
        metodo="wan", marca="wan", llamada="/wan"),
    "abrir la administracion del equipo por WAN": Sitio(
        metodo="access", marca="access", llamada="/access"),
}

# El unico sitio que pedia texto: la contrasena nueva de un usuario.
PEDIR_TEXTO = Sitio(marca="[data-uact]", desde='act === "pass"', llamada="/password")


# --------------------------------------------------------------- herramientas

def _bloques(js=None):
    """`app.js` partido en sus bloques de primer nivel.

    Igual que en `test_propuesta_ia_modal.py`: en este fichero todo lo anidado
    va indentado, asi que una linea que empieza en la columna 0 por letra, `$` o
    `_` abre un bloque nuevo (funcion, manejador o constante)."""
    lineas = (js if js is not None else JS).splitlines()
    anclas = [i for i, l in enumerate(lineas) if re.match(r"[A-Za-z$_]", l or "")]
    for n, inicio in enumerate(anclas):
        fin = anclas[n + 1] if n + 1 < len(anclas) else len(lineas)
        yield inicio + 1, "\n".join(lineas[inicio:fin])


def _codigo(marca, js=None):
    """Todos los bloques de primer nivel que mencionan `marca`, juntos."""
    trozos = [t for _l, t in _bloques(js) if marca in t]
    if not trozos:
        pytest.fail(f"app.js no tiene ningun bloque con {marca!r}")
    return "\n".join(trozos)


def _metodo(nombre, js=None):
    """El cuerpo de un metodo del objeto `actions` (`async wan() {` … `},`).

    Las acciones de equipo (wan, access, fw, reboot…) son metodos de un mismo
    objeto de primer nivel: si se mirara el bloque entero, el `confirmar()` de
    una accion daria por buena a la de al lado."""
    js = JS if js is None else js
    m = re.search(r"\n  (?:async )?(?:\[\"%s\"\]|%s)\s*\(" % (re.escape(nombre),
                                                              re.escape(nombre)), js)
    if not m:
        pytest.fail(f"app.js ya no tiene el metodo {nombre}() del objeto actions")
    fin = re.search(r"\n  \}[,;]?\n", js[m.end():])
    return js[m.start():m.end() + (fin.start() if fin else len(js))]


def _trozo(sitio, js=None):
    """El codigo del sitio: su bloque o su metodo, recortado desde `desde`."""
    texto = (_metodo(sitio.metodo, js) if sitio.metodo
             else _codigo(sitio.marca, js))
    if sitio.desde:
        if sitio.desde not in texto:
            pytest.fail(f"no encuentro {sitio.desde!r} en el codigo de {sitio.marca!r}")
        texto = texto[texto.index(sitio.desde):]
    return texto


def _args(texto, pos):
    """Lo que hay entre los parentesis de la llamada que empieza en `pos`.

    Contando parentesis, para que un objeto con parentesis dentro (una plantilla,
    otra llamada) no corte el argumento a la mitad."""
    i = texto.index("(", pos)
    nivel, j = 0, i
    while j < len(texto):
        if texto[j] == "(":
            nivel += 1
        elif texto[j] == ")":
            nivel -= 1
            if nivel == 0:
                return texto[i + 1:j]
        j += 1
    return texto[i + 1:]


def _etiqueta(id_):
    m = re.search(rf'<(\w+)[^>]*\bid="{re.escape(id_)}"', HTML)
    return m.group(1) if m else None


# --- dejar en el texto solo el codigo, sin comentarios ni literales ---------

_ANTES_DE_REGEX = set("(,=:[!&|?{};+-*%~^<>") | {""}


def _solo_codigo(js):
    """`app.js` con comentarios y literales convertidos en espacios.

    Necesario para la prueba del punto 1: en este panel hay comentarios y textos
    que hablan de los `confirm()` que se quitaron, y una busqueda a pelo los
    contaria como reincidencias. Se respetan los saltos de linea y la longitud
    para que las posiciones sigan valiendo como numero de linea del fichero
    original.

    Los literales de expresion regular se saltan aparte: `/[&<>"']/g` existe de
    verdad en `app.js` y, si se tratara como codigo, su comilla abriria una
    cadena falsa que se comeria las lineas siguientes. Para distinguir `/regex/`
    de una division se mira el ultimo caracter significativo, que es el criterio
    que usan los propios tokenizadores de JavaScript."""
    fuera, i, n, previo = [], 0, len(js), ""
    def blanco(trozo):
        return "".join(c if c == "\n" else " " for c in trozo)

    while i < n:
        c, dos = js[i], js[i:i + 2]
        if dos == "//":
            j = js.find("\n", i)
            j = n if j < 0 else j
            fuera.append(blanco(js[i:j])); i = j; continue
        if dos == "/*":
            j = js.find("*/", i + 2)
            j = n if j < 0 else j + 2
            fuera.append(blanco(js[i:j])); i = j; continue
        if c in "\"'`":
            j = i + 1
            while j < n:
                if js[j] == "\\":
                    j += 2; continue
                if js[j] == c:
                    j += 1; break
                j += 1
            fuera.append(c + blanco(js[i + 1:j - 1]) + c if j - i >= 2 else blanco(js[i:j]))
            i = j; previo = c; continue
        if c == "/" and previo in _ANTES_DE_REGEX:
            j, clase = i + 1, False
            while j < n and js[j] != "\n":
                if js[j] == "\\":
                    j += 2; continue
                if js[j] == "[":
                    clase = True
                elif js[j] == "]":
                    clase = False
                elif js[j] == "/" and not clase:
                    j += 1; break
                j += 1
            fuera.append(blanco(js[i:j])); i = j; previo = "/"; continue
        fuera.append(c)
        if not c.isspace():
            previo = c
        i += 1
    return "".join(fuera)


CODIGO = _solo_codigo(JS)

# La llamada a un dialogo del navegador, y nada mas que eso.
#
#   (?<![.\w$])   no precedida de punto ni de letra/cifra/_/$: asi `confirmar(`
#                 no cuenta (contiene `confirm`, pero el `(` no viene pegado al
#                 final de `confirm`, con lo que el motor ni llega a probarlo),
#                 ni cuentan `reconfirm(`, `pedirTexto(` ni un metodo ajeno
#                 tipo `d.alert(`.
#   \s*\(         admite `confirm (` con espacio: sigue siendo una llamada.
#
# El `window.` va aparte a proposito: `window.confirm("…")` es exactamente el
# mismo dialogo del navegador, y la regla de "no precedida de punto" lo dejaria
# pasar. Se busca sobre CODIGO (sin comentarios ni literales), asi que un texto
# que mencione la palabra no dispara la prueba.
NATIVO = re.compile(r"(?:(?<=\bwindow\.)|(?<![.\w$]))(confirm|prompt|alert)\s*\(")


def _nativos(codigo):
    for m in NATIVO.finditer(codigo):
        antes = codigo[:m.start()]
        # la definicion propia (`function confirmar`) nunca puede casar, pero si
        # alguien llamara a una funcion suya `alert` hay que verlo igual
        if re.search(r"\bfunction\s+$", antes):
            continue
        yield antes.count("\n") + 1, m.group(1)


# ============================ 1. ni un dialogo nativo ========================

def test_no_queda_ningun_dialogo_nativo_en_el_javascript():
    """El corazon del fichero: si en seis meses alguien mete un `confirm()`, salta.

    Se busca sobre el codigo con los comentarios y los literales en blanco
    (`_solo_codigo`) y con una expresion que solo case la llamada nativa: ver el
    comentario de `NATIVO`. Los casos que hay que dejar pasar son `confirmar(`
    y `pedirTexto(`, que son justo las funciones que sustituyen a los dialogos,
    y cualquier frase que mencione la palabra; el que tiene que saltar es
    `confirm(`, `prompt(`, `alert(` o su version con `window.`"""
    culpables = [f"app.js:{linea}: {cual}( del navegador"
                 for linea, cual in _nativos(CODIGO)]
    assert not culpables, (
        "vuelve a haber dialogos del navegador; use confirmar()/pedirTexto():\n"
        + "\n".join(culpables))


def test_la_expresion_del_punto_1_no_se_come_lo_que_no_debe():
    """Prueba de la prueba: sin esto, un falso negativo pasaria inadvertido.

    Una expresion mal escrita puede quedarse verde para siempre (si `confirmar(`
    la despista y la relaja alguien) o roja para siempre (si cuenta los textos).
    Se comprueba con un trozo de JavaScript escrito a mano."""
    muestra = (
        'const t = "aqui habia un confirm() del navegador";  // y un prompt()\n'
        "if (!(await confirmar({titulo: t}))) return;\n"
        "const p = await pedirTexto({tipo: \"password\"});\n"
        'const limpio = s.replace(/[&<>"\']/g, esc);   /* alert() en comentario */\n'
        "if (!confirm(t)) return;\n"
        "window.alert(t);\n"
    )
    encontrados = sorted(_nativos(_solo_codigo(muestra)))
    assert encontrados == [(5, "confirm"), (6, "alert")], encontrados


def test_el_html_tampoco_llama_a_un_dialogo_nativo_desde_un_atributo():
    """Un `onclick="if(!confirm(…))"` en el HTML seria el mismo recuadro gris."""
    culpables = []
    for m in re.finditer(r"\bon\w+\s*=\s*\"([^\"]*)\"", HTML):
        for _l, cual in _nativos(m.group(1)):
            culpables.append(f"index.html: {cual}( en {m.group(0)[:60]}…")
    assert not culpables, "\n".join(culpables)


# ====================== 2. los ids de los dos modales ========================

@pytest.mark.parametrize("id_", list(CONTRATO))
def test_el_html_trae_cada_id_del_contrato(id_):
    veces = HTML.count('id="' + id_ + '"')
    assert veces == 1, f"#{id_} tiene que aparecer una vez exacta en index.html (hay {veces})"
    esperada = CONTRATO[id_]
    if esperada:
        assert _etiqueta(id_) == esperada, f"#{id_} deberia ser un <{esperada}>"


@pytest.mark.parametrize("id_modal", ["confirm-modal", "pedir-modal"])
def test_los_dos_son_dialogos_nativos_con_la_clase_de_los_demas(id_modal):
    """`<dialog class="modal">`: Esc, foco atrapado y ::backdrop, sin programar."""
    marca = re.search(rf'<dialog id="{id_modal}"[^>]*>', HTML)
    assert marca, f'falta el <dialog id="{id_modal}">'
    clases = re.search(r'class="([^"]*)"', marca.group(0))
    assert clases and "modal" in clases.group(1).split(), \
        f'#{id_modal} tiene que llevar class="modal": el cierre por Esc, el clic en ' \
        "el fondo y el ::backdrop se enganchan a dialog.modal"
    assert f'#{id_modal}"' in JS, f"app.js no abre #{id_modal} en ningun sitio"


def test_el_javascript_no_lee_ningun_id_de_los_modales_que_no_exista():
    """Sentido JS -> HTML. Un `$("#…")` que no existe deja el panel en blanco:
    `$(...)` devuelve null y el primer `.addEventListener` aborta app.js."""
    ids_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', HTML))
    usados = set(re.findall(r'#((?:confirm|pedir)-modal[A-Za-z0-9_-]*)', JS))
    faltan = sorted(u for u in usados if u not in ids_html)
    assert not faltan, f"el JS usa ids de modal que no estan en el HTML: {faltan}"


def test_ningun_id_de_los_modales_se_queda_suelto_en_el_html():
    """Sentido HTML -> JS. Un boton del modal que nadie escucha no hace nada.

    Excepcion: un boton con `data-close` lo cierra el manejador generico, y el
    `close` del dialogo es lo que resuelve la promesa, asi que no necesita que
    su id aparezca en el JS."""
    sueltos = []
    for id_ in re.findall(r'id="((?:confirm|pedir)-modal[A-Za-z0-9_-]*)"', HTML):
        etiqueta = re.search(r'<[^>]*\bid="' + re.escape(id_) + r'"[^>]*>', HTML)
        if etiqueta and "data-close" in etiqueta.group(0):
            continue
        if f"#{id_}" not in JS:
            sueltos.append(id_)
    assert not sueltos, f"estan en el HTML y app.js no los usa para nada: {sueltos}"


# ================= 3. la contrasena, nunca en texto plano ====================

def test_el_campo_del_modal_puede_ser_de_contrasena():
    """El `prompt()` nativo ensenaba la contrasena nueva en pantalla.

    Se admite que el `<input>` nazca como `password` en el HTML o que
    `pedirTexto()` le ponga el `type` que le pidan, pero una de las dos."""
    campo = re.search(r'<input[^>]*\bid="pedir-modal-input"[^>]*>', HTML)
    assert campo, 'falta el <input id="pedir-modal-input">'
    bloque = _codigo("function pedirTexto")
    pone_el_tipo = re.search(r'(#pedir-modal-input"\)|campo|input)\s*(\.type\s*=|'
                             r'\.setAttribute\("type")', bloque) or \
        re.search(r'\.type\s*=\s*\w*tipo', bloque)
    assert pone_el_tipo or 'type="password"' in campo.group(0), \
        "pedirTexto() tiene que aplicar el `tipo` al input (o el input nacer password)"
    if 'type="password"' not in campo.group(0):
        assert "tipo" in bloque, "pedirTexto() ignora el parametro `tipo` del contrato"


def test_la_contrasena_de_un_usuario_se_pide_en_un_campo_de_contrasena():
    """El sitio concreto que antes tenia el `prompt()`: tiene que pedir `password`."""
    texto = _trozo(PEDIR_TEXTO)
    llamada = re.search(r"(?<![.\w$])pedirTexto\s*\(", texto)
    assert llamada, "la contrasena nueva de un usuario ya no se pide con pedirTexto()"
    args = _args(texto, llamada.start())
    assert re.search(r"tipo\s*:\s*[\"']password[\"']", args), \
        'la contrasena se pide con tipo: "password"; en texto plano se ve en pantalla:\n' + args
    assert re.search(r"minimo\s*:\s*\d+", args) or "12" in texto, \
        "y con el minimo de 12 caracteres que valida el backend"


def test_ningun_sitio_pide_una_contrasena_en_texto_plano():
    """Cualquier llamada cuyo texto hable de contrasena tiene que pedir `password`."""
    malos = []
    for m in re.finditer(r"(?<![.\w$])pedirTexto\s*\(", CODIGO):
        args = _args(JS, m.start())          # sobre el JS real: aqui si hace falta el texto
        if not re.search(r"(?i)contrase|password|clave", args):
            continue
        if not re.search(r"tipo\s*:\s*[\"']password[\"']", args):
            malos.append(f"app.js:{CODIGO[:m.start()].count(chr(10)) + 1}: "
                         "pide una contrasena sin tipo: \"password\"")
    assert not malos, "\n".join(malos)


# ============ 4. las acciones siguen preguntando antes de disparar ===========

def _confirmacion(texto, nombre):
    """La llamada a `confirmar(` del trozo, con su posicion y sus argumentos."""
    m = re.search(r"(?<![.\w$])confirmar\s*\(", texto)
    if not m:
        pytest.fail(f"«{nombre}» ya no pregunta nada: no hay confirmar( en su codigo.\n"
                    "Sustituir un confirm() y quedarse sin confirmacion ninguna es el "
                    "error tipico de este cambio.")
    return m, _args(texto, m.start())


@pytest.mark.parametrize("nombre", list(ACCIONES))
def test_la_accion_pregunta_antes_de_hacer_nada(nombre):
    """Y la pregunta tiene que estar *delante* de la peticion, cortandola.

    No vale confirmar despues, ni confirmar sin mirar la respuesta: entre el
    `confirmar(` y la peticion tiene que haber un `return` (el `if (!(await
    confirmar(…))) return;` que sustituye al `if (!confirm(…)) return;`)."""
    sitio = ACCIONES[nombre]
    texto = _trozo(sitio)
    assert sitio.llamada in texto, \
        f"el codigo de «{nombre}» ya no contiene {sitio.llamada!r}: revisa la marca"
    pregunta, _args_ = _confirmacion(texto, nombre)
    accion = texto.index(sitio.llamada)
    assert pregunta.start() < accion, \
        f"«{nombre}» lanza {sitio.llamada!r} antes de preguntar"
    entre = texto[pregunta.start():accion]
    assert "return" in entre, \
        f"«{nombre}» pregunta y sigue igual: falta el `return` si se responde que no"


@pytest.mark.parametrize("nombre", [n for n, s in ACCIONES.items() if s.peligro])
def test_la_accion_destructiva_se_marca_como_peligrosa(nombre):
    """`peligro` es lo que pinta el boton en rojo: borrar no puede parecer guardar."""
    _m, args = _confirmacion(_trozo(ACCIONES[nombre]), nombre)
    assert "peligro" in args, \
        f"«{nombre}» destruye algo y su confirmar() no lleva `peligro`:\n{args}"


def test_confirmar_pinta_de_rojo_cuando_le_pasan_peligro():
    """Si `peligro` no llegara al boton, el parametro seria decorativo."""
    bloque = _codigo("function confirmar")
    assert "peligro" in bloque, "confirmar() ignora el parametro `peligro` del contrato"
    assert re.search(r"peligro[^\n]*(classList|className|danger|peligro\")", bloque) or \
        re.search(r"(classList|className)[^\n]*peligro", bloque), \
        "confirmar() recibe `peligro` y no lo usa para marcar el boton"


def test_toda_pregunta_se_espera_con_await():
    """Un `confirmar()` sin `await` devuelve una promesa, que siempre es verdadera.

    Es el fallo mas silencioso de este cambio: `if (!confirmar(…)) return;`
    compila, no avisa de nada y deja la accion disparandose siempre."""
    malos = []
    for nombre in ("confirmar", "pedirTexto"):
        for m in re.finditer(rf"(?<![.\w$]){nombre}\s*\(", CODIGO):
            antes = CODIGO[max(0, m.start() - 20):m.start()]
            if re.search(r"\bfunction\s+$", antes):
                continue                      # la definicion
            despues = CODIGO[m.start():m.start() + 400]
            if "await " in antes or ".then(" in despues:
                continue
            malos.append(f"app.js:{CODIGO[:m.start()].count(chr(10)) + 1}: "
                         f"{nombre}( sin await: la promesa es siempre verdadera")
    assert not malos, "\n".join(malos)


# Borrados que nunca preguntaron nada, ni antes ni ahora (no son regresiones de
# este cambio; se apuntan para que la red de abajo no los vuelva a contar y para
# que se vea que estan sin confirmacion a proposito).
SIN_PREGUNTA = {
    'async ["sched-clear"]': "quitar la programacion de reinicio: se vuelve a poner en dos clics",
    "[data-rule]": "borrar un rango de descubrimiento (candidato a confirmar algun dia)",
}


def test_no_quedan_acciones_destructivas_sin_vigilar():
    """Red de seguridad: una accion nueva que borre y no pregunte nada.

    Si aparece un `method: "DELETE"` en un trozo que no esta ni en ACCIONES ni en
    SIN_PREGUNTA, o hay que anadirlo aqui, o se le olvido la confirmacion."""
    # solo las marcas de bloque: las de metodo (`fw`, `wan`…) son palabras cortas
    # que aparecen por todo app.js y taparian un borrado de verdad
    vigilados = {s.marca for s in ACCIONES.values() if not s.metodo} | set(SIN_PREGUNTA)
    sospechosos = []
    for linea, texto in _bloques():
        if '"DELETE"' not in texto:
            continue
        # el objeto `actions`, metodo a metodo: si no, el confirmar() de una
        # accion daria por buena a la de al lado
        for trozo in re.split(r"\n  (?=async [\[\w])", texto):
            if '"DELETE"' not in trozo or any(m in trozo for m in vigilados):
                continue
            if not re.search(r"(?<![.\w$])confirmar\s*\(", trozo):
                sospechosos.append(
                    f"app.js:~{linea}: borra algo sin confirmar( y sin estar en la tabla "
                    f"ACCIONES ni en SIN_PREGUNTA de esta prueba:\n{trozo[:200]}")
    assert not sospechosos, "\n\n".join(sospechosos)


# ============ 5. las promesas se resuelven aunque se cierre con Esc ==========

def _promesa(nombre):
    """Como devuelve `nombre` su promesa: el nombre del `resolve` y donde lo guarda.

    Sirve para las dos formas razonables de escribir esto: enganchar el `close`
    dentro de la propia funcion (y llamar a `resolve` alli mismo) o guardar el
    `resolve` en una variable del modulo y resolver desde un manejador de primer
    nivel, que es como esta ahora."""
    bloque = _codigo(f"function {nombre}")
    m = re.search(r"new Promise\(\s*(?:async\s*)?\(?\s*([A-Za-z_$][\w$]*)", bloque)
    assert m, f"{nombre}() tiene que devolver una promesa (contrato del panel)"
    res = m.group(1)
    guardado = re.search(r"([A-Za-z_$][\w$]*)\s*=\s*" + re.escape(res) + r"\b", bloque)
    return bloque, res, (guardado.group(1) if guardado else None)


def _manejador_de_close(modal):
    """El cuerpo del manejador del evento `close` de ese `<dialog>`, o None."""
    for _linea, texto in _bloques():
        if f'#{modal}"' not in texto:
            continue
        for m in re.finditer(r'addEventListener\(\s*"close"', texto):
            # que el `close` sea de ESTE dialogo y no de otro del mismo bloque
            antes = texto[max(0, m.start() - 60):m.start()]
            if f'#{modal}"' in antes or texto.count("addEventListener") == 1:
                return _args(texto, m.start())
        onclose = re.search(r'#' + re.escape(modal) + r'"\)\.onclose\s*=', texto)
        if onclose:
            return texto[onclose.end():onclose.end() + 300]
    return None


@pytest.mark.parametrize("nombre,modal", [("confirmar", "confirm-modal"),
                                          ("pedirTexto", "pedir-modal")])
def test_la_funcion_resuelve_tambien_si_se_cierra_con_esc(nombre, modal):
    """Sin el `close` del `<dialog>`, Esc deja la promesa colgada y el panel muerto.

    Esc no pasa por el boton de cancelar: lo cierra el navegador, igual que el
    clic en el fondo y que `cerrarDialogos()` al caducar la sesion. Si la promesa
    solo se resolviera en el `click` del boton, el `await` no volveria nunca: el
    modal desaparece de la vista y la accion se queda esperando para siempre."""
    bloque, res, guardado = _promesa(nombre)
    assert f'#{modal}"' in bloque, f"{nombre}() deberia trabajar sobre #{modal}"
    cuerpo = _manejador_de_close(modal)
    assert cuerpo is not None, (
        f"nadie engancha el evento `close` de #{modal}: si el usuario pulsa Esc, la "
        f"promesa de {nombre}() no se resuelve nunca y la interfaz se queda muerta")
    assert f"{res}(" in cuerpo or (guardado and guardado in cuerpo), (
        f"el manejador de `close` de #{modal} no resuelve la promesa de {nombre}() "
        f"(ni {res}(…) ni {guardado})")


@pytest.mark.parametrize("nombre,modal,vacio", [("confirmar", "confirm-modal", "false"),
                                                ("pedirTexto", "pedir-modal", "null")])
def test_cerrar_sin_responder_cuenta_como_un_no(nombre, modal, vacio):
    """Cerrar con Esc vale `false`/`null`: nunca la accion hacia adelante.

    El manejador tiene que distinguir como se cerro el dialogo --por el
    `returnValue` que pone el boton, o resolviendo el valor vacio a mano--. Si
    resolviera lo mismo en los dos casos, Esc dispararia la accion."""
    cuerpo = _manejador_de_close(modal) or ""
    assert vacio in cuerpo or "returnValue" in cuerpo, (
        f"el `close` de #{modal} no mira como se cerro: {nombre}() tiene que resolver "
        f"{vacio} cuando nadie ha pulsado el boton")


@pytest.mark.parametrize("modal", ["confirm-modal", "pedir-modal"])
def test_los_modales_no_se_quedan_abiertos_al_caducar_la_sesion(modal):
    """Un `<dialog>` abierto deja el resto del documento inerte.

    `cerrarDialogos()` los cierra todos por `dialog[open]`, asi que estos dos
    entran solos; la prueba es que sigue siendo asi y que nadie los saco de
    `dialog.modal`. Si se quedaran abiertos al caducar la sesion, el login
    aparece detras y no se puede ni escribir."""
    assert "dialog[open]" in _codigo("function cerrarDialogos")
    marca = re.search(rf'<dialog id="{modal}"[^>]*class="([^"]*)"', HTML)
    assert marca and "modal" in marca.group(1).split()
