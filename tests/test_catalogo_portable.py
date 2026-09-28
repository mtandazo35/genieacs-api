"""Catalogo portable: llevar los modelos aprendidos a otra instalacion.

El caso real: el laboratorio lleva meses abriendo fichas y ya sabe que expone
cada modelo (rutas, perfil deducido, correcciones a mano). Produccion arranca en
blanco y no puede esperar a que un tecnico abra la ficha de cada CPE. Con
`GET /trees/export` se baja un archivo y con `POST /trees/import` se mete en la
otra instalacion.

Dos cosas dan miedo aqui y son las que se prueban a fondo:

- **El archivo cruza empresas.** Un catalogo se le pasa a otro ISP, va en un
  ticket o en un chat. No puede llevar NADA de la flota: ni ids de equipo, ni
  seriales, ni el ultimo que aporto, ni la lista de quien aporto que rama, ni
  ningun valor del arbol (el SSID y la clave WiFi del abonado estan ahi).
- **Importar no puede pisar lo local.** Lo deducido de un equipo real de ESTA
  flota manda sobre lo que traiga un archivo; y una correccion que un tecnico
  escribio a mano aqui no la borra un archivo de otro sitio.
"""
import json
import re

import pytest

from app import catalog, db
from conftest import ISP_DEV, OTHER_DEV
from test_arboles_modelo import (CLAVE_ABONADO, SSID_ABONADO, _equipo,
                                 con_datos_del_abonado, con_ipv6)
from test_perfil_derivado import ex511

KEY_A = "TP-Link|Device2|EX511|1.0.0"
KEY_B = "TP-Link|Device2|EX511|2.0.0"

# claves de un archivo que viene de otra instalacion
KEY_AJENO = "Cudy|Device2|WR3000|4.2.1"

# un serial que solo existe en el archivo: si aparece en la BD, importar esta
# inventando flota que esta instalacion no tiene
SERIAL_AJENO = "00B0C2-ROUTER-OTROISP9"

CAMPOS_DEL_ARCHIVO = {"key", "root", "manufacturer", "product_class", "model",
                      "firmware", "paths", "profile", "overrides"}


# --------------------------------------------------------------------------
# utilidades

def _aprender_dos_modelos(client, fake, admin_h):
    """Lo que hace el laboratorio: abrir fichas. De ahi sale todo el catalogo.

    El primero lleva el SSID y la clave del abonado puestos, para poder
    comprobar que el archivo exportado no se los lleva."""
    _equipo(fake, ISP_DEV, con_datos_del_abonado(), fw="1.0.0")
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, con_ipv6(), fw="2.0.0", tags=("ISP-B",))
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    assert {f["key"] for f in db.list_model_trees()} == {KEY_A, KEY_B}


def _vaciar_la_base():
    """Produccion recien instalada: arboles, aportes y perfiles a cero."""
    with db.connect() as c:
        for tabla in ("model_tree", "model_tree_device", "model_profile"):
            c.execute(f"DELETE FROM {tabla}")


def _sin_evidencia(perfil):
    return {k: v for k, v in perfil.items() if k != "evidencia"} \
        if isinstance(perfil, dict) else perfil


def _estado(key):
    """Todo lo que el catalogo tiene que conservar de un modelo."""
    fila = db.get_model_tree(key)
    perfil = db.get_model_profile(key) or {}
    return {
        "root": fila["root"] if fila else None,
        "rutas": json.loads(fila["paths"]) if fila else None,
        "n_params": fila["n_params"] if fila else None,
        # sin `evidencia`: cita valores del equipo del que se dedujo (IP del
        # gateway, de la WAN, red del pool DHCP) y por eso no sale de aqui; la
        # instalacion de destino deduce la suya al leer su primer equipo
        "perfil": _sin_evidencia(json.loads(perfil["profile"])) if perfil.get("profile") else None,
        "identidad": {c: perfil.get(c) for c in
                      ("manufacturer", "product_class", "model", "firmware")},
        "overrides": catalog.overrides(key),
    }


def _exportar(client, admin_h):
    r = client.get("/trees/export", headers=admin_h)
    assert r.status_code == 200, r.text
    return r


def _importar(client, admin_h, cuerpo, espera=200):
    r = client.post("/trees/import", json=cuerpo, headers=admin_h)
    assert r.status_code == espera, r.text
    return r.json()


# --------------------------------------------------------------------------
# 1. ida y vuelta: laboratorio -> produccion

def test_ida_y_vuelta_deja_los_modelos_igual(client, fake, admin_h):
    """El caso de uso entero. Se aprende en el laboratorio, se exporta, la base
    se vacia (produccion nueva), se importa el archivo: los modelos quedan con
    las mismas rutas, el mismo perfil y las mismas correcciones."""
    _aprender_dos_modelos(client, fake, admin_h)
    # una correccion a mano, como la haria un tecnico desde el panel
    assert client.put(f"/profiles/{KEY_A}/override", headers=admin_h,
                      json={"concept": "wifi_5g_ssid",
                            "path": "Device.WiFi.SSID.3.SSID"}).status_code == 200

    archivo = _exportar(client, admin_h).json()
    antes = {k: _estado(k) for k in (KEY_A, KEY_B)}
    assert antes[KEY_A]["overrides"] == {"wifi_5g_ssid": "Device.WiFi.SSID.3.SSID"}
    assert antes[KEY_A]["perfil"]["wifi"]["2g"]["ssid"] == "1"     # hay perfil deducido

    _vaciar_la_base()
    assert db.list_model_trees() == [] and db.list_model_profiles() == []
    assert _exportar(client, admin_h).json()["modelos"] == []

    res = _importar(client, admin_h, archivo)
    assert res["modelos"] == 2 and res["nuevos"] == 2
    assert res["perfiles_nuevos"] == 2 and res["correcciones"] == 1
    assert res["rutas_nuevas"] == sum(len(m["paths"]) for m in archivo["modelos"])
    assert res["ignorados"] == []

    for key in (KEY_A, KEY_B):
        assert _estado(key) == antes[key], f"{key} no quedo igual tras la vuelta"
    # lo unico que NO viaja es la evidencia: explica con IPs de esta red por que
    # se dedujo cada instancia, y alli no explicaria nada
    assert "evidencia" not in (archivo["modelos"][0].get("profile") or {})
    assert db.get_model_profile(KEY_A)["profile"].find("evidencia") == -1


def test_lo_importado_se_ve_en_el_panel_como_lo_aprendido(client, fake, admin_h):
    """Despues de importar, produccion tiene que poder trabajar: el listado y el
    detalle de un modelo que aqui no ha visto ningun equipo."""
    _aprender_dos_modelos(client, fake, admin_h)
    archivo = _exportar(client, admin_h).json()
    _vaciar_la_base()
    _importar(client, admin_h, archivo)

    filas = client.get("/trees", headers=admin_h).json()
    assert {f["key"] for f in filas} == {KEY_A, KEY_B}
    for f in filas:
        assert f["n_params"] > 30 and f["model"] == "EX511"
        assert f["devices"] == 0
        # ningun equipo registrado: no se sabe si la union esta completa
        assert f["incompleto"] is None
        assert f["last_device"] is None

    r = client.get(f"/trees/{KEY_A}", headers=admin_h, params={"limit": 0}).json()
    assert r["total"] == r["n_params"] > 30
    assert any(p["path"].endswith("KeyPassphrase") for p in r["paths"])


# --------------------------------------------------------------------------
# 2. lo critico: el archivo no lleva nada de la flota

def test_el_archivo_exportado_no_lleva_ningun_equipo(client, fake, admin_h):
    """LA prueba de esta funcion. El archivo sale de la empresa: ni ids de
    equipo, ni seriales, ni `last_device`, ni la lista de quien aporto."""
    _aprender_dos_modelos(client, fake, admin_h)
    assert db.get_model_tree(KEY_A)["last_device"] == ISP_DEV      # en la BD si esta
    assert db.model_tree_devices(KEY_A)                            # y quien aporto

    r = _exportar(client, admin_h)
    for aguja in (ISP_DEV, OTHER_DEV, ISP_DEV[-6:], OTHER_DEV[-6:]):
        assert aguja not in r.text, f"el archivo exportado lleva {aguja!r}"
    assert "last_device" not in r.text and "equipos" not in r.text
    assert "devices" not in r.text

    for m in r.json()["modelos"]:
        sobran = set(m) - CAMPOS_DEL_ARCHIVO
        assert sobran == set(), f"campos que no deberian salir del ISP: {sorted(sobran)}"


def test_el_archivo_exportado_no_lleva_valores_del_abonado(client, fake, admin_h):
    """El arbol de un equipo real trae el SSID y la clave WiFi del cliente. El
    catalogo dice que el modelo TIENE donde poner la clave, no cual es."""
    _aprender_dos_modelos(client, fake, admin_h)
    r = _exportar(client, admin_h)
    assert SSID_ABONADO not in r.text, "el catalogo exportado lleva el SSID del abonado"
    assert CLAVE_ABONADO not in r.text, "el catalogo exportado lleva la clave WiFi del abonado"

    # las rutas si van, y son lo unico que va del arbol: {ruta: escribible}
    paths = next(m["paths"] for m in r.json()["modelos"] if m["key"] == KEY_A)
    assert paths["Device.WiFi.AccessPoint.1.Security.KeyPassphrase"] is True
    assert paths["Device.WiFi.SSID.1.LowerLayers"] is False
    assert all(isinstance(p, str) and isinstance(w, bool) for p, w in paths.items())


def test_el_archivo_exportado_no_lleva_ips_de_la_red_del_isp(client, fake, admin_h):
    _aprender_dos_modelos(client, fake, admin_h)
    r = _exportar(client, admin_h)
    ip_gateway = "198.51.100.1"        # gateway de la ruta por defecto del equipo
    ip_wan = "198.51.100.3"            # IP de la WAN de ese abonado
    assert ip_gateway not in r.text and ip_wan not in r.text


# --------------------------------------------------------------------------
# 3. importar no pisa lo local

def _archivo_que_choca_con_lo_local():
    """Un archivo de otra instalacion que toca el mismo modelo que hay aqui."""
    return {"formato": 1, "modelos": [
        {"key": KEY_A, "root": "Device",
         "manufacturer": "TP-Link", "product_class": "Device2",
         "model": "EX511", "firmware": "1.0.0",
         "paths": {
             "Device.SOLO.EN.EL.ARCHIVO": True,           # rama que aqui no se conoce
             "Device.WiFi.Radio.1.Channel": False,        # aqui ya consta, y escribible
         },
         "profile": {"root": "Device", "wan": "99", "lan": "99", "wifi": {},
                     "evidencia": {"wan": "lo dijo el archivo"}},
         "overrides": {"wifi_2g_ssid": "Device.DEL.ARCHIVO.SSID",   # aqui esta corregido
                       "wifi_5g_ssid": "Device.WiFi.SSID.3.SSID"}},  # aqui esta libre
        {"key": KEY_AJENO, "root": "Device",
         "manufacturer": "Cudy", "product_class": "Device2",
         "model": "WR3000", "firmware": "4.2.1",
         "paths": {"Device.WiFi.SSID.1.SSID": True, "Device.WiFi.Radio.1.Channel": True},
         "profile": {"root": "Device", "wan": "2", "wifi": {"2g": {"radio": "1"}}},
         "overrides": {"lan_ip": "Device.IP.Interface.1.IPv4Address.1.IPAddress"}},
    ]}


def test_importar_une_las_rutas_sin_perder_las_de_aqui(client, fake, admin_h):
    """Igual que cuando se aprenden solas: la union. Una ruta local que el
    archivo no trae sigue estando, y una del archivo que aqui no se conocia se
    suma. Y el flag de escribible no se pierde por lo que diga el archivo."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    locales = json.loads(db.get_model_tree(KEY_A)["paths"])
    assert locales["Device.WiFi.Radio.1.Channel"] is True

    res = _importar(client, admin_h, _archivo_que_choca_con_lo_local())
    despues = json.loads(db.get_model_tree(KEY_A)["paths"])

    assert set(locales) < set(despues), "importar no puede quitar rutas locales"
    assert set(despues) - set(locales) == {"Device.SOLO.EN.EL.ARCHIVO"}
    assert despues["Device.WiFi.Radio.1.Channel"] is True, \
        "el archivo decia escribible=false y se perdio que aqui SI se puede escribir"
    assert db.get_model_tree(KEY_A)["n_params"] == len(despues)

    nuevas_del_ajeno = len(json.loads(db.get_model_tree(KEY_AJENO)["paths"]))
    assert res["modelos"] == 2
    assert res["nuevos"] == 1                      # KEY_A ya estaba; el ajeno no
    assert res["rutas_nuevas"] == 1 + nuevas_del_ajeno
    assert res["ignorados"] == []


def test_importar_no_sustituye_un_perfil_deducido_aqui(client, fake, admin_h):
    """Lo deducido de un equipo real de esta flota manda: el archivo puede venir
    de otro firmware, de otro pais o de una version anterior del deductor."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    perfil_local = json.loads(db.get_model_profile(KEY_A)["profile"])

    res = _importar(client, admin_h, _archivo_que_choca_con_lo_local())

    assert json.loads(db.get_model_profile(KEY_A)["profile"]) == perfil_local, \
        "el perfil del archivo piso el que se dedujo de un equipo de esta flota"
    assert perfil_local["wan"] != "99"
    # el del modelo que aqui no existia si se toma del archivo
    assert json.loads(db.get_model_profile(KEY_AJENO)["profile"])["wan"] == "2"
    assert res["perfiles_nuevos"] == 1


def test_importar_no_pisa_una_correccion_a_mano_pero_rellena_lo_libre(client, fake, admin_h):
    """Una correccion la escribio un tecnico de aqui mirando un equipo de aqui.
    Un concepto que nadie ha corregido si se puede rellenar con el archivo."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    client.put(f"/profiles/{KEY_A}/override", headers=admin_h,
               json={"concept": "wifi_2g_ssid", "path": "Device.A.MANO.DE.AQUI"})

    res = _importar(client, admin_h, _archivo_que_choca_con_lo_local())

    over = catalog.overrides(KEY_A)
    assert over["wifi_2g_ssid"] == "Device.A.MANO.DE.AQUI", \
        "el archivo piso una correccion escrita a mano en esta instalacion"
    assert over["wifi_5g_ssid"] == "Device.WiFi.SSID.3.SSID"   # estaba libre: se rellena
    assert catalog.overrides(KEY_AJENO) == {
        "lan_ip": "Device.IP.Interface.1.IPv4Address.1.IPAddress"}
    assert res["correcciones"] == 2      # la libre de KEY_A y la del modelo ajeno


# --------------------------------------------------------------------------
# 4. basura de entrada

@pytest.mark.parametrize("formato", [0, 2, 99, -1])
def test_un_formato_desconocido_no_se_importa(client, admin_h, formato):
    """Un archivo de una version futura puede querer decir otra cosa con los
    mismos campos: antes de escribir en la BD hay que saber que se esta leyendo."""
    cuerpo = {"formato": formato,
              "modelos": [{"key": KEY_AJENO, "paths": {"Device.X.Y": True}}]}
    r = client.post("/trees/import", json=cuerpo, headers=admin_h)
    assert r.status_code == 422, f"formato {formato} se importo: {r.text[:200]}"
    assert db.get_model_tree(KEY_AJENO) is None      # y no escribio nada


def test_sin_formato_o_sin_modelos_no_se_importa(client, admin_h):
    assert client.post("/trees/import", json={"modelos": []}, headers=admin_h).status_code == 422
    assert client.post("/trees/import", json={"formato": 1}, headers=admin_h).status_code == 422


def test_las_entradas_malas_van_a_ignorados_y_no_tumban_las_buenas(client, admin_h):
    """Un archivo editado a mano, cortado a medias o de otra herramienta. Lo que
    no se entiende se dice y se sigue: lo demas del catalogo se importa igual."""
    buena = {"key": KEY_AJENO, "root": "Device", "paths": {"Device.X.Y": True}}
    cuerpo = {"formato": 1, "modelos": [
        {"paths": {"Device.A.B": True}},                                # sin clave
        {"key": "   ", "paths": {"Device.A.B": True}},                  # clave en blanco
        {"key": "Marca|Clase|SIN-PATHS|1.0"},                           # sin paths
        {"key": "Marca|Clase|PATHS-VACIO|1.0", "paths": {}},            # paths vacio
        {"key": "Marca|Clase|PATHS-LISTA|1.0", "paths": ["Device.A.B"]},  # no es un objeto
        {"key": "Marca|Clase|PATHS-TEXTO|1.0", "paths": "Device.A.B"},
        {"key": "Marca|Clase|PATHS-NULO|1.0", "paths": None},
        {"key": "Marca|Clase|SOLO-BASURA|1.0", "paths": {"": True}},    # nada aprovechable
        buena,
    ]}
    res = _importar(client, admin_h, cuerpo)

    assert res["modelos"] == 1 and res["nuevos"] == 1
    assert res["ignorados"] == [
        "(sin clave)", "(sin clave)", "Marca|Clase|SIN-PATHS|1.0",
        "Marca|Clase|PATHS-VACIO|1.0", "Marca|Clase|PATHS-LISTA|1.0",
        "Marca|Clase|PATHS-TEXTO|1.0", "Marca|Clase|PATHS-NULO|1.0",
        "Marca|Clase|SOLO-BASURA|1.0"]
    assert [f["key"] for f in db.list_model_trees()] == [KEY_AJENO]


def test_un_modelo_con_demasiadas_rutas_se_ignora(client, admin_h):
    """Un arbol TR-181 refrescado son miles de rutas; cientos de miles es un
    archivo equivocado o un intento de llenar la memoria del servidor."""
    from app.routers import trees
    gordo = {f"Device.Relleno.{i}.Param": True for i in range(trees.MAX_RUTAS_POR_MODELO + 1)}
    res = _importar(client, admin_h, {"formato": 1, "modelos": [
        {"key": "Marca|Clase|GORDO|1.0", "paths": gordo},
        {"key": KEY_AJENO, "paths": {"Device.X.Y": True}},
    ]})
    assert res["modelos"] == 1
    assert res["ignorados"] == [f"Marca|Clase|GORDO|1.0 (demasiadas rutas: {len(gordo)})"]
    assert db.get_model_tree("Marca|Clase|GORDO|1.0") is None
    assert db.get_model_tree(KEY_AJENO) is not None      # la buena entro igual


def test_un_archivo_con_demasiados_modelos_no_se_lee(client, admin_h):
    from app.routers import trees
    modelos = [{"key": f"Marca|Clase|M{i}|1.0", "paths": {"Device.X.Y": True}}
               for i in range(trees.MAX_MODELOS + 1)]
    r = client.post("/trees/import", json={"formato": 1, "modelos": modelos}, headers=admin_h)
    assert r.status_code == 422
    assert db.list_model_trees() == []


def test_de_cada_ruta_solo_se_guarda_la_ruta_y_si_es_escribible(client, admin_h):
    """El archivo puede traer cualquier cosa colgada de `paths` (un valor, un
    objeto, una lista). Solo se guarda {ruta: escribible}; lo que no sea una
    ruta se descarta."""
    res = _importar(client, admin_h, {"formato": 1, "modelos": [{
        "key": KEY_AJENO, "root": "Device",
        "paths": {"": True,                                  # ruta vacia: fuera
                  "Device.OK.Escribible": "si",              # se normaliza a bool
                  "Device.OK.Solo.Lectura": 0,
                  "Device.OK.Con.Valor": {"_value": SSID_ABONADO, "_writable": True}},
    }]})
    assert res["modelos"] == 1
    assert json.loads(db.get_model_tree(KEY_AJENO)["paths"]) == {
        "Device.OK.Escribible": True,
        "Device.OK.Solo.Lectura": False,
        "Device.OK.Con.Valor": True}
    fila_entera = json.dumps(db.get_model_tree(KEY_AJENO), ensure_ascii=False)
    assert SSID_ABONADO not in fila_entera, "un valor del archivo acabo en la BD"


def test_importar_no_inventa_equipos(client, admin_h):
    """Un catalogo describe modelos, no flota. Aunque el archivo traiga seriales
    (editado a mano, o exportado por una version que si los llevaba), aqui no se
    crea ningun aporte de equipo ni se apunta un `last_device`."""
    res = _importar(client, admin_h, {"formato": 1, "modelos": [{
        "key": KEY_AJENO, "root": "Device",
        "paths": {"Device.X.Y": True},
        "last_device": SERIAL_AJENO,
        "equipos": [{"device_id": SERIAL_AJENO, "n_params": 3000}],
        "devices": 7,
    }]})
    assert res["modelos"] == 1

    fila = db.get_model_tree(KEY_AJENO)
    assert fila["last_device"] is None, "importar apunto un equipo que aqui no existe"
    assert db.model_tree_devices(KEY_AJENO) == []
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM model_tree_device").fetchone()[0] == 0
    todo = json.dumps([fila, db.get_model_profile(KEY_AJENO)], ensure_ascii=False)
    assert SERIAL_AJENO not in todo

    # y el panel no puede decir que un equipo trajo esa union
    f = client.get("/trees", headers=admin_h).json()[0]
    assert f["devices"] == 0 and f["max_equipo"] is None and f["incompleto"] is None


def test_importar_no_le_pone_equipos_al_perfil(client, admin_h):
    _importar(client, admin_h, {"formato": 1, "modelos": [
        {"key": KEY_AJENO, "root": "Device", "paths": {"Device.X.Y": True},
         "profile": {"root": "Device", "wan": "2"}}]})
    assert db.get_model_profile(KEY_AJENO)["devices"] == 0
    perfil = client.get(f"/profiles/{KEY_AJENO}", headers=admin_h).json()
    assert perfil["devices"] in (0, None)


# --------------------------------------------------------------------------
# 5. permisos

def test_el_catalogo_portable_es_solo_de_admin(client, fake, admin_h, isp_h):
    """Exportar es llevarse el catalogo de TODOS los ISP de la instalacion, e
    importar escribe en el de todos: un usuario de un ISP no hace ni una cosa
    ni la otra."""
    _aprender_dos_modelos(client, fake, admin_h)
    assert client.get("/trees/export", headers=isp_h).status_code == 403
    r = client.post("/trees/import", headers=isp_h, json={
        "formato": 1, "modelos": [{"key": KEY_AJENO, "paths": {"Device.X.Y": True}}]})
    assert r.status_code == 403
    assert db.get_model_tree(KEY_AJENO) is None      # ni escribio nada


def test_sin_token_no_se_exporta_ni_se_importa(client):
    assert client.get("/trees/export").status_code == 401
    assert client.post("/trees/import", json={"formato": 1, "modelos": []}).status_code == 401


# --------------------------------------------------------------------------
# 6. /trees/export no lo atrapa el comodin de la clave

def test_export_no_choca_con_el_detalle_de_un_modelo(client, fake, admin_h):
    """`GET /trees/{key:path}` es un comodin que se come cualquier cosa: si
    /export se declara despues, exportar responde "de ese modelo no hay arbol
    guardado". El orden de declaracion es el unico que lo evita."""
    _aprender_dos_modelos(client, fake, admin_h)
    r = client.get("/trees/export", headers=admin_h)
    assert r.status_code == 200, f"/trees/export lo atrapo el comodin: {r.text[:200]}"
    j = r.json()
    assert {"formato", "generado", "modelos"} <= set(j)
    assert not {"equipos", "last_device", "devices"} & set(j)
    assert j["formato"] == 1 and j["generado"]
    assert sorted(m["key"] for m in j["modelos"]) == [KEY_A, KEY_B]


def test_export_con_la_base_vacia_devuelve_un_catalogo_vacio_no_un_404(client, admin_h):
    """Sin ningun modelo guardado el comodin daria 404 ("de ese modelo no hay
    arbol"): la diferencia entre las dos rutas se ve justo aqui."""
    r = client.get("/trees/export", headers=admin_h)
    assert r.status_code == 200, f"/trees/export cayo en el comodin: {r.text[:200]}"
    assert r.json()["modelos"] == []


def test_un_modelo_sin_perfil_se_exporta_igual(client, admin_h):
    """El arbol se aprende siempre; el perfil solo si el deductor saco algo. Una
    fila sin perfil no puede quedarse fuera del catalogo."""
    db.merge_model_tree(KEY_AJENO, "Device", {"Device.WiFi.SSID.1.SSID": True}, None)
    assert db.get_model_profile(KEY_AJENO) is None
    m = _exportar(client, admin_h).json()["modelos"]
    assert len(m) == 1 and m[0]["key"] == KEY_AJENO
    assert m[0]["profile"] is None and m[0]["overrides"] == {}
    assert m[0]["paths"] == {"Device.WiFi.SSID.1.SSID": True}


def test_una_fila_ilegible_no_tumba_la_exportacion(client, fake, admin_h):
    """Una restauracion a medias o una edicion a mano deja JSON corrupto en una
    fila: el catalogo de los demas modelos tiene que poder salir."""
    _aprender_dos_modelos(client, fake, admin_h)
    with db.connect() as c:
        c.execute("UPDATE model_tree SET paths='{no es json}' WHERE key=?", (KEY_A,))
    j = _exportar(client, admin_h).json()
    assert [m["key"] for m in j["modelos"]] == [KEY_B]
    # y se dice cuantas quedaron fuera, en vez de entregar un catalogo mas corto
    # sin explicacion
    assert j.get("ilegibles", 1) == 1


# --------------------------------------------------------------------------
# 7. el panel

_EST = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "static"
PANEL_JS = (_EST / "app.js").read_text(encoding="utf-8")
PANEL_HTML = (_EST / "index.html").read_text(encoding="utf-8")

IDS_CONTRATO = ["cat-exportar", "cat-importar", "cat-importar-btn", "cat-resultado"]

# el texto visible del panel, sin etiquetas, partido en frases
PANEL_TEXTO = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", PANEL_HTML)).lower()
FRASES = [f for f in re.split(r"[.;!?]", PANEL_TEXTO) if f.strip()]


def _hay_frase_con(*palabras):
    return any(all(p in f for p in palabras) for f in FRASES)


def test_el_html_trae_los_ids_del_catalogo_portable():
    faltan = [i for i in IDS_CONTRATO if f'id="{i}"' not in PANEL_HTML]
    assert faltan == [], f"ids del contrato que no estan en el HTML: {faltan}"


def test_el_importar_es_un_selector_de_archivo():
    """Sin type=file no hay forma de elegir el archivo exportado."""
    bloque = PANEL_HTML[PANEL_HTML.index('id="cat-importar"') - 200:
                        PANEL_HTML.index('id="cat-importar"') + 200]
    assert "<input" in bloque and 'type="file"' in bloque


def test_el_js_exporta_e_importa_el_catalogo():
    assert "function exportarCatalogo(" in PANEL_JS
    assert "importarCatalogo(" in PANEL_JS
    assert "/trees/export" in PANEL_JS and "/trees/import" in PANEL_JS


def test_cada_id_del_catalogo_que_usa_el_js_existe_en_el_html():
    """El cruce que ya cazo un panel en blanco sin un solo error visible."""
    usados = set(re.findall(r'#(cat-[\w-]+)', PANEL_JS))
    usados |= set(re.findall(r'getElementById\(["\'](cat-[\w-]+)', PANEL_JS))
    assert usados, "el JS no toca ningun id cat-* del catalogo portable"
    faltan = sorted(i for i in usados if f'id="{i}"' not in PANEL_HTML)
    assert faltan == [], f"el JS usa ids que el HTML no tiene: {faltan}"


def test_los_botones_del_catalogo_estan_cableados_al_js():
    """Un id en el HTML que el JS no lee es un boton que no hace nada."""
    for i in ("cat-exportar", "cat-importar-btn", "cat-resultado"):
        assert i in PANEL_JS, f"el HTML tiene {i} pero el JS no lo usa"


def _cuerpo_js(firma):
    i = PANEL_JS.index(firma)
    resto = PANEL_JS[i:]
    return resto[:resto.index(chr(10) + "}")]


def test_el_panel_escapa_lo_que_viene_del_archivo():
    """El nombre del archivo y las claves ignoradas los escribe quien te pasa el
    catalogo: si van a innerHTML sin escapar, importar un archivo es ejecutar su
    HTML en el panel del admin."""
    cuerpo = _cuerpo_js("async function importarCatalogo(")
    assert "ignorados" in cuerpo and "innerHTML" in cuerpo
    # los mensajes de error son texto, no HTML: se escapan al pintarlos
    pintado = re.sub(r"new Error\(`[^`]*`\)", "", cuerpo)
    ajenas = [t for t in re.findall(r"\$\{([^}]*)\}", pintado)
              if "file.name" in t or t.strip() == "x"]
    assert ajenas, "el panel no pinta ni el nombre del archivo ni las claves ignoradas"
    crudas = [t for t in ajenas if "esc(" not in t]
    assert crudas == [], f"texto del archivo ajeno sin escapar: {crudas}"


def test_al_importar_se_refresca_el_listado_de_modelos():
    """Si la tabla no se recarga, el admin no ve lo que acaba de entrar y vuelve
    a importar el mismo archivo."""
    assert "loadArboles()" in _cuerpo_js("async function importarCatalogo(")


def test_el_panel_avisa_de_que_el_catalogo_no_es_el_respaldo_de_la_base():
    """El malentendido que hay que evitar: creerse respaldado porque se bajo el
    catalogo. Lleva modelos, no la base de datos."""
    assert _hay_frase_con("no", "respaldo"), \
        "el panel no dice en ningun sitio que esto no es un respaldo"
    assert _hay_frase_con("no", "respaldo", "base de datos") or \
        _hay_frase_con("no", "respaldo", " bd"), \
        "el aviso no aclara que lo que no sustituye es el respaldo de la BD"


def test_el_panel_dice_que_el_catalogo_no_lleva_equipos():
    """Quien exporta tiene que saber que puede pasarle el archivo a otro ISP."""
    assert _hay_frase_con("no", "equipo"), \
        "el panel no dice que el archivo no lleva equipos de la flota"
