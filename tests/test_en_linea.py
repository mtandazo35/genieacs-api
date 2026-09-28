"""Cuando se da por vivo a un equipo.

Un CPE no mantiene conexion con el ACS: reporta cada PeriodicInformInterval y
entre medias no se sabe nada de el. El panel daba por vivo a cualquiera que
hubiera reportado en los ultimos 15 minutos, con un plazo fijo inventado; como
toda la flota reporta cada 300 s, un router desenchufado hacia 12 minutos
seguia saliendo en verde (lo reporto el usuario el 2026-09-27).

La regla es "no ha perdido dos informes seguidos", medida con el intervalo de
ESE equipo: 11 minutos si reporta cada 5, y 61 si esta configurado cada 30 --
que con el plazo fijo salia caido estando perfectamente vivo.
"""
from datetime import datetime, timedelta, timezone

from conftest import ISP_DEV, OTHER_DEV


def hace(segundos):
    return (datetime.now(timezone.utc) - timedelta(seconds=segundos)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _equipo(fake, dev_id, intervalo, edad, raiz="Device"):
    d = dict(fake.devices[dev_id])
    d["_lastInform"] = hace(edad) if edad is not None else None
    if intervalo is not None:
        rama = d.setdefault(raiz, {}).setdefault("ManagementServer", {})
        rama["PeriodicInformInterval"] = {"_value": intervalo, "_writable": True,
                                          "_type": "xsd:unsignedInt"}
    fake.devices[dev_id] = d
    return d


def _uno(client, admin_h, dev_id):
    return next(x for x in client.get("/devices", headers=admin_h).json() if x["id"] == dev_id)


def test_reporta_cada_5_minutos_y_lleva_12_sin_reportar_no_esta_en_linea(client, fake, admin_h):
    """El caso que se vio en la flota: con el plazo fijo de 15 min salia verde."""
    _equipo(fake, ISP_DEV, 300, 12 * 60)
    fila = _uno(client, admin_h, ISP_DEV)
    assert fila["online"] is False
    assert fila["inform_interval"] == 300 and fila["limite_online"] == 660


def test_reporta_cada_5_minutos_y_acaba_de_reportar_esta_en_linea(client, fake, admin_h):
    _equipo(fake, ISP_DEV, 300, 90)
    assert _uno(client, admin_h, ISP_DEV)["online"] is True


def test_un_equipo_lento_no_se_da_por_caido_antes_de_tiempo(client, fake, admin_h):
    """Configurado cada 30 min: a los 20 min esta vivo, aunque el plazo fijo de
    15 lo pintara caido. Ese es el otro lado del mismo error."""
    _equipo(fake, ISP_DEV, 1800, 20 * 60)
    fila = _uno(client, admin_h, ISP_DEV)
    assert fila["online"] is True and fila["limite_online"] == 3660


def test_dos_informes_perdidos_es_el_limite(client, fake, admin_h):
    """Uno perdido puede ser un paquete; dos seguidos ya no."""
    _equipo(fake, ISP_DEV, 300, 2 * 300 + 30)     # dentro del margen
    assert _uno(client, admin_h, ISP_DEV)["online"] is True
    _equipo(fake, OTHER_DEV, 300, 2 * 300 + 120)  # pasado el margen
    assert _uno(client, admin_h, OTHER_DEV)["online"] is False


def test_sin_intervalo_conocido_se_usa_el_plazo_de_siempre(client, fake, admin_h):
    """Un equipo que aun no reporto su ManagementServer no puede quedarse sin
    estado: se mantiene el criterio anterior hasta que se sepa el suyo."""
    _equipo(fake, ISP_DEV, None, 10 * 60)
    fila = _uno(client, admin_h, ISP_DEV)
    assert fila["inform_interval"] is None and fila["limite_online"] == 900
    assert fila["online"] is True


def test_un_intervalo_absurdo_no_deja_a_nadie_en_verde_para_siempre(client, fake, admin_h):
    """Un 0 (o un texto) en PeriodicInformInterval daria limite 60 s o reventaria."""
    for malo in (0, -1, "", "cada rato"):
        _equipo(fake, ISP_DEV, malo, 10 * 60)
        fila = _uno(client, admin_h, ISP_DEV)
        assert fila["limite_online"] == 900, f"con {malo!r}"


def test_el_que_nunca_reporto_no_es_lo_mismo_que_uno_caido(client, fake, admin_h):
    """None = no se sabe. Pintarlo de rojo diria que se cayo, y nunca estuvo."""
    _equipo(fake, ISP_DEV, 300, None)
    fila = _uno(client, admin_h, ISP_DEV)
    assert fila["online"] is None and fila["sin_reportar"] is None


def test_se_dice_cuanto_lleva_sin_reportar(client, fake, admin_h):
    """Para no tener que restar fechas a ojo en la tarjeta."""
    _equipo(fake, ISP_DEV, 300, 7 * 60)
    assert 6 * 60 <= _uno(client, admin_h, ISP_DEV)["sin_reportar"] <= 8 * 60


def test_el_panel_usa_el_criterio_del_servidor():
    """Si el panel siguiera restando 15 minutos por su cuenta, todo esto sobra."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "d.online == null ? isRecent(d.last_inform) : d.online" in js
