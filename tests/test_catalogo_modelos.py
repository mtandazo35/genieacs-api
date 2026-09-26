"""Catalogo de perfiles por modelo + firmware.

El perfil es del MODELO, no del equipo: el segundo router igual no tiene que
volver a aprenderse nada. Y cuando una deduccion no acierta, se corrige la ruta
de ese concepto sin tocar codigo.
"""
from app import catalog, db
from conftest import ISP_DEV, OTHER_DEV, login
from test_perfil_derivado import ex511, v


def _equipo(fake, dev_id, doc, modelo="EX511", fw="1.0.0", fabricante="TP-Link"):
    """Deja en el ACS falso un equipo TR-181 con identidad conocida."""
    arbol = {k: dict(vv) for k, vv in doc.items()}
    arbol["Device"]["DeviceInfo"] = {"ModelName": v(modelo), "SoftwareVersion": v(fw)}
    fake.devices[dev_id] = {
        "_id": dev_id, "_tags": fake.devices[dev_id]["_tags"],
        "_lastInform": "2026-09-26T21:00:00.000Z",
        "_deviceId": {"_Manufacturer": fabricante, "_ProductClass": "Device2",
                      "_SerialNumber": dev_id[-6:]},
        **arbol,
    }


def test_el_perfil_se_aprende_al_abrir_la_ficha(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    key = st["profile"]["key"]
    assert key == "TP-Link|Device2|EX511|1.0.0"
    guardado = db.get_model_profile(key)
    assert guardado is not None and '"wan": "4"' in guardado["profile"]


def test_dos_equipos_del_mismo_modelo_comparten_perfil(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    _equipo(fake, OTHER_DEV, ex511())
    k1 = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()["profile"]["key"]
    k2 = client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h).json()["profile"]["key"]
    assert k1 == k2
    assert len(db.list_model_profiles()) == 1


def test_otro_firmware_es_otro_perfil(client, fake, admin_h):
    """Un firmware nuevo puede mover las instancias: no se reutiliza el perfil viejo."""
    _equipo(fake, ISP_DEV, ex511(), fw="1.0.0")
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    _equipo(fake, OTHER_DEV, ex511(), fw="2.0.0")
    client.get(f"/devices/{OTHER_DEV}/status", headers=admin_h)
    claves = sorted(p["key"] for p in db.list_model_profiles())
    assert claves == ["TP-Link|Device2|EX511|1.0.0", "TP-Link|Device2|EX511|2.0.0"]


def test_una_correccion_manual_manda_sobre_la_deduccion(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["wifi_5g_ssid"] == "RED-5G"          # deducido: SSID.3
    key = st["profile"]["key"]

    r = client.put(f"/profiles/{key}/override", headers=admin_h,
                   json={"concept": "wifi_5g_ssid", "path": "Device.WiFi.SSID.4.SSID"})
    assert r.status_code == 200
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["wifi_5g_ssid"] == "invitados-5g"    # ahora lee la ruta corregida

    client.put(f"/profiles/{key}/override", headers=admin_h,
               json={"concept": "wifi_5g_ssid", "path": None})
    st = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()
    assert st["wifi_5g_ssid"] == "RED-5G"          # borrada, vuelve la deduccion


def test_el_catalogo_se_lista_con_su_evidencia(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    perfiles = client.get("/profiles", headers=admin_h).json()
    assert len(perfiles) == 1
    p = perfiles[0]
    assert p["model"] == "EX511" and p["profile"]["wan"] == "4"
    assert "ruta por defecto" in p["profile"]["evidencia"]["wan"]


def test_el_catalogo_es_solo_de_admin(client, fake, isp_h, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert client.get("/profiles", headers=isp_h).status_code == 403
    assert client.put("/profiles/x/override", headers=isp_h,
                      json={"concept": "wifi_2g_ssid", "path": "X"}).status_code == 403


def test_corregir_un_perfil_queda_auditado(client, fake, admin_h):
    _equipo(fake, ISP_DEV, ex511())
    key = client.get(f"/devices/{ISP_DEV}/status", headers=admin_h).json()["profile"]["key"]
    client.put(f"/profiles/{key}/override", headers=admin_h,
               json={"concept": "lan_ip", "path": "Device.IP.Interface.1.IPv4Address.1.IPAddress"})
    audit = client.get("/auth/audit", headers=admin_h).json()
    assert any("lan_ip" in (a["action"] or "") for a in audit)


def test_no_se_reescribe_el_perfil_si_no_cambio(client, fake, admin_h, monkeypatch):
    """Abrir la ficha no debe escribir en la BD en cada lectura."""
    _equipo(fake, ISP_DEV, ex511())
    client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    escrituras = []
    real = db.upsert_model_profile
    monkeypatch.setattr(db, "upsert_model_profile",
                        lambda *a, **k: (escrituras.append(a[0]), real(*a, **k))[1])
    monkeypatch.setattr(catalog.db, "upsert_model_profile", db.upsert_model_profile)
    for _ in range(3):
        client.get(f"/devices/{ISP_DEV}/status", headers=admin_h)
    assert escrituras == []


def test_perfil_inexistente_da_404(client, admin_h):
    assert client.get("/profiles/marca|clase|modelo|fw", headers=admin_h).status_code == 404
    assert client.put("/profiles/no|existe|x|y/override", headers=admin_h,
                      json={"concept": "lan_ip", "path": "X"}).status_code == 404


def test_un_isp_tambien_se_beneficia_del_perfil(client, fake, isp_h, admin_h):
    """El ISP no ve el catalogo, pero su ficha usa el perfil igual."""
    _equipo(fake, ISP_DEV, ex511())
    st = client.get(f"/devices/{ISP_DEV}/status", headers=isp_h).json()
    assert st["wan_ip"] == "198.51.100.3"
    assert login(client, "admin", "admin-clave-larga")   # el admin sigue entrando
