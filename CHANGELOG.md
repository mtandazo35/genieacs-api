# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

## [No publicado]

### Añadido
- **Página de Aprovisionamiento en el panel** (admin) con tres pestañas: equipos nuevos (bandeja, rangos por ISP y modo sugerencia/automático), perfiles de modelo (catálogo con evidencia, correcciones y propuesta por IA) y DHCP/Option 43. Reutiliza los estilos del panel; solo se añadió una regla de CSS.
- **Generador del script DHCP (Option 43) para MikroTik**: codificación TLV, URL plana y opción 125, con matcher por `dslforum.org` en RouterOS 7 y aviso explícito de que v6 no lo soporta. El script asigna el conjunto a redes existentes (nunca las crea) y trae su bloque para deshacerse. `POST /provisioning/dhcp/script`.
- Pruebas del panel que cruzan los `id` que usa el JavaScript con los del HTML: si no coinciden, el script muere al cargar y el panel entero deja de funcionar sin que ninguna prueba de la API lo note.

### Corregido
- Se elimina la acción muerta `pppoe()` del panel, que apuntaba a `#ppp-user`/`#ppp-pass`/`#ppp-enable`, unos campos que ya no existen desde que la WAN se rehízo (PPPoE se aplica desde la pestaña WAN). Lo encontró la prueba nueva de ids.
- **Homologación asistida por IA** (etapa 6, opcional y apagada sin clave): para los conceptos que las reglas deterministas no resuelven en un modelo, se le pide una propuesta de mapeo a un modelo de lenguaje. Al proveedor se le mandan **rutas y tipos, nunca valores** (no salen SSID, claves ni IPs de abonados); las rutas que el modelo se invente se descartan comparándolas con el árbol real; y nada se aplica sin confirmación humana. Proveedor intercambiable (`LLM_PROVIDER`): Groq por defecto y cualquier otro compatible con el API de OpenAI, con tope de rutas enviadas por sus ventanas de contexto cortas.
- **Descubrimiento de equipos nuevos** (etapa 5): un bucle en la API detecta los CPE que llegan sin tag —invisibles para los usuarios ISP— y propone a quién pertenecen según la IP desde la que informan, cruzada con una tabla de rangos por ISP. Arranca en **modo sugerencia** (no toca nada) y se pasa a automático cuando demuestra que acierta. Gana el rango más específico; ante un empate entre dos ISP no asigna. Los tags del propio panel (`sched-reboot`, `reboot@HH:MM`) no cuentan como tenencia. Todo lo que asigna queda en la auditoría.
- **WAN por DHCP o IP estática en TR-181** (etapa 4): la interfaz sale del perfil derivado, el gateway se escribe en la entrada de la tabla de rutas que apunta a ella, y el parámetro propietario (`X_TP_ConnType`, `X_TP_SetDefaultGateway`) viaja en la misma tarea que el estándar, porque si no el equipo revierte —la misma lección del acceso remoto—. Solo se escriben rutas que el equipo expone; con el árbol a medias la API se niega y explica que hay que refrescar, en vez de mandar parámetros a ciegas. Se mantienen las guardas de siempre: gateway en la subred e imposibilidad de saltar a otra red (perdería el enlace con el ACS), ahora compartidas entre TR-098 y TR-181.
- **Catálogo de perfiles por modelo + firmware** (etapa 3): el perfil deducido se guarda bajo `fabricante|clase|modelo|firmware` (tabla `model_profile`), de modo que el segundo equipo de un modelo ya no tiene que aprender nada. `GET /profiles` lo lista con la evidencia de cada deducción y cuántos equipos lo usan, y `PUT /profiles/{key}/override` corrige la ruta de un concepto sin tocar código (la corrección manda sobre el mapa y sobre la deducción, y queda en la auditoría). Un firmware distinto genera su propio perfil, porque puede mover las instancias.
- **Perfil derivado del árbol** (etapa 2): el panel deduce del árbol real qué instancia es la WAN (por la ruta por defecto activa), la LAN (por el pool DHCP), cada banda WiFi (por la banda que reporta cada radio, no por el número de instancia) y el PPPoE, con la evidencia de cada deducción en `profile` dentro de `/status`. El mapa escrito a mano sigue mandando; la deducción solo rellena lo que ese mapa deja en blanco porque el equipo numera distinto. Contrastado contra los equipos reales: cero discrepancias en los modelos que ya funcionaban, y WAN visible en los que salía vacía (XX530, EC225-G5).
- Se elimina la detección de WAN en TR-181 por `X_TP_ServiceType`, que solo existe en algunos TP-Link y dejaba sin WAN al resto.
- **Cobertura del árbol en la ficha del equipo** (etapa 1 del plan de perfiles derivados): `GET /devices/{id}/status` devuelve `tree` con cuántos parámetros tiene el ACS de ese equipo, cuáles son las señales que faltan (ruta por defecto y bandas WiFi) y un aviso accionable. El panel lo muestra bajo la cabecera con un botón **Actualizar árbol**. Medido en la flota el 2026-09-26: 6 de 8 equipos estaban sin refrescar (28-45 parámetros) y por eso su ficha salía incompleta; los refrescados tenían 285 y 4653.
- Módulo `app/treeprofile.py` (funciones puras sobre el árbol del ACS, sin IO), base de las siguientes etapas: deducir WAN, LAN, radios y PPPoE del propio árbol en vez de mapas por marca.

## [1.4.0] - 2026-09-21

Correcciones de la auditoría técnica de seguridad del 2026-09-21.

### Seguridad
- **Un ISP ya no puede sacar un equipo del ACS**: `PUT /devices/{id}/param` rechaza (`403`) para usuarios ISP cualquier ruta con `ManagementServer` (URL/usuario/clave del ACS y del connection request) o fuera de `InternetGatewayDevice.`/`Device.`; en `/params` esas claves salen enmascaradas y no editables. El admin mantiene el acceso completo.
- **`GET /devices/{id}/backup` ya no devuelve claves** (WiFi, PPPoE, admin del equipo): salen como `********`. Se siguen guardando para restaurar.
- **Sesiones revocables**: el JWT lleva `uid` + `token_version`; cambiar la clave (propia o por admin) o desactivar al usuario invalida sus tokens al instante. Rol e ISP se leen de la BD en cada petición, no del token. Borrar y recrear un usuario con el mismo nombre no reaprovecha tokens viejos. Duración por defecto: 8 h (antes 12 h).
- **Secreto JWT obligatorio**: sin `GENIEACS_API_JWT_SECRET` de 32+ caracteres (o con el `CAMBIAME` de ejemplo) la API no arranca.
- **Límite de intentos de login** en la propia API: 5 fallos/min por IP y 20/hora por usuario → `429` con `Retry-After`. Mismo tiempo de respuesta exista o no el usuario.
- **Claves de 12+ caracteres** para usuarios nuevos y cambios de clave (API, panel, `manage.py`, instalador). Las claves existentes siguen sirviendo para entrar.
- **Anti-SSRF**: "cargar firmware por URL" solo acepta destinos públicos (o `FIRMWARE_ALLOWED_NETWORKS`) y revalida cada redirección; la URL del NBI (Ajustes y prueba) solo puede apuntar a `ALLOWED_NBI_NETWORKS`. Se valida cada IP resuelta, incluidas IPv4 mapeadas en IPv6.
- **Un ISP solo envía firmware**: no ve ni puede enviar archivos de configuración de proveedor (tipo 3).
- **Firmware con límite de tamaño** (`MAX_UPLOAD_MB`, 512) procesado por bloques, sin cargarlo entero en memoria (tampoco en el middleware de auditoría), y con **SHA256** en la respuesta.
- **Dependencias sin CVEs conocidos**: FastAPI 0.141 / Starlette 1.6, python-multipart 0.0.32, uvicorn 0.53, pydantic 2.13. `python-jose` (y su dependencia `ecdsa`, con vulnerabilidades sin arreglo) se sustituye por **PyJWT**. `pip-audit` pasaba de 33 vulnerabilidades a 0.
- Cabeceras `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` en todas las respuestas.

### Despliegue
- La API escucha **solo en `127.0.0.1:8080`**; el instalador publica el panel por **HTTPS con Caddy** (Let's Encrypt con dominio o certificado propio para la IP), abre 443 en UFW y **cierra el 8080**.
- Servicio systemd endurecido (`ProtectSystem=full`, `NoNewPrivileges`, `PrivateTmp`, `UMask=0077`, `RestrictAddressFamilies`…) y `--proxy-headers` para registrar la IP real.
- **Respaldo de la BD**: copia consistente antes de cada actualización (`/root/backups`) y diaria con `genieacs-api-backup.timer` (14 copias, `manage.py backup-db`). BD y `.env` con permisos `600`.
- `install.sh update` no cambia la exposición de una instalación antigua en `0.0.0.0` (avisa de usar `install`), y regenera el secreto JWT si era débil.

### Cambiado
- **Auto-restauración sin bucles ni acumulación de tareas**: no reintenta hasta que el equipo haya reportado después del intento anterior; tras 3 intentos sin que el equipo conserve los valores, se pausa 6 h y guarda el motivo (visible en la pestaña Respaldo). Los errores se registran en el journal en vez de ignorarse.
- **Auditoría** con IP de origen, User-Agent y `request_id` (cabecera `X-Request-ID`); registra también los logins correctos, fallidos y bloqueados.
- `PUT /auth/me/password` devuelve un token nuevo (el panel sigue en sesión tras cambiar la clave).
- El panel muestra los errores de validación de la API en texto legible.
- Arranque con `lifespan` (Starlette 1.x ya no tiene `on_event`).

### Añadido
- Pruebas de comportamiento en `tests/` (sin red ni ACS real) y CI de GitHub Actions: pyflakes, pytest en Python 3.10/3.11/3.13, `pip-audit` (también semanal) y `shellcheck` del instalador. Dependabot para pip y Actions.

## [1.3.0] - 2026-08-23

### Añadido
- **Restauración de WAN PPPoE en TR-181**: se mapeó `pppoe_enable` (`Device.PPP.Interface.1.Enable`) y se añadió preservación de claves *write-only* (`WRITEONLY_KEYS`/`writeonly_paths()`): las contraseñas PPPoE y WiFi del TP-Link, que no se pueden leer del equipo, se capturan al aplicarlas por el panel y `_snapshot` las conserva del respaldo previo en vez de borrarlas. Antes un `/backup` manual las perdía.

### Cambiado
- **Pestaña WiFi con 2.4 GHz y 5 GHz separadas**: dos bloques independientes (SSID, contraseña, canal, radio, oculta) precargados del estado; se elimina el desplegable "Banda". Cada bloque aplica su banda por separado.
- **Zona horaria consciente del modelo**: el formato de `LocalTimeZone` difiere por modelo (Cudy TR-098 usa la lista enumerada, p.ej. `GMT-05:00`; TP-Link TR-181 usa offset `-05:00`). Flota fijada a Ecuador (UTC-5, sin DST).

### Notas / limitaciones
- En TP-Link, cambiar la WAN a PPPoE puede requerir además `X_TP_ServiceType` en la interfaz WAN; la restauración tras factory reset depende de que exista `Device.PPP.Interface.1`. DHCP/estático por TR-181 no se respalda (DHCP es el default; estático TR-181 no soportado en `set_wan`).

## [1.2.0] - 2026-08-23

### Añadido
- **Soporte TR-181** además de TR-098: `pick_map()` elige el mapa por la raíz que reporta el equipo (`InternetGatewayDevice`→TR-098, `Device`→TR-181), sin tocar la config del CPE. Probado en TP-Link EX511 (WiFi, LAN, WAN, PPPoE, clientes, MAC, info).
- **Modelo real** mostrado desde `DeviceInfo.ModelName` (ya no el ProductClass genérico tipo "Device2").
- **Apartado Acceso**: acceso remoto por WAN (habilitar + puerto + protocolo **HTTP/HTTPS** por checkbox) y usuario/clave admin del equipo (donde el modelo lo exponga). `GET/PUT /devices/{id}/access`.
- **IPv6 (activación)**: habilitar IPv6 en la WAN + método (Auto/DHCPv6/SLAAC/PPPoE/Static) en modelos que lo exponen (TR-181). `GET/PUT /devices/{id}/ipv6-config`.
- **Diagnóstico** ping/traceroute desde el equipo, consciente del modelo (TR-098 `IPPingDiagnostics` / TR-181 `Device.IP.Diagnostics.IPPing`). `POST /devices/{id}/diag/ping|traceroute`.
- **Clientes (LAN hosts)**: `GET /devices/{id}/hosts` (hostname, IP, MAC, conexión, activo).
- **Identificación de abonado**: nombre + cliente + nota por equipo. `GET/PUT /devices/{id}/label`.
- **Auditoría e historial**: registro detallado de cada cambio (con el detalle real: "Acceso remoto ACTIVADO", "cambio de clave WiFi", "WAN → PPPoE", etc.). `GET /auth/audit` (global, admin) y `GET /devices/{id}/audit` (por equipo). Paginación de 20 en 20; las lecturas no se registran.
- Ficha por secciones con MAC (de la conexión activa), clientes conectados por radio, clave PPPoE revelable; WAN muestra todas las conexiones y marca la activa.
- Favicon propio (SVG).

### Cambiado
- Pestaña "Red / IP" renombrada a **"Red LAN"**.
- El envío de firmware/config y las operaciones detectan el tipo automáticamente.

### Notas / límites
- **Config WAN DHCP/estático** por ahora solo TR-098; **PPPoE** soportado en ambos. En TR-181 lo demás vía Avanzado.
- El EX511 no expone por TR-069: clave WiFi legible (write-only), toggle de ping WAN, ni puertos HTTP/HTTPS separados (un solo RemoteAccess).

## [1.1.0] - 2026-08-23

### Añadido
- **Diagnóstico desde el equipo**: ping y traceroute vía TR-069 (IPPingDiagnostics/TraceRouteDiagnostics) con resultados (éxitos/fallos, tiempos, saltos).
- **Log de auditoría**: middleware que registra cada operación de escritura (usuario, acción, equipo, método, estado); pestaña Actividad (admin) y `GET /auth/audit` + `GET /devices/{id}/audit`.
- Ficha: MAC (de la conexión activa), nº de clientes conectados por radio, clave PPPoE con mostrar/ocultar.
- Pestaña Clientes (LAN hosts) e Identificación por equipo (nombre/cliente/nota).

## [1.0.0] - 2026-08-23

Primera versión funcional: panel web + API multi-tenant sobre GenieACS, desplegada y probada contra equipos reales (Cudy WR3000/AX3000).

### Añadido — Núcleo
- API FastAPI con Swagger en `/docs` y panel web (SPA vanilla) servido en `/` con `no-cache`.
- Autenticación JWT (Bearer) y usuarios en SQLite.
- **Multi-tenant por ISP**: filtro por tag de GenieACS; `admin` ve toda la flota, `isp` solo su tag.
- Cliente NBI que maneja el `%`-encoding del `_id` (los CPE reportan `%20`/`%2E` literales) y el connection-request.
- Instalador one-liner (`install.sh`) con menú install/update/uninstall y unidad systemd.

### Añadido — Gestión de equipos
- Ficha de estado por secciones: Dispositivo (modelo, serial, **MAC**, firmware, uptime, CPU), WAN, LAN, WiFi 2.4/5 GHz.
- Lectura de datos frescos del CPE (`/read`) y refresco de árbol (`/refresh`, total o por subárbol).
- **WiFi**: SSID, clave (con mostrar/ocultar), canal, radio on/off, ocultar red, **nº de clientes conectados** por radio.
- **LAN/IP**: IP del router, máscara, rango y lease DHCP.
- **WAN unificada** con 3 modos: DHCP, estática y PPPoE. Lista todas las conexiones WAN y marca la activa; lee/prellena usuario y clave PPPoE; escribe en la conexión activa.
- **DNS** (por DHCP a clientes o del enlace) y **Hora** (zona horaria + NTP).
- **IPv6**: pestaña dinámica que muestra/edita los parámetros IPv6 que exponga el modelo.
- **Avanzado**: explorador de todo el árbol TR-069 (`/params`) con búsqueda y edición de cualquier parámetro (`/param`).
- **Clientes**: lista de LAN hosts (hostname, IP, MAC, tipo de conexión, activo).
- **Identificación**: nombre + cliente + nota por equipo (independiente de los tags de GenieACS); visible en lista, encabezado y buscador.

### Añadido — Operaciones
- Reinicio inmediato y **reinicio programado diario** (vía provision/preset en GenieACS).
- **Respaldo de configuración** por equipo, que se **fusiona con cada cambio** aplicado.
- **Auto-restauración**: bucle que detecta cambios de fábrica (drift) y reaplica la config guardada cuando el equipo vuelve a reportar.
- **Firmware y archivos de configuración** (Vendor Config, fileType 3): cargar por archivo o URL, listar, borrar; envío **masivo** (por tag/modelo/todos) y 1-a-1, con detección automática del tipo.
- **Lectura masiva** de equipos.
- **Gestión de usuarios**: crear, cambiar clave (propia y ajena), activar/desactivar, eliminar, con guardas del último admin; pestaña "Mi cuenta".
- **Conexión al ACS editable en caliente** (`/settings`): cambiar la NBI URL sin reiniciar, con botón de prueba.

### Seguridad
- Validación de WAN estática: IP/máscara/gateway válidos, gateway en la misma subred y la nueva IP en la misma red que la actual (evita perder el enlace con el ACS).
- Desactivar/eliminar usuario revoca el acceso (login y token validan estado activo).
- Pin `bcrypt==4.0.1` (versiones 4.1+ rompen el lector de versión de passlib).

### Corregido
- **Crítico**: el preset de reinicios programados usaba `$regex` sobre `_tags` (no soportado por GenieACS) y mataba el worker `genieacs-cwmp` en cada inform de toda la flota. Ahora usa un tag marcador de coincidencia exacta.
- Pantalla azul por asignar métodos a `actions` antes de su declaración (TDZ). 
- Marca/modelo leídos de `_deviceId` (sobreviven a un BOOTSTRAP que limpia el árbol).
- WAN mostraba la instancia `.1` fija (a veces "Static") en vez de la conexión realmente activa.
- La MAC y la IP/gateway se toman de la conexión activa (con fallback), evitando `00:00:...` o vacíos.
- Los formularios se vacían al aplicar; la ficha y la lista se refrescan tras cada cambio; el panel recuerda la vista/equipo al recargar.
- MongoDB en Debian 13 (trixie): usar el repo de bookworm (el de trixie está vacío) — en el instalador del ACS.

### Notas
- Requiere que el CPE hable TR-069 con el ACS: nada de esto revive un equipo con TR-069 apagado tras un factory reset (ver DEPLOY.md).
- Pendiente: TLS (reverse proxy) + firewall en la VM del panel.
