"""Configuracion via variables de entorno (prefijo GENIEACS_API_)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

# Valores que nunca se aceptan como secreto JWT (placeholders de ejemplo)
_WEAK_SECRETS = {"", "CAMBIAME", "changeme", "secret"}
MIN_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GENIEACS_API_", env_file=".env")

    # GenieACS NBI (northbound). En la misma VM suele ser localhost:7557
    nbi_url: str = "http://127.0.0.1:7557"
    nbi_timeout: float = 30.0
    # Redes a las que se permite apuntar el NBI (Ajustes). Evita que la API se
    # use para hacer peticiones a destinos arbitrarios (SSRF).
    allowed_nbi_networks: str = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10"

    # Autenticacion de la API. Sin secreto valido la API no arranca.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480          # 8 h (la revocacion va por token_version)

    # Anti fuerza bruta en /auth/login (fallos por ventana)
    login_max_fails_ip: int = 5            # por IP y minuto
    login_max_fails_user: int = 20         # por usuario y hora

    # Base de datos de usuarios (SQLite)
    db_path: str = "genieacs_api.db"

    # Connection request al aplicar cambios (aplica al instante vs. proximo inform)
    default_connection_request: bool = True

    # Prefijo del tag que marca horario de reinicio (ver provision scheduled-reboot)
    reboot_tag_prefix: str = "reboot@"

    # Firmware: tamano maximo (MB) y redes privadas permitidas para descargar por URL
    # (por defecto solo destinos publicos; p.ej. "10.99.99.0/24" para un repo interno)
    max_upload_mb: int = 512
    firmware_allowed_networks: str = ""

    # Auto-restauracion: intentos seguidos sin corregir el drift antes de suspender
    autorestore_max_attempts: int = 3
    autorestore_suspend_hours: int = 6


def jwt_secret_problem(secret: str) -> str | None:
    """Motivo por el que el secreto no sirve, o None si es aceptable."""
    if secret.strip() in _WEAK_SECRETS:
        return "GENIEACS_API_JWT_SECRET no esta definido (o es el valor de ejemplo)"
    if len(secret) < MIN_SECRET_LEN:
        return f"GENIEACS_API_JWT_SECRET debe tener al menos {MIN_SECRET_LEN} caracteres"
    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
