import os
from functools import lru_cache


@lru_cache
def get_settings():
    return Settings()


class Settings:
    database_url: str
    debug: bool = False

    def __init__(self):
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://user:password@localhost:5432/inventory_db",
        )
        self.debug = os.getenv("DEBUG", "0") == "1"


settings = get_settings()
