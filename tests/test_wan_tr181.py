"""WAN en TR-181: poner DHCP o IP estatica sin dejar el equipo incomunicado.

Lo aprendido en el EX511: hay que mandar el parametro estandar y el propietario
`X_TP_*` en la misma tarea (si no, el equipo revierte), el gateway va en la tabla
de rutas y no en la interfaz, y nunca se escribe una ruta que el equipo no tenga.
"""
import pytest

from app.treeprofile import WanNoSoportada, wan_write_values
from conftest import ISP_DEV
from test_perfil_derivado import ex511, v, xx530


def tplink_completo():
    """EX511 real: ademas de lo estandar expone X_TP_* y el relay DNS."""
    doc = ex511()
    iface = doc["Device"]["IP"]["Interface"]["4"]
    iface["X_TP_ConnType"] = v("DHCP", True)
    iface["X_TP_SetDefaultGateway"] = v(False, True)
    doc["Device"]["DNS"] = {"Relay": {"Forwarding": {
        "1": {"DNSServer": v("", True), "Enable": v(False, True)},
        "3": {"DNSServer": v("9.9.9.9", True), "Enable": v(True, True)},
    }}}
    return doc


def _paths(values):
    return {v[0]: v[1] for v in values}


def test_dhcp_manda_tambien_el_parametro_propietario():
    values, _notas = wan_write_values(tplink_completo(), "dhcp")
    p = _paths(values)
    assert p["Device.IP.Interface.4.IPv4Address.1.AddressingType"] == "DHCP"
    assert p["Device.IP.Interface.4.X_TP_ConnType"] == "DHCP"   # si no, el equipo revierte


def test_estatico_escribe_el_gateway_en_la_tabla_de_rutas():
    values, notas = wan_write_values(tplink_completo(), "static", ip="198.51.100.9",
                                     mask="255.255.255.248", gateway="198.51.100.1",
                                     dns=["9.9.9.9", "1.0.0.1"], mtu=1480)
    p = _paths(values)
    assert p["Device.IP.Interface.4.IPv4Address.1.IPAddress"] == "198.51.100.9"
    assert p["Device.Routing.Router.1.IPv4Forwarding.3.GatewayIPAddress"] == "198.51.100.1"
    assert p["Device.Routing.Router.1.IPv4Forwarding.3.Enable"] is True
    assert p["Device.DNS.Relay.Forwarding.3.DNSServer"] == "9.9.9.9,1.0.0.1"   # el habilitado
    assert p["Device.IP.Interface.4.MaxMTUSize"] == 1480
    assert any("gateway en" in n for n in notas)


def test_nunca_se_escribe_una_ruta_que_el_equipo_no_tiene():
    """Mandar paths inexistentes genera faults en el ACS."""
    values, _n = wan_write_values(ex511(), "dhcp")        # este arbol no tiene X_TP_*
    assert all("X_TP_" not in v[0] for v in values)


@pytest.mark.parametrize("modo", ["dhcp", "static"])
def test_con_el_arbol_a_medias_no_se_toca_la_wan(modo):
    """En un equipo recien vinculado el ACS no conoce los parametros de la WAN:
    se niega explicando que hay que refrescar, en vez de mandar rutas a ciegas."""
    with pytest.raises(WanNoSoportada) as e:
        wan_write_values(xx530(), modo, ip="198.51.100.9",
                         mask="255.255.255.0", gateway="198.51.100.1")
    assert "Actualizar" in str(e.value)


def test_dhcp_funciona_aunque_no_haya_tabla_de_rutas():
    """Para DHCP basta el modo de direccionamiento; el gateway lo pone el servidor."""
    doc = tplink_completo()
    del doc["Device"]["Routing"]
    doc["Device"]["WiFi"]["Radio"]["1"]["OperatingFrequencyBand"] = v("2.4GHz")
    values, _n = wan_write_values(doc, "dhcp")
    assert _paths(values)["Device.IP.Interface.4.IPv4Address.1.AddressingType"] == "DHCP"


def test_equipo_sin_wan_identificable_se_niega():
    doc = {"Device": {"DeviceInfo": {"UpTime": v(10)}}}
    with pytest.raises(WanNoSoportada) as e:
        wan_write_values(doc, "dhcp")
    assert "interfaz WAN" in str(e.value)


# ---------- por la API, con las guardas de siempre ----------

def _tr181(fake, doc):
    fake.devices[ISP_DEV] = {"_id": ISP_DEV, "_tags": ["ISP-A"],
                             "_lastInform": "2026-09-26T21:00:00.000Z",
                             "_deviceId": {"_Manufacturer": "TP-Link", "_ProductClass": "Device2"},
                             **doc}


def test_la_api_aplica_dhcp_en_tr181(client, fake, isp_h):
    _tr181(fake, tplink_completo())
    r = client.put(f"/devices/{ISP_DEV}/wan", headers=isp_h, json={"mode": "dhcp"})
    assert r.status_code == 200, r.text
    enviados = _paths([v for d, n, p in fake.tasks if n == "setParameterValues" for v in p])
    assert enviados["Device.IP.Interface.4.X_TP_ConnType"] == "DHCP"


def test_la_api_no_deja_saltar_a_otra_red(client, fake, isp_h):
    """La IP actual es 198.51.100.3/29: cambiar a otra red dejaria el equipo sin ACS."""
    _tr181(fake, tplink_completo())
    r = client.put(f"/devices/{ISP_DEV}/wan", headers=isp_h,
                   json={"mode": "static", "ip": "203.0.113.9", "mask": "255.255.255.0",
                         "gateway": "203.0.113.1"})
    assert r.status_code == 400 and "otra red" in r.json()["detail"]
    assert fake.tasks == []          # no se mando nada al equipo


def test_la_api_rechaza_gateway_fuera_de_la_subred(client, fake, isp_h):
    _tr181(fake, tplink_completo())
    r = client.put(f"/devices/{ISP_DEV}/wan", headers=isp_h,
                   json={"mode": "static", "ip": "198.51.100.9", "mask": "255.255.255.248",
                         "gateway": "198.51.100.200"})
    assert r.status_code == 400 and "gateway" in r.json()["detail"].lower()
    assert fake.tasks == []


def test_la_api_explica_que_falta_refrescar(client, fake, isp_h):   # noqa: D401
    _tr181(fake, xx530())
    r = client.put(f"/devices/{ISP_DEV}/wan", headers=isp_h,
                   json={"mode": "static", "ip": "198.51.100.9", "mask": "255.255.255.0",
                         "gateway": "198.51.100.1"})
    assert r.status_code == 400 and "Actualizar" in r.json()["detail"]
    assert fake.tasks == []
