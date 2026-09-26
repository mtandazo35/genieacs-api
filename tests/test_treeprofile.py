"""Cobertura del arbol: distinguir "el equipo no lo soporta" de "nadie se lo ha preguntado".

Los arboles de ejemplo reproducen lo medido en la flota el 2026-09-26:
un equipo recien vinculado trae 28-45 parametros y ninguna ruta por defecto;
uno refrescado trae 296 (TR-098) o 4653 (TR-181) y todas las senales.
Valores genericos: ni SSID, ni claves, ni IPs de clientes.
"""
from app.treeprofile import coverage, flatten, root_of


def v(value, writable=False):
    return {"_value": value, "_writable": writable, "_type": "xsd:string"}


def tr181_minimo():
    """Lo que reporta un TP-Link en su primer inform (~40 parametros)."""
    return {
        "_id": "AAAAAA-MODELO-0001",
        "_lastInform": "2026-09-26T21:06:28.011Z",
        "Device": {
            "DeviceInfo": {"SoftwareVersion": v("1.0.0"), "UpTime": v(3178)},
            "IP": {"Interface": {
                "1": {"IPv4Address": {"1": {"IPAddress": v("192.168.1.1", True)}}},
                "7": {"IPv4Address": {"1": {"IPAddress": v("198.51.100.20")}}},
            }},
            "DHCPv4": {"Server": {"Pool": {"1": {"MinAddress": v("192.168.1.2")}}}},
            "WiFi": {"SSID": {"1": {"SSID": v("RED-2G", True)}, "2": {"SSID": v("RED-5G", True)}}},
            "PPP": {"Interface": {"1": {"ConnectionStatus": v("Disconnected")}}},
        },
    }


def tr181_completo():
    """El mismo equipo tras pulsar Actualizar: ya trae ruta por defecto y radios."""
    doc = tr181_minimo()
    doc["Device"]["Routing"] = {"Router": {"1": {"IPv4Forwarding": {
        "3": {"GatewayIPAddress": v("198.51.100.1", True), "Enable": v(True, True),
              "Interface": v("Device.IP.Interface.7.", True)},
    }}}}
    doc["Device"]["WiFi"]["Radio"] = {
        "1": {"OperatingFrequencyBand": v("2.4GHz", True)},
        "2": {"OperatingFrequencyBand": v("5GHz", True)},
    }
    return doc


def tr098_minimo():
    """Cudy recien vinculado (~44 parametros)."""
    return {
        "_id": "BBBBBB-MODELO-0002",
        "InternetGatewayDevice": {
            "DeviceInfo": {"SoftwareVersion": v("2.4.8")},
            "LANDevice": {"1": {
                "LANHostConfigManagement": {"MinAddress": v("192.168.10.10", True)},
                "WLANConfiguration": {"1": {"SSID": v("RED-2G", True)}},
            }},
        },
    }


def tr098_completo():
    doc = tr098_minimo()
    doc["InternetGatewayDevice"]["Layer3Forwarding"] = {
        "DefaultConnectionService": v("InternetGatewayDevice.WANDevice.1."
                                      "WANConnectionDevice.1.WANPPPConnection.1")}
    doc["InternetGatewayDevice"]["LANDevice"]["1"]["WLANConfiguration"]["1"]["Standard"] = v("bgnax", True)
    return doc


def test_equipo_recien_vinculado_se_marca_incompleto_y_dice_que_hacer():
    c = coverage(tr181_minimo())
    assert c["complete"] is False
    assert c["root"] == "Device"
    assert "ruta_defecto" in c["missing"] and "bandas_wifi" in c["missing"]
    assert "Actualizar" in c["hint"] and str(c["params"]) in c["hint"]


def test_equipo_refrescado_queda_completo_y_sin_aviso():
    c = coverage(tr181_completo())
    assert c["complete"] is True
    assert c["missing"] == []
    assert c["hint"] is None
    assert c["params"] > coverage(tr181_minimo())["params"]


def test_lo_mismo_vale_para_tr098():
    assert coverage(tr098_minimo())["complete"] is False
    completo = coverage(tr098_completo())
    assert completo["complete"] is True and completo["root"] == "InternetGatewayDevice"


def test_una_ruta_sin_gateway_no_cuenta_como_arbol_completo():
    """Un equipo puede tener la tabla de rutas vacia: eso no es haber refrescado."""
    doc = tr181_completo()
    del doc["Device"]["Routing"]
    c = coverage(doc)
    assert c["complete"] is False and c["missing"] == ["ruta_defecto"]


def test_equipo_sin_modelo_de_datos_no_revienta():
    c = coverage({"_id": "X", "_lastInform": "2026-09-26T00:00:00Z"})
    assert c["root"] is None and c["complete"] is False and c["params"] == 0
    assert "reportado" in c["hint"]


def test_flatten_cuenta_solo_parametros_con_valor():
    doc = tr181_minimo()
    paths = [p for p, _v, _w in flatten(doc)]
    assert "Device.IP.Interface.7.IPv4Address.1.IPAddress" in paths
    assert all(not p.startswith("_") for p in paths)          # sin metadatos del ACS
    assert root_of(doc) == "Device"


def test_el_panel_avisa_en_la_ficha_del_equipo(client, fake, admin_h):
    """El aviso tiene que llegar al endpoint que pinta la ficha, no solo al modulo."""
    from conftest import ISP_DEV
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["tree"]["complete"] is False
    assert "Actualizar" in st["tree"]["hint"]

    fake.devices[ISP_DEV]["InternetGatewayDevice"].update({
        "Layer3Forwarding": {"DefaultConnectionService": v("x")},
        "LANDevice": {"1": {"WLANConfiguration": {"1": {"Standard": v("bgnax")}}}},
    })
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["tree"]["complete"] is True and st["tree"]["hint"] is None
