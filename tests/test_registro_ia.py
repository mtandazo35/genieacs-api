"""Registro de lo que hace la IA (tabla `ia_evento` + GET /homologacion/log).

La auditoria general la escribe un middleware: anota la PETICION ("POST
/homologacion/proponer", 200) y nunca la respuesta del modelo. Cuando una
propuesta acaba cambiando el perfil de un modelo de CPE, lo que hay que poder
mirar meses despues es justo lo que falta ahi: que se le pidio, que contesto y
si el proveedor respondio.

Lo delicado de este registro es lo que NO debe entrar. La respuesta de
/homologacion/proponer lleva `valor_actual` de cada ruta propuesta (ahi van el
SSID y la clave WiFi del abonado) para que una persona pueda revisarla, y la
configuracion lleva la clave del proveedor de IA. Ni los valores ni la clave
tienen nada que hacer en una tabla de auditoria que se consulta y se exporta:
el registro guarda conceptos y rutas.
"""
import json
import sqlite3

import httpx
import pytest

from app import db
from conftest import ISP_DEV
from test_ajustes_ia import proveedor
from test_perfil_derivado import ex511, v

# valores reconocibles: si alguno aparece en el registro, se esta guardando la
# configuracion del cliente en la tabla de auditoria de la IA
SSID_ABONADO = "SSID-DEL-ABONADO-4F2A"
CLAVE_ABONADO = "clave-del-abonado-9Z7X"
CLAVE_IA = "gsk-clave-del-proveedor-NO-DEBE-SALIR"

KEY = "MarcaRara|RR1|EX511|1.0.0"
BASE = "https://api.groq.com/openai/v1"
MODELO_IA = "llama-3.3-70b-versatile"

RUTA_CLAVE_WIFI = "Device.WiFi.AccessPoint.1.Security.KeyPassphrase"
RUTA_SSID = "Device.WiFi.SSID.1.SSID"
RUTA_ADMIN = "Device.X_MARCA_Gestion.UsuarioAdmin"

# lo que "propone" el modelo: una ruta con la clave WiFi del abonado, otra con su
# SSID (mal mapeada a proposito: por eso lo confirma una persona) y una inventada
PROPUESTA = {"mapeo": {"wifi_5g_password": RUTA_CLAVE_WIFI,
                       "admin_user": RUTA_SSID,
                       "admin_password": "Device.Users.User.99.Password"},
             "dudas": ["el modelo no expone el usuario de administracion"]}


@pytest.fixture
def ia_configurada(client, admin_h):
    """Proveedor de IA guardado desde el panel, con una clave reconocible."""
    r = client.put("/settings/llm", headers=admin_h,
                   json={"provider": "groq", "api_key": CLAVE_IA,
                         "model": MODELO_IA, "base_url": BASE})
    assert r.status_code == 200 and r.json()["key_set"] is True
    return r.json()


def _equipo_con_datos_del_abonado(fake):
    """TR-181 con un parametro propietario que ninguna regla sabe mapear y con el
    SSID y la clave WiFi que tendria en casa del cliente."""
    doc = ex511()
    doc["Device"]["X_MARCA_Gestion"] = {"UsuarioAdmin": v("admin", True)}
    doc["Device"]["WiFi"]["SSID"]["1"]["SSID"] = v(SSID_ABONADO, True)
    doc["Device"]["WiFi"]["AccessPoint"]["1"]["Security"] = {"KeyPassphrase": v(CLAVE_ABONADO, True)}
    doc["Device"]["DeviceInfo"] = {"ModelName": v("EX511"), "SoftwareVersion": v("1.0.0")}
    fake.devices[ISP_DEV] = {
        "_id": ISP_DEV, "_tags": ["ISP-A"], "_lastInform": "2026-09-27T21:00:00.000Z",
        "_deviceId": {"_Manufacturer": "MarcaRara", "_ProductClass": "RR1"},
        **doc,
    }
    return fake.devices[ISP_DEV]


def _cliente_falso(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr("app.llm.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _modelo_responde(monkeypatch, respuesta=PROPUESTA):
    _cliente_falso(monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(respuesta)}}]}))


def _eventos(tipo=None):
    """Las filas tal como estan en la BD (sin pasar por el endpoint)."""
    with db.connect() as c:
        sql = "SELECT * FROM ia_evento"
        params = []
        if tipo:
            sql += " WHERE tipo=?"
            params.append(tipo)
        return [dict(r) for r in c.execute(sql + " ORDER BY id", params)]


def _uno(tipo):
    filas = _eventos(tipo)
    assert len(filas) == 1, f"se esperaba 1 evento '{tipo}', hay {len(filas)}: {filas}"
    return filas[0]


def _proponer(client, admin_h):
    return client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV})


# ---------- 1. queda constancia de la propuesta ----------

def test_una_propuesta_queda_registrada_con_lo_que_contesto_el_modelo(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """El registro general dice "alguien pidio una propuesta". Aqui esta el resto:
    para que equipo, de que modelo, a que proveedor y que mapeo salio."""
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    r = _proponer(client, admin_h)
    assert r.status_code == 200, r.text

    ev = _uno("propuesta")
    assert ev["ok"] == 1 and ev["error"] is None
    assert ev["user"] == "admin"
    assert ev["model_key"] == KEY and ev["device_id"] == ISP_DEV
    assert ev["proveedor"] == BASE and ev["modelo_ia"] == MODELO_IA
    assert ev["ms"] is not None and ev["ms"] >= 0
    assert ev["ts"]

    det = json.loads(ev["detalle"])
    # los conceptos que se pidieron y el mapeo que propuso: sin esto el registro
    # no sirve para auditar una correccion que ya esta en el catalogo
    assert "wifi_5g_password" in det["pedidos"] and "admin_user" in det["pedidos"]
    assert det["propuesto"] == {"wifi_5g_password": RUTA_CLAVE_WIFI, "admin_user": RUTA_SSID}
    assert det["rutas_enviadas"] > 0
    # tambien lo que se invento: dice cuanto se puede confiar en ese modelo
    assert det["descartadas"] == [["admin_password", "Device.Users.User.99.Password"]]
    assert det["dudas"] == PROPUESTA["dudas"]


# ---------- 2. lo critico: ni valores del abonado ni la clave del proveedor ----------

def test_el_registro_no_guarda_ningun_valor_del_arbol(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """LA prueba de este registro. /homologacion/proponer devuelve `valor_actual`
    de cada ruta (el SSID y la clave WiFi del abonado) para poder revisar la
    propuesta; si eso se guardara, la tabla de auditoria se convertiria en un
    listado de claves WiFi de la flota."""
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    r = _proponer(client, admin_h).json()
    # la respuesta SI los muestra (para que una persona pueda decidir)
    valores = {s["concept"]: s["valor_actual"] for s in r["sugerencias"]}
    assert valores == {"wifi_5g_password": CLAVE_ABONADO, "admin_user": SSID_ABONADO}

    fila = json.dumps(_uno("propuesta"), ensure_ascii=False)
    assert CLAVE_ABONADO not in fila, "la clave WiFi del abonado acabo en el registro de IA"
    assert SSID_ABONADO not in fila, "el SSID del abonado acabo en el registro de IA"
    # las RUTAS si: son lo que se auditaba
    assert RUTA_CLAVE_WIFI in fila and RUTA_SSID in fila


def test_el_endpoint_del_registro_tampoco_devuelve_valores_ni_la_clave(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """Lo que ve el panel (y lo que alguien copia y pega en un ticket)."""
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    _proponer(client, admin_h)

    r = client.get("/homologacion/log", headers=admin_h)
    assert r.status_code == 200
    assert CLAVE_ABONADO not in r.text and SSID_ABONADO not in r.text
    assert CLAVE_IA not in r.text, "la clave del proveedor de IA sale por /homologacion/log"
    ev = r.json()[0]
    assert ev["tipo"] == "propuesta" and ev["ok"] is True
    assert ev["detalle"]["propuesto"] == {"wifi_5g_password": RUTA_CLAVE_WIFI,
                                          "admin_user": RUTA_SSID}
    assert "valor_actual" not in r.text


def test_la_auditoria_general_tampoco_se_queda_los_valores(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """El otro registro (audit_log) ve la peticion, no la respuesta: que siga asi.
    Los dos se consultan y se exportan igual."""
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    _proponer(client, admin_h)
    audit = client.get("/auth/audit", headers=admin_h).text
    assert CLAVE_ABONADO not in audit and SSID_ABONADO not in audit
    assert CLAVE_IA not in audit


def test_la_clave_del_proveedor_no_entra_en_la_fila(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """Se guarda la URL base del proveedor, que es lo que identifica a donde fue
    la consulta; la clave no hace falta para auditar nada."""
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    _proponer(client, admin_h)
    todo = json.dumps(_eventos(), ensure_ascii=False)
    assert CLAVE_IA not in todo
    assert BASE in todo


# ---------- 3. el proveedor falla ----------

def test_si_el_proveedor_falla_queda_el_intento_con_el_motivo(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """Un 429 de Groq no puede ser un silencio: "pedi una propuesta y no paso
    nada" es exactamente lo que este registro tiene que explicar."""
    _equipo_con_datos_del_abonado(fake)
    _cliente_falso(monkeypatch, lambda req: httpx.Response(429, json={"error": "rate limit"}))
    r = _proponer(client, admin_h)
    assert r.status_code == 502 and "no respondio" in r.json()["detail"]

    ev = _uno("propuesta")
    assert ev["ok"] == 0
    assert "429" in ev["error"] and "RuntimeError" in ev["error"]
    assert ev["model_key"] == KEY and ev["device_id"] == ISP_DEV
    assert ev["proveedor"] == BASE
    det = json.loads(ev["detalle"])
    assert det["pedidos"] and det["rutas_enviadas"] > 0
    assert "propuesto" not in det          # no hubo propuesta que anotar

    # y el panel lo ve como fallido
    ev_api = client.get("/homologacion/log", headers=admin_h).json()[0]
    assert ev_api["ok"] is False and "429" in ev_api["error"]


def test_una_respuesta_ilegible_tambien_queda_registrada(
        client, fake, admin_h, ia_configurada, monkeypatch):
    _equipo_con_datos_del_abonado(fake)
    _cliente_falso(monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": "esto no es json"}}]}))
    assert _proponer(client, admin_h).status_code == 502
    ev = _uno("propuesta")
    assert ev["ok"] == 0 and "ilegible" in ev["error"]


# ---------- 4. confirmar ----------

def test_confirmar_una_ruta_queda_registrado_con_concepto_y_ruta(
        client, fake, admin_h, ia_configurada, monkeypatch):
    """La propuesta no cambia nada; la confirmacion si. Es la entrada que hay que
    poder encontrar cuando un modelo de CPE empieza a comportarse raro."""
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    _proponer(client, admin_h)

    r = client.post("/homologacion/confirmar", headers=admin_h,
                    json={"key": KEY, "concept": "admin_user", "path": RUTA_ADMIN})
    assert r.status_code == 200, r.text

    ev = _uno("confirmacion")
    assert ev["user"] == "admin" and ev["model_key"] == KEY and ev["ok"] == 1
    assert json.loads(ev["detalle"]) == {"concepto": "admin_user", "ruta": RUTA_ADMIN}


def test_una_confirmacion_rechazada_no_deja_entrada(client, fake, admin_h, ia_configurada):
    """Solo se anota lo que de verdad cambio el catalogo."""
    r = client.post("/homologacion/confirmar", headers=admin_h,
                    json={"key": "no|existe|x|y", "concept": "admin_user", "path": "Device.X"})
    assert r.status_code == 404
    assert _eventos("confirmacion") == []


# ---------- 5. el boton Probar ----------

def test_probar_el_proveedor_queda_registrado(client, admin_h, ia_configurada, monkeypatch):
    """"Probe y funcionaba" / "pues aqui no consta": con esto consta."""
    proveedor(monkeypatch)
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is True

    ev = _uno("prueba")
    assert ev["ok"] == 1 and ev["error"] is None
    assert ev["user"] == "admin"
    assert ev["proveedor"] == BASE and ev["modelo_ia"] == MODELO_IA
    assert ev["ms"] is not None
    assert ev["model_key"] is None and ev["device_id"] is None
    assert json.loads(ev["detalle"])["modelos_del_proveedor"] == 3
    assert CLAVE_IA not in json.dumps(ev, ensure_ascii=False)


def test_una_prueba_fallida_queda_con_el_error_y_sin_la_clave(
        client, admin_h, ia_configurada, monkeypatch):
    """El caso que mas se consulta: la clave no vale. Antes la auditoria decia
    "configuro el proveedor de IA" y no distinguia una prueba fallida."""
    real = httpx.AsyncClient
    monkeypatch.setattr("app.llm.httpx.AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, json={})), **kw))
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False

    ev = _uno("prueba")
    assert ev["ok"] == 0
    assert "clave no es valida" in ev["error"]
    assert ev["proveedor"] == BASE
    assert CLAVE_IA not in json.dumps(ev, ensure_ascii=False)
    assert CLAVE_IA not in client.get("/homologacion/log", headers=admin_h).text


def test_probar_una_clave_sin_guardar_no_guarda_la_clave_en_el_registro(
        client, admin_h, monkeypatch):
    """Se puede probar una configuracion sin guardarla: tampoco por ahi entra."""
    proveedor(monkeypatch)
    otra = "gsk-clave-sin-guardar-NO-DEBE-SALIR"
    r = client.post("/settings/llm/test", headers=admin_h,
                    json={"provider": "groq", "api_key": otra}).json()
    assert r["ok"] is True
    ev = _uno("prueba")
    assert otra not in json.dumps(ev, ensure_ascii=False)
    assert ev["ok"] == 1 and ev["proveedor"] == BASE


def test_sin_proveedor_configurado_la_prueba_tambien_deja_rastro(client, admin_h):
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False
    ev = _uno("prueba")
    assert ev["ok"] == 0 and "Ajustes" in ev["error"]


# ---------- 6. el endpoint del registro ----------

def _tres_pruebas(client, admin_h, monkeypatch):
    proveedor(monkeypatch)
    for _ in range(3):
        assert client.post("/settings/llm/test", headers=admin_h).json()["ok"] is True


def test_el_registro_devuelve_lo_mas_reciente_primero(client, admin_h, ia_configurada,
                                                      monkeypatch):
    _tres_pruebas(client, admin_h, monkeypatch)
    filas = client.get("/homologacion/log", headers=admin_h).json()
    ids = [e["id"] for e in filas]
    assert len(ids) == 3
    assert ids == sorted(ids, reverse=True), "el registro deberia venir del mas nuevo al mas viejo"


def test_el_registro_respeta_el_limite(client, admin_h, ia_configurada, monkeypatch):
    _tres_pruebas(client, admin_h, monkeypatch)
    todos = client.get("/homologacion/log", headers=admin_h).json()
    assert len(todos) == 3
    dos = client.get("/homologacion/log", headers=admin_h, params={"limit": 2}).json()
    assert len(dos) == 2
    assert [e["id"] for e in dos] == [e["id"] for e in todos[:2]]   # los mas nuevos


def test_un_limite_absurdo_se_rechaza(client, admin_h, ia_configurada, monkeypatch):
    """El limite lo acota el servidor: nadie se lleva la tabla entera de un tiron
    ni pide menos de una fila."""
    _tres_pruebas(client, admin_h, monkeypatch)
    pide = lambda n: client.get("/homologacion/log", headers=admin_h, params={"limit": n})
    assert pide(500).status_code == 200
    assert pide(501).status_code == 422
    assert pide(0).status_code == 422
    assert pide(-5).status_code == 422


def test_el_registro_filtra_por_tipo(client, fake, admin_h, ia_configurada, monkeypatch):
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    _proponer(client, admin_h)
    client.post("/homologacion/confirmar", headers=admin_h,
                json={"key": KEY, "concept": "admin_user", "path": RUTA_ADMIN})

    tipos = [e["tipo"] for e in client.get("/homologacion/log", headers=admin_h).json()]
    assert tipos == ["confirmacion", "propuesta"]
    solo = client.get("/homologacion/log", headers=admin_h,
                      params={"tipo": "propuesta"}).json()
    assert [e["tipo"] for e in solo] == ["propuesta"]
    assert client.get("/homologacion/log", headers=admin_h,
                      params={"tipo": "inventado"}).json() == []


def test_el_registro_vacio_no_revienta(client, admin_h):
    assert client.get("/homologacion/log", headers=admin_h).json() == []


def test_el_registro_de_la_ia_es_solo_de_admin(client, isp_h):
    """Lleva la clave de modelos, equipos de todos los ISP y lo que respondio el
    proveedor: un ISP no tiene nada que ver ahi."""
    assert client.get("/homologacion/log", headers=isp_h).status_code == 403


# ---------- el panel ----------
_EST = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "static"
PANEL_JS = (_EST / "app.js").read_text(encoding="utf-8")
PANEL_HTML = (_EST / "index.html").read_text(encoding="utf-8")

IDS_CONTRATO = ["ia-log-list", "ia-log-refresh", "ia-log-tipo"]


def test_el_registro_de_ia_tiene_su_subpestana_en_auditoria():
    seccion = PANEL_HTML[PANEL_HTML.index('<section id="audit-page"'):]
    seccion = seccion[:seccion.index("</section>")]
    assert 'data-asub="todo"' in seccion and 'data-asub="ia"' in seccion
    assert 'data-apanel="todo"' in seccion and 'data-apanel="ia"' in seccion


def test_el_html_trae_los_ids_del_registro_de_ia():
    faltan = [i for i in IDS_CONTRATO if f'id="{i}"' not in PANEL_HTML]
    assert faltan == [], f"ids del contrato que no estan en el HTML: {faltan}"


def test_cada_id_que_usa_el_js_para_el_registro_existe_en_el_html():
    """El cruce que ya cazo un panel en blanco sin un solo error visible."""
    import re
    usados = set(re.findall(r'#(ia-log-[\w-]+)', PANEL_JS))
    usados |= set(re.findall(r'getElementById\(["\'](ia-log-[\w-]+)', PANEL_JS))
    faltan = sorted(i for i in usados if f'id="{i}"' not in PANEL_HTML)
    assert faltan == [], f"el JS usa ids que el HTML no tiene: {faltan}"


def test_el_panel_carga_el_registro_de_ia():
    assert "function loadIaLog(" in PANEL_JS
    assert "/homologacion/log" in PANEL_JS


# ---------- lo que salio al revisar la primera version ----------

def test_el_registro_dice_a_que_proveedor_se_consulto_aunque_no_se_fije_la_url(
        client, fake, admin_h, monkeypatch):
    """Sin URL en Ajustes se usa el preset del proveedor: la fila decia "" y no
    quedaba constancia de a donde habia ido la consulta."""
    client.put("/settings/llm", headers=admin_h,
               json={"provider": "groq", "api_key": CLAVE_IA, "model": MODELO_IA, "base_url": ""})
    _equipo_con_datos_del_abonado(fake)
    _modelo_responde(monkeypatch)
    assert _proponer(client, admin_h).status_code == 200
    assert _uno("propuesta")["proveedor"] == "https://api.groq.com/openai/v1"


def test_un_fallo_tambien_dice_a_que_proveedor_se_iba(client, fake, admin_h, monkeypatch):
    """Cuando la consulta falla no hay respuesta que diga el destino: si ademas
    no se fijo la URL, la fila quedaba sin decir a donde iba la consulta, que es
    justo lo que hace falta para diagnosticar el fallo."""
    client.put("/settings/llm", headers=admin_h,
               json={"provider": "groq", "api_key": CLAVE_IA, "model": MODELO_IA, "base_url": ""})
    _equipo_con_datos_del_abonado(fake)
    _cliente_falso(monkeypatch, lambda req: httpx.Response(429, json={"error": "slow down"}))
    assert _proponer(client, admin_h).status_code == 502
    fila = _uno("propuesta")
    assert fila["ok"] == 0 and fila["proveedor"] == "https://api.groq.com/openai/v1"


def test_si_falla_el_registro_no_se_lleva_por_delante_la_peticion(monkeypatch):
    """La consulta al proveedor ya se pago: que no se pueda anotar (BD bloqueada,
    disco lleno) no puede convertir una propuesta buena en un 500."""
    def revienta(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("app.db.connect", revienta)
    db.log_ia("propuesta", user="admin", model_key="X|Y|Z|1")   # no debe lanzar


def test_el_registro_no_crece_sin_limite(client, admin_h):
    """Una flota que homologa a diario llenaria la tabla para siempre."""
    monkeypatch_max = db.IA_EVENTOS_MAX
    assert monkeypatch_max >= 100
    for i in range(monkeypatch_max + 25):
        db.log_ia("prueba", user="admin", detalle='{"i": %d}' % i)
    filas = _eventos("prueba")
    assert len(filas) == monkeypatch_max
    # se tiran las viejas, no las recientes
    assert json.loads(filas[-1]["detalle"])["i"] == monkeypatch_max + 24
