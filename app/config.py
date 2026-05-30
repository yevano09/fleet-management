import secrets
import logging
from pydantic_settings import BaseSettings
from typing import Optional

logger = logging.getLogger(__name__)


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
    max_upload_size_mb: int = 100
    ota_firmware_base_url: str = "http://localhost:8000"
    secure_cookies: bool = False

    # V2G / battery degradation settings
    battery_replacement_cost_dollars: float = 35000.0
    battery_capacity_kwh: float = 60.0
    soh_min_discharge: float = 0.7
    soh_deg_threshold: float = 0.8
    spot_price_url: str = ""
    v2g_horizon_hours: int = 24
    v2g_time_step_minutes: int = 60

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/callback"
    jwt_secret_key: str = "change-me-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 1440

    # Admin credentials
    admin_username: str = "admin"
    admin_password: str = "adminadmin"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


def validate_settings():
    warnings = []
    if settings.jwt_secret_key == "change-me-to-a-random-secret":
        warnings.append("JWT_SECRET_KEY is using the default value — set it via .env for production")
    if settings.admin_username == "admin" and settings.admin_password == "adminadmin":
        warnings.append("Admin credentials are using defaults — set ADMIN_USERNAME/ADMIN_PASSWORD via .env for production")
    if not settings.secure_cookies and settings.jwt_secret_key != "change-me-to-a-random-secret":
        warnings.append("SECURE_COOKIES is False — set to True when using HTTPS")
    for w in warnings:
        logger.warning("Settings: %s", w)

