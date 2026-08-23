# GenieACS API

Panel web + **API de negocio multi-tenant** sobre [GenieACS](https://genieacs.com/). En vez de hablar TR-069 crudo (paths largos, `%2520`, tipos `xsd`, connection-request, particularidades por marca), expone endpoints limpios y un panel para soporte/ISP.

GenieACS queda como **motor** por debajo; esta API pone la capa de negocio, la autenticación, la separación por ISP y el panel. FastAPI, con **Swagger interactivo en `/docs`** (referencia siempre actualizada) y **panel web en `/`**.

📄 [CHANGELOG](CHANGELOG.md) · 🚀 [Notas de despliegue (DEPLOY.md)](DEPLOY.md)

## ⚡ Quick install (one-liner)

En la VM (Debian 12/13 o Ubuntu 22.04/24.04, como root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh)
```

Instala dependencias, clona en `/opt/genieacs-api`, crea el entorno Python, pide la **URL del NBI de GenieACS**, crea el usuario **admin**, deja el servicio systemd `genieacs-api` en el puerto **8080** y abre el puerto en UFW si está activo.

Modo no interactivo:

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh -o /root/install.sh
bash /root/install.sh install     # instalar/actualizar
bash /root/install.sh update      # solo actualizar código y reiniciar
bash /root/install.sh uninstall   # desinstalar
```

## Arquitectura

```
Panel web / Mikrowisp / técnico
        │  HTTP + JWT (Bearer)
        ▼
   genieacs-api  (FastAPI, :8080)   ── usuarios y metadatos en SQLite; filtro por tag de ISP
        │  NBI HTTP :7557
        ▼
     GenieACS  (cwmp/nbi/fs/ui)     ── habla TR-069 con los CPEs
```

- **Multi-tenant por ISP**: cada CPE lleva un **tag** con el nombre del ISP. Un usuario `isp` solo ve/gestiona equipos con **su** tag; un `admin` ve toda la flota.
- **`$DEV`** en los ejemplos es el `_id` tal cual lo devuelve `/devices`. La API se encarga del `%`-encoding hacia GenieACS; en tus llamadas HTTP debes URL-encodearlo (el `_id` suele traer `%20`/`%2E`).
- Autenticación: `Authorization: Bearer <token>` en todo salvo `/auth/login`, `/health` y los estáticos del panel.

## Referencia de la API

> La referencia viva e interactiva está en **`/docs`** (Swagger). Este es el resumen.

### Autenticación y cuenta
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| POST | `/auth/login` | público | form `username`,`password` → `{access_token, role, isp}` |
| GET  | `/auth/me` | cualquiera | datos del usuario actual |
| PUT  | `/auth/me/password` | cualquiera | `{current_password, new_password}` cambiar la propia clave |

### Usuarios (solo admin)
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/auth/users` | listar usuarios |
| POST | `/auth/users` | `{username,password,role(admin\|isp),isp_tag}` crear |
| PUT  | `/auth/users/{u}/password` | `{new_password}` resetear clave de otro |
| POST | `/auth/users/{u}/active` | `{active:bool}` activar/desactivar |
| DELETE | `/auth/users/{u}` | eliminar (no el último admin ni a sí mismo) |

### Equipos
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/devices` | lista (id, name, customer, tags, modelo, firmware, último inform), filtrada por tenencia |
| GET  | `/devices/{id}/status` | ficha de estado (dispositivo, WAN activa, LAN, WiFi con clientes, PPPoE con estado, MAC, name/customer) |
| POST | `/devices/{id}/read` | pide al CPE los parámetros de estado (getParameterValues) |
| POST | `/devices/{id}/refresh?object=` | GetParameterNames de la raíz (o del `object` dado) |
| POST | `/devices/read-bulk` | lectura masiva `{all\|tag\|model\|device_ids}` (respeta tenencia) |
| GET  | `/devices/{id}/params?search=&writable_only=` | **todo** el árbol del modelo (Avanzado) |
| PUT  | `/devices/{id}/param` | `{path,value,type?}` escribir cualquier parámetro |
| GET  | `/devices/{id}/hosts` | clientes conectados (LAN hosts): hostname, IP, MAC, conexión, activo |

### Identificación (nombre / cliente)
> **Distinto de los tags de GenieACS.** El nombre/cliente es un campo propio del panel (BD SQLite), pensado como identificación legible del abonado. Los *tags* de GenieACS son para agrupar/filtrar y disparar presets. Cambiar un tag en GenieACS **no** cambia el nombre del panel, y viceversa.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/devices/{id}/label` | `{name, customer, notes}` |
| PUT | `/devices/{id}/label` | `{name?, customer?, notes?}` asignar/editar |

### Configuración del CPE
| Método | Ruta | Cuerpo |
|---|---|---|
| PUT | `/devices/{id}/wifi` | `{band:"2g"\|"5g", ssid?, password?, enable?, channel?, hidden?}` |
| PUT | `/devices/{id}/ip` | `{lan_ip?, lan_mask?, dhcp_enable?, dhcp_min?, dhcp_max?, dhcp_lease?}` (LAN) |
| GET | `/devices/{id}/wan` | lista **todas** las conexiones WAN y marca la activa |
| PUT | `/devices/{id}/wan` | `{mode:"dhcp"}` · `{mode:"static", ip, mask, gateway, dns?, mtu?}` · `{mode:"pppoe", username, password?}` |
| PUT | `/devices/{id}/dns` | `{scope:"lan"\|"wan", servers:[...]}` |
| PUT | `/devices/{id}/time` | `{timezone?, ntp1?, ntp2?}` |

**WAN — validaciones de seguridad (modo static):** IP/máscara/gateway válidos, gateway en la misma subred que la IP, y la nueva IP debe estar en la **misma red que la IP WAN actual** del equipo (si no → `400`), para no perder el enlace con el ACS. Se escribe en la **conexión WAN activa**, no en una instancia fija.

### Sistema / acciones
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/devices/{id}/reboot` | reinicio inmediato |
| POST | `/devices/{id}/factory-reset` | (admin) restaurar de fábrica |
| GET/PUT/DELETE | `/devices/{id}/schedule-reboot` | reinicio programado diario `{hour, minute}` |
| POST | `/devices/{id}/firmware` | empujar un archivo ya cargado `{file_name}` (detecta si es firmware o config) |

### Respaldo / auto-restauración
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/devices/{id}/backup` | guarda la config actual como respaldo |
| GET  | `/devices/{id}/backup` | ver respaldo + estado auto-restauración |
| POST | `/devices/{id}/restore` | reaplica la config guardada |
| POST | `/devices/{id}/autorestore` | `{enabled:bool}` vigilar y reaplicar tras factory reset |

El respaldo **se fusiona con cada cambio** aplicado (nunca queda viejo). Un bucle en la API detecta *drift* vs la config deseada y la reaplica **solo si el equipo vuelve a hablar con el ACS**.

### Firmware / archivos (admin)
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/firmware` | listar archivos cargados (firmware y config) |
| POST | `/firmware/upload` | multipart `file` + `file_type(firmware\|config)` + `product_class?/oui?/version?` |
| POST | `/firmware/upload-url` | `{url, file_name?, file_type, ...}` descarga server-side |
| DELETE | `/firmware/{name}` | borrar |
| POST | `/firmware/push` | envío masivo `{file_name, all\|tag\|model\|device_ids}` (encola; detecta el tipo) |

### Conexión al ACS (admin)
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/settings` | NBI URL efectiva + origen (bd/env) |
| PUT  | `/settings` | `{nbi_url?, nbi_timeout?, default_connection_request?}` (aplica **sin reiniciar**) |
| POST | `/settings/test` | `{nbi_url?}` probar conexión al NBI |

## Uso rápido

```bash
BASE=http://localhost:8080
TOKEN=$(curl -s -X POST $BASE/auth/login -d 'username=admin&password=ClaveFuerte' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
H="Authorization: Bearer $TOKEN"

curl $BASE/devices -H "$H"                       # listar
DEV=...                                           # _id URL-encodeado
curl -X PUT "$BASE/devices/$DEV/wifi" -H "$H" -H 'Content-Type: application/json' \
  -d '{"band":"2g","ssid":"MiRed","password":"claveNueva123"}'
curl -X PUT "$BASE/devices/$DEV/label" -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"Router sala","customer":"Juan Perez C-1300"}'
```

## Conceptos importantes

- **El ACS muestra el ÚLTIMO reporte del CPE (caché), no el estado en vivo.** Tras un cambio, puede seguir viéndose el valor viejo hasta el siguiente inform; por eso hay `/read`, `/refresh` y auto-refresco en el panel (piden datos frescos por connection request).
- **Un CPE puede tener varias conexiones WAN a la vez** (WANIPConnection.1/.2 + WANPPPConnection.1). La API detecta y usa la **activa** (Connected); leer una instancia fija daba información falsa (DHCP vs Static vs PPPoE).
- **Nombre/cliente ≠ tags de GenieACS** (ver sección Identificación).

## Soporte de modelos

La traducción concepto→path TR-069 vive en [app/parammap.py](app/parammap.py). Trae el mapa **TR-098** probado en Cudy WR3000/AX3000. Para otra marca: agregar un dict con sus paths y mapearlo en `pick_map()` según `Manufacturer`/`ProductClass`. Lo que un modelo no exponga (p.ej. IPv6 o máx. de clientes en el WR3000) simplemente no aparece; el explorador **Avanzado** (`/params`) muestra el árbol real.

## Notas / pendientes

- La zona horaria (`time`) se pasa tal cual; el formato válido depende del firmware.
- **Poner TLS delante (reverse proxy) y firewall** si la API/panel sale de la red interna: expone login, claves WiFi/PPPoE y datos de clientes.
