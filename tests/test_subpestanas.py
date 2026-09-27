"""Subpestañas: cada grupo manda solo sobre sus propios paneles.

Fallo real (2026-09-27): el manejador de Actualizaciones usaba `$$(".subtab")`,
que selecciona TODAS las subpestañas del panel. Al añadir las de
Aprovisionamiento y Ajustes, pulsar cualquiera de ellas escondía los paneles de
Actualizaciones —su `dataset.sub` es undefined y no coincide con ninguno—, así
que esa página aparecía vacía hasta volver a pulsar una de sus pestañas.
"""
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")

# grupo -> (atributo de la pestaña, atributo del panel, pagina que lo contiene)
GRUPOS = {
    "actualizaciones": ("data-sub", "data-subpanel", "updates-page"),
    "aprovisionamiento": ("data-psub", "data-ppanel", "prov-page"),
    "ajustes": ("data-ssub", "data-spanel", "settings-page"),
}


def _codigo_sin_comentarios() -> str:
    return "\n".join(l for l in JS.splitlines() if not l.strip().startswith("//"))


@pytest.mark.parametrize("grupo", list(GRUPOS))
def test_el_grupo_solo_toca_sus_paneles(grupo):
    _tab, panel, pagina = GRUPOS[grupo]
    codigo = _codigo_sin_comentarios()
    for uso in re.findall(rf'\$\$\("([^"]*\[{panel}\][^"]*)"\)', codigo):
        assert uso.startswith(("#" + pagina, "#settings-tabs")), \
            f"{grupo}: '{uso}' alcanza paneles de otras paginas"


def test_nadie_selecciona_todas_las_subpestanas_a_la_vez():
    """El selector global fue justo la causa del fallo."""
    codigo = _codigo_sin_comentarios()
    assert '$$(".subtab")' not in codigo
    assert '$$("[data-subpanel]")' not in codigo


@pytest.mark.parametrize("grupo", list(GRUPOS))
def test_al_entrar_en_la_pagina_su_panel_ya_esta_desplegado(grupo):
    """Nada de paginas en blanco esperando a que alguien pulse una pestaña."""
    entrada = {"actualizaciones": "loadUpdates", "aprovisionamiento": "loadProv",
               "ajustes": "abrirAjustes"}[grupo]
    despliega = {"actualizaciones": "mostrarSubActualizaciones",
                 "aprovisionamiento": "mostrarSubProv", "ajustes": "ajustesSub"}[grupo]
    cuerpo = JS[JS.index(f"function {entrada}"):]
    cuerpo = cuerpo[:cuerpo.index(chr(10) + "}")]
    assert 'classList.contains("active")' in cuerpo, \
        f"{entrada} deberia mirar cual es la pestaña activa"
    assert despliega in cuerpo, f"{entrada} deberia llamar a {despliega}"


@pytest.mark.parametrize("grupo", list(GRUPOS))
def test_cada_pestana_tiene_su_panel(grupo):
    tab, panel, pagina = GRUPOS[grupo]
    seccion = re.search(rf'<section id="{pagina}".*?</section>', HTML, re.S).group(0)
    pestanas = set(re.findall(rf'{tab}="(\w+)"', seccion))
    paneles = set(re.findall(rf'{panel}="(\w+)"', seccion))
    assert pestanas and pestanas == paneles, f"{grupo}: {pestanas} vs {paneles}"


@pytest.mark.parametrize("grupo", list(GRUPOS))
def test_solo_una_pestana_nace_activa(grupo):
    tab, _panel, pagina = GRUPOS[grupo]
    seccion = re.search(rf'<section id="{pagina}".*?</section>', HTML, re.S).group(0)
    activas = [t for t in re.findall(rf'<button[^>]*{tab}="\w+"[^>]*>', seccion) if "active" in t]
    assert len(activas) == 1, f"{grupo}: {len(activas)} pestañas activas de inicio"
