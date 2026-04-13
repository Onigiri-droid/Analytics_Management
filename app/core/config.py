import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Подхватываем .env до первого чтения переменных (локальный uvicorn без docker-compose).
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


@lru_cache
def get_settings():
    return Settings()


def _env_trim(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return v or None


class Settings:
    database_url: str
    debug: bool = False
    secret_key: str
    session_max_age_seconds: int
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_sender: str | None
    smtp_use_tls: bool
    smtp_use_ssl: bool
    app_base_url: str

    def __init__(self):
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://user:password@localhost:5432/inventory_db",
        )
        self.debug = os.getenv("DEBUG", "0") == "1"
        self.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
        self.session_max_age_seconds = int(os.getenv("SESSION_MAX_AGE_SECONDS", "43200"))
        self.smtp_host = _env_trim(os.getenv("SMTP_HOST"))
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = _env_trim(os.getenv("SMTP_USERNAME"))
        # Пароль приложения Google часто вставляют с пробелами; в .env без кавычек
        # после первого пробела значение обрезается — убираем все пробелы.
        pw = _env_trim(os.getenv("SMTP_PASSWORD"))
        self.smtp_password = pw.replace(" ", "") if pw else None
        self.smtp_sender = _env_trim(os.getenv("SMTP_SENDER"))
        self.smtp_use_tls = os.getenv("SMTP_USE_TLS", "1") == "1"
        # Порт 465: implicit TLS (SMTP_SSL), не STARTTLS
        self.smtp_use_ssl = os.getenv("SMTP_USE_SSL", "0") == "1"
        self.app_base_url = os.getenv("APP_BASE_URL", "http://localhost:8000")


settings = get_settings()
