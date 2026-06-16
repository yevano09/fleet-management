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
    external_port: int = 8181
    log_level: str = "INFO"
    ota_timeout_seconds: int = 120
    firmware_storage_path: str = "./firmware"
    max_retry_count: int = 3
    max_upload_size_mb: int = 100
    ota_firmware_base_url: str = ""
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
    google_redirect_uri: str = ""
    jwt_secret_key: str = "change-me-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 1440

    # Admin credentials
    admin_username: str = "admin"
    admin_password: str = "adminadmin"

    # Alert channels
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    alert_email_from: str = "fleet@example.com"
    alert_email_to: str = ""
    alert_webhook_url: str = ""

    # Aegis auto-remediation settings
    aegis_scrape_interval: int = 15
    aegis_action_timeout: int = 30
    aegis_retry_max: int = 3
    aegis_active_devices_threshold: float = 2.0
    aegis_ota_in_progress_threshold: float = 3.0
    aegis_latency_threshold: float = 0.5
    aegis_offline_ratio_threshold: float = 0.3
    aegis_backend_url: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def model_post_init(self, __context):
        base = f"http://localhost:{self.external_port}"
        if not self.ota_firmware_base_url:
            object.__setattr__(self, "ota_firmware_base_url", base)
        if not self.google_redirect_uri:
            object.__setattr__(self, "google_redirect_uri", f"{base}/auth/callback")
        if not self.aegis_backend_url:
            object.__setattr__(self, "aegis_backend_url", f"http://localhost:{self.port}")


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

