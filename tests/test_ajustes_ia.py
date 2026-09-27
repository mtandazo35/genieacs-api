"""Configurar el proveedor de IA desde el panel.

Lo delicado es la clave: se guarda para poder usarla, pero **no puede salir**
por la API ni quedar escrita en la auditoria.
"""
import httpx
import pytest

CLAVE = "gsk-clave-secreta-de-prueba-123456"


@pytest.fixture
def guardada(client, admin_h):
    r = client.put("/settings/llm", headers=admin_h,
                   json={"provider": "groq", "api_key": CLAVE, "model": "llama-3.3-70b-versatile"})
    assert r.status_code == 200
    return r.json()


def test_la_clave_nunca_se_devuelve(client, admin_h, guardada):
    assert guardada["key_set"] is True and CLAVE not in str(guardada)
    c = client.get("/settings/llm", headers=admin_h).json()
    assert c["key_set"] is True and c["source"] == "panel"
    assert CLAVE not in str(c)
    assert c["provider"] == "groq" and c["model"] == "llama-3.3-70b-versatile"


def test_la_clave_no_acaba_en_la_auditoria(client, admin_h, guardada):
    audit = client.get("/auth/audit", headers=admin_h).json()
    assert CLAVE not in str(audit)
    assert any("proveedor de IA" in (a["action"] or "") for a in audit)


def test_configurarla_en_el_panel_enciende_la_homologacion(client, admin_h):
    assert client.get("/homologacion/estado", headers=admin_h).json() == {"disponible": False}
    client.put("/settings/llm", headers=admin_h, json={"api_key": CLAVE})
    assert client.get("/homologacion/estado", headers=admin_h).json() == {"disponible": True}


def test_borrar_la_clave_apaga_la_funcion(client, admin_h, guardada):
    client.put("/settings/llm", headers=admin_h, json={"api_key": ""})
    assert client.get("/settings/llm", headers=admin_h).json()["key_set"] is False
    assert client.get("/homologacion/estado", headers=admin_h).json() == {"disponible": False}


def test_lo_del_panel_manda_sobre_el_env(client, admin_h, monkeypatch, guardada):
    """Si alguien dejo una clave en el .env, la del panel es la que se usa."""
    monkeypatch.setenv("GENIEACS_API_LLM_API_KEY", "clave-del-env")
    from app import config, runtime
    config.get_settings.cache_clear()
    assert runtime.llm_config()["api_key"] == CLAVE
    config.get_settings.cache_clear()


def test_una_url_de_proveedor_invalida_se_rechaza(client, admin_h):
    r = client.put("/settings/llm", headers=admin_h, json={"base_url": "api.groq.com"})
    assert r.status_code == 400 and "http" in r.json()["detail"]


def test_un_proveedor_desconocido_se_rechaza(client, admin_h):
    assert client.put("/settings/llm", headers=admin_h,
                      json={"provider": "inventado"}).status_code == 422


def test_la_prueba_dice_claro_que_la_clave_no_vale(client, admin_h, guardada, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr("app.llm.httpx.AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, json={})), **kw))
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False and "clave no es valida" in r["error"]


def test_la_prueba_confirma_cuando_funciona(client, admin_h, guardada, monkeypatch):
    enviado = []

    def handler(req):
        enviado.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr("app.llm.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is True and r["modelo"] == "llama-3.3-70b-versatile"
    assert enviado == [f"Bearer {CLAVE}"]          # la clave se usa, pero solo hacia el proveedor


def test_sin_configurar_la_prueba_no_revienta(client, admin_h):
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False and "Ajustes" in r["error"]


def test_los_ajustes_de_ia_son_solo_de_admin(client, isp_h):
    assert client.get("/settings/llm", headers=isp_h).status_code == 403
    assert client.put("/settings/llm", headers=isp_h, json={"api_key": "x"}).status_code == 403
