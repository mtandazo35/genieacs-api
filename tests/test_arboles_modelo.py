"""Base de arboles TR-069 por modelo.

El ACS guarda el arbol de cada equipo, pero lo que hace falta para trabajar es
saber QUE rutas expone un MODELO: un equipo recien adoptado trae 34 parametros y
otro del mismo modelo, ya refrescado, miles. Sin una referencia del modelo no se
puede distinguir "este equipo no lo soporta" de "a este equipo le falta arbol".

Lo que se guarda son rutas y si son escribibles. Nunca valores: en un arbol real
van el SSID y la clave WiFi del abonado, y en una base por modelo no hacen nada
mas que filtrar datos de clientes a un sitio donde nadie los espera.
"""
import json

import pytest

from app import catalog, db
from conftest import ISP_DEV, OTHER_DEV
from test_perfil_derivado import ex511, v

TERCER_DEV = "00A0B1-ROUTER-ISPA0002"

# valores bien reconocibles: si alguno aparece en la BD del modelo, se estan
# guardando datos del abonado
SSID_ABONADO = "SSID-DEL-ABONADO-4F2A"
CLAVE_ABONADO = "clave-del-abonado-9Z7X"


def _equipo(fake, dev_id, doc, modelo="EX511", fw="1.0.0", fabricante="TP-Link", tags=("ISP-A",)):
    """Deja en el ACS falso un equipo TR-181 con identidad conocida."""
    arbol = {k: dict(vv) for k, vv in doc.items()}
    arbol["Device"]["DeviceInfo"] = {"ModelName": v(modelo), "SoftwareVersion": v(fw)}
    previo = fake.devices.get(dev_id) or {}
    fake.devices[dev_id] = {
        "_id": dev_id, "_tags": previo.get("_tags") or list(tags),
        "_lastInform": "2026-09-27T21:00:00.000Z",
        "_deviceId": {"_Manufacturer": fabricante, "_ProductClass": "Device2",
                      "_SerialNumber": dev_id[-6:]},
        **arbol,
    }
    return fake.devices[dev_id]


def con_datos_del_abonado():
    """El mismo EX511 con el SSID y la clave WiFi que tendria en casa del cliente."""
    doc = ex511()
    doc["Device"]["WiFi"]["SSID"]["1"]["SSID"] = v(SSID_ABONADO, True)
    doc["Device"]["WiFi"]["AccessPoint"]["1"]["Security"] = {"KeyPassphrase": v(CLAVE_ABONADO, True)}
    return doc


def a_medias():
    """Equipo recien adoptado: el ACS solo tiene lo del Inform, sin WiFi ni rutas."""
    return {"Device": {
        "ManagementServer": {"PeriodicInformInterval": v(300, True),
                             "ConnectionRequestURL": v("http://203.0.113.9:7547/")},
        "IP": {"Interface": {"1": {"IPv4Address": {"1": {"IPAddress": v("192.168.0.1")}}}}},
    }}


def con_ipv6():
    """El mismo modelo con IPv6 encendido: expone una rama que el otro no tiene."""
    doc = ex511()
    doc["Device"]["IPv6rd"] = {"InterfaceSetting": {
        "1": {"BorderRelayIPv4Address": v("203.0.113.1", True), "Enable": v(True, True)}}}
    return doc


def _fila(key):
    fila = db.get_model_tree(key)
    assert fila is not None, f"no hay arbol guardado de {key}"
    return fila


def _rutas(key):
    return json.loads(_fila(key)["paths"])


KEY = "TP-Link|Device2|EX511|1.0.0"


# ---------- se aprende al abrir la ficha ----------

def test_al_abrir_la_ficha_queda_guardado_el_arbol_del_modelo(client, fake, admin_h):
    """Nadie tiene que pedir nada: con abrir la ficha de un equipo ya hay
    referencia del modelo para todos los demas iguales."""
    doc = _equipo(fake, ISP_DEV, ex511())
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["profile"]["key"] == KEY

    fila = _fila(KEY)
    esperadas = catalog.rutas_de(doc)
    assert fila["root"] == "Device"
    assert fila["n_params"] == len(esperadas) == len(_rutas(KEY))
    assert fila["last_device"] == ISP_DEV
    assert "Device.WiFi.Radio.1.OperatingFrequencyBand" in _rutas(KEY)


def test_se_guarda_si_la_ruta_es_escribible(client, fake, admin_h):
    """Sin saber si una ruta se puede escribir, la base no sirve para decidir si
    el panel puede ofrecer el campo o solo mostrarlo."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    rutas = _rutas(KEY)
    assert rutas["Device.WiFi.Radio.1.Channel"] is True
    assert rutas["Device.WiFi.SSID.1.LowerLayers"] is False


# ---------- nunca valores ----------

def test_no_se_guarda_ningun_valor_del_abonado(client, fake, admin_h):
    """LA prueba de esta base: guarda que parametros expone un modelo, no lo que
    tiene configurado el cliente. Ni el SSID ni la clave WiFi entran en la fila."""
    _equipo(fake, ISP_DEV, con_datos_del_abonado())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)

    fila_completa = json.dumps(_fila(KEY), ensure_ascii=False)
    assert SSID_ABONADO not in fila_completa
    assert CLAVE_ABONADO not in fila_completa
    # la ruta si esta: lo que interesa es que el modelo tiene donde poner la clave
    assert "Device.WiFi.AccessPoint.1.Security.KeyPassphrase" in _rutas(KEY)


def test_el_endpoint_tampoco_devuelve_valores(client, fake, admin_h):
    """Si el panel pudiera leer valores desde /trees, la base se convertiria en
    un listado de claves WiFi de la flota."""
    _equipo(fake, ISP_DEV, con_datos_del_abonado())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    r = client.get(f"/trees/{KEY}?limit=0", headers=admin_h)
    assert r.status_code == 200
    assert CLAVE_ABONADO not in r.text and SSID_ABONADO not in r.text
    assert set(r.json()["paths"][0]) == {"path", "writable"}


# ---------- se unen, no se sobrescriben ----------

def test_un_arbol_a_medias_no_reduce_lo_guardado(client, fake, admin_h):
    """El caso real: abrir la ficha de un equipo recien adoptado no puede tirar a
    la basura el arbol completo que aporto otro del mismo modelo."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    completo = set(_rutas(KEY))

    _equipo(fake, OTHER_DEV, a_medias(), tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    despues = set(_rutas(KEY))
    assert completo <= despues, f"se perdieron rutas: {sorted(completo - despues)}"
    assert _fila(KEY)["n_params"] == len(despues) >= len(completo)
    assert "Device.WiFi.Radio.1.OperatingFrequencyBand" in despues


def test_una_rama_que_el_otro_no_tenia_se_suma(client, fake, admin_h):
    """Dos equipos del mismo modelo exponen cosas distintas segun lo que tengan
    encendido: la union es la que describe de que es capaz el modelo."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    sin_ipv6 = _fila(KEY)["n_params"]
    assert not any("IPv6rd" in p for p in _rutas(KEY))

    _equipo(fake, OTHER_DEV, con_ipv6(), tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    rutas = _rutas(KEY)
    assert "Device.IPv6rd.InterfaceSetting.1.BorderRelayIPv4Address" in rutas
    assert _fila(KEY)["n_params"] == sin_ipv6 + 2 == len(rutas)


def test_dos_equipos_cuentan_dos_y_el_mismo_no_cuenta_dos_veces(client, fake, admin_h):
    """El numero de equipos que aportaron dice cuanta confianza merece la union;
    refrescar la ficha del mismo equipo no la infla."""
    _equipo(fake, ISP_DEV, ex511())
    for _ in range(3):
        client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert [d["device_id"] for d in db.model_tree_devices(KEY)] == [ISP_DEV]

    _equipo(fake, OTHER_DEV, a_medias(), tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    equipos = {d["device_id"]: d["n_params"] for d in db.model_tree_devices(KEY)}
    assert set(equipos) == {ISP_DEV, OTHER_DEV}
    assert equipos[OTHER_DEV] < equipos[ISP_DEV]     # cada uno con SU tamaño, no la union


def test_cada_firmware_tiene_su_arbol(client, fake, admin_h):
    """Un firmware nuevo puede añadir o quitar parametros: no se mezclan."""
    _equipo(fake, ISP_DEV, ex511(), fw="1.0.0")
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, ex511(), fw="2.0.0", tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    claves = sorted(f["key"] for f in db.list_model_trees())
    assert claves == ["TP-Link|Device2|EX511|1.0.0", "TP-Link|Device2|EX511|2.0.0"]


# ---------- el listado ----------

def test_el_listado_resume_por_modelo_sin_arrastrar_las_rutas(client, fake, admin_h):
    """Son cientos o miles de rutas por fila: en una lista no pintan nada."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    filas = client.get("/trees", headers=admin_h).json()
    assert len(filas) == 1
    f = filas[0]
    assert "paths" not in f
    assert f["key"] == KEY and f["root"] == "Device" and f["model"] == "EX511"
    assert f["devices"] == 1 and f["n_params"] > 30


def test_un_equipo_con_el_arbol_completo_no_marca_incompleto(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert client.get("/trees", headers=admin_h).json()[0]["incompleto"] is False


def test_se_marca_incompleto_cuando_ningun_equipo_llega_a_la_union(client, fake, admin_h):
    """Si la union es mayor que el mayor de los equipos, lo guardado no lo ha
    visto nunca un solo equipo: hay que decirlo antes de tomarlo por dogma."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, con_ipv6(), tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    # el segundo trae IPv6 pero el primero no; ninguno tiene la union entera
    del fake.devices[OTHER_DEV]["Device"]["WiFi"]
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)

    f = client.get("/trees", headers=admin_h).json()[0]
    assert f["devices"] == 2 and f["max_equipo"] < f["n_params"]
    assert f["incompleto"] is True


# ---------- el detalle ----------

def test_la_clave_con_barras_verticales_funciona_en_la_url(client, fake, admin_h):
    """La clave es fabricante|clase|modelo|firmware y viaja en el path: hay que
    poder abrirla tal cual, y tambien como la codifica el panel."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    directa = client.get(f"/trees/{KEY}", headers=admin_h)
    codificada = client.get("/trees/TP-Link%7CDevice2%7CEX511%7C1.0.0", headers=admin_h)
    assert directa.status_code == codificada.status_code == 200
    assert directa.json()["key"] == codificada.json()["key"] == KEY


def test_el_detalle_filtra_por_texto(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    r = client.get(f"/trees/{KEY}", headers=admin_h, params={"q": "wifi.ssid"}).json()
    assert r["total"] == len(r["paths"]) > 0
    assert all("WiFi.SSID" in p["path"] for p in r["paths"])   # busqueda sin mayusculas


def test_el_detalle_filtra_los_escribibles(client, fake, admin_h):
    """Para saber que se puede cambiar en un modelo sin tener el equipo delante."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    todas = client.get(f"/trees/{KEY}", headers=admin_h, params={"limit": 0}).json()
    solo_w = client.get(f"/trees/{KEY}", headers=admin_h,
                        params={"escribibles": "true", "limit": 0}).json()
    assert 0 < solo_w["total"] < todas["total"]
    assert all(p["writable"] for p in solo_w["paths"])


def test_el_limite_avisa_de_que_hay_mas_y_cero_lo_trae_todo(client, fake, admin_h):
    """Un arbol TR-181 refrescado son miles de rutas: la vista recorta y lo dice,
    y para descargar se piden todas con limit=0."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    recortado = client.get(f"/trees/{KEY}", headers=admin_h, params={"limit": 5}).json()
    assert len(recortado["paths"]) == 5
    assert recortado["truncado"] is True and recortado["total"] > 5

    todas = client.get(f"/trees/{KEY}", headers=admin_h, params={"limit": 0}).json()
    assert len(todas["paths"]) == todas["total"] == todas["n_params"]
    assert todas["truncado"] is False


def test_el_detalle_dice_que_equipos_aportaron(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    r = client.get(f"/trees/{KEY}", headers=admin_h).json()
    assert [e["device_id"] for e in r["equipos"]] == [ISP_DEV]
    assert r["last_device"] == ISP_DEV and r["updated_at"]


# ---------- comparar un equipo con su modelo ----------

def test_comparar_dice_cuanto_arbol_le_falta_al_equipo(client, fake, admin_h):
    """El caso que motivo todo esto: un equipo recien adoptado con 34 parametros
    frente al arbol del modelo. No es otro modelo, es que le falta refrescar."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, a_medias(), tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)

    r = client.get(f"/trees/{KEY}", headers=admin_h,
                   params={"comparar_con": OTHER_DEV, "limit": 0}).json()
    c = r["comparacion"]
    assert c["device_id"] == OTHER_DEV
    assert c["tiene"] < c["del_modelo"]
    assert c["faltan"] == c["del_modelo"] - c["tiene"] > 0
    assert any("WiFi" in p for p in c["ejemplos"])      # se ve de que parte del arbol se trata


def test_comparar_un_equipo_completo_no_le_encuentra_nada_de_menos(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    c = client.get(f"/trees/{KEY}", headers=admin_h,
                   params={"comparar_con": ISP_DEV}).json()["comparacion"]
    assert c["faltan"] == 0 and c["ejemplos"] == []


def test_comparar_con_un_equipo_que_no_esta_en_el_acs_da_404(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    r = client.get(f"/trees/{KEY}", headers=admin_h, params={"comparar_con": "NO-EXISTE-0001"})
    assert r.status_code == 404


# ---------- permisos y modelos sin arbol ----------

def test_un_modelo_sin_arbol_guardado_da_404(client, admin_h):
    assert client.get("/trees/marca|clase|modelo|fw", headers=admin_h).status_code == 404


def test_la_base_de_arboles_es_solo_de_admin(client, fake, isp_h, admin_h):
    """Un ISP no tiene por que ver el arbol (ni los equipos) de otros ISP."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert client.get("/trees", headers=isp_h).status_code == 403
    assert client.get(f"/trees/{KEY}", headers=isp_h).status_code == 403


# ---------- una fila corrupta no puede tumbar las fichas ----------

# como puede quedar el JSON de la fila: una restauracion a medias, una edicion a
# mano, un `UPDATE` de una migracion que se quedo corta
BASURA = ["null", "42", '"una cadena"', '["Device.WiFi.SSID.1.SSID"]', "{no es json}", ""]


@pytest.mark.parametrize("basura", BASURA)
def test_una_fila_corrupta_no_revienta_la_ficha_y_el_arbol_se_rehace(client, fake, admin_h, basura):
    """Era un 500 en la ficha de TODOS los equipos de ese modelo: el arbol se lee
    al abrir cualquier ficha, asi que una sola fila mala dejaba el modelo entero
    sin panel. Lo guardado no es sagrado: si no se puede leer, se rehace."""
    doc = _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)      # se aprende bien
    with db.connect() as c:
        c.execute("UPDATE model_tree SET paths=?, n_params=999 WHERE key=?", (basura, KEY))

    r = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert r.status_code == 200, f"la ficha revienta con paths={basura!r}: {r.text[:200]}"
    assert r.json()["profile"]["key"] == KEY

    rutas = _rutas(KEY)
    assert rutas == catalog.rutas_de(doc)                          # rehecho desde el equipo
    assert _fila(KEY)["n_params"] == len(rutas)                    # y el contador cuadra
    assert client.get(f"/trees/{KEY}", headers=admin_h).status_code == 200


@pytest.mark.parametrize("basura", BASURA)
def test_el_detalle_de_un_arbol_corrupto_responde_en_vez_de_reventar(client, fake, admin_h, basura):
    """Mientras nadie abra una ficha de ese modelo, la fila sigue mala: el
    listado y el detalle tienen que poder pintarla igual."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    with db.connect() as c:
        c.execute("UPDATE model_tree SET paths=? WHERE key=?", (basura, KEY))

    assert client.get("/trees", headers=admin_h).status_code == 200
    r = client.get(f"/trees/{KEY}", headers=admin_h, params={"limit": 0})
    assert r.status_code == 200 and r.json()["paths"] == []


# ---------- olvidar el arbol de un modelo ----------

def test_borrar_el_arbol_lo_olvida_y_se_vuelve_a_aprender_al_abrir_una_ficha(client, fake, admin_h):
    """La union solo crece y el flag de escribible se queda pegado: borrar es la
    unica forma de rehacerla. Y no hay que hacer nada mas: se reaprende sola."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    antes = len(_rutas(KEY))
    assert [d["device_id"] for d in db.model_tree_devices(KEY)] == [ISP_DEV]

    r = client.delete(f"/trees/{KEY}", headers=admin_h)
    assert r.status_code == 200 and r.json() == {"ok": True, "key": KEY}
    assert db.get_model_tree(KEY) is None
    assert db.model_tree_devices(KEY) == []          # tambien quien aporto
    assert client.get(f"/trees/{KEY}", headers=admin_h).status_code == 404
    assert client.get("/trees", headers=admin_h).json() == []

    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert len(_rutas(KEY)) == antes
    assert [d["device_id"] for d in db.model_tree_devices(KEY)] == [ISP_DEV]


def test_borrar_el_arbol_se_lleva_la_basura_que_habia_entrado(client, fake, admin_h):
    """El motivo real: un equipo que reportaba mal su firmware metio rutas de otro
    modelo en la union, y de ahi no salian solas."""
    intruso = con_ipv6()
    intruso["Device"]["X_OTRO_MODELO"] = {"CosaQueNoExiste": v("x", True)}
    _equipo(fake, OTHER_DEV, intruso, tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    assert "Device.X_OTRO_MODELO.CosaQueNoExiste" in _rutas(KEY)

    client.delete(f"/trees/{KEY}", headers=admin_h)
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    rutas = _rutas(KEY)
    assert "Device.X_OTRO_MODELO.CosaQueNoExiste" not in rutas
    assert not any("IPv6rd" in p for p in rutas)


def test_borrar_un_arbol_que_no_existe_da_404(client, admin_h):
    assert client.delete("/trees/marca|clase|modelo|fw", headers=admin_h).status_code == 404


def test_borrar_solo_toca_el_modelo_pedido(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511(), fw="1.0.0")
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, ex511(), fw="2.0.0", tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)

    client.delete(f"/trees/{KEY}", headers=admin_h)
    assert [f["key"] for f in db.list_model_trees()] == ["TP-Link|Device2|EX511|2.0.0"]


def test_borrar_un_arbol_es_solo_de_admin(client, fake, isp_h, admin_h):
    """Borrar la referencia de un modelo afecta a la flota de todos los ISP."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert client.delete(f"/trees/{KEY}", headers=isp_h).status_code == 403
    assert db.get_model_tree(KEY) is not None       # y no se borro nada


# ---------- incompleto: si no se sabe, se dice ----------

def test_incompleto_es_nulo_cuando_no_consta_ningun_equipo(client, fake, admin_h):
    """Hay arbol guardado pero nadie sabe de que equipo salio (fila de aporte
    borrada, BD restaurada a medias): eso no es "completo", es "no consta".
    Decir false ahi es afirmar algo que no se ha comprobado."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    with db.connect() as c:
        c.execute("DELETE FROM model_tree_device WHERE key=?", (KEY,))

    f = client.get("/trees", headers=admin_h).json()[0]
    assert f["devices"] == 0 and f["max_equipo"] is None
    assert f["incompleto"] is None, "sin ningun equipo registrado no se sabe: no es false"


def test_los_tres_estados_de_incompleto_conviven(client, fake, admin_h):
    """false = un equipo trae la union entera; true = ninguno llega; null = no
    consta. Tres cosas distintas que antes se contaban como dos."""
    _equipo(fake, ISP_DEV, ex511(), fw="1.0.0")
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)

    _equipo(fake, OTHER_DEV, ex511(), fw="2.0.0", tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, con_ipv6(), fw="2.0.0", tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    del fake.devices[OTHER_DEV]["Device"]["IPv6rd"]
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)   # ya nadie tiene la union

    _equipo(fake, TERCER_DEV, ex511(), fw="3.0.0")
    client.get(f"/devices/{TERCER_DEV}/status", headers=admin_h)
    with db.connect() as c:
        c.execute("DELETE FROM model_tree_device WHERE key=?", ("TP-Link|Device2|EX511|3.0.0",))

    estados = {f["key"]: f["incompleto"] for f in client.get("/trees", headers=admin_h).json()}
    assert estados == {"TP-Link|Device2|EX511|1.0.0": False,
                       "TP-Link|Device2|EX511|2.0.0": True,
                       "TP-Link|Device2|EX511|3.0.0": None}


# ---------- la descarga no se lleva la flota ----------

def test_la_descarga_no_incluye_los_equipos(client, fake, admin_h):
    """limit=0 es el boton Descargar: ese archivo sale del servidor y acaba en un
    ticket o en un chat. Los seriales de los equipos que aportaron cruzan ISP y
    no hacen ninguna falta para saber que parametros expone un modelo."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, ex511(), tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)

    descarga = client.get(f"/trees/{KEY}", headers=admin_h, params={"limit": 0})
    assert descarga.status_code == 200
    assert "equipos" not in descarga.json()
    assert OTHER_DEV not in json.dumps(descarga.json()["paths"])

    # en la vista normal si: ahi es donde se mira si a un equipo le falta arbol
    vista = client.get(f"/trees/{KEY}", headers=admin_h).json()
    assert {e["device_id"] for e in vista["equipos"]} == {ISP_DEV, OTHER_DEV}


def test_filtrar_y_descargar_tampoco_trae_los_equipos(client, fake, admin_h):
    """Mismo limit=0 con q= y escribibles=: la descarga es la descarga."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    r = client.get(f"/trees/{KEY}", headers=admin_h,
                   params={"limit": 0, "q": "wifi", "escribibles": "true"}).json()
    assert "equipos" not in r and r["total"] > 0


# ---------- la raiz ----------

def test_la_raiz_guardada_no_se_pisa_con_un_equipo_que_no_la_trae(client):
    """Un doc sin raiz (equipo recien adoptado, proyeccion recortada del ACS) no
    puede borrar la raiz que ya se sabia: el panel decide con ella si el modelo es
    TR-181 o TR-098, y con root=NULL deja de saberlo."""
    k = "Marca|Clase|Modelo|1.0"
    db.merge_model_tree(k, "Device", {"Device.WiFi.SSID.1.SSID": True}, "DEV-COMPLETO")
    assert db.get_model_tree(k)["root"] == "Device"

    db.merge_model_tree(k, None, {"Device.ManagementServer.PeriodicInformInterval": True},
                        "DEV-A-MEDIAS")
    fila = db.get_model_tree(k)
    assert fila["root"] == "Device", "un equipo sin raiz borro la raiz del modelo"
    assert fila["last_device"] == "DEV-A-MEDIAS"            # lo demas si se actualiza
    assert fila["n_params"] == 2


def test_una_raiz_nueva_si_se_guarda_cuando_antes_no_habia(client):
    k = "Marca|Clase|Modelo|1.0"
    db.merge_model_tree(k, None, {"X.Y": False}, "DEV-1")
    assert db.get_model_tree(k)["root"] is None
    db.merge_model_tree(k, "InternetGatewayDevice", {"InternetGatewayDevice.DeviceInfo.UpTime": False},
                        "DEV-2")
    assert db.get_model_tree(k)["root"] == "InternetGatewayDevice"


# ---------- el panel ----------
_EST = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "static"
PANEL_JS = (_EST / "app.js").read_text(encoding="utf-8")
PANEL_HTML = (_EST / "index.html").read_text(encoding="utf-8")

IDS_CONTRATO = ["arb-list", "arb-refresh", "arb-modal", "arb-modal-titulo", "arb-buscar",
                "arb-solo-escribibles", "arb-total", "arb-paths", "arb-descargar", "arb-cerrar"]


def test_los_arboles_tienen_su_subpestana_en_aprovisionamiento():
    assert 'data-psub="arboles"' in PANEL_HTML
    assert 'data-ppanel="arboles"' in PANEL_HTML


def test_el_html_trae_todos_los_ids_del_contrato():
    faltan = [i for i in IDS_CONTRATO if f'id="{i}"' not in PANEL_HTML]
    assert faltan == [], f"ids del contrato que no estan en el HTML: {faltan}"


def test_cada_id_que_usa_el_js_existe_en_el_html():
    """Este cruce ya cazo un fallo real: el JS escribia en un id que el HTML no
    tenia, asi que el panel se quedaba en blanco sin un solo error visible."""
    import re
    usados = set(re.findall(r'#(arb-[\w-]+)', PANEL_JS))
    usados |= set(re.findall(r'getElementById\(["\'](arb-[\w-]+)', PANEL_JS))
    faltan = sorted(i for i in usados if f'id="{i}"' not in PANEL_HTML)
    assert faltan == [], f"el JS usa ids que el HTML no tiene: {faltan}"


def test_el_panel_carga_y_abre_arboles():
    assert "function loadArboles(" in PANEL_JS
    assert "function abrirArbol(" in PANEL_JS
    assert "/trees" in PANEL_JS


def test_al_entrar_en_la_subpestana_se_cargan_los_arboles():
    cuerpo = PANEL_JS[PANEL_JS.index("function mostrarSubProv"):]
    cuerpo = cuerpo[:cuerpo.index(chr(10) + "}")]
    assert "loadArboles()" in cuerpo, "mostrarSubProv deberia llamar a loadArboles()"


def test_la_clave_del_modelo_se_codifica_en_la_url():
    """La clave lleva '|' y puntos: sin encodeURIComponent la peticion se rompe."""
    import re
    usos = re.findall(r"/trees/\$\{([^}]*)\}", PANEL_JS)
    assert usos, "el panel no construye ninguna URL de /trees con la clave del modelo"
    crudas = [u for u in usos if "encodeURIComponent" not in u]
    assert crudas == [], f"claves sin codificar en la URL: {crudas}"


# ---------- lo que salio al revisar la primera version ----------

def test_la_descarga_no_lleva_ningun_serial(client, fake, admin_h):
    """Quitar la lista de equipos no bastaba: `last_device` tambien es un serial,
    y puede ser de otro ISP que el que se descarga el archivo."""
    _equipo(fake, ISP_DEV, ex511())
    key = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()["profile"]["key"]
    descarga = client.get(f"/trees/{key}?limit=0", headers=admin_h).json()
    assert "equipos" not in descarga and "last_device" not in descarga
    assert ISP_DEV not in json.dumps(descarga)
    # en la vista normal del panel si se ve quien aporto
    normal = client.get(f"/trees/{key}", headers=admin_h).json()
    assert normal["last_device"] == ISP_DEV and normal["equipos"]


def test_un_arbol_ilegible_se_dice_en_vez_de_parecer_un_modelo_sin_rutas(client, fake, admin_h):
    """Devolver paths=[] junto a n_params=4653 hacia pensar en un modelo vacio;
    lo que pasa es que la fila no se puede leer, y eso se dice."""
    _equipo(fake, ISP_DEV, ex511())
    key = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()["profile"]["key"]
    with db.connect() as c:
        c.execute("UPDATE model_tree SET paths='42', n_params=4653 WHERE key=?", (key,))
    r = client.get(f"/trees/{key}", headers=admin_h).json()
    assert r["ilegible"] is True and r["paths"] == [] and r["n_params"] == 0
    assert "rehara" in r["detail"]
    # y se rehace sola al leer un equipo de ese modelo
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    r2 = client.get(f"/trees/{key}", headers=admin_h).json()
    assert "ilegible" not in r2 and r2["n_params"] == len(catalog.rutas_de(fake.devices[ISP_DEV]))
