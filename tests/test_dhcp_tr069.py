"""Generador del script DHCP (Option 43) para MikroTik.

Lo que importa aqui es la codificacion byte a byte y las guardas: un script que
"parece bien" pero entrega una opcion 43 mal formada deja el TR-069 mudo y
cuesta mucho darse cuenta.
"""
import pytest

from app.dhcp_tr069 import DhcpInvalido, generar, option43_tlv, option125, validar_red

URL = "http://10.99.99.5:7547/"
RED = {"cidr": "100.125.125.0/24", "gateway": "100.125.125.1",
       "pool_from": "100.125.125.10", "pool_to": "100.125.125.200"}


def test_el_tlv_lleva_subopcion_longitud_y_url():
    """La URL de ejemplo mide 23 bytes: 0x17. Es el valor que ya usa el router."""
    assert option43_tlv(URL) == "0x0117" + URL.encode().hex()
    assert option43_tlv(URL) == "0x0117687474703a2f2f31302e39392e39392e353a373534372f"


def test_la_longitud_se_recalcula_con_la_url():
    corta = option43_tlv("http://a.b/")
    assert corta[:6] == "0x010b"                      # 11 bytes


def test_una_url_que_no_cabe_se_rechaza():
    with pytest.raises(DhcpInvalido) as e:
        option43_tlv("http://" + "a" * 300)
    assert "no cabe" in str(e.value)


def test_la_opcion_125_envuelve_el_enterprise_del_bbf():
    v = option125(URL)
    assert v.startswith("0x00000de9")                 # 3561
    assert v[10:12] == f"{len(URL) + 2:02x}"          # longitud del bloque
    assert v.endswith(URL.encode().hex())


# ---------- guardas de red ----------

@pytest.mark.parametrize("red,error", [
    ({"cidr": "no-es-red"}, "no valida"),
    ({"cidr": "10.0.0.0/24", "gateway": "10.0.1.1"}, "no pertenece"),
    ({"cidr": "10.0.0.0/24", "gateway": "10.0.0.1", "pool_from": "10.0.0.10"}, "rango completo"),
    ({"cidr": "10.0.0.0/24", "pool_from": "10.0.0.10", "pool_to": "10.9.9.9"}, "se sale"),
    ({"cidr": "10.0.0.0/24", "pool_from": "10.0.0.200", "pool_to": "10.0.0.10"}, "al reves"),
    ({"cidr": "10.0.0.0/24", "gateway": "10.0.0.50",
      "pool_from": "10.0.0.10", "pool_to": "10.0.0.100"}, "dentro del pool"),
])
def test_las_redes_incoherentes_se_rechazan(red, error):
    with pytest.raises(DhcpInvalido) as e:
        validar_red(red)
    assert error in str(e.value)


def test_no_se_admite_la_misma_red_dos_veces():
    with pytest.raises(DhcpInvalido) as e:
        generar(URL, [RED, dict(RED)])
    assert "repetida" in str(e.value)


def test_la_url_tiene_que_ser_http():
    with pytest.raises(DhcpInvalido):
        generar("10.99.99.5:7547", [RED])


# ---------- el script ----------

def test_el_script_asigna_a_redes_existentes_y_no_crea_ninguna():
    """Crear redes desde un script es la forma facil de romper el DHCP de un ISP."""
    s = generar(URL, [RED])["script"]
    assert 'set [find address="100.125.125.0/24"] dhcp-option-set=tr069-genieacs-tlv' in s
    assert "/ip dhcp-server network\nadd " not in s


def test_el_script_trae_su_bloque_para_deshacer():
    s = generar(URL, [RED])["script"]
    assert "--- Deshacer" in s
    assert '#   set [find address="100.125.125.0/24"] dhcp-option-set=""' in s
    assert '# /ip dhcp-server option remove [find comment~"tr069-genieacs"]' in s


def test_v7_con_ambas_codificaciones_usa_un_matcher_por_option_60():
    s = generar(URL, [RED], routeros="7", codificacion="ambas")["script"]
    assert "/ip dhcp-server matcher" in s
    assert 'code=60 matching-type=substring value="dslforum.org"' in s
    assert "option-set=tr069-genieacs-tlv" in s
    # al resto se le entrega la plana por la red
    assert "dhcp-option-set=tr069-genieacs-plana" in s


def test_v6_no_admite_las_dos_codificaciones_y_lo_explica():
    """En v6 no hay matchers: mejor negarse que generar algo que no funciona."""
    with pytest.raises(DhcpInvalido) as e:
        generar(URL, [RED], routeros="6", codificacion="ambas")
    assert "matchers" in str(e.value)


def test_v6_con_una_sola_codificacion_si_genera_script():
    s = generar(URL, [RED], routeros="6", codificacion="plana")["script"]
    assert "matcher" not in s
    assert f"value=\"'{URL}'\"" in s


def test_cada_linea_es_un_comando_o_un_comentario():
    """Un salto de linea mal puesto convierte el script en un error de sintaxis."""
    s = generar(URL, [RED, {"cidr": "10.55.55.0/30", "gateway": "10.55.55.1"}],
                codificacion="ambas")["script"]
    for linea in s.splitlines():
        if not linea.strip():
            continue
        assert linea.startswith(("#", "/", "add ", "set ")), linea


def test_avisa_de_lo_que_el_script_no_puede_garantizar():
    r = generar(URL, [RED], routeros="6", codificacion="tlv")
    assert any("matchers" in a for a in r["avisos"])
    assert any("ya existen" in a for a in r["avisos"])
    r = generar("https://acs.example.com:7547/", [RED])
    assert any("HTTPS" in a for a in r["avisos"])


def test_los_valores_se_devuelven_para_poder_comprobarlos():
    r = generar(URL, [RED])
    assert r["valores"]["option43_tlv"].startswith("0x0117")
    assert r["valores"]["option43_plana"] == URL
