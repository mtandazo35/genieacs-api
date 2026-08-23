# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

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
