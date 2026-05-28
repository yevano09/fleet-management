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
    ota_firmware_base_url: str = "http://localhost:8000"

    # V2G / battery degradation settings
    battery_replacement_cost_dollars: float = 35000.0
    battery_capacity_kwh: float = 60.0
    soh_min_discharge: float = 0.7
    soh_deg_threshold: float = 0.8
    spot_price_url: str = ""  # empty = use static mock prices
    v2g_horizon_hours: int = 24
    v2g_time_step_minutes: int = 60

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
