#!/usr/bin/env python3
"""Utilidad CLI: crear el primer admin y gestionar usuarios sin la API.

Uso:
  python manage.py init-admin <usuario> <clave>
  python manage.py add-isp <usuario> <clave> <isp_tag>
  python manage.py list
  python manage.py disable <usuario>
"""
import sys

from app.db import create_user, init_db, list_users, set_active, get_user
from app.security import hash_password


def _create(username, password, role, isp_tag):
    if get_user(username):
        print(f"El usuario '{username}' ya existe."); sys.exit(1)
    create_user(username, hash_password(password), role, isp_tag)
    print(f"OK: creado {role} '{username}'" + (f" (ISP={isp_tag})" if isp_tag else ""))


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
    elif cmd == "disable" and len(sys.argv) == 3:
        set_active(sys.argv[2], False); print(f"OK: '{sys.argv[2]}' deshabilitado")
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
