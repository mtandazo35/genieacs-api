"""Temas del panel.

El fallo clasico de un tema es dejarse un token: el navegador no avisa, hereda
el del tema por defecto, y aparece un color del tema anterior en medio del
nuevo (texto oscuro sobre fondo oscuro, por ejemplo). Estas pruebas comparan
cada tema contra la paleta base.
"""
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
CSS = (ESTATICOS / "styles.css").read_text(encoding="utf-8")
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")

TEMAS = ["templada", "noche", "claro"]

# tokens que definen la paleta; el resto (--radius, --sans...) no cambia por tema
_NO_PALETA = ("--radius", "--radius-sm", "--shadow", "--shadow-lg")


def _bloque(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", CSS)
    assert m, f"no existe el bloque {selector}"
    return m.group(1)


def _tokens(bloque: str) -> set:
    return {t for t in re.findall(r"(--[a-z0-9-]+)\s*:", bloque) if t not in _NO_PALETA}


BASE = _tokens(_bloque(":root"))


@pytest.mark.parametrize("tema", TEMAS)
def test_cada_tema_define_toda_la_paleta(tema):
    faltan = BASE - _tokens(_bloque(f'[data-theme="{tema}"]'))
    assert not faltan, f'el tema "{tema}" no define: {sorted(faltan)}'


@pytest.mark.parametrize("tema", TEMAS)
def test_ningun_tema_inventa_tokens_nuevos(tema):
    """Un token que solo existe en un tema no se usa en ningun sitio."""
    sobran = _tokens(_bloque(f'[data-theme="{tema}"]')) - BASE
    assert not sobran, f'el tema "{tema}" define tokens que la base no tiene: {sorted(sobran)}'


def test_el_tema_claro_avisa_al_navegador():
    """Sin color-scheme, los controles nativos y las barras siguen en oscuro."""
    assert "color-scheme:light" in _bloque('[data-theme="claro"]').replace(" ", "")


def test_el_panel_ofrece_los_temas_y_el_de_siempre():
    ofrecidos = set(re.findall(r'data-tema="([a-z]*)"', HTML))
    assert ofrecidos == set(TEMAS) | {""}, f"el selector ofrece {ofrecidos}"


def test_el_tema_se_aplica_al_cargar_y_no_solo_en_ajustes():
    """Si solo se aplicara al abrir Ajustes, cada recarga volveria al de siempre."""
    llamadas = [l.strip() for l in JS.splitlines()
                if l.strip() == "aplicarTema(temaGuardado());"]
    assert llamadas, "falta la llamada (o esta comentada) que aplica el tema al cargar"
    cuerpo = JS[JS.index("function aplicarTema"):JS.index("function temaGuardado")]
    assert "document.documentElement.setAttribute" in cuerpo
    assert "removeAttribute" in cuerpo, "elegir el tema por defecto debe quitar el atributo"


def test_el_navegador_sin_almacenamiento_no_rompe_el_panel():
    """En ventana privada localStorage lanza excepcion: no puede tumbar el panel."""
    guardado = JS[JS.index("function temaGuardado"):JS.index("$(\"#temas\")")]
    assert "try" in guardado and "catch" in guardado
    seleccion = JS[JS.index('$("#temas").addEventListener'):]
    assert "try {" in seleccion.split("aplicarTema(tema)")[0]
