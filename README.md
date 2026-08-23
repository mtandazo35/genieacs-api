# GenieACS API

API de negocio **multi-tenant** sobre [GenieACS](https://genieacs.com/). En vez de que tu panel o Mikrowisp hablen el TR-069 crudo (paths larguísimos, `%2520`, tipos `xsd`, connection-request, particularidades por marca), expone endpoints limpios:

```
PUT  /devices/{id}/wifi        # SSID / clave / canal / on-off (2.4 y 5 GHz)
PUT  /devices/{id}/ip          # IP LAN, máscara, rango DHCP
PUT  /devices/{id}/pppoe       # activar PPPoE + usuario/clave
PUT  /devices/{id}/dns         # DNS entregado por DHCP (lan) o del enlace (wan)
PUT  /devices/{id}/time        # zona horaria + NTP
POST /devices/{id}/reboot      # reinicio inmediato
PUT  /devices/{id}/schedule-reboot   # reinicio programado diario (HH:MM)
POST /devices/{id}/firmware    # empujar una actualización ya cargada
POST /firmware/upload          # subir un firmware al ACS (admin)
```

GenieACS queda como **motor** por debajo; esta API pone la capa de negocio, la autenticación y la separación por ISP.

FastAPI, con **Swagger automático en `/docs`**.

## ⚡ Quick install (one-liner)

En la VM (Debian 12/13 o Ubuntu 22.04/24.04, como root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh)
```

Instala dependencias, clona en `/opt/genieacs-api`, crea el entorno Python, pide la **URL del NBI de GenieACS** y crea el usuario **admin**, deja el servicio systemd `genieacs-api` corriendo en el puerto **8080** y abre el puerto en UFW si está activo. Al terminar muestra la URL del panel y las credenciales.

Modo no interactivo / automatización:

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh -o /root/install.sh
bash /root/install.sh install     # instalar/actualizar
bash /root/install.sh update      # solo actualizar código y reiniciar
bash /root/install.sh uninstall   # desinstalar
```

## Arquitectura

```
Panel / Mikrowisp / técnico
        │  HTTPS + JWT
        ▼
   genieacs-api  (FastAPI, :8080)   ── usuarios en SQLite, filtro por tag de ISP
        │  NBI HTTP :7557
        ▼
     GenieACS  (cwmp/nbi/fs/ui)     ── habla TR-069 con los CPEs
```

### Multi-tenant por ISP
- Cada CPE lleva un **tag** con el nombre del ISP (p.ej. `altala`).
- Un usuario con rol `isp` solo ve y gestiona los equipos con **su** tag.
- Un usuario `admin` ve toda la flota.

### Reinicios programados
Implementados **con un provision en GenieACS** (no un cron en la API): al programar un horario, la API etiqueta el CPE con `reboot@HH:MM` y asegura el provision/preset `scheduled-reboot`. En cada inform, el provision compara la hora local del equipo con la programada y, al alcanzarla, declara el parámetro `Reboot` con marca de la hora del día → reinicia **una sola vez al día** (idempotente, sin bucle). Precisión = ventana del intervalo de inform (p.ej. 300 s).

## Instalación (en la VM del ACS)

```bash
sudo mkdir -p /opt/genieacs-api && cd /opt/genieacs-api
# copiar el repo aquí (git clone / scp)
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sed -i "s/CAMBIAME/$(python -c 'import secrets;print(secrets.token_hex(32))')/" .env

# crear el primer admin
python manage.py init-admin admin 'ClaveFuerte'

# arrancar (dev)
uvicorn app.main:app --host 0.0.0.0 --port 8080
# o instalar el servicio:
sudo cp genieacs-api.service /etc/systemd/system/
sudo systemctl enable --now genieacs-api
```

## Uso rápido

```bash
# 1) login -> token
TOKEN=$(curl -s -X POST http://localhost:8080/auth/login \
  -d 'username=admin&password=ClaveFuerte' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2) dar de alta un usuario de ISP (solo admin) y etiquetar sus CPEs con ese tag
curl -X POST http://localhost:8080/auth/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"altala","password":"secreta","role":"isp","isp_tag":"altala"}'

# 3) listar dispositivos
curl http://localhost:8080/devices -H "Authorization: Bearer $TOKEN"

# 4) cambiar WiFi 2.4 GHz
curl -X PUT "http://localhost:8080/devices/$DEV/wifi" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"band":"2g","ssid":"MiRed","password":"claveNueva123"}'

# 5) programar reinicio diario 03:00
curl -X PUT "http://localhost:8080/devices/$DEV/schedule-reboot" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"hour":3,"minute":0}'
```

`$DEV` es el `_id` tal cual lo devuelve `/devices` (la API se encarga del `%`-encoding hacia GenieACS).

## Soporte de modelos

La traducción concepto→path TR-069 vive en [app/parammap.py](app/parammap.py). Hoy trae el mapa **TR-098** probado en Cudy WR3000/AX3000. Para añadir otra marca: agregar un dict con sus paths y mapearlo en `pick_map()` según `Manufacturer`/`ProductClass`.

## Notas / límites

- Cambiar de DHCP a PPPoE puede requerir además desactivar la conexión IP WAN según el modelo; el endpoint fija los parámetros PPPoE y su `Enable`.
- La zona horaria (`time`) se pasa tal cual: el formato válido depende del firmware.
- Poner TLS delante (reverse proxy) si la API sale de la red interna.
