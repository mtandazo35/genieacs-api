"""Configuracion via variables de entorno (prefijo GENIEACS_API_)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GENIEACS_API_", env_file=".env")

    # GenieACS NBI (northbound). En la misma VM suele ser localhost:7557
    nbi_url: str = "http://127.0.0.1:7557"
    nbi_timeout: float = 30.0

    # Autenticacion de la API
    jwt_secret: str = "CAMBIAME"           # generar uno fuerte en produccion
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720          # 12 h

    # Base de datos de usuarios (SQLite)
    db_path: str = "genieacs_api.db"

    # Connection request al aplicar cambios (aplica al instante vs. proximo inform)
    default_connection_request: bool = True

    # Prefijo del tag que marca horario de reinicio (ver provision scheduled-reboot)
    reboot_tag_prefix: str = "reboot@"


@lru_cache
def get_settings() -> Settings:
    return Settings()
