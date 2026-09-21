"""Entorno de pruebas: BD SQLite temporal y un GenieACS falso en memoria.

Las pruebas ejercitan la API real (FastAPI TestClient) sin red ni ACS: el
cliente `genie` se sustituye por FakeGenie, que registra las tareas que la API
le manda al ACS para poder comprobar QUE llega (o que NO llega) al CPE.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SECRET = "x" * 64
ISP_DEV = "00A0B1-ROUTER-ISPA0001"
OTHER_DEV = "00A0B1-ROUTER-ISPB0001"


class FakeGenie:
    def __init__(self):
        self.devices = {
            ISP_DEV: {"_id": ISP_DEV, "_tags": ["ISP-A"], "_lastInform": "2026-01-01T00:00:00.000Z",
                      "InternetGatewayDevice": {"DeviceInfo": {"Manufacturer": {"_value": "Test"}}}},
            OTHER_DEV: {"_id": OTHER_DEV, "_tags": ["ISP-B"], "_lastInform": "2026-01-01T00:00:00.000Z",
                        "InternetGatewayDevice": {"DeviceInfo": {"Manufacturer": {"_value": "Test"}}}},
        }
        self.tasks = []          # (device_id, task_name, payload)
        self.files = [
            {"_id": "fw-v2.bin", "metadata": {"fileType": "1 Firmware Upgrade Image"}},
            {"_id": "isp-b-config.cfg", "metadata": {"fileType": "3 Vendor Configuration File"}},
        ]
        self.uploaded = {}       # nombre -> bytes

    # --- consultas
    async def query_devices(self, query=None, projection=None):
        q = query or {}
        out = []
        for d in self.devices.values():
            if "_id" in q:
                want = q["_id"]
                if isinstance(want, dict):
                    if d["_id"] not in want.get("$in", []):
                        continue
                elif d["_id"] != want:
                    continue
            if "_tags" in q and q["_tags"] not in d.get("_tags", []):
                continue
            out.append(d)
        return out

    async def get_device(self, device_id, projection=None):
        return self.devices.get(device_id)

    # --- tareas
    def _task(self, device_id, name, payload):
        self.tasks.append((device_id, name, payload))
        return {"applied": True, "queued": False, "task": None}

    async def set_parameter_values(self, device_id, values, connection_request=None):
        return self._task(device_id, "setParameterValues", values)

    async def get_parameter_values(self, device_id, names, connection_request=None):
        return self._task(device_id, "getParameterValues", names)

    async def refresh_object(self, device_id, object_name="InternetGatewayDevice", connection_request=None):
        return self._task(device_id, "refreshObject", object_name)

    async def reboot(self, device_id, connection_request=None):
        return self._task(device_id, "reboot", None)

    async def factory_reset(self, device_id, connection_request=None):
        return self._task(device_id, "factoryReset", None)

    async def download(self, device_id, file_name, file_type="1 Firmware Upgrade Image", connection_request=None):
        return self._task(device_id, "download", (file_name, file_type))

    async def add_tag(self, device_id, tag):
        self.devices[device_id]["_tags"].append(tag)

    async def remove_tag(self, device_id, tag):
        self.devices[device_id]["_tags"].remove(tag)

    # --- archivos
    async def list_files(self):
        return list(self.files)

    async def upload_file(self, file_name, content, file_type="1 Firmware Upgrade Image",
                          oui="", product_class="", version="", size=None):
        if isinstance(content, bytes):
            data = content
        else:
            data = b"".join([c async for c in content])
        assert size is None or size == len(data)
        self.uploaded[file_name] = data
        self.files.append({"_id": file_name, "metadata": {"fileType": file_type}})

    async def delete_file(self, file_name):
        self.files = [f for f in self.files if f["_id"] != file_name]

    async def put_provision(self, name, script):
        pass

    async def put_preset(self, name, preset):
        pass

    def names_set(self, device_id):
        """Paths enviados con setParameterValues a ese equipo."""
        return [v[0] for d, n, p in self.tasks if d == device_id and n == "setParameterValues" for v in p]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GENIEACS_API_JWT_SECRET", SECRET)
    monkeypatch.setenv("GENIEACS_API_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GENIEACS_API_MAX_UPLOAD_MB", "1")
    monkeypatch.chdir(tmp_path)              # que no lea un .env real
    from app import config, ratelimit
    config.get_settings.cache_clear()
    ratelimit.reset()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def fake(env, monkeypatch):
    from app import genieacs
    f = FakeGenie()
    for name in dir(f):
        if not name.startswith("_") and callable(getattr(f, name)) and hasattr(genieacs.genie, name):
            monkeypatch.setattr(genieacs.genie, name, getattr(f, name))
    return f


@pytest.fixture
def client(fake):
    from fastapi.testclient import TestClient
    from app import db
    from app.main import app
    from app.security import hash_password
    db.init_db()
    db.create_user("admin", hash_password("admin-clave-larga"), "admin", None)
    db.create_user("ispa", hash_password("ispa-clave-larga"), "isp", "ISP-A")
    with TestClient(app) as c:
        yield c


def login(client, user, password):
    r = client.post("/auth/login", data={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
def admin_h(client):
    return login(client, "admin", "admin-clave-larga")


@pytest.fixture
def isp_h(client):
    return login(client, "ispa", "ispa-clave-larga")
