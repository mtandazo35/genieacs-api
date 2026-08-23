"""Acciones de sistema: reinicio, factory reset, firmware y reinicios programados.

Reinicios programados = provision en GenieACS (decision de diseno):
- La API etiqueta el CPE con  reboot@HH:MM
- Un provision global 'scheduled-reboot' corre en cada inform (via preset) y,
  cuando la hora local del equipo alcanza la programada, declara el parametro
  especial Reboot con un timestamp = hora programada de HOY. Como Reboot solo
  dispara cuando el valor declarado es MAS NUEVO que el guardado, reinicia una
  sola vez al dia (idempotente, sin bucle).
"""
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..config import get_settings
from ..deps import authorized_device, current_user, require_admin
from ..genieacs import genie
from ..schemas import ActionResult, FirmwarefromServer, ScheduleRebootIn

router = APIRouter(tags=["system"])

PROVISION_NAME = "scheduled-reboot"
PRESET_NAME = "scheduled-reboot"
# Tag marcador de coincidencia EXACTA. GenieACS no acepta $regex sobre _tags en
# las precondiciones (lanza "Invalid tag query" y mata el worker cwmp), asi que
# marcamos los equipos con horario con este tag fijo y filtramos por el.
SCHED_MARKER = "sched-reboot"

# --- JS del provision (se declara una vez, es global) ---------------------
_PROVISION_JS = r"""
// scheduled-reboot: reinicia el CPE a la hora del tag reboot@HH:MM
const now = Date.now();
const decl = declare("InternetGatewayDevice.Time.CurrentLocalTime", {value: now});
let clt = null;
for (const p of decl) clt = p.value && p.value[0];

const tags = declare("Tags.*", {value: 1});
let hh = null, mm = null;
for (const t of tags) {
  const name = t.path[t.path.length - 1];
  const m = /^reboot@(\d{1,2}):(\d{2})$/.exec(name);
  if (m) { hh = parseInt(m[1], 10); mm = parseInt(m[2], 10); }
}

if (clt && hh !== null) {
  const g = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(clt);
  if (g) {
    const Y = +g[1], Mo = +g[2], D = +g[3], H = +g[4], Mi = +g[5];
    const scheduled = Date.UTC(Y, Mo - 1, D, hh, mm, 0);
    const cur = Date.UTC(Y, Mo - 1, D, H, Mi, 0);
    if (cur >= scheduled) {
      declare("Reboot", {value: now}, {value: scheduled});
    }
  }
}
"""


async def _ensure_scheduled_reboot_objects() -> None:
    """Crea/actualiza (idempotente) el provision y el preset globales."""
    await genie.put_provision(PROVISION_NAME, _PROVISION_JS)
    await genie.put_preset(PRESET_NAME, {
        "weight": 0,
        # coincidencia exacta por el tag marcador (NO usar $regex sobre _tags)
        "precondition": f'{{"_tags":"{SCHED_MARKER}"}}',
        "configurations": [{"type": "provision", "name": PROVISION_NAME}],
    })


def _reboot_tags(dev: dict) -> list[str]:
    return [t for t in (dev.get("_tags") or []) if t.startswith("reboot@")]


# --- reinicio / factory ---------------------------------------------------
@router.post("/devices/{device_id}/reboot", response_model=ActionResult)
async def reboot(device_id: str, dev=Depends(authorized_device)):
    res = await genie.reboot(device_id)
    return ActionResult(applied=res["applied"], queued=res["queued"])


@router.post("/devices/{device_id}/factory-reset", response_model=ActionResult,
             dependencies=[Depends(require_admin)])
async def factory_reset(device_id: str, dev=Depends(authorized_device)):
    res = await genie.factory_reset(device_id)
    return ActionResult(applied=res["applied"], queued=res["queued"],
                        detail="Factory reset enviado")


# --- reinicios programados ------------------------------------------------
@router.get("/devices/{device_id}/schedule-reboot")
async def get_schedule(device_id: str, dev=Depends(authorized_device)):
    tags = _reboot_tags(dev)
    if not tags:
        return {"scheduled": False}
    m = re.match(r"^reboot@(\d{1,2}):(\d{2})$", tags[0])
    return {"scheduled": True, "hour": int(m.group(1)), "minute": int(m.group(2)), "tag": tags[0]}


@router.put("/devices/{device_id}/schedule-reboot")
async def set_schedule(device_id: str, body: ScheduleRebootIn, dev=Depends(authorized_device)):
    await _ensure_scheduled_reboot_objects()
    # quitar horarios previos
    for t in _reboot_tags(dev):
        await genie.remove_tag(device_id, t)
    tag = f"{get_settings().reboot_tag_prefix}{body.hour:02d}:{body.minute:02d}"
    await genie.add_tag(device_id, tag)
    await genie.add_tag(device_id, SCHED_MARKER)   # activa el preset (match exacto)
    return {"ok": True, "scheduled": True, "hour": body.hour, "minute": body.minute, "tag": tag,
            "detail": "Se reiniciara a esa hora (dentro de la ventana del intervalo de inform)."}


@router.delete("/devices/{device_id}/schedule-reboot")
async def clear_schedule(device_id: str, dev=Depends(authorized_device)):
    removed = _reboot_tags(dev)
    for t in removed:
        await genie.remove_tag(device_id, t)
    if SCHED_MARKER in (dev.get("_tags") or []):
        await genie.remove_tag(device_id, SCHED_MARKER)
    return {"ok": True, "removed": removed}


# --- firmware / actualizaciones ------------------------------------------
@router.get("/firmware")
async def list_firmware(user=Depends(current_user)):
    return await genie.list_files()


@router.post("/firmware/upload", dependencies=[Depends(require_admin)])
async def upload_firmware(
    file: UploadFile = File(...),
    product_class: str = Form(""),
    oui: str = Form(""),
    version: str = Form(""),
):
    content = await file.read()
    await genie.upload_file(file.filename, content, "1 Firmware Upgrade Image",
                            oui=oui, product_class=product_class, version=version)
    return {"ok": True, "file_name": file.filename, "size": len(content)}


@router.post("/devices/{device_id}/firmware", response_model=ActionResult)
async def push_firmware(device_id: str, body: FirmwarefromServer, dev=Depends(authorized_device)):
    res = await genie.download(device_id, body.file_name)
    return ActionResult(applied=res["applied"], queued=res["queued"],
                        detail=f"Descarga de {body.file_name} enviada")
