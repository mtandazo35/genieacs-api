"""Comprobaciones del panel web.

app.js engancha los eventos al cargar: `$("#x").addEventListener(...)`. Si ese
id no existe en el HTML, la excepcion corta la ejecucion del script entero y el
panel se queda muerto, sin que ningun test de la API lo note. Estas pruebas
cruzan lo que el JS usa contra lo que el HTML tiene.
"""
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")

IDS_HTML = set(re.findall(r'id="([^"]+)"', HTML))


def _ids_usados_en_js() -> set:
    """Ids que el JS busca con $("#id") (fuera de plantillas y cadenas dinamicas)."""
    return {m for m in re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', JS)}


def test_todos_los_ids_que_usa_el_js_existen_en_el_html():
    faltan = sorted(_ids_usados_en_js() - IDS_HTML)
    assert not faltan, f"el JS engancha ids que no estan en el HTML: {faltan}"


def test_cada_enlace_de_navegacion_tiene_su_pagina_y_su_carga():
    navs = set(re.findall(r'data-nav="([a-z]+)"', HTML))
    for nav in navs:
        assert f'nav !== "{nav}"' in JS or f'nav === "{nav}"' in JS, \
            f"la navegacion no contempla '{nav}'"


def test_las_subpestanas_de_aprovisionamiento_tienen_panel():
    subs = set(re.findall(r'data-psub="([a-z]+)"', HTML))
    paneles = set(re.findall(r'data-ppanel="([a-z]+)"', HTML))
    assert subs and subs == paneles, f"subpestanas {subs} vs paneles {paneles}"


def test_la_pagina_de_aprovisionamiento_es_solo_para_admin():
    assert '$("#nav-prov").classList.toggle("hidden", S.role !== "admin")' in JS


CSS = (ESTATICOS / "styles.css").read_text(encoding="utf-8")


def test_todas_las_variables_de_color_estan_definidas():
    """Un var(--x) mal escrito no da error: el color simplemente no se aplica."""
    definidas = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS))
    usadas = set(re.findall(r"var\((--[a-z0-9-]+)", CSS))
    assert not (usadas - definidas), f"variables usadas y no definidas: {sorted(usadas - definidas)}"


def test_las_llaves_del_css_estan_equilibradas():
    assert CSS.count("{") == CSS.count("}")


def test_el_contenido_usa_el_ancho_de_la_pantalla():
    """En un monitor ancho el panel dejaba dos franjas laterales vacias enormes."""
    main = re.search(r"\nmain\{([^}]+)\}", CSS).group(1)
    assert "width:100%" in main
    tope = re.search(r"max-width:(\d+)px", main)
    assert tope and int(tope.group(1)) >= 1600, "el contenido sigue encajonado"
    assert "clamp(" in main, "el margen lateral deberia crecer con la pantalla"


def test_en_movil_sigue_habiendo_margen_lateral():
    """Ancho completo no puede significar texto pegado al borde del telefono."""
    movil = re.search(r"@media \(max-width:640px\)\{([^@]+)", CSS).group(1)
    assert "main{padding:1rem}" in movil.replace(" ", "")


def test_el_foco_del_teclado_es_visible():
    """Sin esto, quien navega con Tab no sabe donde esta."""
    assert ":focus-visible" in CSS


@pytest.mark.parametrize("endpoint", [
    "/discovery", "/discovery/rules", "/discovery/assign", "/discovery/mode",
    "/profiles", "/homologacion/estado", "/homologacion/proponer",
    "/provisioning/dhcp/defaults", "/provisioning/dhcp/script",
])
def test_el_panel_llama_a_endpoints_que_existen(client, admin_h, endpoint):
    """Cada ruta que usa el panel tiene que existir en la API (no 404)."""
    assert endpoint in JS, f"el panel no usa {endpoint}"
    rutas = set(client.get("/openapi.json").json()["paths"])
    assert endpoint in rutas, f"{endpoint} no existe en la API"
