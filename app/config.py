import os
import secrets
import logging
from pydantic_settings import BaseSettings
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_JWT_SECRET = "change-me-to-a-random-secret"
DEFAULT_ADMIN_PASSWORD = "adminadmin"
DEFAULT_ORG_ID = "org-default"
SUPER_ORG = "*"  # org_id claim meaning "all organizations" (super-admin)


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

    # ── P0 hardening (UC-23..UC-27) ──────────────────────────────────────
    # AUTH_MODE=open  → legacy behaviour (unauthenticated REST, anonymous MQTT).
    #                   Default ONLY when AUTH_MODE env is unset AND DATABASE_URL is sqlite.
    # AUTH_MODE=strict → required for --profile production; RBAC enforced, MQTT TLS,
    #                   docs disabled by compose, default secrets refused at startup.
    auth_mode: str = "open"
    # HA split (UC-27): leader owns MQTT loop + schedulers; api replicas serve HTTP only.
    role: str = "leader"  # leader | api
    # UC-24: MQTT TLS client material (backend connects to broker on 8883)
    mqtt_tls_enabled: bool = False
    mqtt_ca_cert: str = ""
    mqtt_client_cert: str = ""
    mqtt_client_key: str = ""
    # UC-25: internal CA directory (device certs + CRL)
    internal_ca_dir: str = "./certs"
    device_cert_ttl_days: int = 365
    # Docs visibility (production sets DOCS_ENABLED=false)
    docs_enabled: bool = True
    # Short-lived HMAC token gating firmware downloads in strict mode (C2 channel)
    firmware_token_ttl_seconds: int = 300
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
    spot_price_api_key: str = ""
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
    aegis_dry_run: bool = False

    # Feature 1: Telemetry retention
    telemetry_retention_days: int = 30
    telemetry_sample_interval_seconds: int = 10

    # Feature 2: Geofencing
    geofence_check_interval_seconds: int = 30

    # Feature 4: Scheduled OTA
    ota_scheduler_interval_seconds: int = 30
    ota_scheduler_timezone: str = "UTC"

    # Feature 5: Offline command queue
    command_queue_ttl_seconds: int = 86400
    command_queue_flush_interval_seconds: int = 15

    # Feature 6: Audit log retention
    audit_log_retention_days: int = 90

    # Feature 8: Firmware signing (Ed25519 private key PEM, optional)
    firmware_signing_private_key: str = ""
    firmware_signing_public_key: str = ""
    firmware_require_signature: bool = False

    # Feature 10: Real spot prices (provider: "mock", "iex", "entsoe")
    spot_price_provider: str = "mock"

    # Feature 12: RBAC default role for new OAuth users
    default_user_role: str = "viewer"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def model_post_init(self, __context):
        base = f"http://localhost:{self.external_port}"
        if not self.ota_firmware_base_url:
            object.__setattr__(self, "ota_firmware_base_url", base)
        if not self.google_redirect_uri:
            object.__setattr__(self, "google_redirect_uri", f"{base}/auth/callback")
        if not self.aegis_backend_url:
            object.__setattr__(self, "aegis_backend_url", f"http://localhost:{self.port}")
        # AUTH_MODE default resolution (P0 rule 3): open only when unset AND sqlite.
        if "AUTH_MODE" not in os.environ and not self.database_url.startswith("sqlite"):
            object.__setattr__(self, "auth_mode", "strict")
            logger.info("AUTH_MODE unset with non-sqlite DB → defaulting auth_mode=strict")


settings = Settings()


def validate_settings():
    warnings = []
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET:
        warnings.append("JWT_SECRET_KEY is using the default value — set it via .env for production")
    if settings.admin_username == "admin" and settings.admin_password == DEFAULT_ADMIN_PASSWORD:
        warnings.append("Admin credentials are using defaults — set ADMIN_USERNAME/ADMIN_PASSWORD via .env for production")
    if not settings.secure_cookies and settings.jwt_secret_key != DEFAULT_JWT_SECRET:
        warnings.append("SECURE_COOKIES is False — set to True when using HTTPS")
    for w in warnings:
        logger.warning("Settings: %s", w)
    # P0 UC-23 rule: strict mode refuses insecure defaults outright.
    if settings.auth_mode == "strict":
        errors = []
        if settings.jwt_secret_key == DEFAULT_JWT_SECRET:
            errors.append("AUTH_MODE=strict refuses default JWT_SECRET_KEY")
        if settings.admin_username == "admin" and settings.admin_password == DEFAULT_ADMIN_PASSWORD:
            errors.append("AUTH_MODE=strict refuses default admin credentials")
        if errors:
            raise RuntimeError("; ".join(errors))
