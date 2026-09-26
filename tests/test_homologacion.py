"""Homologacion asistida por IA.

La propuesta del modelo es eso, una propuesta. Lo que protege al equipo es que
solo se aceptan rutas que existen de verdad en su arbol, que nada se aplica sin
confirmacion humana, y que al modelo no se le mandan valores de clientes.
"""
import json

import httpx
import pytest

from app import llm
from conftest import ISP_DEV
from test_perfil_derivado import ex511, v


@pytest.fixture
def con_ia(monkeypatch):
    """Proveedor de IA configurado, sin salir a internet."""
    monkeypatch.setenv("GENIEACS_API_LLM_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("GENIEACS_API_LLM_PROVIDER", "groq")
    from app import config
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _responder(respuesta_modelo: dict, capturado: list):
    """Doble del proveedor: guarda lo que se le manda y devuelve una respuesta."""
    def handler(request):
        capturado.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [
            {"message": {"content": json.dumps(respuesta_modelo)}}]})
    return handler


def _cliente_falso(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr("app.llm.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _equipo_raro(fake):
    """TR-181 con un parametro propietario que ninguna regla sabe mapear."""
    doc = ex511()
    doc["Device"]["X_MARCA_Gestion"] = {"UsuarioAdmin": v("admin", True)}
    fake.devices[ISP_DEV] = {"_id": ISP_DEV, "_tags": ["ISP-A"],
                             "_lastInform": "2026-09-26T21:00:00.000Z",
                             "_deviceId": {"_Manufacturer": "MarcaRara", "_ProductClass": "RR1"},
                             **doc}


def test_sin_clave_configurada_la_funcion_esta_apagada(client, fake, admin_h):
    assert client.get("/homologacion/estado", headers=admin_h).json() == {"disponible": False}
    r = client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV})
    assert r.status_code == 400 and "no hay proveedor" in r.json()["detail"].lower()


def test_al_modelo_se_le_mandan_rutas_pero_nunca_valores(client, fake, admin_h, con_ia, monkeypatch):
    """El mapeo no necesita el SSID ni la clave del abonado: no deben salir."""
    _equipo_raro(fake)
    capturado = []
    _cliente_falso(monkeypatch, _responder({"mapeo": {}}, capturado))
    client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV})
    enviado = json.dumps(capturado[0])
    assert "Device.WiFi.SSID.1.SSID" in enviado          # la ruta si
    assert "RED-2G" not in enviado                        # el valor no
    assert "198.51.100.3" not in enviado                  # ni la IP del abonado


def test_una_ruta_inventada_por_el_modelo_se_descarta(client, fake, admin_h, con_ia, monkeypatch):
    """La proteccion principal: si la ruta no esta en el arbol, no pasa."""
    _equipo_raro(fake)
    _cliente_falso(monkeypatch, _responder({"mapeo": {
        "admin_user": "Device.X_MARCA_Gestion.UsuarioAdmin",       # existe
        "admin_password": "Device.Users.User.99.Password",          # inventada
    }}, []))
    r = client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV}).json()
    rutas = {s["concept"]: s["path"] for s in r["sugerencias"]}
    assert rutas == {"admin_user": "Device.X_MARCA_Gestion.UsuarioAdmin"}
    assert r["descartadas"] and r["descartadas"][0][0] == "admin_password"


def test_la_propuesta_no_se_aplica_sola(client, fake, admin_h, con_ia, monkeypatch):
    _equipo_raro(fake)
    _cliente_falso(monkeypatch, _responder({"mapeo": {
        "admin_user": "Device.X_MARCA_Gestion.UsuarioAdmin"}}, []))
    r = client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV}).json()
    key = r["key"]
    from app import db
    fila = db.get_model_profile(key)
    assert fila is None or not fila.get("overrides")      # nada guardado aun

    client.post("/homologacion/confirmar", headers=admin_h,
                json={"key": key, "concept": "admin_user",
                      "path": "Device.X_MARCA_Gestion.UsuarioAdmin"})
    assert "X_MARCA_Gestion" in db.get_model_profile(key)["overrides"]


def test_confirmar_sobre_un_modelo_desconocido_da_404(client, fake, admin_h, con_ia):
    r = client.post("/homologacion/confirmar", headers=admin_h,
                    json={"key": "no|existe|x|y", "concept": "admin_user", "path": "Device.X"})
    assert r.status_code == 404


def test_si_el_proveedor_falla_se_dice_claro(client, fake, admin_h, con_ia, monkeypatch):
    _equipo_raro(fake)

    def handler(request):
        return httpx.Response(429, json={"error": "rate limit"})
    _cliente_falso(monkeypatch, handler)
    r = client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV})
    assert r.status_code == 502 and "no respondio" in r.json()["detail"]


def test_si_el_modelo_devuelve_basura_no_revienta(client, fake, admin_h, con_ia, monkeypatch):
    _equipo_raro(fake)

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "esto no es json"}}]})
    _cliente_falso(monkeypatch, handler)
    r = client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV})
    assert r.status_code == 502


def test_a_la_ia_solo_se_le_preguntan_los_conceptos_sin_resolver(client, fake, admin_h,
                                                                 con_ia, monkeypatch):
    """Las reglas deterministas van primero: la IA es para lo que quede."""
    llamadas = []
    _cliente_falso(monkeypatch, _responder({"mapeo": {}}, llamadas))
    _equipo_raro(fake)
    r = client.post("/homologacion/proponer", headers=admin_h, json={"device_id": ISP_DEV}).json()
    # lo que el perfil derivado ya resuelve NO se le pregunta al modelo
    for resuelto in ("wifi_2g_ssid", "wifi_5g_ssid", "lan_ip", "wan_ip", "wan_gateway"):
        assert resuelto not in r["faltan"]
    preguntado = json.dumps(llamadas[0])
    assert "wifi_2g_ssid" not in preguntado
    assert "admin_password" in preguntado        # esto si: el equipo no lo expone


def test_la_homologacion_es_solo_de_admin(client, fake, isp_h):
    assert client.get("/homologacion/estado", headers=isp_h).status_code == 403
    assert client.post("/homologacion/proponer", headers=isp_h,
                       json={"device_id": ISP_DEV}).status_code == 403


def test_el_limite_de_rutas_protege_la_ventana_del_modelo():
    """Los arboles TR-181 pasan de 4600 rutas y Groq tiene ventanas cortas."""
    rutas = [f"Device.Cosa.{i}.Param" for i in range(5000)]
    mensajes = llm._prompt(["lan_ip"], rutas, "ModeloX")
    assert f"Device.Cosa.{llm.MAX_RUTAS - 1}.Param" in mensajes[1]["content"]
    assert f"Device.Cosa.{llm.MAX_RUTAS}.Param" not in mensajes[1]["content"]
