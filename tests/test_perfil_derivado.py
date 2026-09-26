"""Perfil derivado: deducir del arbol que instancia es cada cosa.

Los escenarios salen de equipos reales de la flota (2026-09-26), con valores
genericos: el EX511 tiene 14 SSIDs y la WAN en la Interface.4; el XX530 la
tiene en la 7; el WR3000 usa PPPoE aunque el mapa escrito a mano apunta a
WANIPConnection.1.
"""
from app.parammap import pick_map
from app.treeprofile import derive, derived_params, effective_params


def v(value, writable=False):
    return {"_value": value, "_writable": writable}


def ex511():
    """TR-181 refrescado: dos radios, varios SSID por radio, ruta por defecto."""
    return {"Device": {
        "DHCPv4": {"Server": {"Pool": {"1": {"MinAddress": v("192.168.0.2"), "MaxAddress": v("192.168.0.254")}}}},
        "IP": {"Interface": {
            "1": {"IPv4Address": {"1": {"IPAddress": v("192.168.0.1"), "SubnetMask": v("255.255.255.0")}}},
            "3": {"IPv4Address": {"1": {"IPAddress": v("0.0.0.0")}}},
            "4": {"IPv4Address": {"1": {"IPAddress": v("198.51.100.3", True),
                                        "SubnetMask": v("255.255.255.248", True),
                                        "AddressingType": v("DHCP", True)}},
                  "MaxMTUSize": v(1500, True), "X_TP_ServiceType": v("Internet", True)},
        }},
        "Routing": {"Router": {"1": {"IPv4Forwarding": {
            "1": {"Enable": v(False, True), "GatewayIPAddress": v("0.0.0.0", True),
                  "Interface": v("Device.IP.Interface.2.", True)},
            "3": {"Enable": v(True, True), "GatewayIPAddress": v("198.51.100.1", True),
                  "Interface": v("Device.IP.Interface.4.", True)},
        }}}},
        "WiFi": {
            "Radio": {"1": {"OperatingFrequencyBand": v("2.4GHz"), "Channel": v(9, True)},
                      "2": {"OperatingFrequencyBand": v("5GHz"), "Channel": v(48, True)}},
            "SSID": {"1": {"SSID": v("RED-2G", True), "LowerLayers": v("Device.WiFi.Radio.1.")},
                     "2": {"SSID": v("invitados-2g", True), "LowerLayers": v("Device.WiFi.Radio.1.")},
                     "3": {"SSID": v("RED-5G", True), "LowerLayers": v("Device.WiFi.Radio.2.")},
                     "4": {"SSID": v("invitados-5g", True), "LowerLayers": v("Device.WiFi.Radio.2.")}},
            "AccessPoint": {"1": {"SSIDReference": v("Device.WiFi.SSID.1.")},
                            "3": {"SSIDReference": v("Device.WiFi.SSID.3.")}},
        },
        "PPP": {"Interface": {"1": {"ConnectionStatus": v("Disconnected")}}},
    }}


def xx530():
    """TR-181 sin refrescar: sin radios ni rutas, WAN en una instancia alta."""
    return {"Device": {
        "DHCPv4": {"Server": {"Pool": {"1": {"MinAddress": v("192.168.1.2")}}}},
        "IP": {"Interface": {
            "1": {"IPv4Address": {"1": {"IPAddress": v("192.168.1.1", True)}}},
            "7": {"IPv4Address": {"1": {"IPAddress": v("198.51.100.2")}}},
        }},
        "PPP": {"Interface": {"1": {"ConnectionStatus": v("Disconnected")}}},
    }}


def wr3000():
    """TR-098 con PPPoE como servicio por defecto."""
    wan = "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1"
    return {"InternetGatewayDevice": {
        "Layer3Forwarding": {"DefaultConnectionService": v(f"{wan}.WANPPPConnection.1")},
        "LANDevice": {"1": {"WLANConfiguration": {
            "1": {"SSID": v("RED-2G", True), "Standard": v("bgnax"), "Channel": v(9, True)},
            "2": {"SSID": v("RED-5G", True), "Standard": v("anacax"), "Channel": v(44, True)},
        }}},
        "WANDevice": {"1": {"WANConnectionDevice": {"1": {
            "WANIPConnection": {"1": {"ExternalIPAddress": v("0.0.0.0"), "AddressingType": v("DHCP", True)}},
            "WANPPPConnection": {"1": {"ExternalIPAddress": v("198.51.100.6"),
                                       "Username": v("abonado", True),
                                       "ConnectionStatus": v("Connected")}},
        }}}},
    }}


# ---------- que instancia es cada cosa ----------

def test_la_wan_es_la_que_senala_la_ruta_por_defecto():
    d = derive(ex511())
    assert d["wan"] == "4" and d["lan"] == "1"
    assert "ruta por defecto" in d["evidencia"]["wan"]


def test_sin_ruta_cargada_la_wan_es_la_unica_ip_fuera_de_la_lan():
    """Caso del equipo recien vinculado: aun asi hay que encontrarle la WAN."""
    d = derive(xx530())
    assert d["wan"] == "7" and d["lan"] == "1"
    assert "fuera de la LAN" in d["evidencia"]["wan"]
    assert derived_params(xx530())["wan_ip"][0] == "Device.IP.Interface.7.IPv4Address.1.IPAddress"


def test_cada_banda_toma_el_primer_ssid_de_su_radio():
    d = derive(ex511())["wifi"]
    assert d["2g"] == {"radio": "1", "ssid": "1", "ap": "1"}
    assert d["5g"] == {"radio": "2", "ssid": "3", "ap": "3"}   # no el 2, que es de la radio 1


def test_la_banda_no_se_adivina_por_el_numero_de_instancia():
    """Si las radios vienen al reves, el perfil las sigue clasificando bien."""
    doc = ex511()
    doc["Device"]["WiFi"]["Radio"]["1"]["OperatingFrequencyBand"] = v("5GHz")
    doc["Device"]["WiFi"]["Radio"]["2"]["OperatingFrequencyBand"] = v("2.4GHz")
    d = derive(doc)["wifi"]
    assert d["5g"]["radio"] == "1" and d["2g"]["radio"] == "2"


def test_tr098_detecta_la_conexion_por_defecto_y_las_bandas():
    d = derive(wr3000())
    assert d["wan_conn"].endswith("WANPPPConnection.1")
    assert d["wifi"]["2g"]["wlan"] == "1" and d["wifi"]["5g"]["wlan"] == "2"
    dp = derived_params(wr3000())
    assert dp["pppoe_user"][0].endswith("WANPPPConnection.1.Username")


def test_equipo_sin_arbol_no_inventa_nada():
    d = derive({"_id": "x"})
    assert d["root"] is None and derived_params({"_id": "x"}) == {}


# ---------- la regla de convivencia con el mapa escrito a mano ----------

def test_el_mapa_manda_cuando_el_equipo_tiene_esa_ruta():
    doc = wr3000()
    eff = effective_params(pick_map(doc), doc)
    # el mapa apunta a WANIPConnection.1, que este equipo tiene: no se cambia
    assert eff["wan_ip"][0].endswith("WANIPConnection.1.ExternalIPAddress")


def test_la_deduccion_rellena_lo_que_el_mapa_deja_en_blanco():
    doc = xx530()
    eff = effective_params(pick_map(doc), doc)
    # el mapa TR-181 no tiene wan_ip; sin deduccion la ficha salia vacia
    assert eff["wan_ip"][0] == "Device.IP.Interface.7.IPv4Address.1.IPAddress"


def test_si_el_equipo_no_tiene_la_instancia_del_mapa_se_usa_la_deducida():
    """El mapa TR-181 fija SSID.1/SSID.3 (las del EX511). Otro modelo puede
    numerar distinto: ahi el mapa apunta a algo inexistente y la ficha salia vacia."""
    doc = ex511()
    wifi = doc["Device"]["WiFi"]
    wifi["SSID"] = {"4": {"SSID": v("RED-2G", True), "LowerLayers": v("Device.WiFi.Radio.1.")},
                    "5": {"SSID": v("RED-5G", True), "LowerLayers": v("Device.WiFi.Radio.2.")}}
    wifi["AccessPoint"] = {"4": {"SSIDReference": v("Device.WiFi.SSID.4.")},
                           "5": {"SSIDReference": v("Device.WiFi.SSID.5.")}}
    eff = effective_params(pick_map(doc), doc)
    assert eff["wifi_2g_ssid"][0] == "Device.WiFi.SSID.4.SSID"
    assert eff["wifi_5g_ssid"][0] == "Device.WiFi.SSID.5.SSID"
    assert eff["wifi_5g_password"][0] == "Device.WiFi.AccessPoint.5.Security.KeyPassphrase"


def test_la_ficha_del_equipo_muestra_la_wan_deducida(client, fake, admin_h):
    from conftest import ISP_DEV
    fake.devices[ISP_DEV] = {**fake.devices[ISP_DEV], **xx530()}
    del fake.devices[ISP_DEV]["InternetGatewayDevice"]          # equipo TR-181 puro
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["wan_ip"] == "198.51.100.2"
    assert st["profile"]["wan"] == "7"
    assert "fuera de la LAN" in st["profile"]["evidencia"]["wan"]
