# Notas de despliegue

Guía operativa para instalar, actualizar y mantener **genieacs-api** en producción.

## Requisitos

- Debian 12/13 o Ubuntu 22.04/24.04 con systemd, acceso root.
- Un **GenieACS** en marcha y alcanzable en su NBI (por defecto `:7557`) desde esta VM.
- Python 3.11+ (lo instala el script). Recomendado: VM dedicada, 1–2 vCPU / 1 GB.

## Instalación

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mtandazo35/genieacs-api/main/install.sh)
```

Qué hace: instala `git`/`python3-venv`/`pip`, crea el usuario de sistema `genieacs`, clona en `/opt/genieacs-api`, arma el venv, pide la **URL del NBI**, crea el **admin**, instala el servicio `genieacs-api` (uvicorn `:8080`) y abre 8080 en UFW si está activo.

Instalación manual equivalente:

```bash
sudo mkdir -p /opt/genieacs-api && cd /opt/genieacs-api
git clone https://github.com/mtandazo35/genieacs-api.git .
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env
sed -i "s/CAMBIAME/$(python3 -c 'import secrets;print(secrets.token_hex(32))')/" .env
# ajustar GENIEACS_API_NBI_URL en .env
./.venv/bin/python manage.py init-admin admin 'ClaveFuerte'
sudo cp genieacs-api.service /etc/systemd/system/
sudo chown -R genieacs:genieacs /opt/genieacs-api
sudo systemctl enable --now genieacs-api
```

## Configuración (`.env`, prefijo `GENIEACS_API_`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `NBI_URL` | `http://127.0.0.1:7557` | NBI de GenieACS (editable también en caliente desde Ajustes) |
| `JWT_SECRET` | — | secreto para firmar los tokens (generar uno fuerte) |
| `JWT_EXPIRE_MINUTES` | `720` | validez del token |
| `DB_PATH` | `genieacs_api.db` | SQLite (usuarios, metadatos, respaldos) |
| `DEFAULT_CONNECTION_REQUEST` | `true` | aplicar cambios al instante vs. próximo inform |

La **NBI URL** puede cambiarse sin reiniciar desde el panel (Ajustes) o `PUT /settings`; se guarda en la BD y tiene prioridad sobre el `.env`.

## Actualizar

```bash
bash /root/install.sh update        # git pull + pip + restart
# o manual:
cd /opt/genieacs-api && sudo -u genieacs git pull && sudo systemctl restart genieacs-api
```

## Operación

- Estado/logs: `systemctl status genieacs-api` · `journalctl -u genieacs-api -f`
- Usuarios sin panel: `./.venv/bin/python manage.py init-admin|add-isp|list|disable`
- Backup de la BD del panel: copiar el archivo `DB_PATH` (`.db`).

## Seguridad (recomendado en producción)

El panel expone login, claves WiFi/PPPoE y datos de clientes: **no lo dejes en HTTP plano fuera de la red interna.**

1. **Reverse proxy con TLS** (NPM/Caddy/nginx) delante de `:8080`; sirve el panel por HTTPS.
2. **Firewall**: permitir 8080 solo desde el proxy / red de gestión.
   ```bash
   ufw allow from <red-gestion> to any port 8080 proto tcp
   ```
3. **Cambiar la clave admin** inicial (panel → Usuarios / Mi cuenta).
4. `JWT_SECRET` único y `.env` con permisos `600`.

## Cómo llegan los CPE al ACS

La API solo puede gestionar un CPE **cuando este habla TR-069 con el ACS**. Para eso el CPE necesita:
- TR-069 activado con la URL del ACS (`http://<acs>:7547/`), y
- que el ACS sea **alcanzable** desde la red del CPE (IP pública/routable o ruteo interno).

Autodescubrimiento por **DHCP Option 43** (si el CPE lo soporta): el servidor DHCP entrega la URL del ACS. Ojo: muchos equipos de consumo **no** lo soportan o no reactivan TR-069 tras un factory reset — en ese caso se requiere firmware OEM con el ACS pre-cargado o pre-aprovisionar el equipo.

Un `factory reset` del cliente borra la config y, si el firmware no trae TR-069 pre-activado, el equipo deja de reportar y la auto-restauración no puede actuar hasta que vuelva a hablar con el ACS.

## Problemas frecuentes

- **`git pull` da "dubious ownership"**: el repo es del usuario `genieacs` y ejecutas como root → `git config --global --add safe.directory /opt/genieacs-api`, y tras el pull `chown -R genieacs:genieacs /opt/genieacs-api`.
- **El panel muestra datos viejos**: es la caché del ACS (último reporte). Usa "Leer datos"/"Actualizar" o espera al siguiente inform.
- **Un cambio "no se aplica"**: el CPE puede tener varias conexiones WAN; la API usa la activa. Verifica en la pestaña WAN cuál está `Connected`.
- **El navegador no toma la última versión del panel**: fuerza recarga (Ctrl+Shift+R); los estáticos ya van con `no-cache`.
- **Instalación de MongoDB del ACS en Debian 13**: usar el repo de bookworm (el de `trixie` está vacío) — aplica al instalador del ACS, no a esta API.
