"""Ajustes del panel: Mi cuenta dentro, y lo de admin separado.

Mi cuenta estaba en su propia entrada del menu y Ajustes era solo de admin.
Al meter la cuenta dentro, el riesgo evidente es dejar a los usuarios ISP sin
poder cambiar su contraseña; estas pruebas vigilan justo eso.
"""
import re
from pathlib import Path

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
HTML = (ESTATICOS / "index.html").read_text(encoding="utf-8")
JS = (ESTATICOS / "app.js").read_text(encoding="utf-8")
CSS = (ESTATICOS / "styles.css").read_text(encoding="utf-8")


def test_mi_cuenta_vive_dentro_de_ajustes():
    seccion = re.search(r'<section id="settings-page".*?</section>', HTML, re.S).group(0)
    assert 'id="account-form"' in seccion
    assert 'id="account-page"' not in HTML, "la pagina suelta de Mi cuenta deberia desaparecer"
    assert 'data-nav="account"' not in HTML, "y su entrada del menu tambien"


def test_ajustes_es_accesible_para_cualquier_rol():
    """Si se ocultara a los ISP, se quedarian sin poder cambiar su contraseña."""
    assert 'data-nav="settings" id="nav-settings">' in HTML, "el enlace no puede nacer oculto"
    assert '$("#nav-settings").classList.toggle("hidden"' not in JS, \
        "el enlace de Ajustes no debe ocultarse por rol"


def test_lo_que_toca_servidores_solo_lo_ve_el_admin():
    seccion = re.search(r'<section id="settings-page".*?</section>', HTML, re.S).group(0)
    def etiqueta(atributo, valor):
        """La etiqueta completa, no solo lo que va despues del atributo."""
        return re.search(rf'<[^>]*{atributo}="{valor}"[^>]*>', seccion).group(0)

    for panel in ("acs", "ia"):
        assert "admin-only" in etiqueta("data-ssub", panel), f"la pestaña {panel} deberia ser admin-only"
        assert "admin-only" in etiqueta("data-spanel", panel), f"el panel {panel} deberia ser admin-only"
    assert "admin-only" not in etiqueta("data-ssub", "cuenta"), "Mi cuenta es para todos"
    assert "admin-only" not in etiqueta("data-ssub", "tema")
    assert 'S.role === "admin"' in JS and '$$(".admin-only")' in JS


def test_cada_subpestana_tiene_su_panel():
    subs = set(re.findall(r'data-ssub="(\w+)"', HTML))
    paneles = set(re.findall(r'data-spanel="(\w+)"', HTML))
    assert subs == paneles == {"cuenta", "tema", "acs", "ia"}


def test_quien_tenia_guardada_la_vista_mi_cuenta_no_se_queda_en_blanco():
    """localStorage puede traer 'account', una vista que ya no existe."""
    assert '=== "account" ? "settings"' in JS


def test_las_tablas_pueden_desplazarse_en_horizontal():
    """Una tabla ancha rompia la pagina en pantallas pequeñas."""
    assert HTML.count('class="tbl-wrap"') == HTML.count('<table class="tbl"')
    assert ".tbl-wrap{" in CSS and "overflow-x:auto" in CSS


def test_la_cabecera_de_la_tabla_se_queda_fija_al_desplazar():
    bloque = re.search(r"\.tbl th,\.tbl td\{[^}]+\}\s*\.tbl th\{([^}]+)\}", CSS)
    assert bloque and "position:sticky" in bloque.group(1)


def test_las_filas_alternan_color_para_seguir_la_linea():
    assert ".tbl tbody tr:nth-child(even)" in CSS
