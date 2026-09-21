"""Limitador de intentos fallidos de login (en memoria, por proceso).

Cuenta solo FALLOS en una ventana deslizante: por IP (frena barridos) y por
usuario (frena ataques distribuidos contra una cuenta). Un login correcto
limpia los fallos de ese usuario.
"""
import time
from collections import defaultdict, deque

IP_WINDOW = 60          # s
USER_WINDOW = 3600      # s

_by_ip: dict[str, deque] = defaultdict(deque)
_by_user: dict[str, deque] = defaultdict(deque)


def _prune(q: deque, window: int, now: float) -> None:
    while q and now - q[0] > window:
        q.popleft()


def retry_after(ip: str, username: str, max_ip: int, max_user: int, now: float | None = None) -> int:
    """Segundos que hay que esperar, o 0 si se permite intentar."""
    now = time.monotonic() if now is None else now
    wait = 0
    for store, key, window, limit in ((_by_ip, ip, IP_WINDOW, max_ip),
                                      (_by_user, username.lower(), USER_WINDOW, max_user)):
        q = store.get(key)
        if q is None:
            continue
        _prune(q, window, now)
        if not q:
            del store[key]          # no acumular claves vacias en memoria
            continue
        if len(q) >= limit:
            wait = max(wait, int(window - (now - q[0])) + 1)
    return wait


def record_failure(ip: str, username: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    _by_ip[ip].append(now)
    _by_user[username.lower()].append(now)


def record_success(username: str) -> None:
    # Solo limpia la cuenta: los fallos de la IP se mantienen, para que entrar
    # con una cuenta propia no sirva para resetear el contador de la IP.
    _by_user.pop(username.lower(), None)


def reset() -> None:
    _by_ip.clear()
    _by_user.clear()
