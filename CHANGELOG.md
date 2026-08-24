# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

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
