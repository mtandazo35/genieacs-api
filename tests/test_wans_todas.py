"""Todas las WAN configuradas de un equipo, no solo por la que sale el trafico.

El perfil deduce UNA wan (la de la ruta por defecto) y eso contesta "por donde
sale este equipo". Otra pregunta distinta es "que WAN tiene configuradas", que
es la que se compara con lo que el ISP quiso aprovisionar: un XX530 de la flota
tiene la de datos con su VLAN, otra levantada sin IP, una apagada, la de PPPoE
y la del USB. Ver solo la activa escondia justo eso.

El arbol de estas pruebas copia la FORMA del equipo real (IP.Interface sobre
Ethernet.VLANTermination, el cliente DHCP aparte, la ruta por defecto en
Routing.Router) con direcciones de documentacion (RFC 5737).
"""
import pytest

from app.treeprofile import wans


def v(x, w=False):
    return {"_value": x, "_writable": w, "_type": "xsd:string"}


def xx530():
    """Cuatro WAN sobre VLAN + una PPPoE, como el XX530: solo una lleva trafico."""
    return {"_id": "LAB-XX530-0001", "_deviceId": {"_Manufacturer": "TP-Link",
                                                   "_ProductClass": "XX530"},
            "Device": {
        "IP": {"Interface": {
            # LAN: su IP cae en la red del pool DHCP
            "1": {"Name": v("br0"), "Status": v("Up"),
                  "LowerLayers": v("Device.Ethernet.Link.1."),
                  "IPv4Address": {"1": {"IPAddress": v("192.168.1.1"),
                                        "AddressingType": v("Static")}}},
            # PPPoE sin conectar
            "2": {"Name": v(""), "Status": v("Down"),
                  "LowerLayers": v("Device.PPP.Interface.1."),
                  "IPv4Address": {"1": {"IPAddress": v("0.0.0.0"),
                                        "AddressingType": v("IPCP")}}},
            # la WAN de datos: VLAN 100, con IP y ruta por defecto
            "7": {"Name": v("nas3_100"), "Status": v("Up"),
                  "LowerLayers": v("Device.Ethernet.VLANTermination.3."),
                  "IPv4Address": {"1": {"IPAddress": v("203.0.113.20"),
                                        "AddressingType": v("DHCP")}}},
            # levantada, con cliente DHCP activo, pero sin IP todavia
            "6": {"Name": v("nas1_11"), "Status": v("Up"),
                  "LowerLayers": v("Device.Ethernet.VLANTermination.1."),
                  "IPv4Address": {"1": {"IPAddress": v("0.0.0.0"),
                                        "AddressingType": v("DHCP")}}},
            # configurada pero apagada
            "9": {"Name": v("nas5_1"), "Status": v("Down"),
                  "LowerLayers": v("Device.Ethernet.VLANTermination.5."),
                  "IPv4Address": {"1": {"IPAddress": v("0.0.0.0"),
                                        "AddressingType": v("DHCP")}}},
        }},
        "Ethernet": {"VLANTermination": {
            "1": {"Name": v("nas1_11"), "VLANID": v(11), "Status": v("Up")},
            "3": {"Name": v("nas3_100"), "VLANID": v(100), "Status": v("Up")},
            "5": {"Name": v("nas5_1"), "VLANID": v(1), "Status": v("Up")},
        }},
        "PPP": {"Interface": {"1": {"Username": v("soporte1@isp"), "Status": v("Down"),
                                    "ConnectionStatus": v("Disconnected")}}},
        "DHCPv4": {
            "Client": {
                "4": {"Interface": v("Device.IP.Interface.6."), "Status": v("Enabled"),
                      "IPRouters": v("0.0.0.0"), "DNSServers": v("0.0.0.0,0.0.0.0")},
                "6": {"Interface": v("Device.IP.Interface.7."), "Status": v("Enabled"),
                      "IPRouters": v("203.0.113.1"), "DNSServers": v("192.0.2.53,0.0.0.0")},
                "7": {"Interface": v("Device.IP.Interface.9."), "Status": v("Disabled"),
                      "IPRouters": v(""), "DNSServers": v("")},
            },
            "Server": {"Pool": {"1": {"MinAddress": v("192.168.1.100"),
                                      "MaxAddress": v("192.168.1.200")}}},
        },
        "Routing": {"Router": {"1": {"IPv4Forwarding": {
            "7": {"Interface": v("Device.IP.Interface.7."), "Enable": v("True"),
                  "GatewayIPAddress": v("203.0.113.1"), "DestIPAddress": v("")},
            "8": {"Interface": v("Device.IP.Interface.9."), "Enable": v("False"),
                  "GatewayIPAddress": v("0.0.0.0"), "DestIPAddress": v("")},
        }}}},
    }}


def por_instancia(filas):
    return {f["instancia"]: f for f in filas}


def test_se_ven_todas_las_wan_no_solo_la_que_lleva_trafico():
    """Era el problema: el panel mostraba una y el equipo tiene cuatro."""
    filas = wans(xx530())
    assert set(por_instancia(filas)) == {"2", "6", "7", "9"}


def test_la_lan_no_es_una_wan():
    """br0 tiene IP en la red del pool DHCP: es la LAN, no una salida."""
    assert "1" not in por_instancia(wans(xx530()))


def test_cada_wan_trae_su_vlan():
    """Una VLAN equivocada es el fallo de aprovisionamiento mas comun, y el
    VLANID no esta en la interfaz: cuelga de la terminacion de abajo."""
    porif = por_instancia(wans(xx530()))
    assert (porif["7"]["vlan"], porif["6"]["vlan"], porif["9"]["vlan"]) == ("100", "11", "1")
    assert porif["2"]["vlan"] is None          # la PPPoE no va sobre VLAN aqui


def test_la_activa_va_marcada_y_la_primera():
    """La que tiene la ruta por defecto habilitada; y sale arriba porque es la
    que contesta 'por donde sale este equipo'."""
    filas = wans(xx530())
    assert filas[0]["instancia"] == "7" and filas[0]["activa"] is True
    assert [f["activa"] for f in filas[1:]] == [False, False, False]


def test_una_wan_configurada_y_caida_tambien_sale():
    """Es la que hay que ver: esta puesta y no levanta."""
    caida = por_instancia(wans(xx530()))["9"]
    assert caida["estado"] == "Down" and caida["vlan"] == "1" and caida["ip"] is None


def test_el_gateway_y_los_dns_salen_del_cliente_dhcp():
    """No estan en la interfaz: hay que cruzarlos por Device.DHCPv4.Client.N.Interface."""
    porif = por_instancia(wans(xx530()))
    assert porif["7"]["gateway"] == "203.0.113.1" and porif["7"]["dns"] == "192.0.2.53"
    # los 0.0.0.0 de un enlace sin negociar no se pintan como si fueran datos
    assert porif["6"]["gateway"] is None and porif["6"]["dns"] is None
    assert porif["6"]["estado_cliente"] == "Enabled"
    assert porif["9"]["estado_cliente"] == "Disabled"


def test_la_pppoe_se_reconoce_y_trae_su_usuario():
    """IPCP es PPP negociando la IP; para el operador eso es una WAN PPPoE."""
    ppp = por_instancia(wans(xx530()))["2"]
    assert ppp["modo"] == "PPPoE" and ppp["usuario"] == "soporte1@isp"
    assert ppp["estado"] == "Disconnected"     # el del PPP, no el "Down" de la IP


def test_una_rama_a_medias_se_dice_en_vez_de_inventar_una_wan():
    """El ACS a veces trae solo la IP de una interfaz. Eso no es 'una WAN sin
    modo': es que falta leer el arbol, y se dice."""
    doc = xx530()
    doc["Device"]["IP"]["Interface"]["10"] = {"IPv4Address": {"1": {"IPAddress": v("198.51.100.9")}}}
    a_medias = por_instancia(wans(doc))["10"]
    assert a_medias["incompleta"] is True
    assert a_medias["modo"] is None and a_medias["ip"] == "198.51.100.9"
    # y las que si estan completas no se marcan
    assert por_instancia(wans(doc))["7"]["incompleta"] is False


def test_sin_arbol_no_se_inventa_nada():
    assert wans({"_id": "x", "Device": {}}) == []
    assert wans({"_id": "x"}) == []


# ---------- TR-098: cada conexion es una WAN ----------

def tr098():
    conn = {"WANIPConnection": {"1": {
                "Name": v("internet"), "ConnectionStatus": v("Connected"),
                "ExternalIPAddress": v("203.0.113.30"), "DefaultGateway": v("203.0.113.1"),
                "DNSServers": v("192.0.2.53"), "X_TP_VLANIDMark": v(100),
                "AddressingType": v("DHCP")}},
            "WANPPPConnection": {"1": {
                "Name": v("voip"), "ConnectionStatus": v("Disconnected"),
                "ExternalIPAddress": v(""), "Username": v("voip@isp"),
                "X_TP_VLANIDMark": v(33)}}}
    return {"_id": "LAB-TR098-0001", "InternetGatewayDevice": {
        "Layer3Forwarding": {"DefaultConnectionService": v(
            "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1")},
        "WANDevice": {"1": {"WANConnectionDevice": {"1": conn}}}}}


def test_en_tr098_cada_conexion_es_una_wan():
    filas = wans(tr098())
    assert len(filas) == 2
    assert filas[0]["modo"] == "DHCP" and filas[0]["activa"] is True
    assert filas[0]["vlan"] == "100" and filas[0]["ip"] == "203.0.113.30"
    voip = filas[1]
    assert voip["modo"] == "PPPoE" and voip["usuario"] == "voip@isp"
    assert voip["vlan"] == "33" and voip["activa"] is False


@pytest.mark.parametrize("campo", ["ip", "gateway", "dns"])
def test_los_ceros_no_se_pintan_como_datos(campo):
    """Un equipo sin enlace reporta 0.0.0.0; mostrarlo como si fuera una IP
    hace perder el tiempo a quien mira la ficha."""
    porif = por_instancia(wans(xx530()))
    assert porif["9"][campo] is None


@pytest.mark.parametrize("vid", ["0", 0, "-1"])
def test_una_vlan_cero_no_es_una_vlan(vid):
    """0 (y -1 en TR-098) significan "sin etiquetar": pintarlo como VLAN 0 manda
    a buscar en el router una VLAN que no existe. Visto en un equipo real de la
    flota, que reporta VLANID 0 en su interfaz de datos."""
    doc = xx530()
    doc["Device"]["Ethernet"]["VLANTermination"]["3"]["VLANID"] = v(vid)
    assert por_instancia(wans(doc))["7"]["vlan"] is None

    doc98 = tr098()
    conn = doc98["InternetGatewayDevice"]["WANDevice"]["1"]["WANConnectionDevice"]["1"]
    conn["WANIPConnection"]["1"]["X_TP_VLANIDMark"] = v(vid)
    assert wans(doc98)[0]["vlan"] is None
