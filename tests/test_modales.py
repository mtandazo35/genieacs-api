"""Formularios de alta dentro de un modal.

Al mover un formulario a un modal el riesgo no es que se vea feo: es que un
campo se quede fuera y el alta empiece a mandar datos incompletos sin que nada
falle a la vista. Estas pruebas comparan los campos que el JavaScript lee con
los que el modal contiene.
"""
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")
CSS = (ESTATICOS / "styles.css").read_text(encoding="utf-8")

MODALES = {
    "user-modal": ("user-form", "user-new", ["u-name", "u-pass", "u-role", "u-tag"]),
    "rule-modal": ("disc-rule-form", "rule-new", ["disc-cidr", "disc-tag", "disc-comment"]),
}


def _modal(id_modal):
    return re.search(rf'<dialog id="{id_modal}".*?</dialog>', HTML, re.S).group(0)


@pytest.mark.parametrize("id_modal", list(MODALES))
def test_el_modal_lleva_el_formulario_entero(id_modal):
    form, _boton, campos = MODALES[id_modal]
    marca = _modal(id_modal)
    assert f'id="{form}"' in marca
    for campo in campos:
        assert f'id="{campo}"' in marca, f"{campo} se quedo fuera del modal"
        assert HTML.count(f'id="{campo}"') == 1, f"{campo} aparece duplicado"


@pytest.mark.parametrize("id_modal", list(MODALES))
def test_el_javascript_no_lee_campos_que_ya_no_existen(id_modal):
    """Si el JS lee un id que se perdio al mover el formulario, el alta revienta."""
    _form, _boton, _campos = MODALES[id_modal]
    ids_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', HTML))
    for usado in re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', JS):
        assert usado in ids_html, f"el JS usa #{usado} y ya no esta en el HTML"


@pytest.mark.parametrize("id_modal", list(MODALES))
def test_hay_un_boton_que_lo_abre(id_modal):
    _form, boton, _campos = MODALES[id_modal]
    assert f'id="{boton}"' in HTML
    assert f'#{boton}' in JS and "showModal" in JS


@pytest.mark.parametrize("id_modal", list(MODALES))
def test_el_modal_se_cierra_al_guardar(id_modal):
    """Si no se cierra, parece que no se guardo y el usuario lo manda dos veces."""
    form, _boton, _campos = MODALES[id_modal]
    desde = JS.index(f'$("#{form}").addEventListener')
    # hasta el cierre real del manejador: una linea que es exactamente });
    fin = JS.index(chr(10) + '});', desde)
    bloque = JS[desde:fin]
    assert "cerrarModal" in bloque


def test_se_usa_el_dialogo_nativo_y_no_un_invento():
    """<dialog> trae Esc, el foco atrapado y el fondo, sin programar nada."""
    assert "showModal" in JS and "<dialog" in HTML
    assert ".modal::backdrop" in CSS


def test_se_puede_cerrar_sin_guardar():
    assert "data-close" in HTML and '[data-close]' in JS
    # y pulsando fuera, sobre el fondo
    assert "e.target === d" in JS


def test_el_formulario_ya_no_ocupa_la_pagina():
    """El motivo del cambio: ocupaba pantalla siempre para algo ocasional."""
    usuarios = re.search(r'<section id="users-page".*?</section>', HTML, re.S).group(0)
    fuera = usuarios.split("<dialog")[0]
    assert 'id="u-name"' not in fuera, "el formulario sigue suelto en la pagina"
    assert 'id="user-new"' in fuera, "y deberia quedar solo el boton"
