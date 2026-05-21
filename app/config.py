from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./data/fleet.db"
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    ota_timeout_seconds: int = 120
    firmware_storage_path: str = "./firmware"
    max_retry_count: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
