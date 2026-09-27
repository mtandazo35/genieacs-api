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
| GET  | `/devices/{id}/status` | ficha de estado (dispositivo, WAN activa, LAN, WiFi con clientes, PPPoE con estado, MAC, name/customer), `tree` (cuántos parámetros tiene el ACS y si falta refrescar), `profile` (qué instancia es la WAN, la LAN y cada radio, con su evidencia) y `capabilities` (qué funciones soporta ese modelo) |
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

**WAN en TR-181** (TP-Link y similares): además de PPPoE ya se puede fijar **DHCP o IP estática**. La interfaz se toma del perfil derivado; el gateway se escribe en la entrada de la tabla de rutas que apunta a esa interfaz (no en la interfaz), y se manda el parámetro propietario `X_TP_ConnType` junto al estándar, porque si no el equipo revierte. Solo se escriben rutas que el equipo expone: si el árbol está a medias, la API se niega y pide pulsar *Actualizar* en vez de mandar parámetros a ciegas.

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

### DHCP / Option 43: script para MikroTik (admin)
Un CPE que no recibe la URL del ACS nunca aparece en el panel. Esta sección **genera el script** que la entrega por DHCP; el panel no se conecta a ningún router ni guarda credenciales, y el script trae su bloque para deshacer exactamente lo que añadió.

Tres codificaciones, porque el parque real no respeta el estándar por igual: **TLV** (TR-069 Anexo G: subopción 1 + longitud + URL), **URL plana** (equipos que no entienden TLV) y **opción 125** (enterprise 3561). La opción 43 solo admite un valor por cliente: en **RouterOS 7** un *matcher* por `dslforum.org` permite dar TLV a unos y plana a otros; en **v6** no hay matchers y hay que elegir.

El script **no crea redes**: asigna el conjunto de opciones a redes que ya existen, para no tocar el DHCP de un ISP en producción. En [DEPLOY.md](DEPLOY.md#entregar-el-acs-por-dhcp-mikrotik) está el ejemplo completo de comandos, cómo comprobarlo y cómo deshacerlo.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/provisioning/dhcp/defaults` | URL del ACS deducida del NBI configurado |
| POST | `/provisioning/dhcp/script` | `{acs_url?, networks[], routeros, encoding, include_125}` → script + avisos |

### Homologación asistida por IA (admin, opcional)
Para los conceptos que las reglas deterministas no resuelven en un modelo concreto (normalmente parámetros propietarios), se le puede pedir una propuesta de mapeo a un modelo de lenguaje. **Queda apagada si no hay clave configurada.**

Tres cosas la hacen segura:
- Al modelo se le mandan **rutas y tipos, nunca valores**: no salen SSID, claves ni IPs de abonados.
- Una ruta que el modelo se invente **se descarta**: solo se aceptan rutas que existen en el árbol de ese equipo.
- **Nada se aplica solo**: la propuesta se muestra con el valor actual de cada ruta, y solo al confirmar se guarda como corrección del catálogo.

**Cómo se pide.** En Aprovisionamiento → *Perfiles de modelo*, el botón **Proponer mapeo con IA** de la tarjeta del modelo abre un modal. El equipo se elige en un **desplegable con los equipos de ese modelo** que el panel ya tiene listados: antes había que escribir a mano el identificador con el que GenieACS conoce al equipo (`OUI-ProductClass-Serie`, con los escapes de URL de la clase de producto), lo que obligaba a irse a la lista de equipos a copiarlo. El desplegable filtra por fabricante y modelo y **no** por firmware, porque un equipo con otra versión sigue siendo de ese modelo y de él se leen las mismas rutas. Si no hay ningún equipo de ese modelo en el ACS, el modal lo dice y no deja consultar: las rutas salen de un equipo real, no del catálogo.

La respuesta se revisa en una **lista con una casilla por sugerencia** (marcadas de entrada), con el concepto, la ruta propuesta y su valor actual; **Guardar las marcadas** confirma solo ésas, una llamada a `/homologacion/confirmar` por ruta, y si alguna falla dice cuántas se guardaron. Antes se preguntaba por cada sugerencia con un `confirm()` del navegador: seis rutas eran seis diálogos seguidos, sin ver el conjunto y sin poder descartar una sola sin haber decidido ya las anteriores. En el mismo modal se ven ahora las **rutas descartadas por inventadas** y las dudas que dejó el modelo, que la API ya devolvía y el panel tiraba.

Se configura **desde el panel** (Ajustes → Inteligencia artificial): proveedor, clave, modelo y URL. Lo guardado en el panel manda sobre el `.env`, y hay un botón **Probar** que se puede usar **sin guardar** nada.

**Probar** pregunta primero `GET {URL}/models`: eso valida la clave y la URL sin gastar tokens y devuelve **la lista de modelos que ese proveedor tiene de verdad**, que el panel ofrece como chips (un clic los pone en el campo Modelo). Solo después hace una petición mínima al modelo elegido. Así cada fallo dice lo que es, con el mensaje literal del proveedor: clave inválida (401), URL que no es un API (no tiene `/models`), o un modelo que ese proveedor no sirve — antes los tres salían como el mismo 404 ambiguo. Si el campo Modelo se deja vacío se usa el del proveedor, y si ese no está en su lista se elige uno que sí esté; tras una prueba correcta queda escrito en el campo. **La clave no se devuelve nunca** por la API (solo `key_set: true`) ni aparece en la auditoría. Proveedores: **Groq** por defecto, y cualquier otro compatible con el API de OpenAI (OpenRouter, Together, vLLM local) con el mismo cliente. Como esos modelos tienen ventanas de contexto cortas, solo se mandan las rutas escribibles y las de estado, con un tope.

| Método | Ruta | Descripción |
|---|---|---|
| GET/PUT | `/settings/llm` | proveedor, modelo y clave (la clave solo se escribe, nunca se lee) |
| POST | `/settings/llm/test` | comprueba clave, URL y modelo; devuelve `modelos` (los del proveedor) y el error literal si algo falla. Acepta un cuerpo para probar lo que hay escrito sin guardarlo |
| GET | `/homologacion/estado` | si hay proveedor configurado |
| GET | `/homologacion/log` | qué ha hecho la IA. Query: `limit` (100 por defecto, acotado entre 1 y 500) y `tipo` (`propuesta`, `confirmacion` o `prueba`) |
| POST | `/homologacion/proponer` | `{device_id}` pide el mapeo de lo que falta (no aplica nada) |
| POST | `/homologacion/confirmar` | `{key, concept, path}` guarda una sugerencia revisada |

#### Registro de lo que hace la IA

La auditoría general no sirve para esto: la escribe un **middleware que solo ve la petición**, así que anotaba "pidió a la IA un mapeo" y nunca qué contestó el modelo. Lo que interesa auditar cuando una propuesta acaba cambiando el perfil de un modelo es justo la respuesta, y ésa solo la tiene delante el propio endpoint. Por eso hay una tabla aparte, `ia_evento`, que se escribe desde el endpoint con la respuesta ya en la mano. Se anotan tres tipos de evento:

- **`propuesta`**: modelo de CPE (`model_key`), equipo del que salió el árbol, proveedor (su URL base), modelo de lenguaje que respondió, los conceptos que se pidieron, cuántas rutas se enviaron, el mapeo propuesto, las **rutas descartadas por inventadas**, las dudas que devolvió el modelo y los milisegundos que tardó. Si el proveedor falla, queda el evento con `ok: false` y el motivo.
- **`confirmacion`**: qué concepto y qué ruta acabaron en el catálogo. Una propuesta sin confirmar no cambia nada, y así se distingue de una que sí.
- **`prueba`**: cada prueba del proveedor, con URL, modelo, si respondió y el error literal. La auditoría general dice "configuró el proveedor de IA" y no distingue una prueba fallida.

**Nunca se guardan la clave del proveedor ni los valores del árbol.** La respuesta de `/homologacion/proponer` muestra el valor actual de cada ruta propuesta para poder revisarla —ahí van el SSID y la clave del abonado—, y ese valor no entra en el registro. En el panel está en Actividad → **IA**.

### Equipos nuevos / descubrimiento (admin)
Un CPE recién vinculado llega al ACS **sin tag**, y como la multi-tenencia se apoya en los tags, ningún usuario ISP lo ve. El descubrimiento lo detecta y propone a qué ISP pertenece según la IP desde la que informa (la del `ConnectionRequestURL`), cruzada con una tabla de rangos.

Arranca en **modo sugerencia**: propone y no toca nada. Cuando compruebes que acierta, lo pasas a automático. Gana el rango más específico, y si dos ISP reclaman el mismo, no asigna: un tag mal puesto le daría a un ISP acceso a equipos de otro.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/discovery` | equipos sin tag, con el ISP sugerido y el motivo |
| GET/POST | `/discovery/rules` | rangos `CIDR → isp_tag` |
| DELETE | `/discovery/rules/{id}` | borrar un rango |
| POST | `/discovery/assign` | `{device_id, isp_tag}` asignar (confirmar una sugerencia o hacerlo a mano) |
| PUT | `/discovery/mode` | `{auto, interval?}` cambiar entre sugerir y asignar sola |

### Perfiles de modelo (admin)
Cada vez que se abre la ficha de un equipo, el panel guarda el perfil deducido bajo la clave `fabricante|clase|modelo|firmware`, así el siguiente equipo del mismo modelo ya sale completo. Si una deducción no acierta en un modelo concreto, se corrige la ruta de ese concepto **sin tocar código**, y esa corrección manda sobre el mapa y sobre lo deducido.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/profiles` | perfiles aprendidos, con la evidencia de cada deducción y cuántos equipos de la flota usan cada uno |
| GET | `/profiles/{key}` | un perfil concreto |
| PUT | `/profiles/{key}/override` | `{concept, path}` corrige la ruta de un concepto (`path: null` la borra) |

### Árboles por modelo (admin)
Junto al perfil se guarda el **árbol** del equipo bajo la misma clave `fabricante|clase|modelo|firmware`. Así se puede saber qué expone un modelo sin tener un equipo delante.

Lo que alimenta la base son dos operaciones concretas, no cualquier lectura: **abrir la ficha de un equipo** (`GET /devices/{id}/status`) y **pedir una propuesta de homologación** (`POST /homologacion/proponer`). Las demás lecturas del ACS no aportan nada; en particular `comparar_con` lee un equipo del ACS y **no** suma sus rutas a la unión, porque su trabajo es medir ese equipo contra el modelo, no cambiar la referencia con la que se le mide.

De ahí una consecuencia que conviene tener presente: **consultar la base es solo de admin, pero escribir en ella no**. La ficha de un equipo la abre también un usuario ISP para los suyos, y ese `GET /devices/{id}/status` une las rutas de ese equipo a las del modelo. Es deliberado: así el modelo se aprende de toda la flota y no solo de los equipos que mire un administrador. Por eso mismo se guardan **rutas y si son escribibles, nunca valores** (la misma regla que rige lo que se manda a la IA: en un árbol real van el SSID y la clave WiFi del abonado), y por eso la lista de equipos que aportaron no sale en la descarga.

Las rutas se **unen**, no se sobrescriben, por un caso real de esta flota: un equipo recién adoptado trae unos 34 parámetros y otro del mismo modelo ya refrescado trae 3579; si el último en leerse mandara, borraría lo que ya se sabía. La unión recoge además las ramas que un equipo expone y otro no porque tiene esa función apagada. Una ruta queda como escribible si algún equipo del modelo la reportó así, y la raíz guardada (`Device` o `InternetGatewayDevice`) solo se sustituye si el equipo trae una: un documento sin raíz no puede borrar la que ya se sabía.

La unión se hace **en una sola transacción** (`BEGIN IMMEDIATE`): leer en una conexión, unir en Python y escribir en otra deja una ventana en la que dos lecturas simultáneas del mismo modelo pierden una de las dos uniones. Hoy no es alcanzable —un worker y código síncrono—, pero lo sería con `--workers N` o un segundo proceso. Una fila corrupta (una restauración a medias, una edición a mano) **ya no tumba la ficha** de todos los equipos de ese modelo: se anota en el log y el árbol se rehace desde cero.

De la unión sale la comparación que evita el diagnóstico equivocado: `comparar_con=<id de equipo>` dice cuántas rutas del modelo le faltan a ese equipo, con una muestra de cuáles. Un árbol a medias se ve de un vistazo, en vez de concluir que el modelo no soporta algo. En el resumen por modelo, `incompleto` tiene **tres estados**: `true` si ningún equipo por separado llega al tamaño de la unión, `false` si alguno la alcanza y `null` si todavía no se sabe —hay árbol guardado pero ningún equipo registrado que lo respalde—. Devolver `false` en ese caso era mentir, y el panel usa el mismo criterio de tres estados en las capacidades por modelo: con el árbol a medias nunca se afirma que algo no se soporta.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/trees` | resumen por modelo: rutas de la unión (`n_params`), equipos que aportaron (`devices`), el árbol más grande visto en un solo equipo (`max_equipo`) e `incompleto` (`true`/`false`/`null`). Sin las rutas: son cientos o miles por fila |
| GET | `/trees/{key}` | las rutas de ese modelo (`path` + `writable`), con `total` y `truncado`, y los equipos que aportaron (`equipos`) con cuánto árbol cada uno. Query: `q` (filtra rutas que contengan ese texto), `escribibles` (solo las escribibles), `limit` (500 por defecto, máximo 20000, `0` = todas), `comparar_con` (id de un equipo: `tiene`, `del_modelo`, `faltan` y hasta 50 ejemplos) |
| DELETE | `/trees/{key}` | olvida el árbol de ese modelo y quién aportó, para que se reaprenda desde cero |

Sobre el detalle, tres cosas que el panel necesita y no se adivinan:

- **`total` es posterior al filtro**, no el tamaño del modelo: con `q=SSID`, un modelo de 34 rutas devuelve `total: 10`. Quien pinte "X de Y rutas" tiene que usar `n_params` como Y, o dirá "10 de 10". `truncado` avisa de que hay más rutas de las que caben en `limit`.
- **`limit` admite hasta 20000** (`limit` negativo o mayor da `422`). Para un modelo con más rutas que eso, `limit=0` no es una comodidad: es la única forma de verlas todas.
- **`limit=0` no devuelve `equipos`.** Ésa es la descarga a un archivo, y no debe arrastrar los seriales de equipos de varios ISP, que además no hacen falta para saber qué expone un modelo. Con el límite normal, `equipos` viene.

`DELETE` existe porque la unión solo crece y el flag de escribible se queda pegado: si entra basura —un equipo que reportaba mal su firmware, una prueba—, no hay forma de quitar esa ruta de la unión, y ésta es la salida. Borra también los equipos registrados de ese modelo, así que tras el borrado `incompleto` vuelve a ser `null` hasta que alguien abra una ficha.

La clave va en la ruta y lleva `|`: URL-encodéala como el `_id` de un equipo. Si de ese modelo todavía no hay árbol guardado, `404` (también en `DELETE`). Con `comparar_con`, un equipo que simplemente no está en el ACS da **`404`**; el `502` con el motivo queda para cuando la consulta al ACS falla (NBI caído, timeout).

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

- **El ACS solo guarda lo que el equipo ya reportó.** En su primer inform un CPE manda unas decenas de parámetros; el árbol completo (cientos en TR-098, miles en TR-181) solo llega tras un `GetParameterNames`, que es el botón **Actualizar**. Por eso la ficha puede salir a medias en un equipo recién vinculado: no es que el modelo no lo soporte. El campo `tree` de `/status` lo mide y el panel lo avisa en la propia ficha.
- **El ACS muestra el ÚLTIMO reporte del CPE (caché), no el estado en vivo.** Tras un cambio, puede seguir viéndose el valor viejo hasta el siguiente inform; por eso hay `/read`, `/refresh` y auto-refresco en el panel (piden datos frescos por connection request).
- **Un CPE puede tener varias conexiones WAN a la vez** (WANIPConnection.1/.2 + WANPPPConnection.1). La API detecta y usa la **activa** (Connected); leer una instancia fija daba información falsa (DHCP vs Static vs PPPoE).
- **Nombre/cliente ≠ tags de GenieACS** (ver sección Identificación).

## Soporte de modelos (TR-098 y TR-181)

**Capacidades por modelo**: de ese mismo árbol sale qué funciones tiene cada equipo (WiFi por banda, LAN, DHCP, WAN por DHCP/estática/PPPoE, DNS, hora, IPv6, acceso remoto, usuario del equipo, diagnósticos, lista de clientes). El panel **deshabilita las pestañas que ese modelo no expone**, en vez de ofrecer formularios que fallarían al pulsar, y lo dice bajo la cabecera de la ficha.

Hay tres estados, y el tercero es el que evita mentir: **sí**, **no**, y **aún no se sabe** cuando el árbol está incompleto. Con el árbol a medias nunca se dice "no lo soporta". Reiniciar, restaurar de fábrica y enviar firmware no dependen del árbol: son órdenes TR-069 y las soporta cualquier CPE.

**Perfil derivado del árbol** ([app/treeprofile.py](app/treeprofile.py)): antes de usar el mapa, el panel deduce del propio equipo qué instancia es cada cosa, y así no depende de que todos los modelos numeren igual:

| Concepto | Cómo se deduce |
|---|---|
| WAN | la interfaz a la que apunta la **ruta por defecto activa**; si el árbol aún no la trae, la marca de servicio de Internet o la única IP fuera de la LAN |
| LAN | la interfaz cuya IP cae en la red del **pool DHCP** |
| WiFi 2.4/5 GHz | la **banda que reporta cada radio**, y el primer SSID colgado de ella (con su AccessPoint por referencia) |
| PPPoE | la conexión que el equipo declara como servicio por defecto (TR-098) o la `PPP.Interface` existente (TR-181) |

La convivencia con los mapas es conservadora: **el mapa escrito a mano manda**, y la deducción solo se usa donde ese mapa apunta a una instancia que ese equipo no tiene (antes, ese campo salía vacío). Cada deducción viene con su evidencia en `profile.evidencia`, para poder auditar por qué el panel eligió esa ruta.

La traducción concepto→path TR-069 vive en [app/parammap.py](app/parammap.py) con dos mapas: **TR-098** (`InternetGatewayDevice.*`, probado en Cudy WR3000/AX3000) y **TR-181** (`Device.*`, probado en TP-Link EX511). `pick_map()` elige automáticamente según la **raíz que reporta cada equipo**, así una flota mixta funciona sin cambiar la config de los CPE. Para otra marca: agregar/ajustar el dict correspondiente.

Lo que un modelo no exponga simplemente no aparece (p.ej. IPv6 o máx. de clientes en el WR3000; clave WiFi write-only en el EX511); el explorador **Avanzado** (`/params`) muestra el árbol real de cualquier equipo.

Limitaciones actuales por modelo de datos:
- **WAN DHCP/estático y PPPoE**: TR-098 y TR-181.
- **Acceso remoto**: TR-098 (Enable+Port) y TR-181 (Enable+Port+Protocol, el TP-Link exige también los `X_TP_*`). Un solo servicio remoto por equipo (no puertos HTTP/HTTPS separados si el firmware no los expone).

## Panel: Ajustes

**Ajustes** pasa a ser accesible para cualquier usuario y se organiza en subpestañas:

| Subpestaña | Quién la ve | Qué hay |
|---|---|---|
| Mi cuenta | todos | cambiar la propia contraseña (antes era una entrada suelta del menú) |
| Tema | todos | las cuatro paletas del panel |
| Conexión al ACS | admin | la URL del NBI, el timeout y el connection request |
| Inteligencia artificial | admin | proveedor, clave y modelo para la homologación asistida |
| DHCP en MikroTik | admin | el tutorial de la opción 43: por qué hace falta, los comandos que se pegan, cómo comprobarlo y cómo deshacerlo |

Lo que toca servidores queda marcado como `admin-only` y no se le muestra a un usuario ISP; su cuenta y el tema, sí. El tutorial de DHCP no cambia nada en ningún sitio, pero son comandos que tocan el DHCP de un ISP y llevan la URL del ACS: es información de servidor, igual que *Conexión al ACS*, así que va también como `admin-only`.

### Ajustes → DHCP en MikroTik (tutorial, admin)

Cinco pasos en orden: qué hace la opción 43 y por qué el CPE la pide (se anuncia en la opción 60 como `dslforum.org` y el servidor le responde con la URL del ACS); el ejemplo completo de comandos para RouterOS 7 con las tres codificaciones y un botón para copiarlo; cómo comprobar que quedó puesto —incluida la opción 60 que anuncia cada CPE en `lease print detail`—; cómo deshacerlo; y un botón que lleva al generador de Aprovisionamiento → DHCP, que es el que hace el script con los rangos reales.

El contenido no es nuevo: está en [DEPLOY.md](DEPLOY.md#entregar-el-acs-por-dhcp-mikrotik) y, resumido, en el plegable de Aprovisionamiento → DHCP. Lo que faltaba era tenerlo dentro del panel y en orden, sin salir a leer un archivo del repo para entregar el ACS en un rango nuevo. Incluye el aviso que más tiempo de diagnóstico cuesta: la opción 43 solo dice **a dónde llamar** a un cliente TR-069 que ya esté corriendo, y si el equipo lo tiene apagado —típico en modo AP— no hay ajuste de DHCP que lo levante; la señal rápida es el puerto 7547 cerrado.

El ejemplo usa una red de documentación (`192.0.2.0/24`) y un ACS en `10.20.30.5`, los mismos de DEPLOY.md. Una prueba saca del propio HTML la URL, las redes y la codificación, vuelve a generar el script con `app/dhcp_tr069.py` y exige que coincida línea a línea: un tutorial con comandos pegados a mano envejece en silencio cuando cambia el generador, y lo que se pega de ahí acaba en un router en producción.

## Panel: temas

En **Ajustes → Tema** se elige entre cuatro paletas: *Pizarra* (la de siempre), *Templada*, *Noche* y *Claro*. La elección se guarda en el navegador de cada persona, no en el servidor, y se aplica nada más cargar el panel.

## Panel: página de Aprovisionamiento (admin)

Una página con cuatro pestañas, que son la cara visible de todo lo anterior:

- **Equipos nuevos**: bandeja de los que llegan sin tag, con el ISP sugerido y el motivo; tabla de rangos `CIDR → ISP`; y el interruptor entre sugerir y asignar solo.
- **Perfiles de modelo**: catálogo por modelo+firmware con la evidencia de cada deducción, las correcciones manuales, y el botón de proponer mapeo con IA cuando hay proveedor configurado — abre un modal donde se elige el equipo de una lista y se guardan las sugerencias que queden marcadas.
- **Árboles**: tabla de modelos con las rutas de la unión, los equipos que aportaron y el aviso de árbol incompleto; al abrir uno, el detalle con buscador de rutas y descarga del árbol en JSON.
- **DHCP / Option 43**: el generador del script para MikroTik, con vista previa, copiar y descargar `.rsc`.

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
