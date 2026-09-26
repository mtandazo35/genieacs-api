"""Descubrimiento de equipos nuevos.

Un CPE recien vinculado llega sin tag y ningun ISP lo ve. El descubrimiento lo
detecta y propone a que ISP pertenece por la IP desde la que informa. Empieza
sugiriendo: un tag mal puesto le daria a un ISP acceso a equipos de otro.
"""
from app import db, discovery
from conftest import ISP_DEV, OTHER_DEV


def _nuevo(fake, dev_id, ip, tags=None):
    fake.devices[dev_id] = {
        "_id": dev_id, "_tags": tags if tags is not None else [],
        "_lastInform": "2026-09-26T21:00:00.000Z",
        "_deviceId": {"_Manufacturer": "TP-Link", "_ProductClass": "XX530"},
        "Device": {"ManagementServer": {
            "ConnectionRequestURL": {"_value": f"http://{ip}:7547/xyz", "_writable": False}}},
    }


def test_un_equipo_sin_tag_aparece_como_pendiente(client, fake, admin_h):
    _nuevo(fake, ISP_DEV, "100.125.125.2")
    r = client.get("/discovery", headers=admin_h).json()
    assert r["modo"] == "sugerencia"
    pend = {e["id"]: e for e in r["equipos"]}
    assert ISP_DEV in pend and pend[ISP_DEV]["ip"] == "100.125.125.2"
    assert pend[ISP_DEV]["isp_tag"] is None          # aun no hay rangos
    assert "no esta en ningun rango" in pend[ISP_DEV]["motivo"]


def test_con_un_rango_configurado_se_sugiere_el_isp(client, fake, admin_h):
    _nuevo(fake, ISP_DEV, "100.125.125.2")
    client.post("/discovery/rules", headers=admin_h,
                json={"cidr": "100.125.125.0/24", "isp_tag": "ISP-A"})
    pend = client.get("/discovery", headers=admin_h).json()["equipos"]
    yo = next(e for e in pend if e["id"] == ISP_DEV)
    assert yo["isp_tag"] == "ISP-A" and "100.125.125.0/24" in yo["motivo"]
    assert fake.devices[ISP_DEV]["_tags"] == []      # en modo sugerencia NO toca nada


def test_el_barrido_en_modo_sugerencia_no_toca_ningun_equipo(client, fake, admin_h):
    """La garantia del modo sugerencia: cuenta lo que haria, pero no lo hace."""
    import asyncio
    _nuevo(fake, ISP_DEV, "100.125.125.2")
    client.post("/discovery/rules", headers=admin_h,
                json={"cidr": "100.125.125.0/24", "isp_tag": "ISP-A"})
    res = asyncio.run(discovery.ciclo())
    assert res == {"pendientes": 1, "asignados": 0, "modo": "sugerencia"}
    assert fake.devices[ISP_DEV]["_tags"] == []
    assert not [t for t in fake.tasks if t[1] == "addTag"]


def test_en_modo_automatico_si_le_pone_el_tag(client, fake, admin_h):
    import asyncio
    _nuevo(fake, ISP_DEV, "100.125.125.2")
    client.post("/discovery/rules", headers=admin_h,
                json={"cidr": "100.125.125.0/24", "isp_tag": "ISP-A"})
    client.put("/discovery/mode", headers=admin_h, json={"auto": True})
    res = asyncio.run(discovery.ciclo())
    assert res == {"pendientes": 1, "asignados": 1, "modo": "automatico"}
    assert fake.devices[ISP_DEV]["_tags"] == ["ISP-A"]


def test_gana_el_rango_mas_especifico(client, fake, admin_h):
    _nuevo(fake, ISP_DEV, "10.20.30.40")
    client.post("/discovery/rules", headers=admin_h, json={"cidr": "10.0.0.0/8", "isp_tag": "ISP-GRANDE"})
    client.post("/discovery/rules", headers=admin_h, json={"cidr": "10.20.30.0/24", "isp_tag": "ISP-B"})
    yo = next(e for e in client.get("/discovery", headers=admin_h).json()["equipos"]
              if e["id"] == ISP_DEV)
    assert yo["isp_tag"] == "ISP-B"


def test_si_dos_isp_reclaman_el_mismo_rango_no_se_asigna(client, fake, admin_h):
    """Ante la duda, no se asigna: un tag mal puesto cruza clientes."""
    _nuevo(fake, ISP_DEV, "10.20.30.40")
    client.post("/discovery/rules", headers=admin_h, json={"cidr": "10.20.30.0/24", "isp_tag": "ISP-A"})
    client.post("/discovery/rules", headers=admin_h, json={"cidr": "10.20.30.0/24", "isp_tag": "ISP-B"})
    yo = next(e for e in client.get("/discovery", headers=admin_h).json()["equipos"]
              if e["id"] == ISP_DEV)
    assert yo["isp_tag"] is None and "varios ISP" in yo["motivo"]


def test_un_equipo_que_ya_tiene_isp_no_se_toca(client, fake, admin_h):
    _nuevo(fake, ISP_DEV, "100.125.125.2", tags=["ISP-A"])
    pend = client.get("/discovery", headers=admin_h).json()["equipos"]
    assert all(e["id"] != ISP_DEV for e in pend)


def test_los_tags_del_panel_no_cuentan_como_tenencia(client, fake, admin_h):
    """sched-reboot y reboot@HH:MM los pone el propio panel: el equipo sigue huerfano."""
    _nuevo(fake, ISP_DEV, "100.125.125.2", tags=["sched-reboot", "reboot@03:00"])
    pend = client.get("/discovery", headers=admin_h).json()["equipos"]
    assert any(e["id"] == ISP_DEV for e in pend)
    assert discovery.tags_de_tenencia(["sched-reboot", "reboot@03:00", "ISP-A"]) == ["ISP-A"]


def test_asignar_a_mano_pone_el_tag_y_queda_auditado(client, fake, admin_h):
    _nuevo(fake, ISP_DEV, "100.125.125.2")
    r = client.post("/discovery/assign", headers=admin_h,
                    json={"device_id": ISP_DEV, "isp_tag": "ISP-A"})
    assert r.status_code == 200
    assert fake.devices[ISP_DEV]["_tags"] == ["ISP-A"]
    audit = client.get("/auth/audit", headers=admin_h).json()
    assert any("ISP-A" in (a["action"] or "") for a in audit)


def test_asignar_un_equipo_inexistente_da_404(client, fake, admin_h):
    r = client.post("/discovery/assign", headers=admin_h,
                    json={"device_id": "NO-EXISTE", "isp_tag": "ISP-A"})
    assert r.status_code == 404


def test_rango_invalido_se_rechaza(client, admin_h):
    r = client.post("/discovery/rules", headers=admin_h,
                    json={"cidr": "no-es-una-red", "isp_tag": "ISP-A"})
    assert r.status_code == 400
    assert db.list_owner_rules() == []


def test_el_descubrimiento_es_solo_de_admin(client, isp_h):
    assert client.get("/discovery", headers=isp_h).status_code == 403
    assert client.post("/discovery/assign", headers=isp_h,
                       json={"device_id": OTHER_DEV, "isp_tag": "ISP-A"}).status_code == 403


def test_sin_ip_utilizable_no_se_inventa_un_isp(fake):
    tag, motivo = discovery.isp_para(None, [{"cidr": "10.0.0.0/8", "isp_tag": "ISP-A"}])
    assert tag is None and "no reporta" in motivo
    tag, motivo = discovery.isp_para("no-es-ip", [{"cidr": "10.0.0.0/8", "isp_tag": "ISP-A"}])
    assert tag is None and "no valida" in motivo
