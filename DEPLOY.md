# Notas de despliegue

Guía operativa para instalar, actualizar y mantener **genieacs-api** en producción.

## Requisitos

- Debian 12/13 o Ubuntu 22.04/24.04 con systemd, acceso root.
- Un **GenieACS** en marcha y alcanzable en su NBI (por defecto `:7557`) desde esta VM.
- Python 3.10+ (lo instala el script). Recomendado: VM dedicada, 1–2 vCPU / 1 GB.
- Puerto 443 libre para Caddy (si ya hay otro proxy, ver [Proxy propio](#proxy-propio)).

## Instalación

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh)
```

Qué hace:

1. Instala `git`/`python3-venv`/`pip` y crea el usuario de sistema `genieacs`.
2. Clona en `/opt/genieacs-api` y arma el venv.
3. Pide la **URL del NBI** y genera un `JWT_SECRET` aleatorio (`.env` con permisos `600`).
4. Crea el **admin** (clave de 12+ caracteres; vacío = generar una).
5. Instala el servicio `genieacs-api` (uvicorn en **`127.0.0.1:8080`**, systemd endurecido) y el timer de respaldo diario de la BD.
6. Instala **Caddy** y publica el panel por **HTTPS**: pide un dominio (certificado de Let's Encrypt) o, si lo dejas vacío, usa la IP de la VM con un certificado propio de Caddy (el navegador pide aceptarlo la primera vez).
7. Con UFW activo: abre `443/tcp` (y `80/tcp` si hay dominio) y **borra la regla del 8080** de versiones anteriores.

Instalación manual equivalente:

```bash
sudo mkdir -p /opt/genieacs-api && cd /opt/genieacs-api
git clone https://github.com/mtandazo35/genieacs-api.git .
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env
sed -i "s/^GENIEACS_API_JWT_SECRET=.*/GENIEACS_API_JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))')/" .env
# ajustar GENIEACS_API_NBI_URL en .env
./.venv/bin/python manage.py init-admin admin 'UnaClaveLargaDe12+'
sudo cp genieacs-api.service genieacs-api-backup.service genieacs-api-backup.timer /etc/systemd/system/
sudo install -d -m 700 -o genieacs -g genieacs /var/backups/genieacs-api
sudo chown -R genieacs:genieacs /opt/genieacs-api && sudo chmod 600 .env
sudo systemctl daemon-reload && sudo systemctl enable --now genieacs-api genieacs-api-backup.timer
# y un reverse proxy HTTPS hacia 127.0.0.1:8080 (ver "Proxy propio")
```

## Configuración (`.env`, prefijo `GENIEACS_API_`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `NBI_URL` | `http://127.0.0.1:7557` | NBI de GenieACS (editable también en caliente desde Ajustes) |
| `ALLOWED_NBI_NETWORKS` | privadas + loopback + CGNAT | redes a las que Ajustes puede apuntar el NBI (anti-SSRF) |
| `JWT_SECRET` | — (**obligatorio**) | 32+ caracteres; sin él la API no arranca |
| `JWT_EXPIRE_MINUTES` | `480` | validez del token (8 h); cambiar clave o desactivar revoca antes |
| `LOGIN_MAX_FAILS_IP` / `LOGIN_MAX_FAILS_USER` | `5` / `20` | fallos de login permitidos por IP y minuto / por usuario y hora |
| `DB_PATH` | `genieacs_api.db` | SQLite (usuarios, metadatos, respaldos de CPE, auditoría) |
| `DEFAULT_CONNECTION_REQUEST` | `true` | aplicar cambios al instante vs. próximo inform |
| `MAX_UPLOAD_MB` | `512` | tamaño máximo de firmware/config |
| `FIRMWARE_ALLOWED_NETWORKS` | vacío | redes privadas desde las que se permite "cargar por URL" (p.ej. un repo interno `10.99.99.0/24`); por defecto solo URLs públicas |
| `AUTORESTORE_MAX_ATTEMPTS` / `AUTORESTORE_SUSPEND_HOURS` | `3` / `6` | intentos de auto-restauración sin éxito antes de pausarla, y horas de pausa |

La **NBI URL** puede cambiarse sin reiniciar desde el panel (Ajustes) o `PUT /settings`; se guarda en la BD y tiene prioridad sobre el `.env`.

## Actualizar

```bash
bash /root/install.sh update        # respaldo de la BD + git pull + pip + restart
bash /root/install.sh install       # igual, y además (re)configura servicio, HTTPS y firewall
```

Antes de actualizar, el instalador hace una copia consistente de la BD en `/root/backups/genieacs_api-FECHA.db` y aborta si no puede.

### Desde la 1.3 o anteriores (API en `0.0.0.0:8080`)

La 1.4 cambia el perímetro: la API pasa a escuchar solo en localhost y se publica por HTTPS. Para migrar:

1. `bash /root/install.sh install` (no `update`: `update` no cambia la exposición de una instalación vieja y avisa de ello).
2. Entrar por `https://<IP o dominio>/`. El `http://IP:8080` deja de responder.
3. Todos los usuarios vuelven a iniciar sesión una vez (los tokens viejos no llevan `token_version`).
4. Las claves de menos de 12 caracteres **siguen funcionando** para entrar; la política aplica a las claves nuevas. Cambia las cortas desde *Mi cuenta* / *Usuarios*.
5. Si el `.env` tenía el secreto de ejemplo o uno corto, el instalador genera uno nuevo.

La BD se migra sola al arrancar (columnas nuevas; no se pierde nada).

## Operación

- Estado/logs: `systemctl status genieacs-api` · `journalctl -u genieacs-api -f` (los errores del auto-restore y de la auditoría ya no se silencian: salen aquí).
- Usuarios sin panel: `./.venv/bin/python manage.py init-admin|add-isp|list|disable`.
- **Respaldo de la BD**: automático cada día a las 03:15 en `/var/backups/genieacs-api` (14 copias). Manual: `sudo -u genieacs ./.venv/bin/python manage.py backup-db /var/backups/genieacs-api`. No copies el `.db` con `cp` mientras la API escribe: `backup-db` usa la API de backup de SQLite y la copia sale consistente. Guarda una copia fuera del servidor: contiene claves WiFi/PPPoE de los CPE.
- Restaurar: `systemctl stop genieacs-api`, copiar la copia sobre `genieacs_api.db`, `chown genieacs:genieacs` + `chmod 600`, `systemctl start genieacs-api`.
- Un archivo de firmware cargado responde con su **SHA256**; compáralo con el publicado por el fabricante antes de un envío masivo (también queda en el journal).

## Seguridad

El panel expone login, claves WiFi/PPPoE y datos de clientes.

1. **Perímetro**: la API solo escucha en `127.0.0.1:8080`; el acceso es por el proxy HTTPS. No publiques el 8080.
2. **Firewall**: abre `443` solo hacia la red de gestión si el panel no es para Internet:
   ```bash
   ufw delete allow 443/tcp
   ufw allow from <red-gestion> to any port 443 proto tcp
   ```
3. **El NBI de GenieACS (`:7557`) no tiene autenticación**: que solo lo alcancen esta API y localhost.
4. **Clave admin inicial**: cámbiala (panel → Mi cuenta).
5. `JWT_SECRET` único por instalación y `.env` con permisos `600` (el instalador lo deja así). Rotar el secreto cierra todas las sesiones.
6. Límite de intentos de login: lo aplica la propia API (por IP real, que uvicorn toma del `X-Forwarded-For` del proxy local). Si pones otro proxy delante de Caddy, ajusta `--forwarded-allow-ips` en el servicio.

### Proxy propio

Si ya tienes nginx/NPM/HAProxy en la VM (el instalador no instala Caddy cuando el 443 está ocupado), publícalo hacia `127.0.0.1:8080`. nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name panel.example.com;
    ssl_certificate     /etc/letsencrypt/live/panel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.example.com/privkey.pem;
    client_max_body_size 520m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}
```

Si el proxy está en **otra** máquina, cambia en el servicio `--host 127.0.0.1` por la IP interna y `--forwarded-allow-ips` por la IP del proxy, y limita el 8080 en el firewall a esa IP.

Acceso de emergencia sin proxy (túnel SSH): `ssh -L 8080:127.0.0.1:8080 root@<vm>` y abrir `http://localhost:8080/`.

## Entregar el ACS por DHCP (MikroTik)

Un CPE que no recibe la URL del ACS **nunca aparece** en el panel. La vía estándar es la opción 43 del DHCP: el equipo se anuncia en la opción 60 como `dslforum.org` y el servidor le responde con la URL.

El panel genera este script con tus valores en **Aprovisionamiento → DHCP / Option 43** (o `POST /provisioning/dhcp/script`). Abajo queda el ejemplo completo, con una red de documentación (`192.0.2.0/24`) y un ACS en `10.20.30.5`, para tenerlo a mano sin abrir el panel.

### Las tres codificaciones, y por qué hay tres

| Codificación | Qué manda | Cuándo hace falta |
|---|---|---|
| **TLV** (opción 43) | subopción 1 + longitud + URL | es lo que dice TR-069; lo entienden los equipos que siguen el estándar |
| **URL plana** (opción 43) | la URL tal cual | muchos equipos baratos no entienden el TLV y solo leen la cadena |
| **Opción 125** | enterprise 3561 (BBF) + la misma subopción | algunos Huawei y ONT la piden en vez de la 43 |

La opción 43 **solo admite un valor por cliente**: o TLV o cadena plana. En **RouterOS 7** un *matcher* por la opción 60 permite dar TLV a quien se anuncia como `dslforum.org` y la plana al resto; en **RouterOS 6 no existen los matchers**, así que hay que elegir una.

### Ejemplo completo (RouterOS 7, ambas codificaciones)

El valor hexadecimal no es un número mágico: es `0x01` (subopción 1) + la longitud en bytes + la URL en hexadecimal. Para `http://10.20.30.5:7547/`, que mide 23 bytes (`0x17`), sale `0x0117...`. Si cambias la URL, **cambia también la longitud**; por eso conviene generarlo en el panel en vez de copiar y editar a mano.

```rsc
/ip dhcp-server option
add code=43 name=tr069-genieacs-tlv value=0x0117687474703a2f2f31302e32302e33302e353a373534372f comment="tr069-genieacs (subopcion 1 = URL del ACS)"
add code=43 name=tr069-genieacs-plana value="'http://10.20.30.5:7547/'" comment="tr069-genieacs (URL sin TLV)"
add code=125 name=tr069-genieacs-125 value=0x00000de9190117687474703a2f2f31302e32302e33302e353a373534372f comment="tr069-genieacs (enterprise 3561)"

/ip dhcp-server option sets
add name=tr069-genieacs-tlv options=tr069-genieacs-tlv,tr069-genieacs-125 comment="tr069-genieacs"
add name=tr069-genieacs-plana options=tr069-genieacs-plana,tr069-genieacs-125 comment="tr069-genieacs"

# El CPE que se anuncia como dslforum.org entiende TLV; al resto se le da la URL plana.
/ip dhcp-server matcher
add name=tr069-genieacs-dslforum code=60 matching-type=substring value="dslforum.org" option-set=tr069-genieacs-tlv server=all comment="tr069-genieacs"

# Se asigna a redes que YA existen; este script no crea ninguna red.
/ip dhcp-server network
set [find address="192.0.2.0/24"] dhcp-option-set=tr069-genieacs-plana
```

### Comprobar que quedó puesto

```rsc
/ip dhcp-server option print where name~"tr069-genieacs"
/ip dhcp-server network print detail
/ip dhcp-server lease print detail          # mira la opción 60 que anuncia cada CPE
```

Y en el ACS, que el equipo aparezca: el panel lo lista en cuanto hace su primer *inform*.

### Deshacer

```rsc
/ip dhcp-server network
set [find address="192.0.2.0/24"] dhcp-option-set=""
/ip dhcp-server matcher remove [find comment="tr069-genieacs"]
/ip dhcp-server option sets remove [find comment="tr069-genieacs"]
/ip dhcp-server option remove [find comment~"tr069-genieacs"]
```

Todo lo que crea el script lleva el comentario `tr069-genieacs`, así que el bloque de arriba borra eso y nada más.

### Lo que el DHCP no puede arreglar

La opción 43 solo le dice **a dónde llamar** a un cliente TR-069 que ya está corriendo. Si el equipo tiene el TR-069 apagado —típico en modo AP, o en algunos modelos tras un factory reset— no hay ajuste de DHCP que lo levante: hay que activarlo en su interfaz web, o pedir firmware con el ACS precargado. Para saber en cuál de los dos casos estás, mira si el equipo tiene el **puerto 7547 abierto**: si está cerrado, su cliente TR-069 no está corriendo.

```bash
# desde el ACS, contra la IP del CPE
timeout 3 bash -c "echo > /dev/tcp/<ip-del-cpe>/7547" && echo "cliente TR-069 activo" || echo "TR-069 apagado en el equipo"
```

Un aviso de RouterOS 6: si te apoyas en un matcher, no aplicará nada y **no avisará de nada**. Asigna el conjunto directamente al servidor o a la red.

## Cómo llegan los CPE al ACS

La API solo puede gestionar un CPE **cuando este habla TR-069 con el ACS**. Para eso el CPE necesita:
- TR-069 activado con la URL del ACS (`http://<acs>:7547/`), y
- que el ACS sea **alcanzable** desde la red del CPE (IP pública/routable o ruteo interno).

Autodescubrimiento por **DHCP Option 43** (si el CPE lo soporta): el servidor DHCP entrega la URL del ACS — los comandos exactos están arriba, en [Entregar el ACS por DHCP](#entregar-el-acs-por-dhcp-mikrotik). Ojo: muchos equipos de consumo **no** lo soportan o no reactivan TR-069 tras un factory reset — en ese caso se requiere firmware OEM con el ACS pre-cargado o pre-aprovisionar el equipo.

Un `factory reset` del cliente borra la config y, si el firmware no trae TR-069 pre-activado, el equipo deja de reportar y la auto-restauración no puede actuar hasta que vuelva a hablar con el ACS.

## Problemas frecuentes

- **El servicio no arranca y el journal dice `GENIEACS_API_JWT_SECRET ...`**: falta el secreto o es el de ejemplo/corto. `bash /root/install.sh update` lo regenera, o ponlo a mano en `.env`.
- **`http://IP:8080` ya no responde tras actualizar**: es lo esperado desde la 1.4; entra por `https://IP/`.
- **El navegador avisa del certificado**: con la IP (sin dominio) Caddy usa su propia CA. Acéptalo, o instala la raíz de `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt` en los equipos de soporte.
- **`429 Demasiados intentos`**: espera el tiempo indicado; el contador es en memoria y también se reinicia al reiniciar el servicio.
- **"Destino no permitido" al cargar firmware por URL**: el servidor está en una red privada; añádela a `FIRMWARE_ALLOWED_NETWORKS`.
- **La auto-restauración dice "en pausa"**: el equipo no conserva algún valor (lo rechaza o revierte). Revisa el motivo en la pestaña Respaldo; al desactivar y reactivar la auto-restauración se reinicia el contador.
- **`git pull` da "dubious ownership"**: el repo es del usuario `genieacs` y ejecutas como root → `git config --global --add safe.directory /opt/genieacs-api`, y tras el pull `chown -R genieacs:genieacs /opt/genieacs-api`.
- **El panel muestra datos viejos**: es la caché del ACS (último reporte). Usa "Leer datos"/"Actualizar" o espera al siguiente inform.
- **Un cambio "no se aplica"**: el CPE puede tener varias conexiones WAN; la API usa la activa. Verifica en la pestaña WAN cuál está `Connected`.
- **El navegador no toma la última versión del panel**: fuerza recarga (Ctrl+Shift+R); los estáticos ya van con `no-cache`.
- **Instalación de MongoDB del ACS en Debian 13**: usar el repo de bookworm (el de `trixie` está vacío) — aplica al instalador del ACS, no a esta API.
