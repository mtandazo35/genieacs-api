"""Configurar el proveedor de IA desde el panel.

Lo delicado es la clave: se guarda para poder usarla, pero **no puede salir**
por la API ni quedar escrita en la auditoria.
"""
import httpx
import pytest

CLAVE = "gsk-clave-secreta-de-prueba-123456"


MODELOS = ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "whisper-large-v3")


def proveedor(monkeypatch, modelos=MODELOS, chat=200, chat_json=None, models=200,
              models_json=None, visto=None):
    """Proveedor simulado que responde a /models y a /chat/completions.

    La prueba real consulta primero /models: eso valida clave y URL sin gastar
    tokens y permite decir que modelos hay de verdad."""
    def handler(req):
        if visto is not None:
            visto.append((req.method, str(req.url), req.headers.get("authorization")))
        if req.url.path.endswith("/models"):
            return httpx.Response(models, json=models_json if models_json is not None
                                  else {"data": [{"id": m} for m in modelos]})
        return httpx.Response(chat, json=chat_json if chat_json is not None
                              else {"choices": [{"message": {"content": "ok"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr("app.llm.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


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
    visto = []
    proveedor(monkeypatch, visto=visto)
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is True and r["modelo"] == "llama-3.3-70b-versatile"
    # primero la lista de modelos, despues una peticion minima al modelo elegido
    assert [m for m, _u, _a in visto] == ["GET", "POST"]
    assert [u.rsplit("/v1", 1)[-1] for _m, u, _a in visto] == ["/models", "/chat/completions"]
    # la clave se usa, pero solo hacia el proveedor
    assert {a for _m, _u, a in visto} == {f"Bearer {CLAVE}"}


def test_la_prueba_dice_que_modelos_hay(client, admin_h, guardada, monkeypatch):
    """El panel los ofrece para elegir: adivinar el nombre del modelo fue el fallo
    que costo la tarde del 2026-09-27."""
    proveedor(monkeypatch)
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["modelos"] == sorted(MODELOS)


def test_un_modelo_que_el_proveedor_no_tiene_se_dice_con_la_lista(client, admin_h, monkeypatch):
    """Antes esto era un 404 mudo que parecia culpa de la URL."""
    visto = []
    proveedor(monkeypatch, visto=visto)
    r = client.post("/settings/llm/test", headers=admin_h,
                    json={"api_key": "x", "model": "llama-3.3-70b"}).json()
    assert r["ok"] is False
    assert "no tiene el modelo 'llama-3.3-70b'" in r["error"]
    assert "llama-3.3-70b-versatile" in r["error"]          # el que si existe, a la vista
    assert [m for m, _u, _a in visto] == ["GET"]            # no se gasta una peticion de chat


def test_sin_modelo_se_elige_uno_que_el_proveedor_tenga(client, admin_h, monkeypatch):
    proveedor(monkeypatch, modelos=("mixtral-guardian", "modelo-raro-9b"))
    r = client.post("/settings/llm/test", headers=admin_h,
                    json={"api_key": "x", "model": ""}).json()
    assert r["ok"] is True and r["modelo"] == "modelo-raro-9b"   # los guard no son de chat


def test_el_error_del_proveedor_se_repite_tal_cual(client, admin_h, guardada, monkeypatch):
    """Sin el mensaje del proveedor estabamos adivinando la causa."""
    proveedor(monkeypatch, chat=400,
              chat_json={"error": {"message": "organization has no access to this model",
                                   "code": "model_not_found"}})
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False and "organization has no access to this model" in r["error"]


def test_sin_configurar_la_prueba_no_revienta(client, admin_h):
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False and "Ajustes" in r["error"]


def test_los_ajustes_de_ia_son_solo_de_admin(client, isp_h):
    assert client.get("/settings/llm", headers=isp_h).status_code == 403
    assert client.put("/settings/llm", headers=isp_h, json={"api_key": "x"}).status_code == 403

# ---------- probar antes de guardar (el fallo real del 2026-09-27) ----------

def test_se_puede_probar_una_clave_sin_haberla_guardado(client, admin_h, monkeypatch):
    """Antes habia que guardar para probar: si la clave no servia, ya la tenias dentro."""
    visto = []
    proveedor(monkeypatch, visto=visto)
    r = client.post("/settings/llm/test", headers=admin_h,
                    json={"provider": "groq", "api_key": "clave-sin-guardar"}).json()
    assert r["ok"] is True
    assert {a for _m, _u, a in visto} == {"Bearer clave-sin-guardar"}
    # y no se ha guardado nada
    assert client.get("/settings/llm", headers=admin_h).json()["key_set"] is False


def test_probar_sin_cuerpo_sigue_usando_la_guardada(client, admin_h, guardada, monkeypatch):
    proveedor(monkeypatch)
    assert client.post("/settings/llm/test", headers=admin_h).json()["ok"] is True


def test_la_url_de_la_consola_se_corrige_sola(client, admin_h):
    """console.groq.com es la web del proveedor, no su API. Como sabemos cual es
    la equivalente, se cambia sola en vez de dejar al usuario atascado."""
    r = client.put("/settings/llm", headers=admin_h,
                   json={"base_url": "https://console.groq.com/keys"})
    assert r.status_code == 200
    assert "api.groq.com/openai/v1" in r.json()["aviso"]
    guardada = client.get("/settings/llm", headers=admin_h).json()["base_url"]
    assert guardada == "https://api.groq.com/openai/v1"


def test_al_probar_tambien_se_corrige_y_se_avisa(client, admin_h, monkeypatch):
    visto = []
    proveedor(monkeypatch, visto=visto)
    r = client.post("/settings/llm/test", headers=admin_h,
                    json={"api_key": "x", "base_url": "https://console.groq.com"}).json()
    assert r["ok"] is True and "api.groq.com/openai/v1" in r["aviso"]
    assert [u for _m, u, _a in visto] == ["https://api.groq.com/openai/v1/models",
                                          "https://api.groq.com/openai/v1/chat/completions"]


def test_una_consola_que_no_sabemos_traducir_se_rechaza(client, admin_h):
    """Sin equivalencia conocida, mejor decirlo que guardar algo que no funciona."""
    r = client.put("/settings/llm", headers=admin_h,
                   json={"base_url": "https://console.proveedor-raro.example"})
    assert r.status_code == 400 and "consola web" in r.json()["detail"]


def test_la_url_correcta_del_api_si_se_acepta(client, admin_h):
    r = client.put("/settings/llm", headers=admin_h,
                   json={"base_url": "https://api.groq.com/openai/v1"})
    assert r.status_code == 200
    assert client.get("/settings/llm", headers=admin_h).json()["base_url"] == "https://api.groq.com/openai/v1"


def test_un_404_del_proveedor_menciona_la_url(client, admin_h, guardada, monkeypatch):
    """Una URL que no es API no tiene /models: se dice la URL y el codigo, no se adivina."""
    proveedor(monkeypatch, models=404, models_json={})
    r = client.post("/settings/llm/test", headers=admin_h).json()
    assert r["ok"] is False
    assert "api.groq.com/openai/v1" in r["error"] and "404" in r["error"]


# ---------- el panel ----------
_EST = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "static"
PANEL_JS = (_EST / "app.js").read_text(encoding="utf-8")
PANEL_HTML = (_EST / "index.html").read_text(encoding="utf-8")
PANEL_CSS = (_EST / "styles.css").read_text(encoding="utf-8")


def test_el_panel_escribe_el_modelo_comprobado():
    """Si el campo Modelo esta vacio, tras una prueba buena queda el que respondio:
    la propuesta usa ese y no hay que volver a preguntar al proveedor."""
    assert 'if (r.ok && !$("#llm-model").value.trim()) $("#llm-model").value = r.modelo;' in PANEL_JS


def test_el_panel_ofrece_los_modelos_del_proveedor():
    assert "pintarModelos(r.modelos)" in PANEL_JS
    assert 'id="llm-modelos"' in PANEL_HTML and 'list="llm-modelos"' in PANEL_HTML
    assert "data-modelo" in PANEL_JS and 'id="llm-modelos-chips"' in PANEL_HTML
    # un chip sin estilo sale como un boton crudo en medio del formulario
    assert ".chips" in PANEL_CSS and ".chip{" in PANEL_CSS
