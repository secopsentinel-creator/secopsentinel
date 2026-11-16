from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILES = (
    ".env",
    str(Path.home() / ".config" / "secopsentinel" / ".env"),
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    secret_key: str = "change-me"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_socket: str | None = None
    db_name: str = "secopsentinel"
    db_user: str = "secopsentinel_app"
    db_password: str = ""
    db_migration_user: str = "secopsentinel_migrate"
    db_migration_password: str = ""

    data_dir: Path = Path.home() / ".local" / "share" / "secopsentinel"
    cases_dir: Path | None = None
    quarantine_dir: Path | None = None

    ollama_base_url: str = "http://127.0.0.1:11434"

    session_cookie_name: str = "session"
    session_max_age: int = 60 * 60 * 8
    csrf_cookie_name: str = "csrf"
    cookie_secure: bool = False

    password_min_length: int = 12
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15

    @field_validator("data_dir", "cases_dir", "quarantine_dir", mode="before")
    @classmethod
    def _expand_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            return Path(value).expanduser()
        return value.expanduser()

    @property
    def cases_path(self) -> Path:
        return self.cases_dir or (self.data_dir / "cases")

    @property
    def quarantine_path(self) -> Path:
        return self.quarantine_dir or (self.data_dir / "quarantine")

    def _database_url(self, user: str, password: str) -> str:
        from urllib.parse import quote_plus

        quoted_user = quote_plus(user)
        quoted_password = quote_plus(password)
        if self.db_socket:
            socket = quote_plus(self.db_socket)
            return (
                f"mysql+pymysql://{quoted_user}:{quoted_password}@/{self.db_name}"
                f"?unix_socket={socket}&charset=utf8mb4"
            )
        return (
            f"mysql+pymysql://{quoted_user}:{quoted_password}@{self.db_host}:{self.db_port}"
            f"/{self.db_name}?charset=utf8mb4"
        )

    @property
    def database_url(self) -> str:
        return self._database_url(self.db_user, self.db_password)

    @property
    def database_migration_url(self) -> str:
        return self._database_url(self.db_migration_user, self.db_migration_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
