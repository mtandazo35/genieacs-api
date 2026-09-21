"""Auto-restauracion: no debe acumular tareas ni insistir sin fin."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from conftest import ISP_DEV

SSID = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _setup(fake, current_ssid="FABRICA"):
    from app import db
    db.init_db()
    db.save_device_config(ISP_DEV, json.dumps({SSID: ["Casa", "xsd:string"]}))
    db.set_autorestore(ISP_DEV, True)
    lan = fake.devices[ISP_DEV]["InternetGatewayDevice"].setdefault("LANDevice", {})
    lan["1"] = {"WLANConfiguration": {"1": {"SSID": {"_value": current_ssid}}}}


def _inform(fake, when):
    fake.devices[ISP_DEV]["_lastInform"] = when.isoformat().replace("+00:00", "Z")


def _run(now):
    from app import db
    from app.routers.backup import enforce_device
    return asyncio.run(enforce_device(db.list_autorestore()[0], now=now))


def test_equipo_apagado_no_acumula_tareas(fake):
    _setup(fake)
    _inform(fake, T0)
    assert _run(T0 + timedelta(minutes=1)) == "applied"
    # el equipo no vuelve a reportar: los siguientes ciclos no encolan nada
    for i in range(2, 8):
        assert _run(T0 + timedelta(minutes=10 * i)) == "waiting"
    assert len(fake.names_set(ISP_DEV)) == 1


def test_cpe_que_revierte_se_suspende_tras_n_intentos(fake):
    from app import db
    _setup(fake)
    t = T0
    _inform(fake, t)
    for _ in range(3):
        t += timedelta(minutes=10)
        assert _run(t) == "applied"
        _inform(fake, t + timedelta(minutes=1))      # reporta, pero el CPE sigue revertido
    t += timedelta(minutes=10)
    assert _run(t) == "suspended"
    row = db.get_device_config(ISP_DEV)
    assert row["suspended_until"] and SSID in row["last_error"]
    # durante la pausa no se reintenta aunque el equipo reporte
    _inform(fake, t + timedelta(minutes=5))
    assert _run(t + timedelta(minutes=10)) == "skipped"
    assert len(fake.names_set(ISP_DEV)) == 3


def test_config_correcta_reinicia_el_contador(fake):
    from app import db
    _setup(fake)
    _inform(fake, T0)
    assert _run(T0 + timedelta(minutes=1)) == "applied"
    fake.devices[ISP_DEV]["InternetGatewayDevice"]["LANDevice"]["1"]["WLANConfiguration"]["1"]["SSID"]["_value"] = "Casa"
    _inform(fake, T0 + timedelta(minutes=5))
    assert _run(T0 + timedelta(minutes=11)) == "ok"
    assert db.get_device_config(ISP_DEV)["attempts"] == 0


def test_error_en_un_equipo_queda_registrado_y_no_frena_el_resto(fake, monkeypatch):
    from app import db
    from app.genieacs import genie
    from app.routers.backup import enforce_once
    _setup(fake)

    async def boom(*a, **k):
        raise RuntimeError("NBI caido")
    monkeypatch.setattr(genie, "get_device", boom)
    assert asyncio.run(enforce_once()) == 0
    assert "NBI caido" in db.get_device_config(ISP_DEV)["last_error"]
