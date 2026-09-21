# GenieACS API

Panel web + **API de negocio multi-tenant** sobre [GenieACS](https://genieacs.com/). En vez de hablar TR-069 crudo (paths largos, `%2520`, tipos `xsd`, connection-request, particularidades por marca), expone endpoints limpios y un panel para soporte/ISP.

GenieACS queda como **motor** por debajo; esta API pone la capa de negocio, la autenticación, la separación por ISP y el panel. FastAPI, con **Swagger interactivo en `/docs`** (referencia siempre actualizada) y **panel web en `/`**.

📄 [CHANGELOG](CHANGELOG.md) · 🚀 [Notas de despliegue (DEPLOY.md)](DEPLOY.md)

## ⚡ Quick install (one-liner)

En la VM (Debian 12/13 o Ubuntu 22.04/24.04, como root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh)
```

Instala dependencias, clona en `/opt/genieacs-api`, crea el entorno Python, pide la **URL del NBI de GenieACS**, crea el usuario **admin** (clave de 12+ caracteres), deja el servicio systemd `genieacs-api` escuchando **solo en `127.0.0.1:8080`** y lo publica por **HTTPS con Caddy** (Let's Encrypt si das un dominio; si no, certificado propio para la IP). Con UFW activo abre 443 y cierra el 8080 de versiones anteriores. Deja además un respaldo diario de la BD (`genieacs-api-backup.timer`).

Modo no interactivo:

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh -o /root/install.sh
bash /root/install.sh install     # instalar/actualizar (con HTTPS)
bash /root/install.sh update      # solo actualizar código y reiniciar (respalda la BD antes)
bash /root/install.sh uninstall   # desinstalar
```

## Arquitectura

```
Panel web / Mikrowisp / técnico
        │  HTTPS :443 + JWT (Bearer)
        ▼
      Caddy  (reverse proxy TLS)
        │  127.0.0.1:8080
        ▼
   genieacs-api  (FastAPI)          ── usuarios y metadatos en SQLite; filtro por tag de ISP
        │  NBI HTTP :7557
        ▼
     GenieACS  (cwmp/nbi/fs/ui)     ── habla TR-069 con los CPEs
```

- **Multi-tenant por ISP**: cada CPE lleva un **tag** con el nombre del ISP. Un usuario `isp` solo ve/gestiona equipos con **su** tag; un `admin` ve toda la flota.
- **`$DEV`** en los ejemplos es el `_id` tal cual lo devuelve `/devices`. La API se encarga del `%`-encoding hacia GenieACS; en tus llamadas HTTP debes URL-encodearlo (el `_id` suele traer `%20`/`%2E`).
- Autenticación: `Authorization: Bearer <token>` en todo salvo `/auth/login`, `/health` y los estáticos del panel. El token dura 8 h, pero **cambiar la clave o desactivar al usuario lo revoca al instante**; rol e ISP se leen siempre de la BD, no del token.

## Referencia de la API

> La referencia viva e interactiva está en **`/docs`** (Swagger). Este es el resumen.

### Autenticación y cuenta
| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| POST | `/auth/login` | público | form `username`,`password` → `{access_token, role, isp}`. Tras 5 fallos/min por IP o 20/hora por usuario → `429` con `Retry-After` |
| GET  | `/auth/me` | cualquiera | datos del usuario actual |
| PUT  | `/auth/me/password` | cualquiera | `{current_password, new_password}` cambiar la propia clave (12+ caracteres). Cierra las demás sesiones y devuelve un `access_token` nuevo |

### Usuarios (solo admin)
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/auth/users` | listar usuarios |
| POST | `/auth/users` | `{username,password,role(admin\|isp),isp_tag}` crear |
| PUT  | `/auth/users/{u}/password` | `{new_password}` resetear clave de otro |
| POST | `/auth/users/{u}/active` | `{active:bool}` activar/desactivar |
| DELETE | `/auth/users/{u}` | eliminar (no el último admin ni a sí mismo) |
| GET  | `/auth/audit` | registro de auditoría global (admin): cada cambio, con detalle |

### Auditoría / historial
El registro guarda **qué** se hizo (frase legible: "Acceso remoto ACTIVADO", "cambio de clave WiFi 2.4G", "WAN → PPPoE", "Reinicio programado 03:00", "Creó usuario X"…), quién, cuándo, equipo, resultado, **IP de origen, User-Agent y un `request_id`** (el mismo de la cabecera `X-Request-ID` de la respuesta). También quedan los logins correctos, fallidos y bloqueados. Las lecturas/refrescos no se registran. El panel pagina de 20 en 20.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/auth/audit` | actividad global de la flota (admin) |
| GET | `/devices/{id}/audit` | historial de cambios de un equipo (respeta tenencia) |

### Equipos
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/devices` | lista (id, name, customer, tags, modelo, firmware, último inform), filtrada por tenencia |
| GET  | `/devices/{id}/status` | ficha de estado (dispositivo, WAN activa, LAN, WiFi con clientes, PPPoE con estado, MAC, name/customer) |
| POST | `/devices/{id}/read` | pide al CPE los parámetros de estado (getParameterValues) |
| POST | `/devices/{id}/refresh?object=` | GetParameterNames de la raíz (o del `object` dado) |
| POST | `/devices/read-bulk` | lectura masiva `{all\|tag\|model\|device_ids}` (respeta tenencia) |
| GET  | `/devices/{id}/params?search=&writable_only=` | **todo** el árbol del modelo (Avanzado) |
| PUT  | `/devices/{id}/param` | `{path,value,type?}` escribir cualquier parámetro. Un usuario ISP no puede tocar `*.ManagementServer.*` (URL/usuario/clave del ACS: sacarían el equipo del ACS) ni rutas fuera de `InternetGatewayDevice.`/`Device.`; en `/params` ve esas claves enmascaradas |
| GET  | `/devices/{id}/hosts` | clientes conectados (LAN hosts): hostname, IP, MAC, conexión, activo |
| POST | `/devices/{id}/diag/ping` | `{host, count?}` ping desde el equipo (TR-098/TR-181) |
| POST | `/devices/{id}/diag/traceroute` | `{host, max_hops?, tries?}` traceroute desde el equipo |

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
| GET/PUT | `/devices/{id}/access` | acceso remoto WAN `{remote_enable, remote_port?, remote_protocol("HTTP"\|"HTTPS")?}` + admin del equipo `{admin_user?, admin_password?}` |
| GET/PUT | `/devices/{id}/ipv6-config` | activar IPv6 en la WAN `{enable, type("Auto"\|"DHCPv6"\|"SLAAC"\|"PPPoE"\|"Static")}` (modelos que lo exponen) |

**WAN — validaciones de seguridad (modo static):** IP/máscara/gateway válidos, gateway en la misma subred que la IP, y la nueva IP debe estar en la **misma red que la IP WAN actual** del equipo (si no → `400`), para no perder el enlace con el ACS. Se escribe en la **conexión WAN activa**, no en una instancia fija.

### Sistema / acciones
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/devices/{id}/reboot` | reinicio inmediato |
| POST | `/devices/{id}/factory-reset` | (admin) restaurar de fábrica |
| GET/PUT/DELETE | `/devices/{id}/schedule-reboot` | reinicio programado diario `{hour, minute}` |
| POST | `/devices/{id}/firmware` | empujar un archivo ya cargado `{file_name}` (detecta si es firmware o config; un ISP solo puede enviar firmware) |

### Respaldo / auto-restauración
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/devices/{id}/backup` | guarda la config actual como respaldo |
| GET  | `/devices/{id}/backup` | ver respaldo + estado auto-restauración (las claves salen como `********`: se guardan para restaurar, pero no se devuelven) |
| POST | `/devices/{id}/restore` | reaplica la config guardada |
| POST | `/devices/{id}/autorestore` | `{enabled:bool}` vigilar y reaplicar tras factory reset |

El respaldo **se fusiona con cada cambio** aplicado (nunca queda viejo). Un bucle en la API (cada 10 min) detecta *drift* vs la config deseada y la reaplica **solo si el equipo vuelve a hablar con el ACS**: no encola otra tarea hasta que el equipo haya reportado después del intento anterior, y si tras 3 intentos el equipo sigue sin conservar los valores, pausa la auto-restauración 6 h y deja el motivo en `last_error` (se ve en la pestaña Respaldo).

### Firmware / archivos (admin)
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/firmware` | listar archivos cargados (el admin ve firmware y config; un ISP solo firmware) |
| POST | `/firmware/upload` | multipart `file` + `file_type(firmware\|config)` + `product_class?/oui?/version?`. Máx. 512 MB (`MAX_UPLOAD_MB`); responde con el `sha256` |
| POST | `/firmware/upload-url` | `{url, file_name?, file_type, ...}` descarga server-side. Solo URLs públicas (o redes de `FIRMWARE_ALLOWED_NETWORKS`); cada redirección se revalida; responde con el `sha256` |
| DELETE | `/firmware/{name}` | borrar |
| POST | `/firmware/push` | envío masivo `{file_name, all\|tag\|model\|device_ids}` (encola; detecta el tipo) |

### Conexión al ACS (admin)
| Método | Ruta | Descripción |
|---|---|---|
| GET  | `/settings` | NBI URL efectiva + origen (bd/env) |
| PUT  | `/settings` | `{nbi_url?, nbi_timeout?, default_connection_request?}` (aplica **sin reiniciar**). La URL debe resolver a una red de `ALLOWED_NBI_NETWORKS` (por defecto, privadas y loopback) |
| POST | `/settings/test` | `{nbi_url?}` probar conexión al NBI (misma restricción) |

## Uso rápido

```bash
BASE=https://panel.example.com          # o, en la propia VM: http://127.0.0.1:8080
TOKEN=$(curl -s -X POST $BASE/auth/login -d 'username=admin&password=UnaClaveLarga123' \
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

## Soporte de modelos (TR-098 y TR-181)

La traducción concepto→path TR-069 vive en [app/parammap.py](app/parammap.py) con dos mapas: **TR-098** (`InternetGatewayDevice.*`, probado en Cudy WR3000/AX3000) y **TR-181** (`Device.*`, probado en TP-Link EX511). `pick_map()` elige automáticamente según la **raíz que reporta cada equipo**, así una flota mixta funciona sin cambiar la config de los CPE. Para otra marca: agregar/ajustar el dict correspondiente.

Lo que un modelo no exponga simplemente no aparece (p.ej. IPv6 o máx. de clientes en el WR3000; clave WiFi write-only en el EX511); el explorador **Avanzado** (`/params`) muestra el árbol real de cualquier equipo.

Limitaciones actuales por modelo de datos:
- **WAN DHCP/estático**: solo TR-098. **PPPoE**: TR-098 y TR-181. En TR-181 lo demás vía Avanzado.
- **Acceso remoto**: TR-098 (Enable+Port) y TR-181 (Enable+Port+Protocol, el TP-Link exige también los `X_TP_*`). Un solo servicio remoto por equipo (no puertos HTTP/HTTPS separados si el firmware no los expone).

## Seguridad

Resumen de los controles (detalle operativo en [DEPLOY.md](DEPLOY.md#seguridad)):

- **Perímetro**: la API solo escucha en `127.0.0.1` y se publica por HTTPS (Caddy). El servicio corre como `genieacs` con systemd endurecido (`ProtectSystem`, `NoNewPrivileges`, `UMask=0077`…).
- **Sesiones**: secreto JWT obligatorio (32+ caracteres; sin él la API no arranca), tokens de 8 h revocables (`token_version`), claves de 12+ caracteres, límite de intentos de login.
- **Multi-tenant**: el ISP solo ve y toca equipos con su tag, no puede escribir `ManagementServer` ni enviar archivos de configuración.
- **Datos sensibles**: la BD (`chmod 600`) guarda claves de CPE para poder restaurarlas; la API no las devuelve en `/backup`. Respaldo diario consistente de la BD con retención de 14 copias.
- **Peticiones salientes (anti-SSRF)**: firmware por URL solo a destinos públicos (o redes autorizadas) y NBI solo a redes autorizadas, validando cada IP resuelta y cada redirección.
- **Dependencias**: versiones fijadas, revisadas con `pip-audit` en el CI (también semanal) y Dependabot.

## Desarrollo y pruebas

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pyflakes app manage.py tests
.venv/bin/python -m pytest -q tests          # sin red ni GenieACS: usa un ACS falso en memoria
.venv/bin/python -m pip_audit -r requirements.txt
```

Las pruebas (`tests/`) describen escenarios de ataque y de operación: un ISP que intenta sacar su equipo del ACS, un token robado tras cambiar la clave, una descarga de firmware que redirige a la red interna, un CPE apagado que no debe acumular tareas, la migración de una BD de la versión anterior… El CI las corre en Python 3.10, 3.11 y 3.13.

## Notas / pendientes

- La zona horaria (`time`) se pasa tal cual; el formato válido depende del firmware.
- Las operaciones masivas (`/firmware/push`, `/devices/read-bulk`) recorren los equipos dentro de la petición HTTP; para decenas de miles de CPE convendría pasarlas a trabajos en segundo plano.
- La tenencia se apoya en los tags de GenieACS: quien administra el ACS (o su UI) puede mover un equipo de ISP cambiando su tag.
