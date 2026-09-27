"""Qué sabe hacer cada modelo, deducido de su árbol.

El panel enseñaba las mismas pestañas para todos y el usuario descubría al
pulsar que ese equipo no lo soportaba. Con esto, cada ficha refleja lo que ese
modelo expone de verdad — y distingue "no lo soporta" de "todavía no se sabe".
"""
from app.treeprofile import NO, QUIZA, SI, capabilities
from conftest import ISP_DEV
from test_perfil_derivado import ex511, v, xx530


def _estado(doc, funcion):
    return capabilities(doc)[funcion]["estado"]


def test_un_equipo_refrescado_dice_lo_que_soporta_y_lo_que_no():
    caps = capabilities(ex511())
    assert caps["_resumen"]["arbol_completo"] is True
    assert caps["wifi_2g"]["estado"] == SI and caps["wifi_5g"]["estado"] == SI
    assert caps["wan_dhcp"]["estado"] == SI and caps["wan_estatica"]["estado"] == SI
    # este equipo no expone ni DNS configurable ni usuario PPPoE: con el arbol
    # completo eso es un NO firme, no una duda
    assert caps["dns"]["estado"] == NO
    assert caps["wan_pppoe"]["estado"] == NO
    assert "el equipo no lo expone" in caps["dns"]["detalle"]
    assert caps["_resumen"]["por_saber"] == []


def test_con_el_arbol_a_medias_no_se_dice_que_no_lo_soporta():
    """La diferencia que importa: 'no lo tiene' vs 'aun no lo hemos preguntado'."""
    caps = capabilities(xx530())
    assert caps["_resumen"]["arbol_completo"] is False
    assert caps["diag_ping"]["estado"] == QUIZA
    assert "refrescar" in caps["diag_ping"]["detalle"]
    assert caps["_resumen"]["no_soportadas"] == []      # ningun NO con el arbol incompleto


def test_lo_soportado_apunta_a_la_ruta_que_lo_sustenta():
    caps = capabilities(ex511())
    assert caps["wifi_2g"]["detalle"] == "Device.WiFi.SSID.1.SSID"
    assert caps["wifi_2g"]["escribible"] is True


def test_una_funcion_de_solo_lectura_se_marca_como_tal():
    doc = ex511()
    doc["Device"]["WiFi"]["SSID"]["1"]["SSID"] = v("RED-2G", False)   # no escribible
    caps = capabilities(doc)
    assert caps["wifi_2g"]["estado"] == SI and caps["wifi_2g"]["escribible"] is False


def test_las_ordenes_tr069_no_dependen_del_arbol():
    """Reiniciar o mandar firmware son llamadas RPC: las soporta cualquier CPE."""
    caps = capabilities(xx530())
    for f in ("reinicio", "factory_reset", "firmware", "reinicio_programado"):
        assert caps[f]["estado"] == SI


def test_ipv6_se_detecta_por_las_rutas_del_equipo():
    doc = ex511()
    assert _estado(doc, "ipv6") == NO                     # este arbol no trae IPv6
    con_ipv6 = ex511()
    con_ipv6["Device"]["IP"]["Interface"]["4"]["IPv6Enable"] = v(True, True)
    caps = capabilities(con_ipv6)["ipv6"]
    assert caps["estado"] == SI and caps["detalle"].endswith("IPv6Enable")


def test_la_ficha_devuelve_las_capacidades(client, fake, admin_h):
    fake.devices[ISP_DEV] = {"_id": ISP_DEV, "_tags": ["ISP-A"],
                             "_deviceId": {"_Manufacturer": "TP-Link", "_ProductClass": "Device2"},
                             **ex511()}
    caps = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()["capabilities"]
    assert caps["wifi_5g"]["estado"] == SI
    assert "wifi_2g" in caps["_resumen"]["soportadas"]
    assert caps["wan_pppoe"]["nombre"] == "WAN por PPPoE"


def test_el_panel_desactiva_las_pestanas_no_soportadas():
    """La pestaña se deshabilita, no se esconde: se ve que existe y por qué no va."""
    js = open("app/static/app.js", encoding="utf-8").read()
    assert "TAB_REQUIERE" in js and "aplicarCapacidades(st.capabilities)" in js
    assert 't.disabled = true' in js
    # con el arbol incompleto NO se deshabilita nada
    assert 'estados.every(e => e === "no")' in js
