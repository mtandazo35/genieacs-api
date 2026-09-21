"""Escenarios de la auditoria tecnica (2026-09-21): cada prueba describe lo que
un atacante o un error de operacion intentaria, y comprueba que no ocurre."""
import asyncio
import hashlib
import socket

import httpx
import pytest

from conftest import ISP_DEV, OTHER_DEV, login


# ---------- multi-tenant / parametros avanzados ----------

def test_isp_no_puede_sacar_su_equipo_del_acs(client, fake, isp_h):
    """Cambiar ManagementServer.URL desconectaria el CPE del ACS (o lo llevaria a otro)."""
    for path in ("InternetGatewayDevice.ManagementServer.URL",
                 "Device.ManagementServer.Password",
                 "InternetGatewayDevice.ManagementServer.ConnectionRequestUsername"):
        r = client.put(f"/devices/{ISP_DEV}/param", headers=isp_h,
                       json={"path": path, "value": "http://acs.atacante.example/"})
        assert r.status_code == 403, path
    assert fake.names_set(ISP_DEV) == []


def test_isp_no_escribe_fuera_del_arbol_del_cpe(client, fake, isp_h):
    r = client.put(f"/devices/{ISP_DEV}/param", headers=isp_h, json={"path": "Tags.ISP-B", "value": True})
    assert r.status_code == 403
    assert fake.names_set(ISP_DEV) == []


def test_isp_si_edita_parametros_normales_de_su_equipo(client, fake, isp_h):
    path = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID"
    r = client.put(f"/devices/{ISP_DEV}/param", headers=isp_h, json={"path": path, "value": "Casa"})
    assert r.status_code == 200
    assert fake.names_set(ISP_DEV) == [path]


def test_admin_si_puede_ajustar_managementserver(client, fake, admin_h):
    path = "InternetGatewayDevice.ManagementServer.PeriodicInformInterval"
    r = client.put(f"/devices/{ISP_DEV}/param", headers=admin_h, json={"path": path, "value": 300})
    assert r.status_code == 200
    assert fake.names_set(ISP_DEV) == [path]


def test_isp_no_ve_la_clave_del_acs_en_el_explorador(client, fake, isp_h, admin_h):
    fake.devices[ISP_DEV]["InternetGatewayDevice"]["ManagementServer"] = {
        "Password": {"_value": "clave-del-acs", "_writable": True, "_type": "xsd:string"},
        "URL": {"_value": "http://acs.example:7547/", "_writable": True, "_type": "xsd:string"},
    }
    params = {p["path"]: p for p in client.get(f"/devices/{ISP_DEV}/params", headers=isp_h).json()["params"]}
    pw = params["InternetGatewayDevice.ManagementServer.Password"]
    assert pw["value"] != "clave-del-acs" and pw["writable"] is False
    admin_params = {p["path"]: p for p in client.get(f"/devices/{ISP_DEV}/params", headers=admin_h).json()["params"]}
    assert admin_params["InternetGatewayDevice.ManagementServer.Password"]["value"] == "clave-del-acs"


def test_isp_no_toca_equipos_de_otro_isp(client, fake, isp_h):
    r = client.post(f"/devices/{OTHER_DEV}/reboot", headers=isp_h)
    assert r.status_code == 403
    assert fake.tasks == []


# ---------- respaldo ----------

def test_respaldo_no_devuelve_claves_pero_restaurar_si_las_aplica(client, fake, isp_h):
    from app import db
    pw_path = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.X_CUDY_Password"
    ssid_path = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID"
    db.save_device_config(ISP_DEV, '{"%s": ["clave-wifi-real", "xsd:string"], "%s": ["Casa", null]}'
                          % (pw_path, ssid_path))
    b = client.get(f"/devices/{ISP_DEV}/backup", headers=isp_h).json()
    assert "clave-wifi-real" not in str(b)
    assert b["params"][ssid_path] == "Casa"
    client.post(f"/devices/{ISP_DEV}/restore", headers=isp_h)
    sent = {v[0]: v[1] for d, n, p in fake.tasks if n == "setParameterValues" for v in p}
    assert sent[pw_path] == "clave-wifi-real"


# ---------- sesiones / JWT ----------

def test_cambiar_clave_cierra_las_sesiones_abiertas(client, isp_h):
    r = client.put("/auth/me/password", headers=isp_h,
                   json={"current_password": "ispa-clave-larga", "new_password": "otra-clave-muy-larga"})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=isp_h).status_code == 401          # token robado: ya no vale
    nuevo = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.get("/auth/me", headers=nuevo).status_code == 200          # quien cambio sigue dentro


def test_reset_de_clave_por_admin_revoca_tokens(client, admin_h, isp_h):
    r = client.put("/auth/users/ispa/password", headers=admin_h, json={"new_password": "reseteada-por-admin"})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=isp_h).status_code == 401


def test_desactivar_usuario_revoca_aunque_se_reactive(client, admin_h, isp_h):
    client.post("/auth/users/ispa/active", headers=admin_h, json={"active": False})
    client.post("/auth/users/ispa/active", headers=admin_h, json={"active": True})
    assert client.get("/auth/me", headers=isp_h).status_code == 401


def test_token_de_usuario_borrado_no_sirve_para_uno_nuevo_con_el_mismo_nombre(client, admin_h):
    client.post("/auth/users", headers=admin_h,
                json={"username": "temporal", "password": "clave-temporal-1", "role": "admin"})
    viejo = login(client, "temporal", "clave-temporal-1")
    client.delete("/auth/users/temporal", headers=admin_h)
    client.post("/auth/users", headers=admin_h,
                json={"username": "temporal", "password": "clave-temporal-2", "role": "isp", "isp_tag": "ISP-A"})
    assert client.get("/auth/me", headers=viejo).status_code == 401


def test_rol_sale_de_la_bd_y_no_del_token(client, isp_h):
    from app import db
    with db.connect() as c:
        c.execute("UPDATE users SET isp_tag='ISP-B' WHERE username='ispa'")
    assert client.get("/auth/me", headers=isp_h).json()["isp"] == "ISP-B"


def test_la_api_no_arranca_sin_secreto_jwt(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    for weak in ("", "CAMBIAME", "corto-de-mas"):
        monkeypatch.setenv("GENIEACS_API_JWT_SECRET", weak)
        config.get_settings.cache_clear()
        with pytest.raises(RuntimeError):
            with TestClient(app):
                pass


# ---------- politica de claves / login ----------

def test_no_se_crean_usuarios_con_clave_corta(client, admin_h):
    r = client.post("/auth/users", headers=admin_h,
                    json={"username": "corto", "password": "123456", "role": "isp", "isp_tag": "ISP-A"})
    assert r.status_code == 422
    assert "12" in str(r.json()["detail"])


def test_fuerza_bruta_se_bloquea_aunque_luego_acierte(client):
    for _ in range(5):
        assert client.post("/auth/login", data={"username": "admin", "password": "mal"}).status_code == 401
    r = client.post("/auth/login", data={"username": "admin", "password": "admin-clave-larga"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_login_queda_auditado_con_ip(client, admin_h):
    client.post("/auth/login", data={"username": "admin", "password": "mal"})
    audit = client.get("/auth/audit", headers=admin_h).json()
    fallos = [a for a in audit if a["action"] == "Login fallido"]
    assert fallos and fallos[0]["client_ip"] and fallos[0]["user"] == "admin"


def test_cambios_auditados_con_ip_y_request_id(client, admin_h):
    r = client.post(f"/devices/{ISP_DEV}/reboot", headers=admin_h)
    rid = r.headers["X-Request-ID"]
    a = next(x for x in client.get("/auth/audit", headers=admin_h).json() if x["path"].endswith("/reboot"))
    assert a["request_id"] == rid and a["client_ip"]


# ---------- firmware ----------

def test_isp_no_ve_ni_envia_archivos_de_configuracion(client, fake, isp_h, admin_h):
    names = [f["_id"] for f in client.get("/firmware", headers=isp_h).json()]
    assert names == ["fw-v2.bin"]
    r = client.post(f"/devices/{ISP_DEV}/firmware", headers=isp_h, json={"file_name": "isp-b-config.cfg"})
    assert r.status_code == 403
    assert not [t for t in fake.tasks if t[1] == "download"]
    r = client.post(f"/devices/{ISP_DEV}/firmware", headers=isp_h, json={"file_name": "fw-v2.bin"})
    assert r.status_code == 200
    assert len(client.get("/firmware", headers=admin_h).json()) == 2


def test_subida_devuelve_sha256_y_llega_integra(client, fake, admin_h):
    data = b"\x7fFIRMWARE" * 1000
    r = client.post("/firmware/upload", headers=admin_h, files={"file": ("fw-v3.bin", data)},
                    data={"file_type": "firmware"})
    assert r.status_code == 200
    assert r.json()["sha256"] == hashlib.sha256(data).hexdigest()
    assert fake.uploaded["fw-v3.bin"] == data


def test_firmware_demasiado_grande_se_rechaza(client, fake, admin_h):
    data = b"A" * (1024 * 1024 + 10)       # limite de pruebas: 1 MB
    r = client.post("/firmware/upload", headers=admin_h, files={"file": ("grande.bin", data)})
    assert r.status_code == 413
    assert "grande.bin" not in fake.uploaded


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:7557/devices/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/fw.bin",
    "http://[::1]/fw.bin",
    "http://[::ffff:192.168.1.1]/fw.bin",
    "file:///etc/passwd",
])
def test_cargar_por_url_no_alcanza_redes_internas(client, fake, admin_h, url):
    r = client.post("/firmware/upload-url", headers=admin_h, json={"url": url})
    assert r.status_code == 400
    assert fake.uploaded == {}


PUBLIC_HOST = "firmware.example.com"
PUBLIC_IP = "93.184.215.14"        # rango de example.com (IANA)


@pytest.fixture
def dns_publico(monkeypatch):
    """firmware.example.com resuelve a una IP publica, sin salir a la red."""
    real = socket.getaddrinfo

    def fake_gai(host, port, *a, **k):
        if host == PUBLIC_HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]
        return real(host, port, *a, **k)
    monkeypatch.setattr("app.netguard.socket.getaddrinfo", fake_gai)


def test_ip_de_documentacion_no_cuenta_como_publica(env):
    from app.netguard import BlockedURL, check_public_url
    with pytest.raises(BlockedURL):
        asyncio.run(check_public_url("http://203.0.113.10/fw.bin", []))


def test_redireccion_hacia_red_interna_se_bloquea(client, fake, admin_h, monkeypatch, dns_publico):
    pedidos = []

    def handler(request):
        pedidos.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://10.0.0.1/fw.bin"})

    real = httpx.AsyncClient
    monkeypatch.setattr("app.routers.firmware.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = client.post("/firmware/upload-url", headers=admin_h, json={"url": f"http://{PUBLIC_HOST}/fw.bin"})
    assert r.status_code == 400
    assert "10.0.0.1" in r.json()["detail"]
    assert pedidos == [f"http://{PUBLIC_HOST}/fw.bin"]      # nunca se pidio la interna


def test_cargar_por_url_publica_funciona(client, fake, admin_h, monkeypatch, dns_publico):
    data = b"FW" * 5000

    def handler(request):
        return httpx.Response(200, content=data)

    real = httpx.AsyncClient
    monkeypatch.setattr("app.routers.firmware.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = client.post("/firmware/upload-url", headers=admin_h, json={"url": f"http://{PUBLIC_HOST}/fw-v4.bin"})
    assert r.status_code == 200, r.text
    assert fake.uploaded["fw-v4.bin"] == data
    assert r.json()["sha256"] == hashlib.sha256(data).hexdigest()


def test_firmware_de_repo_interno_permitido_si_se_autoriza(env, monkeypatch):
    from app.netguard import BlockedURL, check_public_url, parse_networks
    with pytest.raises(BlockedURL):
        asyncio.run(check_public_url("http://10.99.99.20/fw.bin", []))
    asyncio.run(check_public_url("http://10.99.99.20/fw.bin", parse_networks("10.99.99.0/24")))


# ---------- ajustes (NBI) ----------

def test_nbi_no_se_puede_apuntar_a_internet_ni_al_metadata(client, admin_h, monkeypatch, dns_publico):
    from app import runtime
    pedidos = []

    def handler(request):                 # un "NBI" que responderia bien
        pedidos.append(str(request.url))
        return httpx.Response(200, json=[])

    real = httpx.AsyncClient
    monkeypatch.setattr("app.routers.settings.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    for url in (f"http://{PUBLIC_HOST}:7557", "http://169.254.169.254"):
        r = client.put("/settings", headers=admin_h, json={"nbi_url": url})
        assert r.json()["ok"] is False
        assert client.post("/settings/test", headers=admin_h, json={"nbi_url": url}).json()["ok"] is False
    assert pedidos == []                  # la API no llego a conectarse a esos destinos
    assert runtime.nbi_url() == "http://127.0.0.1:7557"
    assert client.post("/settings/test", headers=admin_h, json={"nbi_url": "http://10.99.99.5:7557"}).json()["ok"]
    r = client.put("/settings", headers=admin_h, json={"nbi_url": "http://10.99.99.5:7557"})
    assert r.json()["ok"] is True and runtime.nbi_url() == "http://10.99.99.5:7557"


def test_ajustes_solo_admin(client, isp_h):
    assert client.put("/settings", headers=isp_h, json={"nbi_url": "http://10.0.0.9:7557"}).status_code == 403
