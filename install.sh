#!/bin/bash
#===============================================================
# GenieACS API Installer - Debian 12/13 y Ubuntu 22.04/24.04
# API FastAPI multi-tenant sobre GenieACS
# https://github.com/mtandazo35/genieacs-api
#===============================================================

REPO="https://github.com/mtandazo35/genieacs-api.git"
APP_DIR="/opt/genieacs-api"
SVC="genieacs-api"
PORT="8080"                       # solo en 127.0.0.1; se publica por HTTPS (Caddy)
BACKUP_DIR="/root/backups"
CADDY_MARK="# genieacs-api (install.sh)"

R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; N='\033[0m'
msg(){ echo -e "${G}[OK]${N} $1"; }
warn(){ echo -e "${Y}[!]${N} $1"; }
err(){ echo -e "${R}[ERROR]${N} $1"; exit 1; }

banner(){
  echo -e "${C}"
  echo "==============================================="
  echo "          GenieACS API Installer"
  echo "   Panel + API multi-tenant sobre GenieACS"
  echo "==============================================="
  echo -e "${N}"
}

check_system(){
  [ "$(id -u)" -eq 0 ] || err "Ejecutar como root"
  command -v systemctl >/dev/null 2>&1 || err "Se requiere systemd"
  . /etc/os-release 2>/dev/null || err "SO no detectado"
  case "$ID" in debian|ubuntu) ;; *) err "Solo Debian/Ubuntu (detectado: $ID)";; esac
}

install_deps(){
  echo "Instalando dependencias del sistema..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3-venv python3-pip curl >/dev/null || err "Fallo instalando dependencias"
  id genieacs >/dev/null 2>&1 || useradd --system --no-create-home --user-group genieacs
  msg "Dependencias listas (git $(git --version | awk '{print $3}'), python $(python3 -V | awk '{print $2}'))"
}

# Respaldo consistente de la BD antes de tocar el codigo (usuarios, respaldos de CPE, auditoria)
backup_db(){
  [ -f "$APP_DIR/genieacs_api.db" ] || return 0
  mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
  local out="$BACKUP_DIR/genieacs_api-$(date +%Y%m%d-%H%M%S).db"
  python3 - "$APP_DIR/genieacs_api.db" "$out" <<'PY' || err "No se pudo respaldar la BD; se aborta"
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
  chmod 600 "$out"
  msg "BD respaldada en $out"
}

clone_or_update(){
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null
  if [ -d "$APP_DIR/.git" ]; then
    backup_db
    echo "Actualizando codigo..."; git -C "$APP_DIR" pull -q || err "git pull fallo"
  else
    echo "Descargando codigo..."; git clone -q "$REPO" "$APP_DIR" || err "git clone fallo"
  fi
  msg "Codigo en $APP_DIR ($(git -C "$APP_DIR" rev-parse --short HEAD))"
}

setup_venv(){
  echo "Instalando entorno Python..."
  [ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt" || err "pip install fallo"
  msg "Entorno Python listo"
}

new_secret(){ "$APP_DIR/.venv/bin/python" -c "import secrets;print(secrets.token_hex(32))"; }

setup_env(){
  if [ -f "$APP_DIR/.env" ]; then
    msg "Config existente en $APP_DIR/.env (se conserva)"
    # la API ya no arranca con un secreto vacio/de ejemplo/corto: repararlo
    local cur
    cur=$(sed -n 's/^GENIEACS_API_JWT_SECRET=//p' "$APP_DIR/.env" | tail -1)
    if [ ${#cur} -lt 32 ] || [ "$cur" = "CAMBIAME" ]; then
      sed -i '/^GENIEACS_API_JWT_SECRET=/d' "$APP_DIR/.env"
      echo "GENIEACS_API_JWT_SECRET=$(new_secret)" >> "$APP_DIR/.env"
      warn "JWT secret debil o ausente: se genero uno nuevo (las sesiones abiertas se cierran)"
    fi
    chmod 600 "$APP_DIR/.env"
    return
  fi
  echo ""
  read -rp "URL del NBI de GenieACS [http://127.0.0.1:7557]: " NBI < /dev/tty
  NBI=${NBI:-http://127.0.0.1:7557}
  cat > "$APP_DIR/.env" <<EOF
GENIEACS_API_NBI_URL=${NBI}
GENIEACS_API_JWT_SECRET=$(new_secret)
GENIEACS_API_JWT_EXPIRE_MINUTES=480
GENIEACS_API_DB_PATH=${APP_DIR}/genieacs_api.db
GENIEACS_API_DEFAULT_CONNECTION_REQUEST=true
EOF
  chmod 600 "$APP_DIR/.env"
  msg "Config creada (NBI: $NBI, JWT secret generado)"
}

create_admin(){
  # solo si no hay usuarios aun
  set -a; . "$APP_DIR/.env"; set +a
  local n
  n=$(cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" -c "from app.db import init_db,list_users; init_db(); print(len(list_users()))" 2>/dev/null || echo 0)
  if [ "$n" != "0" ]; then msg "Ya existen usuarios (no se crea admin)"; return; fi
  echo ""
  read -rp "Usuario admin [admin]: " AU < /dev/tty; AU=${AU:-admin}
  while :; do
    read -rp "Clave admin, minimo 12 caracteres (vacio = generar): " AP < /dev/tty
    [ -z "$AP" ] && AP=$("$APP_DIR/.venv/bin/python" -c "import secrets;print(secrets.token_urlsafe(15))")
    [ ${#AP} -ge 12 ] && break
    warn "La clave debe tener al menos 12 caracteres"
  done
  (cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" manage.py init-admin "$AU" "$AP" >/dev/null) || err "No se pudo crear el admin"
  ADMIN_USER="$AU"; ADMIN_PASS="$AP"
  msg "Admin creado: $AU"
}

install_caddy(){
  command -v caddy >/dev/null 2>&1 && return 0
  echo "Instalando Caddy (reverse proxy HTTPS)..."
  apt-get install -y -qq caddy >/dev/null 2>&1 && return 0
  # sin paquete en la distro (p.ej. Ubuntu 22.04): repo oficial de Caddy
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null 2>&1
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg || return 1
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list || return 1
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null 2>&1
}

setup_proxy(){
  PROXY_OK=0
  if ss -ltnp 2>/dev/null | grep -E ':443\b' | grep -vq caddy; then
    warn "El puerto 443 ya lo usa otro servicio: no se instala Caddy."
    warn "Publica 127.0.0.1:${PORT} con tu proxy (ver DEPLOY.md)."
    return
  fi
  install_caddy || { warn "No se pudo instalar Caddy; publica 127.0.0.1:${PORT} con tu proxy (ver DEPLOY.md)"; return; }
  echo ""
  read -rp "Dominio del panel para HTTPS con Let's Encrypt (vacio = usar la IP con certificado propio): " DOMAIN < /dev/tty
  IP=$(hostname -I | awk '{print $1}')
  if [ -n "$DOMAIN" ]; then SITE="$DOMAIN"; TLS=""; PANEL_URL="https://$DOMAIN/"
  else SITE="https://$IP"; TLS="    tls internal"; PANEL_URL="https://$IP/"; fi
  local conf="/etc/caddy/Caddyfile"
  if [ -f "$conf" ] && ! grep -q "$CADDY_MARK" "$conf" \
     && ! grep -q "The Caddyfile is an easy way to configure your Caddy web server" "$conf"; then
    conf="/etc/caddy/genieacs-api.caddy"   # Caddyfile propio del admin: no se pisa
  fi
  cat > "$conf" <<EOF
$CADDY_MARK
$SITE {
$TLS
    request_body {
        max_size 520MB
    }
    reverse_proxy 127.0.0.1:${PORT} {
        transport http {
            read_timeout 300s
        }
    }
}
EOF
  if [ "$conf" != "/etc/caddy/Caddyfile" ]; then
    warn "Ya hay un Caddyfile propio: la config quedo en $conf"
    warn "Anade al Caddyfile la linea:  import $conf   y recarga Caddy"
  fi
  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    systemctl enable --now caddy >/dev/null 2>&1
    systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy
    PROXY_OK=1
    msg "Caddy publica el panel en $PANEL_URL"
  else
    warn "El Caddyfile no valida:"; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | tail -5
  fi
}

setup_firewall(){
  command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active" || return 0
  { ufw delete allow ${PORT}/tcp comment 'GenieACS API panel' >/dev/null 2>&1 \
    || ufw delete allow ${PORT}/tcp >/dev/null 2>&1; } \
    && msg "UFW: cerrado ${PORT}/tcp (la API ya no escucha fuera de localhost)"
  if [ "$PROXY_OK" = "1" ]; then
    ufw allow 443/tcp comment 'GenieACS API panel (HTTPS)' >/dev/null 2>&1
    [ -n "$DOMAIN" ] && ufw allow 80/tcp comment 'ACME / redireccion a HTTPS' >/dev/null 2>&1
    msg "UFW: abierto 443/tcp$([ -n "$DOMAIN" ] && echo ' y 80/tcp')"
  fi
}

install_units(){
  cp "$APP_DIR/${SVC}.service" "/etc/systemd/system/${SVC}.service"
  cp "$APP_DIR/${SVC}-backup.service" "$APP_DIR/${SVC}-backup.timer" /etc/systemd/system/
  install -d -m 700 -o genieacs -g genieacs /var/backups/genieacs-api
  systemctl daemon-reload
  systemctl enable --now "${SVC}-backup.timer" >/dev/null 2>&1
}

setup_service(){
  chown -R genieacs:genieacs "$APP_DIR"
  chmod 600 "$APP_DIR/.env"
  [ -f "$APP_DIR/genieacs_api.db" ] && chmod 600 "$APP_DIR/genieacs_api.db"
  install_units
  systemctl enable "$SVC" >/dev/null 2>&1
  systemctl restart "$SVC"
  sleep 3
  systemctl is-active --quiet "$SVC" && msg "Servicio $SVC activo (127.0.0.1:${PORT})" \
    || { warn "El servicio no arranco:"; journalctl -u "$SVC" -n 15 --no-pager; }
}

summary(){
  echo ""
  echo -e "${C}===============================================${N}"
  echo -e "${G} GenieACS API instalada${N}"
  echo -e "${C}===============================================${N}"
  if [ "$PROXY_OK" = "1" ]; then
    echo "  Panel:  ${PANEL_URL}"
    echo "  Docs:   ${PANEL_URL}docs"
    [ -z "$DOMAIN" ] && echo "  (certificado propio de Caddy: el navegador pedira aceptarlo la primera vez)"
  else
    echo "  API en 127.0.0.1:${PORT} (sin proxy). Acceso temporal por tunel SSH:"
    echo "    ssh -L ${PORT}:127.0.0.1:${PORT} root@$(hostname -I | awk '{print $1}')  ->  http://localhost:${PORT}/"
  fi
  [ -n "$ADMIN_USER" ] && echo "  Admin:  $ADMIN_USER / $ADMIN_PASS  (cambiala)"
  echo "  Config: $APP_DIR/.env   (NBI del ACS ajustable tambien en Ajustes)"
  echo "  Respaldo BD: diario en /var/backups/genieacs-api (timer ${SVC}-backup)"
  echo ""
}

update_only(){
  check_system; clone_or_update; setup_venv; setup_env
  chown -R genieacs:genieacs "$APP_DIR"
  if grep -q -- "--host 0.0.0.0" "/etc/systemd/system/${SVC}.service" 2>/dev/null; then
    # instalacion previa sin proxy: no cambiar la exposicion en una simple actualizacion
    warn "El servicio instalado sigue escuchando en 0.0.0.0:${PORT} (HTTP)."
    warn "Ejecuta 'install' (opcion 1) para pasar a 127.0.0.1 + HTTPS con Caddy."
  else
    install_units
  fi
  systemctl restart "$SVC"; sleep 2
  systemctl is-active --quiet "$SVC" && msg "Actualizado y reiniciado" \
    || { warn "El servicio no arranco:"; journalctl -u "$SVC" -n 15 --no-pager; }
}

uninstall(){
  read -rp "Eliminar GenieACS API y su servicio? [s/N]: " ok < /dev/tty
  [ "$ok" = "s" ] || [ "$ok" = "S" ] || { echo "Cancelado"; return; }
  systemctl disable --now "$SVC" "${SVC}-backup.timer" >/dev/null 2>&1
  rm -f "/etc/systemd/system/${SVC}.service" "/etc/systemd/system/${SVC}-backup.service" \
        "/etc/systemd/system/${SVC}-backup.timer"; systemctl daemon-reload
  if grep -q "$CADDY_MARK" /etc/caddy/Caddyfile 2>/dev/null || [ -f /etc/caddy/genieacs-api.caddy ]; then
    warn "Caddy sigue instalado con la config del panel (/etc/caddy); quitala si ya no la usas"
  fi
  read -rp "Borrar tambien $APP_DIR (codigo, .env y BD de usuarios)? [s/N]: " ok < /dev/tty
  { [ "$ok" = "s" ] || [ "$ok" = "S" ]; } && { backup_db; rm -rf "$APP_DIR"; }
  msg "Desinstalado"
}

banner
case "${1:-}" in
  install|--install) OPT=1 ;;
  update|--update)   OPT=3 ;;
  uninstall|--uninstall) OPT=2 ;;
  *)
    echo "  1) Instalar / Actualizar (con HTTPS)"
    echo "  2) Desinstalar"
    echo "  3) Solo actualizar codigo"
    echo ""
    read -rp "Opcion [1]: " OPT < /dev/tty; OPT=${OPT:-1} ;;
esac

case "$OPT" in
  1) check_system; install_deps; clone_or_update; setup_venv; setup_env; create_admin
     setup_service; setup_proxy; setup_firewall; summary ;;
  3) update_only ;;
  2) uninstall ;;
  *) echo "Saliendo" ;;
esac
