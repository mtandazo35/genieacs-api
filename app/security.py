"""Hashing de claves (bcrypt), emision/verificacion de JWT y politica de claves."""
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from .config import get_settings, jwt_secret_problem

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

PASSWORD_MIN = 12
PASSWORD_MAX = 128        # bcrypt ignora mas alla de 72 bytes; tope para no hashear megas

# hash fijo para gastar el mismo tiempo cuando el usuario no existe (no delatar
# por tiempo de respuesta que usuarios existen)
_DUMMY_HASH = _pwd.hash("genieacs-api-dummy-password")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        _pwd.verify(plain, _DUMMY_HASH)
        return False
    return _pwd.verify(plain, hashed)


def password_problem(plain: str) -> str | None:
    """Motivo por el que la clave no cumple la politica, o None."""
    if len(plain) < PASSWORD_MIN:
        return f"La contraseña debe tener al menos {PASSWORD_MIN} caracteres"
    if len(plain) > PASSWORD_MAX:
        return f"La contraseña no puede superar {PASSWORD_MAX} caracteres"
    return None


def _secret() -> str:
    s = get_settings().jwt_secret
    problem = jwt_secret_problem(s)
    if problem:
        raise RuntimeError(problem)
    return s


def create_token(user: dict) -> str:
    """El token solo identifica al usuario (nombre + id, que no se reutiliza si
    se borra y recrea) y la version de sus credenciales; rol e ISP se leen
    SIEMPRE de la BD al validar (no se confia en el token)."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"],
        "uid": user["id"],
        "ver": user["token_version"],
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_expire_minutes),
    }
    return jwt.encode(payload, _secret(), algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    s = get_settings()
    try:
        return jwt.decode(token, _secret(), algorithms=[s.jwt_algorithm],
                          options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
