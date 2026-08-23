#!/bin/bash
#===============================================================
# GenieACS API Installer - Debian 12/13 y Ubuntu 22.04/24.04
# API FastAPI multi-tenant sobre GenieACS
# https://github.com/mtandazo35/genieacs-api
#===============================================================

REPO="https://github.com/mtandazo35/genieacs-api.git"
APP_DIR="/opt/genieacs-api"
SVC="genieacs-api"
PORT="8080"

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

clone_or_update(){
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null
  if [ -d "$APP_DIR/.git" ]; then
    echo "Actualizando codigo..."; git -C "$APP_DIR" pull -q || err "git pull fallo"
  else
    echo "Descargando codigo..."; git clone -q "$REPO" "$APP_DIR" || err "git clone fallo"
  fi
  msg "Codigo en $APP_DIR"
}

setup_venv(){
  echo "Instalando entorno Python..."
  [ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt" || err "pip install fallo"
  msg "Entorno Python listo"
}

setup_env(){
  if [ -f "$APP_DIR/.env" ]; then
    msg "Config existente en $APP_DIR/.env (se conserva)"
    return
  fi
  echo ""
  read -rp "URL del NBI de GenieACS [http://127.0.0.1:7557]: " NBI < /dev/tty
  NBI=${NBI:-http://127.0.0.1:7557}
  SECRET=$("$APP_DIR/.venv/bin/python" -c "import secrets;print(secrets.token_hex(32))")
  cat > "$APP_DIR/.env" <<EOF
GENIEACS_API_NBI_URL=${NBI}
GENIEACS_API_JWT_SECRET=${SECRET}
GENIEACS_API_JWT_EXPIRE_MINUTES=720
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
  n=$("$APP_DIR/.venv/bin/python" -c "from app.db import init_db,list_users; init_db(); print(len(list_users()))" 2>/dev/null || echo 0)
  if [ "$n" != "0" ]; then msg "Ya existen usuarios (no se crea admin)"; return; fi
  echo ""
  read -rp "Usuario admin [admin]: " AU < /dev/tty; AU=${AU:-admin}
  read -rp "Clave admin (vacio = generar): " AP < /dev/tty
  [ -z "$AP" ] && AP=$("$APP_DIR/.venv/bin/python" -c "import secrets;print(secrets.token_urlsafe(12))")
  (cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" manage.py init-admin "$AU" "$AP" >/dev/null) || err "No se pudo crear el admin"
  ADMIN_USER="$AU"; ADMIN_PASS="$AP"
  msg "Admin creado: $AU"
}

setup_service(){
  chown -R genieacs:genieacs "$APP_DIR"
  cp "$APP_DIR/${SVC}.service" "/etc/systemd/system/${SVC}.service"
  systemctl daemon-reload
  systemctl enable --now "$SVC" >/dev/null 2>&1
  sleep 3
  systemctl is-active --quiet "$SVC" && msg "Servicio $SVC activo" \
    || { warn "El servicio no arranco:"; journalctl -u "$SVC" -n 15 --no-pager; }
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow ${PORT}/tcp comment 'GenieACS API panel' >/dev/null 2>&1
    msg "UFW: abierto ${PORT}/tcp"
  fi
}

summary(){
  IP=$(hostname -I | awk '{print $1}')
  echo ""
  echo -e "${C}===============================================${N}"
  echo -e "${G} GenieACS API instalada${N}"
  echo -e "${C}===============================================${N}"
  echo "  Panel:  http://${IP}:${PORT}/"
  echo "  Docs:   http://${IP}:${PORT}/docs"
  [ -n "$ADMIN_USER" ] && echo "  Admin:  $ADMIN_USER / $ADMIN_PASS  (cambiala)"
  echo "  Config: $APP_DIR/.env   (NBI del ACS ajustable tambien en Ajustes)"
  echo ""
  warn "Sin TLS: pon un reverse proxy (HTTPS) si sale de la red interna"
}

uninstall(){
  read -rp "Eliminar GenieACS API y su servicio? [s/N]: " ok < /dev/tty
  [ "$ok" = "s" ] || [ "$ok" = "S" ] || { echo "Cancelado"; return; }
  systemctl disable --now "$SVC" >/dev/null 2>&1
  rm -f "/etc/systemd/system/${SVC}.service"; systemctl daemon-reload
  read -rp "Borrar tambien $APP_DIR (codigo, .env y BD de usuarios)? [s/N]: " ok < /dev/tty
  { [ "$ok" = "s" ] || [ "$ok" = "S" ]; } && rm -rf "$APP_DIR"
  msg "Desinstalado"
}

banner
case "${1:-}" in
  install|--install) OPT=1 ;;
  update|--update)   OPT=3 ;;
  uninstall|--uninstall) OPT=2 ;;
  *)
    echo "  1) Instalar / Actualizar"
    echo "  2) Desinstalar"
    echo "  3) Solo actualizar codigo"
    echo ""
    read -rp "Opcion [1]: " OPT < /dev/tty; OPT=${OPT:-1} ;;
esac

case "$OPT" in
  1) check_system; install_deps; clone_or_update; setup_venv; setup_env; create_admin; setup_service; summary ;;
  3) check_system; clone_or_update; setup_venv; chown -R genieacs:genieacs "$APP_DIR"; systemctl restart "$SVC"; msg "Actualizado y reiniciado" ;;
  2) uninstall ;;
  *) echo "Saliendo" ;;
esac
