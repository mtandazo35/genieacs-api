#!/usr/bin/env python3
"""Utilidad CLI: crear el primer admin y gestionar usuarios sin la API.

Uso:
  python manage.py init-admin <usuario> <clave>
  python manage.py add-isp <usuario> <clave> <isp_tag>
  python manage.py list
  python manage.py disable <usuario>
  python manage.py backup-db <directorio> [copias_a_conservar=14]
"""
import glob
import os
import sqlite3
import sys
import time

from app.db import create_user, init_db, list_users, set_active, get_user
from app.security import hash_password, password_problem


def _create(username, password, role, isp_tag):
    if get_user(username):
        print(f"El usuario '{username}' ya existe."); sys.exit(1)
    problem = password_problem(password)
    if problem:
        print(problem); sys.exit(2)
    create_user(username, hash_password(password), role, isp_tag)
    print(f"OK: creado {role} '{username}'" + (f" (ISP={isp_tag})" if isp_tag else ""))


def backup_db(dest_dir, keep=14):
    """Copia consistente de la BD (API de backup de SQLite, segura con la
    API escribiendo) y borra las copias mas viejas que las `keep` ultimas."""
    from app.config import get_settings
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    out = os.path.join(dest_dir, time.strftime("genieacs_api-%Y%m%d-%H%M%S.db"))
    src = sqlite3.connect(get_settings().db_path)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    os.chmod(out, 0o600)
    old = sorted(glob.glob(os.path.join(dest_dir, "genieacs_api-*.db")))[:-keep] if keep > 0 else []
    for f in old:
        os.remove(f)
    print(f"OK: {out} (borradas {len(old)} copias antiguas)")


def main():
    init_db()
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init-admin" and len(sys.argv) == 4:
        _create(sys.argv[2], sys.argv[3], "admin", None)
    elif cmd == "add-isp" and len(sys.argv) == 5:
        _create(sys.argv[2], sys.argv[3], "isp", sys.argv[4])
    elif cmd == "list":
        for u in list_users():
            print(f"#{u['id']:>3} {u['username']:<20} {u['role']:<6} "
                  f"ISP={u['isp_tag'] or '-':<12} activo={bool(u['active'])}")
    elif cmd == "backup-db" and len(sys.argv) in (3, 4):
        backup_db(sys.argv[2], int(sys.argv[3]) if len(sys.argv) == 4 else 14)
    elif cmd == "disable" and len(sys.argv) == 3:
        set_active(sys.argv[2], False); print(f"OK: '{sys.argv[2]}' deshabilitado")
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
